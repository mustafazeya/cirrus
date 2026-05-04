"""
Tests for LogAnalysisService.

Focuses on the sampling logic and column pruning — both are critical for
keeping AI context windows small without losing signal.
"""
import pytest
from datetime import datetime, timezone
from services.log_analysis_service import LogAnalysisService


@pytest.fixture
def service():
    return LogAnalysisService()


def make_rows(n, include_time=True):
    rows = []
    for i in range(n):
        row = {"level": "Error" if i % 3 == 0 else "Info", "message": f"event {i}"}
        if include_time:
            row["TimeGenerated"] = f"2026-01-01T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Empty / trivial inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_results(self, service):
        result = service.prepare_for_ai_analysis([], "some query")
        assert result["total_rows"] == 0
        assert "message" in result

    def test_single_row(self, service):
        rows = [{"level": "Info", "message": "hello", "TimeGenerated": "2026-01-01T00:00:00Z"}]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert result["total_rows"] == 1
        assert result["sampled_rows"] == 1
        assert result["is_sampled"] is False


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

class TestSampling:
    def test_small_dataset_is_not_sampled(self, service):
        rows = make_rows(30)
        result = service.prepare_for_ai_analysis(rows, "q", max_sample_rows=50)
        assert result["is_sampled"] is False
        assert result["sampled_rows"] == 30

    def test_large_dataset_is_sampled(self, service):
        rows = make_rows(1000)
        result = service.prepare_for_ai_analysis(rows, "q", max_sample_rows=50)
        assert result["is_sampled"] is True
        assert result["sampled_rows"] == 50

    def test_sample_spans_full_range(self, service):
        """
        Sampled rows must cover the whole dataset, not just the first N.
        A tail spike would be invisible otherwise.
        """
        rows = make_rows(200)
        # Mark only the very last row with a distinctive value
        rows[-1]["marker"] = "end"
        result = service.prepare_for_ai_analysis(rows, "q", max_sample_rows=20)
        markers = [r.get("marker") for r in result["sample_entries"]]
        assert "end" in markers

    def test_sample_includes_first_row(self, service):
        rows = make_rows(200)
        rows[0]["marker"] = "start"
        result = service.prepare_for_ai_analysis(rows, "q", max_sample_rows=20)
        markers = [r.get("marker") for r in result["sample_entries"]]
        assert "start" in markers

    def test_exact_boundary(self, service):
        rows = make_rows(50)
        result = service.prepare_for_ai_analysis(rows, "q", max_sample_rows=50)
        assert result["is_sampled"] is False
        assert result["sampled_rows"] == 50


# ---------------------------------------------------------------------------
# Empty column pruning
# ---------------------------------------------------------------------------

class TestEmptyColumnPruning:
    def test_null_columns_are_removed(self, service):
        rows = [
            {"level": "Error", "empty_col": None, "TimeGenerated": "2026-01-01T00:00:00Z"},
            {"level": "Info",  "empty_col": None, "TimeGenerated": "2026-01-01T01:00:00Z"},
        ]
        result = service.prepare_for_ai_analysis(rows, "test")
        for row in result["sample_entries"]:
            assert "empty_col" not in row

    def test_empty_string_columns_are_removed(self, service):
        rows = [
            {"level": "Error", "junk": "", "TimeGenerated": "2026-01-01T00:00:00Z"},
        ]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert "junk" not in result["sample_entries"][0]

    def test_null_string_columns_are_removed(self, service):
        rows = [{"level": "Info", "bad": "null", "TimeGenerated": "2026-01-01T00:00:00Z"}]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert "bad" not in result["sample_entries"][0]

    def test_columns_with_any_value_are_kept(self, service):
        rows = [
            {"level": "Error", "sometimes_null": None},
            {"level": "Info",  "sometimes_null": "present"},
        ]
        result = service.prepare_for_ai_analysis(rows, "test")
        kept = {k for row in result["sample_entries"] for k in row}
        assert "sometimes_null" in kept


# ---------------------------------------------------------------------------
# Time range extraction
# ---------------------------------------------------------------------------

class TestTimeRangeExtraction:
    def test_extracts_time_range_from_TimeGenerated(self, service):
        rows = [
            {"level": "Info", "TimeGenerated": "2026-01-01T00:00:00Z"},
            {"level": "Info", "TimeGenerated": "2026-01-01T12:00:00Z"},
            {"level": "Info", "TimeGenerated": "2026-01-01T06:00:00Z"},
        ]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert "2026-01-01T00:00:00" in result["time_range"]["start"]
        assert "2026-01-01T12:00:00" in result["time_range"]["end"]

    def test_no_time_field_returns_unknown(self, service):
        rows = [{"level": "Info", "message": "hello"}]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert result["time_range"]["start"] == "unknown"
        assert result["time_range"]["end"] == "unknown"

    def test_accepts_timestamp_field_name(self, service):
        rows = [
            {"level": "Info", "timestamp": "2026-03-15T10:00:00Z"},
            {"level": "Info", "timestamp": "2026-03-15T11:00:00Z"},
        ]
        result = service.prepare_for_ai_analysis(rows, "test")
        assert result["time_range"]["start"] != "unknown"
