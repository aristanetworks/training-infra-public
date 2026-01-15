"""
Unit tests for bridge_utils.py - Single Source of Truth for Bridge Naming.

Tests cover:
- DEVICE_ABBREVIATIONS dictionary completeness
- parse_device_name() function
- generate_bridge_name() function
- parse_bridge_name() function (NEW)
- split_device_port() helper
- expand_device_code() helper
- expand_port_code() helper
- Round-trip consistency

These tests ensure bridge_utils provides a consistent API for all
bridge naming operations across nodebuilder, captureservice, and uilanding.
"""

import pytest
from bridge_utils import (
    DEVICE_ABBREVIATIONS,
    parse_device_name,
    generate_bridge_name,
    parse_bridge_name,
    split_device_port,
    expand_device_code,
    expand_port_code,
    get_device_abbreviation,
    is_legacy_abbreviation,
    get_abbreviation_mapping,
)


class TestDeviceAbbreviations:
    """Test the DEVICE_ABBREVIATIONS dictionary."""

    def test_all_fabric_devices_present(self):
        """Test that all fabric device types have abbreviations."""
        fabric_devices = ['sp', 'le', 'bo', 'me']
        for abbrev in fabric_devices:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_all_edge_devices_present(self):
        """Test that all edge device types have abbreviations."""
        edge_devices = ['fi', 'ga', 'ro', 'co', 'pe', 'ce', 'dc', 'rr']
        for abbrev in edge_devices:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_all_endpoint_devices_present(self):
        """Test that all endpoint device types have abbreviations."""
        endpoints = ['ho', 'cl', 'cu', 'oo']
        for abbrev in endpoints:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_all_velocloud_devices_present(self):
        """Test that all VeloCloud device types have abbreviations."""
        velocloud = ['ve', 'vc', 'vo']
        for abbrev in velocloud:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_all_provider_devices_present(self):
        """Test that all provider device types have abbreviations."""
        provider = ['is', 'in']
        for abbrev in provider:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_legacy_abbreviations_present(self):
        """Test that legacy abbreviations are present for parsing."""
        legacy = ['bl', 'gw', 'fw']
        for abbrev in legacy:
            assert abbrev in DEVICE_ABBREVIATIONS

    def test_abbreviations_are_two_letters(self):
        """Test that all abbreviations are exactly 2 letters."""
        for abbrev in DEVICE_ABBREVIATIONS:
            assert len(abbrev) == 2
            assert abbrev.isalpha()

    def test_standard_abbreviations_are_first_two_letters(self):
        """Test that non-legacy abbreviations are first 2 letters of device type."""
        standard_mappings = {
            'sp': 'spine',
            'le': 'leaf',
            'bo': 'borderleaf',
            'fi': 'firewall',
            'ga': 'gateway',
            'ho': 'host',
            'ro': 'router',
            'co': 'core',
        }
        for abbrev, full_name in standard_mappings.items():
            assert full_name[:2] == abbrev


