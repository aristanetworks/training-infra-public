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
USER_HOSTS_PATH = os.getenv('USER_HOSTS_PATH', '/etc/atd/user_hosts.yaml')
USER_FIREWALLS_PATH = os.getenv('USER_FIREWALLS_PATH', '/etc/atd/user_firewalls.yaml')

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
# Supports both Debian and Ubuntu base images - uses whichever is available
HOST_BASE_IMAGE_DEBIAN = f'{LIBVIRT_IMAGES_PATH}/hosts/base/debian-lxde-base.qcow2'
HOST_BASE_IMAGE_UBUNTU = f'{LIBVIRT_IMAGES_PATH}/hosts/base/ubuntu-desktop-base.qcow2'
HOST_CPU = 2
HOST_RAM_MB = 1536
HOST_DISK_GB = 10  # Ubuntu needs more space for desktop packages
MAX_HOSTS_PER_TOPOLOGY = 2
# VNC ports for Linux hosts - start at 5920 to avoid conflicts with
# existing topology VMs which typically use ports 5900-5915
HOST_VNC_BASE_PORT = 5920  # VNC ports: 5920, 5921


def download_base_image_from_gcp(gcp_path: str, local_path: str) -> bool:
    """
    Download a base image from GCP bucket if it doesn't exist locally.

    Uses gsutil for authenticated access or curl for public buckets.

    Args:
        gcp_path: Path within the GCP bucket (e.g., 'hosts/ubuntu-desktop-base.qcow2')
        local_path: Local destination path

    Returns:
        True if download succeeded or file already exists, False on error
    """
    import subprocess
    import logging

    logger = logging.getLogger('nodebuilder')

    # Already exists locally
    if os.path.exists(local_path):
        return True

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    # Build full GCP URL
    gcp_url = f"{GCP_BASE_IMAGE_BUCKET}/{gcp_path}"

    logger.info(f"Downloading base image from {gcp_url}")
    logger.info(f"Destination: {local_path}")
    logger.info("This may take several minutes for large images...")

    # Try gsutil first (handles auth automatically)
    try:
        result = subprocess.run(
            ['gsutil', 'cp', gcp_url, local_path],
            capture_output=True,
            text=True,
            timeout=BASE_IMAGE_DOWNLOAD_TIMEOUT
        )
        if result.returncode == 0:
            logger.info(f"Successfully downloaded {local_path}")
            return True
        else:
            logger.warning(f"gsutil failed: {result.stderr}")
    except FileNotFoundError:
        logger.info("gsutil not found, trying curl...")
    except subprocess.TimeoutExpired:
        logger.error("Download timed out")
        # Clean up partial download
        if os.path.exists(local_path):
            os.remove(local_path)
        return False

    # Fallback to curl for public bucket URLs
    # Convert gs:// to https:// storage URL
    https_url = gcp_url.replace('gs://', 'https://storage.googleapis.com/')

    try:
        result = subprocess.run(
            ['curl', '-L', '--progress-bar', '-o', local_path, https_url],
            capture_output=False,  # Show progress bar
            timeout=BASE_IMAGE_DOWNLOAD_TIMEOUT
        )
        if result.returncode == 0 and os.path.exists(local_path):
            # Verify file isn't empty or error page
            if os.path.getsize(local_path) > MIN_VALID_IMAGE_SIZE_BYTES:
                logger.info(f"Successfully downloaded {local_path}")
                return True
            else:
                logger.error("Downloaded file too small - may be an error page")
                os.remove(local_path)
                return False
        else:
            logger.error(f"curl download failed")
            return False
    except FileNotFoundError:
        logger.error("Neither gsutil nor curl available")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Download timed out")
        if os.path.exists(local_path):
            os.remove(local_path)
        return False


