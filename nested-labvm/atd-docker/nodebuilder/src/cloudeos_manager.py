"""
CloudEOS Manager for Nodebuilder Service

Handles CloudEOS VM lifecycle:
- Creating CloudEOS virtual machines with variable connections
- Interface management (mgmt + variable data interfaces)
- VM lifecycle (define, start, stop, delete)
- Persistence via user_cloudeos.yaml
"""

import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from config import (
    LIBVIRT_IMAGES_PATH, CLOUDEOS_BASE_IMAGE_PATH, CLOUDEOS_CPU,
    CLOUDEOS_RAM_MB, MAX_CLOUDEOS_PER_TOPOLOGY, MGMT_BRIDGE,
    USER_CLOUDEOS_PATH, SUBPROCESS_TIMEOUT_DEFAULT, SUBPROCESS_TIMEOUT_LONG
)
from interface_manager import (
    create_ovs_bridge,
    delete_ovs_bridge,
    attach_interface_to_vm,
    generate_bridge_name,
    find_next_available_port
)
from bridge_utils import generate_interface_target_name
from connection_manager import process_connection_for_creation
from resource_manager import ResourceTransaction, get_resource_manager
from persistence import (
    load_user_cloudeos, save_user_cloudeos_pending, update_user_cloudeos_status,
    remove_user_cloudeos, get_user_cloudeos_device,
    get_cloudeos_count as persistence_get_cloudeos_count
)
from slot_reuse import attach_interface_with_slot_reuse, apply_mutual_exclusivity

logger = logging.getLogger('nodebuilder')


# Base XML template for CloudEOS VM
CLOUDEOS_BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
    <controller type='virtio-serial' index='0'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x07' function='0x0'/>
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


def get_cloudeos_count() -> int:
    """
    Count active (non-creating) CloudEOS devices from user_cloudeos.yaml.

    Only counts devices without 'creating' status.

    Returns:
        Number of active CloudEOS devices
    """
    return persistence_get_cloudeos_count(USER_CLOUDEOS_PATH)


def generate_cloudeos_xml(name: str, connections: Optional[List[Dict]] = None) -> str:
    """
    Generate libvirt XML for a CloudEOS VM.

    Interfaces:
    - eth0 (Management): vmgmt bridge
    - Additional interfaces per connection on OVS bridges (starting at slot 0x04)

    Args:
        name: VM name
        connections: List of connection dicts, each with 'bridge' key

    Returns:
        XML string
    """
    if connections is None:
        connections = []

    root = ET.fromstring(CLOUDEOS_BASE_XML.format(ram=CLOUDEOS_RAM_MB, cpu=CLOUDEOS_CPU))

    # Add name element
    name_elem = ET.SubElement(root, 'name')
    name_elem.text = name

    # Get devices section
    devices = root.find('./devices')

    # Add disk (main OS disk)
    disk = ET.SubElement(devices, 'disk', attrib={
        'type': 'file',
        'device': 'disk'
    })
    ET.SubElement(disk, 'driver', attrib={
        'name': 'qemu',
        'type': 'qcow2',
        'cache': 'writeback'
    })
    ET.SubElement(disk, 'source', attrib={
        'file': f'{LIBVIRT_IMAGES_PATH}/cloudeos/{name}.qcow2'
    })
    ET.SubElement(disk, 'target', attrib={
        'dev': 'vda',
        'bus': 'virtio'
    })

    # Add eth0: Management interface (vmgmt bridge)
    mgmt_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
    ET.SubElement(mgmt_int, 'source', attrib={'bridge': MGMT_BRIDGE})
    ET.SubElement(mgmt_int, 'target', attrib={'dev': generate_interface_target_name(name, 'mgmt')})
    ET.SubElement(mgmt_int, 'model', attrib={'type': 'virtio'})
    ET.SubElement(mgmt_int, 'address', attrib={
        'type': 'pci',
        'domain': '0x0000',
        'bus': '0x00',
        'slot': '0x03',
        'function': '0x0'
    })

    # Add data interfaces - one per connection, starting at PCI slot 0x04
    # Max 2 data interfaces (slots 0x04, 0x05) before USB controller at 0x06
    MAX_DATA_INTERFACES = 2
    for idx, conn in enumerate(connections[:MAX_DATA_INTERFACES]):
        if not conn.get('bridge'):
            continue

        # PCI slot starts at 0x04 for first data interface
        slot_num = 0x04 + idx
        slot_hex = f'0x{slot_num:02x}'

        iface = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
        ET.SubElement(iface, 'source', attrib={'bridge': conn['bridge']})

        # Generate a target name for this interface
        local_port = conn.get('local_port', f'eth{idx + 1}')
        target_name = generate_interface_target_name(name, local_port)
        ET.SubElement(iface, 'target', attrib={'dev': target_name})
        ET.SubElement(iface, 'model', attrib={'type': 'virtio'})
        ET.SubElement(iface, 'virtualport', attrib={'type': 'openvswitch'})
        ET.SubElement(iface, 'address', attrib={
            'type': 'pci',
            'domain': '0x0000',
            'bus': '0x00',
            'slot': slot_hex,
            'function': '0x0'
        })

    return ET.tostring(root, encoding='unicode')


