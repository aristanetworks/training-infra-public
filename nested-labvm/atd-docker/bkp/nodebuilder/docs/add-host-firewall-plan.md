# Add Nodes Project - Implementation Status

## Overview

This document tracks the implementation of dynamic node management for ATL (Arista Training Labs):

1. **Add Node** - Dynamically add vEOS switches to running topologies
2. **Add Host** - Add Linux desktop VMs with noVNC access
3. **Add Firewall** - Add VyOS firewall VMs

**Branch:** `feature/add-nodes` (from `nested-release`)
**Commits:** 48+
**Status:** Core functionality complete, testing and polish phase

---

## Implementation Status

### Legend
- [x] Completed
- [ ] Pending
- [~] Partial/Needs Testing

---

## Backend: Nodebuilder Service

### Core Infrastructure
- [x] REST API service (aiohttp, port 8090)
- [x] Modular Python architecture
- [x] Transaction-based operations with rollback
- [x] Atomic YAML persistence
- [x] Input validation and sanitization
- [x] Error handling with safe messages

### vEOS Node Management
- [x] Add new vEOS switches with ZTP
- [x] Edit connections (add/remove interfaces)
- [x] Delete nodes with full cleanup
- [x] Cluster templates (Internet simulation)
- [x] Connection validation
- [x] Port allocation

### Linux Host Support
- [x] Ubuntu Desktop VMs with LXDE
- [x] cloud-init provisioning
- [x] Network tools (ping, traceroute, iperf3, tcpdump, mtr)
- [x] noVNC browser-based desktop access
- [x] Single data interface connection
- [x] Max 2 hosts per topology limit

### VyOS Firewall Support
- [x] VyOS 1.4 firewall VMs
- [x] cloud-init configuration
- [x] Inside/outside interface IP assignment
- [x] SSH and console access
- [x] Edit interface IPs
- [x] Max 1 firewall per topology limit

### Lifecycle Management
- [x] Restore user nodes after reboot
- [x] Full reset to original topology
- [x] Orphaned bridge cleanup
- [x] Device status (ping-based)
- [x] Include hosts/firewalls in restore

---

## Frontend: UILanding

### Wizards
- [x] AddNodeWizard - 4-step vEOS creation
- [x] AddHostWizard - Linux desktop creation
- [x] AddFirewallWizard - VyOS with inside/outside config
- [x] AddClusterWizard - Multi-node templates
- [x] Dropdown connection selection (standardized)
- [x] Multi-port connections to same device

### Topology Visualization
- [x] Cytoscape.js integration
- [x] Device type styling (colors, shapes)
- [x] Tier-based layout
- [x] WAN and Datacenter modes
- [x] Filter by device type
- [x] Position persistence
- [x] linux_host and firewall positioning

### Terminal Page
- [x] SSH access to all devices
- [x] Serial console for vEOS/VyOS
- [x] noVNC desktop for Linux hosts
- [x] Status dots (SSH/Console/noVNC colors)
- [x] Device grouping by type

### Device Type System
- [x] Centralized DeviceTypeConfig class
- [x] Category grouping (provider, core, edge, fabric, endpoint)
- [x] Pattern-based classification
- [x] Explicit classification for user-defined types
- [x] Colors that don't conflict with status

---

## API Endpoints (All Implemented)

| Method | Endpoint | Status |
|--------|----------|--------|
| GET | `/health` | [x] |
| GET | `/available-ips` | [x] |
| GET | `/existing-nodes` | [x] |
| GET | `/target-devices` | [x] |
| GET | `/user-nodes-status` | [x] |
| POST | `/validate-node` | [x] |
| POST | `/add-node` | [x] |
| POST | `/edit-node` | [x] |
| POST | `/delete-node` | [x] |
| POST | `/restore-user-nodes` | [x] |
| POST | `/reset-all-user-nodes` | [x] |
| GET | `/cluster-templates` | [x] |
| POST | `/add-cluster` | [x] |
| POST | `/save-config` | [x] |
| POST | `/reboot-devices` | [x] |
| GET | `/host-status` | [x] |
| POST | `/add-host` | [x] |
| POST | `/delete-host` | [x] |
| GET | `/novnc-token/{name}` | [x] |
| GET | `/firewall-status` | [x] |
| POST | `/add-firewall` | [x] |
| POST | `/edit-firewall` | [x] |
| POST | `/delete-firewall` | [x] |

