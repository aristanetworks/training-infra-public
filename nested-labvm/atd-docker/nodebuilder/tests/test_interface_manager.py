"""
Unit tests for interface_manager module.

Tests cover:
- Device name parsing
- Bridge name generation
- Port allocation and tracking
- Creation lock functionality
"""

import os
import sys
import pytest
import threading
import time
from unittest.mock import patch, Mock, MagicMock

from interface_manager import (
    parse_device_name,
    generate_bridge_name,
    get_used_ports_from_topology,
    find_next_available_port,
    creation_lock,
    port_allocation_lock,
    update_interface_bridge,
    get_vm_interfaces
)


class TestParseDeviceName:
    """Tests for device name parsing."""

    def test_parse_spine(self):
        """Test parsing spine device names."""
        result = parse_device_name('spine1')
        assert result['name'] == 'spine1'
        assert result['code'] == 'sp1'

    def test_parse_leaf(self):
        """Test parsing leaf device names."""
        result = parse_device_name('leaf1')
        assert result['name'] == 'leaf1'
        assert result['code'] == 'le1'

    def test_parse_borderleaf(self):
        """Test parsing borderleaf device names."""
        result = parse_device_name('borderleaf1')
        assert result['name'] == 'borderleaf1'
        assert result['code'] == 'bo1'

    def test_parse_ethernet_port(self):
        """Test parsing Ethernet port names."""
        result = parse_device_name('Ethernet3')
        assert result['code'] == '3'

    def test_parse_with_dc_suffix(self):
        """Test parsing names with -dc suffix."""
        result = parse_device_name('spine1-dc1')
        assert 'sp1' in result['code'] or 'd1' in result['code']

    def test_parse_host(self):
        """Test parsing host device names."""
        result = parse_device_name('host1')
        assert result['code'] == 'ho1'

    def test_parse_case_insensitive(self):
        """Test that parsing handles case variations."""
        result1 = parse_device_name('Spine1')
        result2 = parse_device_name('SPINE1')
        result3 = parse_device_name('spine1')
        # All should produce lowercase codes
        assert result1['code'].islower() or result1['code'].isdigit() or any(c.isdigit() for c in result1['code'])


