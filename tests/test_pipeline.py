from __future__ import annotations

import asyncio
from uuid import uuid4

from moderation_shared import Decision, DecisionEnvelope, LlmSignals, VisionSignals

from worker.config import Settings
from worker.pipeline import (
    ValidationError,
    persist_envelope,
    review_priority,
    validate,
)


def test_validate_rejects_empty_and_wrong_type():
    settings = Settings(max_upload_bytes=1024)
    try:
        validate(b"", "image/png", settings)
        raise AssertionError("empty should fail")
    except ValidationError as exc:
        assert "empty_object" in str(exc)

    try:
        validate(b"abc", "application/pdf", settings)
        raise AssertionError("pdf should fail")
    except ValidationError as exc:
        assert "invalid_content_type" in str(exc)


def test_validate_rejects_oversize():
    settings = Settings(max_upload_bytes=4)
    try:
        validate(b"12345", "image/jpeg", settings)
        raise AssertionError("oversize should fail")
    except ValidationError as exc:
        assert "object_too_large" in str(exc)


def test_validate_accepts_png():
    settings = Settings(max_upload_bytes=1024)
    assert validate(b"not-empty", "image/png", settings) == ["validation_ok"]


def test_review_priority_blocks_before_flags():
    assert review_priority("BLOCK", 0.95) == 10
    assert review_priority("FLAG", 0.80) == 50
    assert review_priority("FLAG", 0.40) == 20
    assert review_priority("BLOCK", 0.40) == 10


class _TxnConn:
    def __init__(self, *, conflict: bool = False):
        self.conflict = conflict
        self.in_txn = False
        self.began = 0
        self.ops: list[str] = []
        self._decision_id = uuid4()

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                conn.in_txn = True
                conn.began += 1
                conn.ops.append("begin")
                return self

            async def __aexit__(self, exc_type, exc, tb):
                conn.ops.append("rollback" if exc_type else "commit")
                conn.in_txn = False
                return False

        return _Txn()

    async def fetchval(self, sql: str, *args):
        assert self.in_txn, "fetchval must run inside a transaction"
        self.ops.append("insert_decision")
        if self.conflict:
            return None
        return self._decision_id

    async def fetchrow(self, sql: str, *args):
        assert self.in_txn
        self.ops.append("fetch_existing")
        existing = DecisionEnvelope(
            job_id=args[0],
            content_id=uuid4(),
            content_hash="abc",
            decision=Decision.ALLOW,
            confidence=0.99,
        )
        return {"envelope": existing.model_dump(mode="json")}

    async def execute(self, sql: str, *args):
        assert self.in_txn, "execute must run inside a transaction"
        lowered = sql.lower()
        if "update jobs" in lowered:
            self.ops.append("update_job")
        elif "metrics_events" in lowered:
            self.ops.append("metrics")
        elif "review_queue" in lowered:
            self.ops.append("review_queue")
        elif "audit_log" in lowered:
            self.ops.append("audit")
        else:
            self.ops.append("execute")
        return "OK"


def _flag_envelope() -> DecisionEnvelope:
    return DecisionEnvelope(
        job_id=uuid4(),
        content_id=uuid4(),
        content_hash="deadbeef",
        decision=Decision.FLAG,
        confidence=0.55,
        reasons=["soft_signal_flag_band"],
        vision=VisionSignals(nsfw_score=0.5, provider="local"),
        llm=LlmSignals(label="FLAG", score=0.55, provider="rules"),
        needs_human_review=True,
    )


def test_persist_envelope_writes_review_inside_transaction():
    conn = _TxnConn()
    envelope = _flag_envelope()
    stored = asyncio.run(persist_envelope(conn, envelope))
    assert stored is envelope
    assert conn.began == 1
    assert conn.ops[0] == "begin"
    assert conn.ops[-1] == "commit"
    assert "review_queue" in conn.ops
    assert "update_job" in conn.ops
    assert not conn.in_txn


def test_persist_envelope_skips_review_for_auto_allow():
    conn = _TxnConn()
    envelope = DecisionEnvelope(
        job_id=uuid4(),
        content_id=uuid4(),
        content_hash="cafe",
        decision=Decision.ALLOW,
        confidence=0.93,
        needs_human_review=False,
    )
    asyncio.run(persist_envelope(conn, envelope))
    assert "review_queue" not in conn.ops
    assert "update_job" in conn.ops


def test_persist_envelope_returns_winner_on_conflict():
    conn = _TxnConn(conflict=True)
    envelope = _flag_envelope()
    stored = asyncio.run(persist_envelope(conn, envelope))
    assert stored is not envelope
    assert stored.decision == Decision.ALLOW
    assert "fetch_existing" in conn.ops
    assert "review_queue" not in conn.ops
