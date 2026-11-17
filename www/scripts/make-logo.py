#!/usr/bin/env python3
"""Render the Coy.bot wordmark and favicon.

Writes public/logo-white.png, public/logo-black.png (header/footer/JSON-LD) and
src/app/favicon.ico. Set in Geist, the site's own font (SIL OFL), taken from
the copy Next.js ships in node_modules, so no download is needed. The dot is
the site's amber accent (Tailwind amber-500).

Usage (from www/):  python3 scripts/make-logo.py
Needs Pillow.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WWW = Path(__file__).resolve().parent.parent
FONT = WWW / "node_modules/next/dist/next-devtools/server/font/geist-latin.woff2"
AMBER = (245, 158, 11, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def font(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_axes([weight])
    return f


def wordmark(ink, size: int = 260) -> Image.Image:
    f = font(size)
    parts = [("Coy", ink), (".", AMBER), ("bot", ink)]
    width = int(sum(f.getlength(t) for t, _ in parts)) + 40
    im = Image.new("RGBA", (width, size + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 20.0
    for text, colour in parts:
        d.text((x, 20), text, font=f, fill=colour)
        x += f.getlength(text)
    return im.crop(im.getbbox())


def favicon(px: int = 256) -> Image.Image:
    """White 'C' and amber dot on a black rounded square."""
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, px - 1, px - 1), radius=px // 5, fill=BLACK)
    f = font(int(px * 0.78), 800)
    c_box = f.getbbox("C")
    dot = int(px * 0.13)
    gap = int(px * 0.03)
    total = (c_box[2] - c_box[0]) + gap + dot
    x = (px - total) / 2 - c_box[0]
    y = (px - (c_box[3] - c_box[1])) / 2 - c_box[1]
    d.text((x, y), "C", font=f, fill=WHITE)
    dx = x + c_box[2] + gap
    dy = y + c_box[3] - dot
    d.rectangle((dx, dy, dx + dot, dy + dot), fill=AMBER)
    return im


if __name__ == "__main__":
    for name, ink in [("logo-white.png", WHITE), ("logo-black.png", BLACK)]:
        im = wordmark(ink)
        im.save(WWW / "public" / name, optimize=True)
        print(f"public/{name}", im.size)
    favicon().save(WWW / "src/app/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("src/app/favicon.ico")
