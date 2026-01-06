"""
Validation utilities for Nodebuilder Service

Handles:
- Parsing dnsmasq configuration for available IPs and MACs
- Validating device names for uniqueness
- Checking IP availability
"""

import os
import re
from typing import Dict, List, Optional, Set, Tuple

from ruamel.yaml import YAML


def parse_dnsmasq_config(path: str = '/etc/NetworkManager/dnsmasq.d/atd.conf') -> List[Dict]:
    """
    Parse dnsmasq DHCP host entries.

    Format: dhcp-host=MAC,IP,HOSTNAME (comma-separated)
    Example: dhcp-host=00:1c:73:18:c6:01,192.168.0.78,eos69

    Args:
        path: Path to dnsmasq config file

    Returns:
        List of dicts with 'mac', 'ip', 'hostname' keys
    """
    entries = []
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('dhcp-host='):
                    # Remove prefix and split on comma
                    content = line.replace('dhcp-host=', '')
                    parts = content.split(',')
                    if len(parts) >= 2:
                        mac = parts[0].strip()
                        ip = parts[1].strip()
                        hostname = parts[2].strip() if len(parts) > 2 else ''
                        entries.append({
                            'mac': mac,
                            'ip': ip,
                            'hostname': hostname
                        })
    except FileNotFoundError:
        pass
    except Exception as e:
        raise RuntimeError(f"Error parsing dnsmasq config: {e}")

    return entries


def get_topo_nodes(topo_build_path: str) -> List[Dict]:
    """
    Get all nodes from topo_build.yml

    Args:
        topo_build_path: Path to topo_build.yml

    Returns:
        List of node dicts from topology
    """
    yaml = YAML()
    nodes = []

    try:
        with open(topo_build_path, 'r') as f:
            topo = yaml.load(f)
            if topo and 'nodes' in topo:
                for node_entry in topo['nodes']:
                    # Each node is a dict with device name as key
                    for device_name, device_info in node_entry.items():
                        nodes.append({
                            'name': device_name,
                            'ip_addr': device_info.get('ip_addr', ''),
                            'sys_mac': device_info.get('sys_mac', ''),
                            'neighbors': device_info.get('neighbors', [])
                        })
    except FileNotFoundError:
        pass
    except Exception as e:
        raise RuntimeError(f"Error parsing topo_build.yml: {e}")

    return nodes


def get_user_nodes(user_nodes_path: str) -> List[Dict]:
    """
    Get all nodes from user_nodes.yaml

    Args:
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        List of node dicts from user additions
    """
    yaml = YAML()
    nodes = []

    try:
        with open(user_nodes_path, 'r') as f:
            data = yaml.load(f)
            if data and 'nodes' in data:
                for node_entry in data['nodes']:
                    # Each node is a dict with device name as key
                    for device_name, device_info in node_entry.items():
                        nodes.append({
                            'name': device_name,
                            'ip_addr': device_info.get('ip_addr', ''),
                            'sys_mac': device_info.get('sys_mac', ''),
                            'neighbors': device_info.get('neighbors', []),
                            'user_added': True
                        })
    except FileNotFoundError:
        pass
    except Exception as e:
        raise RuntimeError(f"Error parsing user_nodes.yaml: {e}")

    return nodes


def get_all_nodes(topo_build_path: str, user_nodes_path: str) -> List[Dict]:
    """
    Get all nodes from both topo_build.yml and user_nodes.yaml

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        Combined list of all nodes
    """
    topo_nodes = get_topo_nodes(topo_build_path)
    user_nodes = get_user_nodes(user_nodes_path)
    return topo_nodes + user_nodes


def get_existing_node_names(topo_build_path: str, user_nodes_path: str) -> Set[str]:
    """
    Get set of all existing node names

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        Set of device names
    """
    all_nodes = get_all_nodes(topo_build_path, user_nodes_path)
    return {node['name'].lower() for node in all_nodes}


def get_used_ips(topo_build_path: str, user_nodes_path: str) -> Set[str]:
    """
    Get set of all IPs currently in use

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        Set of IP addresses in use
    """
    all_nodes = get_all_nodes(topo_build_path, user_nodes_path)
    return {node['ip_addr'] for node in all_nodes if node.get('ip_addr')}