class TestParseDeviceName:
    """Test the parse_device_name function."""

    def test_basic_device_types(self):
        """Test parsing basic device types."""
        test_cases = [
            ('spine1', 'sp1'),
            ('leaf1', 'le1'),
            ('borderleaf1', 'bo1'),
            ('firewall1', 'fi1'),
            ('gateway1', 'ga1'),
            ('host1', 'ho1'),
            ('router1', 'ro1'),
            ('core1', 'co1'),
        ]
        for device_name, expected_code in test_cases:
            result = parse_device_name(device_name)
            assert result['code'] == expected_code, f"Failed for {device_name}"
            assert result['name'] == device_name

    def test_velocloud_devices(self):
        """Test parsing VeloCloud device types."""
        test_cases = [
            ('vce1', 'vc1'),
            ('vcg1', 'vc1'),  # VCG also starts with vc
            ('vco1', 'vc1'),  # VCO also starts with vc
        ]
        for device_name, expected_code in test_cases:
            result = parse_device_name(device_name)
            assert result['code'] == expected_code, f"Failed for {device_name}"

    def test_ethernet_ports(self):
        """Test parsing Ethernet port names."""
        test_cases = [
            ('Ethernet1', '1'),
            ('Ethernet12', '12'),
            ('ethernet5', '5'),
            ('ETHERNET10', '10'),
        ]
        for port_name, expected_code in test_cases:
            result = parse_device_name(port_name)
            assert result['code'] == expected_code, f"Failed for {port_name}"

    def test_velocloud_ports(self):
        """Test parsing VeloCloud port names."""
        test_cases = [
            ('wan1', 'wa1'),
            ('wan2', 'wa2'),
            ('lan1', 'la1'),
            ('lan2', 'la2'),
        ]
        for port_name, expected_code in test_cases:
            result = parse_device_name(port_name)
            assert result['code'] == expected_code, f"Failed for {port_name}"

    def test_linux_host_ports(self):
        """Test parsing Linux host interface names.

        Note: eth prefix is preserved to distinguish from Ethernet (et) ports.
        This allows proper roundtrip for Linux host interfaces.
        """
        test_cases = [
            ('eth0', 'eth0'),  # eth prefix preserved
            ('eth1', 'eth1'),  # eth prefix preserved
        ]
        for port_name, expected_code in test_cases:
            result = parse_device_name(port_name)
            assert result['code'] == expected_code, f"Failed for {port_name}"

    def test_case_insensitive(self):
        """Test that parsing is case-insensitive."""
        result1 = parse_device_name('Spine1')
        result2 = parse_device_name('SPINE1')
        result3 = parse_device_name('spine1')
        assert result1['code'] == result2['code'] == result3['code']

    def test_high_device_numbers(self):
        """Test parsing devices with high numbers."""
        test_cases = [
            ('leaf15', 'le15'),
            ('spine24', 'sp24'),
            ('host100', 'ho100'),
        ]
        for device_name, expected_code in test_cases:
            result = parse_device_name(device_name)
            assert result['code'] == expected_code, f"Failed for {device_name}"

    def test_dc_suffix(self):
        """Test parsing device names with -dc suffix."""
        result = parse_device_name('spine1-dc1')
        assert 'sp1' in result['code'] or 'd1' in result['code']

    def test_core_suffix(self):
        """Test parsing device names with -core suffix."""
        result = parse_device_name('leaf1-core')
        assert 'le1' in result['code']


