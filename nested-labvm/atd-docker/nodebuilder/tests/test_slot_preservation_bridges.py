"""
Tests for bridge lifecycle during slot preservation.

Covers the critical gaps:
1. delete_connection skips bridge deletion when slot is preserved
2. cleanup_node_bridges respects skip_bridges parameter
3. cleanup_connection keeps bridge when slot preserved
4. Old bridge cleanup on slot reuse (create_connection + slot_reuse)
5. cleanup_all_orphaned_bridges skips preserved bridges
6. cleanup_stale_orphaned_interfaces startup safety net
7. delete_node_completely passes preserved bridges to cleanup_node_bridges
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock, call

from connection_manager import ConnectionManager, Connection
from resource_manager import ResourceManager
from orphaned_interfaces import (
    record_orphaned_slot,
    load_orphaned_interfaces,
    get_orphaned_slots_for_device,
    has_orphaned_slots,
    cleanup_stale_orphaned_interfaces,
)


@pytest.fixture
def mock_orphaned_file(temp_dir):
    path = os.path.join(temp_dir, 'orphaned_interfaces.yaml')
    with open(path, 'w') as f:
        f.write("orphaned_interfaces: {}\n")
    return path


@pytest.fixture
def connection_manager():
    return ConnectionManager()


@pytest.fixture
def resource_mgr():
    return ResourceManager()


# =========================================================================
# Gap 1: delete_connection skips bridge deletion when slot preserved
# =========================================================================

class TestDeleteConnectionBridgePreservation:

    def test_bridge_not_deleted_when_slot_preserved(self, connection_manager):
        """Bridge must NOT be deleted when target slot was successfully preserved."""
        conn = Connection(
            source_device='node1', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='no1x1-sp1x5',
        )

        with patch('connection_manager.get_vm_interfaces') as mock_intf, \
             patch('connection_manager.detach_interface_from_vm') as mock_detach, \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br, \
             patch('connection_manager.record_orphaned_slot') as mock_record, \
             patch('connection_manager.extract_port_number', return_value=5), \
             patch('connection_manager.ENABLE_SLOT_PRESERVATION', True):

            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]
            mock_detach.return_value = {'status': 'detached'}

            result = connection_manager.delete_connection(
                conn, detach_from_source=True, detach_from_target=True,
                preserve_target_slot=True,
            )

        assert result['slot_preserved'] is True
        # Bridge must NOT have been deleted
        mock_del_br.assert_not_called()
        # Should have a keep_bridge step
        keep_steps = [s for s in result['steps'] if s['step'] == 'keep_bridge']
        assert len(keep_steps) == 1
        assert keep_steps[0]['status'] == 'preserved'

    def test_bridge_deleted_when_slot_preservation_fails(self, connection_manager):
        """Bridge must be deleted when slot preservation was requested but failed."""
        conn = Connection(
            source_device='node1', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='no1x1-sp1x5',
        )

        with patch('connection_manager.get_vm_interfaces') as mock_intf, \
             patch('connection_manager.detach_interface_from_vm') as mock_detach, \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br, \
             patch('connection_manager.record_orphaned_slot') as mock_record, \
             patch('connection_manager.extract_port_number', return_value=None), \
             patch('connection_manager.ENABLE_SLOT_PRESERVATION', True):

            # extract_port_number returns None -> falls back to detach
            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]
            mock_detach.return_value = {'status': 'detached'}
            mock_del_br.return_value = {'status': 'deleted'}

            result = connection_manager.delete_connection(
                conn, detach_from_source=True, detach_from_target=True,
                preserve_target_slot=True,
            )

        assert result['slot_preserved'] is False
        mock_del_br.assert_called_once_with('no1x1-sp1x5')

    def test_bridge_deleted_when_preservation_disabled(self, connection_manager):
        """Bridge must be deleted when slot preservation is disabled."""
        conn = Connection(
            source_device='node1', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='no1x1-sp1x5',
        )

        with patch('connection_manager.get_vm_interfaces') as mock_intf, \
             patch('connection_manager.detach_interface_from_vm') as mock_detach, \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br, \
             patch('connection_manager.ENABLE_SLOT_PRESERVATION', False):

            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]
            mock_detach.return_value = {'status': 'detached'}
            mock_del_br.return_value = {'status': 'deleted'}

            result = connection_manager.delete_connection(
                conn, preserve_target_slot=False,
            )

        assert result['slot_preserved'] is False
        mock_del_br.assert_called_once_with('no1x1-sp1x5')


# =========================================================================
# Gap 2: cleanup_node_bridges respects skip_bridges
# =========================================================================

class TestCleanupNodeBridgesSkip:

    def test_skip_bridges_are_not_deleted(self, resource_mgr):
        """Bridges in the skip set must not be deleted."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
                {'port': 'Ethernet2', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet6'},
            ]
        }

        with patch('resource_manager.delete_ovs_bridge') as mock_del, \
             patch('resource_manager.generate_bridge_name') as mock_gen:

            # Return predictable bridge names
            mock_gen.side_effect = ['br-keep', 'br-delete']
            mock_del.return_value = {'status': 'deleted'}

            deleted = resource_mgr.cleanup_node_bridges(
                'node1', node_info, skip_bridges={'br-keep'}
            )

        assert 'br-delete' in deleted
        assert 'br-keep' not in deleted
        # Only called once (for br-delete)
        mock_del.assert_called_once_with('br-delete')

    def test_no_skip_bridges_deletes_all(self, resource_mgr):
        """Without skip set, all bridges are deleted."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
                {'port': 'Ethernet2', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet6'},
            ]
        }

        with patch('resource_manager.delete_ovs_bridge') as mock_del, \
             patch('resource_manager.generate_bridge_name') as mock_gen:

            mock_gen.side_effect = ['br-a', 'br-b']
            mock_del.return_value = {'status': 'deleted'}

            deleted = resource_mgr.cleanup_node_bridges('node1', node_info)

        assert len(deleted) == 2
        assert mock_del.call_count == 2

    def test_empty_skip_set_deletes_all(self, resource_mgr):
        """An empty skip set behaves like no skip set."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
            ]
        }

        with patch('resource_manager.delete_ovs_bridge') as mock_del, \
             patch('resource_manager.generate_bridge_name', return_value='br-a'):

            mock_del.return_value = {'status': 'deleted'}
            deleted = resource_mgr.cleanup_node_bridges(
                'node1', node_info, skip_bridges=set()
            )

        assert len(deleted) == 1


