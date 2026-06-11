"""
Integration tests for interface slot preservation feature.

These tests verify the complete flow of:
1. Deleting a device and preserving the target interface slot
2. Adding a new device and reusing the orphaned slot
3. Reset-all clearing orphaned slots

Tests use mocking to simulate virsh/OVS commands.
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock

from connection_manager import ConnectionManager, Connection
from orphaned_interfaces import (
    load_orphaned_interfaces,
    get_orphaned_slots_for_device,
    get_next_orphaned_slot,
    record_orphaned_slot,
    clear_all_orphaned_slots,
    has_orphaned_slots
)
from interface_manager import find_next_available_port


@pytest.fixture
def mock_orphaned_file(temp_dir):
    """Create a temporary orphaned_interfaces.yaml file."""
    path = os.path.join(temp_dir, 'orphaned_interfaces.yaml')
    with open(path, 'w') as f:
        f.write("orphaned_interfaces: {}\n")
    return path


@pytest.fixture
def connection_manager():
    """Create a ConnectionManager instance."""
    return ConnectionManager()


@pytest.fixture
def mock_vm_operations():
    """Mock all VM-related operations."""
    with patch('connection_manager.create_ovs_bridge') as mock_create_bridge:
        with patch('connection_manager.delete_ovs_bridge') as mock_delete_bridge:
            with patch('connection_manager.attach_interface_to_vm') as mock_attach:
                with patch('connection_manager.detach_interface_from_vm') as mock_detach:
                    with patch('connection_manager.update_interface_bridge') as mock_update:
                        with patch('connection_manager.get_vm_interfaces') as mock_interfaces:
                            # Default successful responses
                            mock_create_bridge.return_value = {'status': 'created'}
                            mock_delete_bridge.return_value = {'status': 'deleted'}
                            mock_attach.return_value = {'status': 'attached'}
                            mock_detach.return_value = {'status': 'detached'}
                            mock_update.return_value = {
                                'status': 'updated',
                                'immediate': True
                            }
                            mock_interfaces.return_value = []

                            yield {
                                'create_bridge': mock_create_bridge,
                                'delete_bridge': mock_delete_bridge,
                                'attach': mock_attach,
                                'detach': mock_detach,
                                'update': mock_update,
                                'interfaces': mock_interfaces
                            }


class TestSlotPreservationFlow:
    """Test the complete slot preservation workflow."""

    def test_delete_preserves_slot(
        self, connection_manager, mock_vm_operations, mock_orphaned_file
    ):
        """Test that deleting a connection preserves the target slot."""
        # Setup: Mock finding the interface MAC
        mock_vm_operations['interfaces'].return_value = [
            {
                'interface': 'vnet5',
                'type': 'bridge',
                'source': 'sp15-node11',
                'mac': '52:54:00:aa:bb:05'
            }
        ]

        # Create connection object
        conn = Connection(
            source_device='node1',
            source_port='Ethernet1',
            target_device='spine1',
            target_port='Ethernet5',
            bridge_name='sp15-node11'
        )

        # Delete with slot preservation enabled
        with patch('connection_manager.ENABLE_SLOT_PRESERVATION', True):
            with patch('connection_manager.record_orphaned_slot') as mock_record:
                with patch('connection_manager.extract_port_number', return_value=5):
                    result = connection_manager.delete_connection(
                        conn,
                        preserve_target_slot=True
                    )

        # Verify slot was preserved (not detached)
        assert result['status'] in ('deleted', 'deleted_with_errors')
        assert result['slot_preserved'] is True

        # Verify detach was NOT called for target (only source)
        # The source device should be detached, target should be preserved
        detach_calls = mock_vm_operations['detach'].call_args_list
        target_detached = any(
            call[0][0] == 'spine1' for call in detach_calls
        )
        assert not target_detached, "Target interface should not be detached when preserving slot"

    def test_create_reuses_orphaned_slot(
        self, connection_manager, mock_vm_operations, mock_orphaned_file
    ):
        """Test that creating a connection reuses an orphaned slot."""
        # Setup: Record an orphaned slot
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='old-bridge',
                path=mock_orphaned_file
            )

            # Verify orphaned slot exists
            assert has_orphaned_slots('spine1', mock_orphaned_file)

        # Create new connection targeting the same port
        conn = Connection(
            source_device='node2',
            source_port='Ethernet1',
            target_device='spine1',
            target_port='Ethernet5',
            bridge_name='sp15-node21'
        )

        # Mock get_orphaned_slot_by_port to return our orphaned slot
        with patch('connection_manager.ENABLE_SLOT_PRESERVATION', True):
            with patch('connection_manager.extract_port_number', return_value=5):
                with patch('connection_manager.get_orphaned_slot_by_port') as mock_get:
                    mock_get.return_value = {
                        'slot_number': 5,
                        'mac_address': '52:54:00:aa:bb:05',
                        'old_bridge': 'old-bridge'
                    }
                    with patch('connection_manager.claim_orphaned_slot') as mock_claim:
                        result = connection_manager.create_connection(conn)

        # Verify connection was created
        assert result['status'] == 'created'
        assert result['reused_orphaned_slot'] is True

        # Verify update_interface_bridge was called instead of attach
        mock_vm_operations['update'].assert_called_once()
        mock_vm_operations['attach'].assert_not_called()

    def test_find_next_port_prefers_orphaned(self, mock_orphaned_file):
        """Test that find_next_available_port returns orphaned slots first."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            # Record an orphaned slot at port 5
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='old-bridge',
                path=mock_orphaned_file
            )

        # Mock get_used_ports to return [1,2,3,4,6,7]
        # (gap at 5, which is orphaned)
        with patch('interface_manager.get_used_ports_from_topology', return_value={1, 2, 3, 4, 6, 7}):
            with patch('interface_manager.ENABLE_SLOT_PRESERVATION', True):
                with patch('orphaned_interfaces.get_next_orphaned_slot') as mock_orphan:
                    mock_orphan.return_value = {
                        'slot_number': 5,
                        'mac_address': '52:54:00:aa:bb:05'
                    }

                    # Should return orphaned slot (Ethernet5), not max+1 (Ethernet8)
                    result = find_next_available_port('spine1', use_lock=False)
                    assert result == 'Ethernet5'

    def test_find_next_port_falls_back_without_orphans(self, mock_orphaned_file):
        """Test that find_next_available_port falls back when no orphans exist."""
        with patch('interface_manager.get_used_ports_from_topology', return_value={1, 2, 3, 4}):
            with patch('interface_manager.ENABLE_SLOT_PRESERVATION', True):
                with patch('orphaned_interfaces.get_next_orphaned_slot') as mock_orphan:
                    mock_orphan.return_value = None  # No orphaned slots

                    # Should return max+1
                    result = find_next_available_port('spine1', use_lock=False)
                    assert result == 'Ethernet5'


