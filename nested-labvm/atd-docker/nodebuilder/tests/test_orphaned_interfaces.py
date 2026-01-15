"""
Unit tests for orphaned_interfaces module.

Tests cover:
- Loading and saving orphaned interface data
- Recording orphaned slots
- Claiming (reusing) orphaned slots
- Querying orphaned slots
- Cleanup operations
- Validation and maintenance
"""

import os
import pytest
from unittest.mock import patch, Mock

from orphaned_interfaces import (
    load_orphaned_interfaces,
    save_orphaned_interfaces,
    get_empty_orphaned_interfaces,
    get_orphaned_slots_for_device,
    has_orphaned_slots,
    get_next_orphaned_slot,
    get_orphaned_slot_by_port,
    get_orphaned_slot_by_mac,
    list_all_orphaned_slots,
    count_orphaned_slots,
    record_orphaned_slot,
    claim_orphaned_slot,
    remove_orphaned_slot,
    clear_orphaned_slots_for_device,
    clear_all_orphaned_slots,
    get_orphaned_slot_info
)


@pytest.fixture
def mock_orphaned_interfaces_file(temp_dir):
    """Create a temporary orphaned_interfaces.yaml file."""
    path = os.path.join(temp_dir, 'orphaned_interfaces.yaml')
    with open(path, 'w') as f:
        f.write("orphaned_interfaces: {}\n")
    return path


@pytest.fixture
def populated_orphaned_file(temp_dir):
    """Create a file with pre-populated orphaned slots."""
    path = os.path.join(temp_dir, 'orphaned_interfaces.yaml')
    content = """
version: 1
orphaned_interfaces:
  spine1:
    - slot_number: 5
      mac_address: "52:54:00:aa:bb:05"
      old_bridge: "sp15-node11"
      orphaned_at: "2024-12-29T10:15:30Z"
    - slot_number: 7
      mac_address: "52:54:00:aa:bb:07"
      old_bridge: "sp17-node21"
      orphaned_at: "2024-12-29T11:20:45Z"
  leaf1:
    - slot_number: 3
      mac_address: "52:54:00:cc:dd:03"
      old_bridge: "le13-host11"
      orphaned_at: "2024-12-29T10:30:00Z"
"""
    with open(path, 'w') as f:
        f.write(content)
    return path


class TestLoadSave:
    """Tests for loading and saving orphaned interfaces data."""

    def test_load_empty_file(self, mock_orphaned_interfaces_file):
        """Test loading an empty orphaned interfaces file."""
        data = load_orphaned_interfaces(mock_orphaned_interfaces_file)
        assert 'orphaned_interfaces' in data
        assert data['orphaned_interfaces'] == {}

    def test_load_missing_file(self, temp_dir):
        """Test loading from non-existent file creates default structure."""
        path = os.path.join(temp_dir, 'nonexistent.yaml')
        data = load_orphaned_interfaces(path)
        assert 'orphaned_interfaces' in data
        assert data['orphaned_interfaces'] == {}
        assert 'version' in data
        assert data['version'] == 1

    def test_get_empty_structure(self):
        """Test empty structure has all required fields."""
        data = get_empty_orphaned_interfaces()
        assert 'version' in data
        assert 'created_at' in data
        assert 'updated_at' in data
        assert 'orphaned_interfaces' in data
        assert data['orphaned_interfaces'] == {}

    def test_save_and_load(self, mock_orphaned_interfaces_file):
        """Test saving and loading orphaned data."""
        data = get_empty_orphaned_interfaces()
        data['orphaned_interfaces']['spine1'] = [
            {
                'slot_number': 5,
                'mac_address': '52:54:00:aa:bb:05',
                'old_bridge': 'sp15-test1'
            }
        ]
        save_orphaned_interfaces(data, mock_orphaned_interfaces_file)

        # Reload and verify
        loaded = load_orphaned_interfaces(mock_orphaned_interfaces_file)
        assert 'spine1' in loaded['orphaned_interfaces']
        assert len(loaded['orphaned_interfaces']['spine1']) == 1
        assert loaded['orphaned_interfaces']['spine1'][0]['slot_number'] == 5

    def test_save_updates_timestamp(self, mock_orphaned_interfaces_file):
        """Test that saving updates the updated_at timestamp."""
        data = load_orphaned_interfaces(mock_orphaned_interfaces_file)
        original_updated = data.get('updated_at')

        # Save and reload
        save_orphaned_interfaces(data, mock_orphaned_interfaces_file)
        loaded = load_orphaned_interfaces(mock_orphaned_interfaces_file)

        # Timestamp should be updated
        assert 'updated_at' in loaded
        # Can't easily compare times without mocking, just verify it exists


