"""
Tests for DeviceTypeConfig class in src/device_types.py.

Covers: device classification, tier ordering, group names,
frontend export, group name ordering, and WAN customer device checks.
"""

import pytest
from device_types import DeviceTypeConfig


# ---------------------------------------------------------------------------
# TestClassifyDevice
# ---------------------------------------------------------------------------

class TestClassifyDevice:
    """Test device name → device type classification."""

    @pytest.mark.parametrize("device_name,expected", [
        # Pattern-based (contains, case-insensitive)
        ("spine1",        "spine"),
        ("leaf1",         "leaf"),
        ("borderleaf1",   "borderleaf"),
        ("memleaf1",      "memleaf"),
        ("host1",         "host"),
        ("internet",      "internet"),
        ("core1",         "core"),
        ("router1",       "router"),
        # startswith patterns (case-sensitive)
        ("PE1",           "pe"),
        ("CE1",           "ce"),
        ("BL1",           "borderleaf"),
        # Gateway custom matcher: GWxx where xx starts with digit
        ("GW11",          "gw"),
        ("GW21",          "gw"),
        # P router custom matcher: P + digit
        ("P1",            "p"),
        ("P3",            "p"),
        # Route Reflector custom matcher: RR + digit
        ("RR1",           "rr"),
        ("RR2",           "rr"),
        # Unknown / fallback
        ("unknown_device_xyz", "other"),
        ("",              "other"),
    ])
    def test_classify_device(self, device_name, expected):
        result = DeviceTypeConfig.classify_device(device_name)
        assert result == expected, (
            f"classify_device({device_name!r}) returned {result!r}, expected {expected!r}"
        )

    def test_classify_device_pe_not_p(self):
        """PE devices must NOT be classified as P routers."""
        assert DeviceTypeConfig.classify_device("PE1") == "pe"

    def test_classify_device_borderleaf_before_leaf(self):
        """borderleaf must take priority over leaf pattern."""
        assert DeviceTypeConfig.classify_device("borderleaf2") == "borderleaf"

    def test_classify_device_memleaf_before_leaf(self):
        """memleaf must take priority over leaf pattern."""
        assert DeviceTypeConfig.classify_device("memleaf3") == "memleaf"

    def test_classify_device_case_insensitive_patterns(self):
        """Pattern matching for contains-patterns is case-insensitive."""
        assert DeviceTypeConfig.classify_device("SPINE1") == "spine"
        assert DeviceTypeConfig.classify_device("Leaf2") == "leaf"
        assert DeviceTypeConfig.classify_device("HOST3") == "host"


# ---------------------------------------------------------------------------
# TestGetTier
# ---------------------------------------------------------------------------

class TestGetTier:
    """Test tier values and their relative ordering."""

    def test_spine_tier_less_than_leaf(self):
        assert DeviceTypeConfig.get_tier("spine") < DeviceTypeConfig.get_tier("leaf")

    def test_leaf_tier_less_than_host(self):
        assert DeviceTypeConfig.get_tier("leaf") < DeviceTypeConfig.get_tier("host")

    def test_internet_tier_is_zero(self):
        assert DeviceTypeConfig.get_tier("internet") == 0

    def test_unknown_type_returns_high_tier(self):
        """Unknown device types should return tier 9 (the fallback)."""
        tier = DeviceTypeConfig.get_tier("nonexistent_type")
        assert tier == 9

    def test_spine_before_host(self):
        assert DeviceTypeConfig.get_tier("spine") < DeviceTypeConfig.get_tier("host")

    def test_all_known_types_have_numeric_tier(self):
        for device_type in DeviceTypeConfig.DEVICE_TYPES:
            tier = DeviceTypeConfig.get_tier(device_type)
            assert isinstance(tier, int), f"{device_type} tier should be int, got {tier!r}"


# ---------------------------------------------------------------------------
# TestGetGroupName
# ---------------------------------------------------------------------------