---

## Pending Work

### Testing & Validation
- [ ] End-to-end testing of full reset function
- [ ] Test host/firewall restore after topology reboot
- [ ] Verify interface numbering consistency
- [ ] Test noVNC with multiple concurrent hosts
- [ ] Test cluster creation with internal connections

### Frontend Enhancements
- [ ] Add "Reset All" button to UI (calls `/reset-all-user-nodes`)
- [ ] Edit host connections (currently only add/delete)
- [ ] Progress indicators for long operations
- [ ] Better error messages for failed operations
- [ ] Loading states during API calls

### Documentation
- [x] Update project CLAUDE.md
- [x] Update this project plan
- [ ] Create user-facing documentation
- [ ] Mermaid architecture diagrams
- [ ] Obsidian vault documentation

### Edge Cases & Robustness
- [ ] Handle concurrent add/delete operations
- [ ] Timeout handling for slow VM creation
- [ ] Graceful degradation when images missing
- [ ] VNC port conflict resolution

### Production Readiness
- [ ] Base image distribution (GCP bucket)
- [ ] Docker image builds for nodebuilder
- [ ] Integration with ATL deployment pipeline
- [ ] Resource limits and quotas
- [ ] Monitoring and alerting

### Nice-to-Have Features
- [ ] Clone existing user node
- [ ] Batch operations (delete multiple)
- [ ] Export/import topology customizations
- [ ] Network impairments on user connections

---

## Architecture

### System Integration

```
┌─────────────────────────────────────────────────────────────────┐
│                      UI Landing (Frontend)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ AddNode      │  │ AddHost      │  │ AddFirewall  │          │
│  │ Wizard       │  │ Wizard       │  │ Wizard       │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ AddCluster   │  │ Topology     │  │ Terminal     │          │
│  │ Wizard       │  │ Manager      │  │ Manager      │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Nodebuilder Service (Backend - Port 8090)           │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ vm_manager.py   │  │ host_manager.py │  │firewall_manager │ │
│  │ (vEOS VMs)      │  │ (Linux hosts)   │  │ (VyOS VMs)      │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │resource_manager │  │connection_mgr   │  │ persistence.py  │ │
│  │ (cleanup)       │  │ (OVS bridges)   │  │ (YAML state)    │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Infrastructure Layer                          │
│  - libvirt/KVM for VM management                                │
│  - OVS bridges for network connectivity                         │
│  - cloud-init ISOs for provisioning                             │
│  - Base images: ubuntu-desktop.qcow2, vyos-1.4.qcow2           │
└─────────────────────────────────────────────────────────────────┘
```

### Network Connectivity

**vEOS Node (multiple interfaces)**
```
vEOS (Ethernet1) ──> OVS Bridge ──> Target Switch (EthernetX)
vEOS (Ethernet2) ──> OVS Bridge ──> Target Switch (EthernetY)
```

**Linux Host (1 data interface)**
```
Linux Host (eth0) ──> vmgmt bridge (management - 192.168.0.x)
Linux Host (eth1) ──> OVS Bridge ──> Switch (EthernetX)
```

**VyOS Firewall (3 interfaces)**
```
VyOS (eth0/mgmt)   ──> vmgmt bridge (management - 192.168.0.x)
VyOS (eth1/inside) ──> OVS Bridge ──> Inside Switch
VyOS (eth2/outside)──> OVS Bridge ──> Outside Switch
```

---

## File Structure

