"""Draw numbered boxes over a screenshot so a vision model can refer to marks by id."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .models import Mark

COLORS = {
    "button": (230, 60, 60),
    "input": (40, 140, 240),
    "toggle": (150, 80, 220),
    "image": (240, 150, 30),
    "text": (40, 170, 90),
}


def annotate(png: bytes, marks: list[Mark]) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    for mark in marks:
        color = COLORS.get(mark.kind, (90, 90, 90))
        b = mark.bounds
        draw.rectangle([b.left, b.top, b.right - 1, b.bottom - 1], outline=color, width=2)
        text = str(mark.id)
        box = draw.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0] + 6, box[3] - box[1] + 6
        x, y = max(0, b.left), max(0, b.top)
        draw.rectangle([x, y, x + w, y + h], fill=color)
        draw.text((x + 3, y + 1), text, fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