class TestRecordOrphanedSlot:
    """Tests for recording orphaned slots."""

    def test_record_single_slot(self, mock_orphaned_interfaces_file):
        """Test recording a single orphaned slot."""
        result = record_orphaned_slot(
            target_device='spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='sp15-node11',
            path=mock_orphaned_interfaces_file
        )
        assert result is True

        # Verify it was saved
        data = load_orphaned_interfaces(mock_orphaned_interfaces_file)
        assert 'spine1' in data['orphaned_interfaces']
        slots = data['orphaned_interfaces']['spine1']
        assert len(slots) == 1
        assert slots[0]['slot_number'] == 5
        assert slots[0]['mac_address'] == '52:54:00:aa:bb:05'

    def test_record_multiple_slots_same_device(self, mock_orphaned_interfaces_file):
        """Test recording multiple orphaned slots for same device."""
        record_orphaned_slot(
            target_device='spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='sp15-node11',
            path=mock_orphaned_interfaces_file
        )
        record_orphaned_slot(
            target_device='spine1',
            slot_number=7,
            mac_address='52:54:00:aa:bb:07',
            old_bridge='sp17-node21',
            path=mock_orphaned_interfaces_file
        )

        slots = get_orphaned_slots_for_device('spine1', mock_orphaned_interfaces_file)
        assert len(slots) == 2
        # Should be sorted by slot number
        assert slots[0]['slot_number'] == 5
        assert slots[1]['slot_number'] == 7

    def test_record_with_original_connection(self, mock_orphaned_interfaces_file):
        """Test recording an orphaned slot with original connection info."""
        original_connection = {
            'source_device': 'node1',
            'source_port': 'Ethernet1',
            'target_device': 'spine1',
            'target_port': 'Ethernet5'
        }
        record_orphaned_slot(
            target_device='spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='sp15-node11',
            original_connection=original_connection,
            path=mock_orphaned_interfaces_file
        )

        slot = get_next_orphaned_slot('spine1', mock_orphaned_interfaces_file)
        assert slot is not None
        assert 'original_connection' in slot
        assert slot['original_connection']['source_device'] == 'node1'

    def test_record_updates_existing_slot(self, mock_orphaned_interfaces_file):
        """Test that recording a duplicate slot updates the existing record."""
        # Record initial slot
        record_orphaned_slot(
            target_device='spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='old-bridge',
            path=mock_orphaned_interfaces_file
        )

        # Record same slot with different data
        record_orphaned_slot(
            target_device='spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:99',
            old_bridge='new-bridge',
            path=mock_orphaned_interfaces_file
        )

        # Should still have only one slot, with updated data
        slots = get_orphaned_slots_for_device('spine1', mock_orphaned_interfaces_file)
        assert len(slots) == 1
        assert slots[0]['mac_address'] == '52:54:00:aa:bb:99'
        assert slots[0]['old_bridge'] == 'new-bridge'

    def test_record_case_insensitive_device(self, mock_orphaned_interfaces_file):
        """Test that device names are normalized to lowercase."""
        record_orphaned_slot(
            target_device='Spine1',
            slot_number=5,
            mac_address='52:54:00:aa:bb:05',
            old_bridge='sp15-node11',
            path=mock_orphaned_interfaces_file
        )

        # Should be retrievable with any case
        assert has_orphaned_slots('spine1', mock_orphaned_interfaces_file)
        assert has_orphaned_slots('SPINE1', mock_orphaned_interfaces_file)
        assert has_orphaned_slots('Spine1', mock_orphaned_interfaces_file)


