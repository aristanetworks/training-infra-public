"""
Cross-service consistency tests for bridge name parsing.

These tests ensure that captureservice's local bridge parsing produces
IDENTICAL results to nodebuilder's bridge_utils.py (the single source of truth).

If any of these tests fail, it means captureservice and nodebuilder have
diverged and need to be synchronized.

The canonical implementation is in:
    nodebuilder/src/bridge_utils.py

Captureservice should match that behavior exactly.
"""

import pytest
import sys
import os

# Add paths for both services
NODEBUILDER_SRC = os.path.join(
    os.path.dirname(__file__), '..', '..', 'nodebuilder', 'src'
)
CAPTURESERVICE_SRC = os.path.join(
    os.path.dirname(__file__), '..', 'src'
)

sys.path.insert(0, NODEBUILDER_SRC)
sys.path.insert(0, CAPTURESERVICE_SRC)


# Import from nodebuilder (source of truth)
from bridge_utils import (
    parse_bridge_name as nodebuilder_parse,
    DEVICE_ABBREVIATIONS as NODEBUILDER_ABBREVIATIONS,
    split_device_port as nodebuilder_split,
    expand_device_code as nodebuilder_expand_device,
    expand_port_code as nodebuilder_expand_port,
)


class MockCaptureManager:
    """
    Mock of CaptureManager with bridge parsing methods.

    This mirrors the actual implementation in capture_service.py.
    If the actual implementation changes, update this mock AND run these tests.
    """

    # Copy of DEVICE_ABBREVIATIONS from capture_service.py
    DEVICE_ABBREVIATIONS = {
        'sp': 'spine',
        'le': 'leaf',
        'bo': 'borderleaf',
        'ho': 'host',
        'fi': 'firewall',
        've': 'vce',
        'vc': 'vcg',
        'vo': 'vco',
        'cl': 'client',
        'co': 'core',
        'pe': 'pe',
        'ce': 'ce',
        'dc': 'dci',
        'rr': 'rr',
        'ga': 'gateway',
        'ro': 'router',
        'is': 'isp',
        'in': 'internet',
        'me': 'memleaf',
        'cu': 'customer',
        'oo': 'oob',
        'bl': 'borderleaf',
        'gw': 'gateway',
        'fw': 'firewall',
    }

    def _split_device_port(self, part: str) -> tuple:
        """Split device+port string - mirrors capture_service.py."""
        import re
        lower = part.lower()

        # Check for 'x' separator (nodebuilder format)
        for i, c in enumerate(lower):
            if c == 'x' and i > 0 and i < len(part) - 1:
                prev_char = lower[i - 1]
                next_char = lower[i + 1]
                if prev_char.isdigit() and (next_char.isdigit() or next_char in 'ewl'):
                    return part[:i], part[i + 1:]

        # Look for 'eth'
        eth_idx = lower.find('eth')
        if eth_idx > 0:
            return part[:eth_idx], part[eth_idx:]

        # Look for 'et'
        et_idx = lower.find('et')
        if et_idx > 0:
            return part[:et_idx], part[et_idx:]

        # Legacy kvmbuilder format
        match = re.match(r'^([a-zA-Z]{2})(\d)(\d+)$', part)
        if match:
            prefix = match.group(1)
            device_num = match.group(2)
            port_num = match.group(3).lstrip('0') or '0'
            return f"{prefix}{device_num}", port_num

        return part, ""

    def _expand_device_code(self, code: str) -> str:
        """Expand device code - mirrors capture_service.py."""
        if not code:
            return code

        prefix = ''
        number = ''
        for char in code:
            if char.isalpha():
                prefix += char
            elif char.isdigit():
                number += char

        prefix_lower = prefix.lower()
        if prefix_lower in self.DEVICE_ABBREVIATIONS:
            return f"{self.DEVICE_ABBREVIATIONS[prefix_lower]}{number}"

        return code

    def _expand_port_code(self, code: str) -> str:
        """Expand port code - mirrors capture_service.py."""
        if not code:
            return code

        code_lower = code.lower()

        if code_lower.startswith('eth'):
            return code

        if code_lower.startswith('wan') or code_lower.startswith('lan'):
            return code

        if code_lower.startswith('wa') and len(code) >= 3:
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"wan{number}"
            return code

        if code_lower.startswith('la') and len(code) >= 3:
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"lan{number}"
            return code

        if code_lower.startswith('et'):
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"Ethernet{number}"
            return code

        if code.isdigit():
            return f"Ethernet{code}"

        return code

    def _parse_bridge_name(self, bridge_name: str) -> dict:
        """Parse bridge name - mirrors capture_service.py."""
        result = {
            "source_device": "",
            "source_port": "",
            "source_device_name": "",
            "source_port_name": "",
            "target_device": "",
            "target_port": "",
            "target_device_name": "",
            "target_port_name": ""
        }

        if '-' not in bridge_name:
            return result

        parts = bridge_name.split('-')
        if len(parts) >= 2:
            src = parts[0]
            tgt = parts[1]

            src_device, src_port = self._split_device_port(src)
            tgt_device, tgt_port = self._split_device_port(tgt)

            result["source_device"] = src_device
            result["source_port"] = src_port
            result["target_device"] = tgt_device
            result["target_port"] = tgt_port

            result["source_device_name"] = self._expand_device_code(src_device)
            result["source_port_name"] = self._expand_port_code(src_port)
            result["target_device_name"] = self._expand_device_code(tgt_device)
            result["target_port_name"] = self._expand_port_code(tgt_port)

        return result


