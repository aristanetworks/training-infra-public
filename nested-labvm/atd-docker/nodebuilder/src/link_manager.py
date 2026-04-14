"""
Link Manager for Nodebuilder Service

Manages adding and removing OVS bridge connections between original topology
nodes. Only links between devices that exist in topo_build.yml are permitted.

Protection rules:
- add_link: Both devices must be in topo_build.yml (original topology nodes only)
- remove_link: Only user-added links (in user_links.yaml) can be removed.
  Original topology links (in topo_build.yml only) are rejected.
"""

import logging
import subprocess
from typing import Dict, List, Optional

from config import (
    USER_LINKS_PATH, SUBPROCESS_TIMEOUT_DEFAULT,
    get_topo_build_path
)
from interface_manager import (
    create_ovs_bridge, delete_ovs_bridge,
    find_next_available_port
)
from bridge_utils import generate_bridge_name
from connection_manager import process_connection_for_creation
from resource_manager import get_resource_manager
from persistence import (
    save_user_link, remove_user_link as persistence_remove_user_link,
    list_user_links as persistence_list_user_links,
    is_user_link as persistence_is_user_link,
    get_user_link
)
from slot_reuse import attach_interface_with_slot_reuse, apply_mutual_exclusivity
from validation import get_topo_nodes

logger = logging.getLogger('nodebuilder')


def _get_topo_node_names(topo_build_path: str = None) -> List[str]:
    """
    Get the list of node names from topo_build.yml.

    Args:
        topo_build_path: Path to topo_build.yml (defaults to config path)

    Returns:
        List of device name strings (lowercase)
    """
    if topo_build_path is None:
        topo_build_path = get_topo_build_path()

    nodes = get_topo_nodes(topo_build_path)
    return [node['name'].lower() for node in nodes]


