# Multimodal Content Moderation Pipeline

Event-driven moderation for **images + captions**: ingest → validate → vision signals → policy classifier → confidence routing → human review → audit/eval.

The pipeline combines idempotent jobs, retry and dead-letter handling, structured decision envelopes, pluggable AWS/GCP adapters, and a human review desk.

---

## Architecture

```
Client / scripts/demo.sh
        │
        ▼
 FastAPI ingest ──► object storage (MinIO/S3 | GCS) + Postgres content row
        │
        ▼
 Queue (SQS | Pub/Sub) ──► Worker pipeline
                      ├─ validate (size / MIME)
                      ├─ vision  (local | AWS Rekognition | GCP Vision)
                      ├─ LLM/policy (rules | OpenAI)
                      └─ threshold route → ALLOW / FLAG / BLOCK
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              auto-resolve            review_queue
              (high confidence)             │
                                            ▼
                                   Sentinel Desk (React)
                                            │
                                            ▼
                                        audit_log
```

**Decision envelope** (persisted JSON) includes: `decision`, `confidence`, `reasons`, vision/LLM scores, `policy_version`, `pipeline_version`, model versions, latency.

---

## Quick demo

**Prereqs:** Docker Desktop, Python 3.11+ (for sample generation / eval).

```bash
cp .env.example .env
docker compose up --build -d
./scripts/demo.sh
# or: make seed
```