class TestAbbreviationConsistency:
    """Test that device abbreviations match between services."""

    def test_standard_abbreviations_match(self):
        """Test that standard abbreviations are identical."""
        capture_mgr = MockCaptureManager()

        # All nodebuilder abbreviations should exist in captureservice
        for abbrev, device_type in NODEBUILDER_ABBREVIATIONS.items():
            assert abbrev in capture_mgr.DEVICE_ABBREVIATIONS, \
                f"Missing abbreviation '{abbrev}' in captureservice"
            assert capture_mgr.DEVICE_ABBREVIATIONS[abbrev] == device_type, \
                f"Abbreviation '{abbrev}' differs: nodebuilder={device_type}, " \
                f"captureservice={capture_mgr.DEVICE_ABBREVIATIONS[abbrev]}"

    def test_no_extra_abbreviations_in_captureservice(self):
        """Test that captureservice doesn't have extra abbreviations."""
        capture_mgr = MockCaptureManager()

        for abbrev in capture_mgr.DEVICE_ABBREVIATIONS:
            assert abbrev in NODEBUILDER_ABBREVIATIONS, \
                f"Extra abbreviation '{abbrev}' in captureservice not in nodebuilder"


class TestSplitDevicePortConsistency:
    """Test that device/port splitting is consistent."""

    TEST_CASES = [
        # (input, expected_device, expected_port)
        ('le5x1', 'le5', '1'),
        ('sp4x9', 'sp4', '9'),
        ('fi1xet1', 'fi1', 'et1'),
        ('ve1xwa1', 've1', 'wa1'),
        ('ve1xla1', 've1', 'la1'),
        ('client1eth1', 'client1', 'eth1'),
        ('sp1et1', 'sp1', 'et1'),
        ('le11', 'le1', '1'),
        ('sp12', 'sp1', '2'),
        ('unknown', 'unknown', ''),
    ]

    def test_split_consistency(self):
        """Test that split_device_port produces identical results."""
        capture_mgr = MockCaptureManager()

        for input_str, expected_device, expected_port in self.TEST_CASES:
            nodebuilder_result = nodebuilder_split(input_str)
            captureservice_result = capture_mgr._split_device_port(input_str)

            assert nodebuilder_result == captureservice_result, \
                f"Split mismatch for '{input_str}': " \
                f"nodebuilder={nodebuilder_result}, captureservice={captureservice_result}"


