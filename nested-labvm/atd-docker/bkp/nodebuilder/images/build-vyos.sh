#!/bin/bash
#
# Build/Download VyOS 1.4 Base Image for ATL Firewall
#
# This script downloads and prepares the VyOS rolling release cloud image.
# VyOS Community Edition is distributed as pre-built qcow2 images with
# native cloud-init support.
#
# The image provides:
# - VyOS 1.4 (rolling release, stable snapshots)
# - Native cloud-init support for zero-touch provisioning
# - Full firewall/router capabilities
# - SSH and serial console access
#
# Requirements:
# - wget or curl
# - qemu-img (for image verification/conversion)
# - Internet access
#
# Usage: ./build-vyos.sh [output_path]
#
# Output: vyos-base.qcow2 (approximately 300-400MB)

set -e

# Configuration
IMAGE_NAME="vyos-base"
# VyOS 1.4 (sagitta) rolling release - stable community builds
# Using the generic (non-cloud) image which works with NoCloud datasource
VYOS_VERSION="1.4-rolling"
OUTPUT_DIR="/var/lib/libvirt/images/firewall/base"
# Network bridge for installation - vmgmt is the ATD management bridge
# Only used for --install method, not for --download
NETWORK_BRIDGE="${NETWORK_BRIDGE:-vmgmt}"