def get_available_ips(
    dnsmasq_path: str,
    topo_build_path: str,
    user_nodes_path: str,
    min_last_octet: int = 10
) -> List[Dict]:
    """
    Get list of available IPs from dnsmasq that are not in use

    Args:
        dnsmasq_path: Path to dnsmasq config
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        min_last_octet: Minimum value for the last octet (default 10).
            IPs with last octet below this are reserved for infrastructure
            (e.g., CVP uses low IPs like .5)

    Returns:
        List of available IP entries with 'ip', 'mac', 'hostname'
    """
    # Get all entries from dnsmasq
    dnsmasq_entries = parse_dnsmasq_config(dnsmasq_path)

    # Get IPs already in use
    used_ips = get_used_ips(topo_build_path, user_nodes_path)

    # Filter to only available IPs, excluding reserved low IPs
    available = []
    for entry in dnsmasq_entries:
        ip = entry['ip']
        # Skip IPs already in use
        if ip in used_ips:
            continue
        # Skip reserved low IPs (infrastructure like CVP)
        try:
            last_octet = int(ip.split('.')[-1])
            if last_octet < min_last_octet:
                continue
        except (ValueError, IndexError):
            # If we can't parse the IP, skip it
            continue
        available.append(entry)

    return available


def get_mac_for_ip(ip: str, dnsmasq_path: str) -> Optional[str]:
    """
    Get MAC address for a given IP from dnsmasq config

    Args:
        ip: IP address to look up
        dnsmasq_path: Path to dnsmasq config

    Returns:
        MAC address if found, None otherwise
    """
    entries = parse_dnsmasq_config(dnsmasq_path)

    for entry in entries:
        if entry['ip'] == ip:
            return entry['mac']

    return None


# Security: Reserved names that cannot be used for devices
# Includes system bridge names, common shell commands, and special identifiers
# Also includes phantom host names (host5-8) that exist in some topology neighbor
# lists but don't have actual node definitions - these are reserved interface slots
RESERVED_NAMES = frozenset([
    'all', 'default', 'none', 'null', 'localhost', 'host',
    'vmgmt', 'br0', 'docker0', 'virbr0', 'lo', 'eth0',
    'root', 'admin', 'system', 'test', 'true', 'false',
    # Phantom hosts - exist in neighbor lists but not as actual nodes
    # These reserve interface slots on leaf switches (e.g., leaf1:Ethernet8)
    'host5', 'host6', 'host7', 'host8',
])


def validate_device_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str = None,
    user_firewalls_path: str = None
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a device name is valid and unique across ALL device types.

    Security considerations:
    - Prevents shell metacharacter injection
    - Prevents reserved name collisions
    - Case-insensitive uniqueness check across nodes, hosts, and firewalls

    Args:
        name: Device name to validate
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml (optional for backward compat)
        user_firewalls_path: Path to user_firewalls.yaml (optional for backward compat)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name:
        return False, "Device name is required"

    # Check length first (prevents regex DoS)
    if len(name) > 32:
        return False, "Device name must be 32 characters or less"

    if len(name) < 2:
        return False, "Device name must be at least 2 characters"

    # Check format - alphanumeric, underscore only (no hyphens at start)
    # Security: Stricter pattern to prevent command-line flag confusion
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*[a-zA-Z0-9]$|^[a-zA-Z][a-zA-Z0-9]$', name):
        return False, "Device name must start with a letter, end with letter/number, and contain only letters, numbers, and underscores"

    # Check for reserved names (case-insensitive)
    if name.lower() in RESERVED_NAMES:
        return False, f"'{name}' is a reserved name and cannot be used"

    # Check uniqueness across ALL device types (case-insensitive)
    # Use comprehensive check if paths are provided, otherwise fall back to nodes only
    if user_hosts_path and user_firewalls_path:
        existing_names = get_all_device_names(
            topo_build_path, user_nodes_path, user_hosts_path, user_firewalls_path
        )
    else:
        existing_names = get_existing_node_names(topo_build_path, user_nodes_path)

    if name.lower() in existing_names:
        return False, "This device name is already in use"

    return True, None


