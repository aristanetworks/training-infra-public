"""
Unified Topology Module

Provides a consistent view of ALL devices across all sources:
- Original topology (topo_build.yml) - vEOS nodes
- User-added vEOS nodes (user_nodes.yaml)
- User-added Linux hosts (user_hosts.yaml)
- User-added VyOS firewalls (user_firewalls.yaml)
- User-added VeloCloud devices (user_velo.yaml)

This module standardizes the data format and provides a single source of truth
for topology queries, making it easier to extend with new device types.
"""

import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger('nodebuilder.unified_topology')


class DeviceType(Enum):
    """Standardized device type identifiers."""
    VEOS = 'veos'
    LINUX_HOST = 'linux_host'
    FIREWALL = 'firewall'
    VELO_EDGE = 'velo_edge'
    VELO_GATEWAY = 'velo_gateway'
    VELO_ORCHESTRATOR = 'velo_orchestrator'


class DeviceCategory(Enum):
    """Device category for grouping in UI."""
    NODE = 'node'          # Network devices (vEOS)
    HOST = 'host'          # End-user hosts (Linux desktop)
    FIREWALL = 'firewall'  # Security devices (VyOS)
    SDWAN = 'sdwan'        # SD-WAN devices (VeloCloud)


@dataclass
class UnifiedDevice:
    """
    Standardized device representation.

    All device types are normalized to this common structure,
    with type-specific details in the 'extra' field.
    """
    name: str
    ip: str                           # Unified IP field (mgmt_ip for hosts/fw, ip_addr for nodes)
    device_type: str                  # From DeviceType enum
    device_category: str              # From DeviceCategory enum
    user_added: bool                  # True if added by user, False if from topology
    status: str = 'active'            # Device status
    neighbors: Optional[List[Dict]] = None  # Connection info
    extra: Optional[Dict] = None      # Type-specific fields

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        # Remove None values for cleaner output
        return {k: v for k, v in result.items() if v is not None}


