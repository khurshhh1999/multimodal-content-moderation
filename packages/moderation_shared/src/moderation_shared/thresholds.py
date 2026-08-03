from __future__ import annotations

from dataclasses import dataclass

from .envelope import Decision


@dataclass(frozen=True)
class ThresholdConfig:
    """Confidence bands for auto-resolve vs human review.

    Versioned as ``policy_version``. Tuning these without bumping the version
    breaks auditability of past decisions.

    - ``auto_allow`` — min confidence to auto-ALLOW when fused risk is low
    - ``auto_block`` — min confidence to auto-BLOCK when fused risk is high
    - ``flag_band`` — soft vision scores in ``[nsfw_flag, nsfw_block)`` (and
      violence equivalents) always route to humans as FLAG
    """

    policy_version: str = "policy-v1"
    auto_allow: float = 0.85
    auto_block: float = 0.90
    nsfw_block: float = 0.85
    nsfw_flag: float = 0.45
    violence_block: float = 0.80
    violence_flag: float = 0.40

    # Backward-compatible aliases used by older call sites / tests
    @property
    def auto_allow_min(self) -> float:
        return self.auto_allow

    @property
    def auto_block_min(self) -> float:
        return self.auto_block

    @property
    def flag_band(self) -> tuple[float, float]:
        """Soft-signal FLAG band as (nsfw_flag, nsfw_block)."""
        return (self.nsfw_flag, self.nsfw_block)

    @classmethod
    def from_values(
        cls,
        *,
        policy_version: str = "policy-v1",
        auto_allow: float = 0.85,
        auto_block: float = 0.90,
        nsfw_block: float = 0.85,
        nsfw_flag: float = 0.45,
        violence_block: float = 0.80,
        violence_flag: float = 0.40,
    ) -> ThresholdConfig:
        return cls(
            policy_version=policy_version,
            auto_allow=auto_allow,
            auto_block=auto_block,
            nsfw_block=nsfw_block,
            nsfw_flag=nsfw_flag,
            violence_block=violence_block,
            violence_flag=violence_flag,
        )


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
        if confidence >= t.auto_block:
            return Decision.BLOCK, False, reasons
        return Decision.BLOCK, True, reasons + ["low_confidence_block_needs_review"]

    # Soft signal flag band → always human
    if nsfw_score >= t.nsfw_flag or violence_score >= t.violence_flag:
        reasons.append("soft_signal_flag_band")
        return Decision.FLAG, True, reasons

    if suggested == Decision.BLOCK:
        if confidence >= t.auto_block:
            return Decision.BLOCK, False, reasons
        reasons.append("block_below_auto_threshold")
        return Decision.FLAG, True, reasons

    if suggested == Decision.ALLOW:
        if confidence >= t.auto_allow:
            return Decision.ALLOW, False, reasons
        reasons.append("allow_below_auto_threshold")
        return Decision.FLAG, True, reasons

    # Explicit FLAG from classifier
    return Decision.FLAG, True, reasons + ["classifier_flagged"]
