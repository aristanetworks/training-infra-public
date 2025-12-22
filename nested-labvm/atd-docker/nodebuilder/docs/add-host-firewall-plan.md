# Add Host and Add Firewall Implementation Plan

## Overview

This document outlines the implementation plan for two new features in the ATL (Arista Training Labs) platform:

1. **Add Host** - Allow users to add lightweight Linux desktop VMs
2. **Add Firewall** - Allow users to add a VyOS firewall VM

Both features extend the existing nodebuilder infrastructure and integrate with the terminal page for access.

---

## Feature Specifications

### Add Host (Linux Desktop)

| Specification | Value |
|---------------|-------|
| Operating System | Debian 12 (Bookworm) + LXDE |
| Resources | 1 vCPU, 1GB RAM, 5GB disk |
| Access Methods | noVNC (browser-based desktop), SSH |
| Max per Topology | 2 |
| Network | Single interface, connects to any switch |
| IP Assignment | From existing pool (same as add-node) |
| Provisioning | cloud-init (zero-touch setup) |
| Default User | arista / arista |
| Persistence | Survives lab reset and topology changes |
| Pre-installed Software | ping, traceroute, iperf3, tcpdump, mtr, Firefox ESR |

### Add Firewall (VyOS)

| Specification | Value |
|---------------|-------|
| Operating System | VyOS 1.4 Community Edition |
| Resources | 1 vCPU, 1GB RAM, 5GB disk |
| Access Methods | SSH, Serial Console |
| Max per Topology | 1 |
| Interfaces | 3 (management, inside, outside) |
| Management IP | From existing pool (same as add-node) |
| Inside/Outside IPs | User-configured (CIDR notation) |
| Default Firewall Rules | None (student configures) |
| Provisioning | cloud-init (native VyOS support) |
| Default User | arista / arista |
| Persistence | Survives lab reset and topology changes |

---

## Architecture

### System Integration

```
┌─────────────────────────────────────────────────────────────────┐
│                      UI Landing (Frontend)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ AddNode      │  │ AddHost      │  │ AddFirewall  │          │
│  │ Wizard       │  │ Wizard       │  │ Wizard       │          │
│  │ (existing)   │  │ (NEW)        │  │ (NEW)        │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Nodebuilder Service (Backend - Port 8090)           │
│  New Endpoints:                                                  │
│  - POST /add-host, /edit-host, /delete-host                     │
│  - POST /add-firewall, /edit-firewall, /delete-firewall         │
│  - GET  /novnc-token/{hostname}                                 │
│                                                                  │
│  New Modules:                                                    │
│  - host_manager.py      (Linux host lifecycle)                  │
│  - firewall_manager.py  (VyOS firewall lifecycle)               │
│  - novnc_manager.py     (noVNC token management)                │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     New Services                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  novncproxy Service (Port 6080)                          │  │
│  │  - WebSocket proxy for VNC connections                    │  │
│  │  - Token-based authentication                             │  │
│  │  - Integrates with terminal page                          │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Infrastructure Layer                          │
│  - libvirt/KVM for VM management                                │
│  - OVS bridges for network connectivity                         │
│  - Base images: debian-lxde.qcow2, vyos-1.4.qcow2              │
│  - cloud-init ISOs for provisioning                             │
└─────────────────────────────────────────────────────────────────┘
```

### noVNC Desktop Access Flow

```
Browser                     novncproxy                  Linux Host VM
  │                              │                            │
  │  1. Request token            │                            │
  ├─────────────────────────────>│                            │
  │  2. Return token + URL       │                            │
  │<─────────────────────────────┤                            │
  │                              │                            │
  │  3. WebSocket + token        │                            │
  ├─────────────────────────────>│                            │
  │                              │  4. VNC connection         │
  │                              ├───────────────────────────>│
  │  5. Proxied VNC stream       │                            │
  │<─────────────────────────────┤<───────────────────────────┤
```

### Network Connectivity