Open **[http://localhost:5173](http://localhost:5173)** — **Sentinel Desk** review queue.

| Service | URL |
|---------|-----|
| Review UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (`admin` / `admin`, or anonymous Viewer) |
| Jaeger | http://localhost:16686 |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |
| LocalStack | http://localhost:4566 |

## Happy path

1. `POST /v1/content` (multipart image + caption) or `POST /v1/content/url` (JSON `{image_url, caption}`) with optional `X-Tenant-Id` → per-tenant rate limit → content hash idempotency → object storage + queue job
2. Worker runs vision + policy fusion → threshold route (`auto_allow` / `auto_block` / soft `flag_band`) → writes `decisions`
3. Low-confidence / `FLAG` / soft-risk → `review_queue`
4. Reviewer **Claim → Approve/Reject** (Redis claim lock, notes required on override) → `audit_log`. Locks last 15 minutes; expired claims return to pending so the queue cannot hide work behind a dead lock.

Re-uploading the same image+caption+policy returns `deduplicated: true`.

Ops & audit:

- `GET /health` — process liveness
- `GET /ready` — Postgres + Redis (Compose API healthcheck; worker waits for this)
- `GET /v1/metrics/summary` — queue depth, decisions/min, auto-resolve %, p95 latency
- `GET /metrics` — Prometheus text exposition (scraped by local Prometheus)
- `GET /v1/audit` — filterable audit trail (`entity_type`, `entity_id`, `actor`)
- `./scripts/redrive.sh` — move SQS DLQ messages back to the main queue and reset `dead`/`failed` jobs
- Ingest rate limit — Redis fixed window per `X-Tenant-Id` (default tenant `default`); `429` + `Retry-After` when exceeded
- Review/decision `image_url` values are **time-limited signed URLs** (MinIO/S3 presign or GCS V4); bucket is private by default
- Distributed traces — OpenTelemetry spans from ingest → queue → worker decision stages (Jaeger UI)

---

## Multi-cloud adapters (feature flags)

| Env | Values | Notes |
|-----|--------|-------|
| `VISION_PROVIDER` | `local` (default), `aws`, `gcp` | AWS/GCP fall back to local without credentials |
| `STORAGE_PROVIDER` | `s3` (default), `gcs` | Local demo uses MinIO; set `GCS_BUCKET` + ADC for GCS |
| `QUEUE_PROVIDER` | `sqs` (default), `pubsub` | Local demo uses LocalStack SQS; set `GCP_PROJECT` + topic/sub for Pub/Sub |
| `LLM_PROVIDER` | `rules` (default), `openai` | Set `OPENAI_API_KEY` for live LLM |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to SA JSON | Used by GCP Vision, GCS, and Pub/Sub |
| `POLICY_VERSION` | e.g. `policy-v1` | Bump when changing threshold bands |
| `AUTO_ALLOW` / `AUTO_BLOCK` | `0.85` / `0.90` | Confidence floors for auto-resolve |
| `NSFW_FLAG` / `NSFW_BLOCK` | soft / hard vision bands | Soft band → human review |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `60` / `60` | Per-tenant ingest cap; `0` requests disables |
| `INGEST_URL_TIMEOUT_SECONDS` | `10` | Timeout for `POST /v1/content/url` downloads |
| `DEFAULT_TENANT_ID` | `default` | Used when `X-Tenant-Id` is missing/invalid |
| `SIGNED_URL_TTL_SECONDS` | `900` | Expiry for review/decision content image links |
| `S3_PUBLIC_ENDPOINT_URL` | `http://localhost:9000` | Host embedded in MinIO/S3 presigned URLs (browser-reachable) |
| `OTEL_ENABLED` | `false` (Compose: `true`) | Emit traces when set |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector (Jaeger in Compose) |
| `OTEL_SERVICE_NAME` | `moderation-api` / `moderation-worker` | Resource `service.name` |

Local stack stays MinIO + LocalStack SQS. Flip the provider flags (and credentials) for a GCP path without changing pipeline code.

### Signed content URLs

Review queue and decision responses return short-lived `image_url` links instead of permanent public object paths. MinIO/S3 uses path-style presigned `GetObject` URLs signed against `S3_PUBLIC_ENDPOINT_URL` (so the API can talk to `minio:9000` internally while the browser hits `localhost:9000`). GCS uses V4 signed URLs when a service-account JSON is configured. The Compose MinIO bucket is private (`anonymous none`); expired links stop serving bytes.

### Distributed tracing

Compose runs Jaeger with OTLP/HTTP. The API instruments FastAPI requests plus `ingest.*` / `queue.enqueue` spans; the worker continues the same W3C `traceparent` via SQS/Pub/Sub message attributes and records `worker.consume` → `pipeline.*` (validate, vision, llm, route, persist). Open http://localhost:16686 after a demo upload and search service `moderation-api` or `moderation-worker`.

### Tenant rate limits

`POST /v1/content` and `POST /v1/content/url` are limited per tenant via Redis (`ratelimit:tenant:<id>:<window>`). Pass `X-Tenant-Id: acme` (alphanumeric / `.` `_` `-`, max 64). Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`; over-limit returns `429` with `Retry-After`. Tenant id is recorded on the enqueue audit event.

### Remote URL ingest

`POST /v1/content/url` accepts `{ "image_url": "https://...", "caption": "..." }`, downloads the bytes, then uses the same hash → object store → queue path as multipart upload. Fetches are http/https only: credentials, redirects, loopback/metadata hosts, and DNS that resolves to private/reserved addresses are rejected.

### Claim locks

Review claims are Redis SETNX locks (15 minute TTL) plus `claim_expires_at` on the queue row. Listing or claiming reviews releases expired rows back to `pending` (audit action `claim_expired`) so a crashed reviewer cannot strand an item. The desk shows remaining lock time on claimed cards.

---

## Data model (Postgres)

- `content_items` — object pointer + caption + `content_hash` (unique)
- `jobs` — queue state, attempts, DLQ-oriented failure states
- `decisions` — full envelope + scores
- `review_queue` — claim / resolve workflow
- `audit_log` — immutable actions

Schema: [`db/migrations/001_init.sql`](db/migrations/001_init.sql)

---

## Eval & metrics

```bash
make eval
```

Builds [`eval/labeled_set/`](eval/labeled_set/) (≥50 samples) then runs [`eval/harness.py`](eval/harness.py).

### Measured (local heuristic + rules)

From `make eval` / `eval/reports/latest.json` on the synthetic labeled set (n=57):

| Metric | Target | Measured (n=57) |
|--------|--------|-----------------|
| Macro precision | ≥ 0.95 | **1.00** |
| Macro recall | — | 1.00 |
| Macro F1 | — | 1.00 |
| Accuracy | — | 1.00 |
| Manual review reduction vs send-all | ≥ 0.60 | **0.72** (auto-resolve rate) |

These numbers come from a synthetic fixture set designed for the local heuristic-and-rules path; they do not demonstrate performance on real-world content. A representative evaluation requires human-labeled images and a fresh run for each configured vision provider.

Ops snapshot (live): `GET /v1/metrics/summary` and Prometheus `GET /metrics`.  
Grafana **Moderation ops** dashboard is provisioned at http://localhost:3000 (datasource → local Prometheus).  
Audit trail: `GET /v1/audit`.

## Repo layout

```
apps/api          FastAPI ingest + review + metrics
apps/worker       Queue consumer + pipeline stages + adapters
apps/dashboard    Sentinel Desk (React / Vite)
packages/moderation_shared   Decision envelope + thresholds
db/migrations     SQL schema
eval/             Labeled set + harness
scripts/          demo + seed + DLQ redrive + sample / labeled-set generators
infra/            LocalStack SQS init, Prometheus scrape, Grafana dashboards
```

Jaeger ships in Compose (OTLP :4318, UI :16686); no extra infra files required.

---

## DLQ redrive

After max receives, failed jobs land on `moderation-jobs-dlq`. To retry:

```bash
# Inspect depths
./scripts/redrive.sh --stats

# Plan without moving
./scripts/redrive.sh --dry-run

# Move up to 50 messages; reset matching dead/failed jobs in Postgres
./scripts/redrive.sh

# Or: make redrive ARGS='--limit 10 --job-id <uuid>'
```

Requires local stack (LocalStack SQS + Postgres). Uses `SQS_*` / `DATABASE_URL` from `.env`.

---

## Tests & CI

Local:

```bash
make venv          # once
make lint          # ruff
make test          # pytest
make eval-smoke    # labeled set + harness (--min-n 50)
make dashboard-build
# or: make ci
```

Pull requests run GitHub Actions (`.github/workflows/ci.yml`): Python lint, unit tests, eval smoke, and dashboard `npm run build`.

---

## License

MIT. See [LICENSE](LICENSE).
