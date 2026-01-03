"""
Firewall Manager for Nodebuilder Service

Handles VyOS firewall VM lifecycle:
- Creating VyOS virtual machines with 3 interfaces
- Cloud-init ISO generation for VyOS provisioning
- Interface management (mgmt, inside, outside)
- VM lifecycle (define, start, stop, delete)
"""

import logging
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, Optional

from config import (
    LIBVIRT_IMAGES_PATH,
    get_firewall_base_image_path,
    FIREWALL_CPU,
    FIREWALL_RAM_MB,
    MAX_FIREWALLS_PER_TOPOLOGY,
    MGMT_BRIDGE,
    CLOUD_INIT_TEMPLATES_PATH,
    USER_FIREWALLS_PATH,
    SUBPROCESS_TIMEOUT_DEFAULT,
    SUBPROCESS_TIMEOUT_LONG
)
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
from resource_manager import ResourceTransaction

logger = logging.getLogger('nodebuilder')


# Base XML template for VyOS Firewall VM
FIREWALL_BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
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


def get_firewall_count() -> int:
    """
    Get the current count of user-added VyOS firewalls.

    Only counts firewalls with 'created' status, not pending 'creating' entries.

    Returns:
        Number of firewalls currently defined
    """
    from persistence import load_user_firewalls
    firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
    firewalls = firewalls_data.get('firewalls', [])
    # Exclude 'creating' status entries (pending creations)
    count = 0
    for fw in firewalls:
        if isinstance(fw, dict):
            for fw_info in fw.values():
                if isinstance(fw_info, dict) and fw_info.get('status') != 'creating':
                    count += 1
    return count


