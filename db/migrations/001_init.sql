-- Multimodal content moderation schema
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS content_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash    TEXT NOT NULL UNIQUE,
    object_key      TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    caption         TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL,
    byte_size       BIGINT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'api',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id      UUID NOT NULL REFERENCES content_items(id),
    content_hash    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'succeeded', 'failed', 'dead')),
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    last_error      TEXT,
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS decisions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES jobs(id),
    content_id          UUID NOT NULL REFERENCES content_items(id),
    content_hash        TEXT NOT NULL,
    decision            TEXT NOT NULL CHECK (decision IN ('ALLOW', 'FLAG', 'BLOCK')),
    confidence          DOUBLE PRECISION NOT NULL,
    reasons             JSONB NOT NULL DEFAULT '[]'::jsonb,
    vision_signals      JSONB NOT NULL DEFAULT '{}'::jsonb,
    llm_signals         JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_version      TEXT NOT NULL,
    pipeline_version    TEXT NOT NULL,
    latency_ms          INT NOT NULL DEFAULT 0,
    needs_human_review  BOOLEAN NOT NULL DEFAULT false,
    envelope            JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions(decision);
CREATE INDEX IF NOT EXISTS idx_decisions_needs_review ON decisions(needs_human_review);

CREATE TABLE IF NOT EXISTS review_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID NOT NULL REFERENCES decisions(id) UNIQUE,
    content_id      UUID NOT NULL REFERENCES content_items(id),
    job_id          UUID NOT NULL REFERENCES jobs(id),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'claimed', 'approved', 'rejected', 'escalated')),
    priority        INT NOT NULL DEFAULT 100,
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    claim_expires_at TIMESTAMPTZ,
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    reviewer_decision TEXT CHECK (reviewer_decision IS NULL OR reviewer_decision IN ('ALLOW', 'BLOCK')),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status, priority, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    action          TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT 'system',
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS metrics_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
