#!/bin/bash
#
# Build Debian 12 LXDE Base Image for ATL Linux Hosts
#
# This script creates a ready-to-use qcow2 image with:
# - Debian 12 (Bookworm) minimal install
# - LXDE desktop environment
# - LightDM display manager
# - x11vnc server for noVNC access
# - cloud-init for zero-touch provisioning
# - Network tools (iperf3, tcpdump, mtr, traceroute)
# - Firefox ESR browser
#
# Requirements:
# - virt-install (libvirt)
# - QEMU/KVM
# - Root or libvirt group membership
# - Internet access (for package downloads)
#
# Usage: ./build-debian-lxde.sh [output_path]
#
# Output: debian-lxde-base.qcow2 (approximately 2-3GB)

set -e

# Configuration
IMAGE_NAME="debian-lxde-base"
IMAGE_SIZE="5G"
RAM="2048"
VCPUS="2"
DEBIAN_VERSION="bookworm"
OUTPUT_DIR="/var/lib/libvirt/images/hosts/base"
# Network for installation
# Use virbr0 (default libvirt NAT) for installation to avoid CVP DHCP ZTP options
# The vmgmt DHCP server provides EOS ZTP options that confuse the Debian installer
# After installation, the VM will be configured for vmgmt via cloud-init
INSTALL_NETWORK="${INSTALL_NETWORK:-default}"
# Network bridge for final operation (used by nodebuilder when cloning)
NETWORK_BRIDGE="${NETWORK_BRIDGE:-vmgmt}"

