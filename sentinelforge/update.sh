#!/usr/bin/env bash
# Pull latest code and rebuild identity-svc + detection rules
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[update] Pulling latest images..."
docker compose -f docker-compose.core.yml pull

echo "[update] Rebuilding identity-svc..."
docker compose -f docker-compose.itdr.yml --env-file .env build identity-svc

echo "[update] Restarting ITDR layer..."
docker compose -f docker-compose.itdr.yml --env-file .env up -d

echo "[update] Done. Check: curl -s http://localhost:8000/health"
