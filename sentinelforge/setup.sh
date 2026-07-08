#!/usr/bin/env bash
# SentinelForge ITDR — first-run setup wizard
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SF_INSTALL_DIR:-/opt/sentinelforge}"
DATA_DIR="${SF_DATA_DIR:-/opt/sentinelforge/data}"
CERTS_DIR="${SF_CERTS_DIR:-/opt/sentinelforge/certs}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log() { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${RED}[setup]${NC} $*"; }

require_root_dirs() {
  sudo mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$CERTS_DIR" "$INSTALL_DIR/logs"
  sudo mkdir -p "$DATA_DIR"/{redpanda,postgres,redis,minio,neo4j,vault,prometheus,grafana,loki,geolite2}
  sudo chown -R "${USER}:${USER}" "$INSTALL_DIR" "$DATA_DIR" "$CERTS_DIR" 2>/dev/null || true
}

gen_password() {
  openssl rand -base64 24 | tr -d '/+=' | head -c 24
}

generate_env() {
  if [[ -f "$SCRIPT_DIR/.env" ]]; then
    log ".env already exists — skipping generation"
    return
  fi

  log "Generating secrets..."
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  sed -i "s/CHANGE_ME_POSTGRES/$(gen_password)/" "$SCRIPT_DIR/.env"
  sed -i "s/CHANGE_ME_MINIO/$(gen_password)/" "$SCRIPT_DIR/.env"
  sed -i "s/CHANGE_ME_KEYCLOAK/$(gen_password)/" "$SCRIPT_DIR/.env"
  sed -i "s/CHANGE_ME_GRAFANA/$(gen_password)/" "$SCRIPT_DIR/.env"
  sed -i "s/CHANGE_ME_NEO4J/$(gen_password)/" "$SCRIPT_DIR/.env"
}

generate_tls_certs() {
  if [[ -f "$CERTS_DIR/server.pem" ]]; then
    log "TLS certs already exist"
    return
  fi
  log "Generating self-signed TLS certificates..."
  openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout "$CERTS_DIR/server-key.pem" \
    -out "$CERTS_DIR/server.pem" \
    -subj "/CN=sentinelforge.local/O=SecureITLab"
  cp "$CERTS_DIR/server.pem" "$CERTS_DIR/ca.pem"
}

system_tuning() {
  log "Applying system tuning..."
  sudo swapoff -a 2>/dev/null || true
  sudo sed -i '/swap/d' /etc/fstab 2>/dev/null || true
  grep -q 'nofile 65535' /etc/security/limits.conf 2>/dev/null || {
    echo '* soft nofile 65535' | sudo tee -a /etc/security/limits.conf
    echo '* hard nofile 65535' | sudo tee -a /etc/security/limits.conf
  }
  sudo systemctl enable --now systemd-timesyncd 2>/dev/null || true
}

prompt_client_config() {
  log "Client configuration (press Enter to skip optional fields)"
  read -rp "Portal domain [forge.clientdomain.com]: " portal_domain
  portal_domain="${portal_domain:-forge.clientdomain.com}"

  read -rp "Company domain for BEC detection [example.com]: " company_domain
  company_domain="${company_domain:-example.com}"

  read -rp "Enable M365 connector? [y/N]: " enable_m365
  read -rp "Enable Google connector? [y/N]: " enable_google
  read -rp "Notification webhook URL (optional): " webhook_url

  [[ "$enable_m365" =~ ^[Yy] ]] && m365_enabled="true" || m365_enabled="false"
  [[ "$enable_google" =~ ^[Yy] ]] && google_enabled="true" || google_enabled="false"

  cat > "$SCRIPT_DIR/configs/identity-svc/config.yaml" <<EOF
tenant_id: default
company_domain: ${company_domain}

connectors:
  m365:
    enabled: ${m365_enabled}
    pull_interval_minutes: 5
  google:
    enabled: ${google_enabled}
    pull_interval_minutes: 5

email_scanner:
  enabled: true
  bec_keywords: [wire, transfer, confidential, urgent, escrow, "bank details"]
  suspicious_extensions: [.exe, .scr, .js, .vbs, .bat]

detection:
  impossible_travel_speed_kmh: 900
  mfa_fatigue_push_count: 10
  mfa_fatigue_window_seconds: 300
  token_theft_window_minutes: 30
  aitm_proxy_ips_file: /app/config/aitm_proxy_ips.txt

notifications:
  webhook_url: "${webhook_url}"

redpanda:
  brokers: redpanda:9092

vault:
  addr: http://vault:8200
  m365_secret_path: secret/data/tenants/default/m365
  google_secret_path: secret/data/tenants/default/google
EOF

  sed -i "s|^PORTAL_DOMAIN=.*|PORTAL_DOMAIN=${portal_domain}|" "$SCRIPT_DIR/.env"
}

