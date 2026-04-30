"""
Interface management for Nodebuilder Service

Handles:
- Querying VM interfaces via virsh
- Finding available ports on VMs
- Creating OVS bridges
- Attaching interfaces to VMs

Bridge naming and parsing is delegated to bridge_utils.py (single source of truth).
"""

import logging
import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

# Import bridge utilities - single source of truth for bridge naming
# Re-export for API compatibility with modules that import from interface_manager
from bridge_utils import (
    parse_device_name,
    generate_bridge_name,
    parse_bridge_name,
    split_device_port,
    expand_device_code,
    expand_port_code,
    DEVICE_ABBREVIATIONS,
)

logger = logging.getLogger('nodebuilder.interface_manager')

# Regex pattern for valid bridge names - only alphanumeric, underscore, and hyphen
# This prevents command injection via malicious bridge names
VALID_BRIDGE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,15}$')


def validate_bridge_name(bridge_name: str) -> bool:
    """
    Validate that a bridge name contains only safe characters.

    Security: Prevents command injection by ensuring bridge names
    only contain alphanumeric characters, underscores, and hyphens.

    Args:
        bridge_name: Name to validate

    Returns:
        True if valid, False otherwise
    """
    if not bridge_name:
        return False
    return bool(VALID_BRIDGE_NAME_PATTERN.match(bridge_name))


from validation import get_all_nodes
from config import (
    get_topo_build_path,
    USER_NODES_PATH,
    USER_HOSTS_PATH,
    USER_FIREWALLS_PATH,
    MGMT_BRIDGE,
    ENABLE_SLOT_PRESERVATION,
    SUBPROCESS_TIMEOUT_DEFAULT,
    SUBPROCESS_TIMEOUT_LONG,
    CREATION_LOCK_TIMEOUT,
    PORT_ALLOCATION_LOCK_TIMEOUT
)


# Note: parse_device_name is imported from bridge_utils (single source of truth)


