#!/usr/bin/env python3
"""Generate demo PNGs for local ingest / seed."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1] / "samples"


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
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


def make(name: str, top: tuple[int, int, int], bottom: tuple[int, int, int], label: str) -> None:
    img = _gradient((640, 480), top, bottom)
    # Soft vignette-ish blur of a dark oval for a less flat look
    overlay = Image.new("RGB", img.size, (0, 0, 0))
    mask = Image.new("L", img.size, 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse((80, 60, 560, 420), fill=180)
    mask = mask.filter(ImageFilter.GaussianBlur(40))
    img = Image.composite(img, overlay, Image.eval(mask, lambda v: 255 - int(v * 0.35)))
    draw = ImageDraw.Draw(img)
    draw.rectangle((36, 36, 604, 444), outline=(255, 255, 255), width=3)
    draw.text((70, 210), label, fill=(255, 255, 255))
    img.save(ROOT / name)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    make("safe_sunset.png", (255, 170, 80), (90, 40, 70), "SAFE SUNSET")
    make("safe_forest.png", (60, 140, 80), (20, 50, 30), "SAFE FOREST")
    make("flag_uncertain.png", (120, 130, 150), (50, 55, 70), "UNCERTAIN")
    make("block_nsfw.png", (200, 60, 100), (80, 20, 40), "NSFW CUE")
    make("block_violence.png", (140, 30, 30), (40, 10, 10), "VIOLENCE CUE")
    print(f"Wrote samples to {ROOT}")


if __name__ == "__main__":
    main()
