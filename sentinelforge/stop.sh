#!/usr/bin/env bash
# Stop full SentinelForge ITDR stack
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
docker compose -f docker-compose.itdr.yml down
docker compose -f docker-compose.core.yml down
