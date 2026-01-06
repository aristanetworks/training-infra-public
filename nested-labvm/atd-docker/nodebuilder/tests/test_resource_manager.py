"""
Unit tests for resource_manager module.

Tests cover:
- VM operations (destroy, undefine, exists)
- Cleanup operations
- Reconciliation functionality
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock

from resource_manager import ResourceManager, get_resource_manager


class TestResourceManagerVM:
    """Tests for VM operations in ResourceManager."""

    @pytest.fixture
    def resource_mgr(self):
        """Get a fresh ResourceManager instance."""
        return ResourceManager()

    def test_vm_exists_returns_true(self, resource_mgr):
        """Test vm_exists returns True for existing VM."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            assert resource_mgr.vm_exists('test-vm') is True

    def test_vm_exists_returns_false(self, resource_mgr):
        """Test vm_exists returns False for non-existing VM."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout='', stderr='error')
            assert resource_mgr.vm_exists('nonexistent-vm') is False

    def test_get_vm_state_running(self, resource_mgr):
        """Test get_vm_state returns 'running' for running VM."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='running\n', stderr='')
            state = resource_mgr.get_vm_state('test-vm')
            assert state == 'running'

    def test_get_vm_state_shut_off(self, resource_mgr):
        """Test get_vm_state returns 'shut off' for stopped VM."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='shut off\n', stderr='')
            state = resource_mgr.get_vm_state('test-vm')
            assert state == 'shut off'

    def test_get_vm_state_unknown(self, resource_mgr):
        """Test get_vm_state returns 'unknown' on error."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout='', stderr='error')
            state = resource_mgr.get_vm_state('test-vm')
            assert state == 'unknown'

    def test_destroy_vm_success(self, resource_mgr):
        """Test destroy_vm succeeds."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            result = resource_mgr.destroy_vm('test-vm')
            assert result['status'] == 'destroyed'

    def test_destroy_vm_not_running(self, resource_mgr):
        """Test destroy_vm handles not running state."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=1, stdout='', stderr='domain is not running'
            )
            result = resource_mgr.destroy_vm('test-vm')
            assert result['status'] == 'not_running'

    def test_undefine_vm_success(self, resource_mgr):
        """Test undefine_vm succeeds."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            result = resource_mgr.undefine_vm('test-vm')
            assert result['status'] == 'undefined'


class TestCleanupConnection:
    """Tests for cleanup_connection method."""

    @pytest.fixture
    def resource_mgr(self):
        return ResourceManager()

    def test_cleanup_empty_connection(self, resource_mgr):
        """Test cleanup with None connection."""
        result = resource_mgr.cleanup_connection(None)
        assert result['interface_detached'] is False
        assert result['bridge_deleted'] is False
        assert len(result['errors']) == 0

    def test_cleanup_connection_success(self, resource_mgr):
        """Test successful connection cleanup."""
        with patch('resource_manager.get_vm_interfaces') as mock_interfaces:
            with patch('resource_manager.detach_interface_from_vm') as mock_detach:
                with patch('resource_manager.delete_ovs_bridge') as mock_delete_bridge:
                    mock_interfaces.return_value = [
                        {'source': 'test-bridge', 'mac': '00:11:22:33:44:55'}
                    ]
                    mock_detach.return_value = {'status': 'detached'}
                    mock_delete_bridge.return_value = {'status': 'deleted'}

                    connection = {
                        'bridge': 'test-bridge',
                        'target_device': 'target-vm'
                    }
                    result = resource_mgr.cleanup_connection(connection)

                    assert result['interface_detached'] is True
                    assert result['bridge_deleted'] is True
                    assert result['target_device'] == 'target-vm'

    def test_cleanup_connection_no_interface_found(self, resource_mgr):
        """Test cleanup when interface not found."""
        with patch('resource_manager.get_vm_interfaces') as mock_interfaces:
            with patch('resource_manager.delete_ovs_bridge') as mock_delete_bridge:
                mock_interfaces.return_value = []  # No interfaces
                mock_delete_bridge.return_value = {'status': 'deleted'}

                connection = {
                    'bridge': 'nonexistent-bridge',
                    'target_device': 'target-vm'
                }
                result = resource_mgr.cleanup_connection(connection)

                assert result['interface_detached'] is False
                assert result['bridge_deleted'] is True
                assert len(result['errors']) == 0  # Not an error

    def test_cleanup_connection_detach_error(self, resource_mgr):
        """Test cleanup when detach fails."""
        with patch('resource_manager.get_vm_interfaces') as mock_interfaces:
            with patch('resource_manager.detach_interface_from_vm') as mock_detach:
                with patch('resource_manager.delete_ovs_bridge') as mock_delete_bridge:
                    mock_interfaces.return_value = [
                        {'source': 'test-bridge', 'mac': '00:11:22:33:44:55'}
                    ]
                    mock_detach.side_effect = RuntimeError("Detach failed")
                    mock_delete_bridge.return_value = {'status': 'deleted'}

                    connection = {
                        'bridge': 'test-bridge',
                        'target_device': 'target-vm'
                    }
                    result = resource_mgr.cleanup_connection(connection)

                    assert result['interface_detached'] is False
                    assert result['bridge_deleted'] is True  # Should still try
                    assert len(result['errors']) == 1
                    assert 'Detach failed' in result['errors'][0]


class TestReconciliation:
    """Tests for reconcile_resources method."""

    @pytest.fixture
    def resource_mgr(self):
        return ResourceManager()

    def test_reconcile_dry_run_no_issues(self, resource_mgr, mock_user_nodes_file,
                                          mock_user_hosts_file, mock_user_firewalls_file):
        """Test dry run with no issues."""
        with patch.object(resource_mgr, 'vm_exists', return_value=True):
            with patch('subprocess.run') as mock_run:
                # Mock virsh list --all
                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                    with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                        with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                            result = resource_mgr.reconcile_resources(dry_run=True)

                            assert result['dry_run'] is True
                            assert len(result['orphan_entries']) == 0
                            assert len(result['zombie_vms']) == 0

    def test_reconcile_finds_orphan_entries(self, resource_mgr, mock_user_nodes_file,
                                             mock_user_hosts_file, mock_user_firewalls_file):
        """Test that orphan entries are detected."""
        # Add a node to persistence
        from persistence import save_user_node
        save_user_node({'orphannode': {'ip_addr': '192.168.0.99'}}, mock_user_nodes_file)

        with patch.object(resource_mgr, 'vm_exists', return_value=False):  # VM doesn't exist
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                    with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                        with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                            result = resource_mgr.reconcile_resources(dry_run=True)

                            assert len(result['orphan_entries']) == 1
                            assert result['orphan_entries'][0]['name'] == 'orphannode'
                            assert result['orphan_entries'][0]['type'] == 'node'

    def test_reconcile_finds_stuck_creating(self, resource_mgr, mock_user_nodes_file,
                                             mock_user_hosts_file, mock_user_firewalls_file):
        """Test that stuck 'creating' status is detected."""
        # Add a node with 'creating' status
        from persistence import save_user_node_pending
        save_user_node_pending('stucknode', {'ip_addr': '192.168.0.98'}, mock_user_nodes_file)

        with patch.object(resource_mgr, 'vm_exists', return_value=True):  # VM exists
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                    with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                        with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                            result = resource_mgr.reconcile_resources(dry_run=True)

                            assert len(result['zombie_vms']) == 1
                            assert result['zombie_vms'][0]['name'] == 'stucknode'
                            assert result['zombie_vms'][0]['status'] == 'creating'

    def test_reconcile_fixes_orphan_entries(self, resource_mgr, mock_user_nodes_file,
                                             mock_user_hosts_file, mock_user_firewalls_file):
        """Test that orphan entries are removed in non-dry-run mode."""
        # Add an orphan node
        from persistence import save_user_node, load_user_nodes
        save_user_node({'orphannode': {'ip_addr': '192.168.0.99'}}, mock_user_nodes_file)

        with patch.object(resource_mgr, 'vm_exists', return_value=False):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                with patch.object(resource_mgr, 'cleanup_all_orphaned_bridges', return_value={'orphaned_found': [], 'deleted': []}):
                    with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                        with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                            with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                                result = resource_mgr.reconcile_resources(dry_run=False)

                                assert len(result['fixed']) >= 1
                                # Verify node was actually removed
                                data = load_user_nodes(mock_user_nodes_file)
                                assert len(data['nodes']) == 0


class TestDeleteVMWithCleanup:
    """Tests for delete_vm_with_cleanup method."""

    @pytest.fixture
    def resource_mgr(self):
        return ResourceManager()

    def test_delete_vm_success(self, resource_mgr, temp_dir):
        """Test successful VM deletion."""
        # Create fake disk file
        disk_dir = os.path.join(temp_dir, 'veos')
        os.makedirs(disk_dir)
        disk_path = os.path.join(disk_dir, 'test-vm.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake disk')

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            with patch('resource_manager.LIBVIRT_IMAGES_PATH', temp_dir):
                result = resource_mgr.delete_vm_with_cleanup('test-vm', 'veos', has_cidata=False)

                assert result['vm_destroyed'] is True
                assert result['vm_undefined'] is True
                assert result['disk_deleted'] is True

    def test_delete_vm_with_cidata(self, resource_mgr, temp_dir):
        """Test VM deletion with cloud-init ISO."""
        # Create fake disk and cidata files
        disk_dir = os.path.join(temp_dir, 'hosts')
        os.makedirs(disk_dir)

        disk_path = os.path.join(disk_dir, 'test-host.qcow2')
        cidata_path = os.path.join(disk_dir, 'test-host-cidata.iso')

        with open(disk_path, 'w') as f:
            f.write('fake disk')
        with open(cidata_path, 'w') as f:
            f.write('fake cidata')

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            with patch('resource_manager.LIBVIRT_IMAGES_PATH', temp_dir):
                result = resource_mgr.delete_vm_with_cleanup('test-host', 'hosts', has_cidata=True)

                assert result['disk_deleted'] is True
                assert result['cidata_deleted'] is True
                assert not os.path.exists(disk_path)
                assert not os.path.exists(cidata_path)


class TestIsUserCreatedBridge:
    """Tests for _is_user_created_bridge method.

    Critical for preventing deletion of original topology bridges.
    Nodebuilder bridges use 'x' separator, kvmbuilder bridges don't.
    """

    @pytest.fixture
    def resource_mgr(self):
        return ResourceManager()

    # Nodebuilder bridges (SHOULD match - contain 'x')
    def test_nodebuilder_veos_bridge(self, resource_mgr):
        """Test nodebuilder vEOS bridge with 'x' separator."""
        assert resource_mgr._is_user_created_bridge('le5x1-sp4x9') is True

    def test_nodebuilder_host_bridge(self, resource_mgr):
        """Test nodebuilder host bridge."""
        assert resource_mgr._is_user_created_bridge('cl1xet1-le3x5') is True

    def test_nodebuilder_firewall_bridge(self, resource_mgr):
        """Test nodebuilder firewall bridge."""
        assert resource_mgr._is_user_created_bridge('fw1xet1-sp1x12') is True

    def test_nodebuilder_borderleaf_bridge(self, resource_mgr):
        """Test nodebuilder borderleaf bridge."""
        assert resource_mgr._is_user_created_bridge('bo3x1-sp2x9') is True

    def test_nodebuilder_short_names(self, resource_mgr):
        """Test nodebuilder bridges with short device names."""
        assert resource_mgr._is_user_created_bridge('sp1x3-le2x4') is True

    # Original kvmbuilder bridges (should NOT match - no 'x')
    def test_kvmbuilder_spine_leaf(self, resource_mgr):
        """Test kvmbuilder spine-to-leaf bridge is protected."""
        assert resource_mgr._is_user_created_bridge('sp13-le13') is False

    def test_kvmbuilder_leaf_leaf_mlag(self, resource_mgr):
        """Test kvmbuilder leaf-to-leaf MLAG bridge is protected."""
        assert resource_mgr._is_user_created_bridge('le11-le21') is False

    def test_kvmbuilder_leaf_host(self, resource_mgr):
        """Test kvmbuilder leaf-to-host bridge is protected."""
        assert resource_mgr._is_user_created_bridge('le17-ho11') is False

    def test_kvmbuilder_spine_borderleaf(self, resource_mgr):
        """Test kvmbuilder spine-to-borderleaf bridge is protected."""
        assert resource_mgr._is_user_created_bridge('sp25-bo14') is False

    def test_kvmbuilder_various_patterns(self, resource_mgr):
        """Test various kvmbuilder bridge patterns are all protected."""
        kvmbuilder_bridges = [
            'sp13-le13', 'sp14-le23', 'sp15-le33', 'sp16-le43',
            'le11-le21', 'le12-le22', 'le31-le41', 'le32-le42',
            'le17-ho11', 'le19-ho21', 'le27-ho12', 'le29-ho22',
        ]
        for bridge in kvmbuilder_bridges:
            assert resource_mgr._is_user_created_bridge(bridge) is False, \
                f"Bridge {bridge} should be protected (kvmbuilder)"

    # System bridges (should NOT match)
    def test_system_bridges_not_matched(self, resource_mgr):
        """Test system bridges don't match user pattern."""
        system_bridges = ['oob_mgmt', 'br0', 'br1', 'br-mgmt', 'vmgmt']
        for bridge in system_bridges:
            assert resource_mgr._is_user_created_bridge(bridge) is False


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_resource_manager_returns_same_instance(self):
        """Test that get_resource_manager returns the same instance."""
        # Reset singleton
        import resource_manager as rm_module
        rm_module._resource_manager = None

        mgr1 = get_resource_manager()
        mgr2 = get_resource_manager()
        assert mgr1 is mgr2
