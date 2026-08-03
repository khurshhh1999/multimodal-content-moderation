# Multimodal Content Moderation Pipeline

Event-driven moderation for **images + captions**: ingest → validate → vision signals → policy classifier → confidence routing → human review → audit/eval.

Built as a portfolio system (not a thin cloud API wrapper): idempotent jobs, retries/DLQ, structured decision envelopes, pluggable AWS/GCP adapters, and a review-desk UI.

---

## Architecture

```
Client / scripts/demo.sh
        │
        ▼
 FastAPI ingest ──► MinIO (S3) + Postgres content row
        │
        ▼
 LocalStack SQS ──► Worker pipeline
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
```

Open **[http://localhost:5173](http://localhost:5173)** — **Sentinel Desk** review queue.

| Service | URL |
|---------|-----|
| Review UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |
| LocalStack | http://localhost:4566 |

## Happy path

1. `POST /v1/content` uploads image + caption → content hash idempotency → S3 object + SQS job  
2. Worker runs vision + policy fusion → threshold route (`auto_allow` / `auto_block` / soft `flag_band`) → writes `decisions`  
3. Low-confidence / `FLAG` / soft-risk → `review_queue`  
4. Reviewer **Claim → Approve/Reject** (Redis claim lock, notes required on override) → `audit_log`

Re-uploading the same image+caption+policy returns `deduplicated: true`.

Ops & audit:
- `GET /v1/metrics/summary` — queue depth, decisions/min, auto-resolve %, p95 latency  
- `GET /v1/audit` — filterable audit trail (`entity_type`, `entity_id`, `actor`)

Seed fixtures: `make seed` (or `./scripts/seed.sh`).

---

## Multi-cloud adapters (feature flags)

| Env | Values | Notes |
|-----|--------|-------|
| `VISION_PROVIDER` | `local` (default), `aws`, `gcp` | AWS/GCP fall back to local without credentials |
| `LLM_PROVIDER` | `rules` (default), `openai` | Set `OPENAI_API_KEY` for live LLM |
| `POLICY_VERSION` | e.g. `policy-v1` | Bump when changing threshold bands |
| `AUTO_ALLOW` / `AUTO_BLOCK` | `0.85` / `0.90` | Confidence floors for auto-resolve |
| `NSFW_FLAG` / `NSFW_BLOCK` | soft / hard vision bands | Soft band → human review |
| Storage / queue locally | MinIO + LocalStack SQS | Production: S3+SQS or GCS+Pub/Sub |

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
make samples
make eval
```

Uses [`eval/labeled_set/manifest.json`](eval/labeled_set/manifest.json) (smoke set, n=5).

### Measured (smoke set n=5)

From `make eval` / `eval/reports/latest.json` on the local heuristic+rules path:

| Metric | Target | Measured (n=5 smoke) |
|--------|--------|----------------------|
| Macro precision | ≥ 0.95 | **1.00** |
| Accuracy | — | 1.00 |
| Manual review reduction vs send-all | ≥ 0.60 | **0.80** (auto-resolve rate) |

Harness output on a small fixture set — expand the labeled set before treating these as production-grade.

Ops snapshot (live): `GET /v1/metrics/summary` — queue depth, decisions/min, auto-resolve %, p95 latency.  
Audit trail: `GET /v1/audit`.

---

## Repo layout

```
apps/api          FastAPI ingest + review + metrics
apps/worker       SQS consumer + pipeline stages + adapters
apps/dashboard    Sentinel Desk (React / Vite)
packages/moderation_shared   Decision envelope + thresholds
db/migrations     SQL schema
eval/             Labeled set + harness
scripts/          demo + seed + sample generator
infra/            LocalStack SQS init (queue + DLQ)
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## License

MIT (or your choice).