class TestFullCycleScenarios:
    """Test complete add-delete-add cycles."""

    def test_add_delete_add_reuses_slot(self, mock_orphaned_file):
        """Test the complete cycle: add device, delete it, add another - reuses slot."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            # Step 1: Simulate adding node1 to spine1:Ethernet5
            # (This would be done via create_connection in real use)
            # For this test, we just verify the orphan flow

            # Step 2: Simulate deleting node1, creating orphaned slot
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='sp15-node11',
                path=mock_orphaned_file
            )

            # Verify orphaned slot exists
            assert has_orphaned_slots('spine1', mock_orphaned_file)
            orphaned = get_next_orphaned_slot('spine1', mock_orphaned_file)
            assert orphaned['slot_number'] == 5

            # Step 3: When adding node2, find_next_available_port should return Ethernet5
            with patch('interface_manager.get_used_ports_from_topology', return_value={1, 2, 3, 4}):
                with patch('interface_manager.ENABLE_SLOT_PRESERVATION', True):
                    # Mock the orphaned slot lookup
                    with patch('orphaned_interfaces.get_next_orphaned_slot') as mock_get:
                        mock_get.return_value = orphaned
                        port = find_next_available_port('spine1', use_lock=False)
                        assert port == 'Ethernet5'

    def test_multiple_orphans_uses_lowest(self, mock_orphaned_file):
        """Test that with multiple orphaned slots, the lowest is used first."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            # Create orphaned slots at 5, 7, 9
            for slot in [5, 7, 9]:
                record_orphaned_slot(
                    target_device='spine1',
                    slot_number=slot,
                    mac_address=f'52:54:00:aa:bb:{slot:02d}',
                    old_bridge=f'sp1{slot}-nodeX',
                    path=mock_orphaned_file
                )

            # Verify slots are sorted by slot_number
            slots = get_orphaned_slots_for_device('spine1', mock_orphaned_file)
            assert len(slots) == 3
            assert slots[0]['slot_number'] == 5  # Lowest first
            assert slots[1]['slot_number'] == 7
            assert slots[2]['slot_number'] == 9

            # get_next_orphaned_slot should return slot 5
            next_slot = get_next_orphaned_slot('spine1', mock_orphaned_file)
            assert next_slot['slot_number'] == 5


