"""
Tests for timezone utilities.

CET is the display timezone for all CIRRUS timestamps (SAS is a Swedish company).
The helpers are used in audit logs, cost reports, and scheduler windows — any
silent offset error causes incorrect timestamp labelling in the UI.
"""
import pytest
from datetime import datetime, timezone, timedelta
from utils.timezone_utils import now_cet, utc_to_cet, cet_to_utc, format_cet

CET = timezone(timedelta(hours=1))
UTC = timezone.utc


class TestUtcToCet:
    def test_adds_one_hour(self):
        utc_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        cet_time = utc_to_cet(utc_time)
        assert cet_time.hour == 13

    def test_naive_utc_datetime_is_handled(self):
        naive = datetime(2026, 6, 1, 12, 0, 0)
        cet_time = utc_to_cet(naive)
        assert cet_time.hour == 13

    def test_midnight_utc_is_1am_cet(self):
        utc_time = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)
        cet_time = utc_to_cet(utc_time)
        assert cet_time.hour == 1

    def test_day_boundary_crosses_correctly(self):
        # 23:30 UTC → 00:30 CET next day
        utc_time = datetime(2026, 3, 10, 23, 30, 0, tzinfo=UTC)
        cet_time = utc_to_cet(utc_time)
        assert cet_time.day == 11
        assert cet_time.hour == 0
        assert cet_time.minute == 30

    def test_output_is_timezone_aware(self):
        utc_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        cet_time = utc_to_cet(utc_time)
        assert cet_time.tzinfo is not None


class TestCetToUtc:
    def test_subtracts_one_hour(self):
        cet_time = datetime(2026, 6, 1, 13, 0, 0, tzinfo=CET)
        utc_time = cet_to_utc(cet_time)
        assert utc_time.hour == 12

    def test_naive_cet_datetime_is_handled(self):
        naive = datetime(2026, 6, 1, 13, 0, 0)
        utc_time = cet_to_utc(naive)
        assert utc_time.hour == 12

    def test_roundtrip(self):
        original = datetime(2026, 4, 20, 15, 30, 0, tzinfo=UTC)
        roundtripped = cet_to_utc(utc_to_cet(original))
        assert roundtripped.hour == original.hour
        assert roundtripped.minute == original.minute


class TestNowCet:
    def test_returns_aware_datetime(self):
        result = now_cet()
        assert result.tzinfo is not None

    def test_is_close_to_current_time(self):
        import time
        before = datetime.now(CET)
        time.sleep(0.01)
        result = now_cet()
        time.sleep(0.01)
        after = datetime.now(CET)
        assert before <= result <= after


class TestFormatCet:
    def test_formats_utc_datetime(self):
        dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = format_cet(dt)
        # 10:30 UTC → 11:30 CET
        assert "11:30" in result

    def test_none_returns_empty_or_graceful(self):
        result = format_cet(None)
        # Should not raise — exact value is implementation detail
        assert isinstance(result, str)
