"""
Tests for QueryValidator — the read-only enforcement layer.

These are the most important tests in the suite. CIRRUS never mutates Azure
resources, and this validator is the last line of defence before an AI
decision reaches any Azure API call.
"""
import pytest
from services.query_validator import QueryValidator


@pytest.fixture
def validator():
    return QueryValidator()


# ---------------------------------------------------------------------------
# Natural language — safe queries
# ---------------------------------------------------------------------------

class TestSafeNaturalLanguageQueries:
    def test_show_all_vms(self, validator):
        ok, _ = validator.validate_natural_language_query("show all virtual machines")
        assert ok

    def test_list_storage_accounts(self, validator):
        ok, _ = validator.validate_natural_language_query("list storage accounts in westeurope")
        assert ok

    def test_cost_query(self, validator):
        ok, _ = validator.validate_natural_language_query("what are my costs this month?")
        assert ok

    def test_security_question(self, validator):
        ok, _ = validator.validate_natural_language_query("show security score for subscription")
        assert ok

    def test_empty_query_is_safe(self, validator):
        ok, _ = validator.validate_natural_language_query("")
        assert ok

    def test_none_query_is_safe(self, validator):
        ok, _ = validator.validate_natural_language_query(None)
        assert ok

    def test_logs_query(self, validator):
        ok, _ = validator.validate_natural_language_query("show me firewall deny logs last 24 hours")
        assert ok


# ---------------------------------------------------------------------------
# Natural language — destructive operations blocked
# ---------------------------------------------------------------------------

class TestDestructiveQueriesBlocked:
    def test_delete_vm(self, validator):
        ok, reason = validator.validate_natural_language_query("delete the production VM")
        assert not ok
        assert "delete" in reason.lower()

    def test_remove_resource_group(self, validator):
        ok, reason = validator.validate_natural_language_query("remove resource group rg-prod")
        assert not ok
        assert "remove" in reason.lower()

    def test_destroy_cluster(self, validator):
        ok, _ = validator.validate_natural_language_query("destroy the AKS cluster")
        assert not ok

    def test_purge_key_vault(self, validator):
        ok, _ = validator.validate_natural_language_query("purge key vault secrets")
        assert not ok

    def test_drop_table(self, validator):
        ok, _ = validator.validate_natural_language_query("drop the analytics table")
        assert not ok


# ---------------------------------------------------------------------------
# Natural language — modification operations blocked
# ---------------------------------------------------------------------------

class TestModificationQueriesBlocked:
    def test_reboot_vm(self, validator):
        ok, _ = validator.validate_natural_language_query("reboot the web server VM")
        assert not ok

    def test_deallocate_vm(self, validator):
        ok, _ = validator.validate_natural_language_query("deallocate all dev VMs")
        assert not ok

    def test_patch_firewall(self, validator):
        ok, _ = validator.validate_natural_language_query("patch the firewall policy rules")
        assert not ok

    def test_terminate_instance(self, validator):
        ok, _ = validator.validate_natural_language_query("terminate the container instance")
        assert not ok


# ---------------------------------------------------------------------------
# Natural language — creation operations blocked
# ---------------------------------------------------------------------------

class TestCreationQueriesBlocked:
    def test_create_vm(self, validator):
        ok, _ = validator.validate_natural_language_query("create a new virtual machine in eastus")
        assert not ok

    def test_deploy_function_app(self, validator):
        ok, _ = validator.validate_natural_language_query("deploy a function app to prod")
        assert not ok

    def test_provision_sql(self, validator):
        ok, _ = validator.validate_natural_language_query("provision a SQL database")
        assert not ok


# ---------------------------------------------------------------------------
# general_response intent bypasses keyword checks
# ---------------------------------------------------------------------------

class TestGeneralResponseBypass:
    """
    When AI classifies the query as general_response (code help, explanations,
    Terraform examples) the keywords are irrelevant — they appear in legitimate
    developer questions all the time.
    """

    def test_terraform_create_resource(self, validator):
        # User asking how to write Terraform — contains "create" but it's fine
        ok, _ = validator.validate_natural_language_query(
            "create a Terraform module for AKS", ai_intent="general_response"
        )
        assert ok

    def test_delete_code_example(self, validator):
        ok, _ = validator.validate_natural_language_query(
            "write a script to delete old blobs", ai_intent="general_response"
        )
        assert ok

    def test_deploy_github_workflow(self, validator):
        ok, _ = validator.validate_natural_language_query(
            "help me deploy via GitHub Actions", ai_intent="general_response"
        )
        assert ok

    def test_non_general_intent_still_blocked(self, validator):
        # Same query, but AI classified it as resource_graph — must still block
        ok, _ = validator.validate_natural_language_query(
            "delete all VMs", ai_intent="resource_graph"
        )
        assert not ok


# ---------------------------------------------------------------------------
# KQL validation
# ---------------------------------------------------------------------------

class TestKQLValidation:
    def test_simple_select_is_safe(self, validator):
        kql = "Resources | where type =~ 'microsoft.compute/virtualmachines' | project name, location"
        ok, _ = validator.validate_kql_query(kql)
        assert ok

    def test_summarize_is_safe(self, validator):
        kql = "AzureActivity | where TimeGenerated > ago(7d) | summarize count() by OperationName"
        ok, _ = validator.validate_kql_query(kql)
        assert ok

    def test_dot_delete_blocked(self, validator):
        ok, reason = validator.validate_kql_query("T | .delete")
        assert not ok
        assert ".delete" in reason

    def test_dot_drop_blocked(self, validator):
        ok, _ = validator.validate_kql_query("MyTable | .drop")
        assert not ok

    def test_dot_set_blocked(self, validator):
        ok, _ = validator.validate_kql_query(".set MyTable <| datatable...")
        assert not ok

    def test_dot_purge_blocked(self, validator):
        ok, _ = validator.validate_kql_query(".purge table T records")
        assert not ok

    def test_empty_kql_is_safe(self, validator):
        ok, _ = validator.validate_kql_query("")
        assert ok

    def test_none_kql_is_safe(self, validator):
        ok, _ = validator.validate_kql_query(None)
        assert ok

    def test_case_insensitive_detection(self, validator):
        # The validator lowercases before checking — verify upper case is caught
        ok, _ = validator.validate_kql_query("T | .DELETE")
        assert not ok


# ---------------------------------------------------------------------------
# AI decision validation
# ---------------------------------------------------------------------------

class TestAIDecisionValidation:
    @pytest.mark.parametrize("action", [
        "resource_graph",
        "cost_analysis",
        "cost_comparison",
        "log_analytics",
        "azure_metrics",
        "resource_health",
        "policy_compliance",
        "security_score",
        "general_response",
        "agentic_analysis",
    ])
    def test_all_safe_actions_pass(self, validator, action):
        ok, _ = validator.validate_ai_decision({"action": action})
        assert ok

    @pytest.mark.parametrize("action", [
        "delete_resource",
        "create_vm",
        "update_firewall",
        "stop_vm",
        "start_vm",
        "deploy_function",
        "destroy_rg",
        "run_script",
        "",
        "unknown_action",
    ])
    def test_unsafe_actions_blocked(self, validator, action):
        ok, reason = validator.validate_ai_decision({"action": action})
        assert not ok
        assert action in reason or "not allowed" in reason

    def test_missing_action_key(self, validator):
        ok, _ = validator.validate_ai_decision({})
        assert not ok

    def test_none_action(self, validator):
        ok, _ = validator.validate_ai_decision({"action": None})
        assert not ok
