from __future__ import annotations

import io
import logging
from typing import Protocol

from moderation_shared import VisionSignals
from PIL import Image, ImageStat

from ..config import Settings

logger = logging.getLogger(__name__)

# GCP Vision Likelihood enum → score (UNKNOWN/VERY_UNLIKELY…VERY_LIKELY)
_GCP_LIKELIHOOD_SCORE = {
    0: 0.0,  # UNKNOWN
    1: 0.05,  # VERY_UNLIKELY
    2: 0.2,  # UNLIKELY
    3: 0.45,  # POSSIBLE
    4: 0.75,  # LIKELY
    5: 0.95,  # VERY_LIKELY
}


def gcp_likelihood_score(value: object) -> float:
    """Map a GCP Likelihood enum/int/name to a 0–1 score."""
    if value is None:
        return 0.0
    if hasattr(value, "value"):
        return _GCP_LIKELIHOOD_SCORE.get(int(value.value), 0.0)
    if isinstance(value, int):
        return _GCP_LIKELIHOOD_SCORE.get(value, 0.0)
    name = str(value).upper().split(".")[-1]
    mapping = {
        "UNKNOWN": 0.0,
        "VERY_UNLIKELY": 0.05,
        "UNLIKELY": 0.2,
        "POSSIBLE": 0.45,
        "LIKELY": 0.75,
        "VERY_LIKELY": 0.95,
    }
    return mapping.get(name, 0.0)


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
    """AWS Rekognition adapter. Falls back to local if boto call fails."""

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
    """GCP Vision API adapter (Safe Search + labels + OCR).

    Uses Application Default Credentials / GOOGLE_APPLICATION_CREDENTIALS.
    Falls back to the local heuristic when the SDK or credentials are unavailable.
    """

    model_version = "gcp-vision-safe-search-v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._local = LocalHeuristicVision()

    def analyze(self, image_bytes: bytes, caption: str) -> VisionSignals:
        try:
            from google.cloud import vision  # type: ignore

            client_kwargs: dict = {}
            if self.settings.google_application_credentials:
                client_kwargs["credentials"] = _load_gcp_credentials(
                    self.settings.google_application_credentials
                )
            client = vision.ImageAnnotatorClient(**client_kwargs)
            image = vision.Image(content=image_bytes)

            # Batch features in one round-trip when possible
            features = [
                vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
                vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=10),
                vision.Feature(type_=vision.Feature.Type.TEXT_DETECTION),
            ]
            request = vision.AnnotateImageRequest(image=image, features=features)
            response = client.annotate_image(request=request)
            if response.error.message:
                raise RuntimeError(response.error.message)

            safe = response.safe_search_annotation
            nsfw = max(
                gcp_likelihood_score(safe.adult),
                gcp_likelihood_score(safe.racy),
            )
            violence = gcp_likelihood_score(safe.violence)

            label_names = [lab.description for lab in (response.label_annotations or [])]
            likelihood_labels = [
                f"adult:{_likeli_name(safe.adult)}",
                f"racy:{_likeli_name(safe.racy)}",
                f"violence:{_likeli_name(safe.violence)}",
                f"spoof:{_likeli_name(safe.spoof)}",
                f"medical:{_likeli_name(safe.medical)}",
            ]
            labels = likelihood_labels + label_names

            texts = response.text_annotations or []
            ocr = texts[0].description if texts else ""

            return VisionSignals(
                labels=labels or ["gcp_clean"],
                nsfw_score=round(nsfw, 4),
                violence_score=round(violence, 4),
                ocr_text=(ocr or "")[:2000],
                provider="gcp",
                model_version=self.model_version,
                raw={
                    "safe_search": {
                        "adult": _likeli_name(safe.adult),
                        "racy": _likeli_name(safe.racy),
                        "violence": _likeli_name(safe.violence),
                        "spoof": _likeli_name(safe.spoof),
                        "medical": _likeli_name(safe.medical),
                    },
                    "label_annotations": label_names,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GCP Vision unavailable, using local: %s", exc)
            signals = self._local.analyze(image_bytes, caption)
            signals.raw["gcp_fallback_error"] = str(exc)
            return signals


def _likeli_name(value: object) -> str:
    if value is None:
        return "UNKNOWN"
    if hasattr(value, "name"):
        return str(value.name)
    return str(value)


def _load_gcp_credentials(path: str):
    from google.oauth2 import service_account  # type: ignore

    return service_account.Credentials.from_service_account_file(path)


def get_vision_adapter(settings: Settings) -> VisionAdapter:
    provider = settings.vision_provider.lower()
    if provider == "aws":
        return AwsRekognitionVision(settings)
    if provider == "gcp":
        return GcpVisionAdapter(settings)
    return LocalHeuristicVision()
