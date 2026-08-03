from __future__ import annotations

import redis.asyncio as redis

from .config import get_settings

_client: redis.Redis | None = None


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
