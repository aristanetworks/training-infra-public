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
DEFAULT_USER_CLOUDEOS_PATH = '/etc/atd/user_cloudeos.yaml'
DEFAULT_USER_LINKS_PATH = '/etc/atd/user_links.yaml'

# Security: Allowed base directories for file operations
ALLOWED_PATH_PREFIXES = ('/etc/atd/',)


def _validate_path(path: str) -> bool:
    """
    Validate that a path is within allowed directories.

    Security: Prevents path traversal attacks by resolving symlinks
    and using commonpath comparison instead of string prefix matching.

    Args:
        path: Path to validate

    Returns:
        True if path is allowed
    """
    # Resolve symlinks and normalize the path to prevent traversal
    real_path = os.path.realpath(path)

    for prefix in ALLOWED_PATH_PREFIXES:
        # Ensure prefix is also resolved
        real_prefix = os.path.realpath(prefix)
        try:
            # commonpath returns the longest common sub-path
            # If the common path equals the prefix, then real_path is under prefix
            common = os.path.commonpath([real_path, real_prefix])
            if common == real_prefix:
                return True
        except ValueError:
            # Raised when paths are on different drives (Windows) or incompatible
            continue

    return False


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

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

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


def save_user_node_pending(
    name: str,
    node_info: Dict,
    path: str = DEFAULT_USER_NODES_PATH
) -> bool:
    """
    Save a user node with 'creating' status before VM creation.

    This implements the "save-before-create" pattern to prevent zombie VMs
    if the service crashes between VM creation and persistence save.

    Args:
        name: Device name
        node_info: Node info dict (ip_addr, sys_mac, etc.)
        path: Path to user_nodes.yaml

    Returns:
        True if successful
    """
    data = load_user_nodes(path)

    # Add pending status and timestamp
    node_info['added_at'] = datetime.now(timezone.utc).isoformat()
    node_info['user_added'] = True
    node_info['status'] = 'creating'

    # Append the new node
    data['nodes'].append({name: node_info})

    return save_user_nodes(data, path)


def update_user_node_status(
    name: str,
    status: str = 'active',
    additional_info: Optional[Dict] = None,
    path: str = DEFAULT_USER_NODES_PATH
) -> bool:
    """
    Update a user node's status after VM creation completes.

    Args:
        name: Device name
        status: New status ('active' or 'failed')
        additional_info: Optional dict to merge into node info (e.g., connections)
        path: Path to user_nodes.yaml

    Returns:
        True if node was found and updated
    """
    data = load_user_nodes(path)

    for node_entry in data.get('nodes', []):
        for node_name, node_info in node_entry.items():
            if node_name.lower() == name.lower():
                # Update status
                if status == 'active':
                    # Remove the 'creating' status - active nodes have no status field
                    node_info.pop('status', None)
                else:
                    node_info['status'] = status

                # Merge additional info if provided
                if additional_info:
                    node_info.update(additional_info)

                save_user_nodes(data, path)
                logger.info(f"Updated node {name} status to {status}")
                return True

    logger.warning(f"Node {name} not found for status update")
    return False


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


