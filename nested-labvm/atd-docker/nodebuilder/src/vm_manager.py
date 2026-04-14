"""
VM Manager for Nodebuilder Service

Handles:
- Creating vEOS virtual machines
- Managing VM lifecycle (define, start, stop, delete)
- XML generation for libvirt
- Rollback on failure
"""

import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from interface_manager import (
    create_ovs_bridge,
    delete_ovs_bridge,
    attach_interface_to_vm,
    generate_bridge_name,
    find_next_available_port,
    parse_device_name,
    update_interface_bridge,
    extract_port_number
)
from connection_manager import process_connection_for_creation
from config import (
    LIBVIRT_IMAGES_PATH,
    VEOS_BASE_IMAGE_PATH,
    MGMT_BRIDGE,
    VEOS_CPU,
    VEOS_RAM_MB,
    SUBPROCESS_TIMEOUT_DEFAULT,
    SUBPROCESS_TIMEOUT_LONG
)
from resource_manager import ResourceTransaction

logger = logging.getLogger('nodebuilder')

# Base XML template for vEOS (minimal, interfaces added dynamically)
VEOS_BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<domain type='kvm'>
  <memory unit='MiB'>{ram}</memory>
  <currentMemory unit='MiB'>{ram}</currentMemory>
  <vcpu>{cpu}</vcpu>
  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode="host-model" match="exact">
    <model fallback="allow"/>
  </cpu>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
    <controller type='usb' index='0' model='ich9-ehci1'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x06' function='0x7'/>
    </controller>
    <controller type='ide' index='0'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
    </controller>
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <memballoon model='virtio'/>
  </devices>
  <seclabel type='none'/>
