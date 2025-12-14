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
    parse_device_name
)
from config import (
    LIBVIRT_IMAGES_PATH,
    VEOS_BASE_IMAGE_PATH,
    MGMT_BRIDGE,
    VEOS_CPU,
    VEOS_RAM_MB
)

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


class NodeCreationTransaction:
    """
    Context manager for atomic node creation with automatic rollback on failure.

    Tracks created resources and cleans them up if an exception occurs.
    """

    def __init__(self, node_name: str):
        self.node_name = node_name
        self.created_resources = []
        self.logger = logging.getLogger('nodebuilder')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.logger.error(f"Error creating node {self.node_name}: {exc_val}")
            self.rollback()
        return False  # Re-raise exception

    def add_resource(self, resource_type: str, resource_id: str):
        """Track a created resource for potential rollback."""
        self.created_resources.append((resource_type, resource_id))

    def rollback(self):
        """Clean up all created resources in reverse order."""
        self.logger.info(f"Rolling back creation of {self.node_name}")

        for resource_type, resource_id in reversed(self.created_resources):
            try:
                self.logger.info(f"Rolling back {resource_type}: {resource_id}")

                if resource_type == 'vm':
                    # Destroy running VM
                    subprocess.run(
                        ['virsh', 'destroy', resource_id],
                        capture_output=True, timeout=30
                    )
                    # Undefine VM
                    subprocess.run(
                        ['virsh', 'undefine', resource_id],
                        capture_output=True, timeout=30
                    )

                elif resource_type == 'image':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)

                elif resource_type == 'bridge':
                    delete_ovs_bridge(resource_id)

                elif resource_type == 'xml':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)

            except Exception as e:
                self.logger.warning(
                    f"Rollback failed for {resource_type}:{resource_id}: {e}"
                )


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
    """
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
        timeout=60
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
        timeout=60
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
        timeout=30
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
        timeout=60
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
        timeout=60
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

    with NodeCreationTransaction(name) as txn:
        # Step 1: Copy base image
        logger.info(f"Copying base image for {name}")
        image_path = copy_base_image(name)
        txn.add_resource('image', image_path)

        # Step 2: Process connections - create bridges and determine ports
        processed_connections = []
        local_port_counter = 1

        for conn in connections:
            target_device = conn['target_device']

            # Get next available port on target device
            target_port = find_next_available_port(target_device)

            # Local port for new node (contiguous from Ethernet1)
            local_port = f"Ethernet{local_port_counter}"
            local_port_counter += 1

            # Generate bridge name
            bridge_name = generate_bridge_name(
                name, local_port,
                target_device, target_port
            )

            # Create the OVS bridge
            logger.info(f"Creating OVS bridge: {bridge_name}")
            create_ovs_bridge(bridge_name)
            txn.add_resource('bridge', bridge_name)

            processed_connections.append({
                'target_device': target_device,
                'target_port': target_port,
                'local_port': local_port,
                'bridge': bridge_name
            })

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
        for conn in processed_connections:
            target_device = conn['target_device']
            bridge_name = conn['bridge']

            logger.info(
                f"Attaching interface to {target_device} on bridge {bridge_name}"
            )
            attach_interface_to_vm(target_device, bridge_name)

        # Clean up temp XML file (not needed after define)
        if os.path.exists(xml_path):
            os.remove(xml_path)

        logger.info(f"Successfully created vEOS node: {name}")

        return {
            'status': 'created',
            'name': name,
            'ip': ip,
            'mac': mac,
            'connections': processed_connections
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
            timeout=30
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
            timeout=30
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
    Restore all user-added nodes from user_nodes.yaml.

    This is called when the user clicks "Restore User Nodes" in the UI
    after the original topology is up and running.

    IMPORTANT: We restore in two phases:
    1. Create ALL OVS bridges for ALL nodes first
    2. Then start ALL VMs

    This ensures that if Node B connects to Node A, the bridge exists
    before either VM tries to use it. Nodes are processed in creation
    order (as stored in user_nodes.yaml) to maintain consistency.

    Returns:
        Dict with list of restored nodes and any errors
    """
    from persistence import load_user_nodes
    from config import USER_NODES_PATH

    logger.info("Restoring all user nodes")

    user_data = load_user_nodes(USER_NODES_PATH)
    nodes = user_data.get('nodes', [])

    if not nodes:
        return {
            'status': 'no_nodes',
            'message': 'No user-added nodes to restore',
            'restored': [],
            'errors': []
        }

    # Phase 1: Create ALL OVS bridges for ALL nodes first
    # This ensures bridges exist before any VM tries to use them
    logger.info("Phase 1: Creating OVS bridges for all user nodes")
    bridges_created = []

    for node_entry in nodes:
        for node_name, node_info in node_entry.items():
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
                            bridges_created.append(bridge_name)
                        elif result['status'] == 'exists':
                            logger.debug(f"OVS bridge already exists: {bridge_name}")
                    except Exception as e:
                        logger.warning(f"Failed to create bridge {bridge_name}: {e}")

    logger.info(f"Phase 1 complete: {len(bridges_created)} bridges created")

    # Phase 2: Start all VMs in creation order
    logger.info("Phase 2: Starting user node VMs in creation order")
    restored = []
    errors = []

    for node_entry in nodes:
        for node_name, node_info in node_entry.items():
            # Check if VM exists
            if not vm_exists(node_name):
                errors.append({
                    'name': node_name,
                    'status': 'error',
                    'error': 'VM not defined - may need to be recreated'
                })
                continue

            # Check current state
            state = get_vm_state(node_name)

            if state == 'running':
                logger.info(f"Node {node_name} is already running")
                restored.append({
                    'name': node_name,
                    'status': 'already_running',
                    'ip': node_info.get('ip_addr', '')
                })
                continue

            # Start the VM
            try:
                start_vm(node_name)
                logger.info(f"Started VM: {node_name}")
                restored.append({
                    'name': node_name,
                    'status': 'started',
                    'ip': node_info.get('ip_addr', '')
                })
            except Exception as e:
                logger.error(f"Failed to start VM {node_name}: {e}")
                errors.append({
                    'name': node_name,
                    'status': 'error',
                    'error': str(e)
                })

    logger.info(f"Phase 2 complete: {len(restored)} nodes restored, {len(errors)} errors")

    return {
        'status': 'completed',
        'restored': restored,
        'errors': errors,
        'total': len(nodes),
        'bridges_created': len(bridges_created)
    }


def get_user_nodes_status() -> Dict:
    """
    Get status of all user-added nodes.

    Returns:
        Dict with node statuses and whether restoration is needed
    """
    from persistence import load_user_nodes
    from config import USER_NODES_PATH

    user_data = load_user_nodes(USER_NODES_PATH)
    nodes = user_data.get('nodes', [])

    if not nodes:
        return {
            'has_user_nodes': False,
            'nodes': [],
            'needs_restore': False
        }

    node_statuses = []
    needs_restore = False

    for node_entry in nodes:
        for node_name, node_info in node_entry.items():
            state = get_vm_state(node_name)
            exists = vm_exists(node_name)

            status = {
                'name': node_name,
                'ip': node_info.get('ip_addr', ''),
                'exists': exists,
                'state': state,
                'running': state == 'running'
            }

            if exists and state != 'running':
                needs_restore = True

            node_statuses.append(status)

    return {
        'has_user_nodes': True,
        'nodes': node_statuses,
        'needs_restore': needs_restore
    }
