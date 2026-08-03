#!/usr/bin/env python3
"""Offline eval harness against local vision+rules adapters.

Phase 3 will expand the labeled set and report measured precision/recall.
Do NOT invent résumé metrics — paste output from this script into README.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "worker"))
sys.path.insert(0, str(ROOT / "packages" / "moderation_shared" / "src"))

from moderation_shared import ThresholdConfig, route_decision  # noqa: E402
from worker.adapters.llm import RulesPolicyClassifier  # noqa: E402
from worker.adapters.vision import LocalHeuristicVision  # noqa: E402


def main() -> None:
    manifest_path = ROOT / "eval" / "labeled_set" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    vision = LocalHeuristicVision()
    llm = RulesPolicyClassifier()
    thresholds = ThresholdConfig()

    y_true: list[str] = []
    y_pred: list[str] = []
    auto = 0

    for sample in manifest["samples"]:
        image_path = ROOT / sample["image"]
        if not image_path.exists():
            print(f"Missing image {image_path}; run scripts/generate_samples.py")
            sys.exit(1)
        image_bytes = image_path.read_bytes()
        caption = sample["caption"]
        v = vision.analyze(image_bytes, caption)
        suggested, signals = llm.classify(caption=caption, vision=v)
        final, needs_review, _ = route_decision(
            suggested=suggested,
            confidence=signals.score,
            nsfw_score=v.nsfw_score,
            violence_score=v.violence_score,
            thresholds=thresholds,
        )
        y_true.append(sample["label"])
        y_pred.append(final.value)
        if not needs_review:
            auto += 1

    labels = ["ALLOW", "FLAG", "BLOCK"]
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    accuracy = correct / len(y_true) if y_true else 0.0

    # Precision for BLOCK+FLAG as "actioned" vs ALLOW — and per-class precision
    per_class = {}
    for label in labels:
        tp = sum(1 for a, b in zip(y_true, y_pred) if a == label and b == label)
        fp = sum(1 for a, b in zip(y_true, y_pred) if a != label and b == label)
        fn = sum(1 for a, b in zip(y_true, y_pred) if a == label and b != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "support": sum(1 for a in y_true if a == label),
        }

    # Macro precision (unweighted)
    macro_p = sum(per_class[l]["precision"] for l in labels) / len(labels)
    auto_rate = auto / len(y_true) if y_true else 0.0
    # Manual review reduction vs send-everything baseline
    manual_reduction = auto_rate

    report = {
        "n": len(y_true),
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_p, 4),
        "per_class": per_class,
        "confusion": {
            "true": dict(Counter(y_true)),
            "pred": dict(Counter(y_pred)),
            "pairs": list(zip(y_true, y_pred)),
        },
        "auto_resolve_rate": round(auto_rate, 4),
        "manual_review_reduction_vs_send_all": round(manual_reduction, 4),
        "note": "Small smoke set (n=5). Expand labeled_set before citing résumé metrics.",
    }

    out_dir = ROOT / "eval" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