class TestResetAllClearsOrphans:
    """Test that reset-all clears orphaned slots."""

    def test_reset_clears_all_orphaned_slots(self, mock_orphaned_file):
        """Test that clear_all_orphaned_slots removes all orphaned slots."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            # Create orphaned slots on multiple devices
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='sp15-node11',
                path=mock_orphaned_file
            )
            record_orphaned_slot(
                target_device='leaf1',
                slot_number=3,
                mac_address='52:54:00:cc:dd:03',
                old_bridge='le13-host1',
                path=mock_orphaned_file
            )

            # Verify slots exist
            assert has_orphaned_slots('spine1', mock_orphaned_file)
            assert has_orphaned_slots('leaf1', mock_orphaned_file)

            # Clear all
            cleared = clear_all_orphaned_slots(mock_orphaned_file)
            assert cleared == 2

            # Verify all cleared
            assert not has_orphaned_slots('spine1', mock_orphaned_file)
            assert not has_orphaned_slots('leaf1', mock_orphaned_file)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_orphan_slot_idempotent(self, mock_orphaned_file):
        """Test that recording the same slot twice updates rather than duplicates."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            # Record slot 5 twice
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='old-bridge-1',
                path=mock_orphaned_file
            )
            record_orphaned_slot(
                target_device='spine1',
                slot_number=5,
                mac_address='52:54:00:aa:bb:99',  # Different MAC
                old_bridge='old-bridge-2',
                path=mock_orphaned_file
            )

            # Should only have one slot (updated, not duplicated)
            slots = get_orphaned_slots_for_device('spine1', mock_orphaned_file)
            assert len(slots) == 1
            assert slots[0]['mac_address'] == '52:54:00:aa:bb:99'
            assert slots[0]['old_bridge'] == 'old-bridge-2'

    def test_case_insensitive_device_lookup(self, mock_orphaned_file):
        """Test that device name lookups are case-insensitive."""
        with patch('orphaned_interfaces.DEFAULT_ORPHANED_INTERFACES_PATH', mock_orphaned_file):
            record_orphaned_slot(
                target_device='Spine1',  # Mixed case
                slot_number=5,
                mac_address='52:54:00:aa:bb:05',
                old_bridge='bridge',
                path=mock_orphaned_file
            )

            # Should be retrievable with any case
            assert has_orphaned_slots('spine1', mock_orphaned_file)
            assert has_orphaned_slots('SPINE1', mock_orphaned_file)
            assert has_orphaned_slots('Spine1', mock_orphaned_file)

    def test_slot_preservation_disabled(self, mock_orphaned_file):
        """Test behavior when slot preservation is disabled."""
        with patch('interface_manager.ENABLE_SLOT_PRESERVATION', False):
            with patch('interface_manager.get_used_ports_from_topology', return_value={1, 2, 3, 4}):
                # Even if orphaned slots exist, they should be ignored
                port = find_next_available_port('spine1', use_lock=False)
                # Should use max+1 logic, not check orphaned slots
                assert port == 'Ethernet5'