</domain>
"""


def generate_veos_xml(
    name: str,
    sys_mac: str,
    connections: List[Dict]
) -> str:
    """
    Generate libvirt XML for a vEOS VM.

    Args:
        name: VM name
        sys_mac: System MAC address (for management interface)
        connections: List of connection dicts with 'bridge' and 'local_port'

    Returns:
        XML string

    Raises:
        ValueError: If too many connections would cause PCI slot exhaustion
    """
    from config import MAX_PCI_DATA_INTERFACES

    # Validate PCI slot capacity
    # Each vEOS VM has limited PCI slots. USB controller is at slot 0x06, function 0x7.
    # Exceeding this limit would cause VM boot failure with cryptic errors.
    if len(connections) > MAX_PCI_DATA_INTERFACES:
        raise ValueError(
            f"Too many connections ({len(connections)}): maximum is {MAX_PCI_DATA_INTERFACES} "
            f"before PCI slot exhaustion. Reduce connections or split across multiple VMs."
        )

    # Parse the base XML with fixed CPU/RAM from config
    root = ET.fromstring(VEOS_BASE_XML.format(ram=VEOS_RAM_MB, cpu=VEOS_CPU))

    # Add name element
    name_elem = ET.SubElement(root, 'name')
    name_elem.text = name

    # Get devices section
    devices = root.find('./devices')

    # Add disk
    disk = ET.SubElement(devices, 'disk', attrib={
        'type': 'file',
        'device': 'disk'
    })
    ET.SubElement(disk, 'driver', attrib={
        'name': 'qemu',
        'type': 'qcow2',
        'cache': 'directsync',
        'io': 'native'
    })
    ET.SubElement(disk, 'source', attrib={
        'file': f'{LIBVIRT_IMAGES_PATH}/veos/{name}.qcow2'
    })
    ET.SubElement(disk, 'target', attrib={
        'dev': 'hda',
        'bus': 'ide'
    })
    ET.SubElement(disk, 'alias', attrib={'name': 'ide0-0-0'})

    # Add management interface (slot 0x03, function 0x0)
    mgmt_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
    ET.SubElement(mgmt_int, 'source', attrib={'bridge': MGMT_BRIDGE})
    ET.SubElement(mgmt_int, 'mac', attrib={'address': sys_mac})
    ET.SubElement(mgmt_int, 'target', attrib={'dev': name})
    ET.SubElement(mgmt_int, 'model', attrib={'type': 'virtio'})
    ET.SubElement(mgmt_int, 'address', attrib={
        'type': 'pci',
        'domain': '0x0000',
        'bus': '0x00',
        'slot': '0x03',
        'function': '0x0'
    })

    # Add data interfaces
    slot_counter = 3
    intf_counter = 1

    name_short = parse_device_name(name)['code']

    for conn in connections:
        bridge = conn['bridge']
        local_port = conn['local_port']
        port_info = parse_device_name(local_port)

        data_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
        ET.SubElement(data_int, 'source', attrib={'bridge': bridge})
        ET.SubElement(data_int, 'target', attrib={
            'dev': f"{name_short}x{port_info['code']}"
        })
        ET.SubElement(data_int, 'model', attrib={'type': 'virtio'})
        ET.SubElement(data_int, 'virtualport', attrib={'type': 'openvswitch'})
        ET.SubElement(data_int, 'address', attrib={
            'type': 'pci',
            'domain': '0x0000',
            'bus': '0x00',
            'slot': f'0x0{slot_counter}',
            'function': f'0x{intf_counter}'
        })

        # Increment counters (max 7 functions per slot)
        if intf_counter == 7:
            slot_counter += 1
            intf_counter = 0
        else:
            intf_counter += 1

    return ET.tostring(root, encoding='unicode')


def copy_base_image(vm_name: str) -> str:
    """
    Copy the base vEOS image for a new VM.

    Args:
        vm_name: Name of the new VM

    Returns:
        Path to the new disk image
    """
    dest_path = f'{LIBVIRT_IMAGES_PATH}/veos/{vm_name}.qcow2'

    # Ensure destination directory exists
    dest_dir = os.path.dirname(dest_path)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Check if base image exists
    if not os.path.exists(VEOS_BASE_IMAGE_PATH):
        raise RuntimeError(
            f"Base vEOS image not found at {VEOS_BASE_IMAGE_PATH}"
        )

    # Copy the image
    shutil.copy2(VEOS_BASE_IMAGE_PATH, dest_path)

    return dest_path


def define_vm(xml_path: str) -> Dict:
    """
    Define a VM in libvirt from an XML file.

    Args:
        xml_path: Path to the VM XML file

    Returns:
        Dict with status
    """
    result = subprocess.run(
        ['virsh', 'define', xml_path],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_LONG
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    return {'status': 'defined'}


def start_vm(vm_name: str) -> Dict:
    """
    Start a VM.

    Args:
        vm_name: Name of the VM

    Returns:
        Dict with status
    """
    result = subprocess.run(
        ['virsh', 'start', vm_name],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_LONG
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to start VM: {result.stderr}")

    return {'status': 'started'}


def autostart_vm(vm_name: str) -> Dict:
    """
    Set VM to autostart on host boot.

    Args:
        vm_name: Name of the VM

    Returns:
        Dict with status
    """
    result = subprocess.run(
        ['virsh', 'autostart', vm_name],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_DEFAULT
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to set autostart: {result.stderr}")

    return {'status': 'autostart_enabled'}


def destroy_vm(vm_name: str) -> Dict:
    """
    Force stop a running VM.

    Args:
        vm_name: Name of the VM

    Returns:
        Dict with status
    """
    result = subprocess.run(
        ['virsh', 'destroy', vm_name],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_LONG
    )

    # Don't fail if VM is not running
    return {'status': 'destroyed'}


def undefine_vm(vm_name: str) -> Dict:
    """
    Remove a VM definition from libvirt.

    Args:
        vm_name: Name of the VM

    Returns:
        Dict with status
    """
    result = subprocess.run(
        ['virsh', 'undefine', vm_name],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_LONG
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to undefine VM: {result.stderr}")

    return {'status': 'undefined'}


def create_veos_node(
    name: str,
    ip: str,
    mac: str,
    connections: List[Dict]
) -> Dict:
    """
    Create a complete vEOS node with all connections.

    This is the main entry point for node creation. It:
    1. Copies the base disk image
    2. Creates OVS bridges for each connection
    3. Generates and writes VM XML
    4. Defines and starts the VM
    5. Attaches interfaces to target VMs

    All operations are wrapped in a transaction for automatic rollback on failure.
    CPU and RAM are fixed at 2 vCPUs and 2GB RAM (from config).

    Args:
        name: Device name for the new node
        ip: IP address (for reference, assigned via DHCP)
        mac: System MAC address
        connections: List of connection configs with 'target_device'

    Returns:
        Dict with creation status and connection details
    """
    logger.info(f"Creating vEOS node: {name} (IP: {ip}, MAC: {mac})")

    # Check if VM already exists in libvirt (orphaned from failed delete)
    from resource_manager import get_resource_manager
    resource_mgr = get_resource_manager()
    if resource_mgr.vm_exists(name):
        raise RuntimeError(
            f"VM '{name}' already exists in libvirt. This may be an orphaned VM "
            f"from a previous failed deletion. Please manually run 'virsh undefine {name}' "
            f"to clean it up."
        )

    with ResourceTransaction(name, device_type='node') as txn:
        # Step 1: Copy base image
        logger.info(f"Copying base image for {name}")
        image_path = copy_base_image(name)
        txn.add_resource('image', image_path)

        # Step 2: Process connections - create bridges and determine ports
        processed_connections = []
        local_port_counter = 1

        for conn in connections:
            # Local port for new node (contiguous from Ethernet1)
            local_port = f"Ethernet{local_port_counter}"
            local_port_counter += 1

            # Process connection using shared helper
            processed_conn = process_connection_for_creation(
                source_device=name,
                local_port=local_port,
                target_device=conn['target_device'],
                target_port=conn.get('target_port'),
                txn=txn
            )
            processed_connections.append(processed_conn)

        # Step 3: Generate VM XML
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_veos_xml(name, mac, processed_connections)

        # Write XML to temp file
        xml_path = f'/tmp/{name}.xml'
        with open(xml_path, 'w') as f:
            f.write(xml_content)
        txn.add_resource('xml', xml_path)

        # Step 4: Define the VM
        logger.info(f"Defining VM {name}")
        define_vm(xml_path)
        txn.add_resource('vm', name)

        # Step 5: Start the VM
        logger.info(f"Starting VM {name}")
        start_vm(name)

        # Note: We intentionally do NOT set autostart on user-added VMs.
        # User-added nodes should be restored manually after the original
        # topology is up and running. This is handled by the "Restore User Nodes"
        # button in the UI which calls the /restore-user-nodes endpoint.

        # Step 6: Attach interfaces to target VMs
        # Use shared slot reuse logic to check for orphaned slots that can be reused
        from slot_reuse import (
            process_connections_with_slot_reuse,
            apply_mutual_exclusivity
        )

        targets_reused_slots, targets_need_reboot = process_connections_with_slot_reuse(
            processed_connections
        )

        # Clean up temp XML file (not needed after define)
        if os.path.exists(xml_path):
            os.remove(xml_path)

        logger.info(f"Successfully created vEOS node: {name}")

        # Apply mutual exclusivity: if a device needs reboot for ANY connection,
        # it should only appear in targets_need_reboot
        final_reused, final_reboot = apply_mutual_exclusivity(
            targets_reused_slots, targets_need_reboot
        )

        return {
            'status': 'created',
            'name': name,
            'ip': ip,
            'mac': mac,
            'connections': processed_connections,
            'targets_reused_slots': final_reused,
            'targets_need_reboot': final_reboot
        }


def get_vm_state(vm_name: str) -> str:
    """
    Get the current state of a VM.

    Args:
        vm_name: Name of the VM

    Returns:
        State string: 'running', 'shut off', 'paused', or 'unknown'
    """
    try:
        result = subprocess.run(
            ['virsh', 'domstate', vm_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            return 'unknown'

        return result.stdout.strip().lower()

    except Exception:
        return 'unknown'


def vm_exists(vm_name: str) -> bool:
    """
    Check if a VM is defined in libvirt.

    Args:
        vm_name: Name of the VM

    Returns:
        True if VM exists (defined), False otherwise
    """
    try:
        result = subprocess.run(
            ['virsh', 'dominfo', vm_name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )
        return result.returncode == 0

    except Exception:
        return False


def restore_user_node(node_name: str, node_info: Dict) -> Dict:
    """
    Restore a single user-added node.

    This starts the VM if it's defined but not running,
    and ensures OVS bridges exist for its connections.

    Args:
        node_name: Name of the node
        node_info: Node info dict from user_nodes.yaml

    Returns:
        Dict with restore status
    """
    logger.info(f"Restoring user node: {node_name}")

    # Check if VM exists
    if not vm_exists(node_name):
        return {
            'name': node_name,
            'status': 'error',
            'error': 'VM not defined - may need to be recreated'
        }

    # Check current state
    state = get_vm_state(node_name)

    if state == 'running':
        logger.info(f"Node {node_name} is already running")
        return {
            'name': node_name,
            'status': 'already_running'
        }

    # Ensure OVS bridges exist for connections
    neighbors = node_info.get('neighbors', [])
    for neighbor in neighbors:
        local_port = neighbor.get('port', '')
        target_device = neighbor.get('neighborDevice', '')
        target_port = neighbor.get('neighborPort', '')

        if local_port and target_device and target_port:
            bridge_name = generate_bridge_name(
                node_name, local_port,
                target_device, target_port
            )

            try:
                result = create_ovs_bridge(bridge_name)
                if result['status'] == 'created':
                    logger.info(f"Created OVS bridge: {bridge_name}")
            except Exception as e:
                logger.warning(f"Failed to create bridge {bridge_name}: {e}")

    # Start the VM
    try:
        start_vm(node_name)
        logger.info(f"Started VM: {node_name}")

        return {
            'name': node_name,
            'status': 'started',
            'ip': node_info.get('ip_addr', ''),
        }

    except Exception as e:
        logger.error(f"Failed to start VM {node_name}: {e}")
        return {
            'name': node_name,
            'status': 'error',
            'error': str(e)
        }


def restore_all_user_nodes() -> Dict:
    """
    Restore all user-added nodes, hosts, and firewalls.

    This is called when the user clicks "Restore User Nodes" in the UI
    after the original topology is up and running.

    IMPORTANT: We restore in two phases:
    1. Create ALL OVS bridges for ALL devices first
    2. Then start ALL VMs

    This ensures that if a device connects to another, the bridge exists
    before either VM tries to use it. Devices are processed in creation
    order to maintain consistency.

    Returns:
        Dict with list of restored devices and any errors
    """
    from persistence import (
        load_user_nodes, load_user_hosts, load_user_firewalls,
        load_user_cloudeos, list_user_links
    )
    from config import (
        USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH,
        USER_CLOUDEOS_PATH, USER_LINKS_PATH
    )

    logger.info("Restoring all user nodes, hosts, firewalls, CloudEOS devices, and links")

    # Collect all devices to restore
    all_devices = []

    # Get vEOS nodes
    user_data = load_user_nodes(USER_NODES_PATH)
    nodes = user_data.get('nodes', [])
    for node_entry in nodes:
        for node_name, node_info in node_entry.items():
            all_devices.append({
                'name': node_name,
                'info': node_info,
                'type': 'node',
                'ip_field': 'ip_addr'
            })

    # Get Linux hosts
    hosts_data = load_user_hosts(USER_HOSTS_PATH)
    hosts = hosts_data.get('hosts') or []
    for host_entry in hosts:
        for host_name, host_info in host_entry.items():
            all_devices.append({
                'name': host_name,
                'info': host_info,
                'type': 'host',
                'ip_field': 'mgmt_ip'
            })

    # Get VyOS firewalls
    firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
    firewalls = firewalls_data.get('firewalls') or []
    for fw_entry in firewalls:
        for fw_name, fw_info in fw_entry.items():
            all_devices.append({
                'name': fw_name,
                'info': fw_info,
                'type': 'firewall',
                'ip_field': 'mgmt_ip'
            })

    # Get CloudEOS devices
    try:
        cloudeos_data = load_user_cloudeos(USER_CLOUDEOS_PATH)
        cloudeos_devices = cloudeos_data.get('devices') or []
        for ce_entry in cloudeos_devices:
            for ce_name, ce_info in ce_entry.items():
                all_devices.append({
                    'name': ce_name,
                    'info': ce_info,
                    'type': 'cloudeos',
                    'ip_field': 'ip_addr'
                })
    except Exception as e:
        logger.warning(f"Failed to load CloudEOS devices for restore: {e}")

    # Get user-added links between original topology nodes
    user_links = []
    try:
        user_links = list_user_links(USER_LINKS_PATH)
    except Exception as e:
        logger.warning(f"Failed to load user links for restore: {e}")

    if not all_devices and not user_links:
        return {
            'status': 'no_nodes',
            'message': 'No user-added devices or links to restore',
            'restored': [],
            'errors': []
        }

    # Phase 1: Create ALL OVS bridges and attach to target devices
    # This ensures bridges exist and target devices have interfaces before VMs start
    logger.info("Phase 1: Creating OVS bridges and attaching to target devices")
    bridges_created = []
    interfaces_attached = []

    for device in all_devices:
        device_name = device['name']
        device_info = device['info']
        device_type = device['type']

        # vEOS nodes use 'neighbors' format
        if device_type == 'node':
            neighbors = device_info.get('neighbors', [])
            for neighbor in neighbors:
                local_port = neighbor.get('port', '')
                target_device = neighbor.get('neighborDevice', '')
                target_port = neighbor.get('neighborPort', '')

                if local_port and target_device and target_port:
                    bridge_name = generate_bridge_name(
                        device_name, local_port,
                        target_device, target_port
                    )
                    try:
                        result = create_ovs_bridge(bridge_name)
                        if result['status'] == 'created':
                            logger.info(f"Created OVS bridge: {bridge_name}")
                            bridges_created.append(bridge_name)
                        # Always try to attach to target (may already be attached)
                        attach_interface_to_vm(target_device, bridge_name)
                        interfaces_attached.append(f"{target_device}:{bridge_name}")
                        logger.info(f"Attached interface to {target_device} on {bridge_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create/attach bridge {bridge_name}: {e}")

        # Linux hosts use 'connection' format
        elif device_type == 'host':
            connection = device_info.get('connection')
            if connection:
                target_device = connection.get('target_device', '')
                target_port = connection.get('target_port', '')
                if target_device and target_port:
                    bridge_name = generate_bridge_name(
                        device_name, 'eth1',
                        target_device, target_port
                    )
                    try:
                        result = create_ovs_bridge(bridge_name)
                        if result['status'] == 'created':
                            logger.info(f"Created OVS bridge: {bridge_name}")
                            bridges_created.append(bridge_name)
                        # Always try to attach to target (may already be attached)
                        attach_interface_to_vm(target_device, bridge_name)
                        interfaces_attached.append(f"{target_device}:{bridge_name}")
                        logger.info(f"Attached interface to {target_device} on {bridge_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create/attach bridge {bridge_name}: {e}")

        # VyOS firewalls use 'inside_interface' and 'outside_interface' format
        elif device_type == 'firewall':
            for iface_key in ['inside_interface', 'outside_interface']:
                iface = device_info.get(iface_key, {})
                target_device = iface.get('target_device', '')
                target_port = iface.get('target_port', '')
                local_port = 'eth1' if iface_key == 'inside_interface' else 'eth2'
                if target_device and target_port:
                    bridge_name = generate_bridge_name(
                        device_name, local_port,
                        target_device, target_port
                    )
                    try:
                        result = create_ovs_bridge(bridge_name)
                        if result['status'] == 'created':
                            logger.info(f"Created OVS bridge: {bridge_name}")
                            bridges_created.append(bridge_name)
                        # Always try to attach to target (may already be attached)
                        attach_interface_to_vm(target_device, bridge_name)
                        interfaces_attached.append(f"{target_device}:{bridge_name}")
                        logger.info(f"Attached interface to {target_device} on {bridge_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create/attach bridge {bridge_name}: {e}")

        # CloudEOS devices use same 'neighbors' format as vEOS nodes
        elif device_type == 'cloudeos':
            neighbors = device_info.get('neighbors', [])
            for neighbor in neighbors:
                local_port = neighbor.get('port', '')
                target_device = neighbor.get('neighborDevice', '')
                target_port = neighbor.get('neighborPort', '')

                if local_port and target_device and target_port:
                    bridge_name = generate_bridge_name(
                        device_name, local_port,
                        target_device, target_port
                    )
                    try:
                        result = create_ovs_bridge(bridge_name)
                        if result['status'] == 'created':
                            logger.info(f"Created OVS bridge: {bridge_name}")
                            bridges_created.append(bridge_name)
                        attach_interface_to_vm(target_device.lower(), bridge_name)
                        interfaces_attached.append(f"{target_device}:{bridge_name}")
                        logger.info(f"Attached interface to {target_device} on {bridge_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create/attach bridge {bridge_name}: {e}")

    # Phase 1b: Restore user-added links between original topology nodes
    # These are OVS bridges connecting two existing topology devices
    links_restored = 0
    if user_links:
        logger.info(f"Phase 1b: Restoring {len(user_links)} user-added links")
        for link in user_links:
            source_device = link.get('source_device', '')
            source_port = link.get('source_port', '')
            target_device = link.get('target_device', '')
            target_port = link.get('target_port', '')
            bridge_name = link.get('bridge_name', '')

            if not bridge_name:
                bridge_name = generate_bridge_name(
                    source_device, source_port,
                    target_device, target_port
                )

            try:
                result = create_ovs_bridge(bridge_name)
                if result['status'] == 'created':
                    logger.info(f"Created link bridge: {bridge_name}")
                    bridges_created.append(bridge_name)

                # Attach to both endpoints (lowercase for virsh domain names)
                attach_interface_to_vm(source_device.lower(), bridge_name)
                interfaces_attached.append(f"{source_device}:{bridge_name}")
                attach_interface_to_vm(target_device.lower(), bridge_name)
                interfaces_attached.append(f"{target_device}:{bridge_name}")

                links_restored += 1
                logger.info(
                    f"Restored link: {source_device}:{source_port} <-> "
                    f"{target_device}:{target_port}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to restore link {source_device}:{source_port} <-> "
                    f"{target_device}:{target_port}: {e}"
                )

    logger.info(
        f"Phase 1 complete: {len(bridges_created)} bridges created, "
        f"{len(interfaces_attached)} interfaces attached, "
        f"{links_restored} user links restored"
    )

    # Phase 2: Start all VMs in creation order
    logger.info("Phase 2: Starting user VMs in creation order")
    restored = []
    errors = []

    for device in all_devices:
        device_name = device['name']
        device_info = device['info']
        device_type = device['type']
        ip_field = device['ip_field']

        # Check if VM exists
        if not vm_exists(device_name):
            errors.append({
                'name': device_name,
                'type': device_type,
                'status': 'error',
                'error': 'VM not defined - may need to be recreated'
            })
            continue

        # Check current state
        state = get_vm_state(device_name)

        if state == 'running':
            logger.info(f"Device {device_name} is already running")
            restored.append({
                'name': device_name,
                'type': device_type,
                'status': 'already_running',
                'ip': device_info.get(ip_field, '')
            })
            continue

        # Start the VM
        try:
            start_vm(device_name)
            logger.info(f"Started VM: {device_name}")
            restored.append({
                'name': device_name,
                'type': device_type,
                'status': 'started',
                'ip': device_info.get(ip_field, '')
            })
        except Exception as e:
            logger.error(f"Failed to start VM {device_name}: {e}")
            errors.append({
                'name': device_name,
                'type': device_type,
                'status': 'error',
                'error': str(e)
            })

    logger.info(f"Phase 2 complete: {len(restored)} devices restored, {len(errors)} errors")

    # Phase 3: Cleanup orphaned bridges if no VMs started successfully
    # This handles the case where bridges were created but all VM starts failed
    successfully_started = [r for r in restored if r.get('status') == 'started']
    bridges_cleaned = []

    if len(successfully_started) == 0 and len(bridges_created) > 0 and len(errors) > 0:
        logger.warning("No VMs started successfully - cleaning up orphaned bridges")
        for bridge_name in bridges_created:
            try:
                result = delete_ovs_bridge(bridge_name)
                if result.get('status') in ('deleted', 'not_found'):
                    bridges_cleaned.append(bridge_name)
                    logger.info(f"Cleaned up orphaned bridge: {bridge_name}")
            except Exception as e:
                logger.warning(f"Failed to cleanup bridge {bridge_name}: {e}")

        if bridges_cleaned:
            logger.info(f"Phase 3 complete: Cleaned up {len(bridges_cleaned)} orphaned bridges")

    # Log summary statistics
    logger.info(
        f"Restore summary: {len(successfully_started)} started, "
        f"{len([r for r in restored if r.get('status') == 'already_running'])} already running, "
        f"{len(errors)} errors, "
        f"{len(bridges_created) - len(bridges_cleaned)} bridges active"
    )

    return {
        'status': 'completed',
        'restored': restored,
        'errors': errors,
        'total': len(all_devices),
        'bridges_created': len(bridges_created),
        'bridges_cleaned': len(bridges_cleaned)
    }


def get_user_nodes_status() -> Dict:
    """
    Get status of all user-added nodes, hosts, and firewalls.

    Returns:
        Dict with node statuses and whether restoration is needed
    """
    from persistence import load_user_nodes, load_user_hosts, load_user_firewalls
    from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH

    node_statuses = []
    needs_restore = False

    # Get vEOS nodes
    user_data = load_user_nodes(USER_NODES_PATH)
    nodes = user_data.get('nodes', [])

    for node_entry in nodes:
        for node_name, node_info in node_entry.items():
            state = get_vm_state(node_name)
            exists = vm_exists(node_name)

            status = {
                'name': node_name,
                'ip': node_info.get('ip_addr', ''),
                'type': 'node',
                'exists': exists,
                'state': state,
                'running': state == 'running'
            }

            if exists and state != 'running':
                needs_restore = True

            node_statuses.append(status)

    # Get Linux hosts
    hosts_data = load_user_hosts(USER_HOSTS_PATH)
    hosts = hosts_data.get('hosts') or []

    for host_entry in hosts:
        for host_name, host_info in host_entry.items():
            state = get_vm_state(host_name)
            exists = vm_exists(host_name)

            status = {
                'name': host_name,
                'ip': host_info.get('mgmt_ip', ''),
                'type': 'host',
                'exists': exists,
                'state': state,
                'running': state == 'running'
            }

            if exists and state != 'running':
                needs_restore = True

            node_statuses.append(status)

    # Get VyOS firewalls
    firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
    firewalls = firewalls_data.get('firewalls') or []

    for fw_entry in firewalls:
        for fw_name, fw_info in fw_entry.items():
            state = get_vm_state(fw_name)
            exists = vm_exists(fw_name)

            status = {
                'name': fw_name,
                'ip': fw_info.get('mgmt_ip', ''),
                'type': 'firewall',
                'exists': exists,
                'state': state,
                'running': state == 'running'
            }

            if exists and state != 'running':
                needs_restore = True

            node_statuses.append(status)

    if not node_statuses:
        return {
            'has_user_nodes': False,
            'nodes': [],
            'needs_restore': False
        }

    return {
        'has_user_nodes': True,
        'nodes': node_statuses,
        'needs_restore': needs_restore
    }
