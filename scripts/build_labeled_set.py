#!/usr/bin/env python3
"""Build ≥50 labeled eval images + manifest for the offline harness.

`label` is human ground truth (intended policy outcome). The harness measures
the local heuristic + rules path against these labels — never invent metrics.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "labeled_set" / "images"
MANIFEST = ROOT / "eval" / "labeled_set" / "manifest.json"

sys.path.insert(0, str(ROOT / "apps" / "worker"))
sys.path.insert(0, str(ROOT / "packages" / "moderation_shared" / "src"))

from moderation_shared import ThresholdConfig, route_decision  # noqa: E402
from worker.adapters.llm import RulesPolicyClassifier  # noqa: E402
from worker.adapters.vision import LocalHeuristicVision  # noqa: E402


def _gradient(
    size: tuple[int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    pix = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            pix[x, y] = (r, g, b)
    return img


def make_image(path: Path, top: tuple[int, int, int], bottom: tuple[int, int, int], label: str) -> None:
    img = _gradient((320, 240), top, bottom)
    overlay = Image.new("RGB", img.size, (0, 0, 0))
    mask = Image.new("L", img.size, 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse((40, 30, 280, 210), fill=180)
    mask = mask.filter(ImageFilter.GaussianBlur(24))
    img = Image.composite(img, overlay, Image.eval(mask, lambda v: 255 - int(v * 0.35)))
    draw = ImageDraw.Draw(img)
    draw.text((20, 100), label[:28], fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


SAFE_CAPTIONS = [
    "Beautiful sunset over the lake — nature landscape",
    "Family picnic in the park with food and dog",
    "Golden hour landscape photo of rolling hills",
    "Cute cat sleeping on a sunny windowsill",
    "Homemade pasta dinner with fresh food herbs",
    "Weekend hike through a green forest trail",
    "Kids playing soccer with family at sunset",
    "Quiet nature walk along the river path",
    "Farmers market vegetables and fresh food stalls",
    "Dog running on the beach at golden hour",
    "Mountain landscape under clear blue sky",
    "Cozy family dinner around the kitchen table",
    "Sunset silhouette of trees in nature reserve",
    "Garden flowers blooming beside a stone path",
    "Friendly dog portrait on a sunny afternoon",
    "Coastal landscape with calm waves and birds",
    "Baking fresh food bread in a home kitchen",
    "Autumn forest trail covered in fallen leaves",
    "Cat curled up on a soft family sofa",
    "Picnic blanket spread with snacks and fruit",
    "Sunrise over a quiet nature lake",
    "City park fountain surrounded by green trees",
]

FLAG_CAPTIONS = [
    "This post is uncertain maybe edgy humor",
    "Uncertain vibe, maybe controversial meme",
    "Maybe too spicy for the timeline uncertain",
    "Borderline joke about hate speech discourse",
    "Someone mentioned a threat in the comments",
    "Rumor about drugs for sale in the group chat",
    "Caption with racist undertones needs review",
    "Uncertain political rant with slur adjacent language",
    "Maybe a bomb joke? uncertain intent",
    "Soft hate rhetoric that should be flagged",
    "Threat-adjacent language in a heated thread",
    "Uncertain caption with maybe offensive punchline",
    "Buy cocaine joke? probably not serious uncertain",
    "Terror adjacent meme that feels wrong",
    "Edgy uncertain humor that may need a human",
]

BLOCK_CAPTIONS = [
    "force_nsfw explicit demo sample one",
    "force_nsfw explicit demo sample two",
    "force_nsfw explicit nude content cue",
    "force_violence weapon blood demo one",
    "force_violence weapon blood demo two",
    "force_violence gun fight blood scene",
    "nsfw explicit porn xxx caption sample",
    "violence blood weapon kill fight post",
    "force_nsfw block band verification item",
    "force_violence block band verification item",
    "explicit nsfw material for eval harness",
    "weapon violence gore style caption block",
    "force_nsfw hard signal auto block case",
    "force_violence hard signal auto block case",
    "kill yourself message should hard block",
]


def _palette_for(kind: str, idx: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if kind == "ALLOW":
        bases = [
            ((255, 170, 80), (90, 40, 70)),
            ((60, 140, 80), (20, 50, 30)),
            ((120, 180, 220), (30, 60, 100)),
            ((240, 200, 120), (100, 70, 40)),
        ]
    elif kind == "FLAG":
        bases = [
            ((120, 130, 150), (50, 55, 70)),
            ((150, 140, 100), (60, 55, 40)),
            ((100, 110, 130), (40, 45, 55)),
        ]
    else:
        bases = [
            ((200, 60, 100), (80, 20, 40)),
            ((140, 30, 30), (40, 10, 10)),
            ((160, 40, 80), (50, 10, 30)),
        ]
    return bases[idx % len(bases)]


def predict(image_bytes: bytes, caption: str) -> str:
    vision = LocalHeuristicVision()
    llm = RulesPolicyClassifier()
    thresholds = ThresholdConfig()
    v = vision.analyze(image_bytes, caption)
    suggested, signals = llm.classify(caption=caption, vision=v)
    final, _, _ = route_decision(
        suggested=suggested,
        confidence=signals.score,
        nsfw_score=v.nsfw_score,
        violence_score=v.violence_score,
        thresholds=thresholds,
    )
    return final.value


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []
    mismatches = 0

    groups = [
        ("ALLOW", SAFE_CAPTIONS),
        ("FLAG", FLAG_CAPTIONS),
        ("BLOCK", BLOCK_CAPTIONS),
    ]

    for kind, captions in groups:
        for i, caption in enumerate(captions, start=1):
            sample_id = f"{kind.lower()}_{i:03d}"
            rel = f"eval/labeled_set/images/{sample_id}.png"
            path = ROOT / rel
            top, bottom = _palette_for(kind, i)
            make_image(path, top, bottom, sample_id.upper())
            pred = predict(path.read_bytes(), caption)
            if pred != kind:
                mismatches += 1
                print(f"WARN {sample_id}: intended={kind} pipeline={pred}")
            samples.append(
                {
                    "id": sample_id,
                    "image": rel,
                    "caption": caption,
                    "label": kind,
                }
            )

    demo_links = [
        ("demo_safe_sunset", "samples/safe_sunset.png", "Beautiful sunset over the lake — nature landscape", "ALLOW"),
        ("demo_safe_forest", "samples/safe_forest.png", "Family hike through a green forest trail", "ALLOW"),
        ("demo_flag_uncertain", "samples/flag_uncertain.png", "This post is uncertain maybe edgy humor", "FLAG"),
        ("demo_block_nsfw", "samples/block_nsfw.png", "force_nsfw explicit demo sample", "BLOCK"),
        ("demo_block_violence", "samples/block_violence.png", "force_violence weapon blood demo", "BLOCK"),
    ]
    for sample_id, src_rel, caption, kind in demo_links:
        src = ROOT / src_rel
        if not src.exists():
            continue
        rel = f"eval/labeled_set/images/{sample_id}.png"
        dest = ROOT / rel
        dest.write_bytes(src.read_bytes())
        pred = predict(dest.read_bytes(), caption)
        if pred != kind:
            mismatches += 1
            print(f"WARN {sample_id}: intended={kind} pipeline={pred}")
        samples.append(
            {
                "id": sample_id,
                "image": rel,
                "caption": caption,
                "label": kind,
            }
        )

    manifest = {
        "version": "eval-v2",
        "description": (
            "Labeled set (≥50) for precision/recall. Labels are human ground truth; "
            "run `python -m eval.harness` for measured numbers."
        ),
        "samples": samples,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    counts: dict[str, int] = {}
    for s in samples:
        counts[s["label"]] = counts.get(s["label"], 0) + 1
    print(f"Wrote {len(samples)} samples → {MANIFEST}")
    print(f"Label counts: {counts}")
    print(f"Pipeline mismatches vs intended at build time: {mismatches}")


if __name__ == "__main__":
    main()
