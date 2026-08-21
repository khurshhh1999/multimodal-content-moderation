from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from ..db import connection
from ..prometheus_export import prometheus_payload
from ..schemas import MetricsSummary

router = APIRouter(tags=["metrics"])


async def fetch_metrics_summary() -> MetricsSummary:
    async with connection() as conn:
        pending = await conn.fetchval(
            """
            SELECT COUNT(*) FROM review_queue
            WHERE status = 'pending'
               OR (
                    status = 'claimed'
                    AND (claim_expires_at IS NULL OR claim_expires_at < now())
                  )
            """
        )
        claimed = await conn.fetchval(
            """
            SELECT COUNT(*) FROM review_queue
            WHERE status = 'claimed'
              AND claim_expires_at IS NOT NULL
              AND claim_expires_at >= now()
            """
        )
        queued_jobs = await conn.fetchval(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')"
        )
        totals = await conn.fetchrow(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE decision = 'ALLOW') AS allow_c,
              COUNT(*) FILTER (WHERE decision = 'FLAG') AS flag_c,
              COUNT(*) FILTER (WHERE decision = 'BLOCK') AS block_c,
              COUNT(*) FILTER (WHERE needs_human_review = false) AS auto_c
            FROM decisions
            """
        )
        p95 = await conn.fetchval(
            """
            SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FROM decisions
            WHERE created_at > now() - interval '24 hours'
            """
        )
        last_min = await conn.fetchval(
            """
            SELECT COUNT(*) FROM decisions
            WHERE created_at > now() - interval '1 minute'
            """
        )

    total = int(totals["total"] or 0)
    auto = int(totals["auto_c"] or 0)
    rate = (auto / total) if total else 0.0

    return MetricsSummary(
        queue_depth=int(queued_jobs or 0),
        pending_reviews=int(pending or 0),
        claimed_reviews=int(claimed or 0),
        decisions_total=total,
        decisions_allow=int(totals["allow_c"] or 0),
        decisions_flag=int(totals["flag_c"] or 0),
        decisions_block=int(totals["block_c"] or 0),
        auto_resolved=auto,
        auto_resolve_rate=round(rate, 4),
        p95_latency_ms=float(p95) if p95 is not None else None,
        decisions_last_minute=int(last_min or 0),
    )


@router.get("/v1/metrics/summary", response_model=MetricsSummary)
async def metrics_summary() -> MetricsSummary:
    return await fetch_metrics_summary()


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus text exposition of live moderation gauges."""
    summary = await fetch_metrics_summary()
    return Response(content=prometheus_payload(summary), media_type=CONTENT_TYPE_LATEST)
