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


def download_base_image_from_gcp(
    gcp_path: str,
    local_path: str,
    timeout: int = None
) -> bool:
    """
    Download a base image from GCP bucket if it doesn't exist locally.

    Uses gsutil for authenticated access or curl for public buckets.

    Args:
        gcp_path: Path within the GCP bucket (e.g., 'hosts/ubuntu-desktop-base.qcow2')
        local_path: Local destination path
        timeout: Download timeout in seconds (defaults to BASE_IMAGE_DOWNLOAD_TIMEOUT)

    Returns:
        True if download succeeded or file already exists, False on error
    """
    if timeout is None:
        timeout = BASE_IMAGE_DOWNLOAD_TIMEOUT
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
            timeout=timeout
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
            timeout=timeout
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


# =============================================================================
# VeloCloud Configuration
# =============================================================================
# VeloCloud (VMware SD-WAN) device support - now owned by Arista
# Feature gate is read from ACCESS_INFO.yaml extras.velocloud_enabled

# Feature defaults (individual device types can be disabled here)
VELO_EDGE_ENABLED = True
VELO_GATEWAY_ENABLED = True
VELO_ORCHESTRATOR_ENABLED = True

# VM specifications
VELO_EDGE_CPU = 2
VELO_EDGE_RAM_MB = 8192  # 8GB required for 2 vCPU Edge (per VeloCloud specs)
VELO_GATEWAY_CPU = 4
VELO_GATEWAY_RAM_MB = 16384  # 16GB required for Gateway (per VeloCloud specs)
VELO_ORCHESTRATOR_CPU = 4
VELO_ORCHESTRATOR_RAM_MB = 8192

# Device limits per topology
MAX_VELO_EDGE_PER_TOPOLOGY = 2
MAX_VELO_GATEWAY_PER_TOPOLOGY = 1
MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY = 1

# Base image paths
VELO_EDGE_BASE_IMAGE = f'{LIBVIRT_IMAGES_PATH}/velo/base/velocloud-edge-base.qcow2'
VELO_GATEWAY_BASE_IMAGE = f'{LIBVIRT_IMAGES_PATH}/velo/base/velocloud-gateway-base.qcow2'
VELO_ORCHESTRATOR_BASE_IMAGE = f'{LIBVIRT_IMAGES_PATH}/velo/base/velocloud-orchestrator-base.qcow2'

# VeloCloud Orchestrator has multiple disk images (rootfs + 3 storage disks)
VELO_ORCHESTRATOR_DISKS = [
    {'name': 'rootfs', 'file': 'rootfs.qcow2', 'target': 'vda'},
    {'name': 'store', 'file': 'store.qcow2', 'target': 'vdb'},
    {'name': 'store2', 'file': 'store2.qcow2', 'target': 'vdc'},
    {'name': 'store3', 'file': 'store3.qcow2', 'target': 'vdd'},
]

# Persistence
USER_VELO_PATH = os.getenv('USER_VELO_PATH', '/etc/atd/user_velo.yaml')

# Interface naming
VELO_EDGE_WAN_PORTS = ['eth1', 'eth2', 'eth3']
VELO_EDGE_LAN_PORT = 'eth4'
VELO_GATEWAY_TRANSPORT_PORTS = ['eth1', 'eth2']
VELO_ORCHESTRATOR_DATA_PORT = 'eth1'


# Orphaned interfaces persistence (for interface slot preservation)
ORPHANED_INTERFACES_PATH = os.getenv(
    'ORPHANED_INTERFACES_PATH',
    '/etc/atd/orphaned_interfaces.yaml'
)

# Feature flag for interface slot preservation (can be disabled for rollback)
ENABLE_SLOT_PRESERVATION = os.getenv(
    'ENABLE_SLOT_PRESERVATION', 'true'
).lower() == 'true'

# Orphaned slot aging policy limits
# Maximum age in days before orphaned slots are automatically cleaned up
ORPHANED_SLOT_MAX_AGE_DAYS = int(os.getenv('ORPHANED_SLOT_MAX_AGE_DAYS', '30'))

# Maximum number of orphaned slots per device before oldest are pruned
ORPHANED_SLOT_MAX_PER_DEVICE = int(os.getenv('ORPHANED_SLOT_MAX_PER_DEVICE', '20'))

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
GCP_VELO_EDGE_IMAGE_PATH = 'velo/velocloud-edge-base.qcow2'
GCP_VELO_GATEWAY_IMAGE_PATH = 'velo/velocloud-gateway-base.qcow2'
GCP_VELO_ORCHESTRATOR_IMAGE_PATH = 'velo/velocloud-orchestrator-base.qcow2'

