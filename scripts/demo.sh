#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${API_BASE:-http://localhost:8000}"

echo "==> Waiting for API health..."
for i in $(seq 1 60); do
  if curl -sf "$API/health" >/dev/null; then
    break
  fi
  sleep 2
  if [[ $i -eq 60 ]]; then
    echo "API not healthy at $API"
    exit 1
  fi
done

if [[ ! -f "$ROOT/samples/safe_sunset.png" ]]; then
  echo "==> Generating sample images..."
  python3 "$ROOT/scripts/generate_samples.py"
fi

upload() {
  local file="$1"
  local caption="$2"
  echo "-- POST $file | $caption"
  curl -sf -X POST "$API/v1/content" \
    -F "image=@${file}" \
    -F "caption=${caption}" | python3 -m json.tool
}

echo "==> Ingesting demo content..."
upload "$ROOT/samples/safe_sunset.png" "Beautiful sunset over the lake — nature landscape"
upload "$ROOT/samples/safe_forest.png" "Family hike through a green forest trail"
upload "$ROOT/samples/flag_uncertain.png" "This post is uncertain maybe edgy humor"
upload "$ROOT/samples/block_nsfw.png" "force_nsfw explicit demo sample"
upload "$ROOT/samples/block_violence.png" "force_violence weapon blood demo"

# Idempotency check
echo "==> Re-upload (expect deduplicated=true)..."
upload "$ROOT/samples/safe_sunset.png" "Beautiful sunset over the lake — nature landscape"

echo "==> Waiting for worker decisions..."
sleep 8

echo "==> Decisions:"
curl -sf "$API/v1/decisions?limit=10" | python3 -m json.tool

echo "==> Pending reviews:"
curl -sf "$API/v1/reviews?status=pending" | python3 -m json.tool

echo "==> Metrics:"
curl -sf "$API/v1/metrics/summary" | python3 -m json.tool

echo ""
echo "Open the review desk: http://localhost:5173"
echo "FLAG / low-confidence items should appear in the queue."