def remove_all_device_references(
    deleted_device_name: str,
    user_nodes_path: str = DEFAULT_USER_NODES_PATH,
    user_hosts_path: str = None,
    user_firewalls_path: str = None,
    user_cloudeos_path: str = None,
    user_links_path: str = None
) -> Dict:
    """
    Remove all references to a deleted device from ALL persistence files.

    This is a comprehensive cleanup that handles:
    1. Neighbor references in user_nodes.yaml
    2. Connection references in user_hosts.yaml (target_device field)
    3. Interface references in user_firewalls.yaml (inside/outside target_device)

    Args:
        deleted_device_name: Name of the device that was deleted
        user_nodes_path: Path to user_nodes.yaml
        user_hosts_path: Path to user_hosts.yaml (optional)
        user_firewalls_path: Path to user_firewalls.yaml (optional)

    Returns:
        Dict with counts of removed references by type
    """
    result = {
        'nodes_cleaned': 0,
        'hosts_cleaned': 0,
        'firewalls_cleaned': 0,
        'total': 0
    }

    deleted_lower = deleted_device_name.lower()

    # 1. Clean up neighbor references in user_nodes.yaml
    result['nodes_cleaned'] = remove_neighbor_references(
        deleted_device_name, user_nodes_path
    )

    # 2. Clean up connection references in user_hosts.yaml
    if user_hosts_path:
        try:
            hosts_data = load_user_hosts(user_hosts_path)
            hosts_modified = False

            for host_entry in hosts_data.get('hosts', []) or []:
                for host_name, host_info in host_entry.items():
                    connection = host_info.get('connection', {})
                    if connection.get('target_device', '').lower() == deleted_lower:
                        # Clear the connection - the target device no longer exists
                        logger.warning(
                            f"Host '{host_name}' was connected to deleted device "
                            f"'{deleted_device_name}'. Marking connection as orphaned."
                        )
                        host_info['connection']['orphaned'] = True
                        host_info['connection']['orphaned_target'] = deleted_device_name
                        result['hosts_cleaned'] += 1
                        hosts_modified = True

            if hosts_modified:
                save_user_hosts(hosts_data, user_hosts_path)
                logger.info(
                    f"Marked {result['hosts_cleaned']} host connection(s) as orphaned "
                    f"due to deleted device '{deleted_device_name}'"
                )
        except Exception as e:
            logger.warning(f"Error cleaning host references: {e}")

    # 3. Clean up interface references in user_firewalls.yaml
    if user_firewalls_path:
        try:
            firewalls_data = load_user_firewalls(user_firewalls_path)
            firewalls_modified = False

            for fw_entry in firewalls_data.get('firewalls', []) or []:
                for fw_name, fw_info in fw_entry.items():
                    for iface_key in ['inside_interface', 'outside_interface']:
                        iface = fw_info.get(iface_key, {})
                        if iface.get('target_device', '').lower() == deleted_lower:
                            logger.warning(
                                f"Firewall '{fw_name}' {iface_key} was connected to "
                                f"deleted device '{deleted_device_name}'. Marking as orphaned."
                            )
                            iface['orphaned'] = True
                            iface['orphaned_target'] = deleted_device_name
                            result['firewalls_cleaned'] += 1
                            firewalls_modified = True

            if firewalls_modified:
                save_user_firewalls(firewalls_data, user_firewalls_path)
                logger.info(
                    f"Marked {result['firewalls_cleaned']} firewall interface(s) as orphaned "
                    f"due to deleted device '{deleted_device_name}'"
                )
        except Exception as e:
            logger.warning(f"Error cleaning firewall references: {e}")

    # 4. Clean up neighbor references in user_cloudeos.yaml
    result['cloudeos_cleaned'] = 0
    if user_cloudeos_path:
        try:
            cloudeos_data = load_user_cloudeos(user_cloudeos_path)
            cloudeos_modified = False

            for ce_entry in cloudeos_data.get('devices', []) or []:
                for ce_name, ce_info in ce_entry.items():
                    neighbors = ce_info.get('neighbors', [])
                    original_count = len(neighbors)
                    ce_info['neighbors'] = [
                        n for n in neighbors
                        if n.get('neighborDevice', '').lower() != deleted_lower
                    ]
                    removed = original_count - len(ce_info['neighbors'])
                    if removed > 0:
                        result['cloudeos_cleaned'] += removed
                        cloudeos_modified = True

            if cloudeos_modified:
                save_user_cloudeos(cloudeos_data, user_cloudeos_path)
        except Exception as e:
            logger.warning(f"Error cleaning CloudEOS references: {e}")

    # 5. Clean up user links referencing the deleted device
    result['links_cleaned'] = 0
    if user_links_path:
        try:
            links_data = load_user_links(user_links_path)
            original_count = len(links_data.get('links', []))
            links_data['links'] = [
                link for link in links_data.get('links', [])
                if (link.get('source_device', '').lower() != deleted_lower
                    and link.get('target_device', '').lower() != deleted_lower)
            ]
            removed = original_count - len(links_data['links'])
            if removed > 0:
                result['links_cleaned'] = removed
                save_user_links(links_data, user_links_path)
        except Exception as e:
            logger.warning(f"Error cleaning link references: {e}")

    result['total'] = (
        result['nodes_cleaned'] +
        result['hosts_cleaned'] +
        result['firewalls_cleaned'] +
        result['cloudeos_cleaned'] +
        result['links_cleaned']
    )

    if result['total'] > 0:
        logger.info(
            f"Cross-type cleanup for '{deleted_device_name}': "
            f"{result['nodes_cleaned']} node refs, "
            f"{result['hosts_cleaned']} host refs, "
            f"{result['firewalls_cleaned']} firewall refs, "
            f"{result['cloudeos_cleaned']} CloudEOS refs, "
            f"{result['links_cleaned']} link refs"
        )

    return result


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

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_hosts()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_hosts()

        if 'hosts' not in data or data['hosts'] is None:
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