def get_all_connections(topo_build_path: str, user_nodes_path: str) -> List[Tuple[str, str, str, str]]:
    """
    Get all existing connections from topology and user nodes.

    Returns tuples of (source_device, source_port, target_device, target_port)
    to enable duplicate detection.

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        List of connection tuples
    """
    connections = []
    all_nodes = get_all_nodes(topo_build_path, user_nodes_path)

    for node in all_nodes:
        source_device = node['name']
        for neighbor in node.get('neighbors', []):
            source_port = neighbor.get('port', '')
            target_device = neighbor.get('neighborDevice', '')
            target_port = neighbor.get('neighborPort', '')

            if source_port and target_device and target_port:
                connections.append((
                    source_device.lower(),
                    source_port.lower(),
                    target_device.lower(),
                    target_port.lower()
                ))

    return connections


def validate_connection_unique(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    topo_build_path: str,
    user_nodes_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a connection doesn't already exist.

    Checks for duplicate connections in both directions:
    - A:port1 -> B:port2
    - B:port2 -> A:port1

    Also checks that neither port is already in use.

    Args:
        source_device: Source device name
        source_port: Source port (e.g., "Ethernet1")
        target_device: Target device name
        target_port: Target port (e.g., "Ethernet3")
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        Tuple of (is_valid, error_message)
    """
    existing_connections = get_all_connections(topo_build_path, user_nodes_path)

    # Normalize inputs for case-insensitive comparison
    src_dev = source_device.lower()
    src_port = source_port.lower()
    tgt_dev = target_device.lower()
    tgt_port = target_port.lower()

    # Check if this exact connection exists
    if (src_dev, src_port, tgt_dev, tgt_port) in existing_connections:
        return False, f"Connection {source_device}:{source_port} -> {target_device}:{target_port} already exists"

    # Check reverse direction
    if (tgt_dev, tgt_port, src_dev, src_port) in existing_connections:
        return False, f"Connection already exists in reverse direction"

    # Check if source port is already in use
    for conn in existing_connections:
        if conn[0] == src_dev and conn[1] == src_port:
            return False, f"{source_device}:{source_port} is already connected to {conn[2]}:{conn[3]}"
        if conn[2] == src_dev and conn[3] == src_port:
            return False, f"{source_device}:{source_port} is already connected to {conn[0]}:{conn[1]}"

    # Check if target port is already in use
    for conn in existing_connections:
        if conn[0] == tgt_dev and conn[1] == tgt_port:
            return False, f"{target_device}:{target_port} is already connected to {conn[2]}:{conn[3]}"
        if conn[2] == tgt_dev and conn[3] == tgt_port:
            return False, f"{target_device}:{target_port} is already connected to {conn[0]}:{conn[1]}"

    return True, None


def validate_target_device_exists(
    target_device: str,
    topo_build_path: str,
    user_nodes_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a target device exists.

    Args:
        target_device: Name of the target device
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        Tuple of (exists, error_message)
    """
    existing_names = get_existing_node_names(topo_build_path, user_nodes_path)

    if target_device.lower() not in existing_names:
        return False, f"Target device '{target_device}' does not exist"

    return True, None


def generate_unique_cluster_prefix(
    base_prefix: str,
    template_nodes: list,
    topo_build_path: str,
    user_nodes_path: str,
    max_attempts: int = 100
) -> str:
    """
    Generate a unique prefix for cluster nodes to avoid name conflicts.

    If the base prefix results in conflicts, appends _2, _3, etc. until
    a unique set of names is found.

    Args:
        base_prefix: User-provided prefix (can be empty string)
        template_nodes: List of node templates with name_suffix attribute
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        max_attempts: Maximum number of suffix attempts

    Returns:
        A prefix that results in unique node names for all template nodes

    Raises:
        ValueError: If no unique prefix found within max_attempts
    """
    existing_names = get_existing_node_names(topo_build_path, user_nodes_path)

    def names_are_unique(prefix: str) -> bool:
        """Check if all node names with this prefix are unique."""
        for node in template_nodes:
            if prefix:
                full_name = f"{prefix}_{node.name_suffix}"
            else:
                full_name = node.name_suffix
            if full_name.lower() in existing_names:
                return False
        return True

    # Try the original prefix first
    if names_are_unique(base_prefix):
        return base_prefix

    # Try incrementing suffixes: prefix_2, prefix_3, etc.
    # If base_prefix is empty, use numeric prefix: 2, 3, etc.
    for i in range(2, max_attempts + 2):
        if base_prefix:
            candidate = f"{base_prefix}_{i}"
        else:
            candidate = str(i)

        if names_are_unique(candidate):
            return candidate

    raise ValueError(
        f"Could not generate unique cluster names after {max_attempts} attempts. "
        f"Consider using a different prefix."
    )


# ============================================================================
# Host and Firewall Validation
# ============================================================================

def validate_host_limit(user_hosts_path: str, max_hosts: int = 2) -> Tuple[bool, Optional[str]]:
    """
    Validate that the host limit has not been reached.

    Args:
        user_hosts_path: Path to user_hosts.yaml
        max_hosts: Maximum allowed hosts per topology

    Returns:
        Tuple of (is_valid, error_message)
    """
    from persistence import list_user_hosts

    current_hosts = list_user_hosts(user_hosts_path)
    if len(current_hosts) >= max_hosts:
        return False, f"Maximum of {max_hosts} Linux hosts per topology reached"

    return True, None


def validate_firewall_limit(user_firewalls_path: str, max_firewalls: int = 1) -> Tuple[bool, Optional[str]]:
    """
    Validate that the firewall limit has not been reached.

    Args:
        user_firewalls_path: Path to user_firewalls.yaml
        max_firewalls: Maximum allowed firewalls per topology

    Returns:
        Tuple of (is_valid, error_message)
    """
    from persistence import list_user_firewalls

    current_firewalls = list_user_firewalls(user_firewalls_path)
    if len(current_firewalls) >= max_firewalls:
        return False, f"Maximum of {max_firewalls} firewall per topology reached"

    return True, None


def validate_host_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate a host name for uniqueness across all device types.

    Args:
        name: Device name to validate
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Use the unified validation which now checks ALL device types
    return validate_device_name(
        name, topo_build_path, user_nodes_path, user_hosts_path, user_firewalls_path
    )


def validate_firewall_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate a firewall name for uniqueness across all device types.

    Args:
        name: Device name to validate
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Same validation as host name - they share namespace
    return validate_host_name(
        name, topo_build_path, user_nodes_path,
        user_hosts_path, user_firewalls_path
    )


def validate_cidr_ip(ip_with_cidr: str) -> Tuple[bool, Optional[str]]:
    """
    Validate an IP address in CIDR notation.

    Args:
        ip_with_cidr: IP address with CIDR (e.g., "10.1.1.1/24")

    Returns:
        Tuple of (is_valid, error_message)
    """
    import re

    if not ip_with_cidr:
        return False, "IP address is required"

    # Pattern for IP/CIDR
    cidr_pattern = r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$'
    if not re.match(cidr_pattern, ip_with_cidr):
        return False, f"Invalid CIDR format: {ip_with_cidr}. Expected format: x.x.x.x/xx"

    # Validate IP octets
    parts = ip_with_cidr.split('/')
    ip = parts[0]
    prefix = int(parts[1])

    octets = ip.split('.')
    for octet in octets:
        val = int(octet)
        if val < 0 or val > 255:
            return False, f"Invalid IP octet: {octet}"

    # Validate prefix length
    if prefix < 1 or prefix > 32:
        return False, f"Invalid prefix length: {prefix}. Must be 1-32"

    return True, None


def get_all_device_names(
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str,
    user_velo_path: str = None
) -> Set[str]:
    """
    Get all device names across all types (topology, nodes, hosts, firewalls, velo).

    Args:
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml
        user_velo_path: Path to user_velo.yaml (optional)

    Returns:
        Set of all device names (lowercase)
    """
    from persistence import list_user_hosts, list_user_firewalls, list_user_velo_devices

    # Get existing node names
    names = get_existing_node_names(topo_build_path, user_nodes_path)

    # Add host names
    for host in list_user_hosts(user_hosts_path):
        for host_name in host.keys():
            names.add(host_name.lower())

    # Add firewall names
    for fw in list_user_firewalls(user_firewalls_path):
        for fw_name in fw.keys():
            names.add(fw_name.lower())

    # Add VeloCloud device names
    if user_velo_path:
        for velo in list_user_velo_devices(user_velo_path):
            names.add(velo.get('name', '').lower())

    return names


# ============================================================================
# VeloCloud Validation
# ============================================================================

# Valid VeloCloud device types
VALID_VELO_DEVICE_TYPES = frozenset(['edge', 'gateway', 'orchestrator'])


def validate_velo_device_type(device_type: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that the VeloCloud device type is valid.

    Args:
        device_type: Device type to validate (edge, gateway, orchestrator)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not device_type:
        return False, "VeloCloud device type is required"

    if device_type.lower() not in VALID_VELO_DEVICE_TYPES:
        valid_types = ', '.join(sorted(VALID_VELO_DEVICE_TYPES))
        return False, f"Invalid VeloCloud device type: {device_type}. Valid types: {valid_types}"

    return True, None


def validate_velo_limit(
    device_type: str,
    user_velo_path: str,
    max_edge: int = 2,
    max_gateway: int = 1,
    max_orchestrator: int = 1
) -> Tuple[bool, Optional[str]]:
    """
    Validate that the VeloCloud device limit for the specified type has not been reached.

    Args:
        device_type: Device type (edge, gateway, orchestrator)
        user_velo_path: Path to user_velo.yaml
        max_edge: Maximum Edge devices per topology
        max_gateway: Maximum Gateway devices per topology
        max_orchestrator: Maximum Orchestrator devices per topology

    Returns:
        Tuple of (is_valid, error_message)
    """
    from persistence import get_velo_device_count_by_type

    device_type_lower = device_type.lower()

    # Validate device type first
    valid, error = validate_velo_device_type(device_type)
    if not valid:
        return False, error

    current_count = get_velo_device_count_by_type(device_type_lower, user_velo_path)

    limits = {
        'edge': max_edge,
        'gateway': max_gateway,
        'orchestrator': max_orchestrator
    }

    max_allowed = limits.get(device_type_lower, 1)

    if current_count >= max_allowed:
        type_name = device_type_lower.capitalize()
        return False, f"Maximum of {max_allowed} VeloCloud {type_name} device(s) per topology reached"

    return True, None


def validate_velo_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str,
    user_hosts_path: str,
    user_firewalls_path: str,
    user_velo_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate a VeloCloud device name for uniqueness across all device types.

    Args:
        name: Device name to validate
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml
        user_firewalls_path: Path to user_firewalls.yaml
        user_velo_path: Path to user_velo.yaml

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name:
        return False, "VeloCloud device name is required"

    # Check length first (prevents regex DoS)
    if len(name) > 32:
        return False, "Device name must be 32 characters or less"

    if len(name) < 2:
        return False, "Device name must be at least 2 characters"

    # Check format - alphanumeric, underscore only (no hyphens at start)
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*[a-zA-Z0-9]$|^[a-zA-Z][a-zA-Z0-9]$', name):
        return False, "Device name must start with a letter, end with letter/number, and contain only letters, numbers, and underscores"

    # Check for reserved names (case-insensitive)
    if name.lower() in RESERVED_NAMES:
        return False, f"'{name}' is a reserved name and cannot be used"

    # Check uniqueness across ALL device types including VeloCloud
    existing_names = get_all_device_names(
        topo_build_path, user_nodes_path, user_hosts_path,
        user_firewalls_path, user_velo_path
    )

    if name.lower() in existing_names:
        return False, "This device name is already in use"

    return True, None


def validate_velo_enabled() -> Tuple[bool, Optional[str]]:
    """
    Validate that VeloCloud feature is enabled.

    Returns:
        Tuple of (is_enabled, error_message)
    """
    from config import is_velo_enabled

    if not is_velo_enabled():
        return False, "VeloCloud feature is not enabled for this topology"

    return True, None


def validate_velo_device_type_enabled(device_type: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that a specific VeloCloud device type is enabled.

    Args:
        device_type: Device type to check (edge, gateway, orchestrator)

    Returns:
        Tuple of (is_enabled, error_message)
    """
    from config import get_velo_config

    # First check that VeloCloud is enabled overall
    valid, error = validate_velo_enabled()
    if not valid:
        return False, error

    # Validate the device type
    valid, error = validate_velo_device_type(device_type)
    if not valid:
        return False, error

    config = get_velo_config()
    device_type_lower = device_type.lower()

    # Config structure is nested: config['edge']['enabled'], config['gateway']['enabled'], etc.
    device_config = config.get(device_type_lower, {})
    if not device_config.get('enabled', False):
        type_name = device_type_lower.capitalize()
        return False, f"VeloCloud {type_name} devices are not enabled for this topology"

    return True, None
