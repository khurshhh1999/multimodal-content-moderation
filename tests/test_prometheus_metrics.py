from __future__ import annotations

from app.prometheus_export import prometheus_payload
from app.schemas import MetricsSummary


def test_prometheus_payload_includes_core_gauges() -> None:
    summary = MetricsSummary(
        queue_depth=2,
        pending_reviews=3,
        claimed_reviews=1,
        decisions_total=10,
        decisions_allow=6,
        decisions_flag=2,
        decisions_block=2,
        auto_resolved=7,
        auto_resolve_rate=0.7,
        p95_latency_ms=123.4,
        decisions_last_minute=4,
    )
    body = prometheus_payload(summary).decode()
    assert "moderation_queue_depth 2.0" in body
    assert "moderation_pending_reviews 3.0" in body
    assert "moderation_auto_resolve_rate 0.7" in body
    assert "moderation_p95_latency_ms 123.4" in body
    assert "moderation_decisions_allow 6.0" in body


def test_prometheus_payload_omits_p95_when_missing() -> None:
    summary = MetricsSummary(
        queue_depth=0,
        pending_reviews=0,
        claimed_reviews=0,
        decisions_total=0,
        decisions_allow=0,
        decisions_flag=0,
        decisions_block=0,
        auto_resolved=0,
        auto_resolve_rate=0.0,
        p95_latency_ms=None,
        decisions_last_minute=0,
    )
    body = prometheus_payload(summary).decode()
    assert "moderation_p95_latency_ms" not in body
    assert "moderation_queue_depth 0.0" in body
