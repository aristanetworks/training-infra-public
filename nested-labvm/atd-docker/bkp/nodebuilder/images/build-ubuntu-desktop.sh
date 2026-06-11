#!/bin/bash
#
# Download and Prepare Ubuntu Desktop Image for ATL Linux Hosts
#
# This script downloads the official Ubuntu Cloud image and prepares it
# for use as a Linux desktop host with LXDE and VNC.
#
# Ubuntu Cloud images come with cloud-init pre-installed, making setup trivial.
# Desktop packages (LXDE, x11vnc) are installed on first boot via cloud-init.
#
# Requirements:
# - wget or curl
# - qemu-img
#
# Usage: ./build-ubuntu-desktop.sh
#
# Output: ubuntu-desktop-base.qcow2

set -e

# Configuration
IMAGE_NAME="ubuntu-desktop-base"
OUTPUT_DIR="/var/lib/libvirt/images/hosts/base"
# Disk size for the final image (cloud images are small, we expand them)
DISK_SIZE="10G"

# Ubuntu 22.04 LTS (Jammy) cloud image - stable and well-supported
UBUNTU_VERSION="jammy"
UBUNTU_IMAGE_URL="https://cloud-images.ubuntu.com/${UBUNTU_VERSION}/current/${UBUNTU_VERSION}-server-cloudimg-amd64.img"

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

    command -v qemu-img >/dev/null 2>&1 || missing+=("qemu-img")

    if [ ${#missing[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        log_error "Install with: sudo dnf install qemu-img"
        exit 1
    fi

    # Check for download tool
    if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
        log_error "Neither wget nor curl available"
        exit 1
    fi

    log_info "All prerequisites met"
}

# Download Ubuntu cloud image
download_image() {
    local download_path="${OUTPUT_DIR}/ubuntu-cloud.img"

    mkdir -p "$OUTPUT_DIR"

    if [ -f "$download_path" ]; then
        log_info "Ubuntu cloud image already exists: $download_path"
        log_info "Delete it to re-download"
        return 0
    fi

    log_info "Downloading Ubuntu ${UBUNTU_VERSION} cloud image..."
    log_info "URL: $UBUNTU_IMAGE_URL"
    log_info "This may take a few minutes..."

    if command -v wget >/dev/null 2>&1; then
        wget --progress=bar:force -O "$download_path" "$UBUNTU_IMAGE_URL"
    else
        curl -L --progress-bar -o "$download_path" "$UBUNTU_IMAGE_URL"
    fi

    log_info "Download complete: $download_path"
}

# Prepare the base image
prepare_image() {
    local download_path="${OUTPUT_DIR}/ubuntu-cloud.img"
    local final_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"

    # Remove existing final image
    if [ -f "$final_path" ]; then
        log_warn "Removing existing image: $final_path"
        rm -f "$final_path"
    fi

    log_info "Creating base image from cloud image..."

    # Convert and resize the image
    # Ubuntu cloud images are already qcow2, but we want to resize
    log_info "Converting and resizing to ${DISK_SIZE}..."
    qemu-img convert -O qcow2 "$download_path" "$final_path"
    qemu-img resize "$final_path" "$DISK_SIZE"

    # Get final size
    local size=$(du -h "$final_path" | cut -f1)
    log_info "Base image created: $final_path ($size)"
}

# Create the cloud-init template for desktop setup
create_cloud_init_template() {
    local template_path="${OUTPUT_DIR}/../cloud-init/ubuntu-desktop-template.yaml"

    log_info "Creating cloud-init template for desktop setup..."

    mkdir -p "$(dirname "$template_path")"

    cat > "$template_path" << 'TEMPLATE_EOF'
#cloud-config
# Ubuntu Desktop Host Cloud-Init Template
#
# Variables replaced at runtime:
#   {hostname}  - VM hostname
#   {username}  - Login username (default: arista)
#   {password}  - Login password
#   {mgmt_ip}   - Management IP address
#   {gateway}   - Default gateway
#   {data_ip}   - Data interface IP (optional)

# Set hostname
hostname: {hostname}
fqdn: {hostname}.atl.local
manage_etc_hosts: true

# Create user
users:
  - name: {username}
    groups: [sudo, adm, audio, video, plugdev, netdev]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false
    # Password set via chpasswd below

# Set password (cloud-init hashed format)
chpasswd:
  expire: false
  list:
    - {username}:{password}

# Install desktop packages on first boot
package_update: true
package_upgrade: false
packages:
  - lxde
  - lightdm
  - x11vnc
  - firefox
  - net-tools
  - iputils-ping
  - traceroute
  - mtr
  - tcpdump
  - iperf3
  - curl
  - wget
  - vim
  - nano

# Configure network (eth0 = management)
write_files:
  # x11vnc systemd service
  - path: /etc/systemd/system/x11vnc.service
    content: |
      [Unit]
      Description=x11vnc VNC Server
      After=lightdm.service
      Requires=lightdm.service

      [Service]
      Type=simple
      ExecStart=/usr/bin/x11vnc -display :0 -auth guess -forever -loop -noxdamage -repeat -rfbport 5900 -shared -nopw
      Restart=on-failure
      RestartSec=3

      [Install]
      WantedBy=multi-user.target
    permissions: '0644'

  # LightDM autologin configuration
  - path: /etc/lightdm/lightdm.conf.d/50-autologin.conf
    content: |
      [Seat:*]
      autologin-user={username}
      autologin-user-timeout=0
    permissions: '0644'

# Run commands after boot
runcmd:
  # Enable and start services
  - systemctl enable x11vnc
  - systemctl set-default graphical.target
  # Disable screen blanking
  - mkdir -p /home/{username}/.config/lxsession/LXDE
  - echo '@xset s off' >> /home/{username}/.config/lxsession/LXDE/autostart
  - echo '@xset -dpms' >> /home/{username}/.config/lxsession/LXDE/autostart
  - echo '@xset s noblank' >> /home/{username}/.config/lxsession/LXDE/autostart
  - chown -R {username}:{username} /home/{username}/.config
  # Signal completion
  - touch /var/lib/cloud/instance/desktop-setup-complete

# Power state - reboot after setup to start desktop
power_state:
  mode: reboot
  message: "Rebooting to start desktop environment"
  timeout: 30
  condition: true
TEMPLATE_EOF

    log_info "Template created: $template_path"
}

# Main
main() {
    log_info "=== Ubuntu Desktop Base Image Builder ==="
    log_info "Output directory: $OUTPUT_DIR"
    log_info ""
    log_info "This creates a base image from Ubuntu Cloud images."
    log_info "Desktop packages are installed on first boot via cloud-init."
    log_info ""

    check_prerequisites
    download_image
    prepare_image
    create_cloud_init_template

    log_info ""
    log_info "=== Build Complete ==="
    log_info "Base image: ${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    log_info "Template: ${OUTPUT_DIR}/../cloud-init/ubuntu-desktop-template.yaml"
    log_info ""
    log_info "The image is ready to use. On first boot:"
    log_info "  1. Cloud-init installs LXDE, x11vnc, and tools"
    log_info "  2. System reboots to start the desktop"
    log_info "  3. VNC available on port 5900"
    log_info ""
    log_info "First boot takes 5-10 minutes for package installation."
}

main "$@"
