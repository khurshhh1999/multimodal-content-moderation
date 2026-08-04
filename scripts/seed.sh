#!/usr/bin/env bash
# Seed demo fixtures into a running local stack (API + worker + MinIO).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${API_BASE:-http://localhost:8000}"

echo "==> Ensuring sample images exist..."
python3 "$ROOT/scripts/generate_samples.py"

echo "==> Seeding via demo ingest path..."
exec "$ROOT/scripts/demo.sh"
