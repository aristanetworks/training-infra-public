"""
Persistence layer for Nodebuilder Service

Handles reading and writing user-added resources:
- user_nodes.yaml: vEOS nodes added by users
- user_hosts.yaml: Linux desktop hosts added by users
- user_firewalls.yaml: VyOS firewalls added by users

These files are separate from topo_build.yml and are merged
at read time by the topology API.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ruamel.yaml import YAML

logger = logging.getLogger('nodebuilder')

# Default path for user nodes file
DEFAULT_USER_NODES_PATH = '/etc/atd/user_nodes.yaml'

# Security: Allowed base directories for file operations
ALLOWED_PATH_PREFIXES = ('/etc/atd/',)


def _validate_path(path: str) -> bool:
    """
    Validate that a path is within allowed directories.

    Security: Prevents path traversal attacks.

    Args:
        path: Path to validate

    Returns:
        True if path is allowed
    """
    abs_path = os.path.abspath(path)
    return any(abs_path.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES)


def get_empty_user_nodes() -> Dict:
    """
    Get the structure for an empty user_nodes.yaml file.

    Returns:
        Dict with initialized structure
    """
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'nodes': []
    }


def load_user_nodes(path: str = DEFAULT_USER_NODES_PATH) -> Dict:
    """
    Load user-added nodes from persistence file.

    Creates an empty structure if file doesn't exist.

    Args:
        path: Path to user_nodes.yaml

    Returns:
        Dict with user nodes data
    """
    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_nodes()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_nodes()

        # Ensure required fields exist
        if 'nodes' not in data:
            data['nodes'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user nodes from {path}: {e}")
        return get_empty_user_nodes()


def save_user_nodes(data: Dict, path: str = DEFAULT_USER_NODES_PATH) -> bool:
    """
    Save user nodes data to persistence file.

    Uses atomic write (write to temp, then rename) for safety.

    Args:
        data: User nodes data dict
        path: Path to user_nodes.yaml

    Returns:
        True if successful

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.preserve_quotes = True

    # Update timestamp
    data['updated_at'] = datetime.now(timezone.utc).isoformat()

    # Ensure directory exists
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path)

    # Atomic write: write to temp file, then rename
    temp_path = f"{path}.tmp"

    try:
        with open(temp_path, 'w') as f:
            yaml.dump(data, f)

        # Rename is atomic on POSIX systems
        os.rename(temp_path, path)

        logger.info(f"Saved user nodes to {path}")
        return True

    except Exception as e:
        logger.error(f"Error saving user nodes to {path}: {e}")
        # Clean up temp file if it exists
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_node(node_data: Dict, path: str = DEFAULT_USER_NODES_PATH) -> bool:
    """
    Add a single user node to the persistence file.

    Args:
        node_data: Dict with device name as key and device info as value
            Example: {'newleaf5': {'ip_addr': '...', 'sys_mac': '...', ...}}
        path: Path to user_nodes.yaml

    Returns:
        True if successful
    """
    # Load existing data
    data = load_user_nodes(path)

    # Add timestamp to node
    for name, info in node_data.items():
        info['added_at'] = datetime.now(timezone.utc).isoformat()
        info['user_added'] = True

    # Append the new node
    data['nodes'].append(node_data)

    # Save back
    return save_user_nodes(data, path)


def remove_user_node(name: str, path: str = DEFAULT_USER_NODES_PATH) -> bool:
    """
    Remove a user-added node from the persistence file.

    Note: This does NOT remove the VM or clean up resources.
    That should be done separately.

    Args:
        name: Device name to remove
        path: Path to user_nodes.yaml

    Returns:
        True if node was found and removed
    """
    # Load existing data
    data = load_user_nodes(path)

    # Find and remove the node
    original_count = len(data['nodes'])
    data['nodes'] = [
        node for node in data['nodes']
        if name.lower() not in [k.lower() for k in node.keys()]
    ]

    if len(data['nodes']) == original_count:
        logger.warning(f"Node {name} not found in user nodes")
        return False

    # Save back
    save_user_nodes(data, path)
    logger.info(f"Removed node {name} from user nodes")
    return True