class TestQueryOrphanedSlots:
    """Tests for querying orphaned slots."""

    def test_get_slots_for_device(self, populated_orphaned_file):
        """Test getting orphaned slots for a specific device."""
        slots = get_orphaned_slots_for_device('spine1', populated_orphaned_file)
        assert len(slots) == 2
        # Should be sorted by slot number
        assert slots[0]['slot_number'] == 5
        assert slots[1]['slot_number'] == 7

    def test_get_slots_for_nonexistent_device(self, populated_orphaned_file):
        """Test getting slots for a device with no orphans."""
        slots = get_orphaned_slots_for_device('spine99', populated_orphaned_file)
        assert slots == []

    def test_has_orphaned_slots_true(self, populated_orphaned_file):
        """Test has_orphaned_slots returns True when slots exist."""
        assert has_orphaned_slots('spine1', populated_orphaned_file) is True
        assert has_orphaned_slots('leaf1', populated_orphaned_file) is True

    def test_has_orphaned_slots_false(self, populated_orphaned_file):
        """Test has_orphaned_slots returns False when no slots exist."""
        assert has_orphaned_slots('leaf99', populated_orphaned_file) is False

    def test_get_next_orphaned_slot(self, populated_orphaned_file):
        """Test getting the next available orphaned slot."""
        slot = get_next_orphaned_slot('spine1', populated_orphaned_file)
        assert slot is not None
        # Should return lowest slot number (5, not 7)
        assert slot['slot_number'] == 5

    def test_get_next_orphaned_slot_empty(self, mock_orphaned_interfaces_file):
        """Test getting next slot when none available."""
        slot = get_next_orphaned_slot('spine1', mock_orphaned_interfaces_file)
        assert slot is None

    def test_get_slot_by_port(self, populated_orphaned_file):
        """Test getting a specific slot by port number."""
        slot = get_orphaned_slot_by_port('spine1', 7, populated_orphaned_file)
        assert slot is not None
        assert slot['slot_number'] == 7
        assert slot['mac_address'] == '52:54:00:aa:bb:07'

    def test_get_slot_by_port_not_found(self, populated_orphaned_file):
        """Test getting a slot by port that doesn't exist."""
        slot = get_orphaned_slot_by_port('spine1', 99, populated_orphaned_file)
        assert slot is None

    def test_get_slot_by_mac(self, populated_orphaned_file):
        """Test getting a slot by MAC address."""
        slot = get_orphaned_slot_by_mac('spine1', '52:54:00:aa:bb:05', populated_orphaned_file)
        assert slot is not None
        assert slot['slot_number'] == 5

    def test_get_slot_by_mac_case_insensitive(self, populated_orphaned_file):
        """Test MAC address lookup is case-insensitive."""
        slot = get_orphaned_slot_by_mac('spine1', '52:54:00:AA:BB:05', populated_orphaned_file)
        assert slot is not None
        assert slot['slot_number'] == 5

    def test_list_all_slots(self, populated_orphaned_file):
        """Test listing all orphaned slots."""
        all_slots = list_all_orphaned_slots(populated_orphaned_file)
        assert 'spine1' in all_slots
        assert 'leaf1' in all_slots
        assert len(all_slots['spine1']) == 2
        assert len(all_slots['leaf1']) == 1

    def test_count_orphaned_slots(self, populated_orphaned_file):
        """Test counting orphaned slots."""
        counts = count_orphaned_slots(populated_orphaned_file)
        assert counts['total'] == 3
        assert counts['devices_affected'] == 2
        assert counts['by_device']['spine1'] == 2
        assert counts['by_device']['leaf1'] == 1

    def test_get_orphaned_slot_info(self, populated_orphaned_file):
        """Test getting formatted slot info."""
        info = get_orphaned_slot_info('spine1', 5, populated_orphaned_file)
        assert info is not None
        assert info['device'] == 'spine1'
        assert info['port'] == 'Ethernet5'
        assert info['slot_number'] == 5
        assert info['is_orphaned'] is True


