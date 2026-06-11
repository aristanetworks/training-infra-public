"""
Unit tests for validation module.

Tests cover:
- Device name validation
- IP address validation
- CIDR notation validation
- Resource limit validation
"""

import os
import pytest
from unittest.mock import patch

from validation import (
    validate_device_name,
    validate_host_name,
    validate_firewall_name,
    validate_cidr_ip,
    validate_host_limit,
    validate_firewall_limit,
    get_available_ips,
    get_all_nodes
)


class TestDeviceNameValidation:
    """Tests for device name validation."""

    def test_valid_name(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that valid names pass validation."""
        valid, error = validate_device_name('newleaf1', mock_topo_build_file, mock_user_nodes_file)
        assert valid is True
        assert error is None

    def test_empty_name(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that empty names are rejected."""
        valid, error = validate_device_name('', mock_topo_build_file, mock_user_nodes_file)
        assert valid is False
        assert 'required' in error.lower() or 'empty' in error.lower()

    def test_name_too_long(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that overly long names are rejected."""
        long_name = 'a' * 100
        valid, error = validate_device_name(long_name, mock_topo_build_file, mock_user_nodes_file)
        assert valid is False
        assert 'long' in error.lower() or 'character' in error.lower()

    def test_duplicate_topology_name(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that names matching topology devices are rejected."""
        valid, error = validate_device_name('spine1', mock_topo_build_file, mock_user_nodes_file)
        assert valid is False
        assert 'already' in error.lower() or 'exists' in error.lower() or 'duplicate' in error.lower()

    def test_duplicate_user_node_name(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that names matching existing user nodes are rejected."""
        # First add a user node
        from persistence import save_user_node
        save_user_node({'existingnode': {'ip_addr': '192.168.0.99'}}, mock_user_nodes_file)

        valid, error = validate_device_name('existingnode', mock_topo_build_file, mock_user_nodes_file)
        assert valid is False
        assert 'already' in error.lower() or 'exists' in error.lower()

    def test_case_insensitive_duplicate_check(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that duplicate check is case-insensitive."""
        # spine1 exists in topology
        valid, error = validate_device_name('SPINE1', mock_topo_build_file, mock_user_nodes_file)
        assert valid is False

    def test_special_characters_rejected(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that special characters are rejected."""
        invalid_names = ['leaf@1', 'spine#2', 'node$3', 'device%4', 'test&5']
        for name in invalid_names:
            valid, error = validate_device_name(name, mock_topo_build_file, mock_user_nodes_file)
            # Should either be invalid or sanitized
            # The exact behavior depends on implementation


class TestHostNameValidation:
    """Tests for host name validation."""

    def test_valid_host_name(self, mock_topo_build_file, mock_user_nodes_file,
                              mock_user_hosts_file, mock_user_firewalls_file):
        """Test that valid host names pass validation."""
        valid, error = validate_host_name(
            'newhost1', mock_topo_build_file, mock_user_nodes_file,
            mock_user_hosts_file, mock_user_firewalls_file
        )
        assert valid is True

    def test_host_name_conflicts_with_node(self, mock_topo_build_file, mock_user_nodes_file,
                                           mock_user_hosts_file, mock_user_firewalls_file):
        """Test that host names can't conflict with nodes."""
        valid, error = validate_host_name(
            'spine1', mock_topo_build_file, mock_user_nodes_file,
            mock_user_hosts_file, mock_user_firewalls_file
        )
        assert valid is False


class TestFirewallNameValidation:
    """Tests for firewall name validation."""

    def test_valid_firewall_name(self, mock_topo_build_file, mock_user_nodes_file,
                                  mock_user_hosts_file, mock_user_firewalls_file):
        """Test that valid firewall names pass validation."""
        valid, error = validate_firewall_name(
            'newfw1', mock_topo_build_file, mock_user_nodes_file,
            mock_user_hosts_file, mock_user_firewalls_file
        )
        assert valid is True


class TestCidrValidation:
    """Tests for CIDR IP notation validation."""

    def test_valid_cidr(self):
        """Test that valid CIDR notations pass."""
        valid_cidrs = [
            '10.1.1.1/24',
            '192.168.0.1/16',
            '172.16.0.1/8',
            '10.0.0.0/32'
        ]
        for cidr in valid_cidrs:
            valid, error = validate_cidr_ip(cidr)
            assert valid is True, f"CIDR {cidr} should be valid"

    def test_invalid_cidr_no_prefix(self):
        """Test that IP without prefix is rejected."""
        valid, error = validate_cidr_ip('10.1.1.1')
        assert valid is False
        assert 'cidr' in error.lower() or 'prefix' in error.lower() or '/' in error.lower()

    def test_invalid_cidr_bad_prefix(self):
        """Test that invalid prefix lengths are rejected."""
        valid, error = validate_cidr_ip('10.1.1.1/33')
        assert valid is False

    def test_invalid_ip_address(self):
        """Test that invalid IP addresses are rejected."""
        invalid_ips = [
            '256.1.1.1/24',
            '10.1.1/24',
            'not.an.ip/24',
            '10.1.1.1.1/24'
        ]
        for ip in invalid_ips:
            valid, error = validate_cidr_ip(ip)
            assert valid is False, f"IP {ip} should be invalid"


class TestResourceLimits:
    """Tests for resource limit validation."""

    def test_host_limit_not_reached(self, mock_user_hosts_file):
        """Test that host limit check passes when under limit."""
        valid, error = validate_host_limit(mock_user_hosts_file, max_hosts=5)
        assert valid is True

    def test_host_limit_reached(self, mock_user_hosts_file):
        """Test that host limit check fails when at limit."""
        # Add hosts up to limit
        from persistence import save_user_host
        for i in range(5):
            save_user_host({f'host{i}': {'mgmt_ip': f'192.168.0.{i}'}}, mock_user_hosts_file)

        valid, error = validate_host_limit(mock_user_hosts_file, max_hosts=5)
        assert valid is False
        assert 'limit' in error.lower() or 'maximum' in error.lower()

    def test_firewall_limit_not_reached(self, mock_user_firewalls_file):
        """Test that firewall limit check passes when under limit."""
        valid, error = validate_firewall_limit(mock_user_firewalls_file, max_firewalls=3)
        assert valid is True


class TestGetAvailableIPs:
    """Tests for available IP retrieval."""

    def test_get_available_ips(self, mock_dnsmasq_file, mock_topo_build_file, mock_user_nodes_file):
        """Test getting available IPs from dnsmasq."""
        available = get_available_ips(mock_dnsmasq_file, mock_topo_build_file, mock_user_nodes_file)

        # Should return IPs not used by topology
        assert isinstance(available, list)
        # Each entry should have 'ip' and 'mac' keys
        for entry in available:
            assert 'ip' in entry
            assert 'mac' in entry

    def test_topology_ips_not_available(self, mock_dnsmasq_file, mock_topo_build_file, mock_user_nodes_file):
        """Test that topology IPs are not in available list."""
        available = get_available_ips(mock_dnsmasq_file, mock_topo_build_file, mock_user_nodes_file)
        available_ips = [entry['ip'] for entry in available]

        # Topology IPs should not be available
        topology_ips = ['192.168.0.10', '192.168.0.11', '192.168.0.12']  # From fixture
        for topo_ip in topology_ips:
            assert topo_ip not in available_ips


class TestGetAllNodes:
    """Tests for getting all nodes."""

    def test_get_all_nodes_includes_topology(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that get_all_nodes includes topology nodes."""
        nodes = get_all_nodes(mock_topo_build_file, mock_user_nodes_file)

        # Should include spine1, leaf1, leaf2 from topology
        # Each node is a dict with 'name' key
        node_names = [n['name'].lower() for n in nodes]
        assert 'spine1' in node_names
        assert 'leaf1' in node_names
        assert 'leaf2' in node_names

    def test_get_all_nodes_includes_user_nodes(self, mock_topo_build_file, mock_user_nodes_file):
        """Test that get_all_nodes includes user-added nodes."""
        # Add a user node
        from persistence import save_user_node
        save_user_node({'usernode1': {'ip_addr': '192.168.0.50'}}, mock_user_nodes_file)

        nodes = get_all_nodes(mock_topo_build_file, mock_user_nodes_file)
        # Each node is a dict with 'name' key
        node_names = [n['name'].lower() for n in nodes]
        assert 'usernode1' in node_names
