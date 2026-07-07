#!/usr/bin/env bash
# Start full SentinelForge ITDR stack
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

docker compose -f docker-compose.core.yml --env-file .env up -d
echo "Waiting for Core services..."
sleep 45
docker compose -f docker-compose.itdr.yml --env-file .env up -d --build
echo "Stack started."