def save_user_host_pending(
    name: str,
    host_info: Dict,
    path: str = DEFAULT_USER_HOSTS_PATH
) -> bool:
    """
    Save a user host with 'creating' status before VM creation.

    Args:
        name: Host name
        host_info: Host info dict
        path: Path to user_hosts.yaml

    Returns:
        True if successful
    """
    data = load_user_hosts(path)

    host_info['added_at'] = datetime.now(timezone.utc).isoformat()
    host_info['user_added'] = True
    host_info['device_type'] = 'host'
    host_info['status'] = 'creating'

    data['hosts'].append({name: host_info})
    return save_user_hosts(data, path)


def update_user_host_status(
    name: str,
    status: str = 'active',
    additional_info: Optional[Dict] = None,
    path: str = DEFAULT_USER_HOSTS_PATH
) -> bool:
    """
    Update a user host's status after VM creation completes.

    Args:
        name: Host name
        status: New status ('active' or 'failed')
        additional_info: Optional dict to merge into host info
        path: Path to user_hosts.yaml

    Returns:
        True if host was found and updated
    """
    data = load_user_hosts(path)

    for host_entry in data.get('hosts', []) or []:
        for host_name, host_info in host_entry.items():
            if host_name.lower() == name.lower():
                if status == 'active':
                    host_info.pop('status', None)
                else:
                    host_info['status'] = status

                if additional_info:
                    host_info.update(additional_info)

                save_user_hosts(data, path)
                logger.info(f"Updated host {name} status to {status}")
                return True

    logger.warning(f"Host {name} not found for status update")
    return False


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
    # Handle case where hosts key exists but value is None
    return data.get('hosts') or []


def cleanup_stale_user_hosts(path: str = DEFAULT_USER_HOSTS_PATH) -> int:
    """
    Remove stale host entries with 'creating' or 'failed' status.

    These entries are orphaned from crashed or failed creations that
    didn't properly clean up. Called on service startup.

    Args:
        path: Path to user_hosts.yaml

    Returns:
        Number of stale entries removed
    """
    data = load_user_hosts(path)
    original_count = len(data.get('hosts') or [])

    stale_statuses = {'creating', 'failed'}
    cleaned_hosts = []
    removed_names = []

    for host_entry in data.get('hosts') or []:
        keep = True
        for host_name, host_info in host_entry.items():
            status = host_info.get('status')
            if status in stale_statuses:
                keep = False
                removed_names.append(host_name)
                break
        if keep:
            cleaned_hosts.append(host_entry)

    removed_count = original_count - len(cleaned_hosts)

    if removed_count > 0:
        data['hosts'] = cleaned_hosts
        save_user_hosts(data, path)
        logger.info(f"Cleaned up {removed_count} stale host(s): {removed_names}")

    return removed_count


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

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_firewalls()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_firewalls()

        if 'firewalls' not in data or data['firewalls'] is None:
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


