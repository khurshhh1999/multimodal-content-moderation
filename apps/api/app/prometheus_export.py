from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from .schemas import MetricsSummary


def prometheus_payload(summary: MetricsSummary) -> bytes:
    """Render live moderation gauges in Prometheus text exposition format."""
    registry = CollectorRegistry()
    gauges: list[tuple[str, str, float]] = [
        ("moderation_queue_depth", "Jobs queued or processing", float(summary.queue_depth)),
        (
            "moderation_pending_reviews",
            "Review queue items awaiting claim",
            float(summary.pending_reviews),
        ),
        (
            "moderation_claimed_reviews",
            "Review queue items currently claimed",
            float(summary.claimed_reviews),
        ),
        ("moderation_decisions_total", "Total persisted decisions", float(summary.decisions_total)),
        ("moderation_decisions_allow", "ALLOW decisions", float(summary.decisions_allow)),
        ("moderation_decisions_flag", "FLAG decisions", float(summary.decisions_flag)),
        ("moderation_decisions_block", "BLOCK decisions", float(summary.decisions_block)),
        (
            "moderation_auto_resolved",
            "Decisions that skipped human review",
            float(summary.auto_resolved),
        ),
        (
            "moderation_auto_resolve_rate",
            "Fraction of decisions auto-resolved",
            float(summary.auto_resolve_rate),
        ),
        (
            "moderation_decisions_last_minute",
            "Decisions written in the last minute",
            float(summary.decisions_last_minute),
        ),
    ]
    if summary.p95_latency_ms is not None:
        gauges.append(
            (
                "moderation_p95_latency_ms",
                "p95 decision latency over the last 24h (ms)",
                float(summary.p95_latency_ms),
            )
        )

    for name, help_text, value in gauges:
        Gauge(name, help_text, registry=registry).set(value)

    return generate_latest(registry)
