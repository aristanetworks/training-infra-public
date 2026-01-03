"""
Unit tests for firewall_manager module.

Tests cover:
- VyOS firewall creation
- Cloud-init ISO generation for VyOS (hostname-only)
- XML generation for VMs
- Firewall count and limit checking
- Deletion and cleanup
- Rollback on failure

Note: Interface IPs are configured directly in VyOS after boot,
not via cloud-init or the nodebuilder API.
"""

import os
import pytest
import tempfile
from unittest.mock import patch, Mock, MagicMock


class TestGetFirewallCount:
    """Tests for get_firewall_count function."""

    def test_count_created_firewalls(self, temp_dir):
        """Test counting firewalls with created status."""
        from firewall_manager import get_firewall_count

        firewalls_path = os.path.join(temp_dir, 'user_firewalls.yaml')

        with patch('firewall_manager.USER_FIREWALLS_PATH', firewalls_path):
            with patch('persistence.load_user_firewalls') as mock_load:
                mock_load.return_value = {
                    'firewalls': [
                        {'fw1': {'status': 'created'}},
                        {'fw2': {'status': 'created'}}
                    ]
                }
                count = get_firewall_count()
                assert count == 2

    def test_excludes_creating_status(self, temp_dir):
        """Test that 'creating' status firewalls are excluded."""
        from firewall_manager import get_firewall_count

        firewalls_path = os.path.join(temp_dir, 'user_firewalls.yaml')

        with patch('firewall_manager.USER_FIREWALLS_PATH', firewalls_path):
            with patch('persistence.load_user_firewalls') as mock_load:
                mock_load.return_value = {
                    'firewalls': [
                        {'fw1': {'status': 'created'}},
                        {'fw2': {'status': 'creating'}}
                    ]
                }
                count = get_firewall_count()
                assert count == 1

    def test_empty_list(self, temp_dir):
        """Test counting when no firewalls exist."""
        from firewall_manager import get_firewall_count

        firewalls_path = os.path.join(temp_dir, 'user_firewalls.yaml')

        with patch('firewall_manager.USER_FIREWALLS_PATH', firewalls_path):
            with patch('persistence.load_user_firewalls') as mock_load:
                mock_load.return_value = {'firewalls': []}
                count = get_firewall_count()
                assert count == 0


class TestGenerateVyosCloudInit:
    """Tests for generate_vyos_cloud_init function.

    Note: The function now only takes hostname - interface IPs are
    configured directly in VyOS after boot.
    """

    def test_basic_cloud_init_generation(self, temp_dir):
        """Test basic VyOS cloud-init ISO generation."""
        from firewall_manager import generate_vyos_cloud_init

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                # Create firewall subdirectory
                os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                    iso_path = generate_vyos_cloud_init(hostname='test-fw1')

                    assert 'test-fw1-cidata.iso' in iso_path
                    mock_run.assert_called_once()

    def test_cloud_init_uses_template(self, temp_dir):
        """Test cloud-init uses template file when available."""
        from firewall_manager import generate_vyos_cloud_init

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        # Create a mock template - simplified to hostname only
        # (users configure interfaces manually after boot)
        template_content = """#cloud-config
vyos_config_commands:
  - set system host-name {hostname}
  - set system time-zone UTC
  - set system console device ttyS0 speed 115200
  - set service ssh port 22
"""
        template_path = os.path.join(temp_dir, 'vyos-firewall-template.yaml')
        with open(template_path, 'w') as f:
            f.write(template_content)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                    iso_path = generate_vyos_cloud_init(hostname='test-fw1')

                    assert 'test-fw1-cidata.iso' in iso_path

    def test_cloud_init_no_iso_tools(self, temp_dir):
        """Test error when no ISO tools available."""
        from firewall_manager import generate_vyos_cloud_init

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.CLOUD_INIT_TEMPLATES_PATH', temp_dir):
                os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = FileNotFoundError("Command not found")

                    with pytest.raises(RuntimeError, match="Neither genisoimage nor mkisofs"):
                        generate_vyos_cloud_init(hostname='test-fw1')


