"""
Unit tests for unified topology module.

Tests cover:
- Cross-type name collision validation
- Unified topology data structure
- Device reference cleanup across types
- Consistent data presentation
"""

import os
import pytest
import tempfile
from unittest.mock import patch, Mock

# Add src to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestCrossTypeNameCollision:
    """Tests for cross-type name collision detection."""

    @pytest.fixture
    def temp_files(self, tmp_path):
        """Create temporary persistence files."""
        topo_file = tmp_path / "topo_build.yml"
        nodes_file = tmp_path / "user_nodes.yaml"
        hosts_file = tmp_path / "user_hosts.yaml"
        firewalls_file = tmp_path / "user_firewalls.yaml"

        # Create empty topo
        topo_file.write_text("""
nodes:
  - spine1:
      ip_addr: 192.168.0.10
      neighbors: []
  - leaf1:
      ip_addr: 192.168.0.11
      neighbors: []
""")

        # Create empty user files
        nodes_file.write_text("version: 1\nnodes: []\n")
        hosts_file.write_text("version: 1\nhosts: []\n")
        firewalls_file.write_text("version: 1\nfirewalls: []\n")

        return {
            'topo': str(topo_file),
            'nodes': str(nodes_file),
            'hosts': str(hosts_file),
            'firewalls': str(firewalls_file)
        }

    def test_node_name_collides_with_topology(self, temp_files):
        """Verify vEOS node cannot use topology device name."""
        from validation import validate_device_name

        valid, error = validate_device_name(
            'spine1',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert not valid
        assert 'already in use' in error.lower()

    def test_node_name_collides_with_host(self, temp_files):
        """Verify vEOS node cannot use existing host name."""
        from validation import validate_device_name
        from persistence import save_user_host_pending

        # Create a host first
        save_user_host_pending('desktop1', {
            'mgmt_ip': '192.168.0.50',
            'status': 'active'
        }, temp_files['hosts'])

        # Try to create node with same name
        valid, error = validate_device_name(
            'desktop1',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert not valid
        assert 'already in use' in error.lower()

    def test_node_name_collides_with_firewall(self, temp_files):
        """Verify vEOS node cannot use existing firewall name."""
        from validation import validate_device_name
        from persistence import save_user_firewall_pending

        # Create a firewall first
        save_user_firewall_pending('fw1', {
            'mgmt_ip': '192.168.0.51',
            'status': 'active'
        }, temp_files['firewalls'])

        # Try to create node with same name
        valid, error = validate_device_name(
            'fw1',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert not valid
        assert 'already in use' in error.lower()

    def test_host_name_collides_with_node(self, temp_files):
        """Verify host cannot use existing vEOS node name."""
        from validation import validate_host_name
        from persistence import save_user_node_pending

        # Create a node first
        save_user_node_pending('newleaf', {
            'ip_addr': '192.168.0.50',
            'status': 'active'
        }, temp_files['nodes'])

        # Try to create host with same name
        valid, error = validate_host_name(
            'newleaf',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert not valid
        assert 'already in use' in error.lower()

    def test_case_insensitive_cross_type_collision(self, temp_files):
        """Verify name collision check is case-insensitive across types."""
        from validation import validate_device_name
        from persistence import save_user_host_pending

        # Create a host with lowercase name
        save_user_host_pending('mydevice', {
            'mgmt_ip': '192.168.0.50',
            'status': 'active'
        }, temp_files['hosts'])

        # Try to create node with uppercase name
        valid, error = validate_device_name(
            'MYDEVICE',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert not valid
        assert 'already in use' in error.lower()

    def test_unique_name_allowed(self, temp_files):
        """Verify unique name is allowed."""
        from validation import validate_device_name

        valid, error = validate_device_name(
            'uniquedevice',
            temp_files['topo'],
            temp_files['nodes'],
            temp_files['hosts'],
            temp_files['firewalls']
        )

        assert valid
        assert error is None


class TestUnifiedTopology:
    """Tests for unified topology data structure."""

    @pytest.fixture
    def populated_files(self, tmp_path):
        """Create temporary persistence files with data."""
        topo_file = tmp_path / "topo_build.yml"
        nodes_file = tmp_path / "user_nodes.yaml"
        hosts_file = tmp_path / "user_hosts.yaml"
        firewalls_file = tmp_path / "user_firewalls.yaml"

        # Create topology with nodes
        topo_file.write_text("""
nodes:
  - spine1:
      ip_addr: 192.168.0.10
      sys_mac: 00:1c:73:00:00:01
      neighbors:
        - port: Ethernet1
          neighborDevice: leaf1
          neighborPort: Ethernet1
  - leaf1:
      ip_addr: 192.168.0.11
      sys_mac: 00:1c:73:00:00:02
      neighbors:
        - port: Ethernet1
          neighborDevice: spine1
          neighborPort: Ethernet1
""")

        # Create user node
        nodes_file.write_text("""
version: 1
nodes:
  - leaf5:
      ip_addr: 192.168.0.50
      sys_mac: 00:1c:73:00:00:50
      status: active
      neighbors:
        - port: Ethernet1
          neighborDevice: spine1
          neighborPort: Ethernet5
""")

        # Create user host
        hosts_file.write_text("""
version: 1
hosts:
  - desktop1:
      mgmt_ip: 192.168.0.51
      data_ip: 10.1.1.100/24
      status: active
      connection:
        target_device: leaf1
        target_port: Ethernet10
        bridge: de11-le110
""")

        # Create user firewall
        firewalls_file.write_text("""
version: 1
firewalls:
  - fw1:
      mgmt_ip: 192.168.0.52
      status: active
      inside_interface:
        ip: 10.1.1.1/24
        target_device: leaf1
        target_port: Ethernet11
      outside_interface:
        ip: 10.2.2.1/24
        target_device: spine1
        target_port: Ethernet10
""")

        return {
            'topo': str(topo_file),
            'nodes': str(nodes_file),
            'hosts': str(hosts_file),
            'firewalls': str(firewalls_file)
        }

    def test_unified_topology_includes_all_types(self, populated_files):
        """Verify unified topology includes all device types."""
        from unified_topology import get_unified_topology

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        devices = topology['devices']
        device_names = [d['name'] for d in devices]

        # Check all devices are present
        assert 'spine1' in device_names
        assert 'leaf1' in device_names
        assert 'leaf5' in device_names
        assert 'desktop1' in device_names
        assert 'fw1' in device_names

    def test_unified_topology_summary(self, populated_files):
        """Verify summary counts are correct."""
        from unified_topology import get_unified_topology

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        summary = topology['summary']

        assert summary['topology_nodes'] == 2  # spine1, leaf1
        assert summary['user_nodes'] == 1      # leaf5
        assert summary['user_hosts'] == 1      # desktop1
        assert summary['user_firewalls'] == 1  # fw1
        assert summary['total_devices'] == 5

    def test_unified_device_has_standard_fields(self, populated_files):
        """Verify all devices have standard fields."""
        from unified_topology import get_unified_topology

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        required_fields = ['name', 'ip', 'device_type', 'device_category', 'user_added']

        for device in topology['devices']:
            for field in required_fields:
                assert field in device, f"Device {device.get('name')} missing field {field}"

    def test_device_types_are_correct(self, populated_files):
        """Verify device types are assigned correctly."""
        from unified_topology import get_unified_topology, DeviceType

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        device_map = {d['name']: d for d in topology['devices']}

        assert device_map['spine1']['device_type'] == DeviceType.VEOS.value
        assert device_map['leaf5']['device_type'] == DeviceType.VEOS.value
        assert device_map['desktop1']['device_type'] == DeviceType.LINUX_HOST.value
        assert device_map['fw1']['device_type'] == DeviceType.FIREWALL.value

    def test_user_added_flag_correct(self, populated_files):
        """Verify user_added flag is set correctly."""
        from unified_topology import get_unified_topology

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        device_map = {d['name']: d for d in topology['devices']}

        # Topology devices are not user_added
        assert device_map['spine1']['user_added'] is False
        assert device_map['leaf1']['user_added'] is False

        # User devices are user_added
        assert device_map['leaf5']['user_added'] is True
        assert device_map['desktop1']['user_added'] is True
        assert device_map['fw1']['user_added'] is True

    def test_connections_extracted(self, populated_files):
        """Verify connections are extracted from all device types."""
        from unified_topology import get_unified_topology

        topology = get_unified_topology(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        connections = topology['connections']

        # Should have connections from topology, user node, host, and firewall
        assert len(connections) >= 4

        # Check connection structure
        for conn in connections:
            assert 'source_device' in conn
            assert 'target_device' in conn
            assert 'source_type' in conn


class TestCrossTypeCleanup:
    """Tests for cross-type reference cleanup."""

    @pytest.fixture
    def populated_files(self, tmp_path):
        """Create temporary persistence files with interconnected devices."""
        nodes_file = tmp_path / "user_nodes.yaml"
        hosts_file = tmp_path / "user_hosts.yaml"
        firewalls_file = tmp_path / "user_firewalls.yaml"

        # Create user nodes
        nodes_file.write_text("""
version: 1
nodes:
  - leaf5:
      ip_addr: 192.168.0.50
      status: active
      neighbors:
        - port: Ethernet1
          neighborDevice: leaf6
          neighborPort: Ethernet1
  - leaf6:
      ip_addr: 192.168.0.51
      status: active
      neighbors:
        - port: Ethernet1
          neighborDevice: leaf5
          neighborPort: Ethernet1
""")

        # Create host connected to leaf5
        hosts_file.write_text("""
version: 1
hosts:
  - desktop1:
      mgmt_ip: 192.168.0.52
      status: active
      connection:
        target_device: leaf5
        target_port: Ethernet10
""")

        # Create firewall connected to leaf5
        firewalls_file.write_text("""
version: 1
firewalls:
  - fw1:
      mgmt_ip: 192.168.0.53
      status: active
      inside_interface:
        target_device: leaf5
        target_port: Ethernet11
      outside_interface:
        target_device: spine1
        target_port: Ethernet10
""")

        return {
            'nodes': str(nodes_file),
            'hosts': str(hosts_file),
            'firewalls': str(firewalls_file)
        }

    def test_delete_node_cleans_other_nodes(self, populated_files):
        """Verify deleting a node cleans up references in other nodes."""
        from persistence import remove_all_device_references, load_user_nodes

        result = remove_all_device_references(
            'leaf5',
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        # Check that leaf6's reference to leaf5 was removed
        nodes_data = load_user_nodes(populated_files['nodes'])
        leaf6_neighbors = []
        for node in nodes_data.get('nodes', []):
            if 'leaf6' in node:
                leaf6_neighbors = node['leaf6'].get('neighbors', [])

        # leaf6 should no longer have leaf5 as a neighbor
        neighbor_devices = [n.get('neighborDevice', '').lower() for n in leaf6_neighbors]
        assert 'leaf5' not in neighbor_devices

        assert result['nodes_cleaned'] >= 1

    def test_delete_node_marks_host_orphaned(self, populated_files):
        """Verify deleting a node marks host connection as orphaned."""
        from persistence import remove_all_device_references, load_user_hosts

        result = remove_all_device_references(
            'leaf5',
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        # Check that desktop1's connection is marked orphaned
        hosts_data = load_user_hosts(populated_files['hosts'])
        for host in hosts_data.get('hosts', []):
            if 'desktop1' in host:
                connection = host['desktop1'].get('connection', {})
                assert connection.get('orphaned') is True
                assert connection.get('orphaned_target') == 'leaf5'

        assert result['hosts_cleaned'] == 1

    def test_delete_node_marks_firewall_orphaned(self, populated_files):
        """Verify deleting a node marks firewall interface as orphaned."""
        from persistence import remove_all_device_references, load_user_firewalls

        result = remove_all_device_references(
            'leaf5',
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        # Check that fw1's inside_interface is marked orphaned
        firewalls_data = load_user_firewalls(populated_files['firewalls'])
        for fw in firewalls_data.get('firewalls', []):
            if 'fw1' in fw:
                inside = fw['fw1'].get('inside_interface', {})
                assert inside.get('orphaned') is True
                assert inside.get('orphaned_target') == 'leaf5'

        assert result['firewalls_cleaned'] == 1

    def test_cleanup_returns_total_count(self, populated_files):
        """Verify cleanup returns total count of cleaned references."""
        from persistence import remove_all_device_references

        result = remove_all_device_references(
            'leaf5',
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        assert result['total'] == (
            result['nodes_cleaned'] +
            result['hosts_cleaned'] +
            result['firewalls_cleaned']
        )
        assert result['total'] >= 3  # At least node, host, and firewall references


class TestGetAllDeviceNames:
    """Tests for comprehensive device name collection."""

    @pytest.fixture
    def populated_files(self, tmp_path):
        """Create files with all device types."""
        topo_file = tmp_path / "topo_build.yml"
        nodes_file = tmp_path / "user_nodes.yaml"
        hosts_file = tmp_path / "user_hosts.yaml"
        firewalls_file = tmp_path / "user_firewalls.yaml"

        topo_file.write_text("nodes:\n  - spine1:\n      ip_addr: 192.168.0.10\n")
        nodes_file.write_text("version: 1\nnodes:\n  - leaf5:\n      ip_addr: 192.168.0.50\n")
        hosts_file.write_text("version: 1\nhosts:\n  - desktop1:\n      mgmt_ip: 192.168.0.51\n")
        firewalls_file.write_text("version: 1\nfirewalls:\n  - fw1:\n      mgmt_ip: 192.168.0.52\n")

        return {
            'topo': str(topo_file),
            'nodes': str(nodes_file),
            'hosts': str(hosts_file),
            'firewalls': str(firewalls_file)
        }

    def test_get_all_device_names_includes_all(self, populated_files):
        """Verify all device names are collected."""
        from validation import get_all_device_names

        names = get_all_device_names(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        assert 'spine1' in names
        assert 'leaf5' in names
        assert 'desktop1' in names
        assert 'fw1' in names

    def test_get_all_device_names_lowercase(self, populated_files):
        """Verify names are returned in lowercase."""
        from validation import get_all_device_names

        names = get_all_device_names(
            populated_files['topo'],
            populated_files['nodes'],
            populated_files['hosts'],
            populated_files['firewalls']
        )

        for name in names:
            assert name == name.lower(), f"Name '{name}' is not lowercase"