# VyOS download URLs
# Official VyOS nightly builds from GitHub releases
# Note: VyOS only provides ISOs for free - qcow2 requires subscription
# Updated URL from: https://github.com/vyos/vyos-nightly-build/releases
VYOS_IMAGE_URL="https://github.com/vyos/vyos-nightly-build/releases/download/2025.11.01-0021-rolling/vyos-2025.11.01-0021-rolling-generic-amd64.iso"
VYOS_ISO_NAME="vyos-rolling.iso"

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
    command -v virsh >/dev/null 2>&1 || missing+=("virsh")
    command -v virt-install >/dev/null 2>&1 || missing+=("virt-install")

    if [ ${#missing[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        log_error "Install with: sudo dnf install libvirt virt-install qemu-kvm"
        exit 1
    fi

    # Check for download tool
    if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
        log_error "Neither wget nor curl available"
        exit 1
    fi

    # Check if libvirt is running
    if ! systemctl is-active --quiet libvirtd 2>/dev/null; then
        log_error "libvirtd is not running. Start with: sudo systemctl start libvirtd"
        exit 1
    fi

    log_info "All prerequisites met"
}

# Check network bridge (only needed for install method)
check_network_bridge() {
    if ! ip link show "$NETWORK_BRIDGE" >/dev/null 2>&1; then
        log_error "Network bridge '$NETWORK_BRIDGE' not found"
        log_error "Available bridges:"
        ip link show type bridge 2>/dev/null | grep -E "^[0-9]+:" | awk '{print "  " $2}' | tr -d ':'
        log_error "Set NETWORK_BRIDGE environment variable to use a different bridge"
        exit 1
    fi
    log_info "Using network bridge: $NETWORK_BRIDGE"
}

# Download VyOS ISO
download_vyos_iso() {
    local iso_path="${OUTPUT_DIR}/${VYOS_ISO_NAME}"

    if [ -f "$iso_path" ]; then
        log_info "VyOS ISO already exists: $iso_path"
        return 0
    fi

    log_info "Downloading VyOS ${VYOS_VERSION} ISO..."
    log_info "This may take a few minutes depending on connection speed..."

    mkdir -p "$OUTPUT_DIR"

    if command -v wget >/dev/null 2>&1; then
        wget --progress=bar:force -O "$iso_path" "$VYOS_IMAGE_URL"
    else
        curl -L --progress-bar -o "$iso_path" "$VYOS_IMAGE_URL"
    fi

    log_info "VyOS ISO downloaded: $iso_path"
}

# Create seed ISO for VyOS installation automation
create_seed_config() {
    local seed_dir="${OUTPUT_DIR}/seed"
    local seed_iso="${OUTPUT_DIR}/vyos-seed.iso"

    log_info "Creating seed configuration for automated install..."

    mkdir -p "$seed_dir"

    # Create cloud-init meta-data
    cat > "${seed_dir}/meta-data" << 'EOF'
instance-id: vyos-base-install
local-hostname: vyos
EOF

    # Create cloud-init user-data for initial install
    # This configures basic settings during image creation
    cat > "${seed_dir}/user-data" << 'EOF'
#cloud-config
vyos_config_commands:
  - set system host-name vyos-base
  - set system time-zone UTC
  - set service ssh port 22
  - set system login user vyos authentication plaintext-password vyos
  - set system login user vyos level admin
  # Enable serial console for virsh console access
  - set system console device ttyS0 speed 115200
EOF

    # Create the seed ISO using genisoimage or mkisofs
    if command -v genisoimage >/dev/null 2>&1; then
        genisoimage -output "$seed_iso" -volid cidata -joliet -rock "${seed_dir}/"
    elif command -v mkisofs >/dev/null 2>&1; then
        mkisofs -output "$seed_iso" -volid cidata -joliet -rock "${seed_dir}/"
    else
        log_warn "Neither genisoimage nor mkisofs available"
        log_warn "Install with: sudo dnf install genisoimage"
        log_warn "Continuing without seed ISO - manual configuration required"
        return 1
    fi

    log_info "Seed ISO created: $seed_iso"
    return 0
}

# Install VyOS to a qcow2 image
install_vyos() {
    local iso_path="${OUTPUT_DIR}/${VYOS_ISO_NAME}"
    local disk_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    local seed_iso="${OUTPUT_DIR}/vyos-seed.iso"

    # Remove existing image if present
    if [ -f "$disk_path" ]; then
        log_warn "Removing existing image: $disk_path"
        rm -f "$disk_path"
    fi

    # Remove existing VM if defined
    if virsh dominfo "${IMAGE_NAME}-install" >/dev/null 2>&1; then
        log_warn "Removing existing VM definition"
        virsh destroy "${IMAGE_NAME}-install" 2>/dev/null || true
        virsh undefine "${IMAGE_NAME}-install" 2>/dev/null || true
    fi

    log_info "Creating disk image: $disk_path (5G)"
    qemu-img create -f qcow2 "$disk_path" 5G

    log_info "Starting VyOS installation VM..."
    log_info ""
    log_info "=========================================="
    log_info "MANUAL STEPS REQUIRED:"
    log_info "=========================================="
    log_info "1. The VM will boot to VyOS live mode"
    log_info "2. Login as 'vyos' with password 'vyos'"
    log_info "3. Run: install image"
    log_info "4. Follow prompts:"
    log_info "   - Would you like to continue? (Yes)"
    log_info "   - Partition: Auto"
    log_info "   - Install to: vda"
    log_info "   - Continue? Yes"
    log_info "   - Image name: (accept default)"
    log_info "   - Copy current config: Yes"
    log_info "   - Set password for vyos user: arista"
    log_info "5. After install completes: poweroff"
    log_info "=========================================="
    log_info ""
    log_info "Press Enter to start the VM, or Ctrl+C to abort..."
    read -r

    # Start installation VM
    local disk_opts="path=${disk_path},format=qcow2,bus=virtio"
    local cdrom_opts="path=${iso_path},device=cdrom"

    virt-install \
        --name "${IMAGE_NAME}-install" \
        --ram 1024 \
        --vcpus 1 \
        --disk "$disk_opts" \
        --disk "$cdrom_opts" \
        --osinfo detect=on,name=linux2022 \
        --network bridge="$NETWORK_BRIDGE",model=virtio \
        --graphics none \
        --console pty,target_type=serial \
        --boot cdrom \
        --noautoconsole

    log_info "VM started. Connecting to console..."
    log_info "(To detach from console: Ctrl+] or Ctrl+5)"
    log_info ""

    virsh console "${IMAGE_NAME}-install"

    # Clean up VM definition
    virsh undefine "${IMAGE_NAME}-install" 2>/dev/null || true

    log_info "Installation VM removed"
}

# Post-process the image
post_process_image() {
    local disk_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"
    local temp_path="${OUTPUT_DIR}/${IMAGE_NAME}-temp.qcow2"

    log_info "Post-processing image..."

    # Compress the image
    log_info "Compressing image (this may take a minute)..."
    qemu-img convert -O qcow2 -c "$disk_path" "$temp_path"
    mv "$temp_path" "$disk_path"

    # Get final size
    local size=$(du -h "$disk_path" | cut -f1)
    log_info "Final image size: $size"
}

# Note: VyOS no longer provides free qcow2 downloads
# Pre-built qcow2 images require a VyOS subscription
# See: https://vyos.net/get/ for subscription options
# This script uses the ISO installation method instead

# Print usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Builds a VyOS base image from the official ISO."
    echo "Note: VyOS no longer provides free qcow2 downloads."
    echo ""
    echo "Options:"
    echo "  --help       Show this help"
    echo ""
    echo "The script will:"
    echo "  1. Download the VyOS ISO"
    echo "  2. Start a VM for installation"
    echo "  3. You complete manual install steps"
    echo "  4. Image is ready for nodebuilder"
}

# Main execution
main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --help|-h)
                usage
                exit 0
                ;;
            *)
                shift
                ;;
        esac
    done

    log_info "=== ATL VyOS Base Image Builder ==="
    log_info "Output directory: $OUTPUT_DIR"

    check_prerequisites
    check_network_bridge

    mkdir -p "$OUTPUT_DIR"

    # Download ISO and install
    download_vyos_iso
    create_seed_config || true
    install_vyos
    post_process_image

    local disk_path="${OUTPUT_DIR}/${IMAGE_NAME}.qcow2"

    if [ -f "$disk_path" ]; then
        log_info "=== Build Complete ==="
        log_info "Base image: $disk_path"
        log_info ""
        log_info "To use this image:"
        log_info "  1. Copy to /var/lib/libvirt/images/firewall/base/"
        log_info "  2. The nodebuilder service will clone it for each new firewall"
        log_info "  3. Cloud-init configures hostname, IPs, and user at boot"
        log_info ""
        log_info "Default credentials (before cloud-init):"
        log_info "  Username: vyos"
        log_info "  Password: vyos (or arista if manually installed)"
    else
        log_error "Image creation failed"
        exit 1
    fi
}

# Run main function
main "$@"
