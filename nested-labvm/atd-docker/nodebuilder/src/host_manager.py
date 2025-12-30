"""
Host Manager for Nodebuilder Service

Handles Linux desktop host VM lifecycle:
- Creating Debian LXDE virtual machines
- Cloud-init ISO generation for provisioning
- VNC port management for noVNC access
- VM lifecycle (define, start, stop, delete)
"""

import logging
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from config import (
    LIBVIRT_IMAGES_PATH,
    get_host_base_image_path,
    HOST_CPU,
    HOST_RAM_MB,
    HOST_VNC_BASE_PORT,
    MAX_HOSTS_PER_TOPOLOGY,
    MGMT_BRIDGE,
    CLOUD_INIT_TEMPLATES_PATH,
    USER_HOSTS_PATH,
    SUBPROCESS_TIMEOUT_SHORT,
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

logger = logging.getLogger('nodebuilder')


def yaml_safe_string(value: str) -> str:
    """
    Escape a string for safe use in YAML.

    Wraps the value in single quotes and escapes any embedded single quotes
    by doubling them. This ensures passwords with special characters work.

    Args:
        value: The string to escape

    Returns:
        YAML-safe quoted string
    """
    # Escape single quotes by doubling them, then wrap in single quotes
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


# Base XML template for Linux Host VM
HOST_BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <memballoon model='virtio'/>
  </devices>
  <seclabel type='none'/>
</domain>
"""


def get_host_count() -> int:
    """
    Get the current count of user-added Linux hosts.

    Returns:
        Number of hosts currently defined
    """
    from persistence import load_user_hosts
    hosts_data = load_user_hosts(USER_HOSTS_PATH)
    return len(hosts_data.get('hosts', []))


def generate_cloud_init_iso(
    hostname: str,
    mgmt_ip: str,
    data_ip: Optional[str] = None,
    gateway: str = '192.168.0.1',
    password: Optional[str] = None
) -> str:
    """
    Generate a cloud-init ISO for host provisioning.

    Creates a NoCloud datasource ISO with user-data and meta-data.
    Uses ubuntu-desktop-template.yaml which installs LXDE desktop,
    x11vnc for VNC access, and configures autologin.

    The host has two interfaces:
    - eth0: Management interface (vmgmt bridge, gets mgmt_ip)
    - eth1: Data interface (OVS bridge to switch, user configures or gets data_ip)

    Args:
        hostname: VM hostname
        mgmt_ip: Management IP address (without CIDR) for eth0
        data_ip: Optional data interface IP with CIDR for eth1 (e.g., "10.1.1.100/24")
        gateway: Default gateway (on management network)
        password: Password for arista user (if None, read from ACCESS_INFO.yaml)

    Returns:
        Path to the generated ISO file
    """
    # Get credentials from ACCESS_INFO.yaml if not provided
    from config import get_device_credentials
    creds = get_device_credentials()
    username = creds.get('username', 'arista')
    if password is None:
        password = creds.get('password', 'arista')

    # Create temp directory for cloud-init files
    temp_dir = tempfile.mkdtemp(prefix='cloudinit_')

    try:
        # Try to load the Ubuntu desktop template
        template_path = os.path.join(CLOUD_INIT_TEMPLATES_PATH, 'ubuntu-desktop-template.yaml')

        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                user_data = f.read()

            # Replace placeholders in template
            user_data = user_data.replace('{hostname}', hostname)
            user_data = user_data.replace('{username}', username)
            user_data = user_data.replace('{password}', yaml_safe_string(password))
            user_data = user_data.replace('{mgmt_ip}', mgmt_ip)
            user_data = user_data.replace('{gateway}', gateway)

            # Handle optional data_ip - the template doesn't use it,
            # but we could extend netplan config if needed
            if data_ip:
                user_data = user_data.replace('{data_ip}', data_ip)

            logger.info(f"Using Ubuntu desktop template from {template_path}")
        else:
            # Fallback inline template if template file not found
            logger.warning(f"Template not found at {template_path}, using inline fallback")

            user_data = f"""#cloud-config
# Fallback template - assumes base image has packages pre-installed
# Network configuration is in separate network-config file
hostname: {hostname}
fqdn: {hostname}.atl.local
manage_etc_hosts: true

users:
  - name: {username}
    groups: [sudo, adm, audio, video, plugdev, netdev]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false

chpasswd:
  expire: false
  users:
    - name: {username}
      password: {yaml_safe_string(password)}
      type: text

write_files:
  - path: /etc/systemd/system/x11vnc.service
    content: |
      [Unit]
      Description=x11vnc VNC Server for noVNC
      After=lightdm.service
      Requires=lightdm.service

      [Service]
      Type=simple
      ExecStart=/usr/bin/x11vnc -display :0 -auth guess -forever -loop -noxdamage -repeat -rfbport 5900 -shared -nopw
      Restart=on-failure
      RestartSec=3

      [Install]
      WantedBy=multi-user.target
    permissions: '0644'

  - path: /etc/lightdm/lightdm.conf.d/50-autologin.conf
    content: |
      [Seat:*]
      autologin-user={username}
      autologin-user-timeout=0
    permissions: '0644'

runcmd:
  - systemctl daemon-reload
  - systemctl enable x11vnc
  - systemctl enable lightdm
  - systemctl set-default graphical.target
  - mkdir -p /home/{username}/.config/lxsession/LXDE
  - |
    cat > /home/{username}/.config/lxsession/LXDE/autostart << 'AUTOSTART'
    @lxpanel --profile LXDE
    @pcmanfm --desktop --profile LXDE
    @xset s off
    @xset -dpms
    @xset s noblank
    AUTOSTART
  - chown -R {username}:{username} /home/{username}/.config
  - touch /var/lib/cloud/instance/desktop-setup-complete
  - systemctl start lightdm
"""

        # Write user-data
        with open(os.path.join(temp_dir, 'user-data'), 'w') as f:
            f.write(user_data)

        # Write meta-data
        meta_data = f"""instance-id: {hostname}
local-hostname: {hostname}
"""
        with open(os.path.join(temp_dir, 'meta-data'), 'w') as f:
            f.write(meta_data)

        # Write network-config (version 2 netplan format)
        # This is processed by cloud-init early, before package installation
        # Ubuntu uses predictable interface names: ens3, ens4 for virtio NICs
        network_config = f"""version: 2
ethernets:
  ens3:
    addresses:
      - {mgmt_ip}/24
    routes:
      - to: default
        via: {gateway}
    nameservers:
      addresses:
        - 8.8.8.8
        - {gateway}
  ens4:
    dhcp4: false
    optional: true
    link-local: []
"""
        with open(os.path.join(temp_dir, 'network-config'), 'w') as f:
            f.write(network_config)
        logger.info(f"Generated network-config for {hostname} with IP {mgmt_ip}")

        # Generate ISO
        iso_path = f'{LIBVIRT_IMAGES_PATH}/hosts/{hostname}-cidata.iso'

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
                    logger.info(f"Created cloud-init ISO: {iso_path}")
                    return iso_path
            except FileNotFoundError:
                continue

        raise RuntimeError("Neither genisoimage nor mkisofs available")

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def generate_host_xml(
    name: str,
    connection: Optional[Dict] = None
) -> str:
    """
    Generate libvirt XML for a Linux Host VM.

    Args:
        name: VM name
        connection: Optional connection dict with 'bridge' and 'local_port'

    Returns:
        XML string
    """
    root = ET.fromstring(HOST_BASE_XML.format(ram=HOST_RAM_MB, cpu=HOST_CPU))

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
        'file': f'{LIBVIRT_IMAGES_PATH}/hosts/{name}.qcow2'
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
        'file': f'{LIBVIRT_IMAGES_PATH}/hosts/{name}-cidata.iso'
    })
    ET.SubElement(cdrom, 'target', attrib={
        'dev': 'hdc',
        'bus': 'ide'
    })
    ET.SubElement(cdrom, 'readonly')

    # Add management interface (eth0 -> vmgmt)
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

    # Add data interface if connection specified (eth1 -> OVS bridge)
    if connection:
        bridge = connection.get('bridge', '')
        if bridge:
            data_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
            ET.SubElement(data_int, 'source', attrib={'bridge': bridge})
            ET.SubElement(data_int, 'target', attrib={'dev': f'{name}_data'})
            ET.SubElement(data_int, 'model', attrib={'type': 'virtio'})
            ET.SubElement(data_int, 'virtualport', attrib={'type': 'openvswitch'})
            ET.SubElement(data_int, 'address', attrib={
                'type': 'pci',
                'domain': '0x0000',
                'bus': '0x00',
                'slot': '0x04',
                'function': '0x0'
            })

    # Add VNC graphics for console debugging (libvirt QEMU console)
    # Note: noVNC desktop access uses x11vnc inside VM via management IP
    # autoport=yes lets libvirt pick an available port to avoid conflicts
    graphics = ET.SubElement(devices, 'graphics', attrib={
        'type': 'vnc',
        'autoport': 'yes',
        'listen': '127.0.0.1'  # Only localhost for security (console debugging only)
    })
    ET.SubElement(graphics, 'listen', attrib={
        'type': 'address',
        'address': '127.0.0.1'
    })

    # Add video device for VNC
    video = ET.SubElement(devices, 'video')
    ET.SubElement(video, 'model', attrib={
        'type': 'cirrus',
        'vram': '16384',
        'heads': '1'
    })

    return ET.tostring(root, encoding='unicode')


def copy_host_base_image(vm_name: str) -> str:
    """
    Copy the base Linux host image for a new VM.

    Supports both Ubuntu and Debian base images - uses whichever is available.
    Ubuntu is preferred as it's easier to set up from cloud images.

    Args:
        vm_name: Name of the new VM

    Returns:
        Path to the new disk image
    """
    dest_path = f'{LIBVIRT_IMAGES_PATH}/hosts/{vm_name}.qcow2'

    # Ensure destination directory exists
    dest_dir = os.path.dirname(dest_path)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Get base image path (checks for Ubuntu first, then Debian)
    base_image_path = get_host_base_image_path()

    # Check if base image exists
    if not os.path.exists(base_image_path):
        raise RuntimeError(
            f"Base host image not found. Run one of:\n"
            "  - build-ubuntu-desktop.sh (recommended - uses cloud images)\n"
            "  - build-debian-lxde.sh (requires manual install)"
        )

    # Copy the image
    shutil.copy2(base_image_path, dest_path)
    logger.info(f"Copied base image from {base_image_path} to {dest_path}")

    return dest_path


def create_host(
    name: str,
    mgmt_ip: str,
    connection: Optional[Dict] = None,
    data_ip: Optional[str] = None
) -> Dict:
    """
    Create a complete Linux host VM with two interfaces.

    Interfaces:
    - eth0: Management (vmgmt bridge, gets mgmt_ip from pool)
    - eth1: Data (OVS bridge to switch, optionally configured with data_ip)

    Args:
        name: Hostname for the new host
        mgmt_ip: Management IP address (from available pool, for SSH/VNC access)
        connection: Optional connection config with 'target_device' for eth1
        data_ip: Optional IP for data interface with CIDR (e.g., "10.1.1.100/24")
                 If not provided, user configures IP on the host manually

    Returns:
        Dict with creation status and details
    """
    logger.info(f"Creating Linux host: {name} (Mgmt IP: {mgmt_ip})")

    # Check host limit
    current_count = get_host_count()
    if current_count >= MAX_HOSTS_PER_TOPOLOGY:
        raise RuntimeError(
            f"Maximum of {MAX_HOSTS_PER_TOPOLOGY} hosts per topology reached"
        )

    # x11vnc inside VM always uses port 5900
    # noVNC connects to mgmt_ip:5900 for desktop access
    x11vnc_port = 5900

    created_resources = []

    try:
        # Step 1: Copy base image
        logger.info(f"Copying base image for {name}")
        image_path = copy_host_base_image(name)
        created_resources.append(('image', image_path))

        # Step 2: Generate cloud-init ISO
        logger.info(f"Generating cloud-init ISO for {name}")
        cidata_path = generate_cloud_init_iso(name, mgmt_ip, data_ip=data_ip)
        created_resources.append(('cidata', cidata_path))

        # Step 3: Process connection if specified
        processed_connection = None
        if connection and connection.get('target_device'):
            target_device = connection['target_device']
            target_port = connection.get('target_port') or find_next_available_port(target_device)
            local_port = 'eth1'  # Hosts have single data interface

            # Generate bridge name
            bridge_name = generate_bridge_name(
                name, local_port,
                target_device, target_port
            )

            # Create OVS bridge
            logger.info(f"Creating OVS bridge: {bridge_name}")
            create_ovs_bridge(bridge_name)
            created_resources.append(('bridge', bridge_name))

            processed_connection = {
                'target_device': target_device,
                'target_port': target_port,
                'local_port': local_port,
                'bridge': bridge_name
            }

        # Step 4: Generate VM XML
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_host_xml(name, processed_connection)

        # Write XML to temp file
        xml_path = f'/tmp/{name}.xml'
        with open(xml_path, 'w') as f:
            f.write(xml_content)
        created_resources.append(('xml', xml_path))

        # Step 5: Define the VM
        logger.info(f"Defining VM {name}")
        result = subprocess.run(
            ['virsh', 'define', xml_path],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to define VM: {result.stderr}")
        created_resources.append(('vm', name))

        # Step 6: Start the VM
        logger.info(f"Starting VM {name}")
        result = subprocess.run(
            ['virsh', 'start', name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_LONG
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start VM: {result.stderr}")

        # Step 7: Attach interface to target VM if connected
        # Use shared slot reuse logic to check for orphaned slots
        from slot_reuse import attach_interface_with_slot_reuse

        targets_reused_slots = []
        targets_need_reboot = []

        if processed_connection:
            result = attach_interface_with_slot_reuse(
                target_device=processed_connection['target_device'],
                target_port=processed_connection['target_port'],
                bridge_name=processed_connection['bridge'],
                connection_dict=processed_connection
            )

            if result.reused_slot:
                targets_reused_slots.append(result.target_device)
            else:
                targets_need_reboot.append(result.target_device)

        # Clean up temp XML file
        if os.path.exists(xml_path):
            os.remove(xml_path)

        logger.info(f"Successfully created Linux host: {name}")

        return {
            'status': 'created',
            'name': name,
            'mgmt_ip': mgmt_ip,
            'data_ip': data_ip,
            'vnc_port': x11vnc_port,  # x11vnc inside VM on port 5900
            'connection': processed_connection,
            'targets_reused_slots': targets_reused_slots,
            'targets_need_reboot': targets_need_reboot
        }

    except Exception as e:
        # Rollback on failure
        logger.error(f"Error creating host {name}: {e}")
        logger.info(f"Rolling back creation of {name}")

        for resource_type, resource_id in reversed(created_resources):
            try:
                if resource_type == 'vm':
                    subprocess.run(['virsh', 'destroy', resource_id],
                                   capture_output=True, timeout=SUBPROCESS_TIMEOUT_DEFAULT)
                    subprocess.run(['virsh', 'undefine', resource_id],
                                   capture_output=True, timeout=SUBPROCESS_TIMEOUT_DEFAULT)
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


def delete_host(name: str) -> Dict:
    """
    Delete a Linux host VM completely.

    Cleans up:
    - VM (destroy and undefine)
    - Disk image
    - Cloud-init ISO
    - OVS bridge to target device
    - Interface on target device

    Uses shared ResourceManager methods to avoid code duplication
    with firewall deletion.

    Args:
        name: Name of the host to delete

    Returns:
        Dict with deletion status
    """
    from persistence import get_user_host
    from resource_manager import get_resource_manager

    logger.info(f"Deleting Linux host: {name}")

    resource_mgr = get_resource_manager()
    devices_needing_reboot = []

    # Step 1: Get host info from persistence BEFORE deleting (need connection info)
    host_entry = get_user_host(name, USER_HOSTS_PATH)
    connection = None

    if host_entry:
        for host_name, host_info in host_entry.items():
            connection = host_info.get('connection', {})
            if connection:
                logger.info(
                    f"Found connection: bridge={connection.get('bridge')}, "
                    f"target={connection.get('target_device')}"
                )
            break

    # Step 2: Clean up connection (detach interface + delete bridge)
    conn_result = resource_mgr.cleanup_connection(connection)
    if conn_result['target_device']:
        devices_needing_reboot.append(conn_result['target_device'])

    # Step 3: Delete VM and disk images
    vm_result = resource_mgr.delete_vm_with_cleanup(
        vm_name=name,
        disk_subdir='hosts',
        has_cidata=True
    )

    # Step 4: Revoke any noVNC tokens for this host
    tokens_revoked = 0
    try:
        from novnc_manager import revoke_tokens_for_host
        tokens_revoked = revoke_tokens_for_host(name)
        if tokens_revoked > 0:
            logger.info(f"Revoked {tokens_revoked} noVNC tokens for {name}")
    except Exception as e:
        logger.warning(f"Failed to revoke noVNC tokens for {name}: {e}")

    logger.info(f"Deleted Linux host: {name}")

    return {
        'status': 'deleted',
        'name': name,
        'details': {
            'vm_destroyed': vm_result['vm_destroyed'],
            'vm_undefined': vm_result['vm_undefined'],
            'disk_deleted': vm_result['disk_deleted'],
            'cidata_deleted': vm_result.get('cidata_deleted', False),
            'bridge_deleted': conn_result['bridge_deleted'],
            'target_interface_detached': conn_result['interface_detached'],
            'tokens_revoked': tokens_revoked,
            'devices_needing_reboot': devices_needing_reboot
        }
    }


def get_host_vnc_info(name: str) -> Optional[Dict]:
    """
    Get VNC connection info for a host.

    Args:
        name: Name of the host

    Returns:
        Dict with VNC port and connection info, or None if not found
    """
    try:
        # Get VNC port from virsh
        result = subprocess.run(
            ['virsh', 'vncdisplay', name],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SHORT
        )

        if result.returncode != 0:
            return None

        # Parse display number (e.g., ":0" -> port 5900)
        display = result.stdout.strip()
        if display.startswith(':'):
            display_num = int(display[1:])
            vnc_port = 5900 + display_num
        else:
            vnc_port = HOST_VNC_BASE_PORT

        return {
            'name': name,
            'vnc_port': vnc_port,
            'vnc_display': display
        }

    except Exception as e:
        logger.error(f"Error getting VNC info for {name}: {e}")
        return None