def get_user_node(name: str, path: str = DEFAULT_USER_NODES_PATH) -> Optional[Dict]:
    """
    Get a specific user-added node by name.

    Args:
        name: Device name to find
        path: Path to user_nodes.yaml

    Returns:
        Node info dict if found, None otherwise
    """
    data = load_user_nodes(path)

    for node in data['nodes']:
        for node_name, node_info in node.items():
            if node_name.lower() == name.lower():
                return {node_name: node_info}

    return None


def list_user_nodes(path: str = DEFAULT_USER_NODES_PATH) -> List[Dict]:
    """
    List all user-added nodes.

    Args:
        path: Path to user_nodes.yaml

    Returns:
        List of node dicts
    """
    data = load_user_nodes(path)
    return data.get('nodes', [])


def user_node_exists(name: str, path: str = DEFAULT_USER_NODES_PATH) -> bool:
    """
    Check if a user-added node exists.

    Args:
        name: Device name to check
        path: Path to user_nodes.yaml

    Returns:
        True if node exists
    """
    return get_user_node(name, path) is not None


def remove_neighbor_references(
    deleted_node_name: str,
    path: str = DEFAULT_USER_NODES_PATH
) -> int:
    """
    Remove all neighbor references to a deleted node from other user nodes.

    When a node is deleted, other nodes may still have it listed as a neighbor.
    This function cleans up those orphaned references.

    Args:
        deleted_node_name: Name of the node that was deleted
        path: Path to user_nodes.yaml

    Returns:
        Number of neighbor references removed
    """
    data = load_user_nodes(path)
    removed_count = 0

    for node_entry in data.get('nodes', []):
        for node_name, node_info in node_entry.items():
            neighbors = node_info.get('neighbors', [])
            original_count = len(neighbors)

            # Filter out references to the deleted node
            node_info['neighbors'] = [
                n for n in neighbors
                if n.get('neighborDevice', '').lower() != deleted_node_name.lower()
            ]

            removed_count += original_count - len(node_info['neighbors'])

    if removed_count > 0:
        save_user_nodes(data, path)
        logger.info(
            f"Removed {removed_count} neighbor reference(s) to deleted node "
            f"'{deleted_node_name}' from other user nodes"
        )

    return removed_count


# ============================================================================
# User Hosts Persistence (Linux Desktop VMs)
# ============================================================================

DEFAULT_USER_HOSTS_PATH = '/etc/atd/user_hosts.yaml'


def get_empty_user_hosts() -> Dict:
    """Get the structure for an empty user_hosts.yaml file."""
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'hosts': []
    }


def load_user_hosts(path: str = DEFAULT_USER_HOSTS_PATH) -> Dict:
    """
    Load user-added Linux hosts from persistence file.

    Args:
        path: Path to user_hosts.yaml

    Returns:
        Dict with user hosts data
    """
    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_hosts()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_hosts()

        if 'hosts' not in data:
            data['hosts'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user hosts from {path}: {e}")
        return get_empty_user_hosts()


def save_user_hosts(data: Dict, path: str = DEFAULT_USER_HOSTS_PATH) -> bool:
    """Save user hosts data to persistence file."""
    if not _validate_path(path):
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.preserve_quotes = True

    data['updated_at'] = datetime.now(timezone.utc).isoformat()

    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path)

    temp_path = f"{path}.tmp"

    try:
        with open(temp_path, 'w') as f:
            yaml.dump(data, f)
        os.rename(temp_path, path)
        logger.info(f"Saved user hosts to {path}")
        return True
    except Exception as e:
        logger.error(f"Error saving user hosts to {path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_host(host_data: Dict, path: str = DEFAULT_USER_HOSTS_PATH) -> bool:
    """Add a single user host to the persistence file."""
    data = load_user_hosts(path)

    for name, info in host_data.items():
        info['added_at'] = datetime.now(timezone.utc).isoformat()
        info['user_added'] = True
        info['device_type'] = 'host'

    data['hosts'].append(host_data)
    return save_user_hosts(data, path)


def remove_user_host(name: str, path: str = DEFAULT_USER_HOSTS_PATH) -> bool:
    """Remove a user-added host from the persistence file."""
    data = load_user_hosts(path)
    original_count = len(data['hosts'])

    data['hosts'] = [
        host for host in data['hosts']
        if name.lower() not in [k.lower() for k in host.keys()]
    ]

    if len(data['hosts']) == original_count:
        logger.warning(f"Host {name} not found in user hosts")
        return False

    save_user_hosts(data, path)
    logger.info(f"Removed host {name} from user hosts")
    return True


def get_user_host(name: str, path: str = DEFAULT_USER_HOSTS_PATH) -> Optional[Dict]:
    """Get a specific user-added host by name."""
    data = load_user_hosts(path)

    for host in data['hosts']:
        for host_name, host_info in host.items():
            if host_name.lower() == name.lower():
                return {host_name: host_info}

    return None


def list_user_hosts(path: str = DEFAULT_USER_HOSTS_PATH) -> List[Dict]:
    """List all user-added hosts."""
    data = load_user_hosts(path)
    return data.get('hosts', [])


# ============================================================================
# User Firewalls Persistence (VyOS VMs)
# ============================================================================

DEFAULT_USER_FIREWALLS_PATH = '/etc/atd/user_firewalls.yaml'


def get_empty_user_firewalls() -> Dict:
    """Get the structure for an empty user_firewalls.yaml file."""
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'firewalls': []
    }