```
nodebuilder/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── nodebuilder_service.py  # REST API (25 endpoints)
│   ├── vm_manager.py           # vEOS VM lifecycle
│   ├── host_manager.py         # Linux host lifecycle
│   ├── firewall_manager.py     # VyOS firewall lifecycle
│   ├── resource_manager.py     # Cleanup and reset
│   ├── connection_manager.py   # OVS bridge management
│   ├── interface_manager.py    # Port allocation
│   ├── persistence.py          # YAML state management
│   ├── validation.py           # Input validation
│   ├── novnc_manager.py        # VNC token management
│   ├── cluster_templates.py    # Multi-node templates
│   ├── transactions.py         # Atomic operations
│   └── config.py               # Configuration
├── images/
│   ├── build-ubuntu-desktop.sh
│   ├── build-vyos.sh
│   ├── build-debian-lxde.sh
│   └── cloud-init/
│       ├── ubuntu-desktop-template.yaml
│       ├── vyos-firewall-template.yaml
│       └── debian-host-template.yaml
└── docs/
    └── add-host-firewall-plan.md  # This file

uilanding/src/html/js/topology/
├── add-node-wizard.js       # vEOS creation wizard
├── add-host-wizard.js       # Linux host wizard
├── add-firewall-wizard.js   # VyOS firewall wizard
├── add-cluster-wizard.js    # Cluster deployment wizard
├── base-modal.js            # Shared modal functionality
├── topology-manager.js      # Cytoscape.js integration
├── filter-manager.js        # Device type filtering
├── node-builder-api.js      # Backend API client
├── cytoscape-styles.js      # Graph styling
├── event-handlers.js        # UI event handling
├── status-updater.js        # Device status polling
├── device-reboot-manager.js # VM reboot handling
├── capture-panel.js         # Packet capture UI
└── layout-config.js         # Layout algorithms

uilanding/src/
├── device_types.py          # Device type classification
└── uilanding.py             # Main Flask app
```

---

## Specifications

### Add Node (vEOS Switch)

| Specification | Value |
|---------------|-------|
| Platform | vEOS (Arista EOS virtual) |
| Resources | 2 vCPU, 4GB RAM |
| Provisioning | ZTP via dnsmasq |
| Access Methods | SSH, Serial Console |
| Max per Topology | Limited by available IPs |
| Connections | Multiple, to any switch |

### Add Host (Linux Desktop)

| Specification | Value |
|---------------|-------|
| Operating System | Ubuntu + LXDE Desktop |
| Resources | 1 vCPU, 1GB RAM, 5GB disk |
| Access Methods | noVNC (browser desktop), SSH |
| Max per Topology | 2 |
| Network | Single data interface |
| IP Assignment | From dnsmasq pool |
| Provisioning | cloud-init |
| Default User | arista / arista |
| Pre-installed | ping, traceroute, iperf3, tcpdump, mtr |

### Add Firewall (VyOS)

| Specification | Value |
|---------------|-------|
| Operating System | VyOS 1.4 Community Edition |
| Resources | 1 vCPU, 1GB RAM, 5GB disk |
| Access Methods | SSH, Serial Console |
| Max per Topology | 1 |
| Interfaces | 3 (management, inside, outside) |
| Inside/Outside IPs | User-configured (CIDR) |
| Provisioning | cloud-init |
| Default User | arista / arista |
| Default Rules | None (student configures) |

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2024-12 | Ubuntu + LXDE for hosts | Lightweight, cloud-init support, stable |
| 2024-12 | VyOS 1.4 for firewall | GPL license, native cloud-init, network-focused |
| 2024-12 | Dropdown connection UI | Consistent UX, easier multi-port selection |
| 2024-12 | Device categories | Avoid hardcoded lists, extensible |
| 2024-12 | Purple for linux_host | Avoid conflict with status green/red |
| 2024-12 | Orange for firewall | Avoid conflict with status green/red |
| 2024-12 | Pattern + explicit classification | Support both auto-detected and user-defined types |
| 2024-12 | Full reset function | Allow clean slate without lab restart |

---

## Success Metrics

| Metric | Target | Status |
|--------|--------|--------|
| vEOS creation | < 60 seconds | Achieved |
| Host creation | < 90 seconds | Achieved |
| Firewall creation | < 90 seconds | Achieved |
| noVNC load time | < 5 seconds | Needs testing |
| Creation success rate | 99% | Needs testing |
| Persistence across resets | 100% | Implemented |

---

## Risk Mitigations

| Risk | Mitigation | Status |
|------|------------|--------|
| noVNC instability | x11vnc via mgmt IP | Implemented |
| Cloud-init failures | Template validation, DNS fix | Implemented |
| VNC port conflicts | Dynamic port allocation | Implemented |
| Resource exhaustion | Strict limits (2 hosts, 1 FW) | Implemented |
| Orphaned bridges | Cleanup in restore/reset | Implemented |
| Partial failures | Transaction rollback | Implemented |