class TestExpandDeviceCodeConsistency:
    """Test that device code expansion is consistent."""

    TEST_CASES = [
        ('le5', 'leaf5'),
        ('sp4', 'spine4'),
        ('bo1', 'borderleaf1'),
        ('fi2', 'firewall2'),
        ('ga1', 'gateway1'),
        ('ho3', 'host3'),
        ('ve1', 'vce1'),
        ('vc1', 'vcg1'),
        ('bl1', 'borderleaf1'),  # legacy
        ('gw1', 'gateway1'),     # legacy
        ('', ''),
        ('zz1', 'zz1'),  # unknown
    ]

    def test_expand_device_consistency(self):
        """Test that expand_device_code produces identical results."""
        capture_mgr = MockCaptureManager()

        for code, expected in self.TEST_CASES:
            nodebuilder_result = nodebuilder_expand_device(code)
            captureservice_result = capture_mgr._expand_device_code(code)

            assert nodebuilder_result == captureservice_result, \
                f"Expand device mismatch for '{code}': " \
                f"nodebuilder={nodebuilder_result}, captureservice={captureservice_result}"


class TestExpandPortCodeConsistency:
    """Test that port code expansion is consistent."""

    TEST_CASES = [
        ('1', 'Ethernet1'),
        ('12', 'Ethernet12'),
        ('et5', 'Ethernet5'),
        ('eth0', 'eth0'),
        ('eth1', 'eth1'),
        ('wa1', 'wan1'),
        ('la1', 'lan1'),
        ('wan1', 'wan1'),
        ('lan1', 'lan1'),
        ('', ''),
    ]

    def test_expand_port_consistency(self):
        """Test that expand_port_code produces identical results."""
        capture_mgr = MockCaptureManager()

        for code, expected in self.TEST_CASES:
            nodebuilder_result = nodebuilder_expand_port(code)
            captureservice_result = capture_mgr._expand_port_code(code)

            assert nodebuilder_result == captureservice_result, \
                f"Expand port mismatch for '{code}': " \
                f"nodebuilder={nodebuilder_result}, captureservice={captureservice_result}"


class TestParseBridgeNameConsistency:
    """Test that full bridge parsing is consistent."""

    TEST_CASES = [
        'le1x1-sp1x2',
        'fi1x1-bo1x5',
        've1xwa1-ro1x1',
        've1xla1-le1x5',
        'ho1xet0-le1x10',
        'le11-sp12',
        'ga1x1-co1x3',
        'bl1x1-le1x1',  # legacy
        'nobridgename',
        '',
    ]

    def test_parse_consistency(self):
        """Test that parse_bridge_name produces identical results."""
        capture_mgr = MockCaptureManager()

        for bridge_name in self.TEST_CASES:
            nodebuilder_result = nodebuilder_parse(bridge_name)
            captureservice_result = capture_mgr._parse_bridge_name(bridge_name)

            # Compare all fields
            for key in nodebuilder_result:
                assert nodebuilder_result[key] == captureservice_result.get(key, ''), \
                    f"Parse mismatch for '{bridge_name}' field '{key}': " \
                    f"nodebuilder={nodebuilder_result[key]}, " \
                    f"captureservice={captureservice_result.get(key, '')}"


class TestRoundTripConsistency:
    """Test round-trip consistency between services."""

    def test_generated_bridges_parse_identically(self):
        """Test that bridges generated by nodebuilder parse identically in both services."""
        from bridge_utils import generate_bridge_name

        capture_mgr = MockCaptureManager()

        # Generate bridges using nodebuilder
        test_cases = [
            ('leaf1', 'Ethernet1', 'spine1', 'Ethernet2'),
            ('firewall1', 'Ethernet1', 'borderleaf1', 'Ethernet5'),
            ('gateway1', 'Ethernet1', 'core1', 'Ethernet3'),
            ('vce1', 'wan1', 'router1', 'Ethernet1'),
            ('host1', 'eth0', 'leaf1', 'Ethernet10'),
            ('leaf15', 'Ethernet24', 'spine8', 'Ethernet12'),
        ]

        for dev1, port1, dev2, port2 in test_cases:
            bridge = generate_bridge_name(dev1, port1, dev2, port2)

            nodebuilder_result = nodebuilder_parse(bridge)
            captureservice_result = capture_mgr._parse_bridge_name(bridge)

            for key in nodebuilder_result:
                assert nodebuilder_result[key] == captureservice_result.get(key, ''), \
                    f"Round-trip mismatch for '{bridge}' field '{key}': " \
                    f"nodebuilder={nodebuilder_result[key]}, " \
                    f"captureservice={captureservice_result.get(key, '')}"
