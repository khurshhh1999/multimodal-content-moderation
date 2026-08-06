"""Per-tenant fixed-window rate limiting (Redis INCR + EXPIRE)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .redis_client import get_redis

_TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
DEFAULT_TENANT_ID = "default"


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset_at: int
    count: int


def normalize_tenant_id(raw: str | None, default: str = DEFAULT_TENANT_ID) -> str:
    """Sanitize tenant id from header; fall back to default when missing/invalid."""
    if raw is None:
        return default
    value = raw.strip()
    if not value or not _TENANT_RE.match(value):
        return default
    return value


def evaluate_fixed_window(
    count: int,
    *,
    limit: int,
    window_seconds: int,
    now: int | None = None,
) -> RateLimitDecision:
    """Decide allow/deny from the counter after increment (pure helper for tests)."""
    if limit <= 0:
        # Disabled / unlimited
        return RateLimitDecision(
            allowed=True,
            limit=0,
            remaining=0,
            retry_after=0,
            reset_at=0,
            count=count,
        )
    ts = int(now if now is not None else time.time())
    window = max(1, window_seconds)
    # Align reset to end of current fixed window
    reset_at = ((ts // window) + 1) * window
    retry_after = max(1, reset_at - ts)
    remaining = max(0, limit - count)
    allowed = count <= limit
    if not allowed:
        remaining = 0
    return RateLimitDecision(
        allowed=allowed,
        limit=limit,
        remaining=remaining,
        retry_after=retry_after if not allowed else 0,
        reset_at=reset_at,
        count=count,
    )


def _window_key(tenant_id: str, window_seconds: int, now: int) -> str:
    bucket = now // max(1, window_seconds)
    return f"ratelimit:tenant:{tenant_id}:{bucket}"


async def check_tenant_rate_limit(
    tenant_id: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Increment the tenant counter and return the allow/deny decision."""
    if limit <= 0:
        return evaluate_fixed_window(0, limit=0, window_seconds=window_seconds)

    now = int(time.time())
    key = _window_key(tenant_id, window_seconds, now)
    r = get_redis()
    count = int(await r.incr(key))
    if count == 1:
        await r.expire(key, max(1, window_seconds))
    return evaluate_fixed_window(
        count,
        limit=limit,
        window_seconds=window_seconds,
        now=now,
    )


def rate_limit_headers(decision: RateLimitDecision, tenant_id: str) -> dict[str, str]:
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.reset_at),
    }
    if not decision.allowed and decision.retry_after:
        headers["Retry-After"] = str(decision.retry_after)
    return headers
