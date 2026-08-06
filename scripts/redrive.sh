#!/usr/bin/env bash
# Redrive SQS DLQ messages back to the main moderation jobs queue.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

exec "$PY" "$ROOT/scripts/redrive.py" "$@"
