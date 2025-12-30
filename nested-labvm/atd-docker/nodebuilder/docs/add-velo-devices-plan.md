# VeloCloud Device Support Implementation Plan

## Overview

Add support for VeloCloud (VMware SD-WAN) devices to the nodebuilder service. VeloCloud is now owned by Arista and should be available as training lab nodes.

## Device Types (Priority Order)

1. **Edge** - Customer premise equipment for branch/remote offices
2. **Orchestrator** - Management/control plane
3. **Gateway** - Cloud service gateways in datacenters

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| VCO Integration | Standalone | Training labs don't need real activation |
| Feature Gate Behavior | Hide menu | Cleaner UX when disabled |
| Manager Architecture | Single velo_manager.py | Less code duplication than 3 separate managers |
| Cloud-init | Placeholder templates | Will update when format is confirmed |

## Interface Configuration

| Device Type | Interfaces | Purpose |
|-------------|------------|---------|
| Edge | eth0 (mgmt) + eth1-3 (WAN) + eth4 (LAN) | Multi-WAN + LAN connectivity |
| Gateway | eth0 (mgmt) + eth1-2 (transport) | Datacenter transport interfaces |
| Orchestrator | eth0 (mgmt) + eth1 (data, optional) | Management with optional data plane |

## GCP Image Requirements

Images should be placed in the GCP bucket at the following paths:

```
gs://<bucket>/velo/velocloud-edge-base.qcow2
gs://<bucket>/velo/velocloud-gateway-base.qcow2
gs://<bucket>/velo/velocloud-orchestrator-base.qcow2
```

Local paths after download:
```
/var/lib/libvirt/images/velo/base/velocloud-edge-base.qcow2
/var/lib/libvirt/images/velo/base/velocloud-gateway-base.qcow2
/var/lib/libvirt/images/velo/base/velocloud-orchestrator-base.qcow2
```

## Feature Gate

### ACCESS_INFO.yaml (minimal - just the toggle)

The feature toggle lives under the existing `extras` key:

```yaml
extras:
  velocloud_enabled: true   # Master switch - hides entire Velo menu when false
  customer_details:         # Existing structure preserved
    exam_taker_id: "..."
    # ... other existing fields
```

### config.py (all defaults)

All VeloCloud configuration defaults are defined in config.py, not ACCESS_INFO.yaml:

```python
# VeloCloud feature defaults
VELO_EDGE_ENABLED = True
VELO_GATEWAY_ENABLED = True
VELO_ORCHESTRATOR_ENABLED = True

MAX_VELO_EDGE_PER_TOPOLOGY = 2
MAX_VELO_GATEWAY_PER_TOPOLOGY = 1
MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY = 1
```

**Testing Override**: Unit tests should bypass the feature gate check. Use environment variable or test fixture to enable VeloCloud features regardless of ACCESS_INFO.yaml.

---

## Phase 1: Configuration & Infrastructure

### 1.1 Config Changes (src/config.py)

Add new constants:

```python
# =============================================================================
# VeloCloud Configuration
# =============================================================================
# VeloCloud (VMware SD-WAN) device support - now owned by Arista

# Feature defaults (master switch read from ACCESS_INFO.yaml extras.velocloud_enabled)
VELO_EDGE_ENABLED = True
VELO_GATEWAY_ENABLED = True
VELO_ORCHESTRATOR_ENABLED = True

# VM specifications
VELO_EDGE_CPU = 2
VELO_EDGE_RAM_MB = 2048
VELO_GATEWAY_CPU = 4
VELO_GATEWAY_RAM_MB = 4096
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

# GCP paths for auto-download
GCP_VELO_EDGE_IMAGE_PATH = 'velo/velocloud-edge-base.qcow2'
GCP_VELO_GATEWAY_IMAGE_PATH = 'velo/velocloud-gateway-base.qcow2'
GCP_VELO_ORCHESTRATOR_IMAGE_PATH = 'velo/velocloud-orchestrator-base.qcow2'

# Persistence
USER_VELO_PATH = os.getenv('USER_VELO_PATH', '/etc/atd/user_velo.yaml')

# Interface naming
VELO_EDGE_WAN_PORTS = ['eth1', 'eth2', 'eth3']
VELO_EDGE_LAN_PORT = 'eth4'
VELO_GATEWAY_TRANSPORT_PORTS = ['eth1', 'eth2']
VELO_ORCHESTRATOR_DATA_PORT = 'eth1'
```