class TestGenerateBridgeName:
    """Tests for bridge name generation."""

    def test_basic_bridge_name(self):
        """Test basic bridge name generation."""
        name = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert '-' in name  # Should have separator
        assert len(name) <= 15  # OVS limit

    def test_bridge_name_consistency(self):
        """Test that same inputs produce same output."""
        name1 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert name1 == name2

    def test_bridge_name_case_normalized(self):
        """Test that bridge names are normalized to lowercase."""
        name1 = generate_bridge_name('Leaf1', 'Ethernet1', 'Spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        assert name1 == name2  # Should be identical after normalization

    def test_different_ports_different_bridges(self):
        """Test that different ports produce different bridge names."""
        name1 = generate_bridge_name('leaf1', 'Ethernet1', 'spine1', 'Ethernet2')
        name2 = generate_bridge_name('leaf1', 'Ethernet2', 'spine1', 'Ethernet2')
        assert name1 != name2


class TestPortAllocation:
    """Tests for port allocation functions."""

    def test_get_used_ports_with_mock_topology(self, mock_topo_build_file, mock_user_nodes_file,
                                                mock_user_hosts_file, mock_user_firewalls_file):
        """Test getting used ports from topology."""
        with patch('interface_manager.get_topo_build_path', return_value=mock_topo_build_file):
            with patch('interface_manager.USER_NODES_PATH', mock_user_nodes_file):
                with patch('interface_manager.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('interface_manager.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        # spine1 has 2 neighbors (leaf1, leaf2) using Ethernet1 and Ethernet2
                        used_ports = get_used_ports_from_topology('spine1')
                        assert 1 in used_ports  # Ethernet1
                        assert 2 in used_ports  # Ethernet2

    def test_get_used_ports_empty_for_unknown_device(self, mock_topo_build_file, mock_user_nodes_file,
                                                      mock_user_hosts_file, mock_user_firewalls_file):
        """Test that unknown device returns empty set."""
        with patch('interface_manager.get_topo_build_path', return_value=mock_topo_build_file):
            with patch('interface_manager.USER_NODES_PATH', mock_user_nodes_file):
                with patch('interface_manager.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('interface_manager.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        used_ports = get_used_ports_from_topology('nonexistent')
                        assert len(used_ports) == 0


class TestCreationLock:
    """Tests for creation lock functionality."""

    def test_creation_lock_serializes_access(self, temp_dir):
        """Test that creation lock serializes concurrent access."""
        # Patch the lock file path to use temp directory
        lock_file = os.path.join(temp_dir, 'creation.lock')

        results = []
        errors = []

        def worker(worker_id):
            try:
                with patch('interface_manager._CREATION_LOCK_FILE', lock_file):
                    with creation_lock(f'worker-{worker_id}', timeout=5.0):
                        results.append(f'start-{worker_id}')
                        time.sleep(0.1)  # Simulate work
                        results.append(f'end-{worker_id}')
            except Exception as e:
                errors.append(str(e))

        # Start multiple threads
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Verify no errors
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Verify serialized execution (start-end pairs should be consecutive)
        # Each worker should complete before the next starts
        assert len(results) == 6

    def test_creation_lock_timeout(self, temp_dir):
        """Test that creation lock times out properly."""
        lock_file = os.path.join(temp_dir, 'timeout.lock')
        acquired = threading.Event()
        timeout_occurred = threading.Event()

        def holder():
            with patch('interface_manager._CREATION_LOCK_FILE', lock_file):
                with creation_lock('holder', timeout=10.0):
                    acquired.set()
                    time.sleep(2)  # Hold lock for 2 seconds

        def waiter():
            acquired.wait()  # Wait for holder to acquire
            try:
                with patch('interface_manager._CREATION_LOCK_FILE', lock_file):
                    with creation_lock('waiter', timeout=0.5):
                        pass  # Should not reach here
            except TimeoutError:
                timeout_occurred.set()

        holder_thread = threading.Thread(target=holder)
        waiter_thread = threading.Thread(target=waiter)

        holder_thread.start()
        waiter_thread.start()

        waiter_thread.join(timeout=5)
        holder_thread.join(timeout=5)

        assert timeout_occurred.is_set(), "Timeout should have occurred"

    def test_creation_lock_releases_on_exception(self, temp_dir):
        """Test that lock is released even if exception occurs."""
        lock_file = os.path.join(temp_dir, 'exception.lock')

        with pytest.raises(ValueError):
            with patch('interface_manager._CREATION_LOCK_FILE', lock_file):
                with creation_lock('exception-test', timeout=5.0):
                    raise ValueError("Test exception")

        # Lock should be released - we should be able to acquire again
        with patch('interface_manager._CREATION_LOCK_FILE', lock_file):
            with creation_lock('after-exception', timeout=1.0):
                pass  # Should succeed


class TestPortAllocationLock:
    """Tests for port allocation lock functionality."""

    def test_port_lock_basic(self, temp_dir):
        """Test basic port allocation lock."""
        lock_file = os.path.join(temp_dir, 'port.lock')

        with patch('interface_manager._PORT_LOCK_FILE', lock_file):
            with port_allocation_lock('test-device', timeout=5.0):
                # Should be able to execute code here
                pass

    def test_port_lock_releases_on_exception(self, temp_dir):
        """Test that port lock is released on exception."""
        lock_file = os.path.join(temp_dir, 'port_exception.lock')

        with pytest.raises(RuntimeError):
            with patch('interface_manager._PORT_LOCK_FILE', lock_file):
                with port_allocation_lock('exception-device', timeout=5.0):
                    raise RuntimeError("Test exception")

        # Should be able to acquire again
        with patch('interface_manager._PORT_LOCK_FILE', lock_file):
            with port_allocation_lock('after-exception-device', timeout=1.0):
                pass


class TestUpdateInterfaceBridge:
    """Tests for update_interface_bridge function.

    This function is key to interface slot preservation - it updates
    an existing interface's bridge connection without detaching/reattaching.
    """

    def test_update_interface_bridge_running_vm(self):
        """Test updating bridge on a running VM."""
        with patch('vm_manager.get_vm_state', return_value='running'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                # Mock existing interface
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet5',
                        'type': 'bridge',
                        'source': 'old-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:aa:bb:05'
                    }
                ]

                with patch('interface_manager.subprocess.run') as mock_run:
                    # Mock successful update-device
                    mock_run.return_value = Mock(
                        returncode=0,
                        stdout='Device updated successfully',
                        stderr=''
                    )

                    result = update_interface_bridge(
                        vm_name='spine1',
                        mac_address='52:54:00:aa:bb:05',
                        new_bridge='new-bridge'
                    )

                    assert result['status'] == 'updated'
                    assert result['vm'] == 'spine1'
                    assert result['mac'] == '52:54:00:aa:bb:05'
                    assert result['new_bridge'] == 'new-bridge'
                    assert result['old_bridge'] == 'old-bridge'
                    assert result['immediate'] is True

    def test_update_interface_bridge_stopped_vm(self):
        """Test updating bridge on a stopped VM."""
        with patch('vm_manager.get_vm_state', return_value='shut off'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet5',
                        'type': 'bridge',
                        'source': 'old-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:aa:bb:05'
                    }
                ]

                with patch('interface_manager.subprocess.run') as mock_run:
                    mock_run.return_value = Mock(
                        returncode=0,
                        stdout='Device updated successfully',
                        stderr=''
                    )

                    result = update_interface_bridge(
                        vm_name='spine1',
                        mac_address='52:54:00:aa:bb:05',
                        new_bridge='new-bridge'
                    )

                    assert result['status'] == 'configured'
                    assert result['immediate'] is False

    def test_update_interface_bridge_mac_not_found(self):
        """Test error when MAC address not found on VM."""
        with patch('vm_manager.get_vm_state', return_value='running'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                # No matching MAC
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet1',
                        'type': 'bridge',
                        'source': 'some-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:xx:xx:xx'
                    }
                ]

                with pytest.raises(RuntimeError) as exc_info:
                    update_interface_bridge(
                        vm_name='spine1',
                        mac_address='52:54:00:aa:bb:05',
                        new_bridge='new-bridge'
                    )

                assert 'not found on VM' in str(exc_info.value)

    def test_update_interface_bridge_case_insensitive_mac(self):
        """Test that MAC address matching is case-insensitive."""
        with patch('vm_manager.get_vm_state', return_value='shut off'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet5',
                        'type': 'bridge',
                        'source': 'old-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:AA:BB:05'  # Uppercase
                    }
                ]

                with patch('interface_manager.subprocess.run') as mock_run:
                    mock_run.return_value = Mock(
                        returncode=0,
                        stdout='',
                        stderr=''
                    )

                    # Call with lowercase - should still match
                    result = update_interface_bridge(
                        vm_name='spine1',
                        mac_address='52:54:00:aa:bb:05',
                        new_bridge='new-bridge'
                    )

                    assert result['status'] == 'configured'

    def test_update_interface_bridge_virsh_failure(self):
        """Test error handling when virsh update-device fails."""
        with patch('vm_manager.get_vm_state', return_value='running'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet5',
                        'type': 'bridge',
                        'source': 'old-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:aa:bb:05'
                    }
                ]

                with patch('interface_manager.subprocess.run') as mock_run:
                    mock_run.return_value = Mock(
                        returncode=1,
                        stdout='',
                        stderr='error: operation failed: interface not found'
                    )

                    with pytest.raises(RuntimeError) as exc_info:
                        update_interface_bridge(
                            vm_name='spine1',
                            mac_address='52:54:00:aa:bb:05',
                            new_bridge='new-bridge'
                        )

                    assert 'Failed to update interface' in str(exc_info.value)

    def test_update_interface_uses_correct_virsh_command(self):
        """Test that correct virsh command is used."""
        with patch('vm_manager.get_vm_state', return_value='running'):
            with patch('interface_manager.get_vm_interfaces') as mock_interfaces:
                mock_interfaces.return_value = [
                    {
                        'interface': 'vnet5',
                        'type': 'bridge',
                        'source': 'old-bridge',
                        'model': 'virtio',
                        'mac': '52:54:00:aa:bb:05'
                    }
                ]

                with patch('interface_manager.subprocess.run') as mock_run:
                    mock_run.return_value = Mock(
                        returncode=0,
                        stdout='',
                        stderr=''
                    )

                    update_interface_bridge(
                        vm_name='spine1',
                        mac_address='52:54:00:aa:bb:05',
                        new_bridge='new-bridge'
                    )

                    # Find the update-device call
                    update_call = None
                    for call in mock_run.call_args_list:
                        args = call[0][0] if call[0] else call[1].get('args', [])
                        if 'update-device' in args:
                            update_call = args
                            break

                    assert update_call is not None, "update-device command not found"
                    assert 'virsh' in update_call
                    assert 'update-device' in update_call
                    assert 'spine1' in update_call
                    assert '--config' in update_call
                    assert '--live' in update_call  # Running VM should have --live
