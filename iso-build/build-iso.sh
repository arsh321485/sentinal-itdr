#!/usr/bin/env bash
# SentinelForge ITDR — Build deliverable images on Ubuntu build server
#
# Outputs (in iso-build/output/):
#   sentinelforge-itdr-YYYYMMDD.qcow2  — VM disk for KVM/Proxmox/VMware (via conversion)
#   sentinelforge-itdr-YYYYMMDD.iso    — Bootable installer ISO with embedded autoinstall
#
# Prerequisites on build server:
#   sudo apt install -y qemu-kvm libvirt-daemon-system packer xorriso genisoimage
#   # Packer: https://developer.hashicorp.com/packer/install
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
DATE_TAG=$(date +%Y%m%d)
VM_NAME="sentinelforge-itdr-${DATE_TAG}"

mkdir -p "$OUTPUT_DIR" "$SCRIPT_DIR/cache" "$SCRIPT_DIR/work"

log() { echo "[build-iso] $*"; }

check_prereqs() {
  command -v docker >/dev/null || { log "ERROR: docker required"; exit 1; }
  command -v xorriso >/dev/null || sudo apt-get install -y xorriso genisoimage
  if ! command -v packer >/dev/null; then
    log "Installing Packer..."
    curl -fsSL https://releases.hashicorp.com/packer/1.10.3/packer_1.10.3_linux_amd64.zip -o /tmp/packer.zip
    unzip -o /tmp/packer.zip -d /tmp
    sudo mv /tmp/packer /usr/local/bin/packer
  fi
  if ! groups | grep -q kvm && ! groups | grep -q libvirt; then
    log "WARN: user not in kvm group — run: sudo usermod -aG kvm \$USER"
  fi
}

build_qcow2() {
  log "Building QCOW2 VM image with Packer..."
  cd "$SCRIPT_DIR/packer"
  packer init sentinelforge.pkr.hcl
  packer build -var "vm_name=$VM_NAME" sentinelforge.pkr.hcl

  QCOW2_SRC="$SCRIPT_DIR/packer/output/qcow2/${VM_NAME}.qcow2"
  QCOW2_DST="$OUTPUT_DIR/${VM_NAME}.qcow2"
  cp "$QCOW2_SRC" "$QCOW2_DST"
  log "QCOW2 ready: $QCOW2_DST"
}

build_installer_iso() {
  log "Building bootable installer ISO with autoinstall..."

  UBUNTU_ISO_URL="https://releases.ubuntu.com/noble/ubuntu-24.04.3-live-server-amd64.iso"
  UBUNTU_ISO="$SCRIPT_DIR/cache/ubuntu-24.04-live-server.iso"

  if [[ ! -f "$UBUNTU_ISO" ]]; then
    log "Downloading Ubuntu Server ISO..."
    curl -fL "$UBUNTU_ISO_URL" -o "$UBUNTU_ISO"
  fi

  WORK="$SCRIPT_DIR/work/iso-${DATE_TAG}"
  rm -rf "$WORK"
  mkdir -p "$WORK"

  log "Extracting Ubuntu ISO..."
  xorriso -osirrox on -indev "$UBUNTU_ISO" -extract / "$WORK" 2>/dev/null

  # Embed SentinelForge payload + autoinstall
  mkdir -p "$WORK/sentinelforge"
  cp -a "$REPO_ROOT/sentinelforge/." "$WORK/sentinelforge/"
  cp "$SCRIPT_DIR/packer/http/user-data" "$WORK/sentinelforge/autoinstall-user-data"
  cp "$SCRIPT_DIR/packer/http/meta-data" "$WORK/sentinelforge/autoinstall-meta-data"

  # Autoinstall late-command to copy sentinelforge from ISO
  cat >> "$WORK/sentinelforge/autoinstall-user-data" <<'LATE'

  late-commands:
    - curtin in-target --target=/target -- mkdir -p /opt/sentinelforge
    - curtin in-target --target=/target -- cp -a /cdrom/sentinelforge/. /target/opt/sentinelforge/
    - curtin in-target --target=/target -- chown -R forge:forge /target/opt/sentinelforge
    - curtin in-target --target=/target -- chmod +x /target/opt/sentinelforge/setup.sh
LATE

  ISO_OUT="$OUTPUT_DIR/${VM_NAME}-installer.iso"
  log "Creating installer ISO: $ISO_OUT"
  xorriso -as mkisofs -r -V "SentinelForge ITDR" \
    -o "$ISO_OUT" \
    -J -l -b isolinux/isolinux.bin -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot \
    -isohybrid-gpt-basdat \
    "$WORK" 2>/dev/null || {
      # Fallback for Ubuntu 24.04 layout
      grub-mkrescue -o "$ISO_OUT" "$WORK" 2>/dev/null || \
        xorriso -as mkisofs -r -J -l -o "$ISO_OUT" "$WORK"
    }

  log "Installer ISO ready: $ISO_OUT"
}

build_ova() {
  local qcow2="$OUTPUT_DIR/${VM_NAME}.qcow2"
  [[ -f "$qcow2" ]] || return 0
  if command -v qemu-img >/dev/null && command -v tar >/dev/null; then
    log "Converting to VMDK for OVA..."
    qemu-img convert -f qcow2 -O vmdk "$qcow2" "$OUTPUT_DIR/${VM_NAME}.vmdk"
    OVA_DIR="$SCRIPT_DIR/work/ova-${DATE_TAG}"
    mkdir -p "$OVA_DIR"
    cat > "$OVA_DIR/sentinelforge-itdr.ovf" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Envelope ovf:version="1.0" xmlns="http://schemas.dmtf.org/ovf/envelope/1"
  xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
  <References>
    <File ovf:href="${VM_NAME}.vmdk" ovf:id="file1" ovf:size="$(stat -c%s "$OUTPUT_DIR/${VM_NAME}.vmdk")"/>
  </References>
  <DiskSection>
    <Info>Virtual disk</Info>
    <Disk ovf:capacity="50" ovf:diskId="vmdisk1" ovf:fileRef="file1" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <VirtualSystem ovf:id="sentinelforge-itdr">
    <Name>SentinelForge ITDR</Name>
  </VirtualSystem>
</Envelope>
EOF
    cp "$OUTPUT_DIR/${VM_NAME}.vmdk" "$OVA_DIR/"
    tar -cvf "$OUTPUT_DIR/${VM_NAME}.ova" -C "$OVA_DIR" .
    log "OVA ready: $OUTPUT_DIR/${VM_NAME}.ova"
  fi
}

generate_checksums() {
  cd "$OUTPUT_DIR"
  sha256sum ${VM_NAME}* > "${VM_NAME}.sha256" 2>/dev/null || true
  log "Checksums: $OUTPUT_DIR/${VM_NAME}.sha256"
}

usage() {
  cat <<EOF
Usage: $0 [qcow2|iso|all|ova]

  qcow2  — Pre-built VM disk image (recommended for Proxmox/KVM)
  iso    — Bootable installer ISO with SentinelForge embedded
  ova    — VMware/VirtualBox OVA (requires qcow2 first)
  all    — Build qcow2 + installer ISO + OVA (default)

EOF
}

MODE="${1:-all}"
check_prereqs

case "$MODE" in
  qcow2) build_qcow2 ;;
  iso)   build_installer_iso ;;
  ova)   build_ova ;;
  all)
    build_qcow2
    build_installer_iso
    build_ova
  ;;
  *) usage; exit 1 ;;
esac

generate_checksums
log "Build complete. Artifacts in: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR"/${VM_NAME}* 2>/dev/null || ls -lh "$OUTPUT_DIR"
