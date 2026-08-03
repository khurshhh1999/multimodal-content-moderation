from __future__ import annotations

import logging
import re
from typing import Protocol

from moderation_shared import Decision, LlmSignals, VisionSignals

from ..config import Settings

logger = logging.getLogger(__name__)

BLOCK_PATTERNS = [
    r"\b(kill yourself|kys)\b",
    r"\b(nazi|white power)\b",
    r"\b(child porn|csam)\b",
]
FLAG_PATTERNS = [
    r"\b(hate|racist|slur)\b",
    r"\b(threat|bomb|terror)\b",
    r"\b(drugs? for sale|buy cocaine)\b",
]


class LlmAdapter(Protocol):
    def classify(
        self,
        *,
        caption: str,
        vision: VisionSignals,
    ) -> tuple[Decision, LlmSignals]: ...


class RulesPolicyClassifier:
    """Policy-aware rules fusion over caption + OCR + vision labels (Phase 1)."""

    model_version = "rules-v1"

    def classify(
        self,
        *,
        caption: str,
        vision: VisionSignals,
    ) -> tuple[Decision, LlmSignals]:
        text = f"{caption}\n{vision.ocr_text}".lower()
        reasons: list[str] = []

        for pat in BLOCK_PATTERNS:
            if re.search(pat, text, re.I):
                reasons.append(f"block_pattern:{pat}")
                signals = LlmSignals(
                    label="BLOCK",
                    score=0.97,
                    rationale="Matched hard policy block pattern",
                    provider="rules",
                    model_version=self.model_version,
                    raw={"reasons": reasons},
                )
                return Decision.BLOCK, signals

        for pat in FLAG_PATTERNS:
            if re.search(pat, text, re.I):
                reasons.append(f"flag_pattern:{pat}")
                signals = LlmSignals(
                    label="FLAG",
                    score=0.72,
                    rationale="Matched soft policy flag pattern",
                    provider="rules",
                    model_version=self.model_version,
                    raw={"reasons": reasons},
                )
                return Decision.FLAG, signals

        # Fuse vision risk into suggested decision
        risk = max(vision.nsfw_score, vision.violence_score)
        if risk >= 0.85:
            return Decision.BLOCK, LlmSignals(
                label="BLOCK",
                score=min(0.99, 0.7 + risk * 0.25),
                rationale="High vision risk fused into block",
                provider="rules",
                model_version=self.model_version,
                raw={"vision_risk": risk},
            )
        if risk >= 0.45:
            return Decision.FLAG, LlmSignals(
                label="FLAG",
                score=0.55 + risk * 0.2,
                rationale="Elevated vision risk needs human review",
                provider="rules",
                model_version=self.model_version,
                raw={"vision_risk": risk},
            )

        # Ambiguous / short captions → lower confidence allow
        conf = 0.91 if len(caption.strip()) > 8 else 0.78
        if "uncertain" in text or "maybe" in text:
            conf = 0.62
            return Decision.FLAG, LlmSignals(
                label="FLAG",
                score=conf,
                rationale="Ambiguous language — route to human",
                provider="rules",
                model_version=self.model_version,
                raw={},
            )

        return Decision.ALLOW, LlmSignals(
            label="ALLOW",
            score=conf,
            rationale="No policy hits; vision risk low",
            provider="rules",
            model_version=self.model_version,
            raw={},
        )


class OpenAIPolicyClassifier:
    """Optional OpenAI policy classifier (Phase 2). Falls back to rules."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._rules = RulesPolicyClassifier()
        self.model_version = settings.openai_model

    def classify(
        self,
        *,
        caption: str,
        vision: VisionSignals,
    ) -> tuple[Decision, LlmSignals]:
        if not self.settings.openai_api_key:
            return self._rules.classify(caption=caption, vision=vision)
        try:
            import json
            import urllib.request

            prompt = {
                "caption": caption,
                "ocr_text": vision.ocr_text,
                "vision_labels": vision.labels,
                "nsfw_score": vision.nsfw_score,
                "violence_score": vision.violence_score,
                "instruction": (
                    "Classify UGC as ALLOW, FLAG, or BLOCK for a general social app. "
                    "Return JSON: {decision, confidence, rationale}"
                ),
            }
            body = json.dumps(
                {
                    "model": self.settings.openai_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a content policy classifier. Reply with JSON only.",
                        },
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
            ).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode())
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            decision = Decision(parsed["decision"].upper())
            score = float(parsed.get("confidence", 0.7))
            return decision, LlmSignals(
                label=decision.value,
                score=score,
                rationale=str(parsed.get("rationale", "")),
                provider="openai",
                model_version=self.model_version,
                raw=parsed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI classify failed, using rules: %s", exc)
            decision, signals = self._rules.classify(caption=caption, vision=vision)
            signals.raw["openai_fallback_error"] = str(exc)
            return decision, signals


def get_llm_adapter(settings: Settings) -> LlmAdapter:
    if settings.llm_provider.lower() == "openai":
        return OpenAIPolicyClassifier(settings)
    return RulesPolicyClassifier()
