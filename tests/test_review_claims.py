from datetime import datetime, timedelta, timezone

from app.review_claims import claim_is_active, is_claim_expired


def test_pending_is_never_expired():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    assert (
        is_claim_expired(
            status="pending",
            claim_expires_at=now - timedelta(minutes=1),
            now=now,
        )
        is False
    )


def test_claimed_without_expiry_is_expired():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    assert is_claim_expired(status="claimed", claim_expires_at=None, now=now) is True
    assert claim_is_active(status="claimed", claim_expires_at=None, now=now) is False


def test_claimed_past_ttl_is_expired():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    expires = now - timedelta(seconds=1)
    assert is_claim_expired(status="claimed", claim_expires_at=expires, now=now) is True


def test_claimed_future_ttl_is_active():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    expires = now + timedelta(minutes=15)
    assert is_claim_expired(status="claimed", claim_expires_at=expires, now=now) is False
    assert claim_is_active(status="claimed", claim_expires_at=expires, now=now) is True


def test_naive_expiry_compared_as_utc():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    expires = datetime(2026, 8, 20, 11, 0)  # naive, treated as UTC
    assert is_claim_expired(status="claimed", claim_expires_at=expires, now=now) is True
