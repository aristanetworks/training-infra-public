"""
Unit tests for velo_manager module.

Tests cover:
- VeloCloud device creation (edge, gateway, orchestrator)
- Cloud-init ISO generation
- XML generation for VMs
- Device count and limit checking
- Status reporting
- Deletion and cleanup
- Rollback on failure
"""

import os
import pytest
import tempfile
from unittest.mock import patch, Mock, MagicMock


class TestYamlSafeString:
    """Tests for yaml_safe_string helper function."""

    def test_simple_string(self):
        """Test simple string without special characters."""
        from velo_manager import yaml_safe_string
        result = yaml_safe_string('password123')
        assert result == "'password123'"

    def test_string_with_single_quotes(self):
        """Test string with embedded single quotes."""
        from velo_manager import yaml_safe_string
        result = yaml_safe_string("test'quote")
        assert result == "'test''quote'"

    def test_string_with_special_chars(self):
        """Test string with special characters."""
        from velo_manager import yaml_safe_string
        result = yaml_safe_string('p@ss$word!#')
        assert result == "'p@ss$word!#'"


class TestVeloDeviceCount:
    """Tests for get_velo_device_count function."""

    def test_count_all_devices(self, temp_dir):
        """Test counting all VeloCloud devices."""
        from velo_manager import get_velo_device_count

        velo_path = os.path.join(temp_dir, 'user_velo.yaml')

        with patch('velo_manager.USER_VELO_PATH', velo_path):
            with patch('persistence.get_velo_device_count') as mock_count:
                mock_count.return_value = 2
                count = get_velo_device_count()
                assert count == 2

    def test_count_by_type(self, temp_dir):
        """Test counting devices by type."""
        from velo_manager import get_velo_device_count

        velo_path = os.path.join(temp_dir, 'user_velo.yaml')

        with patch('velo_manager.USER_VELO_PATH', velo_path):
            with patch('persistence.get_velo_device_count_by_type') as mock_count:
                mock_count.return_value = 1
                count = get_velo_device_count('edge')
                mock_count.assert_called_once_with('edge', velo_path)


