"""
Tests for the pure synchronous methods on AIQueryService.

These don't touch OpenAI, Redis, or any Azure API — they test the
data-manipulation helpers that feed into the agentic loop.
"""
import pytest
from unittest.mock import MagicMock, patch
from services.ai_query_service import AIQueryService


@pytest.fixture
def service():
    with patch("services.ai_query_service.settings") as mock_settings:
        mock_settings.OPENAI_API_BASE = "https://test.openai.azure.com"
        mock_settings.OPENAI_API_KEY = "test-key"
        mock_settings.OPENAI_API_VERSION = "2024-12-01-preview"
        mock_settings.OPENAI_DEPLOYMENT_NAME = "test-model"
        mock_settings.OPENAI_CONNECT_TIMEOUT = 10.0
        mock_settings.OPENAI_READ_TIMEOUT = 90.0
        mock_settings.OPENAI_WRITE_TIMEOUT = 10.0
        mock_settings.OPENAI_POOL_TIMEOUT = 10.0
        mock_settings.AI_KQL_ROW_LIMIT_DEFAULT = 100
        mock_settings.AI_KQL_ROW_LIMIT_BROAD = 50
        svc = AIQueryService.__new__(AIQueryService)
        svc.settings = mock_settings
        svc.openai_client = None
        return svc


# ---------------------------------------------------------------------------
# _is_empty_result
# ---------------------------------------------------------------------------

class TestIsEmptyResult:
    def test_resource_graph_with_results_is_not_empty(self, service):
        data = {"results": [{"name": "vm1"}], "total_records": 1}
        assert service._is_empty_result("resource_graph", data) is False

    def test_resource_graph_with_no_results_is_empty(self, service):
        data = {"results": [], "total_records": 0}
        assert service._is_empty_result("resource_graph", data) is True

    def test_cost_analysis_with_zero_total_is_empty(self, service):
        data = {"breakdown": [], "total_cost": 0}
        assert service._is_empty_result("cost_analysis", data) is True

    def test_cost_analysis_with_data_is_not_empty(self, service):
        data = {"breakdown": [{"name": "VMs", "cost": 1500}], "total_cost": 1500}
        assert service._is_empty_result("cost_analysis", data) is False

    def test_log_analytics_with_records_is_not_empty(self, service):
        data = {"results": [{"TimeGenerated": "2026-01-01"}], "total_records": 1}
        assert service._is_empty_result("log_analytics", data) is False

    def test_resource_health_empty_list(self, service):
        assert service._is_empty_result("resource_health", {"resources": []}) is True

    def test_resource_health_with_data(self, service):
        data = {"resources": [{"name": "vm1", "availability_state": "Available"}]}
        assert service._is_empty_result("resource_health", data) is False

    def test_unknown_action_type_is_not_empty(self, service):
        # Unknown action types should not crash and default to not-empty
        assert service._is_empty_result("some_future_action", {"data": "anything"}) is False


# ---------------------------------------------------------------------------
# _build_data_digest
# ---------------------------------------------------------------------------

class TestBuildDataDigest:
    def test_none_returns_empty_string(self, service):
        assert service._build_data_digest(None) == ""

    def test_empty_dict_returns_empty_string(self, service):
        assert service._build_data_digest({}) == ""

    def test_resource_graph_results_included(self, service):
        data = {
            "results": [{"name": "vm-prod-01", "location": "westeurope", "resourceGroup": "rg-prod"}],
            "total_records": 1,
        }
        digest = service._build_data_digest(data)
        assert "vm-prod-01" in digest

    def test_cost_breakdown_included(self, service):
        data = {
            "breakdown": [{"name": "Virtual Machines", "cost": 4200.50}],
            "total_cost": 4200.50,
            "currency": "SEK",
        }
        digest = service._build_data_digest(data)
        assert "4200.50" in digest
        assert "SEK" in digest

    def test_digest_is_capped(self, service):
        # Digest must not blow up token budget — hard cap at 700 chars
        data = {
            "results": [{"name": f"resource-{i}", "location": "westeurope"} for i in range(200)],
            "total_records": 200,
        }
        digest = service._build_data_digest(data)
        assert len(digest) <= 700

    def test_large_result_set_shows_count(self, service):
        data = {
            "results": [{"name": f"vm-{i}"} for i in range(50)],
            "total_records": 50,
        }
        digest = service._build_data_digest(data)
        assert "50" in digest

    def test_comparison_data_included(self, service):
        data = {
            "comparison": {
                "this_month": {"total_cost": 5000, "currency": "SEK"},
                "last_month": {"total_cost": 4500, "currency": "SEK"},
            },
            "difference": 500,
            "percent_change": 11.1,
        }
        digest = service._build_data_digest(data)
        assert "11.1" in digest

    def test_health_data_included(self, service):
        data = {
            "resources": [
                {"name": "vm-prod", "availability_state": "Unavailable"},
                {"name": "vm-dev",  "availability_state": "Available"},
            ]
        }
        digest = service._build_data_digest(data)
        assert "vm-prod" in digest
        assert "Unavailable" in digest