def save_user_firewall_pending(
    name: str,
    firewall_info: Dict,
    path: str = DEFAULT_USER_FIREWALLS_PATH
) -> bool:
    """
    Save a user firewall with 'creating' status before VM creation.

    Args:
        name: Firewall name
        firewall_info: Firewall info dict
        path: Path to user_firewalls.yaml

    Returns:
        True if successful
    """
    data = load_user_firewalls(path)

    firewall_info['added_at'] = datetime.now(timezone.utc).isoformat()
    firewall_info['user_added'] = True
    firewall_info['device_type'] = 'firewall'
    firewall_info['status'] = 'creating'

    data['firewalls'].append({name: firewall_info})
    return save_user_firewalls(data, path)


def update_user_firewall_status(
    name: str,
    status: str = 'active',
    additional_info: Optional[Dict] = None,
    path: str = DEFAULT_USER_FIREWALLS_PATH
) -> bool:
    """
    Update a user firewall's status after VM creation completes.

    Args:
        name: Firewall name
        status: New status ('active' or 'failed')
        additional_info: Optional dict to merge into firewall info
        path: Path to user_firewalls.yaml

    Returns:
        True if firewall was found and updated
    """
    data = load_user_firewalls(path)

    for fw_entry in data.get('firewalls', []) or []:
        for fw_name, fw_info in fw_entry.items():
            if fw_name.lower() == name.lower():
                if status == 'active':
                    fw_info.pop('status', None)
                else:
                    fw_info['status'] = status

                if additional_info:
                    fw_info.update(additional_info)

                save_user_firewalls(data, path)
                logger.info(f"Updated firewall {name} status to {status}")
                return True

    logger.warning(f"Firewall {name} not found for status update")
    return False


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
    # Handle case where firewalls key exists but value is None
    return data.get('firewalls') or []


def cleanup_stale_user_firewalls(path: str = DEFAULT_USER_FIREWALLS_PATH) -> int:
    """
    Remove stale firewall entries with 'creating' or 'failed' status.

    These entries are orphaned from crashed or failed creations that
    didn't properly clean up. Called on service startup.

    Args:
        path: Path to user_firewalls.yaml

    Returns:
        Number of stale entries removed
    """
    data = load_user_firewalls(path)
    original_count = len(data.get('firewalls') or [])

    stale_statuses = {'creating', 'failed'}
    cleaned_firewalls = []
    removed_names = []

    for fw_entry in data.get('firewalls') or []:
        keep = True
        for fw_name, fw_info in fw_entry.items():
            status = fw_info.get('status')
            if status in stale_statuses:
                keep = False
                removed_names.append(fw_name)
                break
        if keep:
            cleaned_firewalls.append(fw_entry)

    removed_count = original_count - len(cleaned_firewalls)

    if removed_count > 0:
        data['firewalls'] = cleaned_firewalls
        save_user_firewalls(data, path)
        logger.info(f"Cleaned up {removed_count} stale firewall(s): {removed_names}")

    return removed_count


# ============================================================================
# User VeloCloud Devices Persistence (Edge, Gateway, Orchestrator)
# ============================================================================

DEFAULT_USER_VELO_PATH = '/etc/atd/user_velo.yaml'


def get_empty_user_velo() -> Dict:
    """Get the structure for an empty user_velo.yaml file."""
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'devices': []
    }