init_vault() {
  log "Initializing Vault (dev mode for first boot)..."
  docker compose -f "$SCRIPT_DIR/docker-compose.core.yml" up -d vault
  sleep 5

  if ! docker exec sf-vault vault status 2>/dev/null | grep -q "Initialized.*true"; then
    docker exec sf-vault vault operator init -key-shares=1 -key-threshold=1 -format=json > /tmp/vault-init.json
    UNSEAL_KEY=$(python3 -c "import json; print(json.load(open('/tmp/vault-init.json'))['unseal_keys_b64'][0])")
    ROOT_TOKEN=$(python3 -c "import json; print(json.load(open('/tmp/vault-init.json'))['root_token'])")
    docker exec sf-vault vault operator unseal "$UNSEAL_KEY"
    echo "$ROOT_TOKEN" > "$INSTALL_DIR/.vault-root-token"
    chmod 600 "$INSTALL_DIR/.vault-root-token"
    sed -i "s|^VAULT_TOKEN=.*|VAULT_TOKEN=${ROOT_TOKEN}|" "$SCRIPT_DIR/.env"
    docker exec -e VAULT_TOKEN="$ROOT_TOKEN" sf-vault vault secrets enable -path=secret kv-v2 || true
    rm -f /tmp/vault-init.json
    log "Vault root token saved to $INSTALL_DIR/.vault-root-token"
  fi
}

store_credentials() {
  local token
  token=$(grep '^VAULT_TOKEN=' "$SCRIPT_DIR/.env" | cut -d= -f2)
  [[ -z "$token" && -f "$INSTALL_DIR/.vault-root-token" ]] && token=$(cat "$INSTALL_DIR/.vault-root-token")

  if [[ -z "$token" ]]; then
    warn "No Vault token — skip credential storage"
    return
  fi

  if grep -q "enabled: true" "$SCRIPT_DIR/configs/identity-svc/config.yaml" && grep -q "m365:" -A2 "$SCRIPT_DIR/configs/identity-svc/config.yaml"; then
    read -rp "M365 tenant_id (optional): " m365_tenant
    read -rp "M365 client_id (optional): " m365_client
    read -rsp "M365 client_secret (optional): " m365_secret; echo
    if [[ -n "$m365_tenant" ]]; then
      docker exec -e VAULT_TOKEN="$token" sf-vault vault kv put secret/tenants/default/m365 \
        tenant_id="$m365_tenant" client_id="$m365_client" client_secret="$m365_secret" || true
    fi
  fi
}

apply_neo4j_schema() {
  local pw
  pw=$(grep '^NEO4J_PASSWORD=' "$SCRIPT_DIR/.env" | cut -d= -f2)
  log "Applying Neo4j schema..."
  for _ in $(seq 1 30); do
    if docker exec sf-neo4j wget -qO- http://localhost:7474 >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  docker exec -i sf-neo4j cypher-shell -u neo4j -p "$pw" \
    < "$SCRIPT_DIR/configs/neo4j/schema.cypher" \
    || warn "Neo4j schema apply failed (constraints may already exist)"
}

start_stack() {
  log "Starting ForgeCore (Layer 1)..."
  cd "$SCRIPT_DIR"
  docker compose -f docker-compose.core.yml --env-file .env up -d
  log "Waiting 60s for Core services..."
  sleep 60

  log "Starting ForgeID (Layer 2)..."
  docker compose -f docker-compose.itdr.yml --env-file .env up -d --build

  apply_neo4j_schema

  log "Creating Redpanda topics..."
  docker exec sf-redpanda rpk topic create identity.m365.default identity.google.default events.normalized.default alerts.itdr.default -p 3 2>/dev/null || true
}

verify() {
  log "Verification:"
  docker compose -f docker-compose.core.yml ps
  docker compose -f docker-compose.itdr.yml ps
  curl -sf http://localhost:8000/health && echo " identity-svc: OK" || warn "identity-svc not ready"
  curl -sf http://localhost:9090/-/ready && echo " Prometheus: OK" || true
}

main() {
  log "SentinelForge ITDR Setup"
  require_root_dirs
  system_tuning
  generate_env
  generate_tls_certs
  prompt_client_config
  init_vault
  store_credentials
  start_stack
  verify
  log "Setup complete. Portal: https://$(grep PORTAL_DOMAIN .env | cut -d= -f2) (or http://localhost via Envoy)"
  log "Grafana: http://localhost:3000 | Neo4j: http://localhost:7474"
}

main "$@"