# URLs - Use network install tree for virt-install --location
# The netinst ISO doesn't work with --location, must use install tree URL
DEBIAN_INSTALL_URL="https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    local missing=()

    command -v virt-install >/dev/null 2>&1 || missing+=("virt-install")
    command -v qemu-img >/dev/null 2>&1 || missing+=("qemu-img")
    command -v virsh >/dev/null 2>&1 || missing+=("virsh")

    if [ ${#missing[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        log_error "Install with: sudo dnf install libvirt virt-install qemu-kvm"
        exit 1
    fi

    # Check if libvirt is running
    if ! systemctl is-active --quiet libvirtd 2>/dev/null; then
        log_error "libvirtd is not running. Start with: sudo systemctl start libvirtd"
        exit 1
    fi

    # Check if libvirt network exists and is active
    if ! virsh net-info "$INSTALL_NETWORK" >/dev/null 2>&1; then
        log_warn "Libvirt network '$INSTALL_NETWORK' not found"
        log_info "Trying to start default network..."
        virsh net-start default 2>/dev/null || true
        if ! virsh net-info "$INSTALL_NETWORK" >/dev/null 2>&1; then
            log_error "Cannot use libvirt network '$INSTALL_NETWORK'"
            log_error "Available networks:"
            virsh net-list --all 2>/dev/null | tail -n +3
            log_error "Set INSTALL_NETWORK environment variable or create the 'default' network"
            exit 1
        fi
    fi

    log_info "Using libvirt network: $INSTALL_NETWORK (avoids CVP DHCP ZTP options)"
    log_info "All prerequisites met"
}

# Verify network connectivity to Debian mirror
check_network() {
    log_info "Checking network connectivity to Debian mirror..."

    if ! curl -s --head --connect-timeout 10 "$DEBIAN_INSTALL_URL" >/dev/null 2>&1; then
        log_error "Cannot reach Debian mirror at $DEBIAN_INSTALL_URL"
        log_error "Network access is required for installation"
        exit 1
    fi

    log_info "Network connectivity verified"
}

# Create preseed file for automated installation
create_preseed() {
    local preseed_path="${OUTPUT_DIR}/preseed.cfg"

    log_info "Creating preseed configuration..."

    cat > "$preseed_path" << 'PRESEED_EOF'
# Debian 12 Preseed for ATL Linux Host Base Image

# Locale and keyboard - set early to avoid prompts
d-i debian-installer/locale string en_US.UTF-8
d-i debian-installer/language string en
d-i debian-installer/country string US
d-i localechooser/supported-locales multiselect en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i console-setup/ask_detect boolean false

# Network (DHCP during install, cloud-init configures later)
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string debian-host
d-i netcfg/get_domain string atl.local
d-i netcfg/hostname string debian-host

# Mirror
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

# Clock and time
d-i clock-setup/utc boolean true
d-i time/zone string UTC
d-i clock-setup/ntp boolean true

# Partitioning - single partition, use entire disk
d-i partman-auto/disk string /dev/vda
d-i partman-auto/method string regular
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-lvm/confirm boolean true
d-i partman-lvm/confirm_nooverwrite boolean true
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

# Root account disabled, use sudo
d-i passwd/root-login boolean false

# Create arista user
d-i passwd/user-fullname string Arista User
d-i passwd/username string arista
# Password: arista (hashed with SHA-512)
d-i passwd/user-password-crypted string $6$rounds=4096$WnWOj6g4$XHr6QxQl5KYz0RJbHYrHwNBJ8VVFBq4qS1VbHVxG5/kVKgXUMq3EVqMnGo5.xyy7TpOKJz5xzVHhJQJjQJhGv0

# Package selection
d-i tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string lxde lightdm x11vnc cloud-init cloud-utils iperf3 tcpdump mtr traceroute firefox-esr net-tools iputils-ping dnsutils curl wget sudo openssh-server vim nano less
d-i pkgsel/upgrade select full-upgrade

# Disable popularity contest
d-i popularity-contest/participate boolean false

# Boot loader
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string /dev/vda
d-i grub-installer/with_other_os boolean false

# Skip some final questions
d-i finish-install/keep-consoles boolean true
d-i finish-install/reboot_in_progress note
d-i cdrom-detect/eject boolean true

# Late command to enable cloud-init and configure system
# Keep it simple - just enable cloud-init and set up sudoers
# Additional config (x11vnc, autologin) will be done via cloud-init or post-boot
d-i preseed/late_command string \
    in-target systemctl enable cloud-init cloud-init-local cloud-config cloud-final; \
    echo 'arista ALL=(ALL) NOPASSWD:ALL' > /target/etc/sudoers.d/arista; \
    chmod 440 /target/etc/sudoers.d/arista; \
    mkdir -p /target/etc/cloud/cloud.cfg.d; \
    echo 'datasource_list: [ NoCloud, None ]' > /target/etc/cloud/cloud.cfg.d/99-atl-datasource.cfg
PRESEED_EOF

    log_info "Preseed created: $preseed_path"
}

# Create post-install script for additional configuration
create_post_install() {
    local script_path="${OUTPUT_DIR}/post-install.sh"

    log_info "Creating post-install script..."

    cat > "$script_path" << 'POST_EOF'
#!/bin/bash
# Post-installation configuration for ATL Linux Host

# Enable cloud-init
systemctl enable cloud-init
systemctl enable cloud-init-local
systemctl enable cloud-config
systemctl enable cloud-final

# Configure x11vnc to start with LightDM
cat > /etc/systemd/system/x11vnc.service << 'EOF'
[Unit]
Description=x11vnc VNC Server for noVNC
After=lightdm.service
Requires=lightdm.service

[Service]
Type=simple
ExecStart=/usr/bin/x11vnc -display :0 -auth guess -forever -loop -noxdamage -repeat -rfbport 5900 -shared
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl enable x11vnc

# Configure LightDM for auto-login (will be overridden by cloud-init)
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-autologin.conf << 'EOF'
[Seat:*]
autologin-user=arista
autologin-user-timeout=0
EOF

# Ensure arista has sudo without password
echo 'arista ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/arista
chmod 440 /etc/sudoers.d/arista

# Configure cloud-init datasource (NoCloud for ISO-based config)
cat > /etc/cloud/cloud.cfg.d/99-atl-datasource.cfg << 'EOF'
datasource_list: [ NoCloud, None ]
EOF

# Clean up for smaller image
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -rf /tmp/*

echo "Post-install configuration complete"
POST_EOF

    chmod +x "$script_path"
    log_info "Post-install script created: $script_path"
}

# Build the base image using virt-install
build_image() {
    local disk_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    local preseed_path="${OUTPUT_DIR}/preseed.cfg"

    # Remove existing image if present
    if [ -f "$disk_path" ]; then
        log_warn "Removing existing image: $disk_path"
        rm -f "$disk_path"
    fi

    # Remove existing VM if defined
    if virsh dominfo "$IMAGE_NAME" >/dev/null 2>&1; then
        log_warn "Removing existing VM definition: $IMAGE_NAME"
        virsh destroy "$IMAGE_NAME" 2>/dev/null || true
        virsh undefine "$IMAGE_NAME" 2>/dev/null || true
    fi

    log_info "Creating disk image: $disk_path"
    qemu-img create -f qcow2 "$disk_path" "$IMAGE_SIZE"

    log_info "Starting automated network installation (this will take 15-30 minutes)..."
    log_info "Using install tree: $DEBIAN_INSTALL_URL"

    # Run with console attached so we can see install progress/errors
    # Use --wait -1 to wait for completion
    log_info "Starting virt-install (console output will be shown)..."
    log_info "Press Ctrl+] to detach from console if needed"

    virt-install \
        --name "$IMAGE_NAME" \
        --ram "$RAM" \
        --vcpus "$VCPUS" \
        --disk path="$disk_path",format=qcow2 \
        --os-variant debian12 \
        --network network="$INSTALL_NETWORK",model=virtio \
        --graphics none \
        --console pty,target_type=serial \
        --location "$DEBIAN_INSTALL_URL" \
        --initrd-inject="$preseed_path" \
        --extra-args "auto=true priority=critical preseed/file=/preseed.cfg locale=en_US console=ttyS0,115200n8" \
        --wait -1

    log_info "Installation complete"

    # Clean up VM definition (we only need the disk image)
    virsh undefine "$IMAGE_NAME" 2>/dev/null || true

    log_info "Base image created: $disk_path"
}

# Compress and finalize the image
finalize_image() {
    local disk_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    local final_path="${OUTPUT_DIR}/${IMAGE_NAME}-final.qcow2"

    log_info "Compressing image..."

    # Use qemu-img to compress
    qemu-img convert -O qcow2 -c "$disk_path" "$final_path"

    # Replace original with compressed version
    mv "$final_path" "$disk_path"

    # Get final size
    local size=$(du -h "$disk_path" | cut -f1)
    log_info "Final image size: $size"
    log_info "Image ready: $disk_path"
}

# Main execution
main() {
    log_info "=== ATL Debian LXDE Base Image Builder ==="
    log_info "Output directory: $OUTPUT_DIR"

    check_prerequisites
    check_network

    mkdir -p "$OUTPUT_DIR"

    create_preseed
    create_post_install
    build_image
    finalize_image

    log_info "=== Build Complete ==="
    log_info "Base image: ${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    log_info ""
    log_info "To use this image:"
    log_info "  1. Copy to /var/lib/libvirt/images/hosts/base/"
    log_info "  2. The nodebuilder service will clone it for each new host"
    log_info "  3. Cloud-init configures hostname, IP, and user at boot"
}

# Run main function
main "$@"