def load_user_velo(path: str = DEFAULT_USER_VELO_PATH) -> Dict:
    """
    Load user-added VeloCloud devices from persistence file.

    Args:
        path: Path to user_velo.yaml

    Returns:
        Dict with user VeloCloud devices data

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_velo()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_velo()

        if 'devices' not in data or data['devices'] is None:
            data['devices'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user VeloCloud devices from {path}: {e}")
        return get_empty_user_velo()


def save_user_velo(data: Dict, path: str = DEFAULT_USER_VELO_PATH) -> bool:
    """Save user VeloCloud devices data to persistence file."""
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
        logger.info(f"Saved user VeloCloud devices to {path}")
        return True
    except Exception as e:
        logger.error(f"Error saving user VeloCloud devices to {path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_velo_device_pending(
    name: str,
    device_type: str,
    device_info: Dict,
    path: str = DEFAULT_USER_VELO_PATH
) -> bool:
    """
    Save a user VeloCloud device with 'creating' status before VM creation.

    Args:
        name: Device name
        device_type: 'edge', 'gateway', or 'orchestrator'
        device_info: Device info dict
        path: Path to user_velo.yaml

    Returns:
        True if successful
    """
    data = load_user_velo(path)

    device_info['added_at'] = datetime.now(timezone.utc).isoformat()
    device_info['user_added'] = True
    device_info['device_type'] = device_type
    device_info['status'] = 'creating'

    data['devices'].append({name: device_info})
    return save_user_velo(data, path)


def update_user_velo_device_status(
    name: str,
    status: str = 'active',
    additional_info: Optional[Dict] = None,
    path: str = DEFAULT_USER_VELO_PATH
) -> bool:
    """
    Update a user VeloCloud device's status after VM creation completes.

    Args:
        name: Device name
        status: New status ('active' or 'failed')
        additional_info: Optional dict to merge into device info
        path: Path to user_velo.yaml

    Returns:
        True if device was found and updated
    """
    data = load_user_velo(path)

    for device_entry in data.get('devices', []) or []:
        for device_name, device_info in device_entry.items():
            if device_name.lower() == name.lower():
                if status == 'active':
                    device_info.pop('status', None)
                else:
                    device_info['status'] = status

                if additional_info:
                    device_info.update(additional_info)

                save_user_velo(data, path)
                logger.info(f"Updated VeloCloud device {name} status to {status}")
                return True

    logger.warning(f"VeloCloud device {name} not found for status update")
    return False


def remove_user_velo_device(name: str, path: str = DEFAULT_USER_VELO_PATH) -> bool:
    """Remove a user-added VeloCloud device from the persistence file."""
    data = load_user_velo(path)
    original_count = len(data['devices'])

    data['devices'] = [
        device for device in data['devices']
        if name.lower() not in [k.lower() for k in device.keys()]
    ]

    if len(data['devices']) == original_count:
        logger.warning(f"VeloCloud device {name} not found in user devices")
        return False

    save_user_velo(data, path)
    logger.info(f"Removed VeloCloud device {name} from user devices")
    return True


def get_user_velo_device(name: str, path: str = DEFAULT_USER_VELO_PATH) -> Optional[Dict]:
    """Get a specific user-added VeloCloud device by name."""
    data = load_user_velo(path)

    for device in data['devices']:
        for device_name, device_info in device.items():
            if device_name.lower() == name.lower():
                return {device_name: device_info}

    return None


def list_user_velo_devices(path: str = DEFAULT_USER_VELO_PATH) -> List[Dict]:
    """List all user-added VeloCloud devices."""
    data = load_user_velo(path)
    return data.get('devices') or []


def get_velo_device_count(path: str = DEFAULT_USER_VELO_PATH) -> int:
    """Get total count of user-added VeloCloud devices."""
    return len(list_user_velo_devices(path))


def cleanup_stale_velo_devices(path: str = DEFAULT_USER_VELO_PATH) -> int:
    """
    Remove stale VeloCloud device entries with 'creating' or 'failed' status.

    These entries are orphaned from crashed or failed creations that
    didn't properly clean up. Called on service startup.

    Args:
        path: Path to user_velo.yaml

    Returns:
        Number of stale entries removed
    """
    data = load_user_velo(path)
    original_count = len(data.get('devices') or [])

    # Filter out devices with stale status
    stale_statuses = {'creating', 'failed'}
    cleaned_devices = []
    removed_names = []

    for device_entry in data.get('devices') or []:
        keep = True
        for device_name, device_info in device_entry.items():
            status = device_info.get('status')
            if status in stale_statuses:
                keep = False
                removed_names.append(device_name)
                break
        if keep:
            cleaned_devices.append(device_entry)

    removed_count = original_count - len(cleaned_devices)

    if removed_count > 0:
        data['devices'] = cleaned_devices
        save_user_velo(data, path)
        logger.info(
            f"Cleaned up {removed_count} stale VeloCloud device(s): {removed_names}"
        )

    return removed_count


def get_velo_device_count_by_type(
    device_type: str,
    path: str = DEFAULT_USER_VELO_PATH,
    include_pending: bool = False
) -> int:
    """
    Get count of user-added VeloCloud devices by type.

    Args:
        device_type: 'edge', 'gateway', or 'orchestrator'
        path: Path to user_velo.yaml
        include_pending: If True, include devices with 'creating' or 'failed' status.
                         If False (default), only count active devices.
                         This prevents stale entries from blocking new device creation.

    Returns:
        Number of devices of the specified type
    """
    devices = list_user_velo_devices(path)
    count = 0

    for device_entry in devices:
        for _, device_info in device_entry.items():
            if device_info.get('device_type', '').lower() == device_type.lower():
                # Only count active devices by default
                # Active devices have no 'status' field (removed on success)
                # Pending devices have status='creating' or status='failed'
                if include_pending:
                    count += 1
                else:
                    status = device_info.get('status')
                    if status is None or status == 'active':
                        count += 1

    return count


# ============================================================================
# User CloudEOS Devices Persistence
# ============================================================================


def get_empty_user_cloudeos() -> Dict:
    """Get the structure for an empty user_cloudeos.yaml file."""
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'devices': []
    }


def load_user_cloudeos(path: str = DEFAULT_USER_CLOUDEOS_PATH) -> Dict:
    """
    Load user-added CloudEOS devices from persistence file.

    Args:
        path: Path to user_cloudeos.yaml

    Returns:
        Dict with user CloudEOS devices data

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_cloudeos()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_cloudeos()

        if 'devices' not in data or data['devices'] is None:
            data['devices'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user CloudEOS devices from {path}: {e}")
        return get_empty_user_cloudeos()


def save_user_cloudeos(data: Dict, path: str = DEFAULT_USER_CLOUDEOS_PATH) -> bool:
    """Save user CloudEOS devices data to persistence file."""
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
        logger.info(f"Saved user CloudEOS devices to {path}")
        return True
    except Exception as e:
        logger.error(f"Error saving user CloudEOS devices to {path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_cloudeos_pending(
    name: str,
    device_info: Dict,
    path: str = DEFAULT_USER_CLOUDEOS_PATH
) -> bool:
    """
    Save a user CloudEOS device with 'creating' status before VM creation.

    Args:
        name: Device name
        device_info: Device info dict
        path: Path to user_cloudeos.yaml

    Returns:
        True if successful
    """
    data = load_user_cloudeos(path)

    device_info['added_at'] = datetime.now(timezone.utc).isoformat()
    device_info['user_added'] = True
    device_info['status'] = 'creating'

    data['devices'].append({name: device_info})
    return save_user_cloudeos(data, path)


def update_user_cloudeos_status(
    name: str,
    status: str = 'active',
    additional_info: Optional[Dict] = None,
    path: str = DEFAULT_USER_CLOUDEOS_PATH
) -> bool:
    """
    Update a user CloudEOS device's status after VM creation completes.

    Args:
        name: Device name
        status: New status ('active' or 'failed')
        additional_info: Optional dict to merge into device info
        path: Path to user_cloudeos.yaml

    Returns:
        True if device was found and updated
    """
    data = load_user_cloudeos(path)

    for device_entry in data.get('devices', []) or []:
        for device_name, device_info in device_entry.items():
            if device_name.lower() == name.lower():
                if status == 'active':
                    device_info.pop('status', None)
                else:
                    device_info['status'] = status

                if additional_info:
                    device_info.update(additional_info)

                save_user_cloudeos(data, path)
                logger.info(f"Updated CloudEOS device {name} status to {status}")
                return True

    logger.warning(f"CloudEOS device {name} not found for status update")
    return False


def remove_user_cloudeos(name: str, path: str = DEFAULT_USER_CLOUDEOS_PATH) -> bool:
    """Remove a user-added CloudEOS device from the persistence file."""
    data = load_user_cloudeos(path)
    original_count = len(data['devices'])

    data['devices'] = [
        device for device in data['devices']
        if name.lower() not in [k.lower() for k in device.keys()]
    ]

    if len(data['devices']) == original_count:
        logger.warning(f"CloudEOS device {name} not found in user devices")
        return False

    save_user_cloudeos(data, path)
    logger.info(f"Removed CloudEOS device {name} from user devices")
    return True


def get_user_cloudeos_device(name: str, path: str = DEFAULT_USER_CLOUDEOS_PATH) -> Optional[Dict]:
    """Get a specific user-added CloudEOS device by name."""
    data = load_user_cloudeos(path)

    for device in data['devices']:
        for device_name, device_info in device.items():
            if device_name.lower() == name.lower():
                return {device_name: device_info}

    return None


def list_user_cloudeos(path: str = DEFAULT_USER_CLOUDEOS_PATH) -> List[Dict]:
    """List all user-added CloudEOS devices."""
    data = load_user_cloudeos(path)
    return data.get('devices') or []


def get_cloudeos_count(path: str = DEFAULT_USER_CLOUDEOS_PATH) -> int:
    """
    Get count of active (non-creating) user-added CloudEOS devices.

    Active devices have no 'status' field. Devices with status='creating'
    or status='failed' are excluded from the count.

    Args:
        path: Path to user_cloudeos.yaml

    Returns:
        Number of active CloudEOS devices
    """
    devices = list_user_cloudeos(path)
    count = 0

    for device_entry in devices:
        for _, device_info in device_entry.items():
            status = device_info.get('status')
            if status is None or status == 'active':
                count += 1

    return count


def cleanup_stale_cloudeos(path: str = DEFAULT_USER_CLOUDEOS_PATH) -> int:
    """
    Remove stale CloudEOS device entries with 'creating' or 'failed' status.

    These entries are orphaned from crashed or failed creations that
    didn't properly clean up. Called on service startup.

    Args:
        path: Path to user_cloudeos.yaml

    Returns:
        Number of stale entries removed
    """
    data = load_user_cloudeos(path)
    original_count = len(data.get('devices') or [])

    stale_statuses = {'creating', 'failed'}
    cleaned_devices = []
    removed_names = []

    for device_entry in data.get('devices') or []:
        keep = True
        for device_name, device_info in device_entry.items():
            status = device_info.get('status')
            if status in stale_statuses:
                keep = False
                removed_names.append(device_name)
                break
        if keep:
            cleaned_devices.append(device_entry)

    removed_count = original_count - len(cleaned_devices)

    if removed_count > 0:
        data['devices'] = cleaned_devices
        save_user_cloudeos(data, path)
        logger.info(
            f"Cleaned up {removed_count} stale CloudEOS device(s): {removed_names}"
        )

    return removed_count


# ============================================================================
# User Links Persistence (Virtual Links Between Devices)
# ============================================================================


def get_empty_user_links() -> Dict:
    """Get the structure for an empty user_links.yaml file."""
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'links': []
    }


