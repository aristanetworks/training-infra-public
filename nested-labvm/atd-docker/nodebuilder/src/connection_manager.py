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
    get_vm_interfaces
)

logger = logging.getLogger('nodebuilder')


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
        2. Interface attachment on source device (if running)
        3. Interface attachment on target device (if running)

        Note: For new nodes, the source interface is added via XML definition,
        so we only attach to the target device. For Edit operations, we may
        need to attach to both.

        Args:
            conn: Connection object

        Returns:
            Dict with creation status and details
        """
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
            'steps': []
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

        # Step 2: Attach interface to target device
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
            try:
                delete_ovs_bridge(conn.bridge_name)
            except Exception:
                pass
            result['status'] = 'failed'
            result['error'] = f"Target attachment failed: {e}"
            return result

        result['status'] = 'created'
        return result

    def delete_connection(
        self,
        conn: Connection,
        detach_from_source: bool = True,
        detach_from_target: bool = True
    ) -> Dict:
        """
        Delete a connection between two nodes.

        Cleans up:
        1. Interface from source device (if requested)
        2. Interface from target device (if requested)
        3. OVS bridge

        Args:
            conn: Connection object
            detach_from_source: Whether to detach interface from source device
            detach_from_target: Whether to detach interface from target device

        Returns:
            Dict with deletion status and details
        """
        self.logger.info(
            f"Deleting connection: {conn.source_device}:{conn.source_port} <-> "
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
            'errors': []
        }

        # Step 1: Detach interface from source device
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
                self.logger.warning(f"Failed to detach from source: {e}")
                result['errors'].append({
                    'step': 'detach_source',
                    'error': str(e)
                })

        # Step 2: Detach interface from target device
        if detach_from_target:
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
                self.logger.warning(f"Failed to detach from target: {e}")
                result['errors'].append({
                    'step': 'detach_target',
                    'error': str(e)
                })

        # Step 3: Delete OVS bridge
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