def get_vm_interfaces(vm_name: str) -> List[Dict]:
    """
    Query libvirt for interfaces attached to a VM.
    Lowercases vm_name since libvirt domains are always lowercase.

    Uses virsh domiflist to get the list of network interfaces.

    Args:
        vm_name: Name of the VM

    Returns:
        List of interface dicts with 'type', 'source', 'model', 'mac'
    """
    try:
        result = subprocess.run(
            ['virsh', 'domiflist', vm_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            return []

        interfaces = []
        lines = result.stdout.strip().split('\n')

        # Skip header lines (first 2 lines)
        for line in lines[2:]:
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    interfaces.append({
                        'interface': parts[0],
                        'type': parts[1],
                        'source': parts[2],
                        'model': parts[3],
                        'mac': parts[4] if len(parts) > 4 else ''
                    })

        return interfaces

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout querying interfaces for {vm_name}")
    except Exception as e:
        raise RuntimeError(f"Error querying interfaces for {vm_name}: {e}")


def get_used_ports_from_live_vm(device_name: str) -> List[int]:
    """
    Get list of used port numbers by querying the live VM interfaces.

    This catches interfaces that have been attached but not yet saved to config.

    Args:
        device_name: Name of the device (VM)

    Returns:
        List of used port numbers based on number of data interfaces
    """
    try:
        # Query VM interfaces using virsh
        result = subprocess.run(
            ['virsh', 'domiflist', device_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            # VM might not be running or doesn't exist - this is normal
            return []

        # Parse output - count data interfaces (connected to OVS bridges, not mgmt)
        # Format: Interface  Type    Source    Model   MAC
        lines = result.stdout.strip().split('\n')
        interface_count = 0

        for line in lines[2:]:  # Skip header lines
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    source = parts[2]
                    # Count OVS bridges (data interfaces, not management bridges)
                    # Management bridges: vmgmt (ATD), br0, br-mgmt, virbr* (libvirt default)
                    mgmt_bridges = {'br0', 'br-mgmt', MGMT_BRIDGE, 'oob_mgmt'}
                    if source not in mgmt_bridges and not source.startswith('virbr'):
                        interface_count += 1

        # Return list of port numbers (1 to interface_count)
        return list(range(1, interface_count + 1))

    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout querying live VM interfaces for {device_name}")
        return []
    except Exception as e:
        logger.warning(f"Error querying live VM interfaces for {device_name}: {e}")
        return []


def check_port_consistency(device_name: str) -> Dict:
    """
    Check for port allocation consistency issues.

    Detects:
    - Gaps in port numbering (indicates persistence/VM mismatch)
    - Differences between persistence and live VM

    Args:
        device_name: Name of the device

    Returns:
        Dict with consistency check results
    """
    topo_ports = get_used_ports_from_topology(device_name)
    live_ports = get_used_ports_from_live_vm(device_name)

    result = {
        'device': device_name,
        'topology_ports': topo_ports,
        'live_ports': live_ports,
        'consistent': True,
        'issues': []
    }

    # Check for gaps in topology ports
    if topo_ports:
        expected = set(range(1, max(topo_ports) + 1))
        actual = set(topo_ports)
        gaps = expected - actual
        if gaps:
            result['consistent'] = False
            result['issues'].append({
                'type': 'port_gap',
                'message': f"Gap in port numbering: missing ports {sorted(gaps)}",
                'missing_ports': sorted(gaps)
            })

    # Check for mismatch between topology and live
    topo_set = set(topo_ports)
    live_set = set(live_ports)

    in_topo_not_live = topo_set - live_set
    in_live_not_topo = live_set - topo_set

    if in_topo_not_live:
        result['issues'].append({
            'type': 'persistence_only',
            'message': f"Ports in persistence but not attached to VM: {sorted(in_topo_not_live)}",
            'ports': sorted(in_topo_not_live)
        })

    if in_live_not_topo:
        result['issues'].append({
            'type': 'live_only',
            'message': f"Ports attached to VM but not in persistence: {sorted(in_live_not_topo)}",
            'ports': sorted(in_live_not_topo)
        })

    if result['issues']:
        result['consistent'] = False

    return result


def get_used_ports_from_topology(device_name: str) -> List[int]:
    """
    Get list of used port numbers for a device from ALL sources.

    Checks:
    1. Base topology (topo_build.yml) - original neighbor connections
    2. User-added vEOS nodes (user_nodes.yaml) - added node neighbors
    3. User-added Linux hosts (user_hosts.yaml) - host connections
    4. User-added VyOS firewalls (user_firewalls.yaml) - firewall connections
    5. Live VM interfaces (virsh domiflist) - catches unsaved attachments

    Args:
        device_name: Name of the device

    Returns:
        List of used port numbers (e.g., [1, 2, 3] for Ethernet1-3)
    """
    from persistence import load_user_hosts, load_user_firewalls

    topo_build_path = get_topo_build_path()
    all_nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)

    used_ports = set()

    # Source 1 & 2: Base topology + user-added vEOS nodes
    for node in all_nodes:
        # Check if this device has neighbors
        if node['name'].lower() == device_name.lower():
            for neighbor in node.get('neighbors', []):
                port = neighbor.get('port', '')
                port_num = extract_port_number(port)
                if port_num:
                    used_ports.add(port_num)

        # Check if this device is a neighbor of another device
        for neighbor in node.get('neighbors', []):
            if neighbor.get('neighborDevice', '').lower() == device_name.lower():
                port = neighbor.get('neighborPort', '')
                port_num = extract_port_number(port)
                if port_num:
                    used_ports.add(port_num)

    # Source 3: User-added Linux hosts
    try:
        hosts_data = load_user_hosts(USER_HOSTS_PATH)
        for host_entry in hosts_data.get('hosts', []) or []:
            for host_name, host_info in host_entry.items():
                connection = host_info.get('connection', {})
                if connection:
                    target_device = connection.get('target_device', '')
                    if target_device.lower() == device_name.lower():
                        port = connection.get('target_port', '')
                        port_num = extract_port_number(port)
                        if port_num:
                            used_ports.add(port_num)
    except Exception:
        pass  # File might not exist

    # Source 4: User-added VyOS firewalls (check both inside and outside)
    try:
        firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
        for fw_entry in firewalls_data.get('firewalls', []) or []:
            for fw_name, fw_info in fw_entry.items():
                # Check inside interface
                inside = fw_info.get('inside_interface', {})
                if inside:
                    target_device = inside.get('target_device', '')
                    if target_device.lower() == device_name.lower():
                        port = inside.get('target_port', '')
                        port_num = extract_port_number(port)
                        if port_num:
                            used_ports.add(port_num)
                # Check outside interface
                outside = fw_info.get('outside_interface', {})
                if outside:
                    target_device = outside.get('target_device', '')
                    if target_device.lower() == device_name.lower():
                        port = outside.get('target_port', '')
                        port_num = extract_port_number(port)
                        if port_num:
                            used_ports.add(port_num)
    except Exception:
        pass  # File might not exist

    # Source 5: Live VM interfaces (catches recently attached but unsaved)
    live_ports = get_used_ports_from_live_vm(device_name)
    for port_num in live_ports:
        used_ports.add(port_num)

    return sorted(list(used_ports))


def extract_port_number(port_name: str) -> Optional[int]:
    """
    Extract port number from port name (e.g., Ethernet3 -> 3)

    Args:
        port_name: Port name string

    Returns:
        Port number or None
    """
    match = re.search(r'(\d+)(?:/\d+)?$', port_name)
    if match:
        return int(match.group(1))
    return None


# Port allocation locking to prevent race conditions
import fcntl
import threading
import time
from contextlib import contextmanager

# In-memory lock for thread safety within this process
_port_allocation_lock = threading.Lock()
# File-based lock path for cross-process safety
_PORT_LOCK_FILE = '/tmp/nodebuilder_port_allocation.lock'

# Global creation lock - prevents concurrent VM/resource creation
_creation_lock = threading.Lock()
_CREATION_LOCK_FILE = '/tmp/nodebuilder_creation.lock'


@contextmanager
def creation_lock(operation_name: str = 'create', timeout: float = CREATION_LOCK_TIMEOUT):
    """
    Context manager for serializing VM/resource creation operations.

    This lock wraps the ENTIRE creation flow (validation through VM creation)
    to prevent race conditions where concurrent requests allocate the same
    resources (IPs, ports, bridge names).

    Uses both threading lock (for same-process concurrency) and
    file lock (for cross-process concurrency).

    Args:
        operation_name: Operation description (for logging)
        timeout: Lock acquisition timeout in seconds (longer for VM creation)

    Raises:
        TimeoutError: If lock cannot be acquired within timeout
    """
    start_time = time.time()

    # Acquire thread lock first
    acquired = _creation_lock.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError(f"Timeout acquiring thread lock for {operation_name}")

    lock_file = None
    try:
        # Then acquire file lock for cross-process safety
        lock_file = open(_CREATION_LOCK_FILE, 'w')
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                logger.debug(f"Acquired creation lock for {operation_name}")
                break
            except (IOError, OSError):
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Timeout acquiring file lock for {operation_name}")
                time.sleep(0.1)

        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass  # Ignore errors during unlock
            logger.debug(f"Released creation lock for {operation_name}")
    finally:
        # Always close file and release thread lock
        if lock_file is not None:
            try:
                lock_file.close()
            except Exception:
                pass
        _creation_lock.release()


@contextmanager
def port_allocation_lock(device_name: str, timeout: float = PORT_ALLOCATION_LOCK_TIMEOUT):
    """
    Context manager for thread-safe and process-safe port allocation.

    Uses both threading lock (for same-process concurrency) and
    file lock (for cross-process concurrency).

    Note: This is a more granular lock for port allocation within the
    creation flow. For full creation serialization, use creation_lock().

    Args:
        device_name: Device name (for logging)
        timeout: Lock acquisition timeout in seconds

    Raises:
        TimeoutError: If lock cannot be acquired within timeout
    """
    start_time = time.time()

    # Acquire thread lock first
    acquired = _port_allocation_lock.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError(f"Timeout acquiring thread lock for port allocation on {device_name}")

    lock_file = None
    try:
        # Then acquire file lock for cross-process safety
        lock_file = open(_PORT_LOCK_FILE, 'w')
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Timeout acquiring file lock for port allocation on {device_name}")
                time.sleep(0.1)

        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass  # Ignore errors during unlock
    finally:
        # Always close file and release thread lock
        if lock_file is not None:
            try:
                lock_file.close()
            except Exception:
                pass
        _port_allocation_lock.release()


def find_next_available_port(
    device_name: str,
    use_lock: bool = True,
    prefer_orphaned: bool = True
) -> str:
    """
    Find the next available Ethernet port on a device.

    When interface slot preservation is enabled, this function first checks
    for orphaned slots that can be reused. Orphaned slots are interfaces that
    were preserved when a user device was deleted, to prevent vEOS renumbering.

    If no orphaned slots are available, falls back to finding the next
    contiguous port after the highest used port.

    Args:
        device_name: Name of the device
        use_lock: Whether to acquire allocation lock (disable for read-only queries)
        prefer_orphaned: Whether to check for orphaned slots first (default True)

    Returns:
        Next available port name (e.g., "Ethernet5")
    """
    def _find_port():
        # Check for orphaned slots first (if enabled)
        if prefer_orphaned and ENABLE_SLOT_PRESERVATION:
            try:
                # Import here to avoid circular import
                from orphaned_interfaces import get_next_orphaned_slot
                orphaned = get_next_orphaned_slot(device_name)
                if orphaned:
                    slot_number = orphaned.get('slot_number')
                    if slot_number:
                        logger.debug(
                            f"Found orphaned slot for {device_name}: Ethernet{slot_number}"
                        )
                        return f"Ethernet{slot_number}"
            except Exception as e:
                # If orphaned slot lookup fails, continue with standard logic
                logger.warning(f"Error checking orphaned slots for {device_name}: {e}")

        # Standard logic: find next port after highest used
        used_ports = get_used_ports_from_topology(device_name)

        if not used_ports:
            return "Ethernet1"

        # Find next contiguous port after highest used
        next_port = max(used_ports) + 1
        return f"Ethernet{next_port}"

    if use_lock:
        with port_allocation_lock(device_name):
            return _find_port()
    else:
        return _find_port()


def get_target_devices_with_ports() -> List[Dict]:
    """
    Get all existing devices with their next available port.

    This is a read-only query, so we don't need the allocation lock.

    Returns:
        List of dicts with 'name' and 'next_available_port'
    """
    topo_build_path = get_topo_build_path()
    all_nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)

    devices = []
    for node in all_nodes:
        device_name = node['name']
        # Use non-locking version for read-only query
        next_port = find_next_available_port(device_name, use_lock=False)

        # Get all used ports for reference
        used_ports = get_used_ports_from_topology(device_name)

        devices.append({
            'name': device_name,
            'ip_addr': node.get('ip_addr', ''),
            'next_available_port': next_port,
            'used_ports': [f"Ethernet{p}" for p in used_ports],
            'user_added': node.get('user_added', False)
        })

    return devices


# Note: generate_bridge_name is imported from bridge_utils (single source of truth)


def create_ovs_bridge(bridge_name: str) -> Dict:
    """
    Create an OVS bridge.

    Args:
        bridge_name: Name of the bridge to create

    Returns:
        Dict with status and bridge name

    Raises:
        ValueError: If bridge name contains invalid characters
    """
    # Security: Validate bridge name before passing to subprocess
    if not validate_bridge_name(bridge_name):
        raise ValueError(
            f"Invalid bridge name '{bridge_name}': must be 1-15 alphanumeric "
            "characters, underscores, or hyphens"
        )

    try:
        # Check if bridge already exists
        result = subprocess.run(
            ['ovs-vsctl', 'br-exists', bridge_name],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode == 0:
            # Bridge already exists
            return {'status': 'exists', 'bridge': bridge_name}

        # Create the bridge
        result = subprocess.run(
            ['ovs-vsctl', 'add-br', bridge_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create bridge: {result.stderr}")

        # Enable BPDU forwarding
        result = subprocess.run(
            ['ovs-vsctl', 'set', 'bridge', bridge_name, 'other-config:forward-bpdu=true'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to set BPDU forwarding: {result.stderr}")

        # Bring the bridge up
        result = subprocess.run(
            ['ip', 'link', 'set', bridge_name, 'up'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        return {'status': 'created', 'bridge': bridge_name}

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout creating bridge {bridge_name}")
    except Exception as e:
        raise RuntimeError(f"Error creating bridge {bridge_name}: {e}")


def delete_ovs_bridge(bridge_name: str) -> Dict:
    """
    Delete an OVS bridge.

    Args:
        bridge_name: Name of the bridge to delete

    Returns:
        Dict with status

    Raises:
        ValueError: If bridge name contains invalid characters
    """
    # Security: Validate bridge name before passing to subprocess
    if not validate_bridge_name(bridge_name):
        raise ValueError(
            f"Invalid bridge name '{bridge_name}': must be 1-15 alphanumeric "
            "characters, underscores, or hyphens"
        )

    try:
        result = subprocess.run(
            ['ovs-vsctl', 'del-br', bridge_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to delete bridge: {result.stderr}")

        return {'status': 'deleted', 'bridge': bridge_name}

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout deleting bridge {bridge_name}")
    except Exception as e:
        raise RuntimeError(f"Error deleting bridge {bridge_name}: {e}")


def attach_interface_to_vm(
    vm_name: str,
    bridge_name: str,
    mac: Optional[str] = None,
    target_port: Optional[str] = None
) -> Dict:
    """
    Attach a new network interface to a VM using OVS bridge.

    Uses virsh attach-device with an XML file that includes the
    OVS virtualport type, which is required for OVS bridges.

    If the VM is running, applies immediately with --live --config.
    If the VM is not running, uses --config only (takes effect on next boot).

    Note: The target_port parameter is accepted for API compatibility but
    not used for PCI address specification. vEOS uses a complex slot+function
    packing scheme that libvirt manages automatically.

    Args:
        vm_name: Name of the VM
        bridge_name: Name of the OVS bridge to connect to
        mac: Optional MAC address for the interface
        target_port: Optional target port name (unused, for API compatibility)

    Returns:
        Dict with status and details
    """
    import tempfile
    from vm_manager import get_vm_state

    # Check if VM is running
    vm_state = get_vm_state(vm_name)
    vm_is_running = vm_state == 'running'

    # Generate interface XML with OVS virtualport type
    # Use XML escaping for defense-in-depth (inputs are validated but escape anyway)
    safe_bridge = xml_escape(bridge_name, {'"': '&quot;', "'": '&apos;'})
    safe_mac = xml_escape(mac, {'"': '&quot;', "'": '&apos;'}) if mac else ""
    mac_element = f"<mac address='{safe_mac}'/>" if mac else ""
    interface_xml = f"""<interface type='bridge'>
  {mac_element}
  <source bridge='{safe_bridge}'/>
  <model type='virtio'/>
  <virtualport type='openvswitch'/>
</interface>"""

    xml_path = None
    try:
        # Write XML to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(interface_xml)
            xml_path = f.name

        # Build command based on VM state
        cmd = ['virsh', 'attach-device', vm_name, xml_path, '--config']
        if vm_is_running:
            cmd.append('--live')  # Apply immediately if running

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to attach interface: {result.stderr}")

        # For running VMs, we need to manually add the interface to OVS
        # virsh attach-device --live doesn't reliably add to OVS bridges
        vnet_interface = None
        if vm_is_running:
            # Find the newly created vnet interface by checking domiflist
            # Retry a few times as the interface may take a moment to appear
            import time
            max_retries = 3
            retry_delay = 0.5  # seconds

            for attempt in range(max_retries):
                domiflist_result = subprocess.run(
                    ['virsh', 'domiflist', vm_name],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )

                if domiflist_result.returncode != 0:
                    logger.warning(
                        f"domiflist failed for {vm_name}: {domiflist_result.stderr}"
                    )
                    break

                # Log the full domiflist output for debugging
                logger.debug(f"domiflist output for {vm_name}:\n{domiflist_result.stdout}")

                # Parse domiflist output to find interface connected to our bridge
                for line in domiflist_result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == bridge_name:
                        vnet_interface = parts[0]
                        logger.info(
                            f"Found interface {vnet_interface} connected to {bridge_name} on {vm_name}"
                        )
                        break

                if vnet_interface:
                    break

                # Interface not found yet, wait and retry
                if attempt < max_retries - 1:
                    logger.debug(
                        f"Interface for bridge {bridge_name} not found on {vm_name}, "
                        f"retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(retry_delay)

            if not vnet_interface:
                # Log warning with all interfaces we did see
                interfaces_seen = []
                for line in domiflist_result.stdout.strip().split('\n')[1:]:  # Skip header
                    parts = line.split()
                    if len(parts) >= 3:
                        interfaces_seen.append(f"{parts[0]}:{parts[2]}")
                logger.warning(
                    f"Could not find interface for bridge {bridge_name} on {vm_name}. "
                    f"Interfaces seen: {interfaces_seen}. "
                    f"Interface may need VM reboot to activate."
                )

            # If we found the interface, add it to OVS if not already there
            if vnet_interface:
                # Check if this interface is already in OVS
                check_port = subprocess.run(
                    ['ovs-vsctl', 'port-to-br', vnet_interface],
                    capture_output=True,
                    text=True
                )
                if check_port.returncode != 0:
                    # Interface not in OVS, add it manually
                    logger.info(f"Adding {vnet_interface} to OVS bridge {bridge_name}")
                    add_result = subprocess.run(
                        ['ovs-vsctl', 'add-port', bridge_name, vnet_interface],
                        capture_output=True,
                        text=True,
                        timeout=SUBPROCESS_TIMEOUT_DEFAULT
                    )
                    if add_result.returncode != 0:
                        # This is a critical failure - interface attached but not in OVS
                        # The network connection won't work
                        raise RuntimeError(
                            f"Failed to add {vnet_interface} to OVS bridge {bridge_name}: "
                            f"{add_result.stderr}. Interface attached to VM but not connected to bridge."
                        )
                    else:
                        logger.info(f"Successfully added {vnet_interface} to {bridge_name}")
                else:
                    logger.debug(
                        f"Interface {vnet_interface} already in OVS bridge "
                        f"{check_port.stdout.strip()}"
                    )

        return {
            'status': 'attached' if vm_is_running else 'configured',
            'vm': vm_name,
            'bridge': bridge_name,
            'immediate': vm_is_running,
            'vnet_interface': vnet_interface
        }

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout attaching interface to {vm_name}")
    except Exception as e:
        raise RuntimeError(f"Error attaching interface to {vm_name}: {e}")
    finally:
        # Clean up temp file
        if xml_path and os.path.exists(xml_path):
            os.remove(xml_path)


def detach_interface_from_vm(
    vm_name: str,
    mac: str
) -> Dict:
    """
    Detach a network interface from a VM by MAC address.

    If the VM is running, applies immediately with --live --config.
    If the VM is not running, uses --config only (takes effect on next boot).

    Args:
        vm_name: Name of the VM
        mac: MAC address of the interface to detach

    Returns:
        Dict with status
    """
    from vm_manager import get_vm_state

    # Check if VM is running
    vm_state = get_vm_state(vm_name)
    vm_is_running = vm_state == 'running'

    try:
        cmd = [
            'virsh', 'detach-interface', vm_name,
            '--type', 'bridge',
            '--mac', mac,
            '--config'
        ]
        if vm_is_running:
            cmd.append('--live')

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to detach interface: {result.stderr}")

        return {
            'status': 'detached' if vm_is_running else 'configured',
            'vm': vm_name,
            'mac': mac,
            'immediate': vm_is_running
        }

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout detaching interface from {vm_name}")
    except Exception as e:
        raise RuntimeError(f"Error detaching interface from {vm_name}: {e}")


def update_interface_bridge(
    vm_name: str,
    mac_address: str,
    new_bridge: str
) -> Dict:
    """
    Update an existing interface's bridge connection without detaching.

    Uses virsh update-device to modify the interface in-place, which
    preserves the interface slot position (preventing vEOS renumbering).

    This is the key function for interface slot preservation:
    - Instead of detaching an interface (which causes renumbering on reboot)
    - We update its bridge connection to point to a new OVS bridge

    If the VM is running, applies immediately with --live --config.
    If the VM is not running, uses --config only (takes effect on next boot).

    Args:
        vm_name: Name of the VM
        mac_address: MAC address of the interface to update
        new_bridge: Name of the new OVS bridge to connect to

    Returns:
        Dict with status and details:
        {
            'status': 'updated' | 'configured',
            'vm': vm_name,
            'mac': mac_address,
            'new_bridge': new_bridge,
            'immediate': bool  # True if VM was running
        }

    Raises:
        RuntimeError: If update fails
    """
    import tempfile
    from vm_manager import get_vm_state

    # Check if VM is running
    vm_state = get_vm_state(vm_name)
    vm_is_running = vm_state == 'running'

    # First, verify the interface exists on this VM
    interfaces = get_vm_interfaces(vm_name)
    interface_found = False
    old_bridge = None

    for intf in interfaces:
        if intf.get('mac', '').lower() == mac_address.lower():
            interface_found = True
            old_bridge = intf.get('source', '')
            break

    if not interface_found:
        raise RuntimeError(
            f"Interface with MAC {mac_address} not found on VM {vm_name}"
        )

    # Generate interface XML with OVS virtualport type
    # Use XML escaping for defense-in-depth
    safe_bridge = xml_escape(new_bridge, {'"': '&quot;', "'": '&apos;'})
    safe_mac = xml_escape(mac_address, {'"': '&quot;', "'": '&apos;'})

    interface_xml = f"""<interface type='bridge'>
  <mac address='{safe_mac}'/>
  <source bridge='{safe_bridge}'/>
  <model type='virtio'/>
  <virtualport type='openvswitch'/>
</interface>"""

    xml_path = None
    try:
        # Write XML to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(interface_xml)
            xml_path = f.name

        # Build command based on VM state
        # Note: update-device modifies existing device, unlike attach-device
        cmd = ['virsh', 'update-device', vm_name, xml_path, '--config']
        if vm_is_running:
            cmd.append('--live')  # Apply immediately if running

        logger.info(
            f"Updating interface bridge: {vm_name} MAC={mac_address} "
            f"old_bridge={old_bridge} new_bridge={new_bridge}"
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to update interface: {result.stderr}")

        # For running VMs, we may need to manually update OVS port
        # The old bridge connection should be removed, new one added
        if vm_is_running:
            # Find the vnet interface by checking domiflist
            domiflist_result = subprocess.run(
                ['virsh', 'domiflist', vm_name],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )

            if domiflist_result.returncode == 0:
                # Find interface with our MAC and new bridge
                for line in domiflist_result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 5:
                        vnet_interface = parts[0]
                        source_bridge = parts[2]
                        intf_mac = parts[4]

                        if intf_mac.lower() == mac_address.lower():
                            # Check if interface is in the new OVS bridge
                            check_port = subprocess.run(
                                ['ovs-vsctl', 'port-to-br', vnet_interface],
                                capture_output=True,
                                text=True
                            )

                            current_bridge = check_port.stdout.strip() if check_port.returncode == 0 else None

                            # If interface is in old bridge, remove it
                            if current_bridge and current_bridge != new_bridge:
                                logger.info(
                                    f"Removing {vnet_interface} from old bridge {current_bridge}"
                                )
                                subprocess.run(
                                    ['ovs-vsctl', 'del-port', current_bridge, vnet_interface],
                                    capture_output=True,
                                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                                )

                            # Add to new bridge if not already there
                            if current_bridge != new_bridge:
                                logger.info(
                                    f"Adding {vnet_interface} to new bridge {new_bridge}"
                                )
                                add_result = subprocess.run(
                                    ['ovs-vsctl', 'add-port', new_bridge, vnet_interface],
                                    capture_output=True,
                                    text=True,
                                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                                )
                                if add_result.returncode != 0:
                                    raise RuntimeError(
                                        f"Failed to add {vnet_interface} to OVS bridge "
                                        f"{new_bridge}: {add_result.stderr}"
                                    )
                            break

        logger.info(
            f"Successfully updated interface bridge: {vm_name} MAC={mac_address} "
            f"-> {new_bridge} (immediate={vm_is_running})"
        )

        return {
            'status': 'updated' if vm_is_running else 'configured',
            'vm': vm_name,
            'mac': mac_address,
            'old_bridge': old_bridge,
            'new_bridge': new_bridge,
            'immediate': vm_is_running
        }

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout updating interface on {vm_name}")
    except Exception as e:
        raise RuntimeError(f"Error updating interface on {vm_name}: {e}")
    finally:
        # Clean up temp file
        if xml_path and os.path.exists(xml_path):
            os.remove(xml_path)


def list_ovs_bridges() -> List[str]:
    """
    List all OVS bridges on the system.

    Returns:
        List of bridge names
    """
    try:
        result = subprocess.run(
            ['ovs-vsctl', 'list-br'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            return []

        return [br.strip() for br in result.stdout.strip().split('\n') if br.strip()]

    except Exception:
        return []