**Linux Host (1 interface)**
```
Linux Host (eth1) ──> OVS Bridge ──> Switch (EthernetX)
```

**VyOS Firewall (3 interfaces)**
```
VyOS (eth0/mgmt)   ──> vmgmt bridge (management network)
VyOS (eth1/inside) ──> OVS Bridge ──> Inside Switch
VyOS (eth2/outside)──> OVS Bridge ──> Outside Switch
```

---

## UI Wizard Flows

### Add Host Wizard (4 Steps)

1. **Name** - Enter hostname, validate uniqueness, show limit (0/2)
2. **IP Address** - Select from available pool
3. **Network Connection** - Choose switch and port (optional)
4. **Review** - Confirm and create

### Add Firewall Wizard (5 Steps)

1. **Name** - Enter hostname, validate uniqueness, show limit (0/1)
2. **Management IP** - Select from available pool
3. **Inside Interface** - Enter IP (CIDR), select switch/port
4. **Outside Interface** - Enter IP (CIDR), select switch/port
5. **Review** - Confirm and create

---

## Cloud-init Templates

### Debian LXDE Host

```yaml
#cloud-config
hostname: {hostname}

users:
  - name: arista
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    passwd: {hashed_password}

network:
  version: 2
  ethernets:
    eth0:
      addresses: [{ip_address}/24]
      gateway4: 192.168.0.1
      nameservers:
        addresses: [8.8.8.8, 192.168.0.1]

packages:
  - iperf3
  - tcpdump
  - mtr
  - firefox-esr

runcmd:
  - systemctl enable lightdm
  - systemctl start lightdm
  # Enable VNC server for noVNC access
  - systemctl enable x11vnc
  - systemctl start x11vnc
```

### VyOS Firewall

```yaml
#cloud-config
vyos_config_commands:
  - set system host-name {hostname}
  - set interfaces ethernet eth0 address {mgmt_ip}/24
  - set interfaces ethernet eth0 description 'Management'
  - set interfaces ethernet eth1 address {in_ip}
  - set interfaces ethernet eth1 description 'Inside'
  - set interfaces ethernet eth2 address {out_ip}
  - set interfaces ethernet eth2 description 'Outside'
  - set service ssh port 22
  - set system login user arista authentication plaintext-password arista
  - set system login user arista level admin
```

---

## API Endpoints

### Host Endpoints

```
POST /add-host
{
  "name": "desktop1",
  "ip": "192.168.0.50",
  "connections": [{"target_device": "leaf1", "target_port": "Ethernet5"}]
}

POST /edit-host
{
  "name": "desktop1",
  "add_connections": [...],
  "remove_connections": [...]
}

POST /delete-host
{"name": "desktop1"}

GET /novnc-token/{hostname}
Returns: {"token": "...", "vnc_port": 5900, "websocket_url": "..."}
```

### Firewall Endpoints

```
POST /add-firewall
{
  "name": "fw1",
  "mgmt_ip": "192.168.0.51",
  "in_interface": {"ip": "10.1.1.1/24", "target_device": "leaf1", "target_port": "Ethernet6"},
  "out_interface": {"ip": "10.2.2.1/24", "target_device": "spine1", "target_port": "Ethernet7"}
}

POST /edit-firewall
{
  "name": "fw1",
  "in_interface": {"ip": "10.1.1.2/24"},
  "out_interface": {"ip": "10.2.2.2/24"}
}

POST /delete-firewall
{"name": "fw1"}
```

---

## Implementation Phases

### Phase 1: Infrastructure Preparation (Week 1)

**Tasks:**
1. Create automated base image build scripts (Packer/virt-install)
   - Debian 12 + LXDE + cloud-init + VNC server
   - VyOS 1.4 Community Edition
2. Set up novncproxy Docker service
3. Update docker-compose.yml
4. Test base images manually

**Deliverables:**
- `build-debian-lxde.sh` - Automated Debian image build
- `build-vyos.sh` - Automated VyOS image build
- `novncproxy/` - Docker service for noVNC proxy
- Updated `docker-compose.yml`