class TestGetGroupName:
    """Test display group name retrieval."""

    def test_spine_group(self):
        assert DeviceTypeConfig.get_group_name("spine") == "Spine"

    def test_leaf_group(self):
        assert DeviceTypeConfig.get_group_name("leaf") == "Leaf"

    def test_host_group(self):
        assert DeviceTypeConfig.get_group_name("host") == "Host"

    @pytest.mark.parametrize("velo_type", [
        "velo_orchestrator", "velo_gateway", "velo_edge"
    ])
    def test_all_velo_types_map_to_velocloud(self, velo_type):
        assert DeviceTypeConfig.get_group_name(velo_type) == "VeloCloud"

    def test_unknown_type_returns_other(self):
        assert DeviceTypeConfig.get_group_name("nonexistent") == "Other"


# ---------------------------------------------------------------------------
# TestExportForFrontend
# ---------------------------------------------------------------------------

class TestExportForFrontend:
    """Test frontend configuration export."""

    def test_returns_dict(self):
        result = DeviceTypeConfig.export_for_frontend()
        assert isinstance(result, dict)

    def test_contains_spine(self):
        result = DeviceTypeConfig.export_for_frontend()
        assert "spine" in result

    def test_contains_leaf(self):
        result = DeviceTypeConfig.export_for_frontend()
        assert "leaf" in result

    def test_contains_host(self):
        result = DeviceTypeConfig.export_for_frontend()
        assert "host" in result

    @pytest.mark.parametrize("device_type", ["spine", "leaf", "host"])
    def test_each_type_has_required_keys(self, device_type):
        result = DeviceTypeConfig.export_for_frontend()
        entry = result[device_type]
        for key in ("tier", "label", "color", "shape"):
            assert key in entry, (
                f"export_for_frontend()[{device_type!r}] missing key {key!r}"
            )

    def test_all_known_types_present(self):
        result = DeviceTypeConfig.export_for_frontend()
        for device_type in DeviceTypeConfig.DEVICE_TYPES:
            assert device_type in result, f"{device_type!r} missing from export_for_frontend()"


# ---------------------------------------------------------------------------
# TestGetAllGroupNames
# ---------------------------------------------------------------------------

class TestGetAllGroupNames:
    """Test ordered unique group name list."""

    def test_returns_list(self):
        result = DeviceTypeConfig.get_all_group_names()
        assert isinstance(result, list)

    def test_spine_before_leaf(self):
        groups = DeviceTypeConfig.get_all_group_names()
        assert groups.index("Spine") < groups.index("Leaf")

    def test_leaf_before_host(self):
        groups = DeviceTypeConfig.get_all_group_names()
        assert groups.index("Leaf") < groups.index("Host")

    def test_no_duplicates(self):
        groups = DeviceTypeConfig.get_all_group_names()
        assert len(groups) == len(set(groups)), "get_all_group_names() returned duplicate group names"

    def test_spine_present(self):
        assert "Spine" in DeviceTypeConfig.get_all_group_names()

    def test_leaf_present(self):
        assert "Leaf" in DeviceTypeConfig.get_all_group_names()

    def test_host_present(self):
        assert "Host" in DeviceTypeConfig.get_all_group_names()


# ---------------------------------------------------------------------------
# TestIsWanCustomerDevice
# ---------------------------------------------------------------------------

class TestIsWanCustomerDevice:
    """Test WAN customer device classification."""

    @pytest.mark.parametrize("device_type", ["ce", "host"])
    def test_customer_devices_return_true(self, device_type):
        assert DeviceTypeConfig.is_wan_customer_device(device_type) is True

    @pytest.mark.parametrize("device_type", ["spine", "p"])
    def test_non_customer_devices_return_false(self, device_type):
        assert DeviceTypeConfig.is_wan_customer_device(device_type) is False

    def test_linux_host_is_customer_device(self):
        """linux_host is in the endpoint category, so should be True."""
        assert DeviceTypeConfig.is_wan_customer_device("linux_host") is True

    def test_pe_is_not_customer_device(self):
        """PE routers are edge devices, not in customer columns."""
        assert DeviceTypeConfig.is_wan_customer_device("pe") is False

    def test_leaf_is_not_customer_device(self):
        assert DeviceTypeConfig.is_wan_customer_device("leaf") is False
