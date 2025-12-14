"""
Interface management for Nodebuilder Service

Handles:
- Querying VM interfaces via virsh
- Finding available ports on VMs
- Creating OVS bridges
- Attaching interfaces to VMs
"""

import re
import subprocess
from typing import Dict, List, Optional, Tuple

from validation import get_all_nodes
from config import get_topo_build_path, USER_NODES_PATH


def parse_device_name(dev_name: str) -> Dict:
    """
    Parse a device name into a short code for bridge naming.

    Based on kvm-topo-builder.py parseNames() function.

    Examples:
        spine1 -> sp1
        leaf1 -> le1
        borderleaf1 -> bo1
        Ethernet3 -> 3
        host1 -> ho1

    Args:
        dev_name: Device or port name

    Returns:
        Dict with 'name' (original) and 'code' (short code)
    """
    alpha = ''
    numer = ''
    split_len = 2
    dev_dc = False
    dev_core = False
    tmp_dev_name = ""

    # Handle DC suffix (e.g., spine1-dc1)
    if '-dc' in dev_name.lower() and 'dci' not in dev_name.lower():
        _tmp = dev_name.split('-')
        tmp_dev_name = _tmp[0]
        if len(_tmp) > 1 and 'dc' in _tmp[1].lower():
            dev_dc = _tmp[1]
        for char in tmp_dev_name:
            if char.isalpha():
                alpha += char
            elif char.isdigit():
                numer += char
    # Handle CORE suffix (e.g., leaf1-core)
    elif '-core' in dev_name.lower():
        _tmp = dev_name.split('-')
        tmp_dev_name = _tmp[0]
        if len(_tmp) > 1:
            dev_core = _tmp[1]
        for char in tmp_dev_name:
            if char.isalpha():
                alpha += char
            elif char.isdigit():
                numer += char
    else:
        for char in dev_name:
            if char.isalpha():
                alpha += char
            elif char.isdigit():
                numer += char

    # For Ethernet ports, just use the number
    if 'ethernet' in dev_name.lower():
        dev_short = ''
    else:
        dev_short = alpha[:split_len].lower()

    # Handle DC/CORE codes
    if dev_dc:
        dev_code = dev_dc.lower().replace('c', '')
    elif dev_core:
        dev_code = dev_core.lower().replace('ore', '')
    else:
        dev_code = ""

    return {
        'name': dev_name,
        'code': dev_short + numer + dev_code,
    }