# =========================================================================
# Gap 3: cleanup_connection keeps bridge when slot preserved
# =========================================================================

class TestCleanupConnectionSlotPreservation:

    def test_bridge_kept_when_slot_preserved(self, resource_mgr):
        """cleanup_connection must NOT delete bridge when slot was preserved."""
        connection = {
            'bridge': 'no1x1-sp1x5',
            'target_device': 'spine1',
            'target_port': 'Ethernet5',
        }

        with patch('resource_manager.get_vm_interfaces') as mock_intf, \
             patch('resource_manager.detach_interface_from_vm') as mock_detach, \
             patch('resource_manager.delete_ovs_bridge') as mock_del_br, \
             patch('config.ENABLE_SLOT_PRESERVATION', True), \
             patch('interface_manager.extract_port_number', return_value=5):

            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]

            with patch('orphaned_interfaces.record_orphaned_slot') as mock_record:
                mock_record.return_value = True
                result = resource_mgr.cleanup_connection(connection, 'test')

        assert result['slot_preserved'] is True
        assert result['bridge_deleted'] is False
        mock_del_br.assert_not_called()

    def test_bridge_deleted_when_slot_not_preserved(self, resource_mgr):
        """cleanup_connection must delete bridge when preservation is disabled."""
        connection = {
            'bridge': 'no1x1-sp1x5',
            'target_device': 'spine1',
            'target_port': 'Ethernet5',
        }

        with patch('resource_manager.get_vm_interfaces') as mock_intf, \
             patch('resource_manager.detach_interface_from_vm') as mock_detach, \
             patch('resource_manager.delete_ovs_bridge') as mock_del_br, \
             patch('config.ENABLE_SLOT_PRESERVATION', False):

            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]
            mock_detach.return_value = {'status': 'detached'}
            mock_del_br.return_value = {'status': 'deleted'}

            result = resource_mgr.cleanup_connection(connection, 'test')

        assert result['slot_preserved'] is False
        assert result['bridge_deleted'] is True
        mock_del_br.assert_called_once()

    def test_bridge_deleted_on_recording_failure(self, resource_mgr):
        """cleanup_connection must delete bridge when orphan recording fails."""
        connection = {
            'bridge': 'no1x1-sp1x5',
            'target_device': 'spine1',
            'target_port': 'Ethernet5',
        }

        with patch('resource_manager.get_vm_interfaces') as mock_intf, \
             patch('resource_manager.detach_interface_from_vm') as mock_detach, \
             patch('resource_manager.delete_ovs_bridge') as mock_del_br, \
             patch('config.ENABLE_SLOT_PRESERVATION', True), \
             patch('interface_manager.extract_port_number', return_value=5):

            mock_intf.return_value = [
                {'source': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05'}
            ]
            mock_detach.return_value = {'status': 'detached'}
            mock_del_br.return_value = {'status': 'deleted'}

            with patch('orphaned_interfaces.record_orphaned_slot') as mock_record:
                mock_record.side_effect = RuntimeError("disk full")
                result = resource_mgr.cleanup_connection(connection, 'test')

        # Recording failed -> fell back to detach -> bridge should be deleted
        assert result['slot_preserved'] is False
        assert result['interface_detached'] is True
        assert result['bridge_deleted'] is True


# =========================================================================
# Gap 4: Old bridge cleanup on slot reuse
# =========================================================================

class TestOldBridgeCleanupOnReuse:

    def test_create_connection_deletes_old_bridge(self, connection_manager):
        """create_connection must delete the old bridge after reusing orphaned slot."""
        conn = Connection(
            source_device='node2', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='no2x1-sp1x5',
        )

        orphaned_slot = {
            'slot_number': 5,
            'mac_address': '52:54:00:aa:bb:05',
            'old_bridge': 'no1x1-sp1x5',
        }

        with patch('connection_manager.ENABLE_SLOT_PRESERVATION', True), \
             patch('connection_manager.extract_port_number', return_value=5), \
             patch('connection_manager.get_orphaned_slot_by_port', return_value=orphaned_slot), \
             patch('connection_manager.create_ovs_bridge') as mock_create, \
             patch('connection_manager.update_interface_bridge') as mock_update, \
             patch('connection_manager.claim_orphaned_slot') as mock_claim, \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br:

            mock_create.return_value = {'status': 'created'}
            mock_update.return_value = {'status': 'updated', 'immediate': True}
            mock_del_br.return_value = {'status': 'deleted'}

            result = connection_manager.create_connection(conn)

        assert result['status'] == 'created'
        assert result['reused_orphaned_slot'] is True
        # Old bridge must be cleaned up
        mock_del_br.assert_called_once_with('no1x1-sp1x5')

    def test_create_connection_skips_old_bridge_if_same(self, connection_manager):
        """Don't delete old bridge if it happens to match new bridge name."""
        conn = Connection(
            source_device='node2', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='same-bridge',
        )

        orphaned_slot = {
            'slot_number': 5,
            'mac_address': '52:54:00:aa:bb:05',
            'old_bridge': 'same-bridge',  # Same as new
        }

        with patch('connection_manager.ENABLE_SLOT_PRESERVATION', True), \
             patch('connection_manager.extract_port_number', return_value=5), \
             patch('connection_manager.get_orphaned_slot_by_port', return_value=orphaned_slot), \
             patch('connection_manager.create_ovs_bridge') as mock_create, \
             patch('connection_manager.update_interface_bridge') as mock_update, \
             patch('connection_manager.claim_orphaned_slot'), \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br:

            mock_create.return_value = {'status': 'created'}
            mock_update.return_value = {'status': 'updated', 'immediate': True}

            connection_manager.create_connection(conn)

        # Should NOT try to delete the bridge we just created
        mock_del_br.assert_not_called()

    def test_old_bridge_cleanup_tolerates_missing_bridge(self, connection_manager):
        """Old bridge cleanup must not fail if bridge is already gone."""
        conn = Connection(
            source_device='node2', source_port='Ethernet1',
            target_device='spine1', target_port='Ethernet5',
            bridge_name='no2x1-sp1x5',
        )

        orphaned_slot = {
            'slot_number': 5,
            'mac_address': '52:54:00:aa:bb:05',
            'old_bridge': 'already-gone',
        }

        with patch('connection_manager.ENABLE_SLOT_PRESERVATION', True), \
             patch('connection_manager.extract_port_number', return_value=5), \
             patch('connection_manager.get_orphaned_slot_by_port', return_value=orphaned_slot), \
             patch('connection_manager.create_ovs_bridge') as mock_create, \
             patch('connection_manager.update_interface_bridge') as mock_update, \
             patch('connection_manager.claim_orphaned_slot'), \
             patch('connection_manager.delete_ovs_bridge') as mock_del_br:

            mock_create.return_value = {'status': 'created'}
            mock_update.return_value = {'status': 'updated', 'immediate': True}
            mock_del_br.side_effect = RuntimeError("no such bridge")

            # Must not raise
            result = connection_manager.create_connection(conn)

        assert result['status'] == 'created'

    def test_slot_reuse_deletes_old_bridge(self):
        """attach_interface_with_slot_reuse must delete old bridge on success."""
        from slot_reuse import attach_interface_with_slot_reuse

        orphaned_slot = {
            'slot_number': 5,
            'mac_address': '52:54:00:aa:bb:05',
            'old_bridge': 'old-br',
        }

        with patch('config.ENABLE_SLOT_PRESERVATION', True), \
             patch('interface_manager.extract_port_number', return_value=5), \
             patch('orphaned_interfaces.get_orphaned_slot_by_port', return_value=orphaned_slot), \
             patch('interface_manager.update_interface_bridge') as mock_update, \
             patch('orphaned_interfaces.claim_orphaned_slot'), \
             patch('interface_manager.delete_ovs_bridge') as mock_del_br:

            mock_update.return_value = {'status': 'updated'}
            mock_del_br.return_value = {'status': 'deleted'}

            result = attach_interface_with_slot_reuse(
                target_device='spine1',
                target_port='Ethernet5',
                bridge_name='new-br',
            )

        assert result.reused_slot is True
        mock_del_br.assert_called_once_with('old-br')


# =========================================================================
# Gap 5: cleanup_all_orphaned_bridges skips preserved bridges
# =========================================================================

class TestCleanupAllOrphanedBridgesPreserved:

    def test_preserved_bridges_are_skipped(self, resource_mgr):
        """Bridges in orphaned slot registry must not be deleted during reconciliation."""
        with patch('subprocess.run') as mock_run, \
             patch.object(resource_mgr, '_get_expected_bridges_from_persistence', return_value=set()), \
             patch.object(resource_mgr, '_is_user_created_bridge', return_value=True), \
             patch.object(resource_mgr, '_get_bridge_port_count', return_value=1), \
             patch('resource_manager.delete_ovs_bridge') as mock_del_br, \
             patch('orphaned_interfaces.list_all_orphaned_slots') as mock_orphans:

            # OVS lists two bridges
            mock_run.return_value = Mock(
                returncode=0, stdout='preserved-br\norphaned-br\n', stderr=''
            )
            # One of them is in the orphaned registry
            mock_orphans.return_value = {
                'spine1': [{'slot_number': 5, 'old_bridge': 'preserved-br', 'mac_address': '52:54:00:aa:bb:05'}]
            }
            mock_del_br.return_value = {'status': 'deleted'}

            result = resource_mgr.cleanup_all_orphaned_bridges()

        assert result['skipped_preserved'] == 1
        assert 'orphaned-br' in result['deleted']
        assert 'preserved-br' not in result['deleted']
        mock_del_br.assert_called_once_with('orphaned-br')

    def test_no_orphaned_slots_deletes_all_orphaned(self, resource_mgr):
        """When no orphaned slots exist, all low-port-count bridges are deleted."""
        with patch('subprocess.run') as mock_run, \
             patch.object(resource_mgr, '_get_expected_bridges_from_persistence', return_value=set()), \
             patch.object(resource_mgr, '_is_user_created_bridge', return_value=True), \
             patch.object(resource_mgr, '_get_bridge_port_count', return_value=0), \
             patch('resource_manager.delete_ovs_bridge') as mock_del_br, \
             patch('orphaned_interfaces.list_all_orphaned_slots') as mock_orphans:

            mock_run.return_value = Mock(
                returncode=0, stdout='br-a\nbr-b\n', stderr=''
            )
            mock_orphans.return_value = {}
            mock_del_br.return_value = {'status': 'deleted'}

            result = resource_mgr.cleanup_all_orphaned_bridges()

        assert result['skipped_preserved'] == 0
        assert len(result['deleted']) == 2


# =========================================================================
# Gap 6: cleanup_stale_orphaned_interfaces startup safety net
# =========================================================================

class TestCleanupStaleOrphanedInterfaces:

    def _mock_subprocess(self, bridges_stdout, vms_stdout, domiflist_map):
        """Helper to build a subprocess mock that routes by command."""
        def side_effect(cmd, **kwargs):
            if cmd[0] == 'ovs-vsctl' and cmd[1] == 'list-br':
                return Mock(returncode=0, stdout=bridges_stdout, stderr='')
            if cmd[0] == 'virsh' and cmd[1] == 'list':
                return Mock(returncode=0, stdout=vms_stdout, stderr='')
            if cmd[0] == 'virsh' and cmd[1] == 'domiflist':
                vm = cmd[2]
                return Mock(
                    returncode=0,
                    stdout=domiflist_map.get(vm, ''), stderr=''
                )
            if cmd[0] == 'virsh' and cmd[1] == 'domstate':
                return Mock(returncode=0, stdout='shut off\n', stderr='')
            if cmd[0] == 'virsh' and cmd[1] == 'start':
                return Mock(returncode=0, stdout='', stderr='')
            return Mock(returncode=0, stdout='', stderr='')
        return side_effect

    def test_recreates_bridge_for_recorded_orphan(self, mock_orphaned_file):
        """Startup must recreate missing bridge when interface matches orphaned slot."""
        record_orphaned_slot(
            target_device='spine1', slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='no1x1-sp1x5',
            path=mock_orphaned_file,
        )

        domiflist = (
            "Interface  Type    Source         Model   MAC\n"
            "---------------------------------------------------\n"
            "vnet0      bridge  vmgmt          virtio  52:54:00:00:00:01\n"
            "vnet5      bridge  no1x1-sp1x5    virtio  52:54:00:aa:bb:05\n"
        )

        # Wrap load_orphaned_interfaces to use our temp file
        real_load = load_orphaned_interfaces

        with patch('orphaned_interfaces.load_orphaned_interfaces', lambda: real_load(mock_orphaned_file)), \
             patch('subprocess.run') as mock_run, \
             patch('interface_manager.create_ovs_bridge') as mock_create:

            mock_run.side_effect = self._mock_subprocess(
                bridges_stdout='vmgmt\n',  # no1x1-sp1x5 is missing
                vms_stdout='spine1\n',
                domiflist_map={'spine1': domiflist},
            )
            mock_create.return_value = {'status': 'created'}

            result = cleanup_stale_orphaned_interfaces()

        assert result['bridges_recreated'] == 1
        assert result['detached_count'] == 0
        mock_create.assert_called_once_with('no1x1-sp1x5')
        assert 'spine1' in result['devices_bridges_recreated']

    def test_detaches_unrecorded_stale_interface(self, mock_orphaned_file):
        """Startup must detach interfaces that don't match any orphaned slot."""
        # No orphaned slots recorded

        domiflist = (
            "Interface  Type    Source         Model   MAC\n"
            "---------------------------------------------------\n"
            "vnet0      bridge  vmgmt          virtio  52:54:00:00:00:01\n"
            "vnet5      bridge  gone-bridge    virtio  52:54:00:ff:ff:ff\n"
        )

        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file), \
             patch('subprocess.run') as mock_run, \
             patch('interface_manager.detach_interface_from_vm') as mock_detach:

            mock_run.side_effect = self._mock_subprocess(
                bridges_stdout='vmgmt\n',
                vms_stdout='spine1\n',
                domiflist_map={'spine1': domiflist},
            )
            mock_detach.return_value = {'status': 'detached'}

            result = cleanup_stale_orphaned_interfaces()

        assert result['detached_count'] == 1
        assert result['bridges_recreated'] == 0
        mock_detach.assert_called_once_with('spine1', '52:54:00:ff:ff:ff')

    def test_fallback_to_detach_on_recreation_failure(self, mock_orphaned_file):
        """If bridge recreation fails, startup must fall back to detaching."""
        record_orphaned_slot(
            target_device='spine1', slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='no1x1-sp1x5',
            path=mock_orphaned_file,
        )

        domiflist = (
            "Interface  Type    Source         Model   MAC\n"
            "---------------------------------------------------\n"
            "vnet5      bridge  no1x1-sp1x5    virtio  52:54:00:aa:bb:05\n"
        )

        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file), \
             patch('subprocess.run') as mock_run, \
             patch('interface_manager.create_ovs_bridge') as mock_create, \
             patch('interface_manager.detach_interface_from_vm') as mock_detach:

            mock_run.side_effect = self._mock_subprocess(
                bridges_stdout='',
                vms_stdout='spine1\n',
                domiflist_map={'spine1': domiflist},
            )
            mock_create.side_effect = RuntimeError("OVS down")
            mock_detach.return_value = {'status': 'detached'}

            result = cleanup_stale_orphaned_interfaces()

        assert result['bridges_recreated'] == 0
        assert result['detached_count'] == 1
        mock_detach.assert_called_once()

    def test_restarts_vm_after_bridge_recreation(self, mock_orphaned_file):
        """VMs that had bridges recreated must be started if shut off."""
        record_orphaned_slot(
            target_device='spine1', slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='no1x1-sp1x5',
            path=mock_orphaned_file,
        )

        domiflist = (
            "Interface  Type    Source         Model   MAC\n"
            "---------------------------------------------------\n"
            "vnet5      bridge  no1x1-sp1x5    virtio  52:54:00:aa:bb:05\n"
        )

        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file), \
             patch('subprocess.run') as mock_run, \
             patch('interface_manager.create_ovs_bridge') as mock_create:

            mock_run.side_effect = self._mock_subprocess(
                bridges_stdout='',
                vms_stdout='spine1\n',
                domiflist_map={'spine1': domiflist},
            )
            mock_create.return_value = {'status': 'created'}

            result = cleanup_stale_orphaned_interfaces()

        assert 'spine1' in result['vms_restarted']

    def test_skips_existing_bridges(self, mock_orphaned_file):
        """Interfaces pointing to existing bridges must be left alone."""
        domiflist = (
            "Interface  Type    Source         Model   MAC\n"
            "---------------------------------------------------\n"
            "vnet0      bridge  vmgmt          virtio  52:54:00:00:00:01\n"
            "vnet5      bridge  healthy-br     virtio  52:54:00:aa:bb:05\n"
        )

        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file), \
             patch('subprocess.run') as mock_run:

            mock_run.side_effect = self._mock_subprocess(
                bridges_stdout='vmgmt\nhealthy-br\n',
                vms_stdout='spine1\n',
                domiflist_map={'spine1': domiflist},
            )

            result = cleanup_stale_orphaned_interfaces()

        assert result['detached_count'] == 0
        assert result['bridges_recreated'] == 0