def get_unified_topology(
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str,
    user_velo_path: str = None
) -> Dict:
    """
    Get complete unified topology with ALL device types.

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml
        user_velo_path: Path to user_velo.yaml (optional for backwards compatibility)

    Returns:
        Dict with:
        - devices: List of unified device dicts
        - summary: Count of each device type
        - connections: List of all connections
    """
    from validation import get_topo_nodes, get_user_nodes
    from persistence import load_user_hosts, load_user_firewalls, load_user_velo

    devices = []
    connections = []

    # 1. Load topology vEOS nodes
    try:
        topo_nodes = get_topo_nodes(topo_build_path)
        for node in topo_nodes:
            device = UnifiedDevice(
                name=node['name'],
                ip=node.get('ip_addr', ''),
                device_type=DeviceType.VEOS.value,
                device_category=DeviceCategory.NODE.value,
                user_added=False,
                neighbors=node.get('neighbors', []),
                extra={
                    'sys_mac': node.get('sys_mac', ''),
                    'source': 'topology'
                }
            )
            devices.append(device.to_dict())

            # Extract connections
            for neighbor in node.get('neighbors', []):
                connections.append({
                    'source_device': node['name'],
                    'source_port': neighbor.get('port', ''),
                    'target_device': neighbor.get('neighborDevice', ''),
                    'target_port': neighbor.get('neighborPort', ''),
                    'source_type': DeviceType.VEOS.value
                })
    except Exception as e:
        logger.warning(f"Error loading topology nodes: {e}")

    # 2. Load user-added vEOS nodes
    try:
        user_nodes = get_user_nodes(user_nodes_path)
        for node in user_nodes:
            device = UnifiedDevice(
                name=node['name'],
                ip=node.get('ip_addr', ''),
                device_type=DeviceType.VEOS.value,
                device_category=DeviceCategory.NODE.value,
                user_added=True,
                status=node.get('status', 'active'),
                neighbors=node.get('neighbors', []),
                extra={
                    'sys_mac': node.get('sys_mac', ''),
                    'source': 'user_nodes'
                }
            )
            devices.append(device.to_dict())

            # Extract connections
            for neighbor in node.get('neighbors', []):
                connections.append({
                    'source_device': node['name'],
                    'source_port': neighbor.get('port', ''),
                    'target_device': neighbor.get('neighborDevice', ''),
                    'target_port': neighbor.get('neighborPort', ''),
                    'source_type': DeviceType.VEOS.value
                })
    except Exception as e:
        logger.warning(f"Error loading user nodes: {e}")

    # 3. Load user-added Linux hosts
    try:
        hosts_data = load_user_hosts(user_hosts_path)
        for host_entry in hosts_data.get('hosts', []) or []:
            for host_name, host_info in host_entry.items():
                connection = host_info.get('connection', {})

                # Build neighbor list from connection
                neighbors = []
                if connection.get('target_device'):
                    neighbors.append({
                        'port': 'eth1',  # Data interface
                        'neighborDevice': connection.get('target_device'),
                        'neighborPort': connection.get('target_port', '')
                    })

                device = UnifiedDevice(
                    name=host_name,
                    ip=host_info.get('mgmt_ip', ''),
                    device_type=DeviceType.LINUX_HOST.value,
                    device_category=DeviceCategory.HOST.value,
                    user_added=True,
                    status=host_info.get('status', 'active'),
                    neighbors=neighbors if neighbors else None,
                    extra={
                        'data_ip': host_info.get('data_ip', ''),
                        'vnc_port': host_info.get('vnc_port'),
                        'source': 'user_hosts',
                        'orphaned': connection.get('orphaned', False)
                    }
                )
                devices.append(device.to_dict())

                # Extract connection
                if connection.get('target_device'):
                    connections.append({
                        'source_device': host_name,
                        'source_port': 'eth1',
                        'target_device': connection.get('target_device'),
                        'target_port': connection.get('target_port', ''),
                        'source_type': DeviceType.LINUX_HOST.value,
                        'orphaned': connection.get('orphaned', False)
                    })
    except Exception as e:
        logger.warning(f"Error loading user hosts: {e}")

    # 4. Load user-added VyOS firewalls
    try:
        firewalls_data = load_user_firewalls(user_firewalls_path)
        for fw_entry in firewalls_data.get('firewalls', []) or []:
            for fw_name, fw_info in fw_entry.items():
                # Build neighbor list from interfaces
                neighbors = []
                for iface_key, port_name in [('inside_interface', 'eth1'), ('outside_interface', 'eth2')]:
                    iface = fw_info.get(iface_key, {})
                    if iface.get('target_device'):
                        neighbors.append({
                            'port': port_name,
                            'neighborDevice': iface.get('target_device'),
                            'neighborPort': iface.get('target_port', ''),
                            'interface_type': iface_key.replace('_interface', '')
                        })

                device = UnifiedDevice(
                    name=fw_name,
                    ip=fw_info.get('mgmt_ip', ''),
                    device_type=DeviceType.FIREWALL.value,
                    device_category=DeviceCategory.FIREWALL.value,
                    user_added=True,
                    status=fw_info.get('status', 'active'),
                    neighbors=neighbors if neighbors else None,
                    extra={
                        'inside_interface': fw_info.get('inside_interface', {}),
                        'outside_interface': fw_info.get('outside_interface', {}),
                        'vnc_port': fw_info.get('vnc_port'),
                        'source': 'user_firewalls'
                    }
                )
                devices.append(device.to_dict())

                # Extract connections for each interface
                for iface_key in ['inside_interface', 'outside_interface']:
                    iface = fw_info.get(iface_key, {})
                    if iface.get('target_device'):
                        port_name = 'eth1' if iface_key == 'inside_interface' else 'eth2'
                        connections.append({
                            'source_device': fw_name,
                            'source_port': port_name,
                            'target_device': iface.get('target_device'),
                            'target_port': iface.get('target_port', ''),
                            'source_type': DeviceType.FIREWALL.value,
                            'interface_type': iface_key.replace('_interface', ''),
                            'orphaned': iface.get('orphaned', False)
                        })
    except Exception as e:
        logger.warning(f"Error loading user firewalls: {e}")

    # 5. Load user-added VeloCloud devices (Edge, Gateway, Orchestrator)
    if user_velo_path:
        try:
            velo_data = load_user_velo(user_velo_path)
            for velo_entry in velo_data.get('devices', []) or []:
                for velo_name, velo_info in velo_entry.items():
                    # Map device_type to DeviceType enum
                    velo_device_type = velo_info.get('device_type', 'edge').lower()
                    device_type_map = {
                        'edge': DeviceType.VELO_EDGE.value,
                        'gateway': DeviceType.VELO_GATEWAY.value,
                        'orchestrator': DeviceType.VELO_ORCHESTRATOR.value
                    }
                    mapped_type = device_type_map.get(velo_device_type, DeviceType.VELO_EDGE.value)

                    # Build neighbor list from connections
                    neighbors = []
                    for conn in velo_info.get('connections', []) or []:
                        if conn.get('target_device'):
                            neighbors.append({
                                'port': conn.get('local_port', ''),
                                'neighborDevice': conn.get('target_device'),
                                'neighborPort': conn.get('target_port', '')
                            })

                    device = UnifiedDevice(
                        name=velo_name,
                        ip=velo_info.get('mgmt_ip', ''),
                        device_type=mapped_type,
                        device_category=DeviceCategory.SDWAN.value,
                        user_added=True,
                        status=velo_info.get('status', 'active'),
                        neighbors=neighbors if neighbors else None,
                        extra={
                            'velo_device_type': velo_device_type,
                            'interface_ips': velo_info.get('interface_ips', {}),
                            'source': 'user_velo'
                        }
                    )
                    devices.append(device.to_dict())

                    # Extract connections
                    for conn in velo_info.get('connections', []) or []:
                        if conn.get('target_device'):
                            connections.append({
                                'source_device': velo_name,
                                'source_port': conn.get('local_port', ''),
                                'target_device': conn.get('target_device'),
                                'target_port': conn.get('target_port', ''),
                                'source_type': mapped_type
                            })
        except Exception as e:
            logger.warning(f"Error loading user VeloCloud devices: {e}")

    # Build summary
    summary = {
        'total_devices': len(devices),
        'topology_nodes': len([d for d in devices if not d['user_added'] and d['device_type'] == DeviceType.VEOS.value]),
        'user_nodes': len([d for d in devices if d['user_added'] and d['device_type'] == DeviceType.VEOS.value]),
        'user_hosts': len([d for d in devices if d['device_type'] == DeviceType.LINUX_HOST.value]),
        'user_firewalls': len([d for d in devices if d['device_type'] == DeviceType.FIREWALL.value]),
        'user_velocloud': len([d for d in devices if d['device_category'] == DeviceCategory.SDWAN.value]),
        'total_connections': len(connections),
        'orphaned_connections': len([c for c in connections if c.get('orphaned')])
    }

    return {
        'devices': devices,
        'connections': connections,
        'summary': summary
    }


def get_device_by_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str,
    user_velo_path: str = None
) -> Optional[Dict]:
    """
    Get a specific device by name from the unified topology.

    Args:
        name: Device name to find
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml
        user_velo_path: Path to user_velo.yaml (optional)

    Returns:
        Device dict if found, None otherwise
    """
    topology = get_unified_topology(
        topo_build_path, user_nodes_path, user_hosts_path, user_firewalls_path,
        user_velo_path
    )

    name_lower = name.lower()
    for device in topology['devices']:
        if device['name'].lower() == name_lower:
            return device

    return None


def get_all_device_names_unified(
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str,
    user_velo_path: str = None
) -> Set[str]:
    """
    Get all device names from unified topology.

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml
        user_velo_path: Path to user_velo.yaml (optional)

    Returns:
        Set of device names (lowercase for case-insensitive comparison)
    """
    topology = get_unified_topology(
        topo_build_path, user_nodes_path, user_hosts_path, user_firewalls_path,
        user_velo_path
    )

    return {device['name'].lower() for device in topology['devices']}