def copy_cloudeos_base_image(vm_name: str) -> str:
    """
    Copy the base vEOS image for a new CloudEOS VM.

    Args:
        vm_name: Name of the new VM

    Returns:
        Path to the new disk image at /var/lib/libvirt/images/cloudeos/{vm_name}.qcow2
    """
    dest_path = f'{LIBVIRT_IMAGES_PATH}/cloudeos/{vm_name}.qcow2'

    # Ensure destination directory exists
    dest_dir = os.path.dirname(dest_path)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    base_image_path = CLOUDEOS_BASE_IMAGE_PATH

    if not os.path.exists(base_image_path):
        raise RuntimeError(
            f"Base CloudEOS image not found at {base_image_path}. "
            "Ensure the vEOS base image is available at the configured path."
        )

    shutil.copy2(base_image_path, dest_path)
    logger.info(f"Copied base CloudEOS image to {dest_path}")

    return dest_path


def create_cloudeos(
    name: str,
    ip: str,
    device_type: str,
    connections: List[Dict]
) -> Dict:
    """
    Create a CloudEOS VM.

    Uses ResourceTransaction for safe creation with rollback.

    Steps: validate count, save pending, copy image, process connections,
    generate XML, define VM, start VM, attach interfaces, update status.

    Args:
        name: Hostname for the new CloudEOS device
        ip: Management IP address
        device_type: Device type for topology diagram positioning
        connections: List of connection dicts, each with:
            - target_device: Device to connect to
            - target_port: Optional port on target device
            - local_port: Optional local interface name

    Returns:
        Dict with status, name, targets_need_reboot, targets_reused_slots
    """
    logger.info(f"Creating CloudEOS device: {name} (Mgmt IP: {ip})")

    # Check CloudEOS limit
    current_count = get_cloudeos_count()
    if current_count >= MAX_CLOUDEOS_PER_TOPOLOGY:
        raise RuntimeError(
            f"Maximum of {MAX_CLOUDEOS_PER_TOPOLOGY} CloudEOS devices per topology reached"
        )

    # Check if VM already exists in libvirt (orphaned from failed delete)
    resource_mgr = get_resource_manager()
    if resource_mgr.vm_exists(name):
        raise RuntimeError(
            f"VM '{name}' already exists in libvirt. This may be an orphaned VM "
            f"from a previous failed deletion. Please manually run 'virsh undefine {name}' "
            f"to clean it up."
        )

    # Save pending entry before starting creation
    # Use ip_addr and neighbors field names to match vEOS convention
    # (unified_topology.py and vm_manager.py read these field names)
    save_user_cloudeos_pending(name, {
        'ip_addr': ip,
        'device_type': device_type,
    }, USER_CLOUDEOS_PATH)

    with ResourceTransaction(name, device_type='cloudeos') as txn:
        # Step 1: Copy base image
        logger.info(f"Copying base CloudEOS image for {name}")
        image_path = copy_cloudeos_base_image(name)
        txn.add_resource('image', image_path)

        # Step 2: Process connections - create OVS bridges for each
        processed_connections = []
        for idx, conn in enumerate(connections):
            target_device = conn.get('target_device')
            if not target_device:
                continue

            # Determine local port name
            local_port = conn.get('local_port', f'eth{idx + 1}')
            target_port = conn.get('target_port')

            processed_conn = process_connection_for_creation(
                source_device=name,
                local_port=local_port,
                target_device=target_device,
                target_port=target_port,
                txn=txn
            )
            processed_connections.append(processed_conn)

        # Step 3: Generate VM XML
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_cloudeos_xml(name, processed_connections)

        # Write XML to temp file
        xml_path = f'/tmp/{name}.xml'
        with open(xml_path, 'w') as f:
            f.write(xml_content)
        txn.add_resource('xml', xml_path)

        # Step 4: Define the VM
        logger.info(f"Defining VM {name}")
        result = subprocess.run(
            ['virsh', 'define', xml_path],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to define VM: {result.stderr}")
        txn.add_resource('vm', name)

        # Step 5: Start the VM
        logger.info(f"Starting VM {name}")
        result = subprocess.run(
            ['virsh', 'start', name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start VM: {result.stderr}")

        # Step 6: Attach interfaces to target VMs with slot reuse
        targets_reused_slots = []
        targets_need_reboot = []

        for conn in processed_connections:
            attach_result = attach_interface_with_slot_reuse(
                target_device=conn['target_device'],
                target_port=conn['target_port'],
                bridge_name=conn['bridge'],
                connection_dict=conn
            )
            if attach_result.reused_slot:
                targets_reused_slots.append(attach_result.target_device)
            else:
                targets_need_reboot.append(attach_result.target_device)

        # Apply mutual exclusivity: if a device needs reboot for ANY interface,
        # it should only appear in targets_need_reboot
        final_reused_slots, final_need_reboot = apply_mutual_exclusivity(
            targets_reused_slots, targets_need_reboot
        )

        # Clean up temp XML file
        if os.path.exists(xml_path):
            os.remove(xml_path)

        # Step 7: Update persistence status to active with neighbor data
        # Convert processed connections to neighbors format for unified_topology
        neighbors = []
        for conn in processed_connections:
            neighbors.append({
                'port': conn.get('local_port', ''),
                'neighborDevice': conn.get('target_device', ''),
                'neighborPort': conn.get('target_port', '')
            })
        update_user_cloudeos_status(
            name, status='active',
            additional_info={'neighbors': neighbors},
            path=USER_CLOUDEOS_PATH
        )

        logger.info(f"Successfully created CloudEOS device: {name}")

        return {
            'status': 'success',
            'name': name,
            'ip': ip,
            'connections': processed_connections,
            'targets_reused_slots': final_reused_slots,
            'targets_need_reboot': final_need_reboot
        }


def delete_cloudeos(name: str) -> Dict:
    """
    Delete a CloudEOS VM completely.

    Cleans up:
    - VM (destroy and undefine)
    - Disk image
    - OVS bridges for all connections
    - Interfaces detached from target VMs
    - Persistence entry

    Args:
        name: Name of the CloudEOS device to delete

    Returns:
        Dict with status and details
    """
    logger.info(f"Deleting CloudEOS device: {name}")

    resource_mgr = get_resource_manager()
    devices_needing_reboot = []
    bridges_deleted = []
    interfaces_detached = []

    # Step 1: Get device info from persistence BEFORE deleting (need connection info)
    device_entry = get_user_cloudeos_device(name, USER_CLOUDEOS_PATH)
    stored_connections = []

    if device_entry:
        for dev_name, dev_info in device_entry.items():
            stored_connections = dev_info.get('neighbors', [])
            logger.info(f"Found CloudEOS neighbors: {stored_connections}")
            break

    # Step 2: Clean up each connection
    # Neighbors are stored as {'port', 'neighborDevice', 'neighborPort'}
    # but cleanup_connection expects {'bridge', 'target_device', 'target_port'}
    for neighbor in stored_connections:
        if not neighbor:
            continue
        local_port = neighbor.get('port', 'eth')
        target_device = neighbor.get('neighborDevice', '')
        target_port = neighbor.get('neighborPort', '')
        # Reconstruct the bridge name from the neighbor data
        bridge_name = generate_bridge_name(name, local_port, target_device, target_port)
        conn = {
            'bridge': bridge_name,
            'target_device': target_device,
            'target_port': target_port,
            'local_port': local_port
        }
        result = resource_mgr.cleanup_connection(conn, local_port)
        if result.get('target_device'):
            if result['target_device'] not in devices_needing_reboot:
                devices_needing_reboot.append(result['target_device'])
        if result.get('bridge_deleted'):
            if bridge_name:
                bridges_deleted.append(bridge_name)
        if result.get('interface_detached'):
            interfaces_detached.append(target_device)

    # Step 3: Delete VM and disk images
    vm_result = resource_mgr.delete_vm_with_cleanup(
        vm_name=name,
        disk_subdir='cloudeos',
        has_cidata=False
    )

    # Step 4: Remove from persistence
    remove_user_cloudeos(name, USER_CLOUDEOS_PATH)

    logger.info(f"Deleted CloudEOS device: {name}")

    return {
        'status': 'success',
        'name': name,
        'details': {
            'vm_destroyed': vm_result['vm_destroyed'],
            'vm_undefined': vm_result['vm_undefined'],
            'disk_deleted': vm_result['disk_deleted'],
            'bridges_deleted': bridges_deleted,
            'interfaces_detached': interfaces_detached,
            'devices_needing_reboot': devices_needing_reboot
        }
    }


def get_cloudeos_status() -> Dict:
    """
    Return count, max, and available slots for CloudEOS devices.

    Returns:
        Dict with count, max, and available fields
    """
    count = get_cloudeos_count()
    available = max(0, MAX_CLOUDEOS_PER_TOPOLOGY - count)

    return {
        'count': count,
        'max_allowed': MAX_CLOUDEOS_PER_TOPOLOGY,
        'available': available,
        'can_add_more': count < MAX_CLOUDEOS_PER_TOPOLOGY
    }