class TestGenerateFirewallXml:
    """Tests for generate_firewall_xml function."""

    def test_basic_xml_generation(self):
        """Test basic XML generation for firewall."""
        from firewall_manager import generate_firewall_xml

        xml = generate_firewall_xml(name='test-fw')

        assert '<name>test-fw</name>' in xml
        assert 'vmgmt' in xml  # Management bridge
        assert 'test-fw.qcow2' in xml
        assert 'test-fw-cidata.iso' in xml

    def test_xml_with_inside_connection(self):
        """Test XML generation with inside connection only."""
        from firewall_manager import generate_firewall_xml

        inside_conn = {
            'bridge': 'fw1_eth1-leaf1_Et5',
            'local_port': 'eth1'
        }

        xml = generate_firewall_xml(name='test-fw', inside_connection=inside_conn)

        assert '<name>test-fw</name>' in xml
        assert 'fw1_eth1-leaf1_Et5' in xml
        assert 'openvswitch' in xml

    def test_xml_with_both_connections(self):
        """Test XML generation with both inside and outside connections."""
        from firewall_manager import generate_firewall_xml

        inside_conn = {
            'bridge': 'fw1_eth1-leaf1_Et5',
            'local_port': 'eth1'
        }
        outside_conn = {
            'bridge': 'fw1_eth2-spine1_Et3',
            'local_port': 'eth2'
        }

        xml = generate_firewall_xml(
            name='test-fw',
            inside_connection=inside_conn,
            outside_connection=outside_conn
        )

        assert 'fw1_eth1-leaf1_Et5' in xml
        assert 'fw1_eth2-spine1_Et3' in xml
        # Check PCI slots are different for each interface
        # ElementTree uses double quotes in output
        assert 'slot="0x04"' in xml  # eth1 (inside)
        assert 'slot="0x05"' in xml  # eth2 (outside)


class TestCopyFirewallBaseImage:
    """Tests for copy_firewall_base_image function."""

    def test_copy_base_image(self, temp_dir):
        """Test copying base VyOS image successfully."""
        from firewall_manager import copy_firewall_base_image

        # Create source image
        source_path = os.path.join(temp_dir, 'vyos-1.4.qcow2')
        with open(source_path, 'w') as f:
            f.write('fake vyos image data')

        dest_dir = os.path.join(temp_dir, 'firewall')
        os.makedirs(dest_dir, exist_ok=True)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_base_image_path') as mock_get_path:
                mock_get_path.return_value = source_path

                dest_path = copy_firewall_base_image('test-fw')

                assert os.path.exists(dest_path)
                assert 'test-fw.qcow2' in dest_path

    def test_copy_image_not_found(self, temp_dir):
        """Test error when base VyOS image not found."""
        from firewall_manager import copy_firewall_base_image

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_base_image_path') as mock_get_path:
                mock_get_path.return_value = '/nonexistent/vyos.qcow2'

                with pytest.raises(RuntimeError, match="Base VyOS image not found"):
                    copy_firewall_base_image('test-fw')


