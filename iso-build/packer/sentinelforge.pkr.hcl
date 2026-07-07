# SentinelForge ITDR — VM/ISO Build Pipeline
# Run on Ubuntu 24.04 build server (8 vCPU, 32 GB RAM, 200+ GB disk)

variable "ubuntu_version" {
  type    = string
  default = "24.04"
}

variable "vm_name" {
  type    = string
  default = "sentinelforge-itdr"
}

variable "disk_size" {
  type    = string
  default = "50000" # MB
}

variable "sentinelforge_source" {
  type    = string
  default = "../.."
}

packer {
  required_plugins {
    qemu = {
      version = ">= 1.0.9"
      source  = "github.com/hashicorp/qemu"
    }
  }
}

source "qemu" "sentinelforge" {
  iso_url          = "https://releases.ubuntu.com/noble/ubuntu-24.04.3-live-server-amd64.iso"
  iso_checksum     = "file:https://releases.ubuntu.com/noble/SHA256SUMS"
  output_directory = "./output/qcow2"
  disk_size        = var.disk_size
  disk_interface   = "virtio"
  format           = "qcow2"
  accelerator      = "kvm"
  headless         = true
  http_directory   = "./http"
  ssh_username     = "forge"
  ssh_password     = "forge"
  ssh_timeout      = "45m"
  vm_name          = "${var.vm_name}.qcow2"
  boot_wait        = "5s"
  boot_command = [
    "c<wait>",
    "linux /casper/vmlinuz --- autoinstall ds=nocloud-net\\;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<enter><wait>",
    "initrd /casper/initrd",
    "<enter><wait>",
    "boot<enter>"
  ]
  shutdown_command = "echo 'forge' | sudo -S shutdown -P now"
}

build {
  name    = "sentinelforge-itdr"
  sources = ["source.qemu.sentinelforge"]

  provisioner "shell" {
    inline = [
      "sudo apt-get update",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl git openssl",
      "curl -fsSL https://get.docker.com | sudo sh",
      "sudo usermod -aG docker forge",
    ]
  }

  provisioner "file" {
    source      = var.sentinelforge_source
    destination = "/tmp/sentinelforge-src"
  }

  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/sentinelforge",
      "sudo cp -a /tmp/sentinelforge-src/sentinelforge/. /opt/sentinelforge/",
      "sudo chown -R forge:forge /opt/sentinelforge",
      "sudo swapoff -a || true",
      "sudo sed -i '/swap/d' /etc/fstab || true",
      "echo '* soft nofile 65535' | sudo tee -a /etc/security/limits.conf",
      "echo '* hard nofile 65535' | sudo tee -a /etc/security/limits.conf",
      "sudo systemctl enable docker",
      "sudo systemctl enable systemd-timesyncd",
    ]
  }

  provisioner "shell" {
    inline = [
      "cd /opt/sentinelforge",
      "chmod +x setup.sh start.sh stop.sh update.sh",
      "sudo mkdir -p /opt/sentinelforge/data /opt/sentinelforge/certs",
      "sudo chown -R forge:forge /opt/sentinelforge",
    ]
  }

  post-processor "shell-local" {
    inline = [
      "echo 'QCOW2 image: iso-build/packer/output/qcow2/${var.vm_name}.qcow2'",
    ]
  }
}