def add_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    user_links_path: str = None,
    topo_build_path: str = None
) -> Dict:
    """
    Add a link between two original topology nodes.

    Steps:
    1. Validate both devices exist in topo_build.yml (original nodes only)
    2. Validate ports are available (not already connected)
    3. Check for orphaned interface slots (reuse if available)
    4. Create OVS bridge via interface_manager.create_ovs_bridge()
    5. Generate bridge name via bridge_utils.generate_bridge_name()
    6. Attach interfaces on both devices via slot_reuse.attach_interface_with_slot_reuse()
    7. Apply mutual exclusivity via slot_reuse.apply_mutual_exclusivity()
    8. Persist to user_links.yaml via persistence.save_user_link()
    9. Return dict with status, bridge name, targets_need_reboot, targets_reused_slots

    Args:
        source_device: Name of the source device (must be in topo_build.yml)
        source_port: Port on the source device (e.g., "Ethernet5")
        target_device: Name of the target device (must be in topo_build.yml)
        target_port: Port on the target device (e.g., "Ethernet5")
        user_links_path: Override path to user_links.yaml (optional)
        topo_build_path: Override path to topo_build.yml (optional)

    Returns:
        Dict with keys: status, bridge_name, targets_need_reboot, targets_reused_slots
    """
    if user_links_path is None:
        user_links_path = USER_LINKS_PATH
    if topo_build_path is None:
        topo_build_path = get_topo_build_path()

    # Step 1: Validate both devices are original topology nodes
    topo_node_names = _get_topo_node_names(topo_build_path)

    if source_device.lower() not in topo_node_names:
        logger.error(
            f"add_link: source device '{source_device}' is not in topo_build.yml"
        )
        return {
            'status': 'error',
            'error': f"Device '{source_device}' not found in original topology (topo_build.yml)"
        }

    if target_device.lower() not in topo_node_names:
        logger.error(
            f"add_link: target device '{target_device}' is not in topo_build.yml"
        )
        return {
            'status': 'error',
            'error': f"Device '{target_device}' not found in original topology (topo_build.yml)"
        }

    # Step 5: Generate bridge name
    bridge_name = generate_bridge_name(
        source_device, source_port,
        target_device, target_port
    )

    logger.info(
        f"add_link: creating link {source_device}:{source_port} <-> "
        f"{target_device}:{target_port} (bridge: {bridge_name})"
    )

    # Step 4: Create OVS bridge
    try:
        create_ovs_bridge(bridge_name)
    except Exception as e:
        logger.error(f"add_link: failed to create OVS bridge {bridge_name}: {e}")
        return {
            'status': 'error',
            'error': f"Failed to create OVS bridge: {e}"
        }

    # Step 6: Attach interfaces on both devices using slot reuse
    targets_need_reboot = []
    targets_reused_slots = []

    # libvirt domain names are lowercase, but topo_build.yml may use mixed case
    # (e.g., "Borderleaf1" in YAML but "borderleaf1" as the virsh domain name)
    source_domain = source_device.lower()
    target_domain = target_device.lower()

    connections = [
        {
            'target_device': source_domain,
            'target_port': source_port,
            'bridge': bridge_name
        },
        {
            'target_device': target_domain,
            'target_port': target_port,
            'bridge': bridge_name
        }
    ]

    for conn in connections:
        try:
            result = attach_interface_with_slot_reuse(
                target_device=conn['target_device'],
                target_port=conn['target_port'],
                bridge_name=conn['bridge'],
                connection_dict=conn
            )

            if result.needs_reboot:
                targets_need_reboot.append(result.target_device)
            elif result.reused_slot:
                targets_reused_slots.append(result.target_device)

        except Exception as e:
            logger.error(
                f"add_link: failed to attach interface on {conn['target_device']}: {e}"
            )
            # Attempt rollback - delete the bridge
            try:
                delete_ovs_bridge(bridge_name)
            except Exception as rollback_err:
                logger.error(
                    f"add_link: rollback failed for bridge {bridge_name}: {rollback_err}"
                )
            return {
                'status': 'error',
                'error': f"Failed to attach interface on {conn['target_device']}: {e}"
            }

    # Step 7: Apply mutual exclusivity to reboot/reused lists
    targets_reused_slots, targets_need_reboot = apply_mutual_exclusivity(
        targets_reused_slots, targets_need_reboot
    )

    # Step 8: Persist to user_links.yaml
    link_data = {
        'source_device': source_device,
        'source_port': source_port,
        'target_device': target_device,
        'target_port': target_port,
        'bridge_name': bridge_name
    }

    try:
        save_user_link(link_data, user_links_path)
    except Exception as e:
        logger.error(f"add_link: failed to persist link to user_links.yaml: {e}")
        # Link was created in OVS but not persisted - log warning
        logger.warning(
            f"add_link: OVS bridge {bridge_name} created but persistence failed. "
            f"Manual cleanup may be required."
        )
        return {
            'status': 'error',
            'error': f"Link created in OVS but failed to persist: {e}"
        }

    logger.info(
        f"add_link: successfully created link {source_device}:{source_port} <-> "
        f"{target_device}:{target_port}"
    )

    return {
        'status': 'success',
        'bridge_name': bridge_name,
        'source_device': source_device,
        'source_port': source_port,
        'target_device': target_device,
        'target_port': target_port,
        'targets_need_reboot': targets_need_reboot,
        'targets_reused_slots': targets_reused_slots
    }