def load_user_links(path: str = DEFAULT_USER_LINKS_PATH) -> Dict:
    """
    Load user-added links from persistence file.

    Args:
        path: Path to user_links.yaml

    Returns:
        Dict with user links data

    Raises:
        ValueError: If path is outside allowed directories
    """
    # Security: Validate path is within allowed directories
    if not _validate_path(path):
        logger.error(f"Security: Attempted to load from disallowed path: {path}")
        raise ValueError(f"Path not allowed: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_user_links()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_user_links()

        if 'links' not in data or data['links'] is None:
            data['links'] = []
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading user links from {path}: {e}")
        return get_empty_user_links()


def save_user_links(data: Dict, path: str = DEFAULT_USER_LINKS_PATH) -> bool:
    """Save user links data to persistence file."""
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
        logger.info(f"Saved user links to {path}")
        return True
    except Exception as e:
        logger.error(f"Error saving user links to {path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def save_user_link(
    link_data: Dict,
    path: str = DEFAULT_USER_LINKS_PATH
) -> bool:
    """
    Append a single user link to the persistence file.

    Args:
        link_data: Dict with link info containing source/target device and port fields
        path: Path to user_links.yaml

    Returns:
        True if successful
    """
    data = load_user_links(path)

    link_data['created_at'] = datetime.now(timezone.utc).isoformat()

    data['links'].append(link_data)
    return save_user_links(data, path)


def remove_user_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    path: str = DEFAULT_USER_LINKS_PATH
) -> bool:
    """
    Remove a specific user link by its endpoints (case-insensitive).

    Args:
        source_device: Source device name
        source_port: Source port name
        target_device: Target device name
        target_port: Target port name
        path: Path to user_links.yaml

    Returns:
        True if link was found and removed
    """
    data = load_user_links(path)
    original_count = len(data.get('links') or [])

    def _matches(link: Dict) -> bool:
        return (
            link.get('source_device', '').lower() == source_device.lower() and
            link.get('source_port', '').lower() == source_port.lower() and
            link.get('target_device', '').lower() == target_device.lower() and
            link.get('target_port', '').lower() == target_port.lower()
        )

    data['links'] = [link for link in (data.get('links') or []) if not _matches(link)]

    if len(data['links']) == original_count:
        logger.warning(
            f"Link {source_device}:{source_port} -> {target_device}:{target_port} "
            f"not found in user links"
        )
        return False

    save_user_links(data, path)
    logger.info(
        f"Removed link {source_device}:{source_port} -> {target_device}:{target_port} "
        f"from user links"
    )
    return True


