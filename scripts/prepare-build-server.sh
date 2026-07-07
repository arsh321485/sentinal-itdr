#!/usr/bin/env bash
# One-time preparation for Ubuntu 24.04 build server
set -euo pipefail

echo "=== SentinelForge ITDR Build Server Setup ==="

# Docker
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Build tools
sudo apt-get update
sudo apt-get install -y \
  qemu-kvm libvirt-daemon-system xorriso genisoimage qemu-utils \
  git curl unzip openssl apache2

# Packer
if ! command -v packer &>/dev/null; then
  curl -fsSL https://releases.hashicorp.com/packer/1.10.3/packer_1.10.3_linux_amd64.zip -o /tmp/packer.zip
  unzip -o /tmp/packer.zip -d /tmp
  sudo mv /tmp/packer /usr/local/bin/packer
fi

# KVM permissions
sudo usermod -aG kvm "$USER" 2>/dev/null || true
sudo usermod -aG libvirt "$USER" 2>/dev/null || true

# Artifact web directory
sudo mkdir -p /var/www/sentinelforge-artifacts
sudo chown "$USER:$USER" /var/www/sentinelforge-artifacts

# Clone directory
sudo mkdir -p /opt/sentinelforge-build
sudo chown "$USER:$USER" /opt/sentinelforge-build

# System tuning (matches guide)
sudo swapoff -a 2>/dev/null || true
sudo sed -i '/swap/d' /etc/fstab 2>/dev/null || true
echo '* soft nofile 65535' | sudo tee -a /etc/security/limits.conf
echo '* hard nofile 65535' | sudo tee -a /etc/security/limits.conf
sudo systemctl enable --now systemd-timesyncd

# Apache for artifact downloads
sudo tee /etc/apache2/sites-available/sentinelforge-artifacts.conf >/dev/null <<'EOF'
<VirtualHost *:80>
    DocumentRoot /var/www/sentinelforge-artifacts
    <Directory /var/www/sentinelforge-artifacts>
        Options Indexes FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
</VirtualHost>
EOF
sudo a2ensite sentinelforge-artifacts
sudo a2dissite 000-default 2>/dev/null || true
sudo systemctl reload apache2

echo ""
echo "Build server ready. Next steps:"
echo "  1. Add GitHub secrets: BUILD_SERVER_HOST, BUILD_SERVER_USER, BUILD_SERVER_SSH_KEY"
echo "  2. Clone repo to /opt/sentinelforge-build"
echo "  3. Run: ./iso-build/build-iso.sh all"
echo "  4. Download artifacts from http://<server-ip>/"