# GCP paths for Orchestrator's multiple disk images
GCP_VELO_ORCHESTRATOR_DISK_PATHS = [
    'velo/orchestrator/rootfs.qcow2',
    'velo/orchestrator/store.qcow2',
    'velo/orchestrator/store2.qcow2',
    'velo/orchestrator/store3.qcow2',
]

# Download timeout for base images (large files need time)
BASE_IMAGE_DOWNLOAD_TIMEOUT = 600  # 10 minutes
LARGE_IMAGE_DOWNLOAD_TIMEOUT = 1800  # 30 minutes for orchestrator disks (~2.5GB each)


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
    logger.info(f"  VeloCloud Edge image: {bucket}/{GCP_VELO_EDGE_IMAGE_PATH}")
    logger.info(f"  VeloCloud Gateway image: {bucket}/{GCP_VELO_GATEWAY_IMAGE_PATH}")
    logger.info(f"  VeloCloud Orchestrator image: {bucket}/{GCP_VELO_ORCHESTRATOR_IMAGE_PATH}")


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
# Security Utilities
# =============================================================================
# Functions for safe handling of sensitive data in logging and error messages.

# Sensitive field names to redact in logs
SENSITIVE_FIELDS = frozenset([
    'password', 'passwd', 'pw', 'secret', 'token', 'key',
    'credential', 'auth', 'api_key', 'apikey', 'activation_code'
])


def redact_sensitive(text: str, replacement: str = '***REDACTED***') -> str:
    """
    Redact potentially sensitive data from a text string.

    This function looks for patterns that might contain passwords or secrets
    and replaces them with a redaction marker.

    Args:
        text: The text to redact
        replacement: The replacement string for sensitive data

    Returns:
        Text with sensitive data redacted
    """
    import re

    # Redact password patterns in cloud-init style: password: value
    text = re.sub(
        r'(password|passwd|pw|secret|token|key)\s*[:=]\s*[^\s\n]+',
        f'\\1: {replacement}',
        text,
        flags=re.IGNORECASE
    )

    # Redact activation codes (XXXX-XXXX-XXXX-XXXX pattern)
    text = re.sub(
        r'\b[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b',
        replacement,
        text
    )

    return text