Add helper functions:

```python
def is_velo_enabled() -> bool:
    """
    Check if VeloCloud features are enabled via ACCESS_INFO.yaml.

    Reads extras.velocloud_enabled from ACCESS_INFO.yaml.
    Returns False if not present (disabled by default for production).

    Returns:
        True if VeloCloud features should be shown/available
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            extras = access_info.get('extras', {})
            return extras.get('velocloud_enabled', False)
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
        Path to the base image
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
```

### 1.2 Persistence (src/persistence.py)

Add functions for VeloCloud device persistence:

```python
def load_user_velo(path: str) -> Dict
def save_user_velo(data: Dict, path: str) -> bool
def get_user_velo_device(name: str, path: str) -> Optional[Dict]
def remove_user_velo_device(name: str, path: str) -> bool
def list_user_velo_devices(path: str) -> List[Dict]
def get_velo_device_count(device_type: str, path: str) -> int
```

File structure (user_velo.yaml):

```yaml
version: 1
created_at: "2025-01-15T10:00:00Z"
updated_at: "2025-01-15T10:30:00Z"
devices:
  - edge1:
      device_type: edge
      mgmt_ip: "192.168.0.50"
      status: active
      user_added: true
      wan_interfaces:
        - port: eth1
          ip: "10.1.1.1/24"
          target_device: spine1
          target_port: Ethernet5
          bridge: velo-edge1-1-sp1-5
        - port: eth2
          ip: "10.1.2.1/24"
          target_device: spine2
          target_port: Ethernet5
          bridge: velo-edge1-2-sp2-5
      lan_interface:
        port: eth4
        ip: "192.168.100.1/24"
        target_device: leaf1
        target_port: Ethernet7
        bridge: velo-edge1-4-le1-7
      neighbors:
        - neighborDevice: spine1
          neighborPort: Ethernet5
          port: eth1
        - neighborDevice: spine2
          neighborPort: Ethernet5
          port: eth2
        - neighborDevice: leaf1
          neighborPort: Ethernet7
          port: eth4
```

### 1.3 Validation (src/validation.py)

Add validation functions:

```python
def validate_velo_device_name(name: str, ...) -> Tuple[bool, str]
def validate_velo_device_limit(device_type: str, path: str) -> Tuple[bool, str]
def validate_velo_features_enabled(device_type: str = None) -> Tuple[bool, str]
```

---

## Phase 2: VeloCloud Manager

### 2.1 New File: src/velo_manager.py

Core functions:

```python
# Device creation
def create_velo_edge(name, mgmt_ip, wan_interfaces, lan_interface) -> Dict
def create_velo_gateway(name, mgmt_ip, transport_interfaces) -> Dict
def create_velo_orchestrator(name, mgmt_ip, data_interface=None) -> Dict

# Generic create dispatcher
def create_velo_device(device_type, name, mgmt_ip, interfaces) -> Dict

# Deletion
def delete_velo_device(name) -> Dict

# Cloud-init
def generate_velo_cloud_init_iso(device_type, hostname, mgmt_ip, interfaces) -> str

# XML generation
def generate_velo_xml(device_type, name, connections) -> str

# Image management
def copy_velo_base_image(device_type, vm_name) -> str

# Counts
def get_velo_device_count() -> int
def get_velo_device_count_by_type(device_type: str) -> int
```

### 2.2 Cloud-init Templates

Create placeholder templates that will be updated when VeloCloud format is confirmed:

**images/cloud-init/velocloud-edge-template.yaml**
```yaml
#cloud-config
# VeloCloud Edge cloud-init template
# TODO: Update with actual VeloCloud cloud-init format when available
#
# Variables replaced at runtime:
#   {hostname}     - VM hostname
#   {password}     - User password
#   {mgmt_ip}      - Management IP (eth0)
#   {gateway}      - Default gateway
#   {wan1_ip}      - WAN1 interface IP with CIDR (eth1)
#   {wan2_ip}      - WAN2 interface IP with CIDR (eth2)
#   {wan3_ip}      - WAN3 interface IP with CIDR (eth3)
#   {lan_ip}       - LAN interface IP with CIDR (eth4)
#
# Interface mapping:
#   eth0 - Management interface (vmgmt bridge)
#   eth1 - WAN1 interface (primary uplink)
#   eth2 - WAN2 interface (secondary uplink)
#   eth3 - WAN3 interface (tertiary uplink)
#   eth4 - LAN interface (local network)

# Placeholder - VeloCloud-specific format TBD
password: {password}

# Network configuration placeholder
# VeloCloud may use different format - update when confirmed
write_files:
  - path: /etc/velocloud/activation.json
    content: |
      {
        "standalone": true,
        "hostname": "{hostname}"
      }
```

**images/cloud-init/velocloud-gateway-template.yaml**
```yaml
#cloud-config
# VeloCloud Gateway cloud-init template
# TODO: Update with actual VeloCloud cloud-init format when available
#
# Variables:
#   {hostname}       - VM hostname
#   {password}       - User password
#   {mgmt_ip}        - Management IP (eth0)
#   {gateway}        - Default gateway
#   {transport1_ip}  - Transport1 IP with CIDR (eth1)
#   {transport2_ip}  - Transport2 IP with CIDR (eth2)
#
# Interface mapping:
#   eth0 - Management interface (vmgmt bridge)
#   eth1 - Transport1 interface
#   eth2 - Transport2 interface

password: {password}

write_files:
  - path: /etc/velocloud/activation.json
    content: |
      {
        "standalone": true,
        "hostname": "{hostname}",
        "role": "gateway"
      }
```

**images/cloud-init/velocloud-orchestrator-template.yaml**
```yaml
#cloud-config
# VeloCloud Orchestrator cloud-init template
# TODO: Update with actual VeloCloud cloud-init format when available
#
# Variables:
#   {hostname}   - VM hostname
#   {password}   - User password
#   {mgmt_ip}    - Management IP (eth0)
#   {gateway}    - Default gateway
#   {data_ip}    - Data interface IP with CIDR (eth1, optional)
#
# Interface mapping:
#   eth0 - Management interface (vmgmt bridge)
#   eth1 - Data interface (optional, for API/UI access)

password: {password}

write_files:
  - path: /etc/velocloud/activation.json
    content: |
      {
        "standalone": true,
        "hostname": "{hostname}",
        "role": "orchestrator"
      }
```

---

## Phase 3: API Endpoints

### 3.1 Status Endpoint

```python
@routes.get('/velo-status')
async def velo_status(request):
    """
    Get VeloCloud feature status and device counts.

    Response:
    {
        "enabled": true,
        "devices": {
            "edge": {"enabled": true, "current": 0, "max": 2, "can_add": true},
            "gateway": {"enabled": true, "current": 0, "max": 1, "can_add": true},
            "orchestrator": {"enabled": true, "current": 0, "max": 1, "can_add": true}
        },
        "existing_devices": [...]
    }

    Returns 403 if VeloCloud feature is disabled (with empty response).
    """
```

### 3.2 Device Creation Endpoints

