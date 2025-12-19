"""
Configuration constants for Nodebuilder Service
"""

import os
import re

# Service configuration
SERVICE_PORT = int(os.getenv('NODEBUILDER_PORT', 8090))
# Bind to all interfaces - required for uilanding proxy access via Docker bridge
# Security note: Port 8090 is not exposed externally, only accessible from host
SERVICE_HOST = os.getenv('NODEBUILDER_HOST', '0.0.0.0')

# File paths
DNSMASQ_PATH = os.getenv('DNSMASQ_PATH', '/etc/NetworkManager/dnsmasq.d/atd.conf')
ACCESS_INFO_PATH = os.getenv('ACCESS_INFO_PATH', '/etc/atd/ACCESS_INFO.yaml')
USER_NODES_PATH = os.getenv('USER_NODES_PATH', '/etc/atd/user_nodes.yaml')

# Security: Pattern for valid topology names (prevents path traversal)
VALID_TOPO_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


# Topology path - dynamically determined from ACCESS_INFO
def get_topo_build_path():
    """Get topo_build.yml path from ACCESS_INFO.yaml"""
    default_path = '/opt/atd/topologies/training-level1/topo_build.yml'
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            topo_tag = access_info.get('topology', 'training-level1')

            # Security: Validate topo_tag to prevent path traversal
            if not topo_tag or not VALID_TOPO_PATTERN.match(topo_tag):
                return default_path

            return f'/opt/atd/topologies/{topo_tag}/topo_build.yml'
    except Exception:
        return default_path

# VM configuration - fixed values, not user-configurable
VEOS_CPU = 2
VEOS_RAM_MB = 2048

# Node limits
MAX_TOTAL_NODES = 30  # Maximum total nodes (topology + user-added)
MAX_CONNECTIONS_PER_NODE = 16  # Maximum connections per node (vEOS limit)

# Valid device types for diagram positioning
# These must match the device types in uilanding/src/device_types.py
VALID_DEVICE_TYPES = frozenset([
    'internet', 'isp', 'rr', 'core', 'dci', 'p',
    'borderleaf', 'pe', 'ce', 'gw', 'router',
    'spine', 'leaf', 'memleaf', 'host', 'customer', 'oob', 'other'
])
DEFAULT_DEVICE_TYPE = 'host'

# libvirt paths
LIBVIRT_IMAGES_PATH = '/var/lib/libvirt/images'
VEOS_BASE_IMAGE_PATH = f'{LIBVIRT_IMAGES_PATH}/veos/base/veos.qcow2'

# Linux Host configuration
HOST_BASE_IMAGE_PATH = f'{LIBVIRT_IMAGES_PATH}/hosts/base/debian-lxde-base.qcow2'
HOST_CPU = 1
HOST_RAM_MB = 1024
HOST_DISK_GB = 5
MAX_HOSTS_PER_TOPOLOGY = 2
HOST_VNC_BASE_PORT = 5900  # VNC ports: 5900, 5901

# VyOS Firewall configuration
FIREWALL_BASE_IMAGE_PATH = f'{LIBVIRT_IMAGES_PATH}/firewall/base/vyos-base.qcow2'
FIREWALL_CPU = 1
FIREWALL_RAM_MB = 1024
FIREWALL_DISK_GB = 5
MAX_FIREWALLS_PER_TOPOLOGY = 1

# Persistence paths for new node types
USER_HOSTS_PATH = os.getenv('USER_HOSTS_PATH', '/etc/atd/user_hosts.yaml')
USER_FIREWALLS_PATH = os.getenv('USER_FIREWALLS_PATH', '/etc/atd/user_firewalls.yaml')

# Cloud-init templates directory
CLOUD_INIT_TEMPLATES_PATH = os.getenv(
    'CLOUD_INIT_TEMPLATES_PATH',
    '/opt/nodebuilder/images/cloud-init'
)

# Management bridge
MGMT_BRIDGE = 'vmgmt'


def get_device_credentials() -> dict:
    """
    Get device login credentials from ACCESS_INFO.yaml.

    Returns:
        Dict with 'username' and 'password' keys

    Falls back to defaults if ACCESS_INFO.yaml cannot be read.
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            login_info = access_info.get('login_info', {})
            # Network devices typically use jump_host credentials
            jump_host = login_info.get('jump_host', {})
            return {
                'username': jump_host.get('user', 'arista'),
                'password': jump_host.get('pw', 'arista')
            }
    except Exception:
        # Fallback to defaults if ACCESS_INFO unavailable
        return {
            'username': 'arista',
            'password': 'arista'
        }
