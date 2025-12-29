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
    get_device_credentials
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

    Returns:
        Number of firewalls currently defined
    """
    from persistence import load_user_firewalls
    firewalls_data = load_user_firewalls(USER_FIREWALLS_PATH)
    return len(firewalls_data.get('firewalls', []))


def generate_vyos_cloud_init(
    hostname: str,
    mgmt_ip: str,
    inside_ip: str,
    outside_ip: str,
    gateway: str = '192.168.0.1',
    password: Optional[str] = None
) -> str:
    """
    Generate a cloud-init ISO for VyOS provisioning.

    VyOS uses vyos_config_commands in cloud-init for configuration.

    Args:
        hostname: VM hostname
        mgmt_ip: Management interface IP (without CIDR, /24 assumed)
        inside_ip: Inside interface IP with CIDR (e.g., 10.1.1.1/24)
        outside_ip: Outside interface IP with CIDR (e.g., 10.2.2.1/24)
        gateway: Default gateway
        password: User password (defaults to password from ACCESS_INFO.yaml)

    Returns:
        Path to the generated ISO file
    """
    # Get password from ACCESS_INFO.yaml if not provided
    if password is None:
        creds = get_device_credentials()
        password = creds.get('password', 'arista')

    # Create temp directory for cloud-init files
    temp_dir = tempfile.mkdtemp(prefix='cloudinit_vyos_')

    try:
        # Load and process user-data template
        template_path = os.path.join(CLOUD_INIT_TEMPLATES_PATH, 'vyos-firewall-template.yaml')

        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                user_data = f.read()
        else:
            # Fallback inline template
            user_data = """#cloud-config
vyos_config_commands:
  - set system host-name {hostname}
  - set system time-zone UTC
  - set system name-server 8.8.8.8
  - set system name-server 192.168.0.1
  - set interfaces ethernet eth0 address {mgmt_ip}/24
  - set interfaces ethernet eth0 description 'Management'
  - set interfaces ethernet eth1 address {inside_ip}
  - set interfaces ethernet eth1 description 'Inside'
  - set interfaces ethernet eth2 address {outside_ip}
  - set interfaces ethernet eth2 description 'Outside'
  - set service ssh port 22
  - set service ssh listen-address 0.0.0.0
  - set system login user arista authentication plaintext-password {password}
  - set system login user arista level admin
  - set protocols static route 0.0.0.0/0 next-hop {gateway}
