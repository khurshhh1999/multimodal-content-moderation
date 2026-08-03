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

## Phase status

| Phase | Scope | Status |
|-------|--------|--------|
| **1** | Ingest + SQS worker + Postgres decisions + local demo + review UI | **Done** |
| **2** | Threshold polish, claim locks, Rekognition/OpenAI flags, richer metrics | Next |
| **3** | GCP adapter path, expanded labeled eval, measured résumé metrics | Planned |

Local planning files (`plan.md`, `AGENTS.md`) are gitignored.

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

### Happy path

1. `POST /v1/content` uploads image + caption → content hash idempotency → S3 object + SQS job  
2. Worker runs vision + policy fusion → writes `decisions`  
3. Low-confidence / `FLAG` / soft-risk → `review_queue`  
4. Reviewer **Claim → Approve/Reject** → `audit_log`

Re-uploading the same image+caption+policy returns `deduplicated: true`.

---

## Multi-cloud adapters (feature flags)

| Env | Values | Notes |
|-----|--------|-------|
| `VISION_PROVIDER` | `local` (default), `aws`, `gcp` | AWS/GCP fall back to local without credentials |
| `LLM_PROVIDER` | `rules` (default), `openai` | Set `OPENAI_API_KEY` for live LLM |
| Storage / queue locally | MinIO + LocalStack SQS | Production: S3+SQS or GCS+Pub/Sub (Phase 3) |

---

## Data model (Postgres)

- `content_items` — object pointer + caption + `content_hash` (unique)  
- `jobs` — queue state, attempts, DLQ-oriented failure states  
- `decisions` — full envelope + scores  
- `review_queue` — claim / resolve workflow  
- `audit_log` — immutable actions  

Schema: [`db/migrations/001_init.sql`](db/migrations/001_init.sql)

---

## Eval & metrics (honest numbers only)

```bash
make samples
make eval
```

Uses [`eval/labeled_set/manifest.json`](eval/labeled_set/manifest.json) (smoke set, n=5).  
**Do not cite résumé precision until the labeled set is expanded and harness output is pasted below.**

### Measured (smoke set n=5 — not résumé-grade)

From `make eval` / `eval/reports/latest.json` on the local heuristic+rules path:

| Metric | Target | Measured (n=5 smoke) |
|--------|--------|----------------------|
| Macro precision | ≥ 0.95 | **1.00** (smoke only — expand labeled set in Phase 3) |
| Accuracy | — | 1.00 |
| Manual review reduction vs send-all | ≥ 0.60 | **0.80** (auto-resolve rate on smoke set) |

These numbers are real harness output on a tiny fixture set. Do **not** put them on a résumé until the labeled set is expanded (≥50) and re-measured.

Ops snapshot (live): `GET /v1/metrics/summary` — queue depth, decisions/min, auto-resolve %, p95 latency.

---

## Repo layout

```
apps/api          FastAPI ingest + review + metrics
apps/worker       SQS consumer + pipeline stages + adapters
apps/dashboard    Sentinel Desk (React / Vite)
packages/moderation_shared   Decision envelope + thresholds
db/migrations     SQL schema
eval/             Labeled set + harness
scripts/          demo + sample generator
infra/            LocalStack SQS init (queue + DLQ)
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## Résumé bullets (update after Phase 3 metrics)

- Designed an event-driven multimodal moderation pipeline (FastAPI, SQS, S3/MinIO, Postgres) with idempotent content-hash ingest and DLQ-backed workers.  
- Fused vision + policy signals into a versioned decision envelope (`ALLOW`/`FLAG`/`BLOCK`) with confidence routing to a human review desk.  
- Built reviewer claim/resolve UX and audit trail; exposed ops metrics (auto-resolve rate, p95 latency, queue depth).  
- Feature-flagged AWS Rekognition / GCP Vision / OpenAI adapters behind a local demo path.  
- *(Phase 3)* Measured precision / manual-review reduction on a labeled eval set — **replace with harness numbers**.

---

## License

MIT (or your choice).
