# SentinelForge ITDR

Identity Threat Detection & Response platform — built from the SecureITLab SentinelForge ITDR Complete Build Guide.

This repository contains the full stack (~16 Docker containers), the custom `identity-svc` Python service, and an ISO/QCOW2 build pipeline for client delivery.

---

## Table of contents

1. [One server — how it all works](#one-server--how-it-all-works)
2. [Architecture](#architecture)
3. [Ubuntu server — full install & run guide](#ubuntu-server--full-install--run-guide)
4. [Generate and store images (same server)](#generate-and-store-images-same-server)
5. [How to use generated images for clients](#how-to-use-generated-images-for-clients)
6. [GitHub CI/CD deploy](#github-cicd-deploy)
7. [Daily operations](#daily-operations)
8. [Run locally on Windows](#run-locally-on-windows)
9. [Detection rules & M365 setup](#identity-svc-detection-rules)
10. [Server requirements](#server-requirements)

---

## One server — how it all works

You use **one Ubuntu server** for everything. No separate build server and runtime server.

| What you do on this server | Where it lives |
|----------------------------|----------------|
| GitHub pushes code here | `/opt/sentinelforge-build/` (git repo) |
| Run & test ITDR live | `/opt/sentinelforge/` (Docker stack) |
| Build QCOW2 / ISO / OVA images | `/opt/sentinelforge-build/iso-build/output/` |
| Store images for future clients | `/var/www/sentinelforge-artifacts/` |

### Recommended single-server specs

| Resource | Minimum (run + build images) |
|----------|------------------------------|
| CPU | **16 vCPU** |
| RAM | **64 GB** |
| Disk | **1 TB SSD** (images are 5–15 GB each; keep many versions) |
| OS | **Ubuntu 24.04 LTS Server** (not Windows) |

Smaller servers (8 vCPU / 32 GB) can **run** ITDR but image builds will be slow or may fail — use 16 vCPU / 64 GB / 1 TB if you run and build on the same machine.

### End-to-end flow (one server)

```mermaid
flowchart LR
    subgraph YourServer["Your ONE Ubuntu server"]
        Git[GitHub push]
        Code[/opt/sentinelforge-build]
        Run[/opt/sentinelforge - Docker ITDR]
        Build[build-iso.sh]
        Store[/var/www/sentinelforge-artifacts]
    end

    Client[Client site]

    Git --> Code
    Code --> Run
    Code --> Build
    Build --> Store
    Store -->|download QCOW2 or ISO| Client
    Client -->|setup.sh + M365 creds| Live[Client ITDR running]
```

**In plain terms:**

1. You install Ubuntu + Docker + build tools **once** on your server.
2. You clone this repo and **run** ITDR there to test (`./setup.sh`).
3. When ready, you **build an image** (`./iso-build/build-iso.sh all`).
4. The image is **saved** on the same server for later (`/var/www/sentinelforge-artifacts/`).
5. When a client needs ITDR, you **give them the image** (download link, USB, or file share).
6. They **install the image** on their own hardware — your server is not involved after that.

Your server = **factory + test lab + image library**.  
Client server = **runs the product** using the image you shipped.

---

## Architecture

| Layer | Components |
|-------|------------|
| **ForgeCore** | Redpanda, Vector, Fluent Bit, PostgreSQL, Redis, MinIO, Keycloak, Vault, Prometheus, Grafana, Loki |
| **ForgeID** | identity-svc (M365/Google connectors, email scanner, 5 detection rules, Neo4j graph) |
| **Gateway** | Envoy (TLS, routes to portal + API + Keycloak + Grafana) |

## Repository layout

```
itdr/
├── sentinelforge/              # Runtime stack (deploy to /opt/sentinelforge)
│   ├── docker-compose.core.yml
│   ├── docker-compose.itdr.yml
│   ├── setup.sh                # First-run wizard
│   ├── start.sh / stop.sh / update.sh
│   ├── configs/
│   └── custom-images/identity-svc/
├── iso-build/                  # VM/ISO image builder
├── scripts/
│   ├── prepare-build-server.sh
│   └── start-local-windows.ps1
└── .github/workflows/
    └── build-deploy.yml
```

---

## Ubuntu server — full install & run guide

Complete setup for your **one Ubuntu server** — installs all tools, runs ITDR, and prepares the machine to build images later.

### What gets installed

| Tool | Purpose |
|------|---------|
| Docker + Compose | Run the 16-container ITDR stack |
| qemu-kvm, Packer, xorriso | Build QCOW2 / ISO / OVA images |
| Apache | Serve stored images for download |
| git, curl, openssl | Clone repo, TLS certs, scripts |

### Step 1 — Install Ubuntu 24.04

1. Download [Ubuntu 24.04 LTS Server ISO](https://ubuntu.com/download/server).
2. Install with **minimal** packages (no desktop/GUI).
3. Set hostname to `sentinelforge` (optional but recommended).
4. Create a user with sudo, e.g. `forge`:

```bash
# Run on the server after install (as root or via sudo)
adduser forge
usermod -aG sudo forge
```

5. SSH into the server:

```bash
ssh forge@<SERVER_IP>
```

### Step 2 — Update system and install base packages

```bash
sudo apt-get update
sudo apt-get upgrade -y

sudo apt-get install -y \
  curl \
  git \
  openssl \
  ca-certificates \
  gnupg \
  lsb-release \
  apt-transport-https \
  software-properties-common \
  ufw \
  jq
```

### Step 3 — Install Docker Engine and Compose

```bash
# Official Docker install script
curl -fsSL https://get.docker.com | sudo sh

# Allow your user to run docker without sudo
sudo usermod -aG docker $USER

# Apply group membership (or log out and SSH back in)
newgrp docker

# Verify versions (need Docker 24+ and Compose v2.20+)
docker --version
docker compose version
```

Expected output examples:

```
Docker version 24.x.x
Docker Compose version v2.2x.x
```

### Step 4 — System tuning (required)

Redis and identity timestamps need swap disabled and higher file limits:

```bash
# Disable swap (required for stable Redis)
sudo swapoff -a
sudo sed -i '/swap/d' /etc/fstab

# Increase open file limits
grep -q 'nofile 65535' /etc/security/limits.conf || {
  echo '* soft nofile 65535' | sudo tee -a /etc/security/limits.conf
  echo '* hard nofile 65535' | sudo tee -a /etc/security/limits.conf
}

# Enable NTP time sync (critical for identity events)
sudo systemctl enable --now systemd-timesyncd
timedatectl status
```

### Step 5 — Configure firewall

```bash
# Allow SSH (do this first so you don't lock yourself out)
sudo ufw allow OpenSSH

# SentinelForge inbound ports
sudo ufw allow 80/tcp    # Envoy HTTP
sudo ufw allow 443/tcp   # Envoy HTTPS (portal)
sudo ufw allow 8888/tcp  # Fluent Bit agents (optional)

# Enable firewall
sudo ufw enable
sudo ufw status
```

Outbound HTTPS (port 443) to Microsoft/Google is allowed by default — required for M365/Google connectors.

### Step 6 — Get the code onto the server

**Option A — Clone from GitHub (recommended)**

```bash
sudo mkdir -p /opt/sentinelforge-build
sudo chown $USER:$USER /opt/sentinelforge-build

git clone https://github.com/<YOUR_ORG>/<YOUR_REPO>.git /opt/sentinelforge-build
cd /opt/sentinelforge-build

# Copy runtime to live install path
sudo mkdir -p /opt/sentinelforge
sudo cp -a sentinelforge/. /opt/sentinelforge/
sudo chown -R $USER:$USER /opt/sentinelforge
```

**Option B — Copy from your PC with scp**

```bash
# Run from your Windows PC
scp -r C:\Users\arshm\Desktop\itdr forge@<SERVER_IP>:/home/forge/itdr

# On the server:
sudo mkdir -p /opt/sentinelforge-build /opt/sentinelforge
sudo cp -a /home/forge/itdr/. /opt/sentinelforge-build/
sudo cp -a /home/forge/itdr/sentinelforge/. /opt/sentinelforge/
sudo chown -R $USER:$USER /opt/sentinelforge-build /opt/sentinelforge
```

### Step 7 — Install image-build tools (same server, one time)

```bash
cd /opt/sentinelforge-build
chmod +x scripts/prepare-build-server.sh
./scripts/prepare-build-server.sh
```

This adds: `qemu-kvm`, Packer, `xorriso`, Apache, and `/var/www/sentinelforge-artifacts/` for storing images.

Log out and SSH back in:

```bash
exit
ssh forge@<SERVER_IP>
groups   # must show: docker kvm
```

### Step 8 — Create data directories

```bash
sudo mkdir -p /opt/sentinelforge/data/{redpanda,postgres,redis,minio,neo4j,vault,prometheus,grafana,loki,geolite2}
sudo mkdir -p /opt/sentinelforge/certs
sudo mkdir -p /opt/sentinelforge/logs
sudo chown -R $USER:$USER /opt/sentinelforge
```

### Step 9 — Make scripts executable

```bash
cd /opt/sentinelforge
chmod +x setup.sh start.sh stop.sh update.sh
```

### Step 10 — Run first-time setup wizard

`setup.sh` generates secrets, TLS certs, initializes Vault, prompts for client config, and starts all containers.

```bash
cd /opt/sentinelforge
./setup.sh
```

During the wizard you will be asked:

| Prompt | Example | Notes |
|--------|---------|-------|
| Portal domain | `forge.yourcompany.com` | Used in TLS/env |
| Company domain (BEC) | `yourcompany.com` | For email impersonation detection |
| Enable M365? | `y` or `n` | Needs Azure AD app |
| Enable Google? | `y` or `n` | Needs service account |
| Webhook URL | `https://hooks.slack.com/...` | Optional alert notifications |
| M365 tenant_id / client_id / client_secret | From Azure portal | Stored in Vault |

Setup takes **3–5 minutes** after containers start. Vault root token is saved to:

```bash
cat /opt/sentinelforge/.vault-root-token
# Keep this file secure — chmod 600
```

### Step 11 — Start stack manually (if not using setup.sh)

If you already ran setup once, or want manual control:

```bash
cd /opt/sentinelforge

# Start Layer 1 — ForgeCore (12 containers)
docker compose -f docker-compose.core.yml --env-file .env up -d

# Wait for core services to initialize
sleep 60

# Start Layer 2 — ForgeID (identity-svc, Neo4j, Envoy)
docker compose -f docker-compose.itdr.yml --env-file .env up -d --build

# Or use the helper script:
./start.sh
```

### Step 12 — Verify everything is running

```bash
cd /opt/sentinelforge

# Container status — all should show "running" or "healthy"
docker compose -f docker-compose.core.yml ps
docker compose -f docker-compose.itdr.yml ps

# Health checks
curl -s http://localhost:8000/health
curl -s http://localhost:9090/-/ready
curl -s http://localhost:8080/health
curl -s http://localhost:9000/minio/health/live

# identity-svc logs (look for M365 pull messages)
docker logs identity-svc --tail 30

# Create Redpanda topics (if setup.sh did not)
docker exec sf-redpanda rpk topic create \
  identity.m365.default \
  identity.google.default \
  events.normalized.default \
  alerts.itdr.default \
  -p 3 2>/dev/null || true
```

### Step 12 — Access the services

Replace `<SERVER_IP>` with your server IP or DNS name.

| Service | URL | Default login |
|---------|-----|---------------|
| **ITDR Portal** | `http://<SERVER_IP>:8000` or `https://<SERVER_IP>` | — |
| **Envoy gateway** | `http://<SERVER_IP>` / `https://<SERVER_IP>` | — |
| **Grafana** | `http://<SERVER_IP>:3000` | `admin` / password in `.env` |
| **Neo4j browser** | `http://<SERVER_IP>:7474` | `neo4j` / password in `.env` |
| **Keycloak** | `http://<SERVER_IP>:8080` | `admin` / password in `.env` |
| **Prometheus** | `http://<SERVER_IP>:9090` | — |
| **MinIO console** | `http://<SERVER_IP>:9001` | credentials in `.env` |

Read passwords from `.env`:

```bash
grep PASSWORD /opt/sentinelforge/.env
```

### Step 14 — Optional: GeoIP database for impossible travel

Vector uses MaxMind GeoLite2 for location enrichment:

```bash
# Register free at https://www.maxmind.com/en/geolite2/signup
# Download GeoLite2-City.mmdb and place it:
sudo mkdir -p /opt/sentinelforge/data/geolite2
sudo cp GeoLite2-City.mmdb /opt/sentinelforge/data/geolite2/
sudo chown -R $USER:$USER /opt/sentinelforge/data/geolite2

# Restart Vector
docker compose -f /opt/sentinelforge/docker-compose.core.yml restart vector
```

### Step 15 — Enable M365 connector after setup

```bash
# Edit config
nano /opt/sentinelforge/configs/identity-svc/config.yaml
# Set connectors.m365.enabled: true

# Store credentials in Vault (use token from .vault-root-token)
export VAULT_TOKEN=$(cat /opt/sentinelforge/.vault-root-token)
docker exec -e VAULT_TOKEN="$VAULT_TOKEN" sf-vault vault kv put secret/tenants/default/m365 \
  tenant_id="YOUR_AZURE_TENANT_ID" \
  client_id="YOUR_APP_CLIENT_ID" \
  client_secret="YOUR_APP_CLIENT_SECRET"

# Update .env with Vault token if not set
grep VAULT_TOKEN /opt/sentinelforge/.env

# Restart identity-svc
docker compose -f /opt/sentinelforge/docker-compose.itdr.yml --env-file /opt/sentinelforge/.env up -d --build identity-svc
```

---

## Generate and store images (same server)

After ITDR runs correctly on your server (`./setup.sh` + health checks pass), build client images **on the same machine**.

### Step 1 — Verify build tools

```bash
docker --version
packer --version
qemu-img --version
groups   # must include docker and kvm
```

### Step 2 — Build images

```bash
cd /opt/sentinelforge-build
chmod +x iso-build/build-iso.sh

# Build everything (QCOW2 + installer ISO + OVA)
./iso-build/build-iso.sh all

# Or one at a time:
./iso-build/build-iso.sh qcow2
./iso-build/build-iso.sh iso
./iso-build/build-iso.sh ova
```

Build time: **30–90 minutes**. The running ITDR stack can stay up during the build, but the server will be under heavy load.

### Step 3 — Store images for future clients

```bash
# Images are created here:
ls -lh /opt/sentinelforge-build/iso-build/output/

# Copy to long-term storage (same server):
sudo mkdir -p /var/www/sentinelforge-artifacts/itdr
sudo cp -a /opt/sentinelforge-build/iso-build/output/* /var/www/sentinelforge-artifacts/itdr/
sudo chown -R $USER:$USER /var/www/sentinelforge-artifacts

# Optional: organize by date
DATE=$(date +%Y-%m-%d)
sudo mkdir -p /var/www/sentinelforge-artifacts/itdr/$DATE
sudo cp -a /opt/sentinelforge-build/iso-build/output/* /var/www/sentinelforge-artifacts/itdr/$DATE/
```

### Step 4 — List stored images anytime

```bash
ls -lh /var/www/sentinelforge-artifacts/itdr/
```

Files you will see:

| File | What it is |
|------|------------|
| `sentinelforge-itdr-YYYYMMDD.qcow2` | Ready-to-boot virtual disk |
| `sentinelforge-itdr-YYYYMMDD-installer.iso` | Bootable installer USB/ISO |
| `sentinelforge-itdr-YYYYMMDD.ova` | VMware / VirtualBox package |
| `*.sha256` | Checksums to verify download integrity |

---

## How to use generated images for clients

This section explains **what each image is** and **exactly what you and the client do**.

### Image types — which one to give the client?

| Image | Give to client when… | Client needs |
|-------|----------------------|--------------|
| **QCOW2** | They use Proxmox, KVM, or OpenStack | Import disk → create VM → boot |
| **OVA** | They use VMware ESXi or VirtualBox | Import OVA → power on VM |
| **Installer ISO** | They have bare metal or want a fresh Ubuntu install | Boot from USB/DVD → autoinstall |

**Most common:** QCOW2 for cloud/private cloud, ISO for physical servers.

### Your workflow (you → client)

```text
1. Build image on your server
      ./iso-build/build-iso.sh all

2. Store it
      cp to /var/www/sentinelforge-artifacts/itdr/

3. Send to client (pick one)
      • Download link:  http://<YOUR_SERVER_IP>/itdr/sentinelforge-itdr-20260703.qcow2
      • SCP:            scp file.qcow2 client@their-server:/tmp/
      • USB drive
      • Shared drive (OneDrive, etc.)

4. Client installs image on THEIR hardware (see below)

5. Client runs setup.sh with THEIR M365/Google credentials

6. Client ITDR is live — independent of your server
```

**Important:** The image contains Ubuntu + Docker + SentinelForge code. It does **not** contain the client's M365 passwords — the client enters those during `setup.sh` on their side.

---

### How client uses QCOW2 (Proxmox / KVM example)

**On your server — share the file:**

```bash
# Client downloads:
wget http://<YOUR_SERVER_IP>/itdr/sentinelforge-itdr-20260703.qcow2
```

**On client Proxmox:**

1. Upload QCOW2 to Proxmox storage (Web UI → local → Upload).
2. Create VM: 8 vCPU, 32 GB RAM, 500 GB disk.
3. Set boot disk to the uploaded QCOW2.
4. Start VM.
5. SSH into VM: `ssh forge@<CLIENT_VM_IP>` (default password from image build: `forge` — change immediately).
6. Run client setup:

```bash
cd /opt/sentinelforge
./setup.sh
```

7. Open portal: `https://<CLIENT_VM_IP>` or `http://<CLIENT_VM_IP>:8000`

**On client KVM (command line):**

```bash
# Client runs on their Linux host:
virt-install \
  --name sentinelforge-itdr \
  --ram 32768 \
  --vcpus 8 \
  --disk path=sentinelforge-itdr-20260703.qcow2,format=qcow2 \
  --import \
  --network network=default \
  --os-variant ubuntu24.04

virsh start sentinelforge-itdr
ssh forge@<VM_IP>
cd /opt/sentinelforge && ./setup.sh
```

---

### How client uses OVA (VMware / VirtualBox)

**Client downloads:**

```bash
wget http://<YOUR_SERVER_IP>/itdr/sentinelforge-itdr-20260703.ova
```

**VMware ESXi / vSphere:**

1. File → Deploy OVF/OVA Template.
2. Select the `.ova` file.
3. Set resources: 8 vCPU, 32 GB RAM.
4. Power on VM.
5. SSH → `cd /opt/sentinelforge && ./setup.sh`

**VirtualBox:**

1. File → Import Appliance → select `.ova`.
2. Settings → 8 CPUs, 32 GB RAM.
3. Start → SSH → `./setup.sh`

---

### How client uses Installer ISO (bare metal / fresh install)

**You give client:** `sentinelforge-itdr-YYYYMMDD-installer.iso`

**Client steps:**

1. Write ISO to USB:

```bash
# On client or your machine:
sudo dd if=sentinelforge-itdr-20260703-installer.iso of=/dev/sdX bs=4M status=progress
# Replace /dev/sdX with correct USB device
```

2. Boot client server from USB.
3. Ubuntu autoinstall runs automatically (15–30 min).
4. Server reboots into installed system.
5. SSH in and finish setup:

```bash
ssh forge@<CLIENT_SERVER_IP>
cd /opt/sentinelforge
./setup.sh
```

6. Enter client-specific values: company domain, M365 tenant, notification webhook.

---

### What `setup.sh` does on the client (every image type)

Same for QCOW2, OVA, or ISO — **always run once on the client machine:**

| Step | Action |
|------|--------|
| 1 | Generate unique passwords (`.env`) |
| 2 | Create TLS certificates |
| 3 | Ask for portal domain, company domain |
| 4 | Ask to enable M365 / Google connectors |
| 5 | Store client credentials in Vault |
| 6 | Start all 16 Docker containers |
| 7 | ITDR portal live at `https://client-domain` |

```bash
cd /opt/sentinelforge
./setup.sh
```

---

### Image library — keep versions for future clients

Organize on your **one server**:

```text
/var/www/sentinelforge-artifacts/
└── itdr/
    ├── 2026-07-03/
    │   ├── sentinelforge-itdr-20260703.qcow2
    │   ├── sentinelforge-itdr-20260703-installer.iso
    │   ├── sentinelforge-itdr-20260703.ova
    │   └── SHA256SUMS
    ├── 2026-08-15/
    │   └── ...
    └── latest -> 2026-08-15/    # optional symlink to newest
```

Create `latest` symlink:

```bash
cd /var/www/sentinelforge-artifacts/itdr
ln -sfn 2026-07-03 latest
# Client can use: http://<YOUR_SERVER_IP>/itdr/latest/sentinelforge-itdr-20260703.qcow2
```

When you fix bugs or add features: build new image → store in new dated folder → send new image to **new** clients; existing clients run `./update.sh`.

---

## GitHub CI/CD deploy

GitHub pushes code to your **same Ubuntu server** automatically.

### Configure GitHub secrets

Repository → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Value |
|--------|-------|
| `BUILD_SERVER_HOST` | Your server IP, e.g. `203.0.113.10` |
| `BUILD_SERVER_USER` | `forge` |
| `BUILD_SERVER_SSH_KEY` | Private SSH key (full PEM contents) |

### Generate deploy key on your server

```bash
# On your Ubuntu server
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy   # paste into GitHub secret BUILD_SERVER_SSH_KEY
```

### Push code to trigger deploy

```bash
# On your dev machine
git add .
git commit -m "Update ITDR stack"
git push origin main

# To also build ISO on push, include in commit message:
git commit -m "Release ITDR v1.0 [build-iso]"
git push origin main
```

Or use **Actions → Build and Deploy SentinelForge ITDR → Run workflow**.

### Direct deploy to client without image (alternative)

If a client already has Ubuntu and Docker, skip images and copy code:

```bash
# From your server
rsync -avz /opt/sentinelforge/ forge@<CLIENT_IP>:/opt/sentinelforge/

# On client
ssh forge@<CLIENT_IP>
cd /opt/sentinelforge
chmod +x setup.sh start.sh stop.sh update.sh
./setup.sh
```

---

## Daily operations

### Start / stop / restart

```bash
cd /opt/sentinelforge

# Start everything
./start.sh

# Stop everything
./stop.sh

# Restart after code update
./update.sh

# Restart single service
docker compose -f docker-compose.itdr.yml --env-file .env restart identity-svc
```

### View logs

```bash
# identity-svc (connectors + detections)
docker logs -f identity-svc

# All core services
docker compose -f docker-compose.core.yml logs -f --tail=50

# Specific service
docker logs -f sf-redpanda
docker logs -f sf-neo4j
```

### Check alerts in Redpanda

```bash
docker exec sf-redpanda rpk topic consume alerts.itdr.default --num 5
```

### Query Neo4j graph

Open `http://<SERVER_IP>:7474` and run:

```cypher
MATCH (n) RETURN n LIMIT 50;
```

### Upgrade after git pull

```bash
cd /opt/sentinelforge-build
git pull origin main
rsync -a sentinelforge/ /opt/sentinelforge/
cd /opt/sentinelforge
./update.sh
```

### Backup important data

```bash
sudo tar -czvf sentinelforge-backup-$(date +%Y%m%d).tar.gz \
  /opt/sentinelforge/.env \
  /opt/sentinelforge/.vault-root-token \
  /opt/sentinelforge/configs \
  /opt/sentinelforge/data/postgres \
  /opt/sentinelforge/data/neo4j \
  /opt/sentinelforge/data/vault
```

---

## Run locally on Windows

For development only — use **Docker Desktop** with WSL2 and **16 GB+ RAM** allocated.

```powershell
cd C:\Users\arshm\Desktop\itdr
.\scripts\start-local-windows.ps1
```

Stop:

```powershell
.\scripts\start-local-windows.ps1 -Stop
```

| Service | URL |
|---------|-----|
| ITDR Portal | http://localhost:8000 |
| Grafana | http://localhost:3000 |
| Neo4j | http://localhost:7474 |

ISO/QCOW2 builds do **not** run on Windows — use your Ubuntu server.

---

## identity-svc detection rules

| Rule | Severity | Trigger |
|------|----------|---------|
| `impossible_travel` | CRITICAL | Login speed > 900 km/h between locations |
| `token_theft` | CRITICAL | Same session token from different /16 networks |
| `mfa_fatigue` | CRITICAL | >10 MFA pushes then approval in 5 min |
| `aitm_phishing` | CRITICAL | MFA from known AiTM proxy IP |
| `rogue_oauth` | HIGH | Non-admin grants sensitive OAuth scopes |
| `bec` | HIGH | Look-alike domain + financial keywords in email |

## M365 app registration (client)

Required API permissions (admin consent):

- `AuditLog.Read.All`
- `Directory.Read.All`
- `Mail.Read`
- `User.Read.All`

Store `tenant_id`, `client_id`, `client_secret` in Vault during `setup.sh`.

## Firewall summary

| Port | Direction | Service |
|------|-----------|---------|
| 22 | Inbound | SSH |
| 80 | Inbound | Envoy HTTP |
| 443 | Inbound | Envoy HTTPS (portal) |
| 443 | Outbound | M365 Graph API, Google APIs |
| 8888 | Inbound | Fluent Bit agents (optional) |

## Server requirements

| M365/Google users | CPU | RAM | Storage |
|-------------------|-----|-----|---------|
| ≤500 | 4 vCPU | 16 GB | 200 GB SSD |
| 500–2,000 | 8 vCPU | 32 GB | 500 GB SSD |
| 2,000–10,000 | 12 vCPU | 48 GB | 1 TB SSD |
| 10,000–50,000 | 16 vCPU | 64 GB | 2 TB SSD |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `permission denied` on docker | `sudo usermod -aG docker $USER` then re-login |
| Containers exit immediately | Check RAM (`free -h`), need 16 GB+ |
| `identity-svc` unhealthy | `docker logs identity-svc` — check Postgres/Neo4j passwords in `.env` |
| M365 not pulling | Verify Vault creds and `connectors.m365.enabled: true` |
| Port already in use | `sudo ss -tlnp \| grep :8000` — stop conflicting service |
| Vault sealed | `export VAULT_TOKEN=...` and unseal with key from init |

## License

Confidential — SecureITLab SentinelForge ITDR v1.0
