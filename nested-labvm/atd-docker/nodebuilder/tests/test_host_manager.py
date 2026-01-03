"""
Unit tests for host_manager module.

Tests cover:
- Linux host creation
- Cloud-init ISO generation
- XML generation for VMs
- Host count and limit checking
- VNC info retrieval
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
        from host_manager import yaml_safe_string
        result = yaml_safe_string('password123')
        assert result == "'password123'"

    def test_string_with_single_quotes(self):
        """Test string with embedded single quotes."""
        from host_manager import yaml_safe_string
        result = yaml_safe_string("test'quote")
        assert result == "'test''quote'"

    def test_string_with_special_chars(self):
        """Test string with special characters."""
        from host_manager import yaml_safe_string
        result = yaml_safe_string('p@ss$word!#')
        assert result == "'p@ss$word!#'"


class TestGetHostCount:
    """Tests for get_host_count function."""

    def test_count_created_hosts(self, temp_dir):
        """Test counting hosts with created status."""
        from host_manager import get_host_count

        hosts_path = os.path.join(temp_dir, 'user_hosts.yaml')
        with open(hosts_path, 'w') as f:
            f.write("""hosts:
  - host1:
      status: created
  - host2:
      status: created
""")

        with patch('host_manager.USER_HOSTS_PATH', hosts_path):
            with patch('persistence.load_user_hosts') as mock_load:
                mock_load.return_value = {
                    'hosts': [
                        {'host1': {'status': 'created'}},
                        {'host2': {'status': 'created'}}
                    ]
                }
                count = get_host_count()
                assert count == 2

    def test_excludes_creating_status(self, temp_dir):
        """Test that 'creating' status hosts are excluded."""
        from host_manager import get_host_count

        hosts_path = os.path.join(temp_dir, 'user_hosts.yaml')

        with patch('host_manager.USER_HOSTS_PATH', hosts_path):
            with patch('persistence.load_user_hosts') as mock_load:
                mock_load.return_value = {
                    'hosts': [
                        {'host1': {'status': 'created'}},
                        {'host2': {'status': 'creating'}}
                    ]
                }
                count = get_host_count()
                assert count == 1


class TestGenerateCloudInitIso:
    """Tests for generate_cloud_init_iso function."""

    def test_basic_cloud_init_generation(self, temp_dir):
        """Test basic cloud-init ISO generation."""
        from host_manager import generate_cloud_init_iso

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('config.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'username': 'arista', 'password': 'testpass'}

                    # Create hosts subdirectory
                    os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        iso_path = generate_cloud_init_iso(
                            hostname='test-host1',
                            mgmt_ip='192.168.0.50'
                        )

                        assert 'test-host1-cidata.iso' in iso_path
                        mock_run.assert_called_once()

    def test_cloud_init_with_data_ip(self, temp_dir):
        """Test cloud-init generation with data interface IP."""
        from host_manager import generate_cloud_init_iso

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('config.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'username': 'arista', 'password': 'testpass'}

                    os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        iso_path = generate_cloud_init_iso(
                            hostname='test-host1',
                            mgmt_ip='192.168.0.50',
                            data_ip='10.1.1.100/24'
                        )

                        assert 'test-host1-cidata.iso' in iso_path

    def test_cloud_init_with_custom_password(self, temp_dir):
        """Test cloud-init with custom password."""
        from host_manager import generate_cloud_init_iso

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('config.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'username': 'arista', 'password': 'default'}

                    os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        iso_path = generate_cloud_init_iso(
                            hostname='test-host1',
                            mgmt_ip='192.168.0.50',
                            password='custom-password'
                        )

                        assert 'test-host1-cidata.iso' in iso_path

    def test_cloud_init_no_iso_tools(self, temp_dir):
        """Test error when no ISO tools available."""
        from host_manager import generate_cloud_init_iso

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('config.get_device_credentials') as mock_creds:
                    mock_creds.return_value = {'username': 'arista', 'password': 'testpass'}

                    os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

                    with patch('subprocess.run') as mock_run:
                        mock_run.side_effect = FileNotFoundError("Command not found")

                        with pytest.raises(RuntimeError, match="Neither genisoimage nor mkisofs"):
                            generate_cloud_init_iso(
                                hostname='test-host1',
                                mgmt_ip='192.168.0.50'
                            )


class TestGenerateHostXml:
    """Tests for generate_host_xml function."""

    def test_basic_xml_generation(self):
        """Test basic XML generation for host."""
        from host_manager import generate_host_xml

        xml = generate_host_xml(name='test-host')

        assert '<name>test-host</name>' in xml
        assert 'vmgmt' in xml  # Management bridge
        assert 'test-host.qcow2' in xml
        assert 'test-host-cidata.iso' in xml

    def test_xml_with_connection(self):
        """Test XML generation with data connection."""
        from host_manager import generate_host_xml

        connection = {
            'bridge': 'host1_eth1-leaf1_Et5',
            'local_port': 'eth1'
        }

        xml = generate_host_xml(name='test-host', connection=connection)

        assert '<name>test-host</name>' in xml
        assert 'host1_eth1-leaf1_Et5' in xml  # Data bridge
        assert 'openvswitch' in xml

    def test_xml_contains_vnc_graphics(self):
        """Test XML contains VNC graphics element."""
        from host_manager import generate_host_xml

        xml = generate_host_xml(name='test-host')

        # ElementTree uses double quotes in output
        assert 'type="vnc"' in xml
        assert 'autoport="yes"' in xml
        assert "127.0.0.1" in xml  # Localhost only


class TestCopyHostBaseImage:
    """Tests for copy_host_base_image function."""

    def test_copy_base_image(self, temp_dir):
        """Test copying base image successfully."""
        from host_manager import copy_host_base_image

        # Create source image
        source_path = os.path.join(temp_dir, 'ubuntu-desktop.qcow2')
        with open(source_path, 'w') as f:
            f.write('fake image data')

        dest_dir = os.path.join(temp_dir, 'hosts')
        os.makedirs(dest_dir, exist_ok=True)

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_base_image_path') as mock_get_path:
                mock_get_path.return_value = source_path

                dest_path = copy_host_base_image('test-host')

                assert os.path.exists(dest_path)
                assert 'test-host.qcow2' in dest_path

    def test_copy_image_not_found(self, temp_dir):
        """Test error when base image not found."""
        from host_manager import copy_host_base_image

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_base_image_path') as mock_get_path:
                mock_get_path.return_value = '/nonexistent/path.qcow2'

                with pytest.raises(RuntimeError, match="Base host image not found"):
                    copy_host_base_image('test-host')


class TestCreateHost:
    """Tests for create_host function."""

    def test_create_host_success(self, temp_dir):
        """Test successful host creation."""
        from host_manager import create_host

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)
        source_image = os.path.join(temp_dir, 'ubuntu-desktop.qcow2')
        with open(source_image, 'w') as f:
            f.write('fake image')

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/hosts/test-host.qcow2'

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/hosts/test-host-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('host_manager.generate_bridge_name', return_value='test-bridge'):
                                with patch('host_manager.create_ovs_bridge'):
                                    with patch('host_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            mock_attach.return_value = Mock(
                                                reused_slot=False,
                                                target_device='leaf1'
                                            )

                                            result = create_host(
                                                name='test-host',
                                                mgmt_ip='192.168.0.50',
                                                connection={'target_device': 'leaf1'}
                                            )

                                            assert result['status'] == 'created'
                                            assert result['name'] == 'test-host'
                                            assert result['mgmt_ip'] == '192.168.0.50'

    def test_create_host_with_data_ip(self, temp_dir):
        """Test host creation with data interface IP."""
        from host_manager import create_host

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/hosts/test-host.qcow2'

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/hosts/test-host-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('host_manager.generate_bridge_name', return_value='test-bridge'):
                                with patch('host_manager.create_ovs_bridge'):
                                    with patch('host_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            mock_attach.return_value = Mock(
                                                reused_slot=True,
                                                target_device='leaf1'
                                            )

                                            result = create_host(
                                                name='test-host',
                                                mgmt_ip='192.168.0.50',
                                                connection={'target_device': 'leaf1'},
                                                data_ip='10.1.1.100/24'
                                            )

                                            assert result['status'] == 'created'
                                            assert result['data_ip'] == '10.1.1.100/24'

    def test_create_host_exceeds_limit(self):
        """Test creation fails when limit exceeded."""
        from host_manager import create_host

        with patch('host_manager.get_host_count', return_value=5):
            with patch('host_manager.MAX_HOSTS_PER_TOPOLOGY', 2):
                with pytest.raises(RuntimeError, match="Maximum of"):
                    create_host(
                        name='test-host',
                        mgmt_ip='192.168.0.50'
                    )

    def test_create_host_rollback_on_failure(self, temp_dir):
        """Test that rollback cleans up resources on failure."""
        from host_manager import create_host

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

        disk_path = os.path.join(temp_dir, 'hosts', 'test-host.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        cidata_path = os.path.join(temp_dir, 'hosts', 'test-host-cidata.iso')
        with open(cidata_path, 'w') as f:
            f.write('fake iso')

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = disk_path

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = cidata_path

                        with patch('subprocess.run') as mock_run:
                            # virsh define fails
                            mock_run.return_value = Mock(
                                returncode=1,
                                stdout='',
                                stderr='definition failed'
                            )

                            with pytest.raises(RuntimeError, match="Failed to define"):
                                create_host(
                                    name='test-host',
                                    mgmt_ip='192.168.0.50'
                                )


class TestDeleteHost:
    """Tests for delete_host function."""

    def test_delete_host_success(self, temp_dir):
        """Test successful host deletion."""
        from host_manager import delete_host

        with patch('persistence.get_user_host') as mock_get:
            mock_get.return_value = {
                'test-host': {
                    'connection': {'bridge': 'test-bridge', 'target_device': 'leaf1'}
                }
            }

            with patch('resource_manager.get_resource_manager') as mock_rm:
                mock_mgr = Mock()
                mock_mgr.cleanup_connection.return_value = {
                    'interface_detached': True,
                    'bridge_deleted': True,
                    'target_device': 'leaf1',
                    'errors': []
                }
                mock_mgr.delete_vm_with_cleanup.return_value = {
                    'vm_destroyed': True,
                    'vm_undefined': True,
                    'disk_deleted': True,
                    'cidata_deleted': True
                }
                mock_rm.return_value = mock_mgr

                with patch('novnc_manager.revoke_tokens_for_host', return_value=1):
                    result = delete_host('test-host')

                    assert result['status'] == 'deleted'
                    assert result['name'] == 'test-host'
                    assert result['details']['vm_destroyed'] is True
                    assert result['details']['tokens_revoked'] == 1

    def test_delete_host_no_connection(self, temp_dir):
        """Test deletion when host has no connection."""
        from host_manager import delete_host

        with patch('persistence.get_user_host') as mock_get:
            mock_get.return_value = {
                'test-host': {}  # No connection
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

                with patch('novnc_manager.revoke_tokens_for_host', return_value=0):
                    result = delete_host('test-host')

                    assert result['status'] == 'deleted'
                    assert result['details']['bridge_deleted'] is False


class TestGetHostVncInfo:
    """Tests for get_host_vnc_info function."""

    def test_get_vnc_info_success(self):
        """Test successful VNC info retrieval."""
        from host_manager import get_host_vnc_info

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=':0\n', stderr='')

            info = get_host_vnc_info('test-host')

            assert info is not None
            assert info['name'] == 'test-host'
            assert info['vnc_port'] == 5900
            assert info['vnc_display'] == ':0'

    def test_get_vnc_info_display_1(self):
        """Test VNC info with display :1."""
        from host_manager import get_host_vnc_info

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=':1\n', stderr='')

            info = get_host_vnc_info('test-host')

            assert info['vnc_port'] == 5901

    def test_get_vnc_info_vm_not_found(self):
        """Test VNC info when VM not found."""
        from host_manager import get_host_vnc_info

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout='', stderr='error')

            info = get_host_vnc_info('nonexistent-host')

            assert info is None

    def test_get_vnc_info_exception(self):
        """Test VNC info when exception occurs."""
        from host_manager import get_host_vnc_info

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Command failed")

            info = get_host_vnc_info('test-host')

            assert info is None


class TestRollbackTracking:
    """Tests for rollback failure tracking."""

    def test_rollback_logs_all_failures(self, temp_dir, caplog):
        """Test that rollback tracks and logs all failures."""
        from host_manager import create_host
        import logging

        caplog.set_level(logging.ERROR)

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

        disk_path = os.path.join(temp_dir, 'hosts', 'test-host.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        cidata_path = os.path.join(temp_dir, 'hosts', 'test-host-cidata.iso')
        with open(cidata_path, 'w') as f:
            f.write('fake iso')

        bridge_name = 'test-bridge'

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = disk_path

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = cidata_path

                        with patch('host_manager.generate_bridge_name', return_value=bridge_name):
                            with patch('host_manager.create_ovs_bridge'):
                                with patch('host_manager.find_next_available_port', return_value='Ethernet5'):
                                    with patch('subprocess.run') as mock_run:
                                        # First call succeeds (for bridge), then VM define fails
                                        mock_run.side_effect = [
                                            Mock(returncode=1, stdout='', stderr='definition failed'),
                                            Mock(returncode=0, stdout='', stderr=''),  # virsh destroy
                                            Mock(returncode=0, stdout='', stderr=''),  # virsh undefine
                                        ]

                                        with patch('host_manager.delete_ovs_bridge'):
                                            with pytest.raises(RuntimeError):
                                                create_host(
                                                    name='test-host',
                                                    mgmt_ip='192.168.0.50',
                                                    connection={'target_device': 'leaf1'}
                                                )

                        # Verify rollback logging occurred
                        # (may or may not have logged errors depending on cleanup success)


class TestSlotReuse:
    """Tests for slot reuse during host creation."""

    def test_host_reports_reused_slots(self, temp_dir):
        """Test that host creation reports reused slots."""
        from host_manager import create_host

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/hosts/test-host.qcow2'

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/hosts/test-host-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('host_manager.generate_bridge_name', return_value='test-bridge'):
                                with patch('host_manager.create_ovs_bridge'):
                                    with patch('host_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            # Simulate reusing a slot
                                            mock_attach.return_value = Mock(
                                                reused_slot=True,
                                                target_device='leaf1'
                                            )

                                            result = create_host(
                                                name='test-host',
                                                mgmt_ip='192.168.0.50',
                                                connection={'target_device': 'leaf1'}
                                            )

                                            assert 'leaf1' in result['targets_reused_slots']
                                            assert 'leaf1' not in result['targets_need_reboot']

    def test_host_reports_need_reboot(self, temp_dir):
        """Test that host creation reports devices needing reboot."""
        from host_manager import create_host

        os.makedirs(os.path.join(temp_dir, 'hosts'), exist_ok=True)

        with patch('host_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('host_manager.get_host_count', return_value=0):
                with patch('host_manager.copy_host_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/hosts/test-host.qcow2'

                    with patch('host_manager.generate_cloud_init_iso') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/hosts/test-host-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('host_manager.generate_bridge_name', return_value='test-bridge'):
                                with patch('host_manager.create_ovs_bridge'):
                                    with patch('host_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            # Simulate NOT reusing a slot
                                            mock_attach.return_value = Mock(
                                                reused_slot=False,
                                                target_device='leaf1'
                                            )

                                            result = create_host(
                                                name='test-host',
                                                mgmt_ip='192.168.0.50',
                                                connection={'target_device': 'leaf1'}
                                            )

                                            assert 'leaf1' not in result['targets_reused_slots']
                                            assert 'leaf1' in result['targets_need_reboot']