class TestCreateFirewall:
    """Tests for create_firewall function.

    Note: Interface IPs are no longer passed to create_firewall -
    they are configured directly in VyOS after boot.
    """

    def test_create_firewall_success(self, temp_dir):
        """Test successful firewall creation."""
        from firewall_manager import create_firewall

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_count', return_value=0):
                with patch('firewall_manager.copy_firewall_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/firewall/test-fw.qcow2'

                    with patch('firewall_manager.generate_vyos_cloud_init') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/firewall/test-fw-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('firewall_manager.generate_bridge_name') as mock_bridge:
                                mock_bridge.side_effect = ['inside-bridge', 'outside-bridge']

                                with patch('firewall_manager.create_ovs_bridge'):
                                    with patch('firewall_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            mock_attach.return_value = Mock(
                                                reused_slot=False,
                                                target_device='leaf1'
                                            )

                                            with patch('slot_reuse.apply_mutual_exclusivity') as mock_mutex:
                                                mock_mutex.return_value = ([], ['leaf1', 'spine1'])

                                                result = create_firewall(
                                                    name='test-fw',
                                                    mgmt_ip='192.168.0.50',
                                                    inside_interface={
                                                        'target_device': 'leaf1'
                                                    },
                                                    outside_interface={
                                                        'target_device': 'spine1'
                                                    }
                                                )

                                                assert result['status'] == 'created'
                                                assert result['name'] == 'test-fw'
                                                assert result['mgmt_ip'] == '192.168.0.50'
                                                # IPs are no longer in the result
                                                assert result['inside_interface']['target_device'] == 'leaf1'
                                                assert result['outside_interface']['target_device'] == 'spine1'

    def test_create_firewall_missing_inside_device(self):
        """Test creation fails when inside target device is missing."""
        from firewall_manager import create_firewall

        with patch('firewall_manager.get_firewall_count', return_value=0):
            with pytest.raises(ValueError, match="Inside interface target device is required"):
                create_firewall(
                    name='test-fw',
                    mgmt_ip='192.168.0.50',
                    inside_interface={},  # No target_device
                    outside_interface={'target_device': 'spine1'}
                )

    def test_create_firewall_missing_outside_device(self):
        """Test creation fails when outside target device is missing."""
        from firewall_manager import create_firewall

        with patch('firewall_manager.get_firewall_count', return_value=0):
            with pytest.raises(ValueError, match="Outside interface target device is required"):
                create_firewall(
                    name='test-fw',
                    mgmt_ip='192.168.0.50',
                    inside_interface={'target_device': 'leaf1'},
                    outside_interface={}  # No target_device
                )

    def test_create_firewall_exceeds_limit(self):
        """Test creation fails when limit exceeded."""
        from firewall_manager import create_firewall

        with patch('firewall_manager.get_firewall_count', return_value=5):
            with patch('firewall_manager.MAX_FIREWALLS_PER_TOPOLOGY', 1):
                with pytest.raises(RuntimeError, match="Maximum of"):
                    create_firewall(
                        name='test-fw',
                        mgmt_ip='192.168.0.50',
                        inside_interface={'target_device': 'leaf1'},
                        outside_interface={'target_device': 'spine1'}
                    )

    def test_create_firewall_rollback_on_failure(self, temp_dir):
        """Test that rollback cleans up resources on failure."""
        from firewall_manager import create_firewall

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        disk_path = os.path.join(temp_dir, 'firewall', 'test-fw.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        cidata_path = os.path.join(temp_dir, 'firewall', 'test-fw-cidata.iso')
        with open(cidata_path, 'w') as f:
            f.write('fake iso')

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_count', return_value=0):
                with patch('firewall_manager.copy_firewall_base_image') as mock_copy:
                    mock_copy.return_value = disk_path

                    with patch('firewall_manager.generate_vyos_cloud_init') as mock_cloudinit:
                        mock_cloudinit.return_value = cidata_path

                        # Patch in connection_manager since process_connection_for_creation is there
                        with patch('connection_manager.generate_bridge_name') as mock_bridge:
                            mock_bridge.side_effect = ['inside-bridge', 'outside-bridge']

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
                                                create_firewall(
                                                    name='test-fw',
                                                    mgmt_ip='192.168.0.50',
                                                    inside_interface={
                                                        'target_device': 'leaf1'
                                                    },
                                                    outside_interface={
                                                        'target_device': 'spine1'
                                                    }
                                                )


class TestDeleteFirewall:
    """Tests for delete_firewall function."""

    def test_delete_firewall_success(self, temp_dir):
        """Test successful firewall deletion."""
        from firewall_manager import delete_firewall

        with patch('persistence.get_user_firewall') as mock_get:
            mock_get.return_value = {
                'test-fw': {
                    'inside_interface': {'bridge': 'inside-bridge', 'target_device': 'leaf1'},
                    'outside_interface': {'bridge': 'outside-bridge', 'target_device': 'spine1'}
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

                result = delete_firewall('test-fw')

                assert result['status'] == 'deleted'
                assert result['name'] == 'test-fw'
                assert result['details']['vm_destroyed'] is True
                assert result['details']['inside_bridge_deleted'] is True
                assert result['details']['outside_bridge_deleted'] is True

    def test_delete_firewall_not_found(self, temp_dir):
        """Test deletion when firewall not found in persistence."""
        from firewall_manager import delete_firewall

        with patch('persistence.get_user_firewall') as mock_get:
            mock_get.return_value = None

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

                result = delete_firewall('nonexistent-fw')

                assert result['status'] == 'deleted'
                assert result['details']['inside_bridge_deleted'] is False


class TestEditFirewall:
    """Tests for edit_firewall function.

    Note: edit_firewall now returns 'no_changes' since interface IPs
    are configured directly in VyOS, not via the nodebuilder API.
    """

    def test_edit_firewall_returns_no_changes(self):
        """Test that edit_firewall returns no_changes status.

        IPs are now configured directly in VyOS, so API-based
        IP editing is no longer supported.
        """
        from firewall_manager import edit_firewall

        with patch('persistence.get_user_firewall') as mock_get:
            mock_get.return_value = {
                'test-fw': {
                    'mgmt_ip': '192.168.0.50',
                    'inside_interface': {'target_device': 'leaf1'},
                    'outside_interface': {'target_device': 'spine1'}
                }
            }

            result = edit_firewall(
                name='test-fw',
                inside_interface={'ip': '10.1.1.100/24'},
                outside_interface={'ip': '10.2.2.100/24'}
            )

            # Should return no_changes with a note about configuring in VyOS
            assert result['status'] == 'no_changes'
            assert 'note' in result
            assert 'VyOS' in result['note']

    def test_edit_firewall_no_changes(self):
        """Test editing returns no_changes."""
        from firewall_manager import edit_firewall

        with patch('persistence.get_user_firewall') as mock_get:
            mock_get.return_value = {
                'test-fw': {
                    'mgmt_ip': '192.168.0.50',
                    'inside_interface': {'target_device': 'leaf1'},
                    'outside_interface': {'target_device': 'spine1'}
                }
            }

            result = edit_firewall(name='test-fw')

            assert result['status'] == 'no_changes'

    def test_edit_firewall_not_found(self):
        """Test editing non-existent firewall."""
        from firewall_manager import edit_firewall

        with patch('persistence.get_user_firewall') as mock_get:
            mock_get.return_value = None

            with pytest.raises(ValueError, match="not found"):
                edit_firewall(name='nonexistent-fw')


class TestRollbackTracking:
    """Tests for rollback failure tracking in firewall creation."""

    def test_rollback_cleans_both_bridges(self, temp_dir, caplog):
        """Test that rollback cleans up both inside and outside bridges."""
        from firewall_manager import create_firewall
        import logging

        caplog.set_level(logging.INFO)

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        disk_path = os.path.join(temp_dir, 'firewall', 'test-fw.qcow2')
        with open(disk_path, 'w') as f:
            f.write('fake image')

        cidata_path = os.path.join(temp_dir, 'firewall', 'test-fw-cidata.iso')
        with open(cidata_path, 'w') as f:
            f.write('fake iso')

        deleted_bridges = []

        def track_delete(bridge_name):
            deleted_bridges.append(bridge_name)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_count', return_value=0):
                with patch('firewall_manager.copy_firewall_base_image') as mock_copy:
                    mock_copy.return_value = disk_path

                    with patch('firewall_manager.generate_vyos_cloud_init') as mock_cloudinit:
                        mock_cloudinit.return_value = cidata_path

                        # Patch in connection_manager since process_connection_for_creation is there
                        with patch('connection_manager.generate_bridge_name') as mock_bridge:
                            mock_bridge.side_effect = ['inside-bridge', 'outside-bridge']

                            with patch('connection_manager.create_ovs_bridge'):
                                with patch('connection_manager.find_next_available_port', return_value='Ethernet5'):
                                    with patch('subprocess.run') as mock_run:
                                        mock_run.return_value = Mock(
                                            returncode=1,
                                            stdout='',
                                            stderr='definition failed'
                                        )

                                        # Patch delete_ovs_bridge in resource_manager since rollback
                                        # now uses ResourceTransaction which is in that module
                                        with patch('resource_manager.delete_ovs_bridge', side_effect=track_delete):
                                            with pytest.raises(RuntimeError):
                                                create_firewall(
                                                    name='test-fw',
                                                    mgmt_ip='192.168.0.50',
                                                    inside_interface={
                                                        'target_device': 'leaf1'
                                                    },
                                                    outside_interface={
                                                        'target_device': 'spine1'
                                                    }
                                                )

                                            # Verify both bridges were attempted to be deleted
                                            assert 'inside-bridge' in deleted_bridges
                                            assert 'outside-bridge' in deleted_bridges


class TestSlotReuse:
    """Tests for slot reuse and mutual exclusivity during firewall creation."""

    def test_firewall_reports_devices_needing_reboot(self, temp_dir):
        """Test that firewall creation properly reports devices needing reboot."""
        from firewall_manager import create_firewall

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_count', return_value=0):
                with patch('firewall_manager.copy_firewall_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/firewall/test-fw.qcow2'

                    with patch('firewall_manager.generate_vyos_cloud_init') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/firewall/test-fw-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('firewall_manager.generate_bridge_name') as mock_bridge:
                                mock_bridge.side_effect = ['inside-bridge', 'outside-bridge']

                                with patch('firewall_manager.create_ovs_bridge'):
                                    with patch('firewall_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            # Both interfaces don't reuse slots
                                            mock_attach.side_effect = [
                                                Mock(reused_slot=False, target_device='leaf1'),
                                                Mock(reused_slot=False, target_device='spine1')
                                            ]

                                            with patch('slot_reuse.apply_mutual_exclusivity') as mock_mutex:
                                                mock_mutex.return_value = ([], ['leaf1', 'spine1'])

                                                result = create_firewall(
                                                    name='test-fw',
                                                    mgmt_ip='192.168.0.50',
                                                    inside_interface={
                                                        'target_device': 'leaf1'
                                                    },
                                                    outside_interface={
                                                        'target_device': 'spine1'
                                                    }
                                                )

                                                assert result['targets_reused_slots'] == []
                                                assert 'leaf1' in result['targets_need_reboot']
                                                assert 'spine1' in result['targets_need_reboot']

    def test_firewall_mutual_exclusivity_applied(self, temp_dir):
        """Test that mutual exclusivity removes duplicates correctly."""
        from firewall_manager import create_firewall

        os.makedirs(os.path.join(temp_dir, 'firewall'), exist_ok=True)

        with patch('firewall_manager.LIBVIRT_IMAGES_PATH', temp_dir):
            with patch('firewall_manager.get_firewall_count', return_value=0):
                with patch('firewall_manager.copy_firewall_base_image') as mock_copy:
                    mock_copy.return_value = f'{temp_dir}/firewall/test-fw.qcow2'

                    with patch('firewall_manager.generate_vyos_cloud_init') as mock_cloudinit:
                        mock_cloudinit.return_value = f'{temp_dir}/firewall/test-fw-cidata.iso'

                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                            with patch('firewall_manager.generate_bridge_name') as mock_bridge:
                                mock_bridge.side_effect = ['inside-bridge', 'outside-bridge']

                                with patch('firewall_manager.create_ovs_bridge'):
                                    with patch('firewall_manager.find_next_available_port', return_value='Ethernet5'):
                                        with patch('slot_reuse.attach_interface_with_slot_reuse') as mock_attach:
                                            # Same device for both interfaces, one reuses slot, one doesn't
                                            mock_attach.side_effect = [
                                                Mock(reused_slot=True, target_device='leaf1'),  # Inside
                                                Mock(reused_slot=False, target_device='leaf1')  # Outside
                                            ]

                                            with patch('slot_reuse.apply_mutual_exclusivity') as mock_mutex:
                                                # Device needs reboot because one interface didn't reuse
                                                mock_mutex.return_value = ([], ['leaf1'])

                                                result = create_firewall(
                                                    name='test-fw',
                                                    mgmt_ip='192.168.0.50',
                                                    inside_interface={
                                                        'target_device': 'leaf1',
                                                        'target_port': 'Ethernet5'
                                                    },
                                                    outside_interface={
                                                        'target_device': 'leaf1',
                                                        'target_port': 'Ethernet6'
                                                    }
                                                )

                                                # Device should only appear in need_reboot, not reused_slots
                                                assert result['targets_reused_slots'] == []
                                                assert result['targets_need_reboot'] == ['leaf1']
