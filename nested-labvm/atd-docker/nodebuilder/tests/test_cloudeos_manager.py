"""
Unit tests for cloudeos_manager module.

Tests cover:
- CloudEOS device counting (active vs creating)
- XML generation for VMs with variable connections
- Base image copy
- Device creation with mocked dependencies
- Device deletion and cleanup
- Status reporting
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock


class TestGetCloudEOSCount:
    """Tests for get_cloudeos_count function."""

    def test_count_active_devices(self, temp_dir):
        """Test counting devices with active (no status) state."""
        from cloudeos_manager import get_cloudeos_count

        cloudeos_path = os.path.join(temp_dir, 'user_cloudeos.yaml')

        with patch('cloudeos_manager.USER_CLOUDEOS_PATH', cloudeos_path):
            with patch('cloudeos_manager.persistence_get_cloudeos_count') as mock_count:
                mock_count.return_value = 2
                count = get_cloudeos_count()
                assert count == 2

    def test_excludes_creating_status(self, temp_dir):
        """Test that 'creating' status devices are excluded from count."""
        from cloudeos_manager import get_cloudeos_count

        cloudeos_path = os.path.join(temp_dir, 'user_cloudeos.yaml')

        with patch('cloudeos_manager.USER_CLOUDEOS_PATH', cloudeos_path):
            with patch('cloudeos_manager.persistence_get_cloudeos_count') as mock_count:
                mock_count.return_value = 1  # Only 1 active, 1 creating excluded
                count = get_cloudeos_count()
                assert count == 1

    def test_empty_returns_zero(self, temp_dir):
        """Test count when no devices exist."""
        from cloudeos_manager import get_cloudeos_count

        cloudeos_path = os.path.join(temp_dir, 'user_cloudeos.yaml')

        with patch('cloudeos_manager.USER_CLOUDEOS_PATH', cloudeos_path):
            with patch('cloudeos_manager.persistence_get_cloudeos_count') as mock_count:
                mock_count.return_value = 0
                count = get_cloudeos_count()
                assert count == 0

    def test_uses_persistence_count(self, temp_dir, mock_user_cloudeos_file):
        """Test that count is read from persistence layer."""
        from cloudeos_manager import get_cloudeos_count
        from persistence import save_user_cloudeos_pending, update_user_cloudeos_status

        cloudeos_path = mock_user_cloudeos_file

        # Add two active devices
        save_user_cloudeos_pending('D1', {'ip': '192.168.0.50', 'device_type': 'pe', 'connections': []}, cloudeos_path)
        update_user_cloudeos_status('D1', status='active', path=cloudeos_path)

        save_user_cloudeos_pending('D2', {'ip': '192.168.0.51', 'device_type': 'pe', 'connections': []}, cloudeos_path)
        update_user_cloudeos_status('D2', status='active', path=cloudeos_path)

        # Add one creating device (should be excluded)
        save_user_cloudeos_pending('D3', {'ip': '192.168.0.52', 'device_type': 'pe', 'connections': []}, cloudeos_path)

        with patch('cloudeos_manager.USER_CLOUDEOS_PATH', cloudeos_path):
            count = get_cloudeos_count()
            assert count == 2


class TestGenerateCloudEOSXml:
    """Tests for generate_cloudeos_xml function."""

    def test_basic_xml_generation(self):
        """Test basic XML generation with no connections."""
        from cloudeos_manager import generate_cloudeos_xml

        xml = generate_cloudeos_xml(name='test-ceos')

        assert '<name>test-ceos</name>' in xml
        assert 'vmgmt' in xml  # Management bridge
        assert 'test-ceos.qcow2' in xml

    def test_xml_with_single_connection(self):
        """Test XML generation with one data connection."""
        from cloudeos_manager import generate_cloudeos_xml

        connections = [
            {'bridge': 'ceos1_eth1-leaf1_Et5', 'local_port': 'eth1'}
        ]

        xml = generate_cloudeos_xml(name='test-ceos', connections=connections)

        assert '<name>test-ceos</name>' in xml
        assert 'ceos1_eth1-leaf1_Et5' in xml
        assert 'openvswitch' in xml

    def test_xml_with_multiple_connections(self):
        """Test XML generation with multiple connections."""
        from cloudeos_manager import generate_cloudeos_xml

        connections = [
            {'bridge': 'ceos1_eth1-leaf1_Et5', 'local_port': 'eth1'},
            {'bridge': 'ceos1_eth2-spine1_Et3', 'local_port': 'eth2'},
        ]

        xml = generate_cloudeos_xml(name='test-ceos', connections=connections)

        assert 'ceos1_eth1-leaf1_Et5' in xml
        assert 'ceos1_eth2-spine1_Et3' in xml
        # First data interface at slot 0x04, second at 0x05
        assert 'slot="0x04"' in xml
        assert 'slot="0x05"' in xml

    def test_xml_mgmt_interface_slot(self):
        """Test that management interface is at PCI slot 0x03."""
        from cloudeos_manager import generate_cloudeos_xml

        xml = generate_cloudeos_xml(name='test-ceos')

        # Management interface should be at slot 0x03
        assert 'slot="0x03"' in xml

    def test_xml_no_connections(self):
        """Test XML generation passes with None connections."""
        from cloudeos_manager import generate_cloudeos_xml

        xml = generate_cloudeos_xml(name='test-ceos', connections=None)

        assert '<name>test-ceos</name>' in xml
        assert 'vmgmt' in xml

    def test_xml_skips_connections_without_bridge(self):
        """Test that connections without bridge field are skipped."""
        from cloudeos_manager import generate_cloudeos_xml

        connections = [
            {'local_port': 'eth1'},  # No bridge key - skipped
            {'bridge': 'ceos1_eth2-leaf1_Et5', 'local_port': 'eth2'},
        ]

        xml = generate_cloudeos_xml(name='test-ceos', connections=connections)

        assert 'ceos1_eth2-leaf1_Et5' in xml
        # Second connection (idx=1) is at slot 0x05 since idx starts at 0
        # (first connection has no bridge so slot 0x04 is skipped)
        assert 'slot="0x05"' in xml

    def test_xml_uses_correct_ram_and_cpu(self):
        """Test that XML uses CLOUDEOS_RAM_MB and CLOUDEOS_CPU from config."""
        from cloudeos_manager import generate_cloudeos_xml
        from config import CLOUDEOS_RAM_MB, CLOUDEOS_CPU

        xml = generate_cloudeos_xml(name='test-ceos')

        assert str(CLOUDEOS_RAM_MB) in xml
        assert str(CLOUDEOS_CPU) in xml


class TestCopyCloudEOSBaseImage:
    """Tests for copy_cloudeos_base_image function."""

    def test_copy_base_image_success(self, temp_dir):
        """Test copying base CloudEOS image successfully."""
        from cloudeos_manager import copy_cloudeos_base_image

        # Create source base image
        source_path = os.path.join(temp_dir, 'veos.qcow2')
        with open(source_path, 'w') as f:
            f.write('fake veos image data')

        dest_dir = os.path.join(temp_dir, 'cloudeos')
        os.makedirs(dest_dir, exist_ok=True)

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.CLOUDEOS_BASE_IMAGE_PATH', source_path):
                dest_path = copy_cloudeos_base_image('test-ceos')

                assert os.path.exists(dest_path)
                assert 'test-ceos.qcow2' in dest_path
                assert 'cloudeos' in dest_path

    def test_copy_image_not_found(self, temp_dir):
        """Test error when base image not found."""
        from cloudeos_manager import copy_cloudeos_base_image

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.CLOUDEOS_BASE_IMAGE_PATH', '/nonexistent/veos.qcow2'):
                with pytest.raises(RuntimeError, match="Base CloudEOS image not found"):
                    copy_cloudeos_base_image('test-ceos')

    def test_creates_destination_directory(self, temp_dir):
        """Test that destination directory is created if missing."""
        from cloudeos_manager import copy_cloudeos_base_image

        source_path = os.path.join(temp_dir, 'veos.qcow2')
        with open(source_path, 'w') as f:
            f.write('fake veos image data')

        # Don't pre-create the cloudeos subdir
        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.CLOUDEOS_BASE_IMAGE_PATH', source_path):
                dest_path = copy_cloudeos_base_image('test-ceos')

                assert os.path.exists(dest_path)
                assert os.path.isdir(os.path.join(temp_dir, 'cloudeos'))


class TestCreateCloudEOS:
    """Tests for create_cloudeos function."""

    def test_create_cloudeos_success(self, temp_dir):
        """Test successful CloudEOS device creation."""
        from cloudeos_manager import create_cloudeos

        os.makedirs(os.path.join(temp_dir, 'cloudeos'), exist_ok=True)

        mock_resource_mgr = Mock()
        mock_resource_mgr.vm_exists.return_value = False

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
                with patch('cloudeos_manager.save_user_cloudeos_pending'):
                    with patch('cloudeos_manager.get_resource_manager', return_value=mock_resource_mgr):
                        with patch('cloudeos_manager.copy_cloudeos_base_image') as mock_copy:
                            mock_copy.return_value = f'{temp_dir}/cloudeos/test-ceos.qcow2'

                            with patch('subprocess.run') as mock_run:
                                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                                with patch('connection_manager.generate_bridge_name') as mock_bridge:
                                    mock_bridge.return_value = 'ceos1_eth1-leaf1_Et5'

                                    with patch('connection_manager.create_ovs_bridge'):
                                        with patch('connection_manager.find_next_available_port', return_value='Ethernet5'):
                                            with patch('cloudeos_manager.attach_interface_with_slot_reuse') as mock_attach:
                                                mock_attach.return_value = Mock(
                                                    reused_slot=False,
                                                    target_device='leaf1'
                                                )

                                                with patch('cloudeos_manager.apply_mutual_exclusivity') as mock_mutex:
                                                    mock_mutex.return_value = ([], ['leaf1'])

                                                    with patch('cloudeos_manager.update_user_cloudeos_status'):
                                                        result = create_cloudeos(
                                                            name='test-ceos',
                                                            ip='192.168.0.50',
                                                            device_type='pe',
                                                            connections=[
                                                                {'target_device': 'leaf1'}
                                                            ]
                                                        )

                                                        assert result['status'] == 'success'
                                                        assert result['name'] == 'test-ceos'
                                                        assert result['ip'] == '192.168.0.50'
                                                        assert 'leaf1' in result['targets_need_reboot']

    def test_create_cloudeos_exceeds_limit(self):
        """Test creation fails when maximum CloudEOS limit is reached."""
        from cloudeos_manager import create_cloudeos

        with patch('cloudeos_manager.get_cloudeos_count', return_value=4):
            with patch('cloudeos_manager.MAX_CLOUDEOS_PER_TOPOLOGY', 4):
                with pytest.raises(RuntimeError, match="Maximum of"):
                    create_cloudeos(
                        name='test-ceos',
                        ip='192.168.0.50',
                        device_type='pe',
                        connections=[{'target_device': 'leaf1'}]
                    )

    def test_create_cloudeos_vm_already_exists(self):
        """Test creation fails when VM already exists in libvirt."""
        from cloudeos_manager import create_cloudeos

        mock_resource_mgr = Mock()
        mock_resource_mgr.vm_exists.return_value = True

        with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
            with patch('cloudeos_manager.get_resource_manager', return_value=mock_resource_mgr):
                with pytest.raises(RuntimeError, match="already exists in libvirt"):
                    create_cloudeos(
                        name='test-ceos',
                        ip='192.168.0.50',
                        device_type='pe',
                        connections=[{'target_device': 'leaf1'}]
                    )

    def test_create_cloudeos_rollback_on_failure(self, temp_dir):
        """Test that rollback cleans up resources on VM define failure."""
        from cloudeos_manager import create_cloudeos

        os.makedirs(os.path.join(temp_dir, 'cloudeos'), exist_ok=True)

        disk_path = os.path.join(temp_dir, 'cloudeos', 'test-ceos.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        mock_resource_mgr = Mock()
        mock_resource_mgr.vm_exists.return_value = False

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
                with patch('cloudeos_manager.save_user_cloudeos_pending'):
                    with patch('cloudeos_manager.get_resource_manager', return_value=mock_resource_mgr):
                        with patch('cloudeos_manager.copy_cloudeos_base_image') as mock_copy:
                            mock_copy.return_value = disk_path

                            with patch('connection_manager.generate_bridge_name') as mock_bridge:
                                mock_bridge.return_value = 'ceos_bridge'

                                with patch('connection_manager.create_ovs_bridge'):
                                    with patch('connection_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('subprocess.run') as mock_run:
                                            # virsh define fails
                                            mock_run.return_value = Mock(
                                                returncode=1,
                                                stdout='',
                                                stderr='definition failed'
                                            )

                                            with patch('resource_manager.delete_ovs_bridge'):
                                                with pytest.raises(RuntimeError, match="Failed to define"):
                                                    create_cloudeos(
                                                        name='test-ceos',
                                                        ip='192.168.0.50',
                                                        device_type='pe',
                                                        connections=[
                                                            {'target_device': 'leaf1'}
                                                        ]
                                                    )

    def test_create_cloudeos_no_connections(self, temp_dir):
        """Test CloudEOS creation with no data connections."""
        from cloudeos_manager import create_cloudeos

        os.makedirs(os.path.join(temp_dir, 'cloudeos'), exist_ok=True)

        mock_resource_mgr = Mock()
        mock_resource_mgr.vm_exists.return_value = False

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
                with patch('cloudeos_manager.save_user_cloudeos_pending'):
                    with patch('cloudeos_manager.get_resource_manager', return_value=mock_resource_mgr):
                        with patch('cloudeos_manager.copy_cloudeos_base_image') as mock_copy:
                            mock_copy.return_value = f'{temp_dir}/cloudeos/test-ceos.qcow2'

                            with patch('subprocess.run') as mock_run:
                                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                                with patch('cloudeos_manager.apply_mutual_exclusivity') as mock_mutex:
                                    mock_mutex.return_value = ([], [])

                                    with patch('cloudeos_manager.update_user_cloudeos_status'):
                                        result = create_cloudeos(
                                            name='test-ceos',
                                            ip='192.168.0.50',
                                            device_type='pe',
                                            connections=[]
                                        )

                                        assert result['status'] == 'success'
                                        assert result['connections'] == []
                                        assert result['targets_need_reboot'] == []
                                        assert result['targets_reused_slots'] == []

    def test_create_cloudeos_multiple_connections(self, temp_dir):
        """Test CloudEOS creation with multiple connections uses slot reuse."""
        from cloudeos_manager import create_cloudeos

        os.makedirs(os.path.join(temp_dir, 'cloudeos'), exist_ok=True)

        mock_resource_mgr = Mock()
        mock_resource_mgr.vm_exists.return_value = False

        with patch('cloudeos_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
                with patch('cloudeos_manager.save_user_cloudeos_pending'):
                    with patch('cloudeos_manager.get_resource_manager', return_value=mock_resource_mgr):
                        with patch('cloudeos_manager.copy_cloudeos_base_image') as mock_copy:
                            mock_copy.return_value = f'{temp_dir}/cloudeos/test-ceos.qcow2'

                            with patch('subprocess.run') as mock_run:
                                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                                with patch('connection_manager.generate_bridge_name') as mock_bridge:
                                    mock_bridge.side_effect = [
                                        'ceos_eth1-leaf1_Et5',
                                        'ceos_eth2-spine1_Et3'
                                    ]

                                    with patch('connection_manager.create_ovs_bridge'):
                                        with patch('connection_manager.find_next_available_port', return_value='Ethernet5'):
                                            with patch('cloudeos_manager.attach_interface_with_slot_reuse') as mock_attach:
                                                mock_attach.side_effect = [
                                                    Mock(reused_slot=True, target_device='leaf1'),
                                                    Mock(reused_slot=False, target_device='spine1')
                                                ]

                                                with patch('cloudeos_manager.apply_mutual_exclusivity') as mock_mutex:
                                                    mock_mutex.return_value = (['leaf1'], ['spine1'])

                                                    with patch('cloudeos_manager.update_user_cloudeos_status'):
                                                        result = create_cloudeos(
                                                            name='test-ceos',
                                                            ip='192.168.0.50',
                                                            device_type='pe',
                                                            connections=[
                                                                {'target_device': 'leaf1'},
                                                                {'target_device': 'spine1'}
                                                            ]
                                                        )

                                                        assert result['status'] == 'success'
                                                        assert 'leaf1' in result['targets_reused_slots']
                                                        assert 'spine1' in result['targets_need_reboot']


class TestDeleteCloudEOS:
    """Tests for delete_cloudeos function."""

    def test_delete_cloudeos_success(self, temp_dir):
        """Test successful CloudEOS device deletion."""
        from cloudeos_manager import delete_cloudeos

        with patch('cloudeos_manager.get_user_cloudeos_device') as mock_get:
            mock_get.return_value = {
                'test-ceos': {
                    'neighbors': [
                        {
                            'port': 'eth1',
                            'neighborDevice': 'leaf1',
                            'neighborPort': 'Ethernet5'
                        }
                    ]
                }
            }

            with patch('cloudeos_manager.get_resource_manager') as mock_rm:
                mock_mgr = Mock()
                mock_mgr.cleanup_connection.return_value = {
                    'interface_detached': True,
                    'bridge_deleted': True,
                    'target_device': 'leaf1',
                    'slot_preserved': True,
                    'errors': []
                }
                mock_mgr.delete_vm_with_cleanup.return_value = {
                    'vm_destroyed': True,
                    'vm_undefined': True,
                    'disk_deleted': True
                }
                mock_rm.return_value = mock_mgr

                with patch('cloudeos_manager.remove_user_cloudeos'):
                    with patch('cloudeos_manager.generate_bridge_name', return_value='test-ceos_eth1-leaf1_Et5'):
                        result = delete_cloudeos('test-ceos')

                    assert result['status'] == 'success'
                    assert result['name'] == 'test-ceos'
                    assert result['details']['vm_destroyed'] is True
                    assert result['details']['vm_undefined'] is True
                    assert result['details']['disk_deleted'] is True

    def test_delete_cloudeos_not_found_in_persistence(self):
        """Test deletion when device not found in persistence."""
        from cloudeos_manager import delete_cloudeos

        with patch('cloudeos_manager.get_user_cloudeos_device') as mock_get:
            mock_get.return_value = None

            with patch('cloudeos_manager.get_resource_manager') as mock_rm:
                mock_mgr = Mock()
                mock_mgr.cleanup_connection.return_value = {
                    'interface_detached': False,
                    'bridge_deleted': False,
                    'target_device': None,
                    'slot_preserved': False,
                    'errors': []
                }
                mock_mgr.delete_vm_with_cleanup.return_value = {
                    'vm_destroyed': False,
                    'vm_undefined': False,
                    'disk_deleted': False
                }
                mock_rm.return_value = mock_mgr

                with patch('cloudeos_manager.remove_user_cloudeos'):
                    result = delete_cloudeos('nonexistent-ceos')

                    assert result['status'] == 'success'
                    assert result['details']['bridges_deleted'] == []
                    assert result['details']['devices_needing_reboot'] == []

    def test_delete_cloudeos_multiple_connections(self):
        """Test deletion with multiple connections cleaned up."""
        from cloudeos_manager import delete_cloudeos

        with patch('cloudeos_manager.get_user_cloudeos_device') as mock_get:
            mock_get.return_value = {
                'test-ceos': {
                    'neighbors': [
                        {
                            'port': 'eth1',
                            'neighborDevice': 'leaf1',
                            'neighborPort': 'Ethernet5'
                        },
                        {
                            'port': 'eth2',
                            'neighborDevice': 'spine1',
                            'neighborPort': 'Ethernet3'
                        }
                    ]
                }
            }

            with patch('cloudeos_manager.get_resource_manager') as mock_rm:
                mock_mgr = Mock()
                mock_mgr.cleanup_connection.side_effect = [
                    {
                        'interface_detached': True,
                        'bridge_deleted': True,
                        'target_device': 'leaf1',
                        'slot_preserved': True,
                        'errors': []
                    },
                    {
                        'interface_detached': True,
                        'bridge_deleted': True,
                        'target_device': 'spine1',
                        'slot_preserved': True,
                        'errors': []
                    }
                ]
                mock_mgr.delete_vm_with_cleanup.return_value = {
                    'vm_destroyed': True,
                    'vm_undefined': True,
                    'disk_deleted': True
                }
                mock_rm.return_value = mock_mgr

                with patch('cloudeos_manager.remove_user_cloudeos'):
                    with patch('cloudeos_manager.generate_bridge_name', side_effect=[
                        'test-ceos_eth1-leaf1_Et5',
                        'test-ceos_eth2-spine1_Et3'
                    ]):
                        result = delete_cloudeos('test-ceos')

                    assert result['status'] == 'success'
                    assert 'leaf1' in result['details']['devices_needing_reboot']
                    assert 'spine1' in result['details']['devices_needing_reboot']


class TestGetCloudEOSStatus:
    """Tests for get_cloudeos_status function."""

    def test_status_empty(self):
        """Test status when no devices exist."""
        from cloudeos_manager import get_cloudeos_status

        with patch('cloudeos_manager.get_cloudeos_count', return_value=0):
            with patch('cloudeos_manager.MAX_CLOUDEOS_PER_TOPOLOGY', 4):
                status = get_cloudeos_status()

                assert status['count'] == 0
                assert status['max_allowed'] == 4
                assert status['available'] == 4
                assert status['can_add_more'] is True

    def test_status_with_devices(self):
        """Test status with some devices created."""
        from cloudeos_manager import get_cloudeos_status

        with patch('cloudeos_manager.get_cloudeos_count', return_value=2):
            with patch('cloudeos_manager.MAX_CLOUDEOS_PER_TOPOLOGY', 4):
                status = get_cloudeos_status()

                assert status['count'] == 2
                assert status['max_allowed'] == 4
                assert status['available'] == 2
                assert status['can_add_more'] is True

    def test_status_at_max(self):
        """Test status when at maximum capacity."""
        from cloudeos_manager import get_cloudeos_status

        with patch('cloudeos_manager.get_cloudeos_count', return_value=4):
            with patch('cloudeos_manager.MAX_CLOUDEOS_PER_TOPOLOGY', 4):
                status = get_cloudeos_status()

                assert status['count'] == 4
                assert status['max_allowed'] == 4
                assert status['available'] == 0
                assert status['can_add_more'] is False

    def test_status_available_never_negative(self):
        """Test that available never goes below zero."""
        from cloudeos_manager import get_cloudeos_status

        with patch('cloudeos_manager.get_cloudeos_count', return_value=5):
            with patch('cloudeos_manager.MAX_CLOUDEOS_PER_TOPOLOGY', 4):
                status = get_cloudeos_status()

                assert status['available'] == 0
                assert status['can_add_more'] is False
