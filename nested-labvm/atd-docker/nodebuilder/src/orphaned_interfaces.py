"""
Orphaned Interfaces Persistence for Nodebuilder Service

Manages interface slots that are preserved when user devices are deleted.
Instead of detaching interfaces (which causes vEOS interface renumbering),
we keep the interface attached but delete the bridge, creating an "orphaned" slot.

When a new device is added, orphaned slots can be reused by updating the
interface's bridge connection using `virsh update-device`.

This solves the vEOS interface renumbering problem where deleting a device
causes all subsequent interface numbers to shift down.

Persistence file: /etc/atd/orphaned_interfaces.yaml
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ruamel.yaml import YAML

from config import SUBPROCESS_TIMEOUT_SHORT

logger = logging.getLogger('nodebuilder')

# Default path - can be overridden via config
DEFAULT_ORPHANED_INTERFACES_PATH = '/etc/atd/orphaned_interfaces.yaml'

# Security: Allowed base directories for file operations
ALLOWED_PATH_PREFIXES = ('/etc/atd/',)


def _validate_path(path: str) -> bool:
    """
    Validate that a path is within allowed directories.

    Security: Prevents path traversal attacks, including via symlinks.
    Uses os.path.realpath() to resolve symlinks before validation.

    Args:
        path: Path to validate

    Returns:
        True if path is allowed
    """
    # Use realpath to resolve symlinks and get the canonical path
    # This prevents symlink-based path traversal attacks
    real_path = os.path.realpath(path)
    return any(real_path.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES)


def get_empty_orphaned_interfaces() -> Dict:
    """
    Get the structure for an empty orphaned_interfaces.yaml file.

    Returns:
        Dict with initialized structure
    """
    return {
        'version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'orphaned_interfaces': {}
    }


def load_orphaned_interfaces(path: str = DEFAULT_ORPHANED_INTERFACES_PATH) -> Dict:
    """
    Load orphaned interfaces from persistence file.

    Creates an empty structure if file doesn't exist.

    Args:
        path: Path to orphaned_interfaces.yaml

    Returns:
        Dict with orphaned interfaces data
    """
    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        if not os.path.exists(path):
            return get_empty_orphaned_interfaces()

        with open(path, 'r') as f:
            data = yaml.load(f)

        if data is None:
            return get_empty_orphaned_interfaces()

        # Ensure required fields exist
        if 'orphaned_interfaces' not in data:
            data['orphaned_interfaces'] = {}
        if 'version' not in data:
            data['version'] = 1

        return data

    except Exception as e:
        logger.error(f"Error loading orphaned interfaces from {path}: {e}")
        return get_empty_orphaned_interfaces()


def save_orphaned_interfaces(
    data: Dict,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> bool:
    """
    Save orphaned interfaces data to persistence file.

    Uses atomic write (write to temp, then rename) for safety.

    Args:
        data: Orphaned interfaces data dict
        path: Path to orphaned_interfaces.yaml

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

        logger.debug(f"Saved orphaned interfaces to {path}")
        return True

    except Exception as e:
        logger.error(f"Error saving orphaned interfaces to {path}: {e}")
        # Clean up temp file if it exists
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


# ============================================================================
# Query Operations
# ============================================================================

