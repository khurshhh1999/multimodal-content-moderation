#!/usr/bin/env python3
"""Generate small demo PNGs for local ingest."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "samples"


def make(name: str, color: tuple[int, int, int], label: str) -> None:
    img = Image.new("RGB", (640, 480), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 40, 600, 440), outline=(255, 255, 255), width=4)
    draw.text((70, 210), label, fill=(255, 255, 255))
    img.save(ROOT / name)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    make("safe_sunset.png", (232, 140, 72), "SAFE SUNSET")
    make("safe_forest.png", (34, 92, 58), "SAFE FOREST")
    make("flag_uncertain.png", (90, 96, 110), "UNCERTAIN")
    make("block_nsfw.png", (160, 40, 80), "NSFW CUE")
    make("block_violence.png", (90, 20, 20), "VIOLENCE CUE")
    print(f"Wrote samples to {ROOT}")


if __name__ == "__main__":
    main()