class TestGenerateVeloCloudInit:
    """Tests for generate_velo_cloud_init function."""

    def test_edge_cloud_init_generation(self, temp_dir):
        """Test cloud-init generation for Edge device."""
        from velo_manager import generate_velo_cloud_init

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('velo_manager.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'password': 'testpass'}

                    # Create velo subdirectory
                    os.makedirs(os.path.join(temp_dir, 'velo'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        iso_path = generate_velo_cloud_init(
                            device_type='edge',
                            hostname='test-edge1',
                            mgmt_ip='192.168.0.50'
                        )

                        assert 'test-edge1-cidata.iso' in iso_path
                        mock_run.assert_called_once()

    def test_gateway_with_config(self, temp_dir):
        """Test cloud-init generation for Gateway with config."""
        from velo_manager import generate_velo_cloud_init

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('velo_manager.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'password': 'testpass'}

                    os.makedirs(os.path.join(temp_dir, 'velo'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        gateway_config = {
                            'vco': 'vco.test.local',
                            'activation_code': 'AAAA-BBBB-CCCC-DDDD',
                            'eth0_ip': '10.0.0.1/24',
                            'eth0_gateway': '10.0.0.254'
                        }

                        iso_path = generate_velo_cloud_init(
                            device_type='gateway',
                            hostname='test-gw1',
                            mgmt_ip='192.168.0.51',
                            gateway_config=gateway_config
                        )

                        assert 'test-gw1-cidata.iso' in iso_path

    def test_invalid_device_type(self):
        """Test cloud-init generation with invalid device type."""
        from velo_manager import generate_velo_cloud_init

        with patch('velo_manager.get_device_credentials') as mock_creds:
            mock_creds.return_value = {'password': 'testpass'}

            with pytest.raises(ValueError, match="Invalid device type"):
                generate_velo_cloud_init(
                    device_type='invalid',
                    hostname='test-device',
                    mgmt_ip='192.168.0.50'
                )


class TestGenerateGatewayNetworkConfig:
    """Tests for _generate_gateway_network_config function."""

    def test_basic_config(self):
        """Test basic gateway network config generation."""
        from velo_manager import _generate_gateway_network_config

        config = _generate_gateway_network_config(
            eth0_ip='10.0.0.1/24',
            eth0_gateway='10.0.0.254'
        )

        assert 'version: 2' in config
        assert 'eth0:' in config
        assert '10.0.0.1/24' in config
        assert 'gateway4: 10.0.0.254' in config

    def test_with_eth1_interface(self):
        """Test config with eth1 handoff interface."""
        from velo_manager import _generate_gateway_network_config

        config = _generate_gateway_network_config(
            eth0_ip='10.0.0.1/24',
            eth0_gateway='10.0.0.254',
            eth1_ip='172.16.0.1/24',
            eth1_gateway='172.16.0.254'
        )

        assert 'eth1:' in config
        assert '172.16.0.1/24' in config
        assert 'metric: 13' in config  # eth1 should have higher metric


class TestGenerateEdgeNetworkInterfaces:
    """Tests for _generate_edge_network_interfaces function."""

    def test_static_interface(self):
        """Test Edge network interface with static IP."""
        from velo_manager import _generate_edge_network_interfaces

        interfaces = {
            'GE3': {
                'type': 'static',
                'ip': '10.1.1.1',
                'netmask': '255.255.255.0',
                'gateway': '10.1.1.254'
            }
        }

        result = _generate_edge_network_interfaces(interfaces)

        assert 'network-interfaces:' in result
        assert 'GE3:' in result
        assert 'type: static' in result
        assert 'ipaddr: 10.1.1.1' in result

    def test_dhcp_interface(self):
        """Test Edge network interface with DHCP."""
        from velo_manager import _generate_edge_network_interfaces

        interfaces = {
            'GE4': {'type': 'dhcp'}
        }

        result = _generate_edge_network_interfaces(interfaces)

        assert 'GE4:' in result
        assert 'type: dhcp' in result

    def test_empty_interfaces(self):
        """Test with no interfaces."""
        from velo_manager import _generate_edge_network_interfaces

        result = _generate_edge_network_interfaces({})
        assert result == ""


class TestGenerateVeloXml:
    """Tests for generate_velo_xml function."""

    def test_edge_xml_generation(self):
        """Test XML generation for Edge device."""
        from velo_manager import generate_velo_xml

        xml = generate_velo_xml(
            name='test-edge',
            device_type='edge',
            connections=[{
                'bridge': 'test-bridge',
                'local_port': 'wan1'
            }]
        )

        assert '<name>test-edge</name>' in xml
        assert 'vmgmt' in xml  # Management bridge
        assert 'test-bridge' in xml  # Data connection

    def test_orchestrator_xml_with_multiple_disks(self):
        """Test XML generation for Orchestrator with multiple disks."""
        from velo_manager import generate_velo_xml

        disk_paths = [
            {'path': '/images/vco-rootfs.qcow2', 'target': 'vda'},
            {'path': '/images/vco-store.qcow2', 'target': 'vdb'},
            {'path': '/images/vco-store2.qcow2', 'target': 'vdc'},
            {'path': '/images/vco-store3.qcow2', 'target': 'vdd'}
        ]

        xml = generate_velo_xml(
            name='test-vco',
            device_type='orchestrator',
            disk_paths=disk_paths
        )

        assert '<name>test-vco</name>' in xml
        assert 'vda' in xml
        assert 'vdb' in xml
        assert 'vdc' in xml
        assert 'vdd' in xml

    def test_invalid_device_type(self):
        """Test XML generation with invalid device type."""
        from velo_manager import generate_velo_xml

        with pytest.raises(ValueError, match="Invalid device type"):
            generate_velo_xml(name='test', device_type='invalid')


class TestCopyVeloBaseImage:
    """Tests for copy_velo_base_image function."""

    def test_copy_edge_image(self, temp_dir):
        """Test copying Edge base image."""
        from velo_manager import copy_velo_base_image

        # Create source image
        source_path = os.path.join(temp_dir, 'velocloud-edge.qcow2')
        with open(source_path, 'w') as f:
            f.write('fake image data')

        dest_dir = os.path.join(temp_dir, 'velo')
        os.makedirs(dest_dir, exist_ok=True)

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.get_velo_base_image_path') as mock_get_path:
                mock_get_path.return_value = source_path

                paths = copy_velo_base_image('test-edge', 'edge')

                assert len(paths) == 1
                assert paths[0]['target'] == 'vda'
                assert os.path.exists(paths[0]['path'])

    def test_copy_orchestrator_images(self, temp_dir):
        """Test copying Orchestrator base images (4 disks)."""
        from velo_manager import copy_velo_base_image

        # Create source directory with 4 disk images
        os.makedirs(os.path.join(temp_dir, 'velo'), exist_ok=True)

        disk_info = [
            {'name': 'rootfs', 'target': 'vda', 'local_path': f'{temp_dir}/vco-rootfs.qcow2'},
            {'name': 'store', 'target': 'vdb', 'local_path': f'{temp_dir}/vco-store.qcow2'},
            {'name': 'store2', 'target': 'vdc', 'local_path': f'{temp_dir}/vco-store2.qcow2'},
            {'name': 'store3', 'target': 'vdd', 'local_path': f'{temp_dir}/vco-store3.qcow2'}
        ]

        for disk in disk_info:
            with open(disk['local_path'], 'w') as f:
                f.write(f"fake {disk['name']} data")

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.get_velo_orchestrator_disk_paths') as mock_get_paths:
                mock_get_paths.return_value = disk_info

                paths = copy_velo_base_image('test-vco', 'orchestrator')

                assert len(paths) == 4
                assert all(os.path.exists(p['path']) for p in paths)


class TestCreateVeloDevice:
    """Tests for create_velo_device function."""

    def test_create_edge_success(self, temp_dir):
        """Test successful Edge device creation."""
        from velo_manager import create_velo_device

        os.makedirs(os.path.join(temp_dir, 'velo'), exist_ok=True)
        source_image = os.path.join(temp_dir, 'velocloud-edge.qcow2')
        with open(source_image, 'w') as f:
            f.write('fake image')

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.is_velo_enabled', return_value=True):
                with patch('velo_manager.get_velo_device_count', return_value=0):
                    with patch('velo_manager.copy_velo_base_image') as mock_copy:
                        mock_copy.return_value = [{'path': f'{temp_dir}/velo/test-edge.qcow2', 'target': 'vda', 'name': 'primary'}]

                        with patch('velo_manager.generate_velo_cloud_init') as mock_cloudinit:
                            mock_cloudinit.return_value = f'{temp_dir}/velo/test-edge-cidata.iso'

                            with patch('subprocess.run') as mock_run:
                                mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                                with patch('velo_manager.generate_bridge_name', return_value='test-bridge'):
                                    with patch('velo_manager.create_ovs_bridge'):
                                        with patch('velo_manager.find_next_available_port', return_value='Ethernet3'):
                                            with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                                mock_attach.return_value = Mock(
                                                    reused_slot=False,
                                                    target_device='spine1'
                                                )

                                                result = create_velo_device(
                                                    name='test-edge',
                                                    device_type='edge',
                                                    mgmt_ip='192.168.0.50',
                                                    connections=[{'target_device': 'spine1'}]
                                                )

                                                assert result['status'] == 'created'
                                                assert result['name'] == 'test-edge'
                                                assert result['device_type'] == 'edge'

    def test_create_exceeds_limit(self):
        """Test creation fails when limit exceeded."""
        from velo_manager import create_velo_device

        with patch('velo_manager.is_velo_enabled', return_value=True):
            with patch('velo_manager.get_velo_device_count', return_value=5):
                with patch('velo_manager.VELO_DEVICE_CONFIGS', {
                    'edge': {'max_per_topology': 3, 'cpu': 2, 'ram': 4096,
                             'interfaces': [], 'template': ''}
                }):
                    with pytest.raises(RuntimeError, match="Maximum of"):
                        create_velo_device(
                            name='test-edge',
                            device_type='edge',
                            mgmt_ip='192.168.0.50'
                        )

    def test_create_velo_disabled(self):
        """Test creation fails when VeloCloud is disabled."""
        from velo_manager import create_velo_device

        with patch('velo_manager.is_velo_enabled', return_value=False):
            with pytest.raises(RuntimeError, match="not enabled"):
                create_velo_device(
                    name='test-edge',
                    device_type='edge',
                    mgmt_ip='192.168.0.50'
                )

    def test_create_rollback_on_failure(self, temp_dir):
        """Test that rollback cleans up resources on failure."""
        from velo_manager import create_velo_device

        os.makedirs(os.path.join(temp_dir, 'velo'), exist_ok=True)

        # Create a fake image that will be copied
        disk_path = os.path.join(temp_dir, 'velo', 'test-edge.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        cidata_path = os.path.join(temp_dir, 'velo', 'test-edge-cidata.iso')
        with open(cidata_path, 'w') as f:
            f.write('fake iso')

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('velo_manager.is_velo_enabled', return_value=True):
                with patch('velo_manager.get_velo_device_count', return_value=0):
                    with patch('velo_manager.copy_velo_base_image') as mock_copy:
                        mock_copy.return_value = [{'path': disk_path, 'target': 'vda', 'name': 'primary'}]

                        with patch('velo_manager.generate_velo_cloud_init') as mock_cloudinit:
                            mock_cloudinit.return_value = cidata_path

                            with patch('subprocess.run') as mock_run:
                                # virsh define fails
                                mock_run.return_value = Mock(
                                    returncode=1,
                                    stdout='',
                                    stderr='definition failed'
                                )

                                with pytest.raises(RuntimeError, match="Failed to define"):
                                    create_velo_device(
                                        name='test-edge',
                                        device_type='edge',
                                        mgmt_ip='192.168.0.50'
                                    )


class TestDeleteVeloDevice:
    """Tests for delete_velo_device function."""

    def test_delete_edge_success(self, temp_dir):
        """Test successful Edge device deletion."""
        from velo_manager import delete_velo_device

        with patch('persistence.get_user_velo_device') as mock_get:
            mock_get.return_value = {
                'device_type': 'edge',
                'connections': [{'bridge': 'test-bridge', 'target_device': 'spine1'}]
            }

            with patch('resource_manager.get_resource_manager') as mock_rm:
                mock_mgr = Mock()
                mock_mgr.cleanup_connection.return_value = {
                    'interface_detached': True,
                    'bridge_deleted': True,
                    'target_device': 'spine1',
                    'errors': []
                }
                mock_mgr.delete_vm_with_cleanup.return_value = {
                    'vm_destroyed': True,
                    'vm_undefined': True,
                    'disk_deleted': True,
                    'cidata_deleted': True
                }
                mock_rm.return_value = mock_mgr

                result = delete_velo_device('test-edge')

                assert result['status'] == 'deleted'
                assert result['name'] == 'test-edge'
                assert result['details']['vm_destroyed'] is True

    def test_delete_orchestrator_cleans_multiple_disks(self, temp_dir):
        """Test Orchestrator deletion cleans up all disk images."""
        from velo_manager import delete_velo_device

        # Create fake orchestrator disk files
        disk_dir = os.path.join(temp_dir, 'velo')
        os.makedirs(disk_dir, exist_ok=True)

        disk_files = [
            'test-vco-rootfs.qcow2',
            'test-vco-store.qcow2',
            'test-vco-store2.qcow2',
            'test-vco-store3.qcow2'
        ]
        for disk in disk_files:
            with open(os.path.join(disk_dir, disk), 'w') as f:
                f.write('fake disk')

        with patch('velo_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('persistence.get_user_velo_device') as mock_get:
                mock_get.return_value = {
                    'device_type': 'orchestrator',
                    'connections': []
                }

                with patch('resource_manager.get_resource_manager') as mock_rm:
                    mock_mgr = Mock()
                    mock_mgr.cleanup_connection.return_value = {
                        'interface_detached': False,
                        'bridge_deleted': False,
                        'target_device': None,
                        'errors': []
                    }
                    mock_mgr.delete_vm_with_cleanup.return_value = {
                        'vm_destroyed': True,
                        'vm_undefined': True,
                        'disk_deleted': True,
                        'cidata_deleted': True
                    }
                    mock_rm.return_value = mock_mgr

                    result = delete_velo_device('test-vco')

                    assert result['status'] == 'deleted'
                    # Check that extra disks were cleaned up
                    for disk in disk_files:
                        assert not os.path.exists(os.path.join(disk_dir, disk))


class TestGetVeloStatus:
    """Tests for get_velo_status function."""

    def test_status_when_enabled(self):
        """Test status when VeloCloud is enabled."""
        from velo_manager import get_velo_status

        with patch('velo_manager.get_velo_config') as mock_config:
            # Use nested structure matching actual get_velo_config() return value
            mock_config.return_value = {
                'enabled': True,
                'edge': {'enabled': True, 'max_count': 3, 'cpu': 2, 'ram_mb': 8192},
                'gateway': {'enabled': True, 'max_count': 2, 'cpu': 4, 'ram_mb': 16384},
                'orchestrator': {'enabled': True, 'max_count': 1, 'cpu': 4, 'ram_mb': 8192}
            }

            with patch('velo_manager.get_velo_device_count') as mock_count:
                mock_count.side_effect = lambda t=None: {
                    None: 3,
                    'edge': 1,
                    'gateway': 1,
                    'orchestrator': 1
                }.get(t, 0)

                status = get_velo_status()

                assert status['enabled'] is True
                assert status['devices']['edge']['enabled'] is True
                assert status['devices']['edge']['count'] == 1
                assert status['devices']['edge']['available'] == 2
                assert status['total_count'] == 3

    def test_status_when_disabled(self):
        """Test status when VeloCloud is disabled."""
        from velo_manager import get_velo_status

        with patch('velo_manager.get_velo_config') as mock_config:
            mock_config.return_value = {'enabled': False}

            with patch('velo_manager.get_velo_device_count', return_value=0):
                status = get_velo_status()

                assert status['enabled'] is False


class TestListVeloDevices:
    """Tests for list_velo_devices function."""

    def test_list_with_running_vms(self):
        """Test listing devices with VM state enrichment."""
        from velo_manager import list_velo_devices

        with patch('persistence.list_user_velo_devices') as mock_list:
            mock_list.return_value = [
                {'name': 'edge1', 'device_type': 'edge'},
                {'name': 'gw1', 'device_type': 'gateway'}
            ]

            with patch('subprocess.run') as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout='running\n', stderr='')

                devices = list_velo_devices()

                assert len(devices) == 2
                assert devices[0]['vm_state'] == 'running'
                assert devices[1]['vm_state'] == 'running'

    def test_list_with_unknown_state(self):
        """Test listing devices when VM state is unknown."""
        from velo_manager import list_velo_devices

        with patch('persistence.list_user_velo_devices') as mock_list:
            mock_list.return_value = [{'name': 'edge1', 'device_type': 'edge'}]

            with patch('subprocess.run') as mock_run:
                mock_run.return_value = Mock(returncode=1, stdout='', stderr='error')

                devices = list_velo_devices()

                assert len(devices) == 1
                assert devices[0]['vm_state'] == 'unknown'