### Phase 2-3: Backend API (Weeks 2-3)

**Tasks:**
1. Create `host_manager.py` module
2. Create `firewall_manager.py` module
3. Create `novnc_manager.py` module
4. Extend `persistence.py` for new node types
5. Extend `validation.py` for limits and IP validation
6. Implement REST API endpoints
7. Unit tests

**Deliverables:**
- New Python modules in nodebuilder
- API endpoints for host/firewall CRUD
- Test coverage

### Phase 4: Frontend UI (Week 4)

**Tasks:**
1. Create `add-host-wizard.js` (extends BaseModal)
2. Create `add-firewall-wizard.js` (extends BaseModal)
3. Extend topology manager for new node types
4. Extend terminal manager for noVNC support
5. Add menu items and context menu options
6. CSS styling for new node types

**Deliverables:**
- Add Host wizard (4 steps)
- Add Firewall wizard (5 steps)
- Topology diagram integration
- noVNC terminal integration

### Phase 5: Testing & Documentation (Week 5)

**Tasks:**
1. End-to-end integration testing
2. Performance testing
3. Edge case testing
4. User documentation
5. Code review and cleanup

**Deliverables:**
- Test results
- User guides
- Updated CLAUDE.md

### Phase 6: Deployment (Week 6)

**Tasks:**
1. Build and push Docker images
2. Deploy base images to lab VMs
3. Production deployment
4. Monitoring setup
5. User acceptance testing

**Deliverables:**
- Production deployment
- Monitoring dashboards
- Sign-off

---

## File Structure

```
nodebuilder/
├── src/
│   ├── host_manager.py      (NEW)
│   ├── firewall_manager.py  (NEW)
│   ├── novnc_manager.py     (NEW)
│   ├── persistence.py       (EXTEND)
│   ├── validation.py        (EXTEND)
│   ├── config.py            (EXTEND)
│   └── main.py              (EXTEND with new endpoints)
├── images/
│   ├── build-debian-lxde.sh (NEW)
│   ├── build-vyos.sh        (NEW)
│   └── cloud-init/          (NEW)
│       ├── debian-template.yaml
│       └── vyos-template.yaml
└── docs/
    └── add-host-firewall-plan.md (THIS FILE)

uilanding/
├── src/html/js/
│   ├── add-host-wizard.js    (NEW)
│   ├── add-firewall-wizard.js (NEW)
│   ├── topology-manager.js   (EXTEND)
│   └── terminal-manager.js   (EXTEND)
└── src/html/css/
    └── wizards.css           (EXTEND)

novncproxy/
├── Dockerfile               (NEW)
├── src/
│   └── novnc_service.py     (NEW)
└── requirements.txt         (NEW)
```

---

## Risk Mitigations

| Risk | Mitigation |
|------|------------|
| noVNC WebSocket instability | Use battle-tested websockify, add reconnection logic |
| Cloud-init provisioning fails | Test templates thoroughly, add health checks |
| VNC port conflicts | Static port assignment (5900-5901) |
| Resource exhaustion | Strict limits (2 hosts, 1 firewall) |
| VyOS bugs | Use stable rolling release, document known issues |

---

## Success Metrics

- Host creation < 60 seconds
- Firewall creation < 90 seconds
- noVNC loads < 5 seconds
- 99% creation success rate
- 100% persistence across resets

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2024-XX-XX | Debian 12 + LXDE | Lightweight, stable, cloud-init support |
| 2024-XX-XX | VyOS Community | GPL license, native cloud-init, network-focused |
| 2024-XX-XX | Automated image builds | Reproducible, maintainable |
| 2024-XX-XX | No default firewall rules | Training focus - students configure |
| 2024-XX-XX | 1GB RAM limit | Keep lightweight, monitor if issues |
| 2024-XX-XX | Persistent across topologies | User expectation, consistent with vEOS nodes |