def get_host_base_image_path(auto_download: bool = True) -> str:
    """
    Get the path to the Linux host base image.
    Checks for Ubuntu first (easier to set up), then Debian.
    Optionally downloads from GCP if not found locally.

    Args:
        auto_download: If True, attempt to download from GCP if not found

    Returns:
        Path to the base image (may not exist if download failed)
    """
    import os
    # Prefer Ubuntu (easier cloud image setup)
    if os.path.exists(HOST_BASE_IMAGE_UBUNTU):
        return HOST_BASE_IMAGE_UBUNTU
    # Fall back to Debian
    if os.path.exists(HOST_BASE_IMAGE_DEBIAN):
        return HOST_BASE_IMAGE_DEBIAN

    # Try to download Ubuntu image from GCP
    if auto_download:
        if download_base_image_from_gcp(GCP_HOST_IMAGE_PATH, HOST_BASE_IMAGE_UBUNTU):
            return HOST_BASE_IMAGE_UBUNTU

    # Return Ubuntu path for error messages (preferred option)
    return HOST_BASE_IMAGE_UBUNTU

# VyOS Firewall configuration
FIREWALL_BASE_IMAGE_PATH = f'{LIBVIRT_IMAGES_PATH}/firewall/base/vyos-base.qcow2'
FIREWALL_CPU = 1
FIREWALL_RAM_MB = 1024
FIREWALL_DISK_GB = 5
MAX_FIREWALLS_PER_TOPOLOGY = 1


def get_firewall_base_image_path(auto_download: bool = True) -> str:
    """
    Get the path to the VyOS firewall base image.
    Downloads from GCP if not found locally.

    Args:
        auto_download: If True, attempt to download from GCP if not found

    Returns:
        Path to the base image (may not exist if download failed)
    """
    if os.path.exists(FIREWALL_BASE_IMAGE_PATH):
        return FIREWALL_BASE_IMAGE_PATH

    # Try to download from GCP
    # Note: GCP_FIREWALL_IMAGE_PATH is defined later in the file
    # This function is called at runtime, so it will be available
    if auto_download:
        try:
            if download_base_image_from_gcp(GCP_FIREWALL_IMAGE_PATH, FIREWALL_BASE_IMAGE_PATH):
                return FIREWALL_BASE_IMAGE_PATH
        except NameError:
            # GCP constants not yet defined (shouldn't happen at runtime)
            pass

    return FIREWALL_BASE_IMAGE_PATH


# Orphaned interfaces persistence (for interface slot preservation)
ORPHANED_INTERFACES_PATH = os.getenv(
    'ORPHANED_INTERFACES_PATH',
    '/etc/atd/orphaned_interfaces.yaml'
)

# Feature flag for interface slot preservation (can be disabled for rollback)
ENABLE_SLOT_PRESERVATION = os.getenv(
    'ENABLE_SLOT_PRESERVATION', 'true'
).lower() == 'true'

# Cloud-init templates directory
CLOUD_INIT_TEMPLATES_PATH = os.getenv(
    'CLOUD_INIT_TEMPLATES_PATH',
    '/opt/nodebuilder/images/cloud-init'
)

# Management bridge
MGMT_BRIDGE = 'vmgmt'

# Interface port naming for topology diagram neighbors
# These are the interface names used on VMs for data connections
HOST_DATA_PORT = 'eth1'           # Linux host data interface
FIREWALL_INSIDE_PORT = 'eth1'     # VyOS firewall inside interface
FIREWALL_OUTSIDE_PORT = 'eth2'    # VyOS firewall outside interface