def redact_dict(data: dict, replacement: str = '***REDACTED***') -> dict:
    """
    Create a copy of a dictionary with sensitive fields redacted.

    Useful for safe logging of configuration or request data.

    Args:
        data: Dictionary to redact
        replacement: The replacement string for sensitive values

    Returns:
        New dictionary with sensitive values redacted
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in SENSITIVE_FIELDS):
            result[key] = replacement
        elif isinstance(value, dict):
            result[key] = redact_dict(value, replacement)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item, replacement) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def safe_error_message(error: Exception) -> str:
    """
    Create a safe error message that doesn't expose sensitive data.

    Filters out any potential passwords or secrets from error messages.

    Args:
        error: The exception to create a message from

    Returns:
        Sanitized error message string
    """
    error_str = str(error)
    return redact_sensitive(error_str)


# =============================================================================
# Timeout Configuration
# =============================================================================
# Centralized timeout values for subprocess calls and lock operations.
# Configurable via environment variables for different system performance profiles.

# Short timeout for quick operations (VM state checks, interface queries)
SUBPROCESS_TIMEOUT_SHORT = int(os.environ.get('NODEBUILDER_TIMEOUT_SHORT', 10))

# Default timeout for most subprocess calls (virsh commands, OVS operations)
SUBPROCESS_TIMEOUT_DEFAULT = int(os.environ.get('NODEBUILDER_TIMEOUT_DEFAULT', 30))

# Long timeout for operations that may take time (VM start/stop, disk operations)
SUBPROCESS_TIMEOUT_LONG = int(os.environ.get('NODEBUILDER_TIMEOUT_LONG', 60))

# Lock acquisition timeouts
CREATION_LOCK_TIMEOUT = float(os.environ.get('NODEBUILDER_CREATION_LOCK_TIMEOUT', 120.0))
PORT_ALLOCATION_LOCK_TIMEOUT = float(os.environ.get('NODEBUILDER_PORT_LOCK_TIMEOUT', 30.0))


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

# Maximum data interfaces before PCI slot collision with USB controller
# Slot 3: funcs 1-7 (7), Slot 4: funcs 0-7 (8), Slot 5: funcs 0-7 (8), Slot 6: funcs 0-6 (7)
# Total = 30, but slot 6 func 7 is USB controller, so max is 29
MAX_PCI_DATA_INTERFACES = 29


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


# =============================================================================
# VeloCloud Helper Functions
# =============================================================================

def is_velo_enabled() -> bool:
    """
    Check if VeloCloud features are enabled.

    VeloCloud is enabled if ANY of:
    1. extras.velocloud_enabled is True in ACCESS_INFO.yaml (explicit enable)
    2. The lab is in dev mode (atd-testdrivetraining-dev project)
    3. NODEBUILDER_TEST_MODE environment variable is set

    For production labs, velocloud_enabled must be explicitly set to True.

    Returns:
        True if VeloCloud features should be shown/available
    """
    # Allow test mode to bypass feature gate
    if os.getenv('NODEBUILDER_TEST_MODE', '').lower() == 'true':
        return True

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)

            # Check explicit enable flag
            extras = access_info.get('extras', {})
            if extras.get('velocloud_enabled', False):
                return True

            # Enable by default for dev labs
            project = access_info.get('project', '')
            if project and 'prod' not in project.lower():
                # Dev environment - enable VeloCloud by default
                return True

            return False
    except Exception:
        return False


def get_velo_config() -> dict:
    """
    Get VeloCloud configuration (all from config.py defaults).

    Returns:
        {
            'enabled': bool,           # From ACCESS_INFO extras
            'edge': {
                'enabled': bool,
                'max_count': int,
                'cpu': int,
                'ram_mb': int
            },
            'gateway': {...},
            'orchestrator': {...}
        }
    """
    return {
        'enabled': is_velo_enabled(),
        'edge': {
            'enabled': VELO_EDGE_ENABLED,
            'max_count': MAX_VELO_EDGE_PER_TOPOLOGY,
            'cpu': VELO_EDGE_CPU,
            'ram_mb': VELO_EDGE_RAM_MB
        },
        'gateway': {
            'enabled': VELO_GATEWAY_ENABLED,
            'max_count': MAX_VELO_GATEWAY_PER_TOPOLOGY,
            'cpu': VELO_GATEWAY_CPU,
            'ram_mb': VELO_GATEWAY_RAM_MB
        },
        'orchestrator': {
            'enabled': VELO_ORCHESTRATOR_ENABLED,
            'max_count': MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY,
            'cpu': VELO_ORCHESTRATOR_CPU,
            'ram_mb': VELO_ORCHESTRATOR_RAM_MB
        }
    }


def get_velo_base_image_path(device_type: str, auto_download: bool = True) -> str:
    """
    Get base image path for VeloCloud device type.
    Downloads from GCP if not found locally.

    Args:
        device_type: 'edge', 'gateway', or 'orchestrator'
        auto_download: If True, download from GCP if missing

    Returns:
        Path to the base image (may not exist if download failed)
    """
    image_map = {
        'edge': (VELO_EDGE_BASE_IMAGE, GCP_VELO_EDGE_IMAGE_PATH),
        'gateway': (VELO_GATEWAY_BASE_IMAGE, GCP_VELO_GATEWAY_IMAGE_PATH),
        'orchestrator': (VELO_ORCHESTRATOR_BASE_IMAGE, GCP_VELO_ORCHESTRATOR_IMAGE_PATH)
    }

    if device_type not in image_map:
        raise ValueError(f"Unknown VeloCloud device type: {device_type}")

    local_path, gcp_path = image_map[device_type]

    if os.path.exists(local_path):
        return local_path

    if auto_download:
        if download_base_image_from_gcp(gcp_path, local_path):
            return local_path

    return local_path


def get_velo_orchestrator_disk_paths(auto_download: bool = True) -> list:
    """
    Get all disk image paths for VeloCloud Orchestrator.
    The orchestrator uses 4 disk images: rootfs, store, store2, store3.

    Args:
        auto_download: If True, download from GCP if missing

    Returns:
        List of dicts with 'local_path', 'gcp_path', 'target', 'name' for each disk
    """
    base_dir = f'{LIBVIRT_IMAGES_PATH}/velo/base/orchestrator'
    disk_paths = []

    for i, disk in enumerate(VELO_ORCHESTRATOR_DISKS):
        local_path = os.path.join(base_dir, disk['file'])
        gcp_path = GCP_VELO_ORCHESTRATOR_DISK_PATHS[i]

        disk_info = {
            'name': disk['name'],
            'local_path': local_path,
            'gcp_path': gcp_path,
            'target': disk['target'],
            'file': disk['file']
        }

        # Download if missing (use longer timeout for large orchestrator disks)
        if auto_download and not os.path.exists(local_path):
            os.makedirs(base_dir, exist_ok=True)
            download_base_image_from_gcp(
                gcp_path, local_path,
                timeout=LARGE_IMAGE_DOWNLOAD_TIMEOUT
            )

        disk_paths.append(disk_info)

    return disk_paths
