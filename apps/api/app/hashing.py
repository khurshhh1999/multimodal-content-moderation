from __future__ import annotations

import hashlib


def content_hash(image_bytes: bytes, caption: str, policy_version: str) -> str:
    """Stable idempotency key for (bytes, caption, policy)."""
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(b"\x00")
    h.update(caption.strip().encode("utf-8"))
    h.update(b"\x00")
    h.update(policy_version.encode("utf-8"))
    return h.hexdigest()
