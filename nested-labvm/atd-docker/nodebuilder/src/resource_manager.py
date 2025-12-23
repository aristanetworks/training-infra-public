"""
Resource Manager for Nodebuilder Service

Provides centralized management of node-related resources:
- VMs (destroy, undefine, autostart)
- Disk images (create, delete)
- OVS bridges (create, delete)
- Interface attachments (attach, detach)

This module is used by:
- NodeCreationTransaction for rollback on failures
- Node Delete functionality for full cleanup
- Node Edit for connection modifications
"""

import logging
import os
import subprocess
from typing import Dict, List, Optional

from interface_manager import (
    delete_ovs_bridge,
    detach_interface_from_vm,
    generate_bridge_name,
    get_vm_interfaces
)
from config import LIBVIRT_IMAGES_PATH

logger = logging.getLogger('nodebuilder')


class ResourceManager:
    """
    Centralized manager for node-related resource lifecycle.

    Provides both individual operations and composite cleanup methods
    for safe resource management during node operations.
    """

    def __init__(self):
        self.logger = logging.getLogger('nodebuilder.resource_manager')

    # =========================================================================
    # VM Operations
    # =========================================================================

    def destroy_vm(self, vm_name: str, force: bool = False) -> Dict:
        """
        Force stop a running VM.

        Args:
            vm_name: Name of the VM
            force: If True, don't raise on failure

        Returns:
            Dict with status
        """
        self.logger.info(f"Destroying VM: {vm_name}")

        result = subprocess.run(
            ['virsh', 'destroy', vm_name],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            if force:
                self.logger.warning(f"Failed to destroy VM {vm_name}: {result.stderr}")
                return {'status': 'failed', 'error': result.stderr}
            # VM might not be running - that's okay
            if 'domain is not running' in result.stderr.lower():
                return {'status': 'not_running'}
            raise RuntimeError(f"Failed to destroy VM: {result.stderr}")

        return {'status': 'destroyed'}

    def undefine_vm(self, vm_name: str, force: bool = False) -> Dict:
        """
        Remove a VM definition from libvirt.

        Args:
            vm_name: Name of the VM
            force: If True, don't raise on failure

        Returns:
            Dict with status
        """
        self.logger.info(f"Undefining VM: {vm_name}")

        result = subprocess.run(
            ['virsh', 'undefine', vm_name],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            if force:
                self.logger.warning(f"Failed to undefine VM {vm_name}: {result.stderr}")
                return {'status': 'failed', 'error': result.stderr}
            raise RuntimeError(f"Failed to undefine VM: {result.stderr}")

        return {'status': 'undefined'}

    def vm_exists(self, vm_name: str) -> bool:
        """Check if a VM is defined in libvirt."""
        try:
            result = subprocess.run(
                ['virsh', 'dominfo', vm_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_vm_state(self, vm_name: str) -> str:
        """Get the current state of a VM."""
        try:
            result = subprocess.run(
                ['virsh', 'domstate', vm_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return 'unknown'
            return result.stdout.strip().lower()
        except Exception:
            return 'unknown'

    # =========================================================================
    # Disk Image Operations
    # =========================================================================

    def delete_vm_disk(self, vm_name: str, force: bool = False) -> Dict:
        """
        Delete the disk image for a VM.

        Args:
            vm_name: Name of the VM (disk path derived from name)
            force: If True, don't raise on failure

        Returns:
            Dict with status
        """
        disk_path = f'{LIBVIRT_IMAGES_PATH}/veos/{vm_name}.qcow2'

        self.logger.info(f"Deleting disk image: {disk_path}")

        if not os.path.exists(disk_path):
            self.logger.warning(f"Disk image not found: {disk_path}")
            return {'status': 'not_found', 'path': disk_path}

        try:
            os.remove(disk_path)
            return {'status': 'deleted', 'path': disk_path}
        except Exception as e:
            if force:
                self.logger.warning(f"Failed to delete disk {disk_path}: {e}")
                return {'status': 'failed', 'error': str(e)}
            raise RuntimeError(f"Failed to delete disk image: {e}")

    # =========================================================================
    # Bridge Operations
    # =========================================================================

    def cleanup_node_bridges(self, node_name: str, node_info: Dict) -> List[str]:
        """
        Delete all OVS bridges associated with a node.

        Args:
            node_name: Name of the node
            node_info: Node info dict containing neighbors

        Returns:
            List of bridge names that were deleted
        """
        self.logger.info(f"Cleaning up bridges for node: {node_name}")

        deleted_bridges = []
        neighbors = node_info.get('neighbors', [])

        for neighbor in neighbors:
            local_port = neighbor.get('port', '')
            target_device = neighbor.get('neighborDevice', '')
            target_port = neighbor.get('neighborPort', '')

            if local_port and target_device and target_port:
                bridge_name = generate_bridge_name(
                    node_name, local_port,
                    target_device, target_port
                )

                try:
                    result = delete_ovs_bridge(bridge_name)
                    if result.get('status') == 'deleted':
                        deleted_bridges.append(bridge_name)
                        self.logger.info(f"Deleted OVS bridge: {bridge_name}")
                except Exception as e:
                    self.logger.warning(f"Failed to delete bridge {bridge_name}: {e}")

        return deleted_bridges

    # =========================================================================
    # Interface Operations
    # =========================================================================

    def detach_all_node_interfaces(self, node_name: str, node_info: Dict) -> List[Dict]:
        """
        Detach all interfaces that this node has attached to other VMs.

        Args:
            node_name: Name of the node being removed
            node_info: Node info dict containing neighbors

        Returns:
            List of dicts describing detached interfaces
        """
        self.logger.info(f"Detaching interfaces from target VMs for: {node_name}")

        detached = []
        neighbors = node_info.get('neighbors', [])

        for neighbor in neighbors:
            target_device = neighbor.get('neighborDevice', '')
            target_port = neighbor.get('neighborPort', '')
            local_port = neighbor.get('port', '')

            if target_device and target_port and local_port:
                bridge_name = generate_bridge_name(
                    node_name, local_port,
                    target_device, target_port
                )

                try:
                    # Find the MAC address of the interface on target VM using this bridge
                    interfaces = get_vm_interfaces(target_device)
                    mac_to_detach = None
                    for intf in interfaces:
                        if intf.get('source') == bridge_name:
                            mac_to_detach = intf.get('mac')
                            break

                    if mac_to_detach:
                        result = detach_interface_from_vm(target_device, mac_to_detach)
                        detached.append({
                            'target_device': target_device,
                            'bridge': bridge_name,
                            'mac': mac_to_detach,
                            'status': result.get('status', 'unknown')
                        })
                        self.logger.info(
                            f"Detached interface from {target_device} "
                            f"(bridge: {bridge_name}, mac: {mac_to_detach})"
                        )
                    else:
                        self.logger.warning(
                            f"No interface found on {target_device} for bridge {bridge_name}"
                        )
                        detached.append({
                            'target_device': target_device,
                            'bridge': bridge_name,
                            'status': 'not_found'
                        })
                except Exception as e:
                    self.logger.warning(
                        f"Failed to detach from {target_device}: {e}"
                    )
                    detached.append({
                        'target_device': target_device,
                        'bridge': bridge_name,
                        'status': 'failed',
                        'error': str(e)
                    })

        return detached

    # =========================================================================
    # Composite Operations
    # =========================================================================

    def delete_node_completely(self, vm_name: str, node_info: Dict) -> Dict:
        """
        Fully delete a node and all its associated resources.

        Order of operations:
        1. Stop the VM if running
        2. Detach interfaces from target VMs
        3. Delete OVS bridges
        4. Undefine the VM
        5. Delete the disk image

        Args:
            vm_name: Name of the VM/node
            node_info: Node info dict containing neighbors and metadata

        Returns:
            Dict with detailed cleanup results
        """
        self.logger.info(f"Completely deleting node: {vm_name}")

        results = {
            'name': vm_name,
            'steps': [],
            'errors': []
        }

        # Step 1: Stop the VM
        try:
            destroy_result = self.destroy_vm(vm_name, force=True)
            results['steps'].append({
                'step': 'destroy_vm',
                'status': destroy_result.get('status')
            })
        except Exception as e:
            results['errors'].append({
                'step': 'destroy_vm',
                'error': str(e)
            })

        # Step 2: Detach interfaces from target VMs
        try:
            detached = self.detach_all_node_interfaces(vm_name, node_info)
            results['steps'].append({
                'step': 'detach_interfaces',
                'detached': detached
            })
        except Exception as e:
            results['errors'].append({
                'step': 'detach_interfaces',
                'error': str(e)
            })

        # Step 3: Delete OVS bridges
        try:
            deleted_bridges = self.cleanup_node_bridges(vm_name, node_info)
            results['steps'].append({
                'step': 'delete_bridges',
                'deleted': deleted_bridges
            })
        except Exception as e:
            results['errors'].append({
                'step': 'delete_bridges',
                'error': str(e)
            })

        # Step 4: Undefine the VM
        try:
            undefine_result = self.undefine_vm(vm_name, force=True)
            results['steps'].append({
                'step': 'undefine_vm',
                'status': undefine_result.get('status')
            })
        except Exception as e:
            results['errors'].append({
                'step': 'undefine_vm',
                'error': str(e)
            })

        # Step 5: Delete disk image
        try:
            delete_result = self.delete_vm_disk(vm_name, force=True)
            results['steps'].append({
                'step': 'delete_disk',
                'status': delete_result.get('status'),
                'path': delete_result.get('path')
            })
        except Exception as e:
            results['errors'].append({
                'step': 'delete_disk',
                'error': str(e)
            })

        # Overall status
        results['status'] = 'completed' if not results['errors'] else 'completed_with_errors'

        self.logger.info(
            f"Node deletion complete: {vm_name} "
            f"(errors: {len(results['errors'])})"
        )

        return results


    def reset_all_user_nodes(self) -> Dict:
        """
        Fully reset all user-added nodes, hosts, and firewalls.

        This removes all user-added devices and restores the topology to its
        original state. Operations performed:
        1. Delete all user-added vEOS nodes
        2. Delete all user-added Linux hosts
        3. Delete all user-added VyOS firewalls
        4. Clean up any orphaned OVS bridges
        5. Clear persistence files

        Returns:
            Dict with detailed reset results
        """
        from persistence import (
            load_user_nodes, load_user_hosts, load_user_firewalls,
            atomic_write_yaml
        )
        from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH
        from host_manager import delete_host
        from firewall_manager import delete_firewall

        self.logger.info("Starting full reset of all user-added nodes")

        results = {
            'nodes_deleted': [],
            'hosts_deleted': [],
            'firewalls_deleted': [],
            'bridges_cleaned': [],
            'errors': [],
            'affected_devices': set()
        }

        # Phase 1: Delete all user-added vEOS nodes
        self.logger.info("Phase 1: Deleting user-added vEOS nodes")
        user_data = load_user_nodes(USER_NODES_PATH)
        nodes = user_data.get('nodes', [])

        for node_entry in nodes:
            for node_name, node_info in node_entry.items():
                try:
                    # Track affected target devices for reboot
                    for neighbor in node_info.get('neighbors', []):
                        target = neighbor.get('neighborDevice', '')
                        if target:
                            results['affected_devices'].add(target)

                    # Delete the node
                    delete_result = self.delete_node_completely(node_name, node_info)
                    results['nodes_deleted'].append({
                        'name': node_name,
                        'status': delete_result.get('status', 'unknown')
                    })
                    self.logger.info(f"Deleted user node: {node_name}")
                except Exception as e:
                    self.logger.error(f"Failed to delete node {node_name}: {e}")
                    results['errors'].append({
                        'type': 'node',
                        'name': node_name,
                        'error': str(e)
                    })

        # Phase 2: Delete all user-added Linux hosts
        self.logger.info("Phase 2: Deleting user-added Linux hosts")
        hosts_data = load_user_hosts(USER_HOSTS_PATH)
        hosts = hosts_data.get('hosts') or []

        for host_entry in hosts:
            for host_name, host_info in host_entry.items():
                try:
                    # Track affected target devices
                    connection = host_info.get('connection')
                    if connection and connection.get('target_device'):
                        results['affected_devices'].add(connection['target_device'])

                    # Delete the host
                    delete_result = delete_host(host_name)
                    results['hosts_deleted'].append({
                        'name': host_name,
                        'status': delete_result.get('status', 'unknown')
                    })
                    self.logger.info(f"Deleted user host: {host_name}")
                except Exception as e:
                    self.logger.error(f"Failed to delete host {host_name}: {e}")
                    results['errors'].append({
                        'type': 'host',
                        'name': host_name,
                        'error': str(e)
                    })

        # Phase 3: Delete all user-added VyOS firewalls
        self.logger.info("Phase 3: Deleting user-added VyOS firewalls")
        firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
        firewalls = firewalls_data.get('firewalls') or []

        for fw_entry in firewalls:
            for fw_name, fw_info in fw_entry.items():
                try:
                    # Track affected target devices
                    for iface_key in ['inside_interface', 'outside_interface']:
                        iface = fw_info.get(iface_key, {})
                        target = iface.get('target_device', '')
                        if target:
                            results['affected_devices'].add(target)

                    # Delete the firewall
                    delete_result = delete_firewall(fw_name)
                    results['firewalls_deleted'].append({
                        'name': fw_name,
                        'status': delete_result.get('status', 'unknown')
                    })
                    self.logger.info(f"Deleted user firewall: {fw_name}")
                except Exception as e:
                    self.logger.error(f"Failed to delete firewall {fw_name}: {e}")
                    results['errors'].append({
                        'type': 'firewall',
                        'name': fw_name,
                        'error': str(e)
                    })

        # Phase 4: Clean up any orphaned OVS bridges (user-created)
        # Uses enhanced cleanup with port-count detection
        self.logger.info("Phase 4: Cleaning up orphaned OVS bridges")
        try:
            cleanup_result = self.cleanup_all_orphaned_bridges()
            results['bridges_cleaned'] = cleanup_result.get('deleted', [])
        except Exception as e:
            self.logger.error(f"Failed to cleanup orphaned bridges: {e}")
            results['errors'].append({
                'type': 'bridges',
                'error': str(e)
            })

        # Phase 5: Clear persistence files
        self.logger.info("Phase 5: Clearing persistence files")
        try:
            # Reset user_nodes.yaml
            empty_nodes = {
                'version': 1,
                'nodes': []
            }
            atomic_write_yaml(empty_nodes, USER_NODES_PATH)
            self.logger.info(f"Cleared {USER_NODES_PATH}")

            # Reset user_hosts.yaml
            empty_hosts = {
                'version': 1,
                'hosts': []
            }
            atomic_write_yaml(empty_hosts, USER_HOSTS_PATH)
            self.logger.info(f"Cleared {USER_HOSTS_PATH}")

            # Reset user_firewalls.yaml
            empty_firewalls = {
                'version': 1,
                'firewalls': []
            }
            atomic_write_yaml(empty_firewalls, USER_FIREWALLS_PATH)
            self.logger.info(f"Cleared {USER_FIREWALLS_PATH}")
        except Exception as e:
            self.logger.error(f"Failed to clear persistence files: {e}")
            results['errors'].append({
                'type': 'persistence',
                'error': str(e)
            })

        # Convert set to list for JSON serialization
        results['affected_devices'] = list(results['affected_devices'])

        # Summary
        total_deleted = (
            len(results['nodes_deleted']) +
            len(results['hosts_deleted']) +
            len(results['firewalls_deleted'])
        )

        results['status'] = 'completed' if not results['errors'] else 'completed_with_errors'
        results['summary'] = {
            'nodes': len(results['nodes_deleted']),
            'hosts': len(results['hosts_deleted']),
            'firewalls': len(results['firewalls_deleted']),
            'bridges': len(results['bridges_cleaned']),
            'errors': len(results['errors']),
            'affected_devices': len(results['affected_devices'])
        }

        self.logger.info(
            f"Reset complete: {total_deleted} devices deleted, "
            f"{len(results['bridges_cleaned'])} bridges cleaned, "
            f"{len(results['errors'])} errors"
        )

        return results

    def _cleanup_orphaned_bridges(self) -> List[str]:
        """
        Find and delete OVS bridges that were created for user nodes.

        User-created bridges follow specific naming patterns that can be
        identified and cleaned up.

        Returns:
            List of bridge names that were deleted
        """
        import subprocess

        cleaned = []

        try:
            # Get all OVS bridges
            result = subprocess.run(
                ['ovs-vsctl', 'list-br'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self.logger.warning(f"Failed to list OVS bridges: {result.stderr}")
                return cleaned

            bridges = result.stdout.strip().split('\n')

            # Known system bridges that should NOT be deleted
            system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}

            for bridge in bridges:
                bridge = bridge.strip()
                if not bridge:
                    continue

                # Skip system bridges
                if bridge in system_bridges:
                    continue

                # Skip bridges that match original topology patterns
                # Original bridges typically use full device names
                # User bridges use shortened names (e.g., sp1-le3)
                if self._is_user_created_bridge(bridge):
                    try:
                        delete_ovs_bridge(bridge)
                        cleaned.append(bridge)
                        self.logger.info(f"Deleted orphaned bridge: {bridge}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete bridge {bridge}: {e}")

        except Exception as e:
            self.logger.error(f"Error during bridge cleanup: {e}")

        return cleaned

    def _is_user_created_bridge(self, bridge_name: str) -> bool:
        """
        Determine if a bridge was created for user-added nodes.

        User bridges follow the pattern: {shortname}{port}-{shortname}{port}
        e.g., sp11-le13 (spine1:Ethernet1 to leaf1:Ethernet3)

        Original topology bridges use full names or different patterns.

        Args:
            bridge_name: Name of the OVS bridge

        Returns:
            True if this appears to be a user-created bridge
        """
        import re

        # User bridge pattern: 2-3 letter prefix + number + hyphen + 2-3 letter prefix + number
        # Examples: sp11-le13, le12-ho11, fw11-le15, fw1et1-sp112
        # Also match firewall patterns: fw1et1-sp112, fw1et2-bo110
        user_bridge_patterns = [
            r'^[a-z]{2,3}\d+-[a-z]{2,3}\d+$',  # Standard: sp11-le13
            r'^[a-z]{2,3}\d+et\d+-[a-z]{2,3}\d+$',  # With et: fw1et1-sp112
            r'^[a-z]{2,3}\d+-[a-z]{2,3}x\d+$',  # With x: sp27-bo14 -> sp2x7
            r'^[a-z]{2,3}x\d+-[a-z]{2,3}\d+$',  # Reversed x
        ]

        for pattern in user_bridge_patterns:
            if re.match(pattern, bridge_name):
                return True
        return False

    def _get_bridge_port_count(self, bridge_name: str) -> int:
        """
        Get the number of ports attached to a bridge.

        Args:
            bridge_name: Name of the OVS bridge

        Returns:
            Number of ports attached, or -1 on error
        """
        import subprocess

        try:
            result = subprocess.run(
                ['ovs-vsctl', 'list-ports', bridge_name],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return -1

            ports = [p.strip() for p in result.stdout.split('\n') if p.strip()]
            return len(ports)

        except Exception:
            return -1

    def cleanup_all_orphaned_bridges(self) -> Dict:
        """
        Comprehensive orphaned bridge cleanup.

        Finds and deletes bridges that are:
        1. User-created (matching naming patterns)
        2. Truly orphaned (0-1 ports attached, meaning one or both VMs deleted)

        Returns:
            Dict with cleanup results including found/deleted counts
        """
        import subprocess

        results = {
            'scanned': 0,
            'orphaned_found': [],
            'deleted': [],
            'failed': [],
            'skipped_system': 0,
            'skipped_healthy': 0
        }

        try:
            # Get all OVS bridges
            result = subprocess.run(
                ['ovs-vsctl', 'list-br'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self.logger.warning(f"Failed to list OVS bridges: {result.stderr}")
                return results

            bridges = result.stdout.strip().split('\n')

            # Known system bridges that should NOT be deleted
            system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}

            for bridge in bridges:
                bridge = bridge.strip()
                if not bridge:
                    continue

                results['scanned'] += 1

                # Skip system bridges
                if bridge in system_bridges:
                    results['skipped_system'] += 1
                    continue

                # Check if it matches user-created patterns
                if not self._is_user_created_bridge(bridge):
                    results['skipped_healthy'] += 1
                    continue

                # Check port count - healthy bridges should have 2 ports
                port_count = self._get_bridge_port_count(bridge)

                if port_count < 2:
                    # This bridge is orphaned (0-1 ports means one/both VMs deleted)
                    results['orphaned_found'].append({
                        'bridge': bridge,
                        'port_count': port_count
                    })

                    try:
                        delete_ovs_bridge(bridge)
                        results['deleted'].append(bridge)
                        self.logger.info(f"Deleted orphaned bridge: {bridge} (had {port_count} ports)")
                    except Exception as e:
                        results['failed'].append({
                            'bridge': bridge,
                            'error': str(e)
                        })
                        self.logger.warning(f"Failed to delete bridge {bridge}: {e}")
                else:
                    # Bridge has 2+ ports, appears healthy
                    results['skipped_healthy'] += 1

        except Exception as e:
            self.logger.error(f"Error during comprehensive bridge cleanup: {e}")
            results['error'] = str(e)

        self.logger.info(
            f"Bridge cleanup complete: scanned={results['scanned']}, "
            f"orphaned={len(results['orphaned_found'])}, deleted={len(results['deleted'])}"
        )

        return results

    # =========================================================================
    # Shared Cleanup Operations (for hosts and firewalls)
    # =========================================================================

    def cleanup_connection(
        self,
        connection: Optional[Dict],
        connection_name: str = ''
    ) -> Dict:
        """
        Clean up a single connection: detach interface from target and delete bridge.

        This is a shared method used by both host and firewall deletion to avoid
        code duplication.

        Args:
            connection: Connection dict with 'bridge' and 'target_device' keys
            connection_name: Optional name for logging (e.g., 'inside', 'outside')

        Returns:
            Dict with cleanup status:
            - interface_detached: bool
            - bridge_deleted: bool
            - target_device: str or None (device that needs reboot if detached)
            - errors: list of error messages
        """
        result = {
            'interface_detached': False,
            'bridge_deleted': False,
            'target_device': None,
            'errors': []
        }

        if not connection:
            return result

        bridge_name = connection.get('bridge')
        target_device = connection.get('target_device')
        log_prefix = f"[{connection_name}] " if connection_name else ""

        # Detach interface from target device
        if target_device and bridge_name:
            try:
                interfaces = get_vm_interfaces(target_device)
                for intf in interfaces:
                    if intf.get('source') == bridge_name:
                        mac = intf.get('mac')
                        if mac:
                            self.logger.info(
                                f"{log_prefix}Detaching interface {mac} from {target_device}"
                            )
                            detach_interface_from_vm(target_device, mac)
                            result['interface_detached'] = True
                            # EOS VMs don't support hot-unplug, track for reboot
                            result['target_device'] = target_device
                        break
            except Exception as e:
                error_msg = f"Failed to detach {log_prefix}interface from {target_device}: {e}"
                self.logger.warning(error_msg)
                result['errors'].append(error_msg)

        # Delete OVS bridge
        if bridge_name:
            try:
                self.logger.info(f"{log_prefix}Deleting OVS bridge: {bridge_name}")
                delete_ovs_bridge(bridge_name)
                result['bridge_deleted'] = True
            except Exception as e:
                error_msg = f"Failed to delete {log_prefix}bridge {bridge_name}: {e}"
                self.logger.warning(error_msg)
                result['errors'].append(error_msg)

        return result

    def delete_vm_with_cleanup(
        self,
        vm_name: str,
        disk_subdir: str,
        has_cidata: bool = True
    ) -> Dict:
        """
        Delete a VM and its associated disk images.

        Common deletion logic for hosts and firewalls.

        Args:
            vm_name: Name of the VM to delete
            disk_subdir: Subdirectory under LIBVIRT_IMAGES_PATH (e.g., 'hosts', 'firewall')
            has_cidata: Whether to also delete cloud-init ISO

        Returns:
            Dict with deletion status:
            - vm_destroyed: bool
            - vm_undefined: bool
            - disk_deleted: bool
            - cidata_deleted: bool (if has_cidata=True)
            - errors: list of error messages
        """
        import subprocess

        result = {
            'vm_destroyed': False,
            'vm_undefined': False,
            'disk_deleted': False,
            'errors': []
        }
        if has_cidata:
            result['cidata_deleted'] = False

        # Step 1: Destroy running VM (force - don't fail if not running)
        try:
            proc = subprocess.run(
                ['virsh', 'destroy', vm_name],
                capture_output=True,
                timeout=30
            )
            result['vm_destroyed'] = proc.returncode == 0
        except Exception as e:
            self.logger.warning(f"Failed to destroy VM {vm_name}: {e}")

        # Step 2: Undefine VM
        try:
            proc = subprocess.run(
                ['virsh', 'undefine', vm_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            result['vm_undefined'] = proc.returncode == 0
            if proc.returncode != 0:
                result['errors'].append(f"Undefine failed: {proc.stderr}")
        except Exception as e:
            error_msg = f"Failed to undefine VM {vm_name}: {e}"
            self.logger.warning(error_msg)
            result['errors'].append(error_msg)

        # Step 3: Delete disk image
        disk_path = f'{LIBVIRT_IMAGES_PATH}/{disk_subdir}/{vm_name}.qcow2'
        if os.path.exists(disk_path):
            try:
                os.remove(disk_path)
                result['disk_deleted'] = True
                self.logger.info(f"Deleted disk: {disk_path}")
            except Exception as e:
                error_msg = f"Failed to delete disk {disk_path}: {e}"
                self.logger.warning(error_msg)
                result['errors'].append(error_msg)
        else:
            self.logger.info(f"Disk not found (already deleted?): {disk_path}")

        # Step 4: Delete cloud-init ISO (if applicable)
        if has_cidata:
            cidata_path = f'{LIBVIRT_IMAGES_PATH}/{disk_subdir}/{vm_name}-cidata.iso'
            if os.path.exists(cidata_path):
                try:
                    os.remove(cidata_path)
                    result['cidata_deleted'] = True
                    self.logger.info(f"Deleted cloud-init ISO: {cidata_path}")
                except Exception as e:
                    error_msg = f"Failed to delete cidata {cidata_path}: {e}"
                    self.logger.warning(error_msg)
                    result['errors'].append(error_msg)

        return result


# Module-level singleton instance
_resource_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Get the singleton ResourceManager instance."""
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager
