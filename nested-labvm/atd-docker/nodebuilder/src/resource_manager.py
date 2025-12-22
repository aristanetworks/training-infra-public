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
        self.logger.info("Phase 4: Cleaning up orphaned OVS bridges")
        try:
            orphaned = self._cleanup_orphaned_bridges()
            results['bridges_cleaned'] = orphaned
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
            system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext'}

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
        # Examples: sp11-le13, le12-ho11, fw11-le15
        user_bridge_pattern = r'^[a-z]{2,3}\d+-[a-z]{2,3}\d+$'

        return bool(re.match(user_bridge_pattern, bridge_name))


# Module-level singleton instance
_resource_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Get the singleton ResourceManager instance."""
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager
