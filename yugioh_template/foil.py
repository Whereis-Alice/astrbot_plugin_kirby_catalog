"""Thin hot-stamped name lettering for the monster-card print face.

The colour lives inside the glyphs. A directional inset edge, soft impression
shadow and microscopic ink grain make the pale gold and silver-spectrum foil
read as print rather than a thick outlined display gradient.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

try:
    from .fonts import load_card_font, load_card_name_mask
except ImportError:
    from .fonts import load_card_font, load_card_name_mask


_GOLD = (
    (0.00, (167, 140, 78)),
    (0.18, (220, 198, 129)),
    (0.38, (249, 239, 198)),
    (0.46, (255, 250, 218)),
    (0.51, (203, 173, 97)),
    (0.54, (137, 105, 47)),
    (0.58, (213, 185, 116)),
    (0.80, (232, 211, 151)),
    (1.00, (185, 155, 84)),
)
_SPECTRUM = (
    (0.00, (205, 150, 64)),
    (0.16, (160, 196, 63)),
    (0.33, (0, 193, 145)),
    (0.50, (0, 175, 221)),
    (0.67, (120, 119, 219)),
    (0.84, (206, 110, 170)),
    (1.00, (214, 136, 85)),
)


def _mix(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _sample(stops, value):
    value = min(1, max(0, value))
    for (lo, a), (hi, b) in zip(stops, stops[1:]):
        if value <= hi:
            return _mix(a, b, (value - lo) / max(0.0001, hi - lo))
    return stops[-1][1]


def _ink_field(kind, width, height, seed):
    """A physical-size foil field, with one-pixel print variation."""
    rng = random.Random(seed)
    field = Image.new("RGBA", (width, height))
    pixels = []
    for y in range(height):
        v = y / max(1, height - 1)
        for x in range(width):
            u = x / max(1, width - 1)
            # The reflection is a shallow diagonal, never a rainbow per letter.
            angle = min(1, max(0, v + (u - 0.5) * 0.095))
            grain = rng.uniform(-2.6, 2.6)
            hairline = math.sin(y * 2.13 + x * 0.07) * 0.7
            if kind == "SSR":
                color = _sample(_GOLD, angle)
            else:
                color = _sample(_SPECTRUM, u)
                silver = 0.06 + 0.16 * math.exp(-(((angle - 0.33) / 0.13) ** 2))
                color = _mix(color, (241, 241, 232), silver)
                crease = math.exp(-(((angle - 0.58) / 0.033) ** 2))
                color = _mix(color, (81, 89, 99), crease * 0.28)
                lower_glint = math.exp(-(((angle - 0.79) / 0.045) ** 2))
                color = _mix(color, (246, 247, 242), lower_glint * 0.18)
            pixels.append(
                tuple(max(0, min(255, round(c + grain + hairline))) for c in color)
                + (255,)
            )
    field.putdata(pixels)
    return field


def _paint_mask(base, mask, color, amount=1, offset=(0, 0)):
    layer = Image.new("RGBA", mask.size, (*color, 0))
    layer.putalpha(mask.point(lambda value: round(value * amount)))
    base.alpha_composite(layer, offset)


@lru_cache(maxsize=24)
def _lettering(name, size, kind, font, artwork_bytes=None):
    scale, padding = 3, 6
    if artwork_bytes is not None:
        from io import BytesIO

        with Image.open(BytesIO(artwork_bytes)) as source:
            artwork = source.convert("L")
    else:
        artwork = load_card_name_mask(name, size)
    width = (
        artwork.width if artwork else max(1, math.ceil(font.getlength(name)))
    ) + padding * 2
    height = (
        artwork.height if artwork else max(1, font.getbbox(name, anchor="lt")[3])
    ) + padding * 2
    large_size = (width * scale, height * scale)
    mask = Image.new("L", large_size)
    if artwork is None:
        large_font = font.font_variant(size=round(size * scale))
        ImageDraw.Draw(mask).text(
            (padding * scale, padding * scale),
            name,
            font=large_font,
            anchor="lt",
            fill=255,
        )
    else:
        # Whole-name image artwork is already rendered at high resolution;
        # the same silhouette is used for solid, gold and spectrum lettering.
        artwork = (
            artwork.resize(
                (artwork.width * scale, artwork.height * scale),
                Image.Resampling.LANCZOS,
            )
            if artwork_bytes is not None
            else load_card_name_mask(name, size * scale)
        )
        mask.paste(artwork, (padding * scale, padding * scale))
    layer = Image.new("RGBA", large_size)
    # Ink impressions have a slight lower shadow, not a uniform black outline.
    shadow = mask.filter(ImageFilter.GaussianBlur(1.0 * scale))
    shadow_color = (46, 35, 19) if kind == "SSR" else (54, 58, 69)
    _paint_mask(
        layer,
        shadow,
        shadow_color,
        0.48 if kind == "SSR" else 0.59,
        (round(0.35 * scale), round(1.05 * scale)),
    )
    seed = sum((index + 11) * ord(char) for index, char in enumerate(name + kind))
    field = _ink_field(kind, width, height, seed).resize(
        large_size, Image.Resampling.BICUBIC
    )
    field.putalpha(mask)
    layer.alpha_composite(field)
    # Fine offset relief remains inside the printed strokes. At phone scale it
    # reads as the edge of a thin foil stamp rather than outlined lettering.
    dark_edge = ImageChops.subtract(mask, ImageChops.offset(mask, -2, -2))
    light_edge = ImageChops.subtract(mask, ImageChops.offset(mask, 1, 2))
    _paint_mask(
        layer,
        dark_edge,
        (91, 68, 28) if kind == "SSR" else (59, 65, 80),
        0.47 if kind == "SSR" else 0.65,
    )
    _paint_mask(
        layer,
        light_edge,
        (255, 250, 219) if kind == "SSR" else (250, 250, 248),
        0.74 if kind == "SSR" else 0.39,
    )
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def render_foil_name(
    base, name: str, x: int, y: int, size: int, kind: str, *, font=None, artwork=None
):
    """Print a name at the same top-left text anchor as Pillow's ``anchor=lt``."""
    if kind not in {"SSR", "SHINY"}:
        raise ValueError("箔字只用于传说与闪光卡面")
    font = font or load_card_font(size, "name", text=str(name))
    raw = None
    if artwork is not None:
        from io import BytesIO

        output = BytesIO()
        artwork.convert("L").save(output, "PNG")
        raw = output.getvalue()
    base.alpha_composite(
        _lettering(str(name), int(size), kind, font, raw), (round(x) - 6, round(y) - 6)
    )
