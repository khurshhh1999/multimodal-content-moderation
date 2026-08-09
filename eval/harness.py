#!/usr/bin/env python3
"""Offline eval harness against local vision+rules adapters.

Expand `eval/labeled_set/` via `scripts/build_labeled_set.py`.
Do NOT invent résumé metrics — paste output from this script into README.
"""

from __future__ import annotations

import argparse
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


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run labeled-set eval harness")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N samples (smoke / quick runs)",
    )
    parser.add_argument(
        "--min-n",
        type=int,
        default=1,
        help="Fail if fewer than N samples were evaluated",
    )
    args = parser.parse_args(argv)

    manifest_path = ROOT / "eval" / "labeled_set" / "manifest.json"
    if not manifest_path.exists():
        print(
            f"Missing {manifest_path}; run: "
            "python scripts/generate_samples.py && python scripts/build_labeled_set.py"
        )
        return 1

    manifest = json.loads(manifest_path.read_text())
    samples = list(manifest["samples"])
    if args.limit is not None:
        if args.limit < 1:
            print("error: --limit must be >= 1", file=sys.stderr)
            return 2
        samples = samples[: args.limit]

    vision = LocalHeuristicVision()
    llm = RulesPolicyClassifier()
    thresholds = ThresholdConfig()

    y_true: list[str] = []
    y_pred: list[str] = []
    auto = 0

    for sample in samples:
        image_path = ROOT / sample["image"]
        if not image_path.exists():
            print(
                f"Missing image {image_path}; run: "
                "python scripts/generate_samples.py && python scripts/build_labeled_set.py"
            )
            return 1
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

    if len(y_true) < args.min_n:
        print(
            f"error: evaluated n={len(y_true)} < --min-n {args.min_n}",
            file=sys.stderr,
        )
        return 1

    labels = ["ALLOW", "FLAG", "BLOCK"]
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    accuracy = correct / len(y_true) if y_true else 0.0

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
            "f1": round(_f1(precision, recall), 4),
            "support": sum(1 for a in y_true if a == label),
        }

    macro_p = sum(per_class[label]["precision"] for label in labels) / len(labels)
    macro_r = sum(per_class[label]["recall"] for label in labels) / len(labels)
    macro_f1 = sum(per_class[label]["f1"] for label in labels) / len(labels)
    auto_rate = auto / len(y_true) if y_true else 0.0
    # Manual review reduction vs send-everything baseline
    manual_reduction = auto_rate

    report = {
        "n": len(y_true),
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_p, 4),
        "macro_recall": round(macro_r, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion": {
            "true": dict(Counter(y_true)),
            "pred": dict(Counter(y_pred)),
            "pairs": list(zip(y_true, y_pred)),
        },
        "auto_resolve_rate": round(auto_rate, 4),
        "manual_review_reduction_vs_send_all": round(manual_reduction, 4),
        "note": (
            f"Labeled set n={len(y_true)}. "
            "Cite only these harness numbers in README / résumé bullets."
        ),
    }

    out_dir = ROOT / "eval" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    out_path.write_text(json.dumps(report, indent=2))
    # Compact console view (omit full pair list)
    printable = {k: v for k, v in report.items() if k != "confusion"}
    printable["confusion"] = {
        "true": report["confusion"]["true"],
        "pred": report["confusion"]["pred"],
    }
    print(json.dumps(printable, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
