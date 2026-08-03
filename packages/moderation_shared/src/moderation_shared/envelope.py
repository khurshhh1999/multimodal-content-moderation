from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Decision(str, Enum):
    ALLOW = "ALLOW"
    FLAG = "FLAG"
    BLOCK = "BLOCK"


class VisionSignals(BaseModel):
    labels: list[str] = Field(default_factory=list)
    nsfw_score: float = 0.0
    violence_score: float = 0.0
    ocr_text: str = ""
    provider: str = "local"
    model_version: str = "local-heuristic-v1"
    raw: dict[str, Any] = Field(default_factory=dict)


class LlmSignals(BaseModel):
    label: str = "unknown"
    score: float = 0.0
    rationale: str = ""
    provider: str = "rules"
    model_version: str = "rules-v1"
    raw: dict[str, Any] = Field(default_factory=dict)


class DecisionEnvelope(BaseModel):
    job_id: UUID
    content_id: UUID
    content_hash: str
    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    vision: VisionSignals = Field(default_factory=VisionSignals)
    llm: LlmSignals = Field(default_factory=LlmSignals)
    policy_version: str = "policy-v1"
    pipeline_version: str = "pipeline-v1"
    latency_ms: int = 0
    needs_human_review: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_storage(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