def get_orphaned_slots_for_device(
    device_name: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> List[Dict]:
    """
    Get all orphaned interface slots for a specific device.

    Args:
        device_name: Name of the device (e.g., 'spine1')
        path: Path to orphaned_interfaces.yaml

    Returns:
        List of orphaned slot dicts, sorted by slot_number
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    # Case-insensitive lookup
    for name, slots in orphaned.items():
        if name.lower() == device_name.lower():
            # Return sorted by slot number (oldest slots first)
            return sorted(slots, key=lambda x: x.get('slot_number', 0))

    return []


def has_orphaned_slots(
    device_name: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> bool:
    """
    Check if a device has any orphaned interface slots.

    Args:
        device_name: Name of the device
        path: Path to orphaned_interfaces.yaml

    Returns:
        True if device has orphaned slots
    """
    return len(get_orphaned_slots_for_device(device_name, path)) > 0


def get_next_orphaned_slot(
    device_name: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Optional[Dict]:
    """
    Get the next orphaned slot to reuse for a device.

    Returns the lowest slot number available (to maintain interface ordering).

    Args:
        device_name: Name of the device
        path: Path to orphaned_interfaces.yaml

    Returns:
        Orphaned slot dict if available, None otherwise
    """
    slots = get_orphaned_slots_for_device(device_name, path)
    if slots:
        # Return lowest slot number (already sorted)
        return slots[0]
    return None


def get_orphaned_slot_by_port(
    device_name: str,
    slot_number: int,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Optional[Dict]:
    """
    Get a specific orphaned slot by device and slot number.

    Args:
        device_name: Name of the device
        slot_number: Slot number (Ethernet number, e.g., 5 for Ethernet5)
        path: Path to orphaned_interfaces.yaml

    Returns:
        Orphaned slot dict if found, None otherwise
    """
    slots = get_orphaned_slots_for_device(device_name, path)
    for slot in slots:
        if slot.get('slot_number') == slot_number:
            return slot
    return None


def get_orphaned_slot_by_mac(
    device_name: str,
    mac_address: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Optional[Dict]:
    """
    Get a specific orphaned slot by device and MAC address.

    Args:
        device_name: Name of the device
        mac_address: MAC address of the orphaned interface
        path: Path to orphaned_interfaces.yaml

    Returns:
        Orphaned slot dict if found, None otherwise
    """
    slots = get_orphaned_slots_for_device(device_name, path)
    mac_lower = mac_address.lower()
    for slot in slots:
        if slot.get('mac_address', '').lower() == mac_lower:
            return slot
    return None


def list_all_orphaned_slots(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Dict[str, List[Dict]]:
    """
    List all orphaned interface slots across all devices.

    Args:
        path: Path to orphaned_interfaces.yaml

    Returns:
        Dict mapping device names to lists of orphaned slots
    """
    data = load_orphaned_interfaces(path)
    return data.get('orphaned_interfaces', {})


def count_orphaned_slots(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Dict:
    """
    Count orphaned slots across all devices.

    Args:
        path: Path to orphaned_interfaces.yaml

    Returns:
        Dict with 'total' and 'by_device' counts
    """
    all_slots = list_all_orphaned_slots(path)
    by_device = {name: len(slots) for name, slots in all_slots.items()}
    total = sum(by_device.values())
    return {
        'total': total,
        'by_device': by_device,
        'devices_affected': len(by_device)
    }


# ============================================================================
# Mutation Operations
# ============================================================================

def record_orphaned_slot(
    target_device: str,
    slot_number: int,
    mac_address: str,
    old_bridge: str,
    original_connection: Optional[Dict] = None,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> bool:
    """
    Record an interface slot as orphaned.

    Called when a user device is deleted but we want to preserve
    the interface slot on the target device.

    Args:
        target_device: Name of the target device (e.g., 'spine1')
        slot_number: Slot number (Ethernet number, e.g., 5 for Ethernet5)
        mac_address: MAC address of the interface
        old_bridge: Name of the deleted OVS bridge
        original_connection: Optional dict with original connection info
        path: Path to orphaned_interfaces.yaml

    Returns:
        True if successful
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.setdefault('orphaned_interfaces', {})

    # Normalize device name for consistent lookup
    device_key = target_device.lower()

    # Initialize device list if needed
    if device_key not in orphaned:
        orphaned[device_key] = []

    # Check if this slot is already recorded (prevent duplicates)
    for existing in orphaned[device_key]:
        if existing.get('slot_number') == slot_number:
            logger.warning(
                f"Orphaned slot {slot_number} already exists for {target_device}, "
                f"updating record"
            )
            # Update existing record
            existing['mac_address'] = mac_address
            existing['old_bridge'] = old_bridge
            existing['orphaned_at'] = datetime.now(timezone.utc).isoformat()
            if original_connection:
                existing['original_connection'] = original_connection
            save_orphaned_interfaces(data, path)
            return True

    # Create new orphaned slot record
    slot_record = {
        'slot_number': slot_number,
        'mac_address': mac_address,
        'old_bridge': old_bridge,
        'orphaned_at': datetime.now(timezone.utc).isoformat()
    }
    if original_connection:
        slot_record['original_connection'] = original_connection

    orphaned[device_key].append(slot_record)

    save_orphaned_interfaces(data, path)
    logger.info(
        f"Recorded orphaned slot: {target_device}:Ethernet{slot_number} "
        f"(MAC: {mac_address}, bridge: {old_bridge})"
    )
    return True


def claim_orphaned_slot(
    device_name: str,
    mac_address: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> bool:
    """
    Claim (remove) an orphaned slot after it has been reused.

    Called after successfully updating an interface's bridge connection.

    Args:
        device_name: Name of the device
        mac_address: MAC address of the interface
        path: Path to orphaned_interfaces.yaml

    Returns:
        True if slot was found and removed
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    device_key = device_name.lower()
    if device_key not in orphaned:
        logger.warning(f"No orphaned slots found for device {device_name}")
        return False

    mac_lower = mac_address.lower()
    original_count = len(orphaned[device_key])

    # Remove the slot with matching MAC
    orphaned[device_key] = [
        slot for slot in orphaned[device_key]
        if slot.get('mac_address', '').lower() != mac_lower
    ]

    if len(orphaned[device_key]) == original_count:
        logger.warning(
            f"Orphaned slot with MAC {mac_address} not found for {device_name}"
        )
        return False

    # Remove device entry if no more orphaned slots
    if not orphaned[device_key]:
        del orphaned[device_key]

    save_orphaned_interfaces(data, path)
    logger.info(f"Claimed orphaned slot: {device_name} (MAC: {mac_address})")
    return True


def remove_orphaned_slot(
    device_name: str,
    slot_number: int,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> bool:
    """
    Remove an orphaned slot by slot number.

    Args:
        device_name: Name of the device
        slot_number: Slot number to remove
        path: Path to orphaned_interfaces.yaml

    Returns:
        True if slot was found and removed
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    device_key = device_name.lower()
    if device_key not in orphaned:
        return False

    original_count = len(orphaned[device_key])

    orphaned[device_key] = [
        slot for slot in orphaned[device_key]
        if slot.get('slot_number') != slot_number
    ]

    if len(orphaned[device_key]) == original_count:
        return False

    # Remove device entry if no more orphaned slots
    if not orphaned[device_key]:
        del orphaned[device_key]

    save_orphaned_interfaces(data, path)
    logger.info(f"Removed orphaned slot: {device_name}:Ethernet{slot_number}")
    return True


# ============================================================================
# Cleanup Operations
# ============================================================================

def clear_orphaned_slots_for_device(
    device_name: str,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> int:
    """
    Clear all orphaned interface slots for a specific device.

    Args:
        device_name: Name of the device
        path: Path to orphaned_interfaces.yaml

    Returns:
        Number of slots cleared
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    device_key = device_name.lower()
    if device_key not in orphaned:
        return 0

    count = len(orphaned[device_key])
    del orphaned[device_key]

    save_orphaned_interfaces(data, path)
    logger.info(f"Cleared {count} orphaned slot(s) for device {device_name}")
    return count


def clear_all_orphaned_slots(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> int:
    """
    Clear all orphaned interface slots.

    Typically called during reset-all-user-nodes.

    Args:
        path: Path to orphaned_interfaces.yaml

    Returns:
        Total number of slots cleared
    """
    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    total = sum(len(slots) for slots in orphaned.values())

    if total > 0:
        data['orphaned_interfaces'] = {}
        save_orphaned_interfaces(data, path)
        logger.info(f"Cleared all orphaned slots: {total} slot(s) removed")

    return total


# ============================================================================
# Validation & Maintenance
# ============================================================================

def validate_orphaned_slots(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Dict:
    """
    Validate orphaned slots against actual VM state.

    Checks:
    1. Devices exist in libvirt
    2. Interfaces with recorded MACs exist on devices
    3. No duplicate slot numbers per device

    Args:
        path: Path to orphaned_interfaces.yaml

    Returns:
        Dict with validation results
    """
    import subprocess

    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    result = {
        'valid': True,
        'devices_checked': 0,
        'slots_checked': 0,
        'issues': []
    }

    for device_name, slots in orphaned.items():
        result['devices_checked'] += 1

        # Check if device exists
        try:
            proc = subprocess.run(
                ['virsh', 'dominfo', device_name],
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_SHORT
            )
            if proc.returncode != 0:
                result['valid'] = False
                result['issues'].append({
                    'type': 'device_not_found',
                    'device': device_name,
                    'message': f"Device {device_name} not found in libvirt"
                })
                continue
        except Exception as e:
            result['issues'].append({
                'type': 'check_error',
                'device': device_name,
                'message': f"Error checking device: {e}"
            })
            continue

        # Check for duplicate slot numbers
        slot_numbers = [s.get('slot_number') for s in slots]
        if len(slot_numbers) != len(set(slot_numbers)):
            result['valid'] = False
            result['issues'].append({
                'type': 'duplicate_slots',
                'device': device_name,
                'message': f"Duplicate slot numbers detected: {slot_numbers}"
            })

        # Check each slot
        for slot in slots:
            result['slots_checked'] += 1
            mac = slot.get('mac_address', '')

            # Verify MAC exists on device
            try:
                proc = subprocess.run(
                    ['virsh', 'domiflist', device_name],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_SHORT
                )
                if proc.returncode == 0:
                    if mac.lower() not in proc.stdout.lower():
                        result['issues'].append({
                            'type': 'mac_not_found',
                            'device': device_name,
                            'slot': slot.get('slot_number'),
                            'mac': mac,
                            'message': f"MAC {mac} not found on {device_name}"
                        })
            except Exception as e:
                result['issues'].append({
                    'type': 'check_error',
                    'device': device_name,
                    'message': f"Error checking interfaces: {e}"
                })

    return result


def cleanup_invalid_orphaned_slots(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH,
    dry_run: bool = False
) -> Dict:
    """
    Remove orphaned slots that reference non-existent devices or interfaces.

    Args:
        path: Path to orphaned_interfaces.yaml
        dry_run: If True, only report issues without removing slots

    Returns:
        Dict with cleanup results including:
        - removed_count: Number of slots removed (or would be removed in dry_run)
        - devices_cleaned: List of devices that had slots removed
        - errors: List of error messages
    """
    validation = validate_orphaned_slots(path)

    result = {
        'removed_count': 0,
        'devices_cleaned': [],
        'would_remove': [],  # For dry_run mode
        'errors': []
    }

    # Remove slots for non-existent devices
    for issue in validation.get('issues', []):
        if issue.get('type') == 'device_not_found':
            device = issue.get('device')
            slot_count = issue.get('slots', 0)

            if dry_run:
                result['would_remove'].append({
                    'device': device,
                    'slots': slot_count
                })
                result['removed_count'] += slot_count
                result['devices_cleaned'].append(device)
            else:
                try:
                    count = clear_orphaned_slots_for_device(device, path)
                    result['removed_count'] += count
                    result['devices_cleaned'].append(device)
                except Exception as e:
                    result['errors'].append(f"Failed to clear {device}: {e}")

    return result


def analyze_orphaned_slot_health(
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH,
    max_age_days: Optional[int] = None,
    max_per_device: Optional[int] = None
) -> Dict:
    """
    Analyze orphaned slot health and report potential issues.

    IMPORTANT: Orphaned slots preserve PCI slot ordering on target devices.
    We do NOT automatically remove valid slots because that would cause
    interface renumbering on the next reboot, breaking network connectivity.

    This function only REPORTS issues, it does not remove slots.
    To remove slots, use:
    - cleanup_invalid_orphaned_slots(): for slots on non-existent devices
    - clear_all_orphaned_slots(): for full reset

    Args:
        path: Path to orphaned_interfaces.yaml
        max_age_days: Age threshold for warnings (default from config)
        max_per_device: Count threshold for warnings (default from config)

    Returns:
        Dict with health analysis including warnings
    """
    from datetime import timedelta
    from config import ORPHANED_SLOT_MAX_AGE_DAYS, ORPHANED_SLOT_MAX_PER_DEVICE

    # Use config defaults if not specified
    if max_age_days is None:
        max_age_days = ORPHANED_SLOT_MAX_AGE_DAYS
    if max_per_device is None:
        max_per_device = ORPHANED_SLOT_MAX_PER_DEVICE

    data = load_orphaned_interfaces(path)
    orphaned = data.get('orphaned_interfaces', {})

    result = {
        'total_slots': 0,
        'total_devices': len(orphaned),
        'old_slots': [],        # Slots older than threshold (warning only)
        'high_count_devices': [],  # Devices with many slots (warning only)
        'warnings': []
    }

    now = datetime.now(timezone.utc)
    max_age = timedelta(days=max_age_days)

    for device_key, slots in orphaned.items():
        result['total_slots'] += len(slots)

        # Check for old slots (warning only - do not remove!)
        for slot in slots:
            orphaned_at_str = slot.get('orphaned_at')
            if orphaned_at_str:
                try:
                    orphaned_at = datetime.fromisoformat(orphaned_at_str)
                    if orphaned_at.tzinfo is None:
                        orphaned_at = orphaned_at.replace(tzinfo=timezone.utc)
                    age = now - orphaned_at
                    if age > max_age:
                        result['old_slots'].append({
                            'device': device_key,
                            'slot_number': slot.get('slot_number'),
                            'age_days': age.days
                        })
                except (ValueError, TypeError):
                    pass

        # Check for high slot count per device (warning only)
        if len(slots) > max_per_device:
            result['high_count_devices'].append({
                'device': device_key,
                'slot_count': len(slots),
                'threshold': max_per_device
            })

    # Generate human-readable warnings
    if result['old_slots']:
        result['warnings'].append(
            f"{len(result['old_slots'])} orphaned slot(s) are older than {max_age_days} days. "
            f"This is informational only - slots are preserved to prevent interface renumbering."
        )

    if result['high_count_devices']:
        devices = [d['device'] for d in result['high_count_devices']]
        result['warnings'].append(
            f"{len(result['high_count_devices'])} device(s) have many orphaned slots: {devices}. "
            f"Consider running a full reset if this causes performance issues."
        )

    if result['warnings']:
        for warning in result['warnings']:
            logger.info(f"Orphaned slot health: {warning}")

    return result


# ============================================================================
# Helper Functions
# ============================================================================

def get_orphaned_slot_info(
    device_name: str,
    slot_number: int,
    path: str = DEFAULT_ORPHANED_INTERFACES_PATH
) -> Optional[Dict]:
    """
    Get detailed info about an orphaned slot.

    Convenience function that combines slot lookup with formatted output.

    Args:
        device_name: Name of the device
        slot_number: Slot number
        path: Path to orphaned_interfaces.yaml

    Returns:
        Dict with slot info or None
    """
    slot = get_orphaned_slot_by_port(device_name, slot_number, path)
    if not slot:
        return None

    return {
        'device': device_name,
        'port': f"Ethernet{slot_number}",
        'slot_number': slot_number,
        'mac_address': slot.get('mac_address'),
        'old_bridge': slot.get('old_bridge'),
        'orphaned_at': slot.get('orphaned_at'),
        'original_connection': slot.get('original_connection'),
        'is_orphaned': True
    }


def cleanup_stale_orphaned_interfaces() -> Dict:
    """
    Handle interfaces pointing to non-existent OVS bridges at startup.

    For interfaces that match recorded orphaned slots: recreate the missing bridge
    so the VM can boot. The slot is still available for reuse.

    For interfaces that don't match any orphaned slot: detach them to prevent
    boot failures.

    Should be called at nodebuilder startup.

    Returns:
        Dict with detached_count, bridges_recreated, devices_cleaned, and errors
    """
    import subprocess
    from config import SUBPROCESS_TIMEOUT_DEFAULT

    result = {
        'detached_count': 0,
        'bridges_recreated': 0,
        'devices_cleaned': [],
        'devices_bridges_recreated': [],
        'errors': []
    }

    try:
        # Get list of all existing OVS bridges
        bridge_result = subprocess.run(
            ['ovs-vsctl', 'list-br'],
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )
        if bridge_result.returncode != 0:
            result['errors'].append(f"Failed to list OVS bridges: {bridge_result.stderr}")
            return result

        existing_bridges = set(bridge_result.stdout.strip().split('\n')) if bridge_result.stdout.strip() else set()

        # System bridges that are always expected (not OVS data bridges)
        system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}

        # Load orphaned slots registry to check against
        orphaned_data = load_orphaned_interfaces()
        all_orphaned = orphaned_data.get('orphaned_interfaces', {})

        # Build a lookup: (device_lower, mac_lower) -> orphaned_slot
        orphaned_lookup = {}
        for device_name, slots in all_orphaned.items():
            for slot in slots:
                mac = slot.get('mac_address', '').lower()
                if mac:
                    orphaned_lookup[(device_name.lower(), mac)] = slot

        # Get list of all VMs
        vm_result = subprocess.run(
            ['virsh', 'list', '--all', '--name'],
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )
        if vm_result.returncode != 0:
            result['errors'].append(f"Failed to list VMs: {vm_result.stderr}")
            return result

        vm_names = [name.strip() for name in vm_result.stdout.strip().split('\n') if name.strip()]

        for vm_name in vm_names:
            try:
                # Get interfaces for this VM
                intf_result = subprocess.run(
                    ['virsh', 'domiflist', vm_name],
                    capture_output=True, text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )
                if intf_result.returncode != 0:
                    continue

                lines = intf_result.stdout.strip().split('\n')
                for line in lines[2:]:  # Skip header lines
                    if not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) < 5:
                        continue

                    bridge_name = parts[2]
                    mac = parts[4]

                    # Skip system/management bridges
                    if bridge_name in system_bridges or bridge_name == '-':
                        continue

                    # Check if the bridge exists
                    if bridge_name not in existing_bridges:
                        # Check if this matches a recorded orphaned slot
                        lookup_key = (vm_name.lower(), mac.lower())
                        orphaned_slot = orphaned_lookup.get(lookup_key)

                        if orphaned_slot:
                            # This is an intentionally preserved slot -- recreate
                            # the bridge so the VM can boot
                            logger.info(
                                f"Startup cleanup: {vm_name} has orphaned slot (MAC {mac}) "
                                f"with missing bridge '{bridge_name}'. Recreating bridge."
                            )
                            try:
                                from interface_manager import create_ovs_bridge
                                create_ovs_bridge(bridge_name)
                                existing_bridges.add(bridge_name)
                                result['bridges_recreated'] += 1
                                if vm_name not in result['devices_bridges_recreated']:
                                    result['devices_bridges_recreated'].append(vm_name)
                                logger.info(
                                    f"Startup cleanup: Recreated bridge {bridge_name} "
                                    f"for orphaned slot on {vm_name}"
                                )
                            except Exception as e:
                                # Bridge recreation failed -- fall back to detach
                                logger.warning(
                                    f"Startup cleanup: Failed to recreate bridge "
                                    f"{bridge_name} for {vm_name}: {e}. Detaching instead."
                                )
                                try:
                                    from interface_manager import detach_interface_from_vm
                                    detach_interface_from_vm(vm_name, mac)
                                    result['detached_count'] += 1
                                    if vm_name not in result['devices_cleaned']:
                                        result['devices_cleaned'].append(vm_name)
                                    # Remove the stale orphaned slot record since
                                    # the interface has been detached
                                    slot_num = orphaned_slot.get('slot_number')
                                    if slot_num is not None:
                                        remove_orphaned_slot(vm_name, slot_num)
                                        logger.info(
                                            f"Startup cleanup: Removed stale orphaned "
                                            f"slot record {vm_name}:Ethernet{slot_num}"
                                        )
                                except Exception as e2:
                                    result['errors'].append(
                                        f"Failed to detach from {vm_name} "
                                        f"(bridge {bridge_name}): {e2}"
                                    )
                        else:
                            # No orphaned slot -- detach the stale interface
                            logger.warning(
                                f"Startup cleanup: {vm_name} has interface (MAC {mac}) "
                                f"pointing to non-existent bridge '{bridge_name}'. Detaching."
                            )
                            try:
                                from interface_manager import detach_interface_from_vm
                                detach_interface_from_vm(vm_name, mac)
                                result['detached_count'] += 1
                                if vm_name not in result['devices_cleaned']:
                                    result['devices_cleaned'].append(vm_name)
                                logger.info(
                                    f"Startup cleanup: Detached stale interface from {vm_name} "
                                    f"(bridge: {bridge_name}, MAC: {mac})"
                                )
                            except Exception as e:
                                result['errors'].append(
                                    f"Failed to detach from {vm_name} (bridge {bridge_name}): {e}"
                                )

            except Exception as e:
                result['errors'].append(f"Error checking {vm_name}: {e}")

    except Exception as e:
        result['errors'].append(f"Startup cleanup error: {e}")

    # After cleanup, try to start any affected VMs that are shut off.
    # Original topology VMs boot via libvirt autostart BEFORE nodebuilder
    # starts, so they may have failed due to stale/missing bridges.
    # Now that we've fixed them (detached stale or recreated missing), try starting.
    result['vms_restarted'] = []
    vms_to_restart = set(result['devices_cleaned']) | set(result['devices_bridges_recreated'])
    for vm_name in vms_to_restart:
        try:
            state_result = subprocess.run(
                ['virsh', 'domstate', vm_name],
                capture_output=True, text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            if state_result.returncode == 0 and 'shut off' in state_result.stdout:
                logger.info(f"Startup cleanup: Starting {vm_name} after interface cleanup")
                start_result = subprocess.run(
                    ['virsh', 'start', vm_name],
                    capture_output=True, text=True,
                    timeout=60
                )
                if start_result.returncode == 0:
                    result['vms_restarted'].append(vm_name)
                    logger.info(f"Startup cleanup: Successfully started {vm_name}")
                else:
                    result['errors'].append(
                        f"Failed to start {vm_name} after cleanup: {start_result.stderr.strip()}"
                    )
        except Exception as e:
            result['errors'].append(f"Error starting {vm_name}: {e}")

    if result['bridges_recreated'] > 0:
        logger.info(
            f"Startup cleanup: Recreated {result['bridges_recreated']} bridge(s) "
            f"for recorded orphaned slots"
        )

    return result
