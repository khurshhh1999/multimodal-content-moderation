from __future__ import annotations

from dataclasses import dataclass

from .envelope import Decision


@dataclass(frozen=True)
class ThresholdConfig:
    """Confidence bands for auto-resolve vs human review.

    - confidence >= auto_allow_min and fused risk low → ALLOW (auto)
    - confidence >= auto_block_min and fused risk high → BLOCK (auto)
    - otherwise → FLAG (human review)
    """

    policy_version: str = "policy-v1"
    auto_allow_min: float = 0.85
    auto_block_min: float = 0.90
    # Mid-band always routes to humans even if model says ALLOW/BLOCK
    flag_confidence_ceiling: float = 0.75
    nsfw_block: float = 0.85
    nsfw_flag: float = 0.45
    violence_block: float = 0.80
    violence_flag: float = 0.40


def route_decision(
    *,
    suggested: Decision,
    confidence: float,
    nsfw_score: float,
    violence_score: float,
    thresholds: ThresholdConfig | None = None,
) -> tuple[Decision, bool, list[str]]:
    """Return (final_decision, needs_human_review, extra_reasons)."""
    t = thresholds or ThresholdConfig()
    reasons: list[str] = []

    # Hard signal overrides
    if nsfw_score >= t.nsfw_block or violence_score >= t.violence_block:
        reasons.append("hard_signal_block_threshold")
        if confidence >= t.auto_block_min:
            return Decision.BLOCK, False, reasons
        return Decision.BLOCK, True, reasons + ["low_confidence_block_needs_review"]

    if nsfw_score >= t.nsfw_flag or violence_score >= t.violence_flag:
        reasons.append("soft_signal_flag_band")
        return Decision.FLAG, True, reasons

    if suggested == Decision.BLOCK:
        if confidence >= t.auto_block_min:
            return Decision.BLOCK, False, reasons
        reasons.append("block_below_auto_threshold")
        return Decision.FLAG, True, reasons

    if suggested == Decision.ALLOW:
        if confidence >= t.auto_allow_min:
            return Decision.ALLOW, False, reasons
        reasons.append("allow_below_auto_threshold")
        return Decision.FLAG, True, reasons

    # Explicit FLAG from classifier
    return Decision.FLAG, True, reasons + ["classifier_flagged"]
