# ATL Base Image Builders

This directory contains scripts and templates for building base VM images for the Add Host and Add Firewall features.

## Directory Structure

```
images/
├── README.md                           # This file
├── build-debian-lxde.sh               # Debian 12 LXDE build script
├── build-vyos.sh                      # VyOS 1.4 build script
└── cloud-init/
    ├── debian-host-template.yaml      # Cloud-init template for Linux hosts
    └── vyos-firewall-template.yaml    # Cloud-init template for VyOS firewalls
```

## Base Images

### Debian 12 LXDE (Linux Host)

A lightweight Linux desktop for end-host simulation in network training.

| Property | Value |
|----------|-------|
| OS | Debian 12 (Bookworm) |
| Desktop | LXDE + LightDM |
| Remote Access | x11vnc (port 5900) for noVNC |
| Provisioning | cloud-init (NoCloud) |
| Default User | arista / arista |
| Disk Size | 5GB |

**Pre-installed Tools:**
- Network: iperf3, tcpdump, mtr, traceroute, ping
- Browser: Firefox ESR
- Utilities: curl, wget, vim, nano

**Build the image:**
```bash
sudo ./build-debian-lxde.sh /var/lib/libvirt/images/hosts/base
```

Build time: 15-30 minutes (automated, unattended)

### VyOS 1.4 (Firewall)

A network operating system for firewall/router training.

| Property | Value |
|----------|-------|
| OS | VyOS 1.4 Rolling (Community) |
| Interfaces | 3 (mgmt, inside, outside) |
| Remote Access | SSH, Serial Console |
| Provisioning | cloud-init (native VyOS) |
| Default User | arista / arista |
| Disk Size | 5GB |

**Features:**
- Stateful firewall
- NAT (SNAT/DNAT)
- Routing (static, OSPF, BGP)
- VPN (IPsec, OpenVPN)
- Zone-based firewall

**Build the image:**
```bash
# Option 1: Download pre-built image (fastest)
sudo ./build-vyos.sh --download /var/lib/libvirt/images/firewall/base

# Option 2: Install from ISO (manual steps required)
sudo ./build-vyos.sh --install /var/lib/libvirt/images/firewall/base
```

## Cloud-init Templates

Templates are used by the nodebuilder service to configure VMs at first boot.

### Variables

**Debian Host:**
- `{hostname}` - VM hostname
- `{ip_address}` - Management IP (e.g., 192.168.0.50)
- `{gateway}` - Default gateway (192.168.0.1)
- `{password_hash}` - SHA-512 password hash

**VyOS Firewall:**
- `{hostname}` - VM hostname
- `{mgmt_ip}` - Management IP (e.g., 192.168.0.51)
- `{inside_ip}` - Inside interface IP with CIDR (e.g., 10.1.1.1/24)
- `{outside_ip}` - Outside interface IP with CIDR (e.g., 10.2.2.1/24)
- `{gateway}` - Default gateway (192.168.0.1)

### Generating Password Hash

```bash
# Generate SHA-512 hash for cloud-init
mkpasswd -m sha-512 "arista"
```

## Deployment

After building, copy images to the libvirt images directory:

```bash
# For Linux hosts
sudo mkdir -p /var/lib/libvirt/images/hosts/base
sudo cp debian-lxde-base.qcow2 /var/lib/libvirt/images/hosts/base/

# For VyOS firewalls
sudo mkdir -p /var/lib/libvirt/images/firewall/base
sudo cp vyos-base.qcow2 /var/lib/libvirt/images/firewall/base/
```

The nodebuilder service will:
1. Clone the base image for each new VM
2. Generate a cloud-init ISO from the template
3. Attach both to the VM
4. Boot the VM (cloud-init configures on first boot)

## Requirements

- libvirt/QEMU-KVM
- virt-install
- qemu-img
- genisoimage or mkisofs (for cloud-init ISOs)
- Internet access (for downloads)

Install on RHEL/CentOS:
```bash
sudo dnf install libvirt virt-install qemu-kvm qemu-img genisoimage
sudo systemctl enable --now libvirtd
```

## License

- Debian: Various open source licenses
- VyOS Community: GPL v2
