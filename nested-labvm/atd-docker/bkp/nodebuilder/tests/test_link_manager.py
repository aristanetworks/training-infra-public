"""
Unit tests for link_manager module.

Tests cover:
- Protection rules: only original topology devices can be linked
- Protection rules: only user-added links can be removed
- add_link success and failure paths
- remove_link success and error paths
- get_user_links listing
- is_user_link check
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock


class TestAddLink:
    """Tests for add_link function."""

    def _mock_resource_mgr(self, known_vms=None):
        """Create a mock resource manager that knows about specific VMs.
        Supports case-insensitive lookup like the real ResourceManager."""
        if known_vms is None:
            known_vms = ['spine1', 'leaf1', 'leaf2']
        mgr = MagicMock()
        # vm_exists tries original name then lowercase
        mgr.vm_exists.side_effect = lambda name: (
            name in known_vms or name.lower() in known_vms
        )
        # resolve_domain_name returns the actual VM name
        def _resolve(name):
            if name in known_vms:
                return name
            if name.lower() in known_vms:
                return name.lower()
            return name
        mgr.resolve_domain_name.side_effect = _resolve
        return mgr

    def test_add_link_success(self, temp_dir, mock_topo_build_file, mock_user_links_file):
        """Test adding a link between two existing VMs."""
        from link_manager import add_link

        mock_slot_result = Mock()
        mock_slot_result.reused_slot = False
        mock_slot_result.needs_reboot = True
        mock_slot_result.target_device = 'spine1'

        mock_slot_result2 = Mock()
        mock_slot_result2.reused_slot = False
        mock_slot_result2.needs_reboot = True
        mock_slot_result2.target_device = 'leaf1'

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            with patch('link_manager.create_ovs_bridge') as mock_create:
                mock_create.return_value = {'status': 'created'}

                with patch('link_manager.attach_interface_with_slot_reuse') as mock_attach:
                    mock_attach.side_effect = [mock_slot_result, mock_slot_result2]

                    with patch('link_manager.apply_mutual_exclusivity') as mock_mutex:
                        mock_mutex.return_value = ([], ['spine1', 'leaf1'])

                        with patch('link_manager.save_user_link') as mock_save:
                            mock_save.return_value = True

                            result = add_link(
                                source_device='spine1',
                                source_port='Ethernet5',
                                target_device='leaf1',
                                target_port='Ethernet5',
                                user_links_path=mock_user_links_file,
                                topo_build_path=mock_topo_build_file
                            )

        assert result['status'] == 'success'
        assert 'bridge_name' in result
        assert result['source_device'] == 'spine1'
        assert result['target_device'] == 'leaf1'
        mock_create.assert_called_once()
        mock_save.assert_called_once()

    def test_add_link_source_vm_not_found(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test that add_link rejects source device that doesn't exist as VM."""
        from link_manager import add_link

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            result = add_link(
                source_device='unknowndevice',
                source_port='Ethernet1',
                target_device='leaf1',
                target_port='Ethernet5',
                user_links_path=mock_user_links_file,
                topo_build_path=mock_topo_build_file
            )

        assert result['status'] == 'error'
        assert 'unknowndevice' in result['error']

    def test_add_link_target_vm_not_found(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test that add_link rejects target device that doesn't exist as VM."""
        from link_manager import add_link

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            result = add_link(
                source_device='spine1',
                source_port='Ethernet5',
                target_device='nonexistentdevice',
                target_port='Ethernet5',
                user_links_path=mock_user_links_file,
                topo_build_path=mock_topo_build_file
            )

        assert result['status'] == 'error'
        assert 'nonexistentdevice' in result['error']

    def test_add_link_ovs_failure_returns_error(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test that add_link returns error when OVS bridge creation fails."""
        from link_manager import add_link

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            with patch('link_manager.create_ovs_bridge') as mock_create:
                mock_create.side_effect = RuntimeError("OVS not available")

                result = add_link(
                    source_device='spine1',
                    source_port='Ethernet5',
                    target_device='leaf1',
                    target_port='Ethernet5',
                    user_links_path=mock_user_links_file,
                    topo_build_path=mock_topo_build_file
                )

        assert result['status'] == 'error'
        assert 'OVS' in result['error'] or 'bridge' in result['error'].lower()

    def test_add_link_attachment_failure_triggers_rollback(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test that add_link rolls back OVS bridge when interface attachment fails."""
        from link_manager import add_link

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            with patch('link_manager.create_ovs_bridge') as mock_create:
                mock_create.return_value = {'status': 'created'}

                with patch('link_manager.attach_interface_with_slot_reuse') as mock_attach:
                    mock_attach.side_effect = RuntimeError("virsh attach failed")

                    with patch('link_manager.delete_ovs_bridge') as mock_delete:
                        result = add_link(
                            source_device='spine1',
                            source_port='Ethernet5',
                            target_device='leaf1',
                            target_port='Ethernet5',
                            user_links_path=mock_user_links_file,
                            topo_build_path=mock_topo_build_file
                        )

        assert result['status'] == 'error'
        mock_delete.assert_called_once()

    def test_add_link_both_devices_same_topology(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test adding a link between leaf1 and leaf2 (both existing VMs)."""
        from link_manager import add_link

        mock_slot_result = Mock()
        mock_slot_result.reused_slot = False
        mock_slot_result.needs_reboot = True
        mock_slot_result.target_device = 'leaf1'

        mock_slot_result2 = Mock()
        mock_slot_result2.reused_slot = True
        mock_slot_result2.needs_reboot = False
        mock_slot_result2.target_device = 'leaf2'

        with patch('link_manager.get_resource_manager', return_value=self._mock_resource_mgr()):
            with patch('link_manager.create_ovs_bridge', return_value={'status': 'created'}):
                with patch('link_manager.attach_interface_with_slot_reuse') as mock_attach:
                    mock_attach.side_effect = [mock_slot_result, mock_slot_result2]

                    with patch('link_manager.apply_mutual_exclusivity') as mock_mutex:
                        mock_mutex.return_value = (['leaf2'], ['leaf1'])

                        with patch('link_manager.save_user_link', return_value=True):
                            result = add_link(
                                source_device='leaf1',
                                source_port='Ethernet5',
                                target_device='leaf2',
                                target_port='Ethernet5',
                                user_links_path=mock_user_links_file,
                                topo_build_path=mock_topo_build_file
                            )

        assert result['status'] == 'success'
        assert 'leaf1' in result['targets_need_reboot']
        assert 'leaf2' in result['targets_reused_slots']

    def test_add_link_uppercase_vm_names(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test adding a link with uppercase VM names (L4 topology: P4, PE1).
        VMs on L4 topology use uppercase domain names in libvirt."""
        from link_manager import add_link

        # L4 topology VMs are uppercase in libvirt
        known_vms = ['P4', 'P5', 'PE1', 'PE2']

        mock_slot_result = Mock()
        mock_slot_result.reused_slot = False
        mock_slot_result.needs_reboot = True
        mock_slot_result.target_device = 'P4'

        mock_slot_result2 = Mock()
        mock_slot_result2.reused_slot = False
        mock_slot_result2.needs_reboot = True
        mock_slot_result2.target_device = 'P5'

        with patch('link_manager.get_resource_manager',
                   return_value=self._mock_resource_mgr(known_vms)):
            with patch('link_manager.create_ovs_bridge', return_value={'status': 'created'}):
                with patch('link_manager.attach_interface_with_slot_reuse') as mock_attach:
                    mock_attach.side_effect = [mock_slot_result, mock_slot_result2]

                    with patch('link_manager.apply_mutual_exclusivity') as mock_mutex:
                        mock_mutex.return_value = ([], ['P4', 'P5'])

                        with patch('link_manager.save_user_link', return_value=True):
                            result = add_link(
                                source_device='P4',
                                source_port='Ethernet3',
                                target_device='P5',
                                target_port='Ethernet3',
                                user_links_path=mock_user_links_file,
                                topo_build_path=mock_topo_build_file
                            )

        assert result['status'] == 'success'
        assert result['source_device'] == 'P4'
        assert result['target_device'] == 'P5'

    def test_add_link_mixed_case_vm_names(
        self, temp_dir, mock_topo_build_file, mock_user_links_file
    ):
        """Test adding a link when UI sends mixed case but VMs are lowercase.
        Verifies case-insensitive fallback works for standard topologies."""
        from link_manager import add_link

        # Standard topology VMs are lowercase
        known_vms = ['spine1', 'leaf1']

        mock_slot_result = Mock()
        mock_slot_result.reused_slot = False
        mock_slot_result.needs_reboot = True
        mock_slot_result.target_device = 'spine1'

        mock_slot_result2 = Mock()
        mock_slot_result2.reused_slot = False
        mock_slot_result2.needs_reboot = True
        mock_slot_result2.target_device = 'leaf1'

        with patch('link_manager.get_resource_manager',
                   return_value=self._mock_resource_mgr(known_vms)):
            with patch('link_manager.create_ovs_bridge', return_value={'status': 'created'}):
                with patch('link_manager.attach_interface_with_slot_reuse') as mock_attach:
                    mock_attach.side_effect = [mock_slot_result, mock_slot_result2]

                    with patch('link_manager.apply_mutual_exclusivity') as mock_mutex:
                        mock_mutex.return_value = ([], ['spine1', 'leaf1'])

                        with patch('link_manager.save_user_link', return_value=True):
                            # UI sends "Spine1" but VM is "spine1"
                            result = add_link(
                                source_device='Spine1',
                                source_port='Ethernet5',
                                target_device='Leaf1',
                                target_port='Ethernet5',
                                user_links_path=mock_user_links_file,
                                topo_build_path=mock_topo_build_file
                            )

        assert result['status'] == 'success'


class TestRemoveLink:
    """Tests for remove_link function."""

    def test_remove_user_link_success(self, temp_dir, mock_user_links_file):
        """Test removing a link that exists in user_links.yaml succeeds."""
        from link_manager import remove_link
        from persistence import save_user_link

        # Pre-populate user_links.yaml with a link entry
        save_user_link({
            'source_device': 'spine1',
            'source_port': 'Ethernet5',
            'target_device': 'leaf1',
            'target_port': 'Ethernet5',
            'bridge_name': 'sp1x5-le1x5'
        }, mock_user_links_file)

        with patch('link_manager.delete_ovs_bridge') as mock_delete:
            mock_delete.return_value = {'status': 'deleted'}

            result = remove_link(
                source_device='spine1',
                source_port='Ethernet5',
                target_device='leaf1',
                target_port='Ethernet5',
                user_links_path=mock_user_links_file
            )

        assert result['status'] == 'success'
        assert result['bridge_name'] == 'sp1x5-le1x5'
        assert result['bridge_deleted'] is True
        mock_delete.assert_called_once_with('sp1x5-le1x5')

    def test_remove_original_link_rejected(self, temp_dir, mock_user_links_file):
        """Test that removing a link NOT in user_links.yaml is rejected."""
        from link_manager import remove_link

        # Do not add any link to user_links.yaml - link is 'original topology'
        result = remove_link(
            source_device='spine1',
            source_port='Ethernet1',
            target_device='leaf1',
            target_port='Ethernet1',
            user_links_path=mock_user_links_file
        )

        assert result['status'] == 'error'
        # Error must mention "original" or "topology" per spec
        error_lower = result['error'].lower()
        assert 'original' in error_lower or 'topology' in error_lower

    def test_remove_link_ovs_failure_still_removes_persistence(
        self, temp_dir, mock_user_links_file
    ):
        """Test that if OVS deletion fails, persistence entry is still removed."""
        from link_manager import remove_link
        from persistence import save_user_link, is_user_link

        save_user_link({
            'source_device': 'spine1',
            'source_port': 'Ethernet5',
            'target_device': 'leaf1',
            'target_port': 'Ethernet5',
            'bridge_name': 'sp1x5-le1x5'
        }, mock_user_links_file)

        with patch('link_manager.delete_ovs_bridge') as mock_delete:
            mock_delete.side_effect = RuntimeError("Bridge not found")

            result = remove_link(
                source_device='spine1',
                source_port='Ethernet5',
                target_device='leaf1',
                target_port='Ethernet5',
                user_links_path=mock_user_links_file
            )

        # Should report deleted_with_errors (bridge error logged)
        assert result['status'] in ('success', 'deleted_with_errors')
        assert result['bridge_deleted'] is False

        # Persistence should be cleaned up regardless
        assert not is_user_link(
            'spine1', 'Ethernet5', 'leaf1', 'Ethernet5', mock_user_links_file
        )

    def test_remove_link_returns_bridge_name(self, temp_dir, mock_user_links_file):
        """Test that remove_link returns the bridge name in result."""
        from link_manager import remove_link
        from persistence import save_user_link

        save_user_link({
            'source_device': 'leaf1',
            'source_port': 'Ethernet6',
            'target_device': 'leaf2',
            'target_port': 'Ethernet6',
            'bridge_name': 'le1x6-le2x6'
        }, mock_user_links_file)

        with patch('link_manager.delete_ovs_bridge', return_value={'status': 'deleted'}):
            result = remove_link(
                source_device='leaf1',
                source_port='Ethernet6',
                target_device='leaf2',
                target_port='Ethernet6',
                user_links_path=mock_user_links_file
            )

        assert result['status'] == 'success'
        assert result['bridge_name'] == 'le1x6-le2x6'


class TestGetUserLinks:
    """Tests for get_user_links function."""

    def test_list_empty(self, mock_user_links_file):
        """Test that get_user_links returns empty list when no links exist."""
        from link_manager import get_user_links

        links = get_user_links(user_links_path=mock_user_links_file)

        assert isinstance(links, list)
        assert len(links) == 0

    def test_list_with_links(self, mock_user_links_file):
        """Test that get_user_links returns all saved links."""
        from link_manager import get_user_links
        from persistence import save_user_link

        save_user_link({
            'source_device': 'spine1',
            'source_port': 'Ethernet5',
            'target_device': 'leaf1',
            'target_port': 'Ethernet5',
            'bridge_name': 'sp1x5-le1x5'
        }, mock_user_links_file)

        save_user_link({
            'source_device': 'leaf1',
            'source_port': 'Ethernet6',
            'target_device': 'leaf2',
            'target_port': 'Ethernet6',
            'bridge_name': 'le1x6-le2x6'
        }, mock_user_links_file)

        links = get_user_links(user_links_path=mock_user_links_file)

        assert len(links) == 2
        source_devices = [link['source_device'] for link in links]
        assert 'spine1' in source_devices
        assert 'leaf1' in source_devices

    def test_list_uses_persistence_layer(self, mock_user_links_file):
        """Test that get_user_links delegates to persistence.list_user_links."""
        from link_manager import get_user_links

        with patch('link_manager.persistence_list_user_links') as mock_list:
            mock_list.return_value = [
                {
                    'source_device': 'spine1',
                    'source_port': 'Ethernet5',
                    'target_device': 'leaf1',
                    'target_port': 'Ethernet5',
                    'bridge_name': 'sp1x5-le1x5'
                }
            ]

            links = get_user_links(user_links_path=mock_user_links_file)

        assert len(links) == 1
        mock_list.assert_called_once_with(mock_user_links_file)


class TestIsUserLink:
    """Tests for is_user_link function."""

    def test_user_link_returns_true(self, mock_user_links_file):
        """Test that is_user_link returns True when link is in user_links.yaml."""
        from link_manager import is_user_link
        from persistence import save_user_link

        save_user_link({
            'source_device': 'spine1',
            'source_port': 'Ethernet5',
            'target_device': 'leaf1',
            'target_port': 'Ethernet5',
            'bridge_name': 'sp1x5-le1x5'
        }, mock_user_links_file)

        result = is_user_link(
            source_device='spine1',
            source_port='Ethernet5',
            target_device='leaf1',
            target_port='Ethernet5',
            user_links_path=mock_user_links_file
        )

        assert result is True

    def test_original_link_returns_false(self, mock_user_links_file):
        """Test that is_user_link returns False when link is not in user_links.yaml."""
        from link_manager import is_user_link

        # This link is in the original topology (topo_build.yml) but not user_links.yaml
        result = is_user_link(
            source_device='spine1',
            source_port='Ethernet1',
            target_device='leaf1',
            target_port='Ethernet1',
            user_links_path=mock_user_links_file
        )

        assert result is False

    def test_is_user_link_case_insensitive(self, mock_user_links_file):
        """Test that is_user_link checks are case-insensitive."""
        from link_manager import is_user_link
        from persistence import save_user_link

        save_user_link({
            'source_device': 'Spine1',
            'source_port': 'Ethernet5',
            'target_device': 'Leaf1',
            'target_port': 'Ethernet5',
            'bridge_name': 'sp1x5-le1x5'
        }, mock_user_links_file)

        result = is_user_link(
            source_device='spine1',
            source_port='Ethernet5',
            target_device='leaf1',
            target_port='Ethernet5',
            user_links_path=mock_user_links_file
        )

        assert result is True

    def test_nonexistent_link_returns_false(self, mock_user_links_file):
        """Test is_user_link returns False when link does not exist at all."""
        from link_manager import is_user_link

        result = is_user_link(
            source_device='leaf1',
            source_port='Ethernet99',
            target_device='leaf2',
            target_port='Ethernet99',
            user_links_path=mock_user_links_file
        )

        assert result is False


class TestGetAvailablePorts:
    """Tests for get_available_ports function."""

    def test_get_available_ports_returns_list(self, mock_topo_build_file, mock_user_links_file):
        """Test that get_available_ports returns a list."""
        from link_manager import get_available_ports

        with patch('link_manager.find_next_available_port', return_value='Ethernet5'):
            ports = get_available_ports(
                device_name='spine1',
                topo_build_path=mock_topo_build_file,
                user_links_path=mock_user_links_file
            )

        assert isinstance(ports, list)

    def test_get_available_ports_exception_returns_empty(
        self, mock_topo_build_file, mock_user_links_file
    ):
        """Test that get_available_ports returns empty list on error."""
        from link_manager import get_available_ports

        with patch('link_manager.find_next_available_port') as mock_port:
            mock_port.side_effect = RuntimeError("virsh not available")

            ports = get_available_ports(
                device_name='spine1',
                topo_build_path=mock_topo_build_file,
                user_links_path=mock_user_links_file
            )

        assert ports == []
