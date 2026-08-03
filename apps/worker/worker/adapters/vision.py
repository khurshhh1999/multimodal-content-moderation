from __future__ import annotations

import io
import logging
from typing import Protocol

from moderation_shared import VisionSignals
from PIL import Image, ImageStat

from ..config import Settings

logger = logging.getLogger(__name__)


class VisionAdapter(Protocol):
    def analyze(self, image_bytes: bytes, caption: str) -> VisionSignals: ...


class LocalHeuristicVision:
    """Deterministic local vision stub for demos without cloud credentials.

    Uses simple image stats + caption cues. Real AWS/GCP adapters replace this
    behind VISION_PROVIDER.
    """

    model_version = "local-heuristic-v1"

    def analyze(self, image_bytes: bytes, caption: str) -> VisionSignals:
        labels: list[str] = []
        nsfw = 0.05
        violence = 0.05
        ocr_text = ""
        brightness: float | None = None

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            w, h = img.size
            labels.append(f"resolution:{w}x{h}")
            stat = ImageStat.Stat(img)
            # High red dominance + low brightness → soft violence cue (demo only)
            r, g, b = stat.mean
            brightness = (r + g + b) / 3.0
            if r > g + 25 and r > b + 25:
                labels.append("warm_tones")
                violence = max(violence, 0.25)
            if brightness < 50:
                labels.append("dark_scene")
                violence = max(violence, 0.35)
            if brightness > 200:
                labels.append("bright_scene")
            # Extreme saturation proxy via channel variance
            variance = sum(stat.var) / 3.0
            if variance > 4000:
                labels.append("high_contrast")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PIL analysis failed: %s", exc)
            labels.append("unreadable_image")
            return VisionSignals(
                labels=labels,
                nsfw_score=0.5,
                violence_score=0.5,
                ocr_text="",
                provider="local",
                model_version=self.model_version,
                raw={"error": str(exc)},
            )

        cap = caption.lower()
        # Caption-driven OCR simulation for demo labeled set
        if "ocr:" in cap:
            ocr_text = cap.split("ocr:", 1)[1].strip()[:500]
        else:
            ocr_text = ""

        nsfw_terms = ("nsfw", "nude", "explicit", "porn", "xxx")
        violence_terms = ("violence", "blood", "weapon", "gun", "kill", "fight")
        safe_terms = ("nature", "landscape", "food", "cat", "dog", "family", "sunset")

        if any(t in cap for t in nsfw_terms):
            nsfw = max(nsfw, 0.92)
            labels.append("caption_nsfw_cue")
        if any(t in cap for t in violence_terms):
            violence = max(violence, 0.88)
            labels.append("caption_violence_cue")
        if any(t in cap for t in safe_terms):
            labels.append("caption_safe_cue")
            nsfw = min(nsfw, 0.08)
            violence = min(violence, 0.08)

        # Demo: filenames/captions can force bands
        if "force_nsfw" in cap:
            nsfw = 0.95
            labels.append("force_nsfw")
        if "force_violence" in cap:
            violence = 0.93
            labels.append("force_violence")

        return VisionSignals(
            labels=labels,
            nsfw_score=round(nsfw, 4),
            violence_score=round(violence, 4),
            ocr_text=ocr_text,
            provider="local",
            model_version=self.model_version,
            raw={"brightness": brightness},
        )


class AwsRekognitionVision:
    """AWS Rekognition adapter (Phase 2). Falls back to local if boto call fails."""

    model_version = "rekognition-moderation-v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = LocalHeuristicVision()

    def analyze(self, image_bytes: bytes, caption: str) -> VisionSignals:
        try:
            import boto3

            client = boto3.client(
                "rekognition",
                region_name=self.settings.aws_default_region,
                aws_access_key_id=self.settings.aws_access_key_id,
                aws_secret_access_key=self.settings.aws_secret_access_key,
            )
            resp = client.detect_moderation_labels(Image={"Bytes": image_bytes}, MinConfidence=50)
            labels = [x["Name"] for x in resp.get("ModerationLabels", [])]
            nsfw = 0.0
            violence = 0.0
            for item in resp.get("ModerationLabels", []):
                name = item["Name"].lower()
                conf = float(item["Confidence"]) / 100.0
                if any(k in name for k in ("explicit", "nudity", "suggestive", "sexual")):
                    nsfw = max(nsfw, conf)
                if any(k in name for k in ("violence", "blood", "weapon", "gore")):
                    violence = max(violence, conf)
            text_resp = client.detect_text(Image={"Bytes": image_bytes})
            ocr = " ".join(
                d["DetectedText"]
                for d in text_resp.get("TextDetections", [])
                if d.get("Type") == "LINE"
            )
            return VisionSignals(
                labels=labels or ["rekognition_clean"],
                nsfw_score=round(nsfw, 4),
                violence_score=round(violence, 4),
                ocr_text=ocr[:2000],
                provider="aws",
                model_version=self.model_version,
                raw={"moderation_labels": resp.get("ModerationLabels", [])},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rekognition unavailable, using local: %s", exc)
            signals = self._local.analyze(image_bytes, caption)
            signals.raw["aws_fallback_error"] = str(exc)
            return signals


class GcpVisionAdapter:
    """GCP Vision API adapter (Phase 3). Falls back to local without credentials."""

    model_version = "gcp-vision-safe-search-v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = LocalHeuristicVision()

    def analyze(self, image_bytes: bytes, caption: str) -> VisionSignals:
        try:
            from google.cloud import vision  # type: ignore

            client = vision.ImageAnnotatorClient()
            image = vision.Image(content=image_bytes)
            safe = client.safe_search_detection(image=image).safe_search_annotation
            texts = client.text_detection(image=image).text_annotations
            ocr = texts[0].description if texts else ""

            def likeli(v) -> float:
                mapping = {
                    0: 0.0,
                    1: 0.1,
                    2: 0.3,
                    3: 0.6,
                    4: 0.85,
                    5: 0.98,
                }
                return mapping.get(int(v), 0.0)

            nsfw = max(likeli(safe.adult), likeli(safe.racy))
            violence = likeli(safe.violence)
            labels = [
                f"adult:{safe.adult.name}",
                f"racy:{safe.racy.name}",
                f"violence:{safe.violence.name}",
            ]
            return VisionSignals(
                labels=labels,
                nsfw_score=round(nsfw, 4),
                violence_score=round(violence, 4),
                ocr_text=(ocr or "")[:2000],
                provider="gcp",
                model_version=self.model_version,
                raw={},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GCP Vision unavailable, using local: %s", exc)
            signals = self._local.analyze(image_bytes, caption)
            signals.raw["gcp_fallback_error"] = str(exc)
            return signals


def get_vision_adapter(settings: Settings) -> VisionAdapter:
    provider = settings.vision_provider.lower()
    if provider == "aws":
        return AwsRekognitionVision(settings)
    if provider == "gcp":
        return GcpVisionAdapter(settings)
    return LocalHeuristicVision()
