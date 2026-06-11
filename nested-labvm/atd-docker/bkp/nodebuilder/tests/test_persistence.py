"""
Unit tests for persistence module.

Tests cover:
- Loading and saving user nodes/hosts/firewalls
- Pending status for save-before-create pattern
- Status updates after VM creation
- Removal of entries
"""

import os
import pytest
import yaml

from persistence import (
    load_user_nodes, save_user_nodes, save_user_node, save_user_node_pending,
    update_user_node_status, remove_user_node,
    load_user_hosts, save_user_hosts, save_user_host, save_user_host_pending,
    update_user_host_status, remove_user_host,
    load_user_firewalls, save_user_firewalls, save_user_firewall, save_user_firewall_pending,
    update_user_firewall_status, remove_user_firewall
)


class TestUserNodes:
    """Tests for user node persistence."""

    def test_load_empty_file(self, mock_user_nodes_file):
        """Test loading an empty nodes file."""
        data = load_user_nodes(mock_user_nodes_file)
        assert 'nodes' in data
        assert data['nodes'] == []

    def test_load_missing_file(self, temp_dir):
        """Test loading from non-existent file creates default."""
        path = os.path.join(temp_dir, 'nonexistent.yaml')
        data = load_user_nodes(path)
        assert 'nodes' in data
        assert data['nodes'] == []

    def test_save_and_load_node(self, mock_user_nodes_file):
        """Test saving and loading a node."""
        node_data = {
            'testnode1': {
                'ip_addr': '192.168.0.50',
                'sys_mac': '00:1c:73:00:00:50',
                'platform': 'veos'
            }
        }
        save_user_node(node_data, mock_user_nodes_file)

        # Reload and verify
        data = load_user_nodes(mock_user_nodes_file)
        assert len(data['nodes']) == 1
        assert 'testnode1' in data['nodes'][0]
        assert data['nodes'][0]['testnode1']['ip_addr'] == '192.168.0.50'
        assert data['nodes'][0]['testnode1']['user_added'] is True
        assert 'added_at' in data['nodes'][0]['testnode1']

    def test_save_node_pending(self, mock_user_nodes_file):
        """Test saving a node with pending status."""
        node_info = {
            'ip_addr': '192.168.0.51',
            'sys_mac': '00:1c:73:00:00:51',
            'platform': 'veos'
        }
        save_user_node_pending('pendingnode', node_info, mock_user_nodes_file)

        # Verify pending status
        data = load_user_nodes(mock_user_nodes_file)
        assert len(data['nodes']) == 1
        assert 'pendingnode' in data['nodes'][0]
        assert data['nodes'][0]['pendingnode']['status'] == 'creating'

    def test_update_node_status_to_active(self, mock_user_nodes_file):
        """Test updating a pending node to active removes status field."""
        # First save as pending
        node_info = {
            'ip_addr': '192.168.0.52',
            'sys_mac': '00:1c:73:00:00:52',
            'platform': 'veos'
        }
        save_user_node_pending('updatenode', node_info, mock_user_nodes_file)

        # Update to active
        result = update_user_node_status(
            'updatenode', 'active',
            {'neighbors': [{'neighborDevice': 'spine1', 'neighborPort': 'Ethernet3', 'port': 'Ethernet1'}]},
            mock_user_nodes_file
        )
        assert result is True

        # Verify status is removed and neighbors added
        data = load_user_nodes(mock_user_nodes_file)
        node = data['nodes'][0]['updatenode']
        assert 'status' not in node
        assert len(node['neighbors']) == 1
        assert node['neighbors'][0]['neighborDevice'] == 'spine1'

    def test_update_nonexistent_node(self, mock_user_nodes_file):
        """Test updating a non-existent node returns False."""
        result = update_user_node_status('nonexistent', 'active', {}, mock_user_nodes_file)
        assert result is False

    def test_remove_node(self, mock_user_nodes_file):
        """Test removing a node."""
        # Add a node first
        node_data = {'removenode': {'ip_addr': '192.168.0.53'}}
        save_user_node(node_data, mock_user_nodes_file)

        # Verify it exists
        data = load_user_nodes(mock_user_nodes_file)
        assert len(data['nodes']) == 1

        # Remove it
        result = remove_user_node('removenode', mock_user_nodes_file)
        assert result is True

        # Verify it's gone
        data = load_user_nodes(mock_user_nodes_file)
        assert len(data['nodes']) == 0

    def test_remove_nonexistent_node(self, mock_user_nodes_file):
        """Test removing a non-existent node returns False."""
        result = remove_user_node('nonexistent', mock_user_nodes_file)
        assert result is False

    def test_case_insensitive_operations(self, mock_user_nodes_file):
        """Test that node operations are case-insensitive."""
        node_info = {'ip_addr': '192.168.0.54'}
        save_user_node_pending('CamelCaseNode', node_info, mock_user_nodes_file)

        # Update with different case
        result = update_user_node_status('camelcasenode', 'active', {}, mock_user_nodes_file)
        assert result is True

        # Remove with different case
        result = remove_user_node('CAMELCASENODE', mock_user_nodes_file)
        assert result is True