# =========================================================================
# Gap 7: delete_node_completely passes preserved bridges
# =========================================================================

class TestDeleteNodeCompletelyBridgeFlow:

    def test_preserved_bridges_passed_to_cleanup(self, resource_mgr):
        """delete_node_completely must skip preserved bridges in bridge cleanup."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
                {'port': 'Ethernet2', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet6'},
            ]
        }

        # Mock detach_all_node_interfaces to return one preserved, one detached
        mock_detach_results = [
            {
                'target_device': 'spine1', 'target_port': 'Ethernet5',
                'bridge': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05',
                'status': 'slot_preserved', 'interface_detached': False,
            },
            {
                'target_device': 'spine1', 'target_port': 'Ethernet6',
                'bridge': 'no1x2-sp1x6', 'mac': '52:54:00:aa:bb:06',
                'status': 'detached', 'interface_detached': True,
            },
        ]

        with patch.object(resource_mgr, 'destroy_vm', return_value={'status': 'destroyed'}), \
             patch.object(resource_mgr, 'detach_all_node_interfaces', return_value=mock_detach_results), \
             patch.object(resource_mgr, 'cleanup_node_bridges') as mock_cleanup, \
             patch.object(resource_mgr, 'undefine_vm', return_value={'status': 'undefined'}), \
             patch.object(resource_mgr, 'delete_vm_disk', return_value={'status': 'deleted', 'path': '/x'}):

            mock_cleanup.return_value = ['no1x2-sp1x6']

            result = resource_mgr.delete_node_completely('node1', node_info)

        # Verify cleanup_node_bridges was called with skip_bridges containing preserved bridge
        mock_cleanup.assert_called_once()
        call_args = mock_cleanup.call_args
        skip_set = call_args[1].get('skip_bridges') or (call_args[0][2] if len(call_args[0]) > 2 else None)
        assert skip_set is not None
        assert 'no1x1-sp1x5' in skip_set
        assert 'no1x2-sp1x6' not in skip_set

    def test_no_preserved_bridges_passes_none(self, resource_mgr):
        """When no slots are preserved, skip_bridges should be None."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
            ]
        }

        mock_detach_results = [
            {
                'target_device': 'spine1', 'target_port': 'Ethernet5',
                'bridge': 'no1x1-sp1x5', 'mac': '52:54:00:aa:bb:05',
                'status': 'detached', 'interface_detached': True,
            },
        ]

        with patch.object(resource_mgr, 'destroy_vm', return_value={'status': 'destroyed'}), \
             patch.object(resource_mgr, 'detach_all_node_interfaces', return_value=mock_detach_results), \
             patch.object(resource_mgr, 'cleanup_node_bridges') as mock_cleanup, \
             patch.object(resource_mgr, 'undefine_vm', return_value={'status': 'undefined'}), \
             patch.object(resource_mgr, 'delete_vm_disk', return_value={'status': 'deleted', 'path': '/x'}):

            mock_cleanup.return_value = ['no1x1-sp1x5']
            resource_mgr.delete_node_completely('node1', node_info)

        call_args = mock_cleanup.call_args
        skip_set = call_args[1].get('skip_bridges')
        # Empty set should be passed as None
        assert skip_set is None

    def test_detach_exception_still_runs_cleanup(self, resource_mgr):
        """If detach_all_node_interfaces raises, bridge cleanup still runs."""
        node_info = {
            'neighbors': [
                {'port': 'Ethernet1', 'neighborDevice': 'spine1', 'neighborPort': 'Ethernet5'},
            ]
        }

        with patch.object(resource_mgr, 'destroy_vm', return_value={'status': 'destroyed'}), \
             patch.object(resource_mgr, 'detach_all_node_interfaces', side_effect=RuntimeError("boom")), \
             patch.object(resource_mgr, 'cleanup_node_bridges') as mock_cleanup, \
             patch.object(resource_mgr, 'undefine_vm', return_value={'status': 'undefined'}), \
             patch.object(resource_mgr, 'delete_vm_disk', return_value={'status': 'deleted', 'path': '/x'}):

            mock_cleanup.return_value = []

            result = resource_mgr.delete_node_completely('node1', node_info)

        # Bridge cleanup should still be called (with no skip)
        mock_cleanup.assert_called_once()
        # Should have an error recorded
        assert any(e['step'] == 'detach_interfaces' for e in result['errors'])