```python
@routes.post('/add-velo-edge')
async def add_velo_edge(request):
    """
    Create VeloCloud Edge device.

    Request:
    {
        "name": "edge1",
        "mgmt_ip": "192.168.0.50",
        "wan_interfaces": [
            {"ip": "10.1.1.1/24", "target_device": "spine1", "target_port": "Ethernet5"},
            {"ip": "10.1.2.1/24", "target_device": "spine2"}  // target_port auto-selected
        ],
        "lan_interface": {
            "ip": "192.168.100.1/24",
            "target_device": "leaf1",
            "target_port": "Ethernet7"  // optional
        }
    }

    Response:
    {
        "status": "created",
        "name": "edge1",
        "mgmt_ip": "192.168.0.50",
        "wan_interfaces": [...],
        "lan_interface": {...},
        "targets_need_reboot": ["spine1", "spine2", "leaf1"]
    }
    """

@routes.post('/add-velo-gateway')
async def add_velo_gateway(request):
    """
    Create VeloCloud Gateway device.

    Request:
    {
        "name": "gateway1",
        "mgmt_ip": "192.168.0.51",
        "transport_interfaces": [
            {"ip": "10.1.1.254/24", "target_device": "spine1"},
            {"ip": "10.2.1.254/24", "target_device": "spine2"}
        ]
    }
    """

@routes.post('/add-velo-orchestrator')
async def add_velo_orchestrator(request):
    """
    Create VeloCloud Orchestrator device.

    Request:
    {
        "name": "vco1",
        "mgmt_ip": "192.168.0.52",
        "data_interface": {  // Optional
            "ip": "10.10.10.1/24",
            "target_device": "core1"
        }
    }
    """
```

### 3.3 Device Deletion

```python
@routes.post('/delete-velo-device')
async def delete_velo_device(request):
    """
    Delete any VeloCloud device by name.

    Request:
    {
        "name": "edge1"
    }

    Response:
    {
        "status": "deleted",
        "name": "edge1",
        "device_type": "edge",
        "details": {
            "vm_destroyed": true,
            "vm_undefined": true,
            "disk_deleted": true,
            "bridges_deleted": ["velo-edge1-1-sp1-5", ...],
            "devices_needing_reboot": ["spine1", "spine2", "leaf1"]
        }
    }
    """
```

### 3.4 Feature Gate Decorator

```python
def require_velo_feature(device_type: str = None):
    """
    Decorator to enforce VeloCloud feature gate.
    Returns 403 with empty body if feature is disabled.
    """
    def decorator(handler):
        async def wrapped(request):
            # Allow bypass in test mode
            if os.getenv('NODEBUILDER_TEST_MODE') == 'true':
                return await handler(request)

            features = get_velo_features_enabled()

            if not features['enabled']:
                return web.json_response({}, status=403)

            if device_type:
                type_key = f'{device_type}_enabled'
                if not features.get(type_key, False):
                    return web.json_response({
                        'error': f'VeloCloud {device_type} is not enabled'
                    }, status=403)

            return await handler(request)
        return wrapped
    return decorator
```

---

## Phase 4: Integration

### 4.1 Resource Manager Updates

Add VeloCloud cleanup to reset_all_user_nodes():

```python
# In resource_manager.py reset_all_user_nodes()

# Phase 4: Delete VeloCloud devices
velo_devices = list_user_velo_devices(USER_VELO_PATH)
for device in velo_devices:
    name = list(device.keys())[0]
    try:
        delete_velo_device(name)
        results['velo_deleted'].append(name)
    except Exception as e:
        results['errors'].append(f"Failed to delete velo device {name}: {e}")
```

### 4.2 Unified Topology Updates

Add VeloCloud devices to unified topology view:

```python
# In unified_topology.py get_unified_topology()

# Add VeloCloud devices
velo_devices = load_user_velo(user_velo_path)
for device_entry in velo_devices.get('devices', []):
    for name, info in device_entry.items():
        device_type = info.get('device_type', 'edge')
        unified_devices.append({
            'name': name,
            'device_type': f'velo_{device_type}',
            'category': 'edge' if device_type != 'orchestrator' else 'core',
            'mgmt_ip': info.get('mgmt_ip'),
            'user_added': True,
            'source': 'velo',
            'neighbors': info.get('neighbors', [])
        })
```

### 4.3 Device Type Classification

Update uilanding device_types.py (separate PR):

```python
# Add VeloCloud device types
DEVICE_TYPES = {
    ...
    'velo_edge': {'category': 'edge', 'tier': 6},
    'velo_gateway': {'category': 'edge', 'tier': 5},
    'velo_orchestrator': {'category': 'core', 'tier': 2}
}
```

