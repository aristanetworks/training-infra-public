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


def parse_dnsmasq_config(path: str = '/etc/dnsmasq.d/atd.conf') -> List[Dict]:
    """
    Parse dnsmasq DHCP host entries.

    Format: dhcp-host=MAC  IP  HOSTNAME (whitespace-separated after =)
    Example: dhcp-host=00:1c:73:b1:c6:01	192.168.0.11	eos2

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
                    # Remove prefix and split on whitespace
                    content = line.replace('dhcp-host=', '')
                    parts = content.split()
                    if len(parts) >= 2:
                        mac = parts[0]
                        ip = parts[1]
                        hostname = parts[2] if len(parts) > 2 else ''
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
    user_nodes_path: str
) -> List[Dict]:
    """
    Get list of available IPs from dnsmasq that are not in use

    Args:
        dnsmasq_path: Path to dnsmasq config
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

    Returns:
        List of available IP entries with 'ip', 'mac', 'hostname'
    """
    # Get all entries from dnsmasq
    dnsmasq_entries = parse_dnsmasq_config(dnsmasq_path)

    # Get IPs already in use
    used_ips = get_used_ips(topo_build_path, user_nodes_path)

    # Filter to only available IPs
    available = [
        entry for entry in dnsmasq_entries
        if entry['ip'] not in used_ips
    ]

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
RESERVED_NAMES = frozenset([
    'all', 'default', 'none', 'null', 'localhost', 'host',
    'vmgmt', 'br0', 'docker0', 'virbr0', 'lo', 'eth0',
    'root', 'admin', 'system', 'test', 'true', 'false',
])


def validate_device_name(
    name: str,
    topo_build_path: str,
    user_nodes_path: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a device name is valid and unique

    Security considerations:
    - Prevents shell metacharacter injection
    - Prevents reserved name collisions
    - Case-insensitive uniqueness check

    Args:
        name: Device name to validate
        topo_build_path: Path to topo_build.yml
        user_nodes_path: Path to user_nodes.yaml

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

    # Check uniqueness (case-insensitive)
    existing_names = get_existing_node_names(topo_build_path, user_nodes_path)
    if name.lower() in existing_names:
        return False, "This device name is already in use"

    return True, None
