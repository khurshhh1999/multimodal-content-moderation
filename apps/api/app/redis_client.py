from __future__ import annotations

import redis.asyncio as redis

from .config import get_settings

_client: redis.Redis | None = None

# Default claim lock TTL — keep in sync with review claim window
CLAIM_TTL_SECONDS = 15 * 60


async def init_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_redis() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis not initialized")
    return _client


async def acquire_ingest_lock(content_hash: str, ttl_seconds: int = 120) -> bool:
    """Return True if this caller owns the ingest for content_hash."""
    key = f"ingest:{content_hash}"
    return bool(await get_redis().set(key, "1", nx=True, ex=ttl_seconds))


def _claim_key(review_id: str) -> str:
    return f"claim:{review_id}"


async def acquire_review_claim(
    review_id: str,
    reviewer: str,
    ttl_seconds: int = CLAIM_TTL_SECONDS,
) -> tuple[bool, str | None]:
    """Acquire or refresh a Redis claim lock.

    Returns ``(ok, owner)``. ``ok`` is True when this reviewer owns the lock.
    On conflict, ``owner`` is the other reviewer holding the lock.
    """
    r = get_redis()
    key = _claim_key(review_id)
    current = await r.get(key)
    if current is None:
        acquired = await r.set(key, reviewer, nx=True, ex=ttl_seconds)
        if acquired:
            return True, reviewer
        current = await r.get(key)
    if current == reviewer:
        await r.expire(key, ttl_seconds)
        return True, reviewer
    return False, current


async def release_review_claim(review_id: str, reviewer: str) -> None:
    """Drop the claim lock if owned by this reviewer."""
    r = get_redis()
    key = _claim_key(review_id)
    current = await r.get(key)
    if current == reviewer:
        await r.delete(key)


async def get_review_claim_owner(review_id: str) -> str | None:
    return await get_redis().get(_claim_key(review_id))