def load_user_firewalls(path: str = DEFAULT_USER_FIREWALLS_PATH) -> Dict:
    """
    Load user-added VyOS firewalls from persistence file.

    Args:
        path: Path to user_firewalls.yaml

    Returns:
        Dict with user firewalls data
    """
    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_firewalls()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_firewalls()

        if 'firewalls' not in data:
            data['firewalls'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user firewalls from {path}: {e}")
        return get_empty_user_firewalls()


def save_user_firewalls(data: Dict, path: str = DEFAULT_USER_FIREWALLS_PATH) -> bool:
    """Save user firewalls data to persistence file."""
    if not _validate_path(path):
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.preserve_quotes = True

    data['updated_at'] = datetime.now(timezone.utc).isoformat()

    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path)

    temp_path = f"{path}.tmp"

    try:
        with open(temp_path, 'w') as f:
            yaml.dump(data, f)
        os.rename(temp_path, path)
        logger.info(f"Saved user firewalls to {path}")
        return True
    except Exception as e:
        logger.error(f"Error saving user firewalls to {path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_firewall(firewall_data: Dict, path: str = DEFAULT_USER_FIREWALLS_PATH) -> bool:
    """Add a single user firewall to the persistence file."""
    data = load_user_firewalls(path)

    for name, info in firewall_data.items():
        info['added_at'] = datetime.now(timezone.utc).isoformat()
        info['user_added'] = True
        info['device_type'] = 'firewall'

    data['firewalls'].append(firewall_data)
    return save_user_firewalls(data, path)


def remove_user_firewall(name: str, path: str = DEFAULT_USER_FIREWALLS_PATH) -> bool:
    """Remove a user-added firewall from the persistence file."""
    data = load_user_firewalls(path)
    original_count = len(data['firewalls'])

    data['firewalls'] = [
        fw for fw in data['firewalls']
        if name.lower() not in [k.lower() for k in fw.keys()]
    ]

    if len(data['firewalls']) == original_count:
        logger.warning(f"Firewall {name} not found in user firewalls")
        return False

    save_user_firewalls(data, path)
    logger.info(f"Removed firewall {name} from user firewalls")
    return True


def get_user_firewall(name: str, path: str = DEFAULT_USER_FIREWALLS_PATH) -> Optional[Dict]:
    """Get a specific user-added firewall by name."""
    data = load_user_firewalls(path)

    for fw in data['firewalls']:
        for fw_name, fw_info in fw.items():
            if fw_name.lower() == name.lower():
                return {fw_name: fw_info}

    return None


def list_user_firewalls(path: str = DEFAULT_USER_FIREWALLS_PATH) -> List[Dict]:
    """List all user-added firewalls."""
    data = load_user_firewalls(path)
    return data.get('firewalls', [])