---

## Phase 5: Testing

### 5.1 Test Files to Create

1. **tests/test_velo_manager.py**
   - Cloud-init generation for each device type
   - XML generation for each device type
   - Image path resolution
   - Device count functions

2. **tests/test_velo_persistence.py**
   - Load/save operations
   - Device CRUD operations
   - Count by type

3. **tests/test_velo_validation.py**
   - Name validation
   - Limit validation
   - Feature gate validation

4. **tests/test_velo_api.py**
   - All endpoints with mocked managers
   - Feature gate returns 403 when disabled
   - Validation error responses

### 5.2 Test Fixtures

```python
@pytest.fixture
def velo_enabled():
    """Enable VeloCloud features for testing"""
    os.environ['NODEBUILDER_TEST_MODE'] = 'true'
    yield
    del os.environ['NODEBUILDER_TEST_MODE']

@pytest.fixture
def mock_velo_features_enabled():
    """Mock feature gate as enabled"""
    with patch('config.get_velo_features_enabled') as mock:
        mock.return_value = {
            'enabled': True,
            'edge_enabled': True,
            'edge_max': 2,
            'gateway_enabled': True,
            'gateway_max': 1,
            'orchestrator_enabled': True,
            'orchestrator_max': 1
        }
        yield mock
```

---

## Implementation Order

### Sprint 1: Foundation (Days 1-2)
- [ ] Add config constants and helper functions
- [ ] Add persistence functions for VeloCloud devices
- [ ] Add validation functions
- [ ] Create placeholder cloud-init templates
- [ ] Add feature gate reading from ACCESS_INFO.yaml

### Sprint 2: Edge Device (Days 3-5)
- [ ] Implement velo_manager.py (Edge creation/deletion)
- [ ] Add /velo-status endpoint
- [ ] Add /add-velo-edge endpoint
- [ ] Add /delete-velo-device endpoint
- [ ] Unit tests for Edge
- [ ] Integration test for Edge lifecycle

### Sprint 3: Gateway & Orchestrator (Days 6-7)
- [ ] Extend velo_manager.py for Gateway
- [ ] Extend velo_manager.py for Orchestrator
- [ ] Add corresponding API endpoints
- [ ] Unit + integration tests for both

### Sprint 4: Integration (Days 8-9)
- [ ] Update resource_manager for reset-all
- [ ] Update unified_topology
- [ ] Add orphaned bridge cleanup
- [ ] Full integration testing
- [ ] Documentation

---

## File Summary

### New Files
| File | Purpose | Est. Lines |
|------|---------|------------|
| src/velo_manager.py | VeloCloud device lifecycle | 500-600 |
| images/cloud-init/velocloud-edge-template.yaml | Edge provisioning | 40 |
| images/cloud-init/velocloud-gateway-template.yaml | Gateway provisioning | 30 |
| images/cloud-init/velocloud-orchestrator-template.yaml | Orchestrator provisioning | 25 |
| tests/test_velo_manager.py | Manager unit tests | 200 |
| tests/test_velo_persistence.py | Persistence tests | 100 |
| tests/test_velo_validation.py | Validation tests | 80 |
| tests/test_velo_api.py | API endpoint tests | 150 |

### Modified Files
| File | Changes | Est. Lines Added |
|------|---------|------------------|
| src/config.py | Constants, helpers | 100 |
| src/persistence.py | Velo CRUD functions | 120 |
| src/validation.py | Velo validation | 60 |
| src/nodebuilder_service.py | API endpoints | 250 |
| src/resource_manager.py | Reset-all cleanup | 40 |
| src/unified_topology.py | Include velo devices | 30 |

---

## Open Items

1. **Cloud-init Format**: Update templates when VeloCloud format is confirmed
2. **UI Integration**: Coordinate with UI team for "Velo" submenu implementation
3. **Image Building**: Document process for creating VeloCloud qcow2 images
4. **VNC Support**: Determine if VeloCloud devices need noVNC access like Linux hosts