def generate_vyos_cloud_init(hostname: str) -> str:
    """
    Generate a cloud-init ISO for VyOS provisioning.

    VyOS uses vyos_config_commands in cloud-init for configuration.
    Only sets hostname - users configure interface IPs manually after boot.
    Uses default VyOS credentials (vyos/arista from base image).

    Args:
        hostname: VM hostname

    Returns:
        Path to the generated ISO file
    """
    # Create temp directory for cloud-init files
    temp_dir = tempfile.mkdtemp(prefix='cloudinit_vyos_')

    try:
        # Load and process user-data template
        template_path = os.path.join(CLOUD_INIT_TEMPLATES_PATH, 'vyos-firewall-template.yaml')

        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                user_data = f.read()
        else:
            # Fallback inline template - minimal config
            # Users will configure interface IPs manually after boot
            # Login: vyos / arista (from base image)
            # Note: Values must be in single quotes per VyOS cloud-init requirements
            user_data = """#cloud-config
vyos_config_commands:
  - set system host-name '{hostname}'
  - set system time-zone 'UTC'
  - set system console device ttyS0 speed '115200'
  - set service ssh port '22'
  - set service lldp interface 'all'
"""

        # Replace placeholders (only hostname now)
        user_data = user_data.format(hostname=hostname)

        # Write user-data
        with open(os.path.join(temp_dir, 'user-data'), 'w') as f:
            f.write(user_data)

        # Write meta-data
        meta_data = f"""instance-id: {hostname}
local-hostname: {hostname}
"""
        with open(os.path.join(temp_dir, 'meta-data'), 'w') as f:
            f.write(meta_data)

        # Generate ISO
        iso_path = f'{LIBVIRT_IMAGES_PATH}/firewall/{hostname}-cidata.iso'

        # Ensure directory exists
        os.makedirs(os.path.dirname(iso_path), exist_ok=True)

        # Use genisoimage or mkisofs
        for iso_cmd in ['genisoimage', 'mkisofs']:
            try:
                result = subprocess.run(
                    [iso_cmd, '-output', iso_path, '-volid', 'cidata',
                     '-joliet', '-rock', temp_dir],
                    capture_output=True,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT_DEFAULT
                )
                if result.returncode == 0:
                    logger.info(f"Created VyOS cloud-init ISO: {iso_path}")
                    return iso_path
            except FileNotFoundError:
                continue

        raise RuntimeError("Neither genisoimage nor mkisofs available")

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def generate_firewall_xml(
    name: str,
    inside_connection: Optional[Dict] = None,
    outside_connection: Optional[Dict] = None
) -> str:
    """
    Generate libvirt XML for a VyOS Firewall VM.

    Interfaces:
    - eth0: Management (vmgmt bridge)
    - eth1: Inside network (OVS bridge)
    - eth2: Outside network (OVS bridge)

    Args:
        name: VM name
        inside_connection: Inside interface connection with 'bridge'
        outside_connection: Outside interface connection with 'bridge'

    Returns:
        XML string
    """
    root = ET.fromstring(FIREWALL_BASE_XML.format(ram=FIREWALL_RAM_MB, cpu=FIREWALL_CPU))

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
        'file': f'{LIBVIRT_IMAGES_PATH}/firewall/{name}.qcow2'
    })
    ET.SubElement(disk, 'target', attrib={
        'dev': 'vda',
        'bus': 'virtio'
    })

    # Add cloud-init CDROM
    cdrom = ET.SubElement(devices, 'disk', attrib={
        'type': 'file',
        'device': 'cdrom'
    })
    ET.SubElement(cdrom, 'driver', attrib={
        'name': 'qemu',
        'type': 'raw'
    })
    ET.SubElement(cdrom, 'source', attrib={
        'file': f'{LIBVIRT_IMAGES_PATH}/firewall/{name}-cidata.iso'
    })
    ET.SubElement(cdrom, 'target', attrib={
        'dev': 'hdc',
        'bus': 'ide'
    })
    ET.SubElement(cdrom, 'readonly')

    # Add eth0: Management interface (vmgmt bridge)
    mgmt_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
    ET.SubElement(mgmt_int, 'source', attrib={'bridge': MGMT_BRIDGE})
    ET.SubElement(mgmt_int, 'target', attrib={'dev': f'{name}_mgmt'})
    ET.SubElement(mgmt_int, 'model', attrib={'type': 'virtio'})
    ET.SubElement(mgmt_int, 'address', attrib={
        'type': 'pci',
        'domain': '0x0000',
        'bus': '0x00',
        'slot': '0x03',
        'function': '0x0'
    })

    # Add eth1: Inside interface (OVS bridge)
    if inside_connection and inside_connection.get('bridge'):
        inside_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
        ET.SubElement(inside_int, 'source', attrib={'bridge': inside_connection['bridge']})
        ET.SubElement(inside_int, 'target', attrib={'dev': f'{name}_in'})
        ET.SubElement(inside_int, 'model', attrib={'type': 'virtio'})
        ET.SubElement(inside_int, 'virtualport', attrib={'type': 'openvswitch'})
        ET.SubElement(inside_int, 'address', attrib={
            'type': 'pci',
            'domain': '0x0000',
            'bus': '0x00',
            'slot': '0x04',
            'function': '0x0'
        })

    # Add eth2: Outside interface (OVS bridge)
    if outside_connection and outside_connection.get('bridge'):
        outside_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
        ET.SubElement(outside_int, 'source', attrib={'bridge': outside_connection['bridge']})
        ET.SubElement(outside_int, 'target', attrib={'dev': f'{name}_out'})
        ET.SubElement(outside_int, 'model', attrib={'type': 'virtio'})
        ET.SubElement(outside_int, 'virtualport', attrib={'type': 'openvswitch'})
        ET.SubElement(outside_int, 'address', attrib={
            'type': 'pci',
            'domain': '0x0000',
            'bus': '0x00',
            'slot': '0x05',
            'function': '0x0'
        })

    return ET.tostring(root, encoding='unicode')


def copy_firewall_base_image(vm_name: str) -> str:
    """
    Copy the base VyOS image for a new VM.

    Downloads from GCP if not found locally.

    Args:
        vm_name: Name of the new VM

    Returns:
        Path to the new disk image
    """
    dest_path = f'{LIBVIRT_IMAGES_PATH}/firewall/{vm_name}.qcow2'

    # Ensure destination directory exists
    dest_dir = os.path.dirname(dest_path)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Get base image path (downloads from GCP if not found locally)
    base_image_path = get_firewall_base_image_path()

    # Check if base image exists (after potential download attempt)
    if not os.path.exists(base_image_path):
        raise RuntimeError(
            f"Base VyOS image not found at {base_image_path}. "
            "Run build-vyos.sh to create it, or ensure GCP bucket is accessible."
        )

    # Copy the image
    shutil.copy2(base_image_path, dest_path)
    logger.info(f"Copied base VyOS image to {dest_path}")

    return dest_path


