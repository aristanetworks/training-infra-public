"""
Shared slot reuse logic for all device managers.

This module provides a unified interface for attaching interfaces to VMs
with automatic orphaned slot detection and reuse. This eliminates code
duplication across vm_manager.py, host_manager.py, and firewall_manager.py.

The slot reuse optimization allows new devices to connect to existing
topology devices without requiring a reboot when an orphaned interface
slot is available (left behind when a previous device was deleted).
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger('nodebuilder')


@dataclass
class SlotReuseResult:
    """Result of an interface attachment with slot reuse attempt."""
    reused_slot: bool
    needs_reboot: bool
    target_device: str
    error: Optional[str] = None


def attach_interface_with_slot_reuse(
    target_device: str,
    target_port: str,
    bridge_name: str,
    connection_dict: Optional[Dict] = None
) -> SlotReuseResult:
    """
    Attach an interface to a target VM, attempting to reuse an orphaned slot first.

    This function implements the slot preservation optimization:
    1. Check if slot preservation is enabled
    2. Look for an orphaned slot on the target device/port
    3. If found, try to reuse it via update_interface_bridge (no reboot needed)
    4. If not found or reuse fails, fall back to attach_interface_to_vm (reboot needed)

    Args:
        target_device: Name of the target VM to attach to
        target_port: Port on the target device (e.g., "Ethernet5")
        bridge_name: Name of the OVS bridge to connect
        connection_dict: Optional dict to update with 'reused_orphaned_slot' key

    Returns:
        SlotReuseResult with reuse status and reboot requirement
    """
    from interface_manager import (
        attach_interface_to_vm,
        update_interface_bridge,
        extract_port_number
    )

    # Check if slot preservation is enabled
    try:
        from config import ENABLE_SLOT_PRESERVATION
        slot_preservation_enabled = ENABLE_SLOT_PRESERVATION
    except ImportError:
        slot_preservation_enabled = False

    # Try to find an orphaned slot to reuse
    orphaned_slot = None
    if slot_preservation_enabled:
        try:
            from orphaned_interfaces import get_orphaned_slot_by_port, claim_orphaned_slot
            port_num = extract_port_number(target_port)
            orphaned_slot = get_orphaned_slot_by_port(target_device, port_num)
            if orphaned_slot:
                logger.debug(
                    f"Found orphaned slot for {target_device}:{target_port} "
                    f"(slot {orphaned_slot.get('slot_number')}, MAC {orphaned_slot.get('mac_address')})"
                )
        except Exception as e:
            logger.warning(
                f"Error checking orphaned slots for {target_device}:{target_port}: {e}"
            )
            orphaned_slot = None

    if orphaned_slot:
        # Attempt to reuse the orphaned slot
        logger.info(
            f"Reusing orphaned slot {orphaned_slot.get('slot_number')} on "
            f"{target_device} for bridge {bridge_name}"
        )
        try:
            from orphaned_interfaces import claim_orphaned_slot

            result = update_interface_bridge(
                target_device,
                orphaned_slot['mac_address'],
                bridge_name
            )
            # Both 'updated' (VM running) and 'configured' (VM stopped) are success
            # 'updated' = applied immediately, 'configured' = takes effect on next boot
            status = result.get('status')
            if status in ('updated', 'configured'):
                # Successfully reused the slot
                claim_orphaned_slot(target_device, orphaned_slot['mac_address'])
                if connection_dict is not None:
                    connection_dict['reused_orphaned_slot'] = True

                # If 'configured', the VM still needs a reboot for changes to take effect
                needs_reboot = (status == 'configured')
                logger.info(
                    f"Successfully reused orphaned slot on {target_device} - "
                    f"{'reboot needed for changes to take effect' if needs_reboot else 'no reboot needed'}"
                )
                return SlotReuseResult(
                    reused_slot=True,
                    needs_reboot=needs_reboot,
                    target_device=target_device
                )
            else:
                # Update failed, fall back to attach
                logger.warning(
                    f"Failed to update bridge on {target_device}, "
                    f"falling back to attach"
                )
        except Exception as e:
            logger.warning(
                f"Failed to reuse orphaned slot on {target_device}: {e}, "
                f"falling back to attach"
            )

    # No orphaned slot or reuse failed - attach new interface
    logger.info(
        f"Attaching new interface to {target_device} on bridge {bridge_name}"
    )
    attach_interface_to_vm(target_device, bridge_name)
    if connection_dict is not None:
        connection_dict['reused_orphaned_slot'] = False

    return SlotReuseResult(
        reused_slot=False,
        needs_reboot=True,
        target_device=target_device
    )


def process_connections_with_slot_reuse(
    connections: List[Dict]
) -> Tuple[List[str], List[str]]:
    """
    Process multiple connections, attempting slot reuse for each.

    This is a convenience function for processing a list of connections
    and collecting the reboot information.

    Args:
        connections: List of connection dicts, each with:
            - target_device: str
            - target_port: str
            - bridge: str

    Returns:
        Tuple of (targets_reused_slots, targets_need_reboot) lists
    """
    targets_reused_slots = []
    targets_need_reboot = []

    for conn in connections:
        result = attach_interface_with_slot_reuse(
            target_device=conn['target_device'],
            target_port=conn['target_port'],
            bridge_name=conn['bridge'],
            connection_dict=conn
        )

        # Check needs_reboot directly - reused slots may still need reboot
        # if the target VM was stopped (status='configured')
        if result.needs_reboot:
            targets_need_reboot.append(result.target_device)
        elif result.reused_slot:
            # Only add to reused_slots if NO reboot needed
            targets_reused_slots.append(result.target_device)

    return targets_reused_slots, targets_need_reboot


def apply_mutual_exclusivity(
    targets_reused_slots: List[str],
    targets_need_reboot: List[str]
) -> Tuple[List[str], List[str]]:
    """
    Apply mutual exclusivity to reboot lists.

    If a device appears in both lists (e.g., one connection reused a slot,
    another connection on the same device needed a new attachment), the
    device should only appear in targets_need_reboot.

    Args:
        targets_reused_slots: List of devices that reused orphaned slots
        targets_need_reboot: List of devices that need reboot

    Returns:
        Tuple of (final_reused_slots, final_need_reboot) with mutual exclusivity applied
    """
    reused_set = set(targets_reused_slots)
    reboot_set = set(targets_need_reboot)

    # Remove devices from reused if they also need reboot
    final_reused = reused_set - reboot_set

    return list(final_reused), list(reboot_set)
