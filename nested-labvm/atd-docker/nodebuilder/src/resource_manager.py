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

from ruamel.yaml import YAML

from interface_manager import (
    delete_ovs_bridge,
    detach_interface_from_vm,
    generate_bridge_name,
    get_vm_interfaces
)
from config import (
    LIBVIRT_IMAGES_PATH,
    SUBPROCESS_TIMEOUT_SHORT,
    SUBPROCESS_TIMEOUT_DEFAULT,
    SUBPROCESS_TIMEOUT_LONG
)

logger = logging.getLogger('nodebuilder')


class ResourceTransaction:
    """
    Context manager for atomic resource creation with automatic rollback on failure.

    Tracks created resources and cleans them up if an exception occurs.
    Provides detailed rollback tracking with success/failure reporting.

    Supported resource types:
    - 'vm': Virtual machine (virsh destroy + undefine)
    - 'image': Disk image file (os.remove)
    - 'cidata': Cloud-init ISO file (os.remove)
    - 'bridge': OVS bridge (delete_ovs_bridge)
    - 'xml': XML definition file (os.remove)

    Usage:
        with ResourceTransaction('my-device') as txn:
            txn.add_resource('image', '/path/to/disk.qcow2')
            # ... create resources ...
            txn.add_resource('vm', 'my-device')
            # If exception occurs, all resources are rolled back
    """

    def __init__(self, device_name: str, device_type: str = 'device'):
        """
        Initialize a new resource transaction.

        Args:
            device_name: Name of the device being created (for logging)
            device_type: Type of device ('node', 'host', 'firewall', 'velo')
        """
        self.device_name = device_name
        self.device_type = device_type
        self.created_resources: List[tuple] = []
        self.logger = logging.getLogger('nodebuilder')
        self.rollback_results: Dict = {
            'success': [],
            'failed': []
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.logger.error(
                f"Error creating {self.device_type} '{self.device_name}': {exc_val}"
            )
            self.rollback()
        return False  # Re-raise exception

    def add_resource(self, resource_type: str, resource_id: str):
        """
        Track a created resource for potential rollback.

        Args:
            resource_type: Type of resource ('vm', 'image', 'cidata', 'bridge', 'xml')
            resource_id: Identifier (VM name, file path, or bridge name)
        """
        self.created_resources.append((resource_type, resource_id))
        self.logger.debug(
            f"[{self.device_name}] Tracked resource: {resource_type}={resource_id}"
        )

    def rollback(self) -> Dict:
        """
        Clean up all created resources in reverse order.

        Returns:
            Dict with 'success' and 'failed' lists of rolled back resources
        """
        self.logger.info(
            f"Rolling back creation of {self.device_type} '{self.device_name}' "
            f"({len(self.created_resources)} resources to clean up)"
        )

        self.rollback_results = {'success': [], 'failed': []}

        for resource_type, resource_id in reversed(self.created_resources):
            try:
                self.logger.info(f"Rolling back {resource_type}: {resource_id}")
                self._cleanup_resource(resource_type, resource_id)
                self.rollback_results['success'].append(
                    f"{resource_type}:{resource_id}"
                )
            except Exception as e:
                self.rollback_results['failed'].append({
                    'resource_type': resource_type,
                    'resource_id': resource_id,
                    'error': str(e)
                })
                self.logger.warning(
                    f"Rollback failed for {resource_type}:{resource_id}: {e}"
                )

        # Log rollback summary
        if self.rollback_results['failed']:
            failed = self.rollback_results['failed']
            self.logger.error(
                f"Rollback incomplete for {self.device_name}: "
                f"{len(failed)} failure(s), "
                f"{len(self.rollback_results['success'])} success(es). "
                f"Failed resources may need manual cleanup: "
                f"{[f['resource_type']+'='+f['resource_id'] for f in failed]}"
            )
        else:
            self.logger.info(
                f"Rollback complete for {self.device_name}: "
                f"{len(self.rollback_results['success'])} resource(s) cleaned up"
            )

        return self.rollback_results

    def _cleanup_resource(self, resource_type: str, resource_id: str):
        """
        Clean up a single resource based on its type.

        Args:
            resource_type: Type of resource
            resource_id: Identifier for the resource

        Raises:
            Exception: If cleanup fails
        """
        if resource_type == 'vm':
            # Destroy running VM (ignore errors - may not be running)
            subprocess.run(
                ['virsh', 'destroy', resource_id],
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            # Undefine VM
            subprocess.run(
                ['virsh', 'undefine', resource_id],
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )

        elif resource_type in ('image', 'cidata', 'xml'):
            if os.path.exists(resource_id):
                os.remove(resource_id)

        elif resource_type == 'bridge':
            # Note: We clean up bridges but NOT orphaned interfaces
            # Orphaned interfaces are preserved for interface slot ordering
            delete_ovs_bridge(resource_id)

        else:
            self.logger.warning(
                f"Unknown resource type '{resource_type}' for {resource_id}"
            )


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
        vm_name = self.resolve_domain_name(vm_name)
        self.logger.info(f"Destroying VM: {vm_name}")

        result = subprocess.run(
            ['virsh', 'destroy', vm_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
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
        vm_name = self.resolve_domain_name(vm_name)
        self.logger.info(f"Undefining VM: {vm_name}")

        result = subprocess.run(
            ['virsh', 'undefine', vm_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )

        if result.returncode != 0:
            if force:
                self.logger.warning(f"Failed to undefine VM {vm_name}: {result.stderr}")
                return {'status': 'failed', 'error': result.stderr}
            raise RuntimeError(f"Failed to undefine VM: {result.stderr}")

        return {'status': 'undefined'}

    def resolve_domain_name(self, vm_name: str) -> str:
        """Resolve the actual libvirt domain name for a VM.

        Tries the name as-is first, then lowercase as fallback.
        This handles topologies where VMs use original case from
        topo_build.yml (e.g., L4: P4, PE1) as well as topologies
        where VMs are lowercase (e.g., spine1, leaf1).

        Args:
            vm_name: Name of the VM to look up

        Returns:
            The actual domain name found, or vm_name as-is if neither matches
        """
        for name in dict.fromkeys([vm_name, vm_name.lower()]):
            try:
                result = subprocess.run(
                    ['virsh', 'dominfo', name],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )
                if result.returncode == 0:
                    return name
            except Exception:
                continue
        return vm_name

    def vm_exists(self, vm_name: str) -> bool:
        """Check if a VM is defined in libvirt.
        Tries original name first, then lowercase (virsh is case-sensitive
        and domain names may be uppercase on some topologies like L4)."""
        for name in dict.fromkeys([vm_name, vm_name.lower()]):
            try:
                result = subprocess.run(
                    ['virsh', 'dominfo', name],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )
                if result.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def get_vm_state(self, vm_name: str) -> str:
        """Get the current state of a VM.
        Resolves the actual domain name first (may be uppercase or lowercase)."""
        resolved = self.resolve_domain_name(vm_name)
        try:
            result = subprocess.run(
                ['virsh', 'domstate', resolved],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
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

    def cleanup_node_bridges(
        self, node_name: str, node_info: Dict, skip_bridges: Optional[set] = None
    ) -> List[str]:
        """
        Delete all OVS bridges associated with a node.

        Args:
            node_name: Name of the node
            node_info: Node info dict containing neighbors
            skip_bridges: Optional set of bridge names to skip (preserved for orphaned slots)

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

                if skip_bridges and bridge_name in skip_bridges:
                    self.logger.info(
                        f"Keeping bridge {bridge_name} (preserved for orphaned slot)"
                    )
                    continue

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
        Handle all interfaces that this node has attached to other VMs.

        With slot preservation enabled, this records orphaned slots instead of
        detaching interfaces. This allows new devices to reuse the same interface
        slots without requiring a reboot.

        Args:
            node_name: Name of the node being removed
            node_info: Node info dict containing neighbors

        Returns:
            List of dicts describing processed interfaces, each with:
            - target_device: str - The device the interface was on
            - target_port: str - The port name (e.g., 'Ethernet5')
            - bridge: str - The OVS bridge name
            - mac: str - The MAC address of the interface
            - status: str - One of:
                - 'slot_preserved': Slot recorded for reuse (no reboot needed)
                - 'detached': Interface detached (slot preservation disabled)
                - 'detached_fallback': Interface detached due to port extraction
                  or recording failure (reboot may be needed)
                - 'not_found': No interface found on target device
                - 'failed': Processing error occurred
            - interface_detached: bool - True if interface was detached
            - reason: str - (optional) Reason for fallback if applicable
        """
        from interface_manager import extract_port_number

        self.logger.info(f"Processing interfaces for deletion of: {node_name}")

        processed = []
        neighbors = node_info.get('neighbors', [])

        # Check if slot preservation is enabled and import orphan manager
        try:
            from config import ENABLE_SLOT_PRESERVATION
            slot_preservation_enabled = ENABLE_SLOT_PRESERVATION
        except ImportError:
            slot_preservation_enabled = False

        # Import orphan recording function once if needed
        record_orphaned_slot = None
        if slot_preservation_enabled:
            try:
                from orphaned_interfaces import record_orphaned_slot
            except ImportError:
                self.logger.warning(
                    "Could not import orphaned_interfaces module, "
                    "falling back to detach mode"
                )
                slot_preservation_enabled = False

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
                    mac_found = None
                    for intf in interfaces:
                        if intf.get('source') == bridge_name:
                            mac_found = intf.get('mac')
                            break

                    if mac_found:
                        status = None
                        interface_detached = False
                        reason = None

                        if slot_preservation_enabled:
                            # Try to record orphaned slot instead of detaching
                            port_num = extract_port_number(target_port)
                            if port_num:
                                try:
                                    self.logger.info(
                                        f"Recording orphaned slot: {target_device}:{target_port} "
                                        f"(MAC {mac_found}, bridge {bridge_name})"
                                    )
                                    record_orphaned_slot(
                                        target_device=target_device,
                                        slot_number=port_num,
                                        mac_address=mac_found,
                                        old_bridge=bridge_name,
                                        original_connection=neighbor
                                    )
                                    status = 'slot_preserved'
                                except Exception as e:
                                    # Recording failed - fall back to detach
                                    self.logger.warning(
                                        f"Failed to record orphaned slot for "
                                        f"{target_device}:{target_port}: {e}. "
                                        f"Falling back to detach."
                                    )
                                    detach_interface_from_vm(target_device, mac_found)
                                    status = 'detached_fallback'
                                    interface_detached = True
                                    reason = 'recording_failed'
                            else:
                                # Port number extraction failed - fall back to detach
                                self.logger.warning(
                                    f"Could not extract port number from {target_port} "
                                    f"on {target_device}. Slot preservation requires "
                                    f"numeric port identifiers. Falling back to detach "
                                    f"(interface may renumber on reboot)."
                                )
                                detach_interface_from_vm(target_device, mac_found)
                                status = 'detached_fallback'
                                interface_detached = True
                                reason = 'port_extraction_failed'
                        else:
                            # Slot preservation disabled - detach interface
                            result = detach_interface_from_vm(target_device, mac_found)
                            status = result.get('status', 'detached')
                            interface_detached = True
                            self.logger.info(
                                f"Detached interface from {target_device} "
                                f"(bridge: {bridge_name}, mac: {mac_found})"
                            )

                        entry = {
                            'target_device': target_device,
                            'target_port': target_port,
                            'bridge': bridge_name,
                            'mac': mac_found,
                            'status': status,
                            'interface_detached': interface_detached
                        }
                        if reason:
                            entry['reason'] = reason
                        processed.append(entry)
                    else:
                        # No interface attached - nothing to preserve or detach
                        self.logger.info(
                            f"No interface found on {target_device} for bridge {bridge_name}"
                        )
                        processed.append({
                            'target_device': target_device,
                            'bridge': bridge_name,
                            'status': 'not_found',
                            'interface_detached': False
                        })
                except Exception as e:
                    self.logger.warning(
                        f"Failed to process interface for {target_device}: {e}"
                    )
                    processed.append({
                        'target_device': target_device,
                        'bridge': bridge_name,
                        'status': 'failed',
                        'interface_detached': False,
                        'error': str(e)
                    })

        return processed

    # =========================================================================
    # Composite Operations
    # =========================================================================

    def delete_node_completely(self, vm_name: str, node_info: Dict) -> Dict:
        """
        Fully delete a node and all its associated resources.

        Order of operations:
        1. Stop the VM if running
        2. Handle interfaces on target VMs (preserve slots or detach)
        3. Delete OVS bridges
        4. Undefine the VM
        5. Delete the disk image

        With slot preservation enabled, step 2 records orphaned slots instead of
        detaching interfaces, allowing new devices to reuse the same interface
        slots without requiring a reboot.

        Args:
            vm_name: Name of the VM/node
            node_info: Node info dict containing neighbors and metadata

        Returns:
            Dict with detailed cleanup results including 'slots_preserved' count
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
        detached = []
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

        # Collect bridges that were preserved for orphaned slots -- don't delete them
        preserved_bridges = {
            item.get('bridge') for item in detached
            if item.get('status') == 'slot_preserved' and item.get('bridge')
        }

        # Step 3: Delete OVS bridges (skip preserved ones)
        try:
            deleted_bridges = self.cleanup_node_bridges(
                vm_name, node_info, skip_bridges=preserved_bridges or None
            )
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

        # Count slot preservation metrics (from step 2)
        slots_preserved = 0
        slots_detached_fallback = 0
        interfaces_detached = 0
        for step in results['steps']:
            if step.get('step') == 'detach_interfaces':
                for item in step.get('detached', []):
                    status = item.get('status')
                    if status == 'slot_preserved':
                        slots_preserved += 1
                    elif status == 'detached_fallback':
                        slots_detached_fallback += 1
                        interfaces_detached += 1
                    elif item.get('interface_detached'):
                        interfaces_detached += 1

        results['slots_preserved'] = slots_preserved
        results['slots_detached_fallback'] = slots_detached_fallback
        results['interfaces_detached'] = interfaces_detached

        self.logger.info(
            f"Node deletion complete: {vm_name} "
            f"(errors: {len(results['errors'])}, slots_preserved: {slots_preserved}, "
            f"fallbacks: {slots_detached_fallback})"
        )

        return results


    def reset_all_user_nodes(self) -> Dict:
        """
        Fully reset all user-added nodes, hosts, firewalls, VeloCloud, CloudEOS, and links.

        This removes all user-added devices and restores the topology to its
        original state. Operations performed:
        1. Delete all user-added vEOS nodes
        2. Delete all user-added Linux hosts
        3. Delete all user-added VyOS firewalls
        4. Delete all user-added VeloCloud devices
        5. Delete all user-added CloudEOS devices
        6. Remove all user-added links (clear user_links.yaml and OVS bridges)
        7. Clean up any orphaned OVS bridges
        8. Clear persistence files
        9. Clear orphaned interface slots

        Returns:
            Dict with detailed reset results
        """
        from persistence import (
            load_user_nodes, load_user_hosts, load_user_firewalls,
            remove_user_node, remove_user_host, remove_user_firewall
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

        # Track successfully deleted entries for selective persistence removal
        successfully_deleted_nodes = []
        successfully_deleted_hosts = []
        successfully_deleted_firewalls = []

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
                    delete_status = delete_result.get('status', 'unknown')
                    results['nodes_deleted'].append({
                        'name': node_name,
                        'status': delete_status
                    })
                    # Only track as successfully deleted if status indicates success
                    if delete_status in ('deleted', 'success', 'completed'):
                        successfully_deleted_nodes.append(node_name)
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
                    delete_status = delete_result.get('status', 'unknown')
                    results['hosts_deleted'].append({
                        'name': host_name,
                        'status': delete_status
                    })
                    # Only track as successfully deleted if status indicates success
                    if delete_status in ('deleted', 'success', 'completed'):
                        successfully_deleted_hosts.append(host_name)
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
                    delete_status = delete_result.get('status', 'unknown')
                    results['firewalls_deleted'].append({
                        'name': fw_name,
                        'status': delete_status
                    })
                    # Only track as successfully deleted if status indicates success
                    if delete_status in ('deleted', 'success', 'completed'):
                        successfully_deleted_firewalls.append(fw_name)
                    self.logger.info(f"Deleted user firewall: {fw_name}")
                except Exception as e:
                    self.logger.error(f"Failed to delete firewall {fw_name}: {e}")
                    results['errors'].append({
                        'type': 'firewall',
                        'name': fw_name,
                        'error': str(e)
                    })

        # Phase 4: Delete all user-added VeloCloud devices
        self.logger.info("Phase 4: Deleting user-added VeloCloud devices")
        from persistence import list_user_velo_devices, remove_user_velo_device
        from config import USER_VELO_PATH
        from velo_manager import delete_velo_device

        successfully_deleted_velo = []
        results['velo_deleted'] = []

        try:
            velo_devices = list_user_velo_devices(USER_VELO_PATH)
            for velo_entry in velo_devices:
                if isinstance(velo_entry, dict):
                    for velo_name, velo_info in velo_entry.items():
                        try:
                            # Track affected target devices
                            for conn in velo_info.get('connections', []):
                                target = conn.get('target_device', '')
                                if target:
                                    results['affected_devices'].add(target)

                            # Delete the VeloCloud device
                            delete_result = delete_velo_device(velo_name)
                            delete_status = delete_result.get('status', 'unknown')
                            results['velo_deleted'].append({
                                'name': velo_name,
                                'status': delete_status
                            })
                            if delete_status in ('deleted', 'success', 'completed'):
                                successfully_deleted_velo.append(velo_name)
                            self.logger.info(f"Deleted user VeloCloud device: {velo_name}")
                        except Exception as e:
                            self.logger.error(f"Failed to delete VeloCloud device {velo_name}: {e}")
                            results['errors'].append({
                                'type': 'velocloud',
                                'name': velo_name,
                                'error': str(e)
                            })
        except Exception as e:
            self.logger.error(f"Failed to load VeloCloud devices: {e}")
            results['errors'].append({
                'type': 'velocloud_load',
                'error': str(e)
            })

        # Phase 5: Delete all user-added CloudEOS devices
        self.logger.info("Phase 5: Deleting user-added CloudEOS devices")
        from cloudeos_manager import delete_cloudeos
        from persistence import list_user_cloudeos
        from config import USER_CLOUDEOS_PATH

        successfully_deleted_cloudeos = []
        results['cloudeos_deleted'] = []

        try:
            cloudeos_entries = list_user_cloudeos(USER_CLOUDEOS_PATH)
            for device_entry in cloudeos_entries:
                if isinstance(device_entry, dict):
                    for cloudeos_name, cloudeos_info in device_entry.items():
                        try:
                            # Track affected target devices
                            for conn in (cloudeos_info.get('neighbors') or []):
                                target = conn.get('neighborDevice', '')
                                if target:
                                    results['affected_devices'].add(target)

                            # Delete the CloudEOS device
                            delete_result = delete_cloudeos(name=cloudeos_name)
                            delete_status = delete_result.get('status', 'unknown')
                            results['cloudeos_deleted'].append({
                                'name': cloudeos_name,
                                'status': delete_status
                            })
                            if delete_status in ('deleted', 'success', 'completed'):
                                successfully_deleted_cloudeos.append(cloudeos_name)
                            self.logger.info(f"Deleted user CloudEOS device: {cloudeos_name}")
                        except Exception as e:
                            self.logger.error(f"Failed to delete CloudEOS device {cloudeos_name}: {e}")
                            results['errors'].append({
                                'type': 'cloudeos',
                                'name': cloudeos_name,
                                'error': str(e)
                            })
        except Exception as e:
            self.logger.error(f"Failed to load CloudEOS devices: {e}")
            results['errors'].append({
                'type': 'cloudeos_load',
                'error': str(e)
            })

        # Phase 6: Delete all user-added DMF devices
        self.logger.info("Phase 6: Deleting user-added DMF devices")
        from dmf_manager import delete_dmf_device
        from persistence import list_user_dmf_devices, remove_user_dmf_device
        from config import USER_DMF_PATH

        successfully_deleted_dmf = []
        results['dmf_deleted'] = []

        try:
            dmf_entries = list_user_dmf_devices(USER_DMF_PATH)
            for device_entry in dmf_entries:
                if isinstance(device_entry, dict):
                    for dmf_name, dmf_info in device_entry.items():
                        try:
                            for conn in (dmf_info.get('neighbors') or []):
                                target = conn.get('neighborDevice', '')
                                if target:
                                    results['affected_devices'].add(target)

                            delete_result = delete_dmf_device(name=dmf_name)
                            delete_status = delete_result.get('status', 'unknown')
                            results['dmf_deleted'].append({
                                'name': dmf_name,
                                'status': delete_status
                            })
                            if delete_status in ('deleted', 'success', 'completed'):
                                successfully_deleted_dmf.append(dmf_name)
                            self.logger.info(f"Deleted user DMF device: {dmf_name}")
                        except Exception as e:
                            self.logger.error(f"Failed to delete DMF device {dmf_name}: {e}")
                            results['errors'].append({
                                'type': 'dmf',
                                'name': dmf_name,
                                'error': str(e)
                            })
        except Exception as e:
            self.logger.error(f"Failed to load DMF devices: {e}")
            results['errors'].append({
                'type': 'dmf_load',
                'error': str(e)
            })

        # Phase 7: Clear user-added links (user_links.yaml) and delete their OVS bridges
        self.logger.info("Phase 7: Removing user-added links")
        from link_manager import remove_link
        from persistence import list_user_links, save_user_links, get_empty_user_links
        from config import USER_LINKS_PATH

        results['links_deleted'] = []

        try:
            user_links = list_user_links(USER_LINKS_PATH)
            for link in list(user_links):  # iterate a copy since we modify the file
                src = link.get('source_device', '')
                src_port = link.get('source_port', '')
                tgt = link.get('target_device', '')
                tgt_port = link.get('target_port', '')
                if not (src and tgt):
                    continue
                try:
                    link_result = remove_link(src, src_port, tgt, tgt_port, USER_LINKS_PATH)
                    link_status = link_result.get('status', 'unknown')
                    results['links_deleted'].append({
                        'source': src,
                        'target': tgt,
                        'status': link_status
                    })
                    self.logger.info(f"Removed user link: {src}:{src_port} <-> {tgt}:{tgt_port}")
                except Exception as e:
                    self.logger.error(f"Failed to remove link {src} <-> {tgt}: {e}")
                    results['errors'].append({
                        'type': 'link',
                        'source': src,
                        'target': tgt,
                        'error': str(e)
                    })
            # Ensure the file is cleared even if individual removals had issues
            save_user_links(get_empty_user_links(), USER_LINKS_PATH)
        except Exception as e:
            self.logger.error(f"Failed to process user links: {e}")
            results['errors'].append({
                'type': 'links_load',
                'error': str(e)
            })

        # Phase 8: Clean up any orphaned OVS bridges (user-created)
        # Uses enhanced cleanup with port-count detection
        self.logger.info("Phase 8: Cleaning up orphaned OVS bridges")
        try:
            cleanup_result = self.cleanup_all_orphaned_bridges()
            results['bridges_cleaned'] = cleanup_result.get('deleted', [])
        except Exception as e:
            self.logger.error(f"Failed to cleanup orphaned bridges: {e}")
            results['errors'].append({
                'type': 'bridges',
                'error': str(e)
            })

        # Phase 9: Remove successfully deleted entries from persistence
        # Only remove entries that were successfully deleted to prevent zombie VMs
        self.logger.info("Phase 9: Updating persistence files (removing successfully deleted entries)")
        try:
            # Remove successfully deleted nodes from user_nodes.yaml
            for node_name in successfully_deleted_nodes:
                try:
                    remove_user_node(node_name, USER_NODES_PATH)
                except Exception as e:
                    self.logger.warning(f"Failed to remove node {node_name} from persistence: {e}")

            # Remove successfully deleted hosts from user_hosts.yaml
            for host_name in successfully_deleted_hosts:
                try:
                    remove_user_host(host_name, USER_HOSTS_PATH)
                except Exception as e:
                    self.logger.warning(f"Failed to remove host {host_name} from persistence: {e}")

            # Remove successfully deleted firewalls from user_firewalls.yaml
            for fw_name in successfully_deleted_firewalls:
                try:
                    remove_user_firewall(fw_name, USER_FIREWALLS_PATH)
                except Exception as e:
                    self.logger.warning(f"Failed to remove firewall {fw_name} from persistence: {e}")

            # Remove successfully deleted VeloCloud devices from user_velo.yaml
            for velo_name in successfully_deleted_velo:
                try:
                    remove_user_velo_device(velo_name, USER_VELO_PATH)
                except Exception as e:
                    self.logger.warning(f"Failed to remove VeloCloud device {velo_name} from persistence: {e}")

            # Remove successfully deleted DMF devices from user_dmf.yaml
            for dmf_name in successfully_deleted_dmf:
                try:
                    remove_user_dmf_device(dmf_name, USER_DMF_PATH)
                except Exception as e:
                    self.logger.warning(f"Failed to remove DMF device {dmf_name} from persistence: {e}")

            self.logger.info(
                f"Persistence updated: removed {len(successfully_deleted_nodes)} nodes, "
                f"{len(successfully_deleted_hosts)} hosts, {len(successfully_deleted_firewalls)} firewalls, "
                f"{len(successfully_deleted_velo)} VeloCloud devices, "
                f"{len(successfully_deleted_dmf)} DMF devices"
            )
        except Exception as e:
            self.logger.error(f"Failed to update persistence files: {e}")
            results['errors'].append({
                'type': 'persistence',
                'error': str(e)
            })

        # Phase 10: Clear orphaned interface slots
        # When doing a full reset, we need to clear the orphaned slots registry
        # and optionally detach the orphaned interfaces from VMs
        self.logger.info("Phase 10: Clearing orphaned interface slots")
        try:
            from orphaned_interfaces import clear_all_orphaned_slots, list_all_orphaned_slots
            from interface_manager import detach_interface_from_vm

            # Get current orphaned slots before clearing
            all_orphaned = list_all_orphaned_slots()
            orphaned_count = sum(len(slots) for slots in all_orphaned.values())

            if orphaned_count > 0:
                # Optionally detach the orphaned interfaces for a clean slate
                # This is safe during reset-all because we're restoring to original state
                detach_successes = []
                detach_failures = []

                for device_name, slots in all_orphaned.items():
                    for slot in slots:
                        try:
                            mac = slot.get('mac_address')
                            if mac:
                                detach_interface_from_vm(device_name, mac)
                                detach_successes.append({
                                    'device': device_name,
                                    'mac': mac
                                })
                                self.logger.info(
                                    f"Detached orphaned interface {mac} from {device_name}"
                                )
                        except Exception as e:
                            # Interface may already be gone - warn but continue
                            detach_failures.append({
                                'device': device_name,
                                'mac': slot.get('mac_address'),
                                'error': str(e)
                            })
                            self.logger.warning(
                                f"Could not detach orphaned interface from {device_name}: {e}"
                            )

                # Track detachment results
                results['orphaned_detach_successes'] = len(detach_successes)
                results['orphaned_detach_failures'] = detach_failures

                # Clear the orphaned slots registry
                cleared_count = clear_all_orphaned_slots()
                results['orphaned_slots_cleared'] = cleared_count
                self.logger.info(
                    f"Cleared {cleared_count} orphaned interface slot(s) "
                    f"({len(detach_successes)} detached, {len(detach_failures)} failed)"
                )
            else:
                results['orphaned_slots_cleared'] = 0
                self.logger.info("No orphaned interface slots to clear")

        except Exception as e:
            self.logger.error(f"Failed to clear orphaned interface slots: {e}")
            results['errors'].append({
                'type': 'orphaned_slots',
                'error': str(e)
            })
            results['orphaned_slots_cleared'] = 0

        # Convert set to list for JSON serialization
        results['affected_devices'] = list(results['affected_devices'])

        # Summary
        total_deleted = (
            len(results['nodes_deleted']) +
            len(results['hosts_deleted']) +
            len(results['firewalls_deleted']) +
            len(results.get('velo_deleted', [])) +
            len(results.get('cloudeos_deleted', []))
        )

        results['status'] = 'completed' if not results['errors'] else 'completed_with_errors'
        results['summary'] = {
            'nodes': len(results['nodes_deleted']),
            'hosts': len(results['hosts_deleted']),
            'firewalls': len(results['firewalls_deleted']),
            'velocloud': len(results.get('velo_deleted', [])),
            'cloudeos': len(results.get('cloudeos_deleted', [])),
            'dmf': len(results.get('dmf_deleted', [])),
            'links': len(results.get('links_deleted', [])),
            'bridges': len(results['bridges_cleaned']),
            'orphaned_slots': results.get('orphaned_slots_cleared', 0),
            'orphaned_detach_failures': len(results.get('orphaned_detach_failures', [])),
            'errors': len(results['errors']),
            'affected_devices': len(results['affected_devices'])
        }

        self.logger.info(
            f"Reset complete: {total_deleted} devices deleted, "
            f"{len(results.get('links_deleted', []))} links removed, "
            f"{len(results['bridges_cleaned'])} bridges cleaned, "
            f"{results.get('orphaned_slots_cleared', 0)} orphaned slots cleared, "
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
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
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
        Determine if a bridge was created by nodebuilder for user-added nodes.

        Nodebuilder bridges ALWAYS use 'x' as separator between device code and port:
        Format: {dev_code}x{port_code}-{dev_code}x{port_code}
        Examples: le5x1-sp4x9, cl1xet1-le3x5, fw1xet1-sp1x12

        Original topology bridges (created by kvmbuilder) do NOT use 'x':
        Examples: sp13-le13, le11-le21, le17-ho11

        This distinction ensures we NEVER delete original topology bridges.

        Args:
            bridge_name: Name of the OVS bridge

        Returns:
            True if this is a nodebuilder-created bridge (contains 'x')
        """
        import re

        # Nodebuilder bridge pattern: MUST contain 'x' separator
        # Format: {code}x{port}-{code}x{port}
        # Examples: le5x1-sp4x9, cl1xet1-le3x5, fw1xet1-sp1x12, bo3x1-sp2x9
        #
        # Original kvmbuilder bridges do NOT have 'x': sp13-le13, le11-le21
        # By requiring 'x', we protect all original topology bridges
        nodebuilder_pattern = r'^[a-z]{2,4}\d*x[a-z0-9]+-[a-z]{2,4}\d*x[a-z0-9]+$'

        return bool(re.match(nodebuilder_pattern, bridge_name))

    def _get_expected_bridges_from_persistence(self) -> set:
        """
        Build a set of expected bridge names from persistence files.

        This cross-references bridges with what should exist based on
        user_nodes.yaml, user_hosts.yaml, and user_firewalls.yaml.

        Returns:
            Set of bridge names that should exist for active user devices
        """
        from persistence import load_user_nodes, load_user_hosts, load_user_firewalls
        from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH
        from interface_manager import generate_bridge_name

        expected_bridges = set()

        try:
            # Get bridges from user nodes
            nodes_data = load_user_nodes(USER_NODES_PATH)
            for node_entry in nodes_data.get('nodes', []):
                for node_name, node_info in node_entry.items():
                    for neighbor in node_info.get('neighbors', []):
                        local_port = neighbor.get('port', '')
                        target_device = neighbor.get('neighborDevice', '')
                        target_port = neighbor.get('neighborPort', '')
                        if local_port and target_device and target_port:
                            bridge = generate_bridge_name(
                                node_name, local_port, target_device, target_port
                            )
                            expected_bridges.add(bridge)

            # Get bridges from user hosts
            hosts_data = load_user_hosts(USER_HOSTS_PATH)
            for host_entry in hosts_data.get('hosts', []):
                for host_name, host_info in host_entry.items():
                    connection = host_info.get('connection', {})
                    bridge = connection.get('bridge')
                    if bridge:
                        expected_bridges.add(bridge)

            # Get bridges from user firewalls
            firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
            for fw_entry in firewalls_data.get('firewalls', []):
                for fw_name, fw_info in fw_entry.items():
                    for iface_key in ['inside_interface', 'outside_interface']:
                        iface = fw_info.get(iface_key, {})
                        bridge = iface.get('bridge')
                        if bridge:
                            expected_bridges.add(bridge)

            self.logger.debug(f"Found {len(expected_bridges)} expected bridges from persistence")

        except Exception as e:
            self.logger.warning(f"Error building expected bridges from persistence: {e}")

        return expected_bridges

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
                timeout=SUBPROCESS_TIMEOUT_SHORT
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
        2. Truly orphaned, determined by:
           a) Port count (0-1 ports means one/both VMs deleted)
           b) NOT preserved for an orphaned interface slot

        Cross-references with persistence files and orphaned slot registry.

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
            'skipped_healthy': 0,
            'skipped_preserved': 0
        }

        try:
            # Get expected bridges from persistence for cross-reference
            expected_bridges = self._get_expected_bridges_from_persistence()

            # Get bridges preserved for orphaned slots -- these must NOT be deleted
            preserved_slot_bridges = set()
            try:
                from orphaned_interfaces import list_all_orphaned_slots
                all_orphaned = list_all_orphaned_slots()
                for device_name, slots in all_orphaned.items():
                    for slot in slots:
                        old_bridge = slot.get('old_bridge')
                        if old_bridge:
                            preserved_slot_bridges.add(old_bridge)
            except Exception as e:
                self.logger.warning(f"Could not load orphaned slots for bridge cleanup: {e}")

            # Get all OVS bridges
            result = subprocess.run(
                ['ovs-vsctl', 'list-br'],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
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

                # Skip bridges preserved for orphaned interface slots
                if bridge in preserved_slot_bridges:
                    results['skipped_preserved'] += 1
                    self.logger.debug(
                        f"Skipping bridge {bridge} (preserved for orphaned slot)"
                    )
                    continue

                # Check port count - healthy bridges should have 2 ports
                port_count = self._get_bridge_port_count(bridge)

                # Check if bridge is in persistence (expected to exist)
                in_persistence = bridge in expected_bridges

                # Determine if bridge is orphaned:
                # A bridge is orphaned if it has < 2 ports (one or both VMs deleted)
                #
                # IMPORTANT: We only use port count, not persistence.
                # Original topology bridges are NOT in user persistence but should
                # never be deleted. If a bridge has 2 ports, it's healthy regardless
                # of whether it's in user persistence or the original topology.
                is_orphaned = port_count < 2

                if is_orphaned:
                    # Bridge has < 2 ports, meaning one or both VMs are gone
                    reason = f"port_count={port_count}"

                    results['orphaned_found'].append({
                        'bridge': bridge,
                        'port_count': port_count,
                        'in_persistence': in_persistence,
                        'reason': reason
                    })

                    try:
                        delete_ovs_bridge(bridge)
                        results['deleted'].append(bridge)
                        self.logger.info(f"Deleted orphaned bridge: {bridge} ({reason})")
                    except Exception as e:
                        results['failed'].append({
                            'bridge': bridge,
                            'error': str(e)
                        })
                        self.logger.warning(f"Failed to delete bridge {bridge}: {e}")
                else:
                    # Bridge has 2+ ports and is in persistence - healthy
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
        Clean up a single connection: record orphaned slot and delete bridge.

        With slot preservation enabled, this method does NOT detach the interface
        from the target device. Instead, it:
        1. Records the interface as an orphaned slot (for reuse later)
        2. Deletes the OVS bridge

        This allows new devices to reuse the same interface slot without
        requiring a reboot of the target device.

        Args:
            connection: Connection dict with 'bridge', 'target_device', 'target_port' keys
            connection_name: Optional name for logging (e.g., 'inside', 'outside')

        Returns:
            Dict with cleanup status:
            - slot_preserved: bool - True if slot was recorded for reuse
            - interface_detached: bool - True if interface was detached
            - bridge_deleted: bool - True if OVS bridge was deleted
            - target_device: str or None - Device that needs reboot if detached
            - reason: str or None - Reason for fallback if applicable:
                - 'port_extraction_failed': Could not parse port number
                - 'recording_failed': Failed to record orphaned slot
            - errors: list - Error messages encountered
        """
        from interface_manager import extract_port_number

        result = {
            'slot_preserved': False,
            'interface_detached': False,
            'bridge_deleted': False,
            'target_device': None,
            'reason': None,
            'errors': []
        }

        if not connection:
            return result

        bridge_name = connection.get('bridge')
        target_device = connection.get('target_device')
        target_port = connection.get('target_port', '')
        log_prefix = f"[{connection_name}] " if connection_name else ""

        # Check if slot preservation is enabled
        try:
            from config import ENABLE_SLOT_PRESERVATION
            slot_preservation_enabled = ENABLE_SLOT_PRESERVATION
        except ImportError:
            slot_preservation_enabled = False

        # Record orphaned slot instead of detaching (if slot preservation enabled)
        if target_device and bridge_name and slot_preservation_enabled:
            try:
                interfaces = get_vm_interfaces(target_device)
                interface_found = False
                for intf in interfaces:
                    if intf.get('source') == bridge_name:
                        interface_found = True
                        mac = intf.get('mac')
                        if mac:
                            # Extract port number from target_port (e.g., "Ethernet5" -> 5)
                            port_num = extract_port_number(target_port)
                            if port_num:
                                try:
                                    from orphaned_interfaces import record_orphaned_slot
                                    self.logger.info(
                                        f"{log_prefix}Recording orphaned slot: {target_device}:{target_port} "
                                        f"(MAC {mac}, bridge {bridge_name})"
                                    )
                                    record_orphaned_slot(
                                        target_device=target_device,
                                        slot_number=port_num,
                                        mac_address=mac,
                                        old_bridge=bridge_name,
                                        original_connection=connection
                                    )
                                    result['slot_preserved'] = True
                                    # No reboot needed - slot is preserved
                                except Exception as e:
                                    # Recording failed - fall back to detach
                                    self.logger.warning(
                                        f"{log_prefix}Failed to record orphaned slot for "
                                        f"{target_device}:{target_port}: {e}. "
                                        f"Falling back to detach."
                                    )
                                    detach_interface_from_vm(target_device, mac)
                                    result['interface_detached'] = True
                                    result['target_device'] = target_device
                                    result['reason'] = 'recording_failed'
                            else:
                                # Fallback: detach interface to avoid zombie
                                self.logger.warning(
                                    f"{log_prefix}Could not extract port number from {target_port} "
                                    f"on {target_device}. Slot preservation requires "
                                    f"numeric port identifiers. Falling back to detach "
                                    f"(interface may renumber on reboot)."
                                )
                                detach_interface_from_vm(target_device, mac)
                                result['interface_detached'] = True
                                result['target_device'] = target_device
                                result['reason'] = 'port_extraction_failed'
                        break

                if not interface_found:
                    self.logger.info(
                        f"{log_prefix}No interface found on bridge {bridge_name} for {target_device}"
                    )
            except Exception as e:
                error_msg = f"Failed to record orphaned slot for {target_device}: {e}"
                self.logger.warning(error_msg)
                result['errors'].append(error_msg)
                # Fall through to delete bridge anyway

        elif target_device and bridge_name and not slot_preservation_enabled:
            # Slot preservation disabled - detach interface (old behavior)
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
                            result['target_device'] = target_device
                        break
            except Exception as e:
                error_msg = f"Failed to detach {log_prefix}interface from {target_device}: {e}"
                self.logger.error(error_msg)
                result['errors'].append(error_msg)

        # Delete OVS bridge (skip if slot was preserved -- bridge keeps target VM bootable)
        if bridge_name and not result['slot_preserved']:
            try:
                self.logger.info(f"{log_prefix}Deleting OVS bridge: {bridge_name}")
                delete_ovs_bridge(bridge_name)
                result['bridge_deleted'] = True
            except Exception as e:
                error_msg = f"Failed to delete {log_prefix}bridge {bridge_name}: {e}"
                self.logger.warning(error_msg)
                result['errors'].append(error_msg)
        elif bridge_name and result['slot_preserved']:
            self.logger.info(
                f"{log_prefix}Keeping bridge {bridge_name} (preserved for orphaned slot)"
            )

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
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            result['vm_destroyed'] = proc.returncode == 0
        except Exception as e:
            self.logger.warning(f"Failed to destroy VM {vm_name}: {e}")

        # Step 2: Undefine VM (CRITICAL - must succeed to remove from persistence)
        try:
            proc = subprocess.run(
                ['virsh', 'undefine', vm_name],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            result['vm_undefined'] = proc.returncode == 0
            if proc.returncode != 0:
                error_msg = f"Failed to undefine VM '{vm_name}': {proc.stderr.strip()}"
                self.logger.error(error_msg)
                # This is a critical failure - VM still exists in libvirt
                raise RuntimeError(error_msg)
        except subprocess.TimeoutExpired as e:
            error_msg = f"Timeout undefining VM '{vm_name}': {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)
        except RuntimeError:
            # Re-raise our own RuntimeError
            raise
        except Exception as e:
            error_msg = f"Failed to undefine VM '{vm_name}': {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

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

    # =========================================================================
    # Reconciliation Operations (detect and fix inconsistencies)
    # =========================================================================

    def reconcile_resources(self, dry_run: bool = True) -> Dict:
        """
        Detect and optionally fix inconsistencies between persistence and reality.

        This reconciliation handles three types of issues:
        1. Zombie VMs: VMs that exist in libvirt but have no persistence entry
        2. Orphan entries: Persistence entries for VMs that don't exist
        3. Orphaned bridges: OVS bridges with missing VM attachments

        Args:
            dry_run: If True, only report issues without fixing them

        Returns:
            Dict with reconciliation results
        """
        from persistence import (
            load_user_nodes, load_user_hosts, load_user_firewalls,
            remove_user_node, remove_user_host, remove_user_firewall,
            update_user_node_status
        )
        from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH

        results = {
            'dry_run': dry_run,
            'zombie_vms': [],        # VMs without persistence
            'orphan_entries': [],    # Persistence without VMs
            'orphan_bridges': [],    # Bridges without proper attachments
            'fixed': [],             # Items that were fixed
            'errors': []
        }

        self.logger.info(f"Starting resource reconciliation (dry_run={dry_run})")

        # Collect all persistence entries
        persisted_names = set()

        # Get node names from persistence
        nodes_data = load_user_nodes(USER_NODES_PATH)
        for node_entry in nodes_data.get('nodes', []):
            for node_name in node_entry.keys():
                persisted_names.add(node_name.lower())

        # Get host names from persistence
        hosts_data = load_user_hosts(USER_HOSTS_PATH)
        for host_entry in hosts_data.get('hosts', []) or []:
            for host_name in host_entry.keys():
                persisted_names.add(host_name.lower())

        # Get firewall names from persistence
        firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
        for fw_entry in firewalls_data.get('firewalls', []) or []:
            for fw_name in fw_entry.keys():
                persisted_names.add(fw_name.lower())

        self.logger.info(f"Found {len(persisted_names)} persisted device names")

        # Get all VMs from libvirt
        try:
            result = subprocess.run(
                ['virsh', 'list', '--all', '--name'],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            vm_names = [name.strip() for name in result.stdout.split('\n') if name.strip()]
        except Exception as e:
            self.logger.error(f"Failed to list VMs: {e}")
            results['errors'].append(f"Failed to list VMs: {e}")
            return results

        # Find zombie VMs (in libvirt but not in any known source)
        # A zombie is a VM that exists in libvirt but isn't in:
        # - topo_build.yml (original topology devices)
        # - user_nodes.yaml, user_hosts.yaml, user_firewalls.yaml, user_velo.yaml
        # These are typically VMs where creation succeeded but persistence save failed
        try:
            from persistence import list_user_velo_devices
            from config import USER_VELO_PATH, TOPO_BUILD_PATH

            # Get topology VM names from topo_build.yml
            topology_names = set()
            if os.path.exists(TOPO_BUILD_PATH):
                try:
                    yaml = YAML()
                    with open(TOPO_BUILD_PATH, 'r') as f:
                        topo_data = yaml.load(f)
                    if topo_data and 'nodes' in topo_data:
                        for node in topo_data['nodes']:
                            if isinstance(node, dict):
                                for name in node.keys():
                                    topology_names.add(name.lower())
                except Exception as e:
                    self.logger.warning(f"Could not load topology names: {e}")

            # Get VeloCloud device names
            velo_names = set()
            try:
                velo_devices = list_user_velo_devices(USER_VELO_PATH)
                for device_entry in velo_devices:
                    if isinstance(device_entry, dict):
                        for name in device_entry.keys():
                            velo_names.add(name.lower())
            except Exception as e:
                self.logger.warning(f"Could not load VeloCloud names: {e}")

            # All known VM names
            all_known_names = persisted_names | topology_names | velo_names

            # Find VMs that exist in libvirt but aren't in any known source
            for vm_name in vm_names:
                vm_lower = vm_name.lower()
                if vm_lower not in all_known_names:
                    # This is a potential zombie VM
                    # Get VM state for more info
                    vm_state = self.get_vm_state(vm_name)
                    results['zombie_vms'].append({
                        'name': vm_name,
                        'type': 'unknown',
                        'state': vm_state,
                        'reason': 'VM exists in libvirt but not in any persistence file or topology'
                    })

        except Exception as e:
            self.logger.error(f"Error detecting zombie VMs: {e}")
            results['errors'].append(f"Zombie VM detection failed: {e}")

        # Find orphan entries (in persistence but not in libvirt)
        for node_entry in nodes_data.get('nodes', []):
            for node_name, node_info in node_entry.items():
                if not self.vm_exists(node_name):
                    status = node_info.get('status', 'active')
                    results['orphan_entries'].append({
                        'name': node_name,
                        'type': 'node',
                        'status': status,
                        'reason': 'VM not defined in libvirt'
                    })
                    if not dry_run:
                        try:
                            remove_user_node(node_name, USER_NODES_PATH)
                            results['fixed'].append(f"Removed orphan node entry: {node_name}")
                        except Exception as e:
                            results['errors'].append(f"Failed to remove orphan node {node_name}: {e}")

        for host_entry in hosts_data.get('hosts', []) or []:
            for host_name, host_info in host_entry.items():
                if not self.vm_exists(host_name):
                    status = host_info.get('status', 'active')
                    results['orphan_entries'].append({
                        'name': host_name,
                        'type': 'host',
                        'status': status,
                        'reason': 'VM not defined in libvirt'
                    })
                    if not dry_run:
                        try:
                            remove_user_host(host_name, USER_HOSTS_PATH)
                            results['fixed'].append(f"Removed orphan host entry: {host_name}")
                        except Exception as e:
                            results['errors'].append(f"Failed to remove orphan host {host_name}: {e}")

        for fw_entry in firewalls_data.get('firewalls', []) or []:
            for fw_name, fw_info in fw_entry.items():
                if not self.vm_exists(fw_name):
                    status = fw_info.get('status', 'active')
                    results['orphan_entries'].append({
                        'name': fw_name,
                        'type': 'firewall',
                        'status': status,
                        'reason': 'VM not defined in libvirt'
                    })
                    if not dry_run:
                        try:
                            remove_user_firewall(fw_name, USER_FIREWALLS_PATH)
                            results['fixed'].append(f"Removed orphan firewall entry: {fw_name}")
                        except Exception as e:
                            results['errors'].append(f"Failed to remove orphan firewall {fw_name}: {e}")

        # Check for entries with 'creating' status (stuck creates)
        for node_entry in nodes_data.get('nodes', []):
            for node_name, node_info in node_entry.items():
                if node_info.get('status') == 'creating':
                    # This is a pending create that may have failed
                    if self.vm_exists(node_name):
                        results['zombie_vms'].append({
                            'name': node_name,
                            'type': 'node',
                            'status': 'creating',
                            'reason': 'Stuck in creating status but VM exists'
                        })
                        # Auto-fix: If VM exists, update status to active
                        if not dry_run:
                            try:
                                update_user_node_status(node_name, 'active', {}, USER_NODES_PATH)
                                results['fixed'].append(f"Fixed stuck node status: {node_name}")
                            except Exception as e:
                                results['errors'].append(f"Failed to fix stuck node {node_name}: {e}")
                    # If VM doesn't exist, it's already caught as orphan

        # Clean orphaned bridges
        if not dry_run:
            try:
                bridge_results = self.cleanup_all_orphaned_bridges()
                results['orphan_bridges'] = bridge_results.get('orphaned_found', [])
                for bridge in bridge_results.get('deleted', []):
                    results['fixed'].append(f"Deleted orphan bridge: {bridge}")
            except Exception as e:
                results['errors'].append(f"Bridge cleanup failed: {e}")
        else:
            # Dry run - just scan for orphan bridges
            try:
                result = subprocess.run(
                    ['ovs-vsctl', 'list-br'],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )
                if result.returncode == 0:
                    bridges = [b.strip() for b in result.stdout.split('\n') if b.strip()]
                    system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}
                    for bridge in bridges:
                        if bridge in system_bridges:
                            continue
                        if any(c in bridge for c in ['x', '-']):  # User bridges have patterns
                            port_count = self._get_bridge_port_count(bridge)
                            if port_count < 2:
                                results['orphan_bridges'].append({
                                    'bridge': bridge,
                                    'port_count': port_count
                                })
            except Exception as e:
                results['errors'].append(f"Bridge scan failed: {e}")

        # Clean up orphaned interface slots for devices that no longer exist
        # This removes stale entries where the target device has been deleted
        # NOTE: We do NOT age out valid orphaned slots because they preserve
        # PCI slot ordering - removing them would cause interface renumbering on reboot
        try:
            from orphaned_interfaces import cleanup_invalid_orphaned_slots, analyze_orphaned_slot_health

            # Remove slots for devices that no longer exist in libvirt
            invalid_slots = cleanup_invalid_orphaned_slots(dry_run=dry_run)
            results['invalid_orphaned_slots'] = invalid_slots
            if invalid_slots.get('removed_count', 0) > 0:
                if not dry_run:
                    results['fixed'].append(
                        f"Removed {invalid_slots['removed_count']} invalid orphaned slots"
                    )
                self.logger.info(
                    f"Cleaned up {invalid_slots['removed_count']} invalid orphaned slots "
                    f"(devices: {invalid_slots.get('devices_cleaned', [])})"
                )

            # Analyze slot health (warnings only - do not auto-remove valid slots)
            health = analyze_orphaned_slot_health()
            results['orphaned_slot_health'] = health
            if health.get('warnings'):
                for warning in health['warnings']:
                    self.logger.info(f"Orphaned slot health: {warning}")
        except Exception as e:
            self.logger.error(f"Failed to process orphaned slots: {e}")
            results['errors'].append(f"Orphaned slot processing failed: {e}")

        self.logger.info(
            f"Reconciliation complete: orphan_entries={len(results['orphan_entries'])}, "
            f"zombie_vms={len(results['zombie_vms'])}, "
            f"orphan_bridges={len(results['orphan_bridges'])}, "
            f"fixed={len(results['fixed'])}"
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
