"""
Persistence layer for Nodebuilder Service

Handles reading and writing user-added nodes to /etc/atd/user_nodes.yaml

The user_nodes.yaml file stores nodes that users have dynamically added
to running labs. This file is separate from topo_build.yml and is merged
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
