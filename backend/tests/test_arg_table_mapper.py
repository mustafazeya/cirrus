"""
Tests for ARG table mapper utility functions.

The mapper translates Azure resource types to the right Resource Graph table.
Getting this wrong means queries silently return 0 results — hard to debug in
production. These tests cover the pure synchronous helpers only (no network,
no Redis).
"""
import pytest
from utils.arg_table_mapper import (
    extract_resource_provider,
    is_child_resource_type,
    get_parent_resource_type,
    is_valid_resource_type,
    _table_mapping_cache,
)


# ---------------------------------------------------------------------------
# extract_resource_provider
# ---------------------------------------------------------------------------

class TestExtractResourceProvider:
    def test_standard_type(self):
        assert extract_resource_provider("Microsoft.Network/virtualNetworks") == "Microsoft.Network"

    def test_compute_vm(self):
        assert extract_resource_provider("Microsoft.Compute/virtualMachines") == "Microsoft.Compute"

    def test_child_resource_still_returns_provider(self):
        result = extract_resource_provider("Microsoft.Network/virtualNetworks/subnets")
        assert result == "Microsoft.Network"

    def test_lowercase_input(self):
        result = extract_resource_provider("microsoft.storage/storageaccounts")
        assert result == "microsoft.storage"

    def test_no_slash_returns_none(self):
        assert extract_resource_provider("MicrosoftNetwork") is None

    def test_empty_string_returns_none(self):
        assert extract_resource_provider("") is None

    def test_none_returns_none(self):
        assert extract_resource_provider(None) is None

    def test_deeply_nested_child(self):
        result = extract_resource_provider("Microsoft.Network/firewallPolicies/ruleCollectionGroups/rules")
        assert result == "Microsoft.Network"


# ---------------------------------------------------------------------------
# is_child_resource_type
# ---------------------------------------------------------------------------

class TestIsChildResourceType:
    def test_parent_resource_is_not_child(self):
        assert is_child_resource_type("Microsoft.Network/virtualNetworks") is False

    def test_child_resource_is_child(self):
        assert is_child_resource_type("Microsoft.Network/virtualNetworks/subnets") is True

    def test_firewall_rule_collection_group(self):
        assert is_child_resource_type("Microsoft.Network/firewallPolicies/ruleCollectionGroups") is True

    def test_deeply_nested(self):
        assert is_child_resource_type("A/B/C/D") is True

    def test_empty_returns_false(self):
        assert is_child_resource_type("") is False

    def test_none_returns_false(self):
        assert is_child_resource_type(None) is False

    def test_provider_only_no_slash(self):
        assert is_child_resource_type("Microsoft.Network") is False


# ---------------------------------------------------------------------------
# get_parent_resource_type
# ---------------------------------------------------------------------------

class TestGetParentResourceType:
    def test_subnet_parent_is_vnet(self):
        result = get_parent_resource_type("Microsoft.Network/virtualNetworks/subnets")
        assert result == "Microsoft.Network/virtualNetworks"

    def test_rule_collection_group_parent(self):
        result = get_parent_resource_type("Microsoft.Network/firewallPolicies/ruleCollectionGroups")
        assert result == "Microsoft.Network/firewallPolicies"

    def test_parent_resource_returns_none(self):
        assert get_parent_resource_type("Microsoft.Compute/virtualMachines") is None

    def test_empty_returns_none(self):
        assert get_parent_resource_type("") is None

    def test_none_returns_none(self):
        assert get_parent_resource_type(None) is None


# ---------------------------------------------------------------------------
# is_valid_resource_type — with no cache loaded
# ---------------------------------------------------------------------------

class TestIsValidResourceTypeNoCache:
    """
    When the cache hasn't been populated (startup scenario or test env),
    is_valid_resource_type must assume valid rather than rejecting everything.
    Unknown is not the same as invalid.
    """

    def setup_method(self):
        # Ensure cache is empty for these tests
        self._original = _table_mapping_cache.copy()
        _table_mapping_cache["data"] = None
        _table_mapping_cache["timestamp"] = None

    def teardown_method(self):
        _table_mapping_cache.update(self._original)

    def test_known_type_without_cache_is_assumed_valid(self):
        assert is_valid_resource_type("microsoft.compute/virtualmachines") is True

    def test_empty_returns_false_regardless_of_cache(self):
        assert is_valid_resource_type("") is False

    def test_none_returns_false_regardless_of_cache(self):
        assert is_valid_resource_type(None) is False


# ---------------------------------------------------------------------------
# is_valid_resource_type — with cache loaded
# ---------------------------------------------------------------------------

class TestIsValidResourceTypeWithCache:
    def setup_method(self):
        self._original_data = _table_mapping_cache.get("data")
        self._original_ts = _table_mapping_cache.get("timestamp")
        from datetime import datetime
        _table_mapping_cache["data"] = {
            "mappings": {
                "microsoft.compute/virtualmachines": ["ComputeResources"],
                "microsoft.network/virtualnetworks": ["NetworkResources"],
                "microsoft.storage/storageaccounts": ["StorageResources"],
            }
        }
        _table_mapping_cache["timestamp"] = datetime.now()

    def teardown_method(self):
        _table_mapping_cache["data"] = self._original_data
        _table_mapping_cache["timestamp"] = self._original_ts

    def test_known_type_is_valid(self):
        assert is_valid_resource_type("microsoft.compute/virtualmachines") is True

    def test_unknown_type_is_invalid(self):
        assert is_valid_resource_type("microsoft.made.up/fakeresource") is False

    def test_child_of_known_parent_is_valid(self):
        # microsoft.network/virtualnetworks/subnets — parent is in cache
        assert is_valid_resource_type("microsoft.network/virtualnetworks/subnets") is True

    def test_case_insensitive(self):
        assert is_valid_resource_type("Microsoft.Compute/VirtualMachines") is True