def create_firewall(
    name: str,
    mgmt_ip: str,
    inside_interface: Dict,
    outside_interface: Dict
) -> Dict:
    """
    Create a complete VyOS firewall VM.

    Interface IPs are not configured via cloud-init - users configure
    them manually in VyOS after boot.

    Args:
        name: Hostname for the new firewall
        mgmt_ip: Management IP address (from available pool)
        inside_interface: Inside interface config:
            - target_device: Switch to connect to
            - target_port: Optional port on target switch
        outside_interface: Outside interface config (same structure)

    Returns:
        Dict with creation status and details
    """
    logger.info(f"Creating VyOS firewall: {name} (Mgmt IP: {mgmt_ip})")

    # Check firewall limit
    current_count = get_firewall_count()
    if current_count >= MAX_FIREWALLS_PER_TOPOLOGY:
        raise RuntimeError(
            f"Maximum of {MAX_FIREWALLS_PER_TOPOLOGY} firewall per topology reached"
        )

    # Validate required connection fields
    if not inside_interface.get('target_device'):
        raise ValueError("Inside interface target device is required")
    if not outside_interface.get('target_device'):
        raise ValueError("Outside interface target device is required")

    with ResourceTransaction(name, device_type='firewall') as txn:
        # Step 1: Copy base image
        logger.info(f"Copying base VyOS image for {name}")
        image_path = copy_firewall_base_image(name)
        txn.add_resource('image', image_path)

        # Step 2: Generate cloud-init ISO (hostname only - IPs configured manually after boot)
        logger.info(f"Generating VyOS cloud-init ISO for {name}")
        cidata_path = generate_vyos_cloud_init(hostname=name)
        txn.add_resource('cidata', cidata_path)

        # Step 3: Process inside connection
        inside_conn = None
        if inside_interface.get('target_device'):
            target_device = inside_interface['target_device']
            target_port = inside_interface.get('target_port') or find_next_available_port(target_device)

            bridge_name = generate_bridge_name(
                name, 'eth1',
                target_device, target_port
            )

            logger.info(f"Creating inside OVS bridge: {bridge_name}")
            create_ovs_bridge(bridge_name)
            txn.add_resource('bridge', bridge_name)

            inside_conn = {
                'target_device': target_device,
                'target_port': target_port,
                'local_port': 'eth1',
                'bridge': bridge_name
            }

        # Step 4: Process outside connection
        outside_conn = None
        if outside_interface.get('target_device'):
            target_device = outside_interface['target_device']
            target_port = outside_interface.get('target_port') or find_next_available_port(target_device)

            bridge_name = generate_bridge_name(
                name, 'eth2',
                target_device, target_port
            )

            logger.info(f"Creating outside OVS bridge: {bridge_name}")
            create_ovs_bridge(bridge_name)
            txn.add_resource('bridge', bridge_name)

            outside_conn = {
                'target_device': target_device,
                'target_port': target_port,
                'local_port': 'eth2',
                'bridge': bridge_name
            }

        # Step 5: Generate VM XML
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_firewall_xml(name, inside_conn, outside_conn)

        # Write XML to temp file
        xml_path = f'/tmp/{name}.xml'
        with open(xml_path, 'w') as f:
            f.write(xml_content)
        txn.add_resource('xml', xml_path)

        # Step 6: Define the VM
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

        # Step 7: Start the VM
        logger.info(f"Starting VM {name}")
        result = subprocess.run(
            ['virsh', 'start', name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start VM: {result.stderr}")

        # Step 8: Attach interfaces to target VMs
        # Use shared slot reuse logic to check for orphaned slots
        from slot_reuse import attach_interface_with_slot_reuse, apply_mutual_exclusivity

        targets_reused_slots = []
        targets_need_reboot = []

        # Attach inside interface
        if inside_conn:
            result = attach_interface_with_slot_reuse(
                target_device=inside_conn['target_device'],
                target_port=inside_conn['target_port'],
                bridge_name=inside_conn['bridge'],
                connection_dict=inside_conn
            )
            if result.reused_slot:
                targets_reused_slots.append(result.target_device)
            else:
                targets_need_reboot.append(result.target_device)

        # Attach outside interface
        if outside_conn:
            result = attach_interface_with_slot_reuse(
                target_device=outside_conn['target_device'],
                target_port=outside_conn['target_port'],
                bridge_name=outside_conn['bridge'],
                connection_dict=outside_conn
            )
            if result.reused_slot:
                targets_reused_slots.append(result.target_device)
            else:
                targets_need_reboot.append(result.target_device)

        # Apply mutual exclusivity: if a device needs reboot for ANY interface,
        # it should only appear in targets_need_reboot
        final_reused_slots, final_need_reboot = apply_mutual_exclusivity(
            targets_reused_slots, targets_need_reboot
        )

        # Clean up temp XML file
        if os.path.exists(xml_path):
            os.remove(xml_path)

        logger.info(f"Successfully created VyOS firewall: {name}")

        return {
            'status': 'created',
            'name': name,
            'mgmt_ip': mgmt_ip,
            'inside_interface': inside_conn,
            'outside_interface': outside_conn,
            'targets_reused_slots': final_reused_slots,
            'targets_need_reboot': final_need_reboot
        }


def delete_firewall(name: str) -> Dict:
    """
    Delete a VyOS firewall VM completely.

    Cleans up:
    - VM (destroy and undefine)
    - Disk image
    - Cloud-init ISO
    - Inside OVS bridge and target interface
    - Outside OVS bridge and target interface

    Uses shared ResourceManager methods to avoid code duplication
    with host deletion.

    Args:
        name: Name of the firewall to delete

    Returns:
        Dict with deletion status
    """
    from persistence import get_user_firewall
    from resource_manager import get_resource_manager

    logger.info(f"Deleting VyOS firewall: {name}")

    resource_mgr = get_resource_manager()
    devices_needing_reboot = []

    # Step 1: Get firewall info from persistence BEFORE deleting (need connection info)
    fw_entry = get_user_firewall(name, USER_FIREWALLS_PATH)
    inside_conn = None
    outside_conn = None

    if fw_entry:
        for fw_name, fw_info in fw_entry.items():
            inside_conn = fw_info.get('inside_interface', {})
            outside_conn = fw_info.get('outside_interface', {})
            logger.info(f"Found firewall connections: inside={inside_conn}, outside={outside_conn}")
            break

    # Step 2: Clean up inside connection
    inside_result = resource_mgr.cleanup_connection(inside_conn, 'inside')
    if inside_result['target_device']:
        devices_needing_reboot.append(inside_result['target_device'])

    # Step 3: Clean up outside connection
    outside_result = resource_mgr.cleanup_connection(outside_conn, 'outside')
    if outside_result['target_device'] and outside_result['target_device'] not in devices_needing_reboot:
        devices_needing_reboot.append(outside_result['target_device'])

    # Step 4: Delete VM and disk images
    vm_result = resource_mgr.delete_vm_with_cleanup(
        vm_name=name,
        disk_subdir='firewall',
        has_cidata=True
    )

    logger.info(f"Deleted VyOS firewall: {name}")

    return {
        'status': 'deleted',
        'name': name,
        'details': {
            'vm_destroyed': vm_result['vm_destroyed'],
            'vm_undefined': vm_result['vm_undefined'],
            'disk_deleted': vm_result['disk_deleted'],
            'cidata_deleted': vm_result.get('cidata_deleted', False),
            'inside_bridge_deleted': inside_result['bridge_deleted'],
            'outside_bridge_deleted': outside_result['bridge_deleted'],
            'inside_target_detached': inside_result['interface_detached'],
            'outside_target_detached': outside_result['interface_detached'],
            'devices_needing_reboot': devices_needing_reboot
        }
    }


def edit_firewall(
    name: str,
    inside_interface: Optional[Dict] = None,
    outside_interface: Optional[Dict] = None
) -> Dict:
    """
    Edit firewall configuration.

    Note: Interface IPs are not managed via the API - users configure
    them directly in VyOS. This endpoint is kept for API compatibility
    but currently returns a no-op response.

    Args:
        name: Name of the firewall
        inside_interface: Not used (IPs configured in VyOS)
        outside_interface: Not used (IPs configured in VyOS)

    Returns:
        Dict with status message
    """
    from persistence import get_user_firewall

    logger.info(f"Edit firewall called for: {name}")

    # Verify firewall exists
    firewall = get_user_firewall(name, USER_FIREWALLS_PATH)
    if not firewall:
        raise ValueError(f"Firewall '{name}' not found")

    # Interface IPs are configured directly in VyOS, not via cloud-init
    return {
        'status': 'no_changes',
        'name': name,
        'note': 'Interface IPs are configured directly in VyOS. SSH to the firewall to change IP addresses.'
    }
