# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the Arista Test Drive (ATD) public repository containing documentation, infrastructure configurations, and deployment automation for ATD training lab environments. ATD provides hands-on network training labs using Arista EOS devices (virtual and physical), CloudVision Portal (CVP), and containerized services.

For support: atd-help@arista.com

## Repository Structure

### Key Directories

- **topologies/** - Individual lab topology definitions (17+ topologies)
  - Each topology contains:
    - `topo_build.yml` - Network topology definition with node IPs, neighbors, CPU/RAM allocation
    - `configlets/` - EOS configuration snippets for devices (hundreds of configlets per topology)
    - `labguides/` - Sphinx-based lab documentation
    - `files/` - Topology-specific files (CVP info, menu options, app configs, scripts)
    - `ATD-INFRA` - Base infrastructure configuration template
    - `hosts` - Ansible inventory

- **nested-labvm/** - Nested lab VM services and Docker infrastructure
  - `atd-docker/` - Containerized services for ATD platform
  - `services/` - Systemd services for lab VM functionality
  - See detailed breakdown below

- **labvm/** - Base lab VM configuration

- **presenter/** - Presenter mode configurations

- **images/** - Topology diagrams and images

## Nested Lab VM Architecture

The nested lab VM runs multiple Docker containers that provide the ATD platform services:

### Docker Services (docker-compose.yml)

Core services defined in `nested-labvm/atd-docker/docker-compose.yml`:

- **uilanding** - Web UI landing page (port 80/443 via nginx)
- **labguides** & **labguides-v2** - Lab guide documentation servers
- **login** - User login and authentication
- **cvpupdater** - CVP configuration synchronization
- **gitconfigletsync** - Git-based configlet synchronization
- **conftopo** - Topology configuration manager
- **kvmbuilder** - KVM VM builder for network devices
- **ceosbuilder** - cEOS container builder
- **coder** - VS Code Server (code-server) IDE
- **jenkins** - CI/CD automation server
- **nginx** - Reverse proxy and SSL termination
- **monitor** - System monitoring and logging
- **syslog** - Centralized syslog server (port 1514)
- **tacacs** - TACACS+ authentication (port 49)
- **freeradius** - RADIUS authentication (ports 1812/1813)
- **ssh** - Web-based SSH interface
- **webui** - Firefox browser container
- **vtepinfo** - VXLAN VTEP information service
- **uptime** - System uptime monitoring
- **nodebuilder** - Dynamic node management service (port 8090)

### Nodebuilder Service (nested-labvm/atd-docker/nodebuilder/)

The nodebuilder service provides a REST API for dynamically adding VMs to running KVM-based labs. It runs on port 8090 with host network mode for libvirt access.

**Core Capabilities:**
- Add/edit/delete vEOS switches with ZTP via dnsmasq
- Add/delete Linux desktop hosts with noVNC access
- Add/edit/delete VyOS firewalls
- Cluster templates for multi-node deployments
- Restore user nodes after topology reboot
- Full reset to original topology

**Python Modules:**
- `nodebuilder_service.py` - REST API endpoints (aiohttp)
- `vm_manager.py` - VM lifecycle (create, start, stop, delete)
- `host_manager.py` - Linux host creation with cloud-init
- `firewall_manager.py` - VyOS firewall creation with cloud-init
- `resource_manager.py` - Bridge and interface cleanup
- `connection_manager.py` - OVS bridge management
- `interface_manager.py` - Port allocation and attachment
- `persistence.py` - YAML state management
- `validation.py` - Input validation and limits
- `novnc_manager.py` - VNC token management
- `cluster_templates.py` - Multi-node templates

**API Endpoints:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/available-ips` | List unused IPs from dnsmasq |
| GET | `/existing-nodes` | All topology nodes |
| GET | `/target-devices` | Connection targets with available ports |
| GET | `/user-nodes-status` | Status for restore button |
| POST | `/validate-node` | Pre-creation validation |
| POST | `/add-node` | Create vEOS VM |
| POST | `/edit-node` | Modify vEOS connections |
| POST | `/delete-node` | Remove vEOS node |
| POST | `/restore-user-nodes` | Start all user VMs after reboot |
| POST | `/reset-all-user-nodes` | Full reset to original topology |
| GET | `/cluster-templates` | Available cluster templates |
| POST | `/add-cluster` | Create node cluster |
| POST | `/save-config` | Save running config via eAPI |
| POST | `/reboot-devices` | Reboot VMs via virsh |
| GET | `/host-status` | Linux host count and availability |
| POST | `/add-host` | Create Linux desktop VM |
| POST | `/delete-host` | Remove Linux host |
| GET | `/novnc-token/{name}` | VNC access token |
| GET | `/firewall-status` | Firewall count and availability |
| POST | `/add-firewall` | Create VyOS firewall VM |
| POST | `/edit-firewall` | Change firewall interface IPs |
| POST | `/delete-firewall` | Remove firewall |

**Persistence Files:**
- `/home/arista/arista-dir/apps/nodebuilder/user_nodes.yaml` - User-added vEOS nodes
- `/home/arista/arista-dir/apps/nodebuilder/user_hosts.yaml` - User-added Linux hosts
- `/home/arista/arista-dir/apps/nodebuilder/user_firewalls.yaml` - User-added VyOS firewalls

**Base VM Images:**
- `ubuntu-desktop.qcow2` - Ubuntu with LXDE desktop for Linux hosts
- `vyos-1.4.qcow2` - VyOS Community Edition for firewalls
- Images stored in GCP bucket and cached locally

### Device Type Classification (uilanding/src/device_types.py)

Centralized device type system used by topology visualization and terminal page:

**Categories:**
- `provider` - External network (internet, isp)
- `core` - Backbone devices (core, dci, p, rr)
- `edge` - Edge/aggregation (borderleaf, pe, ce, gw, router, firewall)
- `fabric` - DC fabric (spine, leaf, memleaf)
- `endpoint` - End devices (host, linux_host, customer, oob)

**Classification Methods:**
- Pattern-based: Device type inferred from name (e.g., 'leaf1' → 'leaf')
- Explicit: Device type set via `device_type` field (for user-added nodes)

**Key Methods:**
- `DeviceTypeConfig.classify_device(name)` - Get type from name
- `DeviceTypeConfig.get_tier(type)` - Vertical position (0=top, 9=bottom)
- `DeviceTypeConfig.get_category(type)` - Category grouping
- `DeviceTypeConfig.is_user_defined(type)` - Check if explicit-only type

### LabVM Services (nested-labvm/services/)

Systemd services for topology lifecycle management:

- **unit-test/** - Comprehensive test suite for ATD labs
  - Tests: CVP connectivity, SSH to nodes, web services, inventory validation
  - Run: `cd nested-labvm/services/unit-test/src && python3 main.py`
  - Requires: `/etc/atd/UNIT_TEST_CONFIG.yaml`, `/etc/atd/ACCESS_INFO.yaml`

- **atdStartup/** - ATD initialization on boot
- **atdUpdate/** - ATD software updates
- **cvpStartup/** - CVP initialization
- **cvpUpdate/** - CVP updates
- **eosStartup/** - EOS device initialization
- **eosUpdate/** - EOS device updates

Service directory naming must match filenames in both script and `.service` files.

## Common Development Tasks

### Building Lab Guides

Lab guides use Sphinx with reStructuredText:

```bash
cd topologies/<topology-name>/labguides/

# Install dependencies
pip install sphinx sphinx_bootstrap_theme

# Build HTML documentation
make html

# Output: build/html/
```

### Running Unit Tests

```bash
cd nested-labvm/services/unit-test

# Install dependencies
pip3 install -r requirements.txt

# Copy config to /etc/atd/
sudo cp UNIT_TEST_CONFIG.yaml /etc/atd/

# Run all tests
cd src
python3 main.py

# Run individual test
python3 test_cvp_ssh.py
python3 test_node_ssh.py
python3 test_web.py
```

Tests validate:
- ATD configuration and topology files
- CVP SSH and API connectivity
- Web services (labguides)
- Device inventory in CVP
- SSH access to all topology nodes

### Building Docker Images

```bash
cd nested-labvm/atd-docker/

# Build specific service
docker build -t atddocker_<service>:1.0 <service>/.

# Example:
docker build -t atddocker_uilanding:1.0 uilanding/.
```

Most images are pulled from GCR: `us.gcr.io/atd-testdrivetraining-dev/atddocker_*`

### Running Docker Services

```bash
cd nested-labvm/atd-docker/

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f <service-name>

# Stop services
docker-compose down
```

Services require environment variables `$ArID`, `$ArGD`, `$AtID`, `$AtGD` for user/group IDs.

## Topology Configuration

### Topology Structure (topo_build.yml)

Each topology defines:
- Resource allocation: `host_cpu`, `cvp_cpu`, `cvp_nodes`, `cvp_ram`, `veos_cpu`
- Network nodes with IP addresses, MAC addresses, neighbor relationships
- Device naming follows patterns: A1/B1/C1, PE1/PE2, P3-P6, spine1-2, leaf1-4

### Configlets

Device configurations stored in `configlets/` directory:
- Base configs: `<device>-BASE`
- Lab-specific configs: `<device>-<LAB-TYPE>`
- Common configs: `VLANs`, IP routing, ISIS, OSPF, BGP, EVPN, VXLAN

Applied via CVP using `MenuOptions.yaml` mapping.

### Menu Options (files/MenuOptions.yaml)

Defines lab scenarios with:
- **options.{lab-name}** - Command and description for lab reset
- **labconfiglets.{lab-name}** - Device-to-configlet mappings

Common labs: reset, mlag, bgp, vxlan, l2evpn, l3evpn, cvp, media

Commands use `/usr/local/bin/ConfigureTopology.py -t <lab-type>`

## File Paths and Configuration

### Standard ATD File Locations

- `/etc/atd/ACCESS_INFO.yaml` - Lab access credentials (CVP, nodes, passwords)
- `/etc/atd/ATD_REPO.yaml` - Repository and branch information
- `/etc/atd/UNIT_TEST_CONFIG.yaml` - Unit test configuration
- `/opt/atd/topologies/` - Deployed topology files
- `/opt/labguides/web/` - Published lab guides
- `/home/arista/arista-dir/` - Arista user workspace
  - `apps/` - Application configs (coder, uilanding, syslog, tacacs)
  - `menus/` - Menu configurations
  - `cvp/` - CVP data

### ACCESS_INFO.yaml Structure

Contains credentials for:
- `login_info.cvp.shell` - CVP SSH (root and arista users)
- `login_info.jump_host.shell` - Lab VM SSH
- Passwords, IPs, usernames for all lab components

## Working with Topologies

### Topology Naming Convention

- `training-level{N}` - Training level N (1-7)
- `training-level{N}-exam` - Exam version
- `training-level{N}-v{X}` - Version X
- `training-level-x-cl` - Specialist certification labs
- Suffixes: `-veos` (vEOS), `-campus` (campus fabric), `-acsp` (ACSP cert), `-avd` (AVD)

### Adding New Topology

1. Create directory: `topologies/training-<name>/`
2. Add required files:
   - `topo_build.yml` - Topology definition
   - `configlets/` - Device configs
   - `files/MenuOptions.yaml` - Lab scenarios
   - `files/cvp/cvp_info.yaml` - CVP metadata
   - `labguides/` - Documentation
   - `ATD-INFRA` - Base config
   - `hosts` - Inventory

### CVP Integration

CVP (CloudVision Portal) manages device configurations:
- Configlets synced via `gitconfigletsync` service
- Device inventory validated by unit tests
- Streaming telemetry required (status: "active")
- API: REST API with token auth at `192.168.0.5:9910`

## Key Technologies

- **Arista EOS** - Network operating system (vEOS for virtual labs)
- **CVP** - CloudVision Portal for network management
- **Docker** - Container platform for services
- **Docker Compose** - Multi-container orchestration
- **Python 3** - Automation scripts, tests, services
- **Sphinx** - Documentation generation (reStructuredText)
- **YAML** - Configuration files
- **Ansible** - Configuration management (implied by .ansible.cfg)
- **libvirt/KVM** - Virtualization for network devices
- **cEOS** - Containerized EOS

## Authentication & Security

Multiple auth mechanisms supported:
- **TACACS+** - Device authentication (port 49)
- **RADIUS** - 802.1X and device auth (ports 1812/1813)
- **Local** - Fallback authentication
- **SSH Keys** - Passwordless access for arista user
- **CVP Tokens** - API authentication

## Important Patterns

### Configuration Hierarchy
1. Base infrastructure (`ATD-INFRA`) applied to all devices
2. Lab-specific configlets applied per scenario
3. User customizations preserved across resets

### Service Dependencies
- nginx depends on uilanding
- monitor depends on nginx
- Services mount shared volumes for coordination
- Network: All services on `atd_nginx` bridge network

### Volume Mounts
- `/etc/atd` - Read-only config access for most services
- `/opt/atd` - Topology data (read-only)
- `/home/arista/arista-dir` - Shared workspace (read-write)
- CVP data persisted in Docker volume `cvp_dir`

## Branch Strategy

Main branch for PRs: `nested-release`

**Feature Branches:**
- `feature/add-nodes` - Dynamic node management (vEOS, Linux hosts, VyOS firewalls)

## Exam Guard Feature

Recent commit (e89da99c) added exam session guard to prevent multiple tabs during exams. This indicates exam proctoring capabilities in the platform.