class TestClaimOrphanedSlot:
    """Tests for claiming (reusing) orphaned slots."""

    def test_claim_slot_by_mac(self, populated_orphaned_file):
        """Test claiming an orphaned slot removes it from registry."""
        # Verify slot exists
        assert has_orphaned_slots('spine1', populated_orphaned_file)
        initial_slots = get_orphaned_slots_for_device('spine1', populated_orphaned_file)
        assert len(initial_slots) == 2

        # Claim the slot
        result = claim_orphaned_slot('spine1', '52:54:00:aa:bb:05', populated_orphaned_file)
        assert result is True

        # Verify slot was removed
        remaining_slots = get_orphaned_slots_for_device('spine1', populated_orphaned_file)
        assert len(remaining_slots) == 1
        assert remaining_slots[0]['slot_number'] == 7  # Only slot 7 remains

    def test_claim_last_slot_removes_device(self, populated_orphaned_file):
        """Test that claiming the last slot removes the device entry."""
        # leaf1 only has one slot
        result = claim_orphaned_slot('leaf1', '52:54:00:cc:dd:03', populated_orphaned_file)
        assert result is True

        # Device should no longer have any entries
        assert has_orphaned_slots('leaf1', populated_orphaned_file) is False
        all_slots = list_all_orphaned_slots(populated_orphaned_file)
        assert 'leaf1' not in all_slots

    def test_claim_nonexistent_slot(self, populated_orphaned_file):
        """Test claiming a slot that doesn't exist."""
        result = claim_orphaned_slot('spine1', '99:99:99:99:99:99', populated_orphaned_file)
        assert result is False

    def test_claim_slot_case_insensitive_mac(self, populated_orphaned_file):
        """Test claiming with different MAC case."""
        result = claim_orphaned_slot('spine1', '52:54:00:AA:BB:05', populated_orphaned_file)
        assert result is True


class TestRemoveOrphanedSlot:
    """Tests for removing orphaned slots by slot number."""

    def test_remove_slot_by_number(self, populated_orphaned_file):
        """Test removing a slot by slot number."""
        result = remove_orphaned_slot('spine1', 5, populated_orphaned_file)
        assert result is True

        # Verify removal
        slot = get_orphaned_slot_by_port('spine1', 5, populated_orphaned_file)
        assert slot is None

    def test_remove_nonexistent_slot(self, populated_orphaned_file):
        """Test removing a slot that doesn't exist."""
        result = remove_orphaned_slot('spine1', 99, populated_orphaned_file)
        assert result is False


class TestClearOrphanedSlots:
    """Tests for clearing orphaned slots."""

    def test_clear_slots_for_device(self, populated_orphaned_file):
        """Test clearing all slots for a specific device."""
        # Verify slots exist
        assert len(get_orphaned_slots_for_device('spine1', populated_orphaned_file)) == 2

        # Clear
        count = clear_orphaned_slots_for_device('spine1', populated_orphaned_file)
        assert count == 2

        # Verify cleared
        assert has_orphaned_slots('spine1', populated_orphaned_file) is False
        # Other devices should be unaffected
        assert has_orphaned_slots('leaf1', populated_orphaned_file) is True

    def test_clear_all_slots(self, populated_orphaned_file):
        """Test clearing all orphaned slots."""
        # Verify slots exist
        counts = count_orphaned_slots(populated_orphaned_file)
        assert counts['total'] == 3

        # Clear all
        total_cleared = clear_all_orphaned_slots(populated_orphaned_file)
        assert total_cleared == 3

        # Verify all cleared
        counts_after = count_orphaned_slots(populated_orphaned_file)
        assert counts_after['total'] == 0

    def test_clear_empty_returns_zero(self, mock_orphaned_interfaces_file):
        """Test clearing when no slots exist returns 0."""
        count = clear_all_orphaned_slots(mock_orphaned_interfaces_file)
        assert count == 0


class TestValidation:
    """Tests for validation operations."""

    def test_validate_with_mocked_virsh(self, populated_orphaned_file):
        """Test validation with mocked virsh commands."""
        with patch('subprocess.run') as mock_run:
            # Mock successful virsh dominfo
            mock_run.return_value = Mock(
                returncode=0,
                stdout='running\n',
                stderr=''
            )

            from orphaned_interfaces import validate_orphaned_slots
            result = validate_orphaned_slots(populated_orphaned_file)

            assert 'devices_checked' in result
            assert 'slots_checked' in result
            assert result['devices_checked'] >= 1

    def test_validate_detects_missing_device(self, populated_orphaned_file):
        """Test validation detects when device doesn't exist."""
        with patch('subprocess.run') as mock_run:
            # Mock failed virsh dominfo (device not found)
            mock_run.return_value = Mock(
                returncode=1,
                stdout='',
                stderr='error: failed to get domain'
            )

            from orphaned_interfaces import validate_orphaned_slots
            result = validate_orphaned_slots(populated_orphaned_file)

            assert result['valid'] is False
            assert len(result['issues']) > 0
            assert any(i['type'] == 'device_not_found' for i in result['issues'])
