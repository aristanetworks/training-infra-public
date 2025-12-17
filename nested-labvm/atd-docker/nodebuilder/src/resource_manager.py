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


# Module-level singleton instance
_resource_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Get the singleton ResourceManager instance."""
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager
