"""
VeloCloud Manager for Nodebuilder Service

Handles VeloCloud SD-WAN device lifecycle:
- Edge: Software appliance with WAN/LAN interfaces
- Gateway: Data plane hub for Edge traffic
- Orchestrator (VCO): Management and control plane

All devices are configured for standalone training mode
(no external VCO activation required).
"""

import logging
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from config import (
    LIBVIRT_IMAGES_PATH,
    get_velo_base_image_path,
    get_velo_orchestrator_disk_paths,
    is_velo_enabled,
    get_velo_config,
    VELO_EDGE_CPU,
    VELO_EDGE_RAM_MB,
    VELO_GATEWAY_CPU,
    VELO_GATEWAY_RAM_MB,
    VELO_ORCHESTRATOR_CPU,
    VELO_ORCHESTRATOR_RAM_MB,
    MAX_VELO_EDGE_PER_TOPOLOGY,
    MAX_VELO_GATEWAY_PER_TOPOLOGY,
    MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY,
    VELO_ORCHESTRATOR_DISKS,
    MGMT_BRIDGE,
    CLOUD_INIT_TEMPLATES_PATH,
    USER_VELO_PATH,
    get_device_credentials,
    SUBPROCESS_TIMEOUT_DEFAULT,
    SUBPROCESS_TIMEOUT_LONG
)
from interface_manager import (
    create_ovs_bridge,
    delete_ovs_bridge,
    generate_bridge_name,
    find_next_available_port
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
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


# Device type configurations
VELO_DEVICE_CONFIGS = {
    'edge': {
        'cpu': VELO_EDGE_CPU,
        'ram': VELO_EDGE_RAM_MB,
        'max_per_topology': MAX_VELO_EDGE_PER_TOPOLOGY,
        'interfaces': ['wan1', 'wan2', 'wan3', 'lan'],
        'template': 'velocloud-edge-template.yaml'
    },
    'gateway': {
        'cpu': VELO_GATEWAY_CPU,
        'ram': VELO_GATEWAY_RAM_MB,
        'max_per_topology': MAX_VELO_GATEWAY_PER_TOPOLOGY,
        'interfaces': ['transport1', 'transport2'],
        'template': 'velocloud-gateway-template.yaml'
    },
    'orchestrator': {
        'cpu': VELO_ORCHESTRATOR_CPU,
        'ram': VELO_ORCHESTRATOR_RAM_MB,
        'max_per_topology': MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY,
        'interfaces': ['data'],
        'template': 'velocloud-orchestrator-template.yaml'
    }
}


# Base XML template for VeloCloud VMs
VELO_BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
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


def get_velo_device_count(device_type: str = None) -> int:
    """
    Get the current count of user-added VeloCloud devices.

    Args:
        device_type: Optional device type to filter (edge, gateway, orchestrator)

    Returns:
        Number of VeloCloud devices currently defined
    """
    from persistence import get_velo_device_count as persistence_count
    from persistence import get_velo_device_count_by_type

    if device_type:
        return get_velo_device_count_by_type(device_type.lower(), USER_VELO_PATH)
    return persistence_count(USER_VELO_PATH)


def generate_velo_cloud_init(
    device_type: str,
    hostname: str,
    mgmt_ip: str,
    gateway: str = '192.168.0.1',
    password: Optional[str] = None,
    interface_ips: Optional[Dict[str, str]] = None,
    gateway_config: Optional[Dict[str, str]] = None,
    edge_config: Optional[Dict[str, str]] = None,
    orchestrator_config: Optional[Dict[str, str]] = None
) -> str:
    """
    Generate a cloud-init ISO for VeloCloud device provisioning.

    Args:
        device_type: Type of device (edge, gateway, orchestrator)
        hostname: VM hostname
        mgmt_ip: Management interface IP (without CIDR, /24 assumed)
        gateway: Default gateway
        password: User password (defaults to ACCESS_INFO.yaml)
        interface_ips: Optional dict of interface IPs with CIDR
                       e.g., {'wan1': '10.1.1.1/24', 'lan': '10.2.2.1/24'}
        gateway_config: Optional dict for Gateway-specific config:
                        - vco: VeloCloud Orchestrator address
                        - activation_code: Gateway activation key
                        - eth0_ip: Public interface IP with CIDR
                        - eth0_gateway: Default gateway for eth0
                        - eth1_ip: Handoff interface IP with CIDR
                        - eth1_gateway: Gateway for eth1
        edge_config: Optional dict for Edge-specific config:
                     - vco: VeloCloud Orchestrator address
                     - activation_code: Edge activation key
                     - interfaces: dict of GE1-GE8 config (ip, netmask, gateway, type)
        orchestrator_config: Optional dict for Orchestrator-specific config:
                             - eth0_ip: Management interface IP with CIDR
                             - eth0_netmask: Management interface netmask
                             - eth0_gateway: Default gateway
                             - eth1_ip: Data interface IP (optional)
                             - eth1_netmask: Data interface netmask (optional)

    Returns:
        Path to the generated ISO file
    """
    # Get password from ACCESS_INFO.yaml if not provided
    if password is None:
        creds = get_device_credentials()
        password = creds.get('password', 'arista')

    device_type_lower = device_type.lower()
    config = VELO_DEVICE_CONFIGS.get(device_type_lower)
    if not config:
        raise ValueError(f"Invalid device type: {device_type}")

    # Create temp directory for cloud-init files
    temp_dir = tempfile.mkdtemp(prefix=f'cloudinit_velo_{device_type_lower}_')

    try:
        # Load template
        template_path = os.path.join(CLOUD_INIT_TEMPLATES_PATH, config['template'])

        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                user_data = f.read()
        else:
            # Fallback inline template
            user_data = _get_fallback_template(device_type_lower)

        # Replace basic placeholders
        user_data = user_data.replace('{hostname}', hostname)
        user_data = user_data.replace('{mgmt_ip}', mgmt_ip)
        user_data = user_data.replace('{gateway}', gateway)
        user_data = user_data.replace('{password}', yaml_safe_string(password))

        # Replace interface IP placeholders
        if interface_ips:
            for iface, ip in interface_ips.items():
                placeholder = '{' + f'{iface}_ip' + '}'
                user_data = user_data.replace(placeholder, ip)

        # Handle Gateway-specific configuration
        if device_type_lower == 'gateway' and gateway_config:
            vco = gateway_config.get('vco', 'orchestrator.velocloud.net')
            activation_code = gateway_config.get('activation_code', 'XXXX-XXXX-XXXX-XXXX')
            user_data = user_data.replace('{vco}', vco)
            user_data = user_data.replace('{activation_code}', activation_code)

            # Generate network-config for Gateway (Netplan v2 format)
            eth0_ip = gateway_config.get('eth0_ip', f'{mgmt_ip}/24')
            eth0_gateway = gateway_config.get('eth0_gateway', gateway)
            eth1_ip = gateway_config.get('eth1_ip', '')
            eth1_gateway = gateway_config.get('eth1_gateway', '')

            network_config = _generate_gateway_network_config(
                eth0_ip, eth0_gateway, eth1_ip, eth1_gateway
            )
            with open(os.path.join(temp_dir, 'network-config'), 'w') as f:
                f.write(network_config)
        elif device_type_lower == 'gateway':
            # Provide defaults for Gateway if no config specified
            user_data = user_data.replace('{vco}', 'orchestrator.velocloud.net')
            user_data = user_data.replace('{activation_code}', 'XXXX-XXXX-XXXX-XXXX')

        # Handle Edge-specific configuration
        if device_type_lower == 'edge' and edge_config:
            vco = edge_config.get('vco', 'orchestrator.velocloud.net')
            activation_code = edge_config.get('activation_code', 'XXXX-XXXX-XXXX-XXXX')
            user_data = user_data.replace('{vco}', vco)
            user_data = user_data.replace('{activation_code}', activation_code)
        elif device_type_lower == 'edge':
            # Provide defaults for Edge if no config specified
            user_data = user_data.replace('{vco}', 'orchestrator.velocloud.net')
            user_data = user_data.replace('{activation_code}', 'XXXX-XXXX-XXXX-XXXX')

        # Write user-data
        with open(os.path.join(temp_dir, 'user-data'), 'w') as f:
            f.write(user_data)

        # Write meta-data (Edge and Orchestrator use network-interfaces section)
        meta_data = f"""instance-id: {hostname}
local-hostname: {hostname}
"""
        # Add Edge network-interfaces section if edge_config provided
        if device_type_lower == 'edge' and edge_config:
            network_interfaces = edge_config.get('interfaces', {})
            if network_interfaces:
                meta_data += _generate_edge_network_interfaces(network_interfaces)

        # Add Orchestrator network-interfaces section
        if device_type_lower == 'orchestrator':
            orch_config = orchestrator_config or {}
            meta_data += _generate_orchestrator_network_interfaces(
                mgmt_ip, gateway, orch_config
            )

        with open(os.path.join(temp_dir, 'meta-data'), 'w') as f:
            f.write(meta_data)

        # Generate ISO
        iso_path = f'{LIBVIRT_IMAGES_PATH}/velo/{hostname}-cidata.iso'

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
                    logger.info(f"Created VeloCloud cloud-init ISO: {iso_path}")
                    return iso_path
            except FileNotFoundError:
                continue

        raise RuntimeError("Neither genisoimage nor mkisofs available")

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def _generate_gateway_network_config(
    eth0_ip: str,
    eth0_gateway: str,
    eth1_ip: str = '',
    eth1_gateway: str = ''
) -> str:
    """
    Generate Netplan v2 network-config for VeloCloud Gateway.

    eth0: Public/internet-facing interface (primary, metric 1)
    eth1: Handoff interface to PE router (secondary, metric 13)
    """
    # Start with eth0 (required)
    config = f"""version: 2
ethernets:
  eth0:
    addresses:
      - {eth0_ip}
    gateway4: {eth0_gateway}
    nameservers:
      addresses:
        - 8.8.8.8
        - 8.8.4.4
      search: []
    routes:
      - to: 0.0.0.0/0
        via: {eth0_gateway}
        metric: 1"""

    # Add eth1 if configured (handoff interface)
    if eth1_ip:
        config += f"""
  eth1:
    addresses:
      - {eth1_ip}"""
        if eth1_gateway:
            config += f"""
    routes:
      - to: 0.0.0.0/0
        via: {eth1_gateway}
        metric: 13"""

    return config


def _generate_edge_network_interfaces(interfaces: Dict[str, Dict]) -> str:
    """
    Generate network-interfaces section for VeloCloud Edge meta-data.

    VeloCloud Edge uses a different format than standard cloud-init:
    - Interface names are GE1-GE8 (not eth0-eth7)
    - Network config goes in meta-data, not network-config file
    - Supports static and DHCP types

    Args:
        interfaces: Dict of interface configs, e.g.:
                   {'GE3': {'type': 'static', 'ip': '10.1.1.1', 'netmask': '255.255.255.0', 'gateway': '10.1.1.254'}}

    Returns:
        YAML string for network-interfaces section
    """
    if not interfaces:
        return ""

    lines = ["network-interfaces:"]

    for iface_name, config in interfaces.items():
        # Normalize interface name (ensure uppercase GE format)
        iface_upper = iface_name.upper()
        if not iface_upper.startswith('GE'):
            continue

        iface_type = config.get('type', 'dhcp')

        if iface_type == 'static':
            ip = config.get('ip', '')
            netmask = config.get('netmask', '255.255.255.0')
            gw = config.get('gateway', '')

            if ip:
                lines.append(f"  {iface_upper}:")
                lines.append(f"    type: static")
                lines.append(f"    ipaddr: {ip}")
                lines.append(f"    netmask: {netmask}")
                if gw:
                    lines.append(f"    gateway: {gw}")
        else:
            # DHCP is default for VeloCloud Edge
            lines.append(f"  {iface_upper}:")
            lines.append(f"    type: dhcp")

    if len(lines) == 1:
        return ""  # No interfaces configured

    return "\n".join(lines) + "\n"


def _generate_orchestrator_network_interfaces(
    mgmt_ip: str,
    gateway: str,
    orch_config: Dict[str, str]
) -> str:
    """
    Generate network-interfaces section for VeloCloud Orchestrator meta-data.

    VeloCloud Orchestrator uses the same meta-data format as VCE Edge:
    - eth0: Management interface (required)
    - eth1: Data interface (optional - for Edge/Gateway connectivity)

    Args:
        mgmt_ip: Management IP address (without CIDR)
        gateway: Default gateway
        orch_config: Dict with optional overrides:
                    - eth0_ip: Override management IP
                    - eth0_netmask: Management netmask (default 255.255.255.0)
                    - eth0_gateway: Override gateway
                    - eth1_ip: Data interface IP (optional)
                    - eth1_netmask: Data interface netmask

    Returns:
        YAML string for network-interfaces section
    """
    lines = ["network-interfaces: |"]

    # eth0 - Management interface (always configured)
    eth0_ip = orch_config.get('eth0_ip', mgmt_ip)
    eth0_netmask = orch_config.get('eth0_netmask', '255.255.255.0')
    eth0_gateway = orch_config.get('eth0_gateway', gateway)

    lines.append("  auto eth0")
    lines.append("  iface eth0 inet static")
    lines.append(f"    address {eth0_ip}")
    lines.append(f"    netmask {eth0_netmask}")
    lines.append(f"    gateway {eth0_gateway}")

    # eth1 - Data interface (optional)
    eth1_ip = orch_config.get('eth1_ip', '')
    if eth1_ip:
        eth1_netmask = orch_config.get('eth1_netmask', '255.255.255.0')
        lines.append("  auto eth1")
        lines.append("  iface eth1 inet static")
        lines.append(f"    address {eth1_ip}")
        lines.append(f"    netmask {eth1_netmask}")

    return "\n".join(lines) + "\n"


def _get_fallback_template(device_type: str) -> str:
    """Get fallback cloud-init template for a device type."""
    return f"""#cloud-config
# Fallback template for VeloCloud {device_type.capitalize()}
hostname: {{hostname}}
manage_etc_hosts: true

users:
  - name: arista
    groups: [sudo, wheel]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false

chpasswd:
  expire: false
  users:
    - name: arista
      password: {{password}}
      type: text
    - name: root
      password: {{password}}
      type: text

runcmd:
  - ip link set eth0 up
  - touch /var/lib/cloud/instance/velocloud-{device_type}-init-complete
"""


def generate_velo_xml(
    name: str,
    device_type: str,
    connections: Optional[List[Dict]] = None,
    disk_paths: Optional[List[Dict]] = None
) -> str:
    """
    Generate libvirt XML for a VeloCloud VM.

    Args:
        name: VM name
        device_type: Type of device (edge, gateway, orchestrator)
        connections: Optional list of connection dicts with 'bridge' and 'local_port'
        disk_paths: Optional list of disk info dicts with 'path' and 'target'
                    For orchestrator, this should include all 4 disks

    Returns:
        XML string
    """
    config = VELO_DEVICE_CONFIGS.get(device_type.lower())
    if not config:
        raise ValueError(f"Invalid device type: {device_type}")

    root = ET.fromstring(VELO_BASE_XML.format(
        ram=config['ram'],
        cpu=config['cpu']
    ))

    # Add name element
    name_elem = ET.SubElement(root, 'name')
    name_elem.text = name

    # Get devices section
    devices = root.find('./devices')

    # Add disk(s)
    if disk_paths:
        # Use provided disk paths (for orchestrator with multiple disks)
        for disk_info in disk_paths:
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
                'file': disk_info['path']
            })
            ET.SubElement(disk, 'target', attrib={
                'dev': disk_info['target'],
                'bus': 'virtio'
            })
    else:
        # Default: single disk (for edge/gateway)
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
            'file': f'{LIBVIRT_IMAGES_PATH}/velo/{name}.qcow2'
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
        'file': f'{LIBVIRT_IMAGES_PATH}/velo/{name}-cidata.iso'
    })
    ET.SubElement(cdrom, 'target', attrib={
        'dev': 'hdc',
        'bus': 'ide'
    })
    ET.SubElement(cdrom, 'readonly')

    # Add management interface (eth0 -> vmgmt)
    # PCI slot 0x03 for mgmt
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

    # Add data interfaces based on connections
    # Start at PCI slot 0x04
    pci_slot = 4
    if connections:
        for conn in connections:
            bridge = conn.get('bridge', '')
            local_port = conn.get('local_port', '')
            if bridge:
                data_int = ET.SubElement(devices, 'interface', attrib={'type': 'bridge'})
                ET.SubElement(data_int, 'source', attrib={'bridge': bridge})
                ET.SubElement(data_int, 'target', attrib={'dev': f'{name}_{local_port}'})
                ET.SubElement(data_int, 'model', attrib={'type': 'virtio'})
                ET.SubElement(data_int, 'virtualport', attrib={'type': 'openvswitch'})
                ET.SubElement(data_int, 'address', attrib={
                    'type': 'pci',
                    'domain': '0x0000',
                    'bus': '0x00',
                    'slot': hex(pci_slot),
                    'function': '0x0'
                })
                pci_slot += 1

    # Add VNC graphics for console debugging
    graphics = ET.SubElement(devices, 'graphics', attrib={
        'type': 'vnc',
        'autoport': 'yes',
        'listen': '127.0.0.1'
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


def copy_velo_base_image(vm_name: str, device_type: str) -> List[str]:
    """
    Copy the base VeloCloud image(s) for a new VM.

    For Edge and Gateway, copies a single disk image.
    For Orchestrator, copies all 4 disk images (rootfs, store, store2, store3).

    Args:
        vm_name: Name of the new VM
        device_type: Type of device (edge, gateway, orchestrator)

    Returns:
        List of paths to the new disk images
    """
    dest_dir = f'{LIBVIRT_IMAGES_PATH}/velo'

    # Ensure destination directory exists
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    copied_paths = []

    if device_type.lower() == 'orchestrator':
        # Orchestrator has 4 disk images
        disk_paths = get_velo_orchestrator_disk_paths(auto_download=True)

        for disk in disk_paths:
            # Destination path: /path/velo/{vm_name}-{diskname}.qcow2
            dest_path = f'{dest_dir}/{vm_name}-{disk["name"]}.qcow2'

            if not os.path.exists(disk['local_path']):
                raise RuntimeError(
                    f"VeloCloud orchestrator disk {disk['name']} not found at "
                    f"{disk['local_path']}. Please ensure the image is available "
                    "or check GCP bucket access."
                )

            shutil.copy2(disk['local_path'], dest_path)
            logger.info(f"Copied orchestrator {disk['name']} to {dest_path}")
            copied_paths.append({
                'path': dest_path,
                'target': disk['target'],
                'name': disk['name']
            })
    else:
        # Edge and Gateway have a single disk
        dest_path = f'{dest_dir}/{vm_name}.qcow2'

        # Get base image path (downloads from GCP if needed)
        base_image_path = get_velo_base_image_path(device_type, auto_download=True)

        if not os.path.exists(base_image_path):
            raise RuntimeError(
                f"VeloCloud {device_type} base image not found at {base_image_path}. "
                "Please ensure the image is available or check GCP bucket access."
            )

        shutil.copy2(base_image_path, dest_path)
        logger.info(f"Copied VeloCloud {device_type} base image to {dest_path}")
        copied_paths.append({
            'path': dest_path,
            'target': 'vda',
            'name': 'primary'
        })

    return copied_paths


def create_velo_device(
    name: str,
    device_type: str,
    mgmt_ip: str,
    connections: Optional[List[Dict]] = None,
    interface_ips: Optional[Dict[str, str]] = None,
    gateway_config: Optional[Dict[str, str]] = None,
    edge_config: Optional[Dict[str, str]] = None,
    orchestrator_config: Optional[Dict[str, str]] = None
) -> Dict:
    """
    Create a complete VeloCloud device VM.

    Args:
        name: Hostname for the new device
        device_type: Type of device (edge, gateway, orchestrator)
        mgmt_ip: Management IP address (from available pool)
        connections: Optional list of connection configs with 'target_device' and 'local_port'
        interface_ips: Optional dict of interface IPs with CIDR
        gateway_config: Optional dict for Gateway-specific config:
                        - vco: VeloCloud Orchestrator address
                        - activation_code: Gateway activation key
                        - eth0_ip: Public interface IP with CIDR
                        - eth0_gateway: Default gateway for eth0
                        - eth1_ip: Handoff interface IP with CIDR
                        - eth1_gateway: Gateway for eth1
        edge_config: Optional dict for Edge-specific config:
                     - vco: VeloCloud Orchestrator address
                     - activation_code: Edge activation key
                     - interfaces: dict of GE1-GE8 config (ip, netmask, gateway, type)
        orchestrator_config: Optional dict for Orchestrator-specific config:
                             - eth0_ip: Override management IP
                             - eth0_netmask: Management interface netmask
                             - eth0_gateway: Override gateway
                             - eth1_ip: Data interface IP (optional)
                             - eth1_netmask: Data interface netmask (optional)

    Returns:
        Dict with creation status and details
    """
    device_type_lower = device_type.lower()
    config = VELO_DEVICE_CONFIGS.get(device_type_lower)
    if not config:
        raise ValueError(f"Invalid device type: {device_type}")

    logger.info(f"Creating VeloCloud {device_type_lower}: {name} (Mgmt IP: {mgmt_ip})")

    # Check if VeloCloud is enabled
    if not is_velo_enabled():
        raise RuntimeError("VeloCloud feature is not enabled for this topology")

    # Check device limit
    current_count = get_velo_device_count(device_type_lower)
    max_allowed = config['max_per_topology']
    if current_count >= max_allowed:
        raise RuntimeError(
            f"Maximum of {max_allowed} VeloCloud {device_type_lower} "
            f"device(s) per topology reached"
        )

    created_resources = []

    try:
        # Step 1: Copy base image(s)
        # For orchestrator, this returns multiple disk paths
        logger.info(f"Copying base image(s) for {name}")
        disk_paths = copy_velo_base_image(name, device_type_lower)
        for disk_info in disk_paths:
            created_resources.append(('image', disk_info['path']))

        # Step 2: Generate cloud-init ISO
        logger.info(f"Generating cloud-init ISO for {name}")
        cidata_path = generate_velo_cloud_init(
            device_type_lower, name, mgmt_ip,
            interface_ips=interface_ips,
            gateway_config=gateway_config,
            edge_config=edge_config,
            orchestrator_config=orchestrator_config
        )
        created_resources.append(('cidata', cidata_path))

        # Step 3: Process connections if specified
        processed_connections = []
        if connections:
            for conn in connections:
                target_device = conn.get('target_device')
                if target_device:
                    target_port = conn.get('target_port') or find_next_available_port(target_device)
                    local_port = conn.get('local_port', 'wan1')

                    # Generate bridge name
                    bridge_name = generate_bridge_name(
                        name, local_port,
                        target_device, target_port
                    )

                    # Create OVS bridge
                    logger.info(f"Creating OVS bridge: {bridge_name}")
                    create_ovs_bridge(bridge_name)
                    created_resources.append(('bridge', bridge_name))

                    processed_connections.append({
                        'target_device': target_device,
                        'target_port': target_port,
                        'local_port': local_port,
                        'bridge': bridge_name
                    })

        # Step 4: Generate VM XML
        # Pass disk_paths for orchestrator's multiple disks
        logger.info(f"Generating VM XML for {name}")
        xml_content = generate_velo_xml(
            name, device_type_lower, processed_connections, disk_paths
        )

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

        # Step 7: Attach interfaces to target VMs if connected
        from slot_reuse import attach_interface_with_slot_reuse

        targets_reused_slots = []
        targets_need_reboot = []

        for conn in processed_connections:
            result = attach_interface_with_slot_reuse(
                target_device=conn['target_device'],
                target_port=conn['target_port'],
                bridge_name=conn['bridge'],
                connection_dict=conn
            )

            if result.reused_slot:
                targets_reused_slots.append(result.target_device)
            else:
                targets_need_reboot.append(result.target_device)

        # Clean up temp XML file
        if os.path.exists(xml_path):
            os.remove(xml_path)

        logger.info(f"Successfully created VeloCloud {device_type_lower}: {name}")

        return {
            'status': 'created',
            'name': name,
            'device_type': device_type_lower,
            'mgmt_ip': mgmt_ip,
            'interface_ips': interface_ips or {},
            'connections': processed_connections,
            'targets_reused_slots': targets_reused_slots,
            'targets_need_reboot': targets_need_reboot
        }

    except Exception as e:
        # Rollback on failure - track all failures for diagnostics
        logger.error(f"Error creating VeloCloud device {name}: {e}")
        logger.info(f"Rolling back creation of {name} ({len(created_resources)} resources to clean up)")

        rollback_failures = []
        rollback_success = []

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
                    # Note: We clean up bridges but NOT orphaned interfaces
                    # Orphaned interfaces are preserved for interface slot ordering
                    delete_ovs_bridge(resource_id)
                elif resource_type == 'xml':
                    if os.path.exists(resource_id):
                        os.remove(resource_id)
                rollback_success.append(f"{resource_type}:{resource_id}")
            except Exception as cleanup_error:
                rollback_failures.append({
                    'resource_type': resource_type,
                    'resource_id': resource_id,
                    'error': str(cleanup_error)
                })
                logger.warning(f"Rollback failed for {resource_type}:{resource_id}: {cleanup_error}")

        # Log rollback summary
        if rollback_failures:
            logger.error(
                f"Rollback incomplete for {name}: {len(rollback_failures)} failure(s), "
                f"{len(rollback_success)} success(es). "
                f"Failed resources may need manual cleanup: "
                f"{[f['resource_type']+'='+f['resource_id'] for f in rollback_failures]}"
            )
        else:
            logger.info(f"Rollback complete for {name}: {len(rollback_success)} resource(s) cleaned up")

        raise


def delete_velo_device(name: str) -> Dict:
    """
    Delete a VeloCloud device VM completely.

    Cleans up:
    - VM (destroy and undefine)
    - Disk image
    - Cloud-init ISO
    - OVS bridges to target devices
    - Interfaces on target devices

    Args:
        name: Name of the device to delete

    Returns:
        Dict with deletion status
    """
    from persistence import get_user_velo_device
    from resource_manager import get_resource_manager

    logger.info(f"Deleting VeloCloud device: {name}")

    resource_mgr = get_resource_manager()
    devices_needing_reboot = []

    # Step 1: Get device info from persistence BEFORE deleting
    device_entry = get_user_velo_device(name, USER_VELO_PATH)
    connections = []

    if device_entry:
        connections = device_entry.get('connections', [])
        if connections:
            logger.info(f"Found {len(connections)} connections to clean up")

    # Step 2: Clean up each connection
    for conn in connections:
        conn_result = resource_mgr.cleanup_connection(conn)
        if conn_result['target_device']:
            devices_needing_reboot.append(conn_result['target_device'])

    # Step 3: Delete VM and disk images
    vm_result = resource_mgr.delete_vm_with_cleanup(
        vm_name=name,
        disk_subdir='velo',
        has_cidata=True
    )

    # For orchestrator, also delete the additional disk images
    # (rootfs, store, store2, store3 - named as {name}-{diskname}.qcow2)
    device_type = device_entry.get('device_type', '') if device_entry else ''
    if device_type == 'orchestrator':
        for disk in VELO_ORCHESTRATOR_DISKS:
            disk_path = f'{LIBVIRT_IMAGES_PATH}/velo/{name}-{disk["name"]}.qcow2'
            if os.path.exists(disk_path):
                try:
                    os.remove(disk_path)
                    logger.info(f"Deleted orchestrator disk: {disk_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete orchestrator disk {disk_path}: {e}")

    logger.info(f"Deleted VeloCloud device: {name}")

    return {
        'status': 'deleted',
        'name': name,
        'details': {
            'vm_destroyed': vm_result['vm_destroyed'],
            'vm_undefined': vm_result['vm_undefined'],
            'disk_deleted': vm_result['disk_deleted'],
            'cidata_deleted': vm_result.get('cidata_deleted', False),
            'connections_cleaned': len(connections),
            'devices_needing_reboot': devices_needing_reboot
        }
    }


def get_velo_status() -> Dict:
    """
    Get the current status of VeloCloud devices.

    Returns:
        Dict with device counts, availability, and feature status
    """
    velo_config = get_velo_config()

    status = {
        'enabled': velo_config.get('enabled', False),
        'devices': {
            'edge': {
                'enabled': velo_config.get('edge_enabled', False),
                'count': get_velo_device_count('edge'),
                'max': velo_config.get('max_edge', MAX_VELO_EDGE_PER_TOPOLOGY),
                'available': 0
            },
            'gateway': {
                'enabled': velo_config.get('gateway_enabled', False),
                'count': get_velo_device_count('gateway'),
                'max': velo_config.get('max_gateway', MAX_VELO_GATEWAY_PER_TOPOLOGY),
                'available': 0
            },
            'orchestrator': {
                'enabled': velo_config.get('orchestrator_enabled', False),
                'count': get_velo_device_count('orchestrator'),
                'max': velo_config.get('max_orchestrator', MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY),
                'available': 0
            }
        },
        'total_count': get_velo_device_count()
    }

    # Calculate availability for each type
    for device_type in ['edge', 'gateway', 'orchestrator']:
        device_info = status['devices'][device_type]
        if device_info['enabled']:
            device_info['available'] = device_info['max'] - device_info['count']

    return status


def list_velo_devices() -> List[Dict]:
    """
    List all VeloCloud devices with their status.

    Returns:
        List of device info dicts
    """
    from persistence import list_user_velo_devices

    devices = list_user_velo_devices(USER_VELO_PATH)

    # Enrich with VM status
    for device in devices:
        name = device.get('name', '')
        try:
            result = subprocess.run(
                ['virsh', 'domstate', name],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )
            device['vm_state'] = result.stdout.strip() if result.returncode == 0 else 'unknown'
        except Exception:
            device['vm_state'] = 'unknown'

    return devices