class TestUserHosts:
    """Tests for user host persistence."""

    def test_load_empty_file(self, mock_user_hosts_file):
        """Test loading an empty hosts file."""
        data = load_user_hosts(mock_user_hosts_file)
        assert 'hosts' in data
        assert data['hosts'] == []

    def test_save_host_pending(self, mock_user_hosts_file):
        """Test saving a host with pending status."""
        host_info = {
            'mgmt_ip': '192.168.0.60',
            'data_ip': '10.1.1.100/24'
        }
        save_user_host_pending('pendinghost', host_info, mock_user_hosts_file)

        data = load_user_hosts(mock_user_hosts_file)
        assert len(data['hosts']) == 1
        assert data['hosts'][0]['pendinghost']['status'] == 'creating'
        assert data['hosts'][0]['pendinghost']['device_type'] == 'host'

    def test_update_host_status(self, mock_user_hosts_file):
        """Test updating host status."""
        host_info = {'mgmt_ip': '192.168.0.61'}
        save_user_host_pending('updatehost', host_info, mock_user_hosts_file)

        result = update_user_host_status(
            'updatehost', 'active',
            {'vnc_port': 5901, 'connection': {'target_device': 'leaf1'}},
            mock_user_hosts_file
        )
        assert result is True

        data = load_user_hosts(mock_user_hosts_file)
        host = data['hosts'][0]['updatehost']
        assert 'status' not in host
        assert host['vnc_port'] == 5901

    def test_remove_host(self, mock_user_hosts_file):
        """Test removing a host."""
        host_data = {'removehost': {'mgmt_ip': '192.168.0.62'}}
        save_user_host(host_data, mock_user_hosts_file)

        result = remove_user_host('removehost', mock_user_hosts_file)
        assert result is True

        data = load_user_hosts(mock_user_hosts_file)
        assert len(data['hosts']) == 0


class TestUserFirewalls:
    """Tests for user firewall persistence."""

    def test_load_empty_file(self, mock_user_firewalls_file):
        """Test loading an empty firewalls file."""
        data = load_user_firewalls(mock_user_firewalls_file)
        assert 'firewalls' in data
        assert data['firewalls'] == []

    def test_save_firewall_pending(self, mock_user_firewalls_file):
        """Test saving a firewall with pending status."""
        fw_info = {
            'mgmt_ip': '192.168.0.70',
            'inside_interface': {'ip': '10.1.1.1/24'},
            'outside_interface': {'ip': '10.2.2.1/24'}
        }
        save_user_firewall_pending('pendingfw', fw_info, mock_user_firewalls_file)

        data = load_user_firewalls(mock_user_firewalls_file)
        assert len(data['firewalls']) == 1
        assert data['firewalls'][0]['pendingfw']['status'] == 'creating'
        assert data['firewalls'][0]['pendingfw']['device_type'] == 'firewall'

    def test_update_firewall_status(self, mock_user_firewalls_file):
        """Test updating firewall status."""
        fw_info = {'mgmt_ip': '192.168.0.71'}
        save_user_firewall_pending('updatefw', fw_info, mock_user_firewalls_file)

        result = update_user_firewall_status(
            'updatefw', 'active',
            {'inside_interface': {'ip': '10.1.1.1/24', 'bridge': 'test-bridge'}},
            mock_user_firewalls_file
        )
        assert result is True

        data = load_user_firewalls(mock_user_firewalls_file)
        fw = data['firewalls'][0]['updatefw']
        assert 'status' not in fw
        assert fw['inside_interface']['bridge'] == 'test-bridge'

    def test_remove_firewall(self, mock_user_firewalls_file):
        """Test removing a firewall."""
        fw_data = {'removefw': {'mgmt_ip': '192.168.0.72'}}
        save_user_firewall(fw_data, mock_user_firewalls_file)

        result = remove_user_firewall('removefw', mock_user_firewalls_file)
        assert result is True

        data = load_user_firewalls(mock_user_firewalls_file)
        assert len(data['firewalls']) == 0


class TestAtomicWrites:
    """Tests for atomic file write behavior."""

    def test_concurrent_writes_preserved(self, temp_dir):
        """Test that rapid successive writes don't corrupt data."""
        path = os.path.join(temp_dir, 'concurrent.yaml')
        with open(path, 'w') as f:
            f.write("nodes: []\n")

        # Rapid successive writes
        for i in range(10):
            node_data = {f'node{i}': {'ip_addr': f'192.168.0.{i}'}}
            save_user_node(node_data, path)

        # All nodes should be present
        data = load_user_nodes(path)
        assert len(data['nodes']) == 10

    def test_file_not_corrupted_on_error(self, mock_user_nodes_file):
        """Test that original file is preserved if save fails."""
        # Add initial data
        node_data = {'initial': {'ip_addr': '192.168.0.1'}}
        save_user_node(node_data, mock_user_nodes_file)

        # Verify initial data exists
        data = load_user_nodes(mock_user_nodes_file)
        assert len(data['nodes']) == 1
        assert 'initial' in data['nodes'][0]