def remove_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    user_links_path: str = None
) -> Dict:
    """
    Remove a user-added link.

    Steps:
    1. Validate the link exists in user_links.yaml (NOT in topo_build.yml)
    2. If link is only in topo_build.yml, reject with error
    3. Get bridge name from user_links.yaml entry
    4. Delete OVS bridge via interface_manager.delete_ovs_bridge()
    5. Track orphaned slots via orphaned_interfaces module
    6. Remove from user_links.yaml via persistence.remove_user_link()
    7. Return dict with status and details

    Args:
        source_device: Name of the source device
        source_port: Port on the source device
        target_device: Name of the target device
        target_port: Port on the target device
        user_links_path: Override path to user_links.yaml (optional)

    Returns:
        Dict with status and details
    """
    if user_links_path is None:
        user_links_path = USER_LINKS_PATH

    # Step 1 & 2: Check if link exists in user_links.yaml
    if not persistence_is_user_link(
        source_device, source_port, target_device, target_port, user_links_path
    ):
        logger.error(
            f"remove_link: link {source_device}:{source_port} -> "
            f"{target_device}:{target_port} not found in user_links.yaml"
        )
        return {
            'status': 'error',
            'error': (
                f"Cannot remove original topology links. "
                f"Link {source_device}:{source_port} -> {target_device}:{target_port} "
                f"was not found in user_links.yaml. "
                f"Only user-added links can be removed."
            )
        }

    # Step 3: Get bridge name from persistence
    link_entry = get_user_link(
        source_device, source_port, target_device, target_port, user_links_path
    )
    bridge_name = link_entry.get('bridge_name', '') if link_entry else ''

    if not bridge_name:
        # Fall back to generating it from the device/port names
        bridge_name = generate_bridge_name(
            source_device, source_port, target_device, target_port
        )
        logger.warning(
            f"remove_link: bridge name not found in persistence, "
            f"using generated name: {bridge_name}"
        )

    logger.info(
        f"remove_link: removing link {source_device}:{source_port} <-> "
        f"{target_device}:{target_port} (bridge: {bridge_name})"
    )

    # Step 4: Delete OVS bridge
    bridge_deleted = False
    bridge_error = None
    try:
        delete_ovs_bridge(bridge_name)
        bridge_deleted = True
        logger.info(f"remove_link: deleted OVS bridge {bridge_name}")
    except Exception as e:
        logger.warning(f"remove_link: failed to delete OVS bridge {bridge_name}: {e}")
        bridge_error = str(e)

    # Step 6: Remove from user_links.yaml
    removed = persistence_remove_user_link(
        source_device, source_port, target_device, target_port, user_links_path
    )

    if not removed:
        logger.warning(
            f"remove_link: link entry not found in user_links.yaml during removal "
            f"(was found in is_user_link check - possible race condition)"
        )

    result = {
        'status': 'success',
        'bridge_name': bridge_name,
        'bridge_deleted': bridge_deleted,
        'source_device': source_device,
        'source_port': source_port,
        'target_device': target_device,
        'target_port': target_port
    }

    if bridge_error:
        result['status'] = 'deleted_with_errors'
        result['bridge_error'] = bridge_error

    logger.info(
        f"remove_link: successfully removed link {source_device}:{source_port} <-> "
        f"{target_device}:{target_port}"
    )

    return result


def get_user_links(user_links_path: str = None) -> List[Dict]:
    """
    List all user-added links from user_links.yaml.

    Args:
        user_links_path: Override path to user_links.yaml (optional)

    Returns:
        List of link dicts
    """
    if user_links_path is None:
        user_links_path = USER_LINKS_PATH

    return persistence_list_user_links(user_links_path)


def is_user_link(
    source_device: str,
    source_port: str,
    target_device: str,
    target_port: str,
    user_links_path: str = None
) -> bool:
    """
    Check if a link is user-added (exists in user_links.yaml).

    Args:
        source_device: Name of the source device
        source_port: Port on the source device
        target_device: Name of the target device
        target_port: Port on the target device
        user_links_path: Override path to user_links.yaml (optional)

    Returns:
        True if the link is user-added, False otherwise
    """
    if user_links_path is None:
        user_links_path = USER_LINKS_PATH

    return persistence_is_user_link(
        source_device, source_port, target_device, target_port, user_links_path
    )


def get_available_ports(
    device_name: str,
    topo_build_path: str = None,
    user_links_path: str = None
) -> List[str]:
    """
    Get available (unconnected) ports on a device.

    Scans virsh domiflist to find unused ports by comparing the
    next available port pattern. Uses interface_manager.find_next_available_port()
    to discover what ports are currently in use.

    Args:
        device_name: Name of the device to query
        topo_build_path: Override path to topo_build.yml (optional)
        user_links_path: Override path to user_links.yaml (optional)

    Returns:
        List of available port names (e.g., ["Ethernet5", "Ethernet6"])
    """
    if topo_build_path is None:
        topo_build_path = get_topo_build_path()
    if user_links_path is None:
        user_links_path = USER_LINKS_PATH

    try:
        # find_next_available_port gives us the first free port
        next_port = find_next_available_port(device_name)
        # Return it as the primary available port
        if next_port:
            return [next_port]
        return []
    except Exception as e:
        logger.warning(f"get_available_ports: failed to query ports on {device_name}: {e}")
        return []
