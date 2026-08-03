from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    job_id: UUID
    content_id: UUID
    content_hash: str
    status: str
    deduplicated: bool = False
    message: str = "accepted"


class DecisionOut(BaseModel):
    id: UUID
    job_id: UUID
    content_id: UUID
    content_hash: str
    decision: str
    confidence: float
    reasons: list[str]
    vision_signals: dict[str, Any]
    llm_signals: dict[str, Any]
    policy_version: str
    pipeline_version: str
    latency_ms: int
    needs_human_review: bool
    created_at: datetime
    caption: str | None = None
    image_url: str | None = None


class ReviewItemOut(BaseModel):
    id: UUID
    decision_id: UUID
    content_id: UUID
    job_id: UUID
    status: str
    priority: int
    claimed_by: str | None
    claimed_at: datetime | None
    created_at: datetime
    decision: str
    confidence: float
    reasons: list[str]
    vision_signals: dict[str, Any]
    llm_signals: dict[str, Any]
    caption: str
    image_url: str
    content_type: str


class ClaimRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)


class ResolveRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)
    reviewer_decision: str = Field(pattern="^(ALLOW|BLOCK)$")
    notes: str = Field(default="", max_length=4000)


class MetricsSummary(BaseModel):
    queue_depth: int
    pending_reviews: int
    claimed_reviews: int
    decisions_total: int
    decisions_allow: int
    decisions_flag: int
    decisions_block: int
    auto_resolved: int
    auto_resolve_rate: float
    p95_latency_ms: float | None
    decisions_last_minute: int