def get_user_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    path: str = DEFAULT_USER_LINKS_PATH
) -> Optional[Dict]:
    """
    Find a specific user link by its endpoints (case-insensitive).

    Args:
        source_device: Source device name
        source_port: Source port name
        target_device: Target device name
        target_port: Target port name
        path: Path to user_links.yaml

    Returns:
        Link dict if found, None otherwise
    """
    data = load_user_links(path)

    for link in data.get('links') or []:
        if (
            link.get('source_device', '').lower() == source_device.lower() and
            link.get('source_port', '').lower() == source_port.lower() and
            link.get('target_device', '').lower() == target_device.lower() and
            link.get('target_port', '').lower() == target_port.lower()
        ):
            return link

    return None


def list_user_links(path: str = DEFAULT_USER_LINKS_PATH) -> List[Dict]:
    """List all user-added links."""
    data = load_user_links(path)
    return data.get('links') or []


def is_user_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    path: str = DEFAULT_USER_LINKS_PATH
) -> bool:
    """
    Check if a specific user link exists (case-insensitive).

    Args:
        source_device: Source device name
        source_port: Source port name
        target_device: Target device name
        target_port: Target port name
        path: Path to user_links.yaml

    Returns:
        True if link exists
    """
    return get_user_link(source_device, source_port, target_device, target_port, path) is not None