"""

        # Replace placeholders
        user_data = user_data.format(
            hostname=hostname,
            mgmt_ip=mgmt_ip,
            inside_ip=inside_ip,
            outside_ip=outside_ip,
            gateway=gateway,
            password=password
        )

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
                    timeout=30
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

    Args:
        name: Hostname for the new firewall
        mgmt_ip: Management IP address (from available pool)
        inside_interface: Inside interface config:
            - ip: IP address with CIDR (e.g., "10.1.1.1/24")
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

    # Validate required fields
    inside_ip = inside_interface.get('ip')
    outside_ip = outside_interface.get('ip')

    if not inside_ip:
        raise ValueError("Inside interface IP is required (with CIDR notation)")
    if not outside_ip:
        raise ValueError("Outside interface IP is required (with CIDR notation)")

    created_resources = []

    try:
        # Step 1: Copy base image
        logger.info(f"Copying base VyOS image for {name}")
        image_path = copy_firewall_base_image(name)
        created_resources.append(('image', image_path))

        # Step 2: Generate cloud-init ISO
        logger.info(f"Generating VyOS cloud-init ISO for {name}")
        cidata_path = generate_vyos_cloud_init(
            hostname=name,
            mgmt_ip=mgmt_ip,
            inside_ip=inside_ip,
            outside_ip=outside_ip
        )
        created_resources.append(('cidata', cidata_path))

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
            created_resources.append(('bridge', bridge_name))

            inside_conn = {
                'target_device': target_device,
                'target_port': target_port,
                'local_port': 'eth1',
                'bridge': bridge_name,
                'ip': inside_ip
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
            created_resources.append(('bridge', bridge_name))

            outside_conn = {
                'target_device': target_device,
                'target_port': target_port,
                'local_port': 'eth2',
                'bridge': bridge_name,
                'ip': outside_ip
            }

        # Step 5: Generate VM XML
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_firewall_xml(name, inside_conn, outside_conn)

        # Write XML to temp file
        xml_path = f'/tmp/{name}.xml'
        with open(xml_path, 'w') as f:
            f.write(xml_content)
        created_resources.append(('xml', xml_path))

        # Step 6: Define the VM
        logger.info(f"Defining VM {name}")
        result = subprocess.run(
            ['virsh', 'define', xml_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to define VM: {result.stderr}")
        created_resources.append(('vm', name))

        # Step 7: Start the VM
        logger.info(f"Starting VM {name}")
        result = subprocess.run(
            ['virsh', 'start', name],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start VM: {result.stderr}")

        # Step 8: Attach interfaces to target VMs
        # Check for orphaned slots that can be reused (live updates, no reboot needed)
        targets_reused_slots = []
        targets_need_reboot = []

        # Helper to check and use orphaned slot or attach new interface
        def attach_with_orphan_check(conn, interface_name):
            """Check for orphaned slot and reuse, or attach new interface."""
            if not conn:
                return

            target_device = conn['target_device']
            target_port = conn['target_port']
            bridge_name = conn['bridge']

            # Check for orphaned slot to reuse
            orphaned_slot = None
            try:
                from config import ENABLE_SLOT_PRESERVATION
                from orphaned_interfaces import get_orphaned_slot_by_port, claim_orphaned_slot
                if ENABLE_SLOT_PRESERVATION:
                    port_num = extract_port_number(target_port)
                    orphaned_slot = get_orphaned_slot_by_port(target_device, port_num)
                    if orphaned_slot:
                        logger.debug(
                            f"Found orphaned slot for {target_device}:{target_port} "
                            f"(MAC {orphaned_slot.get('mac_address')})"
                        )
            except Exception as e:
                logger.warning(f"Error checking orphaned slots for {target_device}: {e}")
                orphaned_slot = None

            if orphaned_slot:
                # Reuse existing interface by updating the bridge connection
                logger.info(
                    f"Reusing orphaned slot on {target_device} for {interface_name} "
                    f"(bridge {bridge_name})"
                )
                try:
                    result = update_interface_bridge(
                        target_device,
                        orphaned_slot['mac_address'],
                        bridge_name
                    )
                    if result.get('status') == 'updated':
                        claim_orphaned_slot(target_device, orphaned_slot['mac_address'])
                        conn['reused_orphaned_slot'] = True
                        targets_reused_slots.append(target_device)
                        logger.info(
                            f"Successfully reused orphaned slot on {target_device} - "
                            f"no reboot needed"
                        )
                    else:
                        # Fallback to attach
                        logger.warning(
                            f"Failed to update bridge on {target_device}, falling back to attach"
                        )
                        attach_interface_to_vm(target_device, bridge_name)
                        conn['reused_orphaned_slot'] = False
                        targets_need_reboot.append(target_device)
                except Exception as e:
                    logger.warning(
                        f"Failed to reuse orphaned slot on {target_device}: {e}, "
                        f"falling back to attach"
                    )
                    attach_interface_to_vm(target_device, bridge_name)
                    conn['reused_orphaned_slot'] = False
                    targets_need_reboot.append(target_device)
            else:
                # No orphaned slot - attach new interface
                logger.info(f"Attaching new {interface_name} to {target_device}")
                attach_interface_to_vm(target_device, bridge_name)
                conn['reused_orphaned_slot'] = False
                targets_need_reboot.append(target_device)

        # Attach inside interface
        attach_with_orphan_check(inside_conn, 'inside interface')

        # Attach outside interface
        attach_with_orphan_check(outside_conn, 'outside interface')

        # Ensure mutual exclusivity: if a device needs reboot for ANY interface,
        # it should only appear in targets_need_reboot
        targets_reused_slots_set = set(targets_reused_slots)
        targets_need_reboot_set = set(targets_need_reboot)
        final_reused_slots = targets_reused_slots_set - targets_need_reboot_set

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
            'targets_reused_slots': list(final_reused_slots),
            'targets_need_reboot': list(targets_need_reboot_set)
        }

    except Exception as e:
        # Rollback on failure
        logger.error(f"Error creating firewall {name}: {e}")
        logger.info(f"Rolling back creation of {name}")

        for resource_type, resource_id in reversed(created_resources):
            try:
                if resource_type == 'vm':
                    subprocess.run(['virsh', 'destroy', resource_id],
                                   capture_output=True, timeout=30)
                    subprocess.run(['virsh', 'undefine', resource_id],
                                   capture_output=True, timeout=30)
                elif resource_type == 'image':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)
                elif resource_type == 'cidata':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)
                elif resource_type == 'bridge':
                    delete_ovs_bridge(resource_id)
                elif resource_type == 'xml':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)
            except Exception as cleanup_error:
                logger.warning(f"Rollback failed for {resource_type}:{resource_id}: {cleanup_error}")

        raise


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
    Edit firewall interface IPs.

    Note: This requires VM restart to apply cloud-init changes.
    Connection changes (target device/port) are not supported via edit.

    Args:
        name: Name of the firewall
        inside_interface: New inside interface config (ip only)
        outside_interface: New outside interface config (ip only)

    Returns:
        Dict with edit status
    """
    # For now, editing firewall IPs requires recreating the cloud-init ISO
    # and rebooting the VM. This is a simplified implementation.
    logger.info(f"Editing VyOS firewall: {name}")

    # Get current config from persistence
    from persistence import get_user_firewall, load_user_firewalls, save_user_firewalls

    firewall = get_user_firewall(name, USER_FIREWALLS_PATH)
    if not firewall:
        raise ValueError(f"Firewall '{name}' not found")

    fw_info = firewall.get(name, {})

    # Update IPs if provided
    current_inside_ip = fw_info.get('inside_interface', {}).get('ip', '')
    current_outside_ip = fw_info.get('outside_interface', {}).get('ip', '')

    new_inside_ip = inside_interface.get('ip') if inside_interface else current_inside_ip
    new_outside_ip = outside_interface.get('ip') if outside_interface else current_outside_ip

    if new_inside_ip == current_inside_ip and new_outside_ip == current_outside_ip:
        return {
            'status': 'no_changes',
            'name': name
        }

    # Regenerate cloud-init ISO with new IPs
    mgmt_ip = fw_info.get('mgmt_ip', '')
    generate_vyos_cloud_init(
        hostname=name,
        mgmt_ip=mgmt_ip,
        inside_ip=new_inside_ip,
        outside_ip=new_outside_ip
    )

    # Update persistence
    all_firewalls = load_user_firewalls(USER_FIREWALLS_PATH)
    for fw_entry in all_firewalls.get('firewalls', []):
        for fw_name, info in fw_entry.items():
            if fw_name.lower() == name.lower():
                if 'inside_interface' in info:
                    info['inside_interface']['ip'] = new_inside_ip
                if 'outside_interface' in info:
                    info['outside_interface']['ip'] = new_outside_ip
                break

    save_user_firewalls(all_firewalls, USER_FIREWALLS_PATH)

    # Reboot VM to apply changes
    try:
        subprocess.run(['virsh', 'reboot', name],
                       capture_output=True, timeout=30)
    except Exception as e:
        logger.warning(f"Failed to reboot firewall: {e}")

    logger.info(f"Updated VyOS firewall: {name}")

    return {
        'status': 'updated',
        'name': name,
        'inside_ip': new_inside_ip,
        'outside_ip': new_outside_ip,
        'note': 'Firewall is rebooting to apply changes'
    }
