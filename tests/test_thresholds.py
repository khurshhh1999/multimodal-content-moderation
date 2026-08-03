from moderation_shared import Decision, ThresholdConfig, route_decision


def test_auto_allow_high_confidence():
    decision, needs_review, _ = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.92,
        nsfw_score=0.05,
        violence_score=0.05,
    )
    assert decision == Decision.ALLOW
    assert needs_review is False


def test_low_confidence_allow_goes_to_flag():
    decision, needs_review, reasons = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.70,
        nsfw_score=0.05,
        violence_score=0.05,
    )
    assert decision == Decision.FLAG
    assert needs_review is True
    assert "allow_below_auto_threshold" in reasons


def test_hard_nsfw_blocks():
    decision, needs_review, reasons = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.95,
        nsfw_score=0.91,
        violence_score=0.1,
        thresholds=ThresholdConfig(),
    )
    assert decision == Decision.BLOCK
    assert needs_review is False
    assert "hard_signal_block_threshold" in reasons


def test_soft_band_flags():
    decision, needs_review, reasons = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.9,
        nsfw_score=0.5,
        violence_score=0.1,
    )
    assert decision == Decision.FLAG
    assert needs_review is True
    assert "soft_signal_flag_band" in reasons


def test_custom_auto_allow_threshold():
    tight = ThresholdConfig.from_values(auto_allow=0.95, policy_version="policy-tight")
    decision, needs_review, reasons = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.90,
        nsfw_score=0.05,
        violence_score=0.05,
        thresholds=tight,
    )
    assert decision == Decision.FLAG
    assert needs_review is True
    assert "allow_below_auto_threshold" in reasons
    assert tight.flag_band == (0.45, 0.85)
    assert tight.auto_allow_min == 0.95


def test_low_confidence_hard_block_needs_review():
    decision, needs_review, reasons = route_decision(
        suggested=Decision.ALLOW,
        confidence=0.70,
        nsfw_score=0.91,
        violence_score=0.1,
    )
    assert decision == Decision.BLOCK
    assert needs_review is True
    assert "low_confidence_block_needs_review" in reasons


def test_auto_block_high_confidence():
    decision, needs_review, _ = route_decision(
        suggested=Decision.BLOCK,
        confidence=0.95,
        nsfw_score=0.1,
        violence_score=0.1,
    )
    assert decision == Decision.BLOCK
    assert needs_review is False