def get_vm_interfaces(vm_name: str) -> List[Dict]:
    """
    Query libvirt for interfaces attached to a VM.

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
            timeout=30
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


def get_used_ports_from_topology(device_name: str) -> List[int]:
    """
    Get list of used port numbers for a device from topology.

    Args:
        device_name: Name of the device

    Returns:
        List of used port numbers (e.g., [1, 2, 3] for Ethernet1-3)
    """
    topo_build_path = get_topo_build_path()
    all_nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)

    used_ports = set()

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


def find_next_available_port(device_name: str) -> str:
    """
    Find the next available Ethernet port on a device.

    vEOS requires contiguous interfaces, so we find the highest
    used port and return the next one.

    Args:
        device_name: Name of the device

    Returns:
        Next available port name (e.g., "Ethernet5")
    """
    used_ports = get_used_ports_from_topology(device_name)

    if not used_ports:
        return "Ethernet1"

    # Find next contiguous port after highest used
    next_port = max(used_ports) + 1
    return f"Ethernet{next_port}"


def get_target_devices_with_ports() -> List[Dict]:
    """
    Get all existing devices with their next available port.

    Returns:
        List of dicts with 'name' and 'next_available_port'
    """
    topo_build_path = get_topo_build_path()
    all_nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)

    devices = []
    for node in all_nodes:
        device_name = node['name']
        next_port = find_next_available_port(device_name)

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


def generate_bridge_name(
    device1: str,
    port1: str,
    device2: str,
    port2: str
) -> str:
    """
    Generate OVS bridge name following kvmbuilder conventions.

    Format: {dev1_code}{port1_num}-{dev2_code}{port2_num}
    Example: sp11-le13 (spine1 Ethernet1 to leaf1 Ethernet3)

    Args:
        device1: First device name
        port1: First port name
        device2: Second device name
        port2: Second port name

    Returns:
        Bridge name string
    """
    dev1_info = parse_device_name(device1)
    port1_info = parse_device_name(port1)
    dev2_info = parse_device_name(device2)
    port2_info = parse_device_name(port2)

    return f"{dev1_info['code']}{port1_info['code']}-{dev2_info['code']}{port2_info['code']}"


def create_ovs_bridge(bridge_name: str) -> Dict:
    """
    Create an OVS bridge.

    Args:
        bridge_name: Name of the bridge to create

    Returns:
        Dict with status and bridge name
    """
    try:
        # Check if bridge already exists
        result = subprocess.run(
            ['ovs-vsctl', 'br-exists', bridge_name],
            capture_output=True,
            timeout=30
        )

        if result.returncode == 0:
            # Bridge already exists
            return {'status': 'exists', 'bridge': bridge_name}

        # Create the bridge
        result = subprocess.run(
            ['ovs-vsctl', 'add-br', bridge_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create bridge: {result.stderr}")

        # Enable BPDU forwarding
        result = subprocess.run(
            ['ovs-vsctl', 'set', 'bridge', bridge_name, 'other-config:forward-bpdu=true'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to set BPDU forwarding: {result.stderr}")

        # Bring the bridge up
        result = subprocess.run(
            ['ip', 'link', 'set', bridge_name, 'up'],
            capture_output=True,
            text=True,
            timeout=30
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
    """
    try:
        result = subprocess.run(
            ['ovs-vsctl', 'del-br', bridge_name],
            capture_output=True,
            text=True,
            timeout=30
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
    mac: Optional[str] = None
) -> Dict:
    """
    Attach a new network interface to a running VM using OVS bridge.

    Uses virsh attach-device with an XML file that includes the
    OVS virtualport type, which is required for OVS bridges.

    Args:
        vm_name: Name of the VM
        bridge_name: Name of the OVS bridge to connect to
        mac: Optional MAC address for the interface

    Returns:
        Dict with status and details
    """
    import tempfile

    # Generate interface XML with OVS virtualport type
    mac_element = f"<mac address='{mac}'/>" if mac else ""
    interface_xml = f"""<interface type='bridge'>
  {mac_element}
  <source bridge='{bridge_name}'/>
  <model type='virtio'/>
  <virtualport type='openvswitch'/>
</interface>"""

    xml_path = None
    try:
        # Write XML to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(interface_xml)
            xml_path = f.name

        # Attach using virsh attach-device
        cmd = [
            'virsh', 'attach-device', vm_name, xml_path,
            '--config',  # Persist across reboots
            '--live'     # Apply immediately
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to attach interface: {result.stderr}")

        return {
            'status': 'attached',
            'vm': vm_name,
            'bridge': bridge_name
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

    Args:
        vm_name: Name of the VM
        mac: MAC address of the interface to detach

    Returns:
        Dict with status
    """
    try:
        result = subprocess.run(
            ['virsh', 'detach-interface', vm_name,
             '--type', 'bridge',
             '--mac', mac,
             '--config',
             '--live'],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to detach interface: {result.stderr}")

        return {'status': 'detached', 'vm': vm_name, 'mac': mac}

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout detaching interface from {vm_name}")
    except Exception as e:
        raise RuntimeError(f"Error detaching interface from {vm_name}: {e}")


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
            timeout=30
        )

        if result.returncode != 0:
            return []

        return [br.strip() for br in result.stdout.strip().split('\n') if br.strip()]

    except Exception:
        return []
