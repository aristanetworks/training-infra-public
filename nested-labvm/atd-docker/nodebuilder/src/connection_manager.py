"""
Connection Manager for Nodebuilder Service

Provides decoupled CRUD operations for node connections:
- Create connections (OVS bridges + interface attachments)
- Delete connections (interface detachment + bridge cleanup)
- Query connections

This module enables:
- Atomic connection operations for Node Edit
- Batch connection creation for Node Clusters
- Independent connection lifecycle management
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from interface_manager import (
    create_ovs_bridge,
    delete_ovs_bridge,
    attach_interface_to_vm,
    detach_interface_from_vm,
    generate_bridge_name,
    find_next_available_port,
    get_vm_interfaces,
    update_interface_bridge,
    extract_port_number
)
from orphaned_interfaces import (
    record_orphaned_slot,
    get_orphaned_slot_by_port,
    claim_orphaned_slot
)
from config import ENABLE_SLOT_PRESERVATION

logger = logging.getLogger('nodebuilder')


def process_connection_for_creation(
    source_device: str,
    local_port: str,
    target_device: str,
    target_port: Optional[str] = None,
    txn: Optional['ResourceTransaction'] = None
) -> Dict:
    """
    Process a single connection during device creation.

    Creates OVS bridge and returns connection details for XML generation.
    This is the common pattern used by all device managers when creating
    new VMs (vEOS, hosts, firewalls, VeloCloud devices).

    The source interface is typically defined in the VM XML rather than
    being attached separately, so we only create the bridge here.

    Args:
        source_device: Name of the device being created
        local_port: Local port/interface name on source device
        target_device: Name of the target device to connect to
        target_port: Target port (if None, finds next available)
        txn: ResourceTransaction to track the bridge for rollback (optional)

    Returns:
        Dict with keys: target_device, target_port, local_port, bridge
    """
    # Get target port if not specified
    if not target_port:
        target_port = find_next_available_port(target_device)

    # Generate bridge name using standard naming convention
    bridge_name = generate_bridge_name(
        source_device, local_port,
        target_device, target_port
    )

    # Create the OVS bridge
    logger.info(f"Creating OVS bridge: {bridge_name}")
    create_ovs_bridge(bridge_name)

    # Track for rollback if transaction provided
    if txn:
        txn.add_resource('bridge', bridge_name)

    return {
        'target_device': target_device,
        'target_port': target_port,
        'local_port': local_port,
        'bridge': bridge_name
    }


@dataclass
class Connection:
    """
    Represents a single connection between two nodes.

    Connections are bidirectional - both endpoints get an interface
    attached to a shared OVS bridge.
    """
    source_device: str
    source_port: str
    target_device: str
    target_port: str
    bridge_name: str = ''

    def __post_init__(self):
        """Generate bridge name if not provided."""
        if not self.bridge_name:
            self.bridge_name = generate_bridge_name(
                self.source_device, self.source_port,
                self.target_device, self.target_port
            )


class ConnectionManager:
    """
    Manages individual and batch connection operations.

    Provides atomic operations that can be used in transactions
    for rollback support.
    """

    def __init__(self):
        self.logger = logging.getLogger('nodebuilder.connection_manager')

    def prepare_connection(
        self,
        source_device: str,
        target_device: str,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None
    ) -> Connection:
        """
        Prepare a connection object with auto-assigned ports.

        Does not create any resources - just prepares the connection
        configuration for later execution.

        Args:
            source_device: Source device name
            target_device: Target device name
            source_port: Optional explicit source port (auto-assigned if None)
            target_port: Optional explicit target port (auto-assigned if None)

        Returns:
            Connection object ready for creation
        """
        # Auto-assign ports if not specified
        if not source_port:
            source_port = find_next_available_port(source_device)
        if not target_port:
            target_port = find_next_available_port(target_device)

        return Connection(
            source_device=source_device,
            source_port=source_port,
            target_device=target_device,
            target_port=target_port
        )

    def create_connection(self, conn: Connection) -> Dict:
        """
        Create a single connection between two nodes.

        Creates:
        1. OVS bridge with appropriate name
        2. Interface attachment on target device:
           - If target port has an orphaned slot: reuse it via update_interface_bridge
           - Otherwise: attach a new interface via attach_interface_to_vm

        Note: For new nodes, the source interface is added via XML definition,
        so we only attach to the target device. For Edit operations, we may
        need to attach to both.

        Args:
            conn: Connection object

        Returns:
            Dict with creation status and details
        """
        # Check if target port corresponds to an orphaned slot
        slot_number = extract_port_number(conn.target_port)
        orphaned_slot = None
        if slot_number and ENABLE_SLOT_PRESERVATION:
            orphaned_slot = get_orphaned_slot_by_port(conn.target_device, slot_number)

        if orphaned_slot:
            self.logger.info(
                f"Creating connection (reusing orphaned slot): "
                f"{conn.source_device}:{conn.source_port} <-> "
                f"{conn.target_device}:{conn.target_port} (bridge: {conn.bridge_name})"
            )
        else:
            self.logger.info(
                f"Creating connection: {conn.source_device}:{conn.source_port} <-> "
                f"{conn.target_device}:{conn.target_port} (bridge: {conn.bridge_name})"
            )

        result = {
            'connection': {
                'source_device': conn.source_device,
                'source_port': conn.source_port,
                'target_device': conn.target_device,
                'target_port': conn.target_port,
                'bridge': conn.bridge_name
            },
            'steps': [],
            'reused_orphaned_slot': orphaned_slot is not None
        }

        # Step 1: Create OVS bridge
        try:
            bridge_result = create_ovs_bridge(conn.bridge_name)
            result['steps'].append({
                'step': 'create_bridge',
                'status': bridge_result.get('status'),
                'bridge': conn.bridge_name
            })
        except Exception as e:
            self.logger.error(f"Failed to create bridge: {e}")
            result['status'] = 'failed'
            result['error'] = f"Bridge creation failed: {e}"
            return result

        # Step 2: Connect target device to bridge
        if orphaned_slot:
            # Reuse orphaned slot - update existing interface's bridge
            update_succeeded = False
            try:
                update_result = update_interface_bridge(
                    vm_name=conn.target_device,
                    mac_address=orphaned_slot['mac_address'],
                    new_bridge=conn.bridge_name
                )
                update_succeeded = True
                result['steps'].append({
                    'step': 'reuse_orphaned_slot',
                    'status': update_result.get('status'),
                    'device': conn.target_device,
                    'port': conn.target_port,
                    'mac': orphaned_slot['mac_address'],
                    'old_bridge': orphaned_slot.get('old_bridge'),
                    'immediate': update_result.get('immediate', False)
                })

            except Exception as e:
                self.logger.error(f"Failed to update interface bridge: {e}")
                # Rollback: delete the bridge (only if update failed)
                rollback_success = False
                try:
                    delete_ovs_bridge(conn.bridge_name)
                    rollback_success = True
                    self.logger.info(f"Rolled back bridge {conn.bridge_name} after update failure")
                except Exception as rollback_err:
                    self.logger.error(
                        f"ORPHANED BRIDGE: Failed to rollback bridge {conn.bridge_name}: {rollback_err}"
                    )

                # Don't claim the orphaned slot - leave it for retry
                result['status'] = 'failed'
                result['error'] = f"Failed to reuse orphaned slot: {e}"
                result['rollback_success'] = rollback_success
                if not rollback_success:
                    result['orphaned_bridge'] = conn.bridge_name
                return result

            # Only claim the orphaned slot after update succeeds
            # This is done in a separate try block to avoid rollback if only claim fails
            if update_succeeded:
                try:
                    claim_orphaned_slot(conn.target_device, orphaned_slot['mac_address'])
                    self.logger.info(
                        f"Successfully reused orphaned slot: {conn.target_device}:{conn.target_port} "
                        f"(MAC: {orphaned_slot['mac_address']}) -> bridge {conn.bridge_name}"
                    )
                except Exception as claim_err:
                    # Interface update succeeded but claim failed
                    # Log error but don't rollback - the connection is working
                    self.logger.warning(
                        f"Failed to claim orphaned slot on {conn.target_device}: {claim_err}. "
                        f"Slot may be orphaned in registry but interface is connected."
                    )
                    result['steps'].append({
                        'step': 'claim_orphaned_slot',
                        'status': 'warning',
                        'error': str(claim_err),
                        'note': 'Interface connected but registry not updated'
                    })

                # Clean up the old bridge that was kept alive for the orphaned slot
                old_bridge = orphaned_slot.get('old_bridge')
                if old_bridge and old_bridge != conn.bridge_name:
                    try:
                        delete_ovs_bridge(old_bridge)
                        self.logger.info(
                            f"Cleaned up old bridge {old_bridge} after slot reuse"
                        )
                    except Exception as e:
                        self.logger.debug(
                            f"Old bridge {old_bridge} cleanup skipped: {e}"
                        )
        else:
            # Standard attach - create new interface
            try:
                attach_result = attach_interface_to_vm(conn.target_device, conn.bridge_name)
                result['steps'].append({
                    'step': 'attach_target',
                    'status': attach_result.get('status'),
                    'device': conn.target_device
                })
            except Exception as e:
                self.logger.error(f"Failed to attach to target: {e}")
                # Rollback: delete the bridge
                rollback_success = False
                try:
                    delete_ovs_bridge(conn.bridge_name)
                    rollback_success = True
                    self.logger.info(f"Rolled back bridge {conn.bridge_name} after attachment failure")
                except Exception as rollback_err:
                    self.logger.error(
                        f"ORPHANED BRIDGE: Failed to rollback bridge {conn.bridge_name}: {rollback_err}. "
                        f"Manual cleanup may be required."
                    )
                result['status'] = 'failed'
                result['error'] = f"Target attachment failed: {e}"
                result['rollback_success'] = rollback_success
                if not rollback_success:
                    result['orphaned_bridge'] = conn.bridge_name
                return result

        result['status'] = 'created'
        return result

    def delete_connection(
        self,
        conn: Connection,
        detach_from_source: bool = True,
        detach_from_target: bool = True,
        preserve_target_slot: bool = None
    ) -> Dict:
        """
        Delete a connection between two nodes.

        Cleans up:
        1. Interface from source device (if requested)
        2. Interface from target device (if requested, unless preserving slot)
        3. OVS bridge

        When preserve_target_slot is True, the target interface is kept attached
        to the VM but recorded as "orphaned" for later reuse. This prevents
        vEOS interface renumbering issues.

        Args:
            conn: Connection object
            detach_from_source: Whether to detach interface from source device
            detach_from_target: Whether to detach interface from target device
            preserve_target_slot: If True, keep target interface attached and record
                as orphaned. Defaults to ENABLE_SLOT_PRESERVATION config.

        Returns:
            Dict with deletion status and details
        """
        # Default to config setting if not specified
        if preserve_target_slot is None:
            preserve_target_slot = ENABLE_SLOT_PRESERVATION

        self.logger.info(
            f"Deleting connection: {conn.source_device}:{conn.source_port} <-> "
            f"{conn.target_device}:{conn.target_port} (bridge: {conn.bridge_name})"
            f" [preserve_slot={preserve_target_slot}]"
        )

        result = {
            'connection': {
                'source_device': conn.source_device,
                'source_port': conn.source_port,
                'target_device': conn.target_device,
                'target_port': conn.target_port,
                'bridge': conn.bridge_name
            },
            'steps': [],
            'errors': []
        }

        # Step 1: Detach interface from source device (the user-added device being deleted)
        if detach_from_source:
            try:
                mac = self._find_interface_mac(conn.source_device, conn.bridge_name)
                if mac:
                    detach_result = detach_interface_from_vm(conn.source_device, mac)
                    result['steps'].append({
                        'step': 'detach_source',
                        'status': detach_result.get('status'),
                        'device': conn.source_device,
                        'mac': mac
                    })
                else:
                    result['steps'].append({
                        'step': 'detach_source',
                        'status': 'not_found',
                        'device': conn.source_device
                    })
            except Exception as e:
                self.logger.error(f"Failed to detach from source {conn.source_device}: {e}")
                result['errors'].append({
                    'step': 'detach_source',
                    'device': conn.source_device,
                    'error': str(e)
                })

        # Step 2: Handle target device interface
        # If preserving slot, record as orphaned instead of detaching
        if preserve_target_slot and detach_from_target:
            try:
                mac = self._find_interface_mac(conn.target_device, conn.bridge_name)
                if mac:
                    # Extract slot number from port name (e.g., "Ethernet5" -> 5)
                    slot_number = extract_port_number(conn.target_port)
                    if slot_number:
                        # Record as orphaned slot for later reuse
                        record_orphaned_slot(
                            target_device=conn.target_device,
                            slot_number=slot_number,
                            mac_address=mac,
                            old_bridge=conn.bridge_name,
                            original_connection={
                                'source_device': conn.source_device,
                                'source_port': conn.source_port,
                                'target_device': conn.target_device,
                                'target_port': conn.target_port
                            }
                        )
                        self.logger.info(
                            f"Preserved interface slot: {conn.target_device}:{conn.target_port} "
                            f"(MAC: {mac}) - recorded as orphaned for reuse"
                        )
                        result['steps'].append({
                            'step': 'preserve_target_slot',
                            'status': 'orphaned',
                            'device': conn.target_device,
                            'port': conn.target_port,
                            'slot_number': slot_number,
                            'mac': mac
                        })
                    else:
                        # Can't extract slot number, fall back to detach
                        self.logger.warning(
                            f"Could not extract slot number from {conn.target_port}, "
                            f"falling back to detach"
                        )
                        detach_result = detach_interface_from_vm(conn.target_device, mac)
                        result['steps'].append({
                            'step': 'detach_target',
                            'status': detach_result.get('status'),
                            'device': conn.target_device,
                            'mac': mac,
                            'fallback': True
                        })
                else:
                    result['steps'].append({
                        'step': 'preserve_target_slot',
                        'status': 'not_found',
                        'device': conn.target_device
                    })
            except Exception as e:
                self.logger.error(
                    f"Failed to preserve slot on {conn.target_device}: {e}"
                )
                result['errors'].append({
                    'step': 'preserve_target_slot',
                    'device': conn.target_device,
                    'error': str(e)
                })

        elif detach_from_target:
            # Standard detach behavior (no slot preservation)
            try:
                mac = self._find_interface_mac(conn.target_device, conn.bridge_name)
                if mac:
                    detach_result = detach_interface_from_vm(conn.target_device, mac)
                    result['steps'].append({
                        'step': 'detach_target',
                        'status': detach_result.get('status'),
                        'device': conn.target_device,
                        'mac': mac
                    })
                else:
                    result['steps'].append({
                        'step': 'detach_target',
                        'status': 'not_found',
                        'device': conn.target_device
                    })
            except Exception as e:
                self.logger.error(f"Failed to detach from target {conn.target_device}: {e}")
                result['errors'].append({
                    'step': 'detach_target',
                    'device': conn.target_device,
                    'error': str(e)
                })

        # Check if target slot was actually preserved (step 2 succeeded)
        slot_actually_preserved = any(
            step.get('step') == 'preserve_target_slot' and step.get('status') == 'orphaned'
            for step in result['steps']
        )

        # Step 3: Delete OVS bridge (skip if slot was preserved -- bridge keeps target VM bootable)
        if slot_actually_preserved:
            self.logger.info(
                f"Keeping bridge {conn.bridge_name} (preserved for orphaned slot on "
                f"{conn.target_device}:{conn.target_port})"
            )
            result['steps'].append({
                'step': 'keep_bridge',
                'status': 'preserved',
                'bridge': conn.bridge_name
            })
        else:
            detachment_failures = [
                err for err in result['errors']
                if err.get('step') in ('detach_source', 'detach_target', 'preserve_target_slot')
            ]

            if detachment_failures:
                self.logger.warning(
                    f"BRIDGE DELETION WITH ISSUES: {len(detachment_failures)} "
                    f"error(s) for bridge {conn.bridge_name}. Details: {detachment_failures}"
                )
                result['warning'] = (
                    f"Bridge {conn.bridge_name} deleted but {len(detachment_failures)} "
                    f"error(s) occurred during cleanup"
                )

            try:
                bridge_result = delete_ovs_bridge(conn.bridge_name)
                result['steps'].append({
                    'step': 'delete_bridge',
                    'status': bridge_result.get('status'),
                    'bridge': conn.bridge_name
                })
            except Exception as e:
                self.logger.warning(f"Failed to delete bridge: {e}")
                result['errors'].append({
                    'step': 'delete_bridge',
                    'error': str(e)
                })

        result['status'] = 'deleted' if not result['errors'] else 'deleted_with_errors'
        result['slot_preserved'] = slot_actually_preserved
        return result

    def _find_interface_mac(self, vm_name: str, bridge_name: str) -> Optional[str]:
        """
        Find the MAC address of an interface connected to a specific bridge.

        Args:
            vm_name: Name of the VM
            bridge_name: Name of the OVS bridge

        Returns:
            MAC address if found, None otherwise
        """
        try:
            interfaces = get_vm_interfaces(vm_name)
            for intf in interfaces:
                if intf.get('source') == bridge_name:
                    return intf.get('mac')
            return None
        except Exception as e:
            self.logger.warning(f"Error finding interface MAC: {e}")
            return None

    def create_connections_batch(
        self,
        source_device: str,
        connections: List[Dict]
    ) -> List[Dict]:
        """
        Create multiple connections from a source device.

        Used during node creation to establish all initial connections.

        Args:
            source_device: Name of the source device
            connections: List of connection specs with 'target_device' key

        Returns:
            List of result dicts for each connection
        """
        self.logger.info(
            f"Creating {len(connections)} connections for {source_device}"
        )

        results = []
        local_port_counter = 1

        for conn_spec in connections:
            target_device = conn_spec.get('target_device')
            target_port = conn_spec.get('target_port') or find_next_available_port(target_device)
            local_port = conn_spec.get('local_port') or f"Ethernet{local_port_counter}"

            conn = Connection(
                source_device=source_device,
                source_port=local_port,
                target_device=target_device,
                target_port=target_port
            )

            result = self.create_connection(conn)
            result['local_port'] = local_port
            result['target_port'] = target_port
            results.append(result)

            local_port_counter += 1

        return results

    def get_node_connections(self, node_name: str, node_info: Dict) -> List[Connection]:
        """
        Get all connections for a node from its info dict.

        Args:
            node_name: Name of the node
            node_info: Node info dict containing neighbors

        Returns:
            List of Connection objects
        """
        connections = []
        neighbors = node_info.get('neighbors', [])

        for neighbor in neighbors:
            local_port = neighbor.get('port', '')
            target_device = neighbor.get('neighborDevice', '')
            target_port = neighbor.get('neighborPort', '')

            if local_port and target_device and target_port:
                connections.append(Connection(
                    source_device=node_name,
                    source_port=local_port,
                    target_device=target_device,
                    target_port=target_port
                ))

        return connections


# Module-level singleton instance
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """Get the singleton ConnectionManager instance."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