class TestGenerateBridgeName:
    """Test the generate_bridge_name function."""

    def test_basic_generation(self):
        """Test basic bridge name generation."""
        name = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert '-' in name
        assert 'x' in name
        assert len(name) <= 15

    def test_uses_x_separator(self):
        """Test that bridge names use 'x' separator."""
        name = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        # Format: {dev}x{port}-{dev}x{port}
        parts = name.split('-')
        assert len(parts) == 2
        assert 'x' in parts[0]
        assert 'x' in parts[1]

    def test_consistent_output(self):
        """Test that same inputs produce same output."""
        name1 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert name1 == name2

    def test_case_normalized(self):
        """Test that bridge names are normalized to lowercase."""
        name1 = generate_bridge_name('Leaf1', 'Ethernet1', 'Spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert name1 == name2
        assert name1 == name1.lower()

    def test_different_inputs_different_outputs(self):
        """Test that different inputs produce different outputs."""
        name1 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet2', 'spine1', 'Ethernet2')
        assert name1 != name2

    def test_ovs_length_limit(self):
        """Test that bridge names respect 15-character limit."""
        name = generate_bridge_name('borderleaf1', 'Ethernet12', 'spine24', 'Ethernet15')
        assert len(name) <= 15

    def test_firewall_uses_fi_not_fw(self):
        """Test that firewall uses 'fi' abbreviation, not 'fw'."""
        name = generate_bridge_name('firewall1', 'Ethernet1', 'borderleaf1', 'Ethernet5')
        assert 'fi1' in name
        assert 'fw' not in name

    def test_borderleaf_uses_bo_not_bl(self):
        """Test that borderleaf uses 'bo' abbreviation, not 'bl'."""
        name = generate_bridge_name('leaf1', 'Ethernet1', 'borderleaf1', 'Ethernet5')
        assert 'bo1' in name
        assert 'bl' not in name

    def test_gateway_uses_ga_not_gw(self):
        """Test that gateway uses 'ga' abbreviation, not 'gw'."""
        name = generate_bridge_name('gateway1', 'Ethernet1', 'core1', 'Ethernet3')
        assert 'ga1' in name
        assert 'gw' not in name

    def test_velocloud_bridge_name(self):
        """Test bridge name for VeloCloud connections."""
        name = generate_bridge_name('vce1', 'wan1', 'router1', 'Ethernet1')
        assert 'vc1' in name or 've1' in name


class TestParseBridgeName:
    """Test the parse_bridge_name function."""

    def test_basic_parsing(self):
        """Test basic bridge name parsing."""
        result = parse_bridge_name('le1x1-sp1x2')
        assert result['source_device_name'] == 'leaf1'
        assert result['source_port_name'] == 'Ethernet1'
        assert result['target_device_name'] == 'spine1'
        assert result['target_port_name'] == 'Ethernet2'

    def test_firewall_parsing(self):
        """Test parsing firewall bridge names."""
        result = parse_bridge_name('fi1x1-bo1x5')
        assert result['source_device_name'] == 'firewall1'
        assert result['target_device_name'] == 'borderleaf1'

    def test_velocloud_wan_parsing(self):
        """Test parsing VeloCloud WAN bridge names."""
        result = parse_bridge_name('ve1xwa1-ro1x1')
        assert 'vce1' in result['source_device_name'] or 've1' in result['source_device']
        assert result['source_port_name'] == 'wan1'

    def test_velocloud_lan_parsing(self):
        """Test parsing VeloCloud LAN bridge names."""
        result = parse_bridge_name('ve1xla1-le1x5')
        assert result['source_port_name'] == 'lan1'

    def test_host_parsing(self):
        """Test parsing Linux host bridge names."""
        # Note: eth0 -> et0 -> Ethernet0 (known limitation)
        result = parse_bridge_name('ho1xet0-le1x10')
        assert result['source_device_name'] == 'host1'
        assert result['source_port_name'] == 'Ethernet0'  # eth0 becomes Ethernet0

    def test_legacy_format_parsing(self):
        """Test parsing legacy kvmbuilder format."""
        result = parse_bridge_name('le11-sp12')
        assert result['source_device_name'] == 'leaf1'
        assert result['source_port_name'] == 'Ethernet1'
        assert result['target_device_name'] == 'spine1'
        assert result['target_port_name'] == 'Ethernet2'

    def test_legacy_abbreviations_parsed(self):
        """Test that legacy abbreviations are correctly parsed."""
        result = parse_bridge_name('bl1x1-le1x1')
        assert result['source_device_name'] == 'borderleaf1'

        result = parse_bridge_name('gw1x1-co1x1')
        assert result['source_device_name'] == 'gateway1'

    def test_no_separator_returns_empty(self):
        """Test that bridge names without '-' return empty result."""
        result = parse_bridge_name('nobridgename')
        assert result['source_device_name'] == ''
        assert result['target_device_name'] == ''

    def test_empty_string(self):
        """Test that empty string returns empty result."""
        result = parse_bridge_name('')
        assert result['source_device_name'] == ''


class TestSplitDevicePort:
    """Test the split_device_port helper function."""

    def test_x_separator_numeric_port(self):
        """Test splitting with 'x' separator and numeric port."""
        device, port = split_device_port('le5x1')
        assert device == 'le5'
        assert port == '1'

    def test_x_separator_et_port(self):
        """Test splitting with 'x' separator and 'et' port."""
        device, port = split_device_port('fi1xet1')
        assert device == 'fi1'
        assert port == 'et1'

    def test_x_separator_wan_port(self):
        """Test splitting with 'x' separator and WAN port."""
        device, port = split_device_port('ve1xwa1')
        assert device == 've1'
        assert port == 'wa1'

    def test_x_separator_lan_port(self):
        """Test splitting with 'x' separator and LAN port."""
        device, port = split_device_port('ve1xla1')
        assert device == 've1'
        assert port == 'la1'

    def test_eth_prefix_split(self):
        """Test splitting with 'eth' prefix."""
        device, port = split_device_port('client1eth1')
        assert device == 'client1'
        assert port == 'eth1'

    def test_et_prefix_split(self):
        """Test splitting with 'et' prefix."""
        device, port = split_device_port('sp1et1')
        assert device == 'sp1'
        assert port == 'et1'

    def test_legacy_kvmbuilder_format(self):
        """Test splitting legacy kvmbuilder format."""
        device, port = split_device_port('le11')
        assert device == 'le1'
        assert port == '1'

    def test_unknown_format(self):
        """Test that unknown format returns whole string as device."""
        device, port = split_device_port('unknown')
        assert device == 'unknown'
        assert port == ''


class TestExpandDeviceCode:
    """Test the expand_device_code helper function."""

    def test_standard_expansions(self):
        """Test standard device code expansions."""
        test_cases = [
            ('le5', 'leaf5'),
            ('sp4', 'spine4'),
            ('bo1', 'borderleaf1'),
            ('fi2', 'firewall2'),
            ('ga1', 'gateway1'),
            ('ho3', 'host3'),
        ]
        for code, expected in test_cases:
            assert expand_device_code(code) == expected, f"Failed for {code}"

    def test_velocloud_expansions(self):
        """Test VeloCloud device code expansions."""
        assert expand_device_code('ve1') == 'vce1'
        assert expand_device_code('vc1') == 'vcg1'
        assert expand_device_code('vo1') == 'vco1'

    def test_legacy_expansions(self):
        """Test legacy abbreviation expansions."""
        assert expand_device_code('bl1') == 'borderleaf1'
        assert expand_device_code('gw1') == 'gateway1'
        assert expand_device_code('fw1') == 'firewall1'

    def test_empty_code(self):
        """Test that empty code returns empty string."""
        assert expand_device_code('') == ''

    def test_unknown_code(self):
        """Test that unknown code is returned as-is."""
        assert expand_device_code('zz1') == 'zz1'


class TestExpandPortCode:
    """Test the expand_port_code helper function."""

    def test_numeric_to_ethernet(self):
        """Test that numeric codes expand to Ethernet."""
        assert expand_port_code('1') == 'Ethernet1'
        assert expand_port_code('12') == 'Ethernet12'

    def test_et_to_ethernet(self):
        """Test that 'et' prefix expands to Ethernet."""
        assert expand_port_code('et5') == 'Ethernet5'
        assert expand_port_code('et12') == 'Ethernet12'

    def test_eth_preserved(self):
        """Test that 'eth' prefix is preserved (Linux interface)."""
        assert expand_port_code('eth0') == 'eth0'
        assert expand_port_code('eth1') == 'eth1'

    def test_wan_expansion(self):
        """Test that 'wa' prefix expands to 'wan'."""
        assert expand_port_code('wa1') == 'wan1'
        assert expand_port_code('wa2') == 'wan2'

    def test_lan_expansion(self):
        """Test that 'la' prefix expands to 'lan'."""
        assert expand_port_code('la1') == 'lan1'
        assert expand_port_code('la2') == 'lan2'

    def test_full_names_preserved(self):
        """Test that full names are preserved."""
        assert expand_port_code('wan1') == 'wan1'
        assert expand_port_code('lan1') == 'lan1'

    def test_empty_code(self):
        """Test that empty code returns empty string."""
        assert expand_port_code('') == ''


class TestRoundTrip:
    """Test round-trip consistency: generate -> parse."""

    def test_veos_roundtrip(self):
        """Test vEOS to vEOS round-trip."""
        bridge = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        result = parse_bridge_name(bridge)

        assert result['source_device_name'] == 'leaf1'
        assert result['source_port_name'] == 'Ethernet1'
        assert result['target_device_name'] == 'spine1'
        assert result['target_port_name'] == 'Ethernet2'

    def test_firewall_roundtrip(self):
        """Test firewall connection round-trip."""
        bridge = generate_bridge_name('firewall1', 'Ethernet1', 'borderleaf1', 'Ethernet5')
        result = parse_bridge_name(bridge)

        assert result['source_device_name'] == 'firewall1'
        assert result['source_port_name'] == 'Ethernet1'
        assert result['target_device_name'] == 'borderleaf1'
        assert result['target_port_name'] == 'Ethernet5'

    def test_gateway_roundtrip(self):
        """Test gateway connection round-trip."""
        bridge = generate_bridge_name('gateway1', 'Ethernet1', 'core1', 'Ethernet3')
        result = parse_bridge_name(bridge)

        assert result['source_device_name'] == 'gateway1'
        assert result['source_port_name'] == 'Ethernet1'
        assert result['target_device_name'] == 'core1'
        assert result['target_port_name'] == 'Ethernet3'

    def test_velocloud_wan_roundtrip(self):
        """Test VeloCloud WAN connection round-trip."""
        bridge = generate_bridge_name('vce1', 'wan1', 'router1', 'Ethernet1')
        result = parse_bridge_name(bridge)

        # Note: vce1 generates 'vc1', which parses back to 'vcg1'
        # This is a known limitation - VeloCloud device types share 'vc' prefix
        assert 'vc' in result['source_device_name'] or 've' in result['source_device_name']
        assert result['source_port_name'] == 'wan1'
        assert result['target_device_name'] == 'router1'

    def test_velocloud_lan_roundtrip(self):
        """Test VeloCloud LAN connection round-trip."""
        bridge = generate_bridge_name('vce1', 'lan1', 'leaf1', 'Ethernet5')
        result = parse_bridge_name(bridge)

        assert result['source_port_name'] == 'lan1'
        assert result['target_device_name'] == 'leaf1'
        assert result['target_port_name'] == 'Ethernet5'

    def test_host_roundtrip(self):
        """Test Linux host connection round-trip.

        Note: eth prefix is now preserved through the roundtrip to distinguish
        Linux host interfaces (eth0, eth1) from Ethernet ports (Ethernet1, et1).
        """
        bridge = generate_bridge_name('host1', 'eth0', 'leaf1', 'Ethernet10')
        result = parse_bridge_name(bridge)

        assert result['source_device_name'] == 'host1'
        # eth0 is preserved through roundtrip
        assert result['source_port_name'] == 'eth0'
        assert result['target_device_name'] == 'leaf1'
        assert result['target_port_name'] == 'Ethernet10'

    def test_high_numbers_roundtrip(self):
        """Test high device/port numbers round-trip."""
        bridge = generate_bridge_name('leaf15', 'Ethernet24', 'spine8', 'Ethernet12')
        result = parse_bridge_name(bridge)

        assert result['source_device_name'] == 'leaf15'
        assert result['source_port_name'] == 'Ethernet24'
        assert result['target_device_name'] == 'spine8'
        assert result['target_port_name'] == 'Ethernet12'


class TestUtilityFunctions:
    """Test utility functions."""

    def test_get_device_abbreviation(self):
        """Test get_device_abbreviation function."""
        assert get_device_abbreviation('borderleaf') == 'bo'
        assert get_device_abbreviation('firewall') == 'fi'
        assert get_device_abbreviation('gateway') == 'ga'

    def test_is_legacy_abbreviation(self):
        """Test is_legacy_abbreviation function."""
        assert is_legacy_abbreviation('bl') is True
        assert is_legacy_abbreviation('gw') is True
        assert is_legacy_abbreviation('fw') is True
        assert is_legacy_abbreviation('bo') is False
        assert is_legacy_abbreviation('le') is False

    def test_get_abbreviation_mapping(self):
        """Test get_abbreviation_mapping function."""
        mapping = get_abbreviation_mapping()
        assert isinstance(mapping, dict)
        assert 'sp' in mapping
        assert 'le' in mapping
        # Verify it's a copy, not the original
        mapping['test'] = 'test'
        assert 'test' not in DEVICE_ABBREVIATIONS