# GCP bucket for base images (downloaded on first use)
# Bucket is determined by ATL project: dev or prod
def get_gcp_project() -> str:
    """
    Get the GCP project from ACCESS_INFO.yaml.

    Returns:
        'dev' or 'prod' based on the project field
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            project = access_info.get('project', '')
            # atd-testdrivetraining-dev -> dev
            # atd-testdrivetraining-prod -> prod
            if 'prod' in project.lower():
                return 'prod'
            return 'dev'
    except Exception:
        return 'dev'  # Default to dev for safety


def get_gcp_base_image_bucket() -> str:
    """
    Get the GCP bucket URL for base images based on environment.

    Bucket names:
    - Dev (atd-testdrivetraining-dev): gs://cloud-init-files-atl-labs
    - Prod (atd-testdrivetraining-prod): gs://cloud-init-files-atl-labs-prod

    Returns:
        GCP bucket URL
    """
    # Allow override via environment variable
    env_bucket = os.getenv('GCP_BASE_IMAGE_BUCKET')
    if env_bucket:
        return env_bucket

    # Determine bucket from project
    env = get_gcp_project()
    if env == 'prod':
        return 'gs://cloud-init-files-atl-labs-prod'
    return 'gs://cloud-init-files-atl-labs'


# Cached bucket URL (computed on first access)
GCP_BASE_IMAGE_BUCKET = get_gcp_base_image_bucket()
GCP_HOST_IMAGE_PATH = 'hosts/ubuntu-desktop-base.qcow2'
GCP_FIREWALL_IMAGE_PATH = 'firewall/vyos-base.qcow2'

# Download timeout for base images (large files need time)
BASE_IMAGE_DOWNLOAD_TIMEOUT = 600  # 10 minutes


def log_gcp_config():
    """
    Log the GCP configuration for debugging.
    Called at service startup.
    """
    import logging
    logger = logging.getLogger('nodebuilder')
    project = get_gcp_project()
    bucket = GCP_BASE_IMAGE_BUCKET
    logger.info(f"GCP Configuration: project={project}, bucket={bucket}")
    logger.info(f"  Host image: {bucket}/{GCP_HOST_IMAGE_PATH}")
    logger.info(f"  Firewall image: {bucket}/{GCP_FIREWALL_IMAGE_PATH}")


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


# =============================================================================
# Timeout Configuration
# =============================================================================
# Centralized timeout values for subprocess calls and lock operations.
# Adjust these based on system performance and network conditions.

# Short timeout for quick operations (VM state checks, interface queries)
SUBPROCESS_TIMEOUT_SHORT = 10

# Default timeout for most subprocess calls (virsh commands, OVS operations)
SUBPROCESS_TIMEOUT_DEFAULT = 30

# Long timeout for operations that may take time (VM start/stop, disk operations)
SUBPROCESS_TIMEOUT_LONG = 60

# Lock acquisition timeouts
CREATION_LOCK_TIMEOUT = 120.0  # Lock for VM/cluster creation operations
PORT_ALLOCATION_LOCK_TIMEOUT = 30.0  # Lock for port allocation


# =============================================================================
# PCI Slot Configuration
# =============================================================================
# PCI slots for VM interface assignments. These follow libvirt conventions.
# Format: (slot_hex, function_hex)

# USB controller slots (standard for all VMs)
PCI_SLOT_USB_CONTROLLER = ('0x06', '0x7')

# VirtIO serial controller (standard for all VMs)
PCI_SLOT_VIRTIO_SERIAL = ('0x07', '0x0')

# Management interface slot (eth0/first interface)
PCI_SLOT_MGMT_INTERFACE = ('0x03', '0x0')

# Data interface slots for Linux hosts and firewalls
PCI_SLOT_DATA_INTERFACE_1 = ('0x04', '0x0')  # eth1 / inside interface
PCI_SLOT_DATA_INTERFACE_2 = ('0x05', '0x0')  # eth2 / outside interface


# =============================================================================
# Port Number Configuration
# =============================================================================
# Ethernet port numbering for topology connections

# Minimum port number for data interfaces (Ethernet1 is first data port)
MIN_DATA_PORT_NUMBER = 1

# Maximum port number for data interfaces (vEOS limit)
MAX_DATA_PORT_NUMBER = 99

# Starting port number for topology neighbor detection
DEFAULT_STARTING_PORT = 1


# =============================================================================
# Network Configuration Defaults
# =============================================================================
# Default values for network simulation and configuration

# Default network latency for simulated links (milliseconds)
DEFAULT_NETWORK_LATENCY_MS = 25

# Minimum file size to consider a downloaded image valid (1MB)
MIN_VALID_IMAGE_SIZE_BYTES = 1000000
