"""Clouded print stocks reconstructed from the supplied monster-card references.

The local swatches contain only bare stock. Their soft, irregular clouds replace
the engraved contours, facets and grids used by the previous design. Composition
and typography belong to ``yugioh_cards``; this module supplies plain materials.
All public results are independent RGBA copies of a bounded cache.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageOps, ImageEnhance

try:
    from .colors import TYPE_COLORS
except ImportError:
    from .colors import TYPE_COLORS


KINDS = ("C", "R", "SR", "SSR", "SHINY")
READING_INKS = {kind: (32, 26, 29) for kind in KINDS}
READING_MUTED = {kind: (57, 48, 55) for kind in KINDS}
TITLE_INKS = {
    "C": (40, 24, 17),
    "R": (252, 244, 249),
    "SR": (30, 18, 42),
    "SSR": (233, 191, 83),
    "SHINY": (51, 57, 87),
}
_PAPER = {
    "C": ((224, 201, 179), (247, 234, 215)),
    "R": ((223, 196, 216), (246, 231, 240)),
    "SR": ((215, 202, 229), (241, 231, 245)),
    "SSR": ((227, 225, 218), (251, 246, 232)),
    "SHINY": ((223, 226, 232), (252, 250, 246)),
}
_ASSETS = Path(__file__).parent / "assets" / "yugioh"
_SEEDS = {"C": 17003, "R": 24029, "SR": 39113, "SSR": 57119, "SHINY": 79133}
_ALIASES = dict(
    zip(
        (
            "grass",
            "fire",
            "water",
            "electric",
            "psychic",
            "fighting",
            "dark",
            "steel",
            "dragon",
            "normal",
            "ice",
            "ground",
            "flying",
            "poison",
            "rock",
            "bug",
            "ghost",
            "fairy",
        ),
        (
            "草",
            "火",
            "水",
            "电",
            "超能力",
            "格斗",
            "恶",
            "钢",
            "龙",
            "一般",
            "冰",
            "地面",
            "飞行",
            "毒",
            "岩石",
            "虫",
            "幽灵",
            "妖精",
        ),
    )
)


def _mix(a, b, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _normalise(kind, width, height, type_name="一般"):
    kind = str(kind).upper()
    if kind not in KINDS:
        kind = "C"
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("Yu-Gi-Oh material dimensions must be positive")
    type_name = _ALIASES.get(str(type_name).lower(), str(type_name))
    if type_name not in TYPE_COLORS:
        type_name = "一般"
    return kind, width, height, type_name


@lru_cache(maxsize=5)
def _stock_source(kind):
    with Image.open(_ASSETS / f"{kind.lower()}-stock.jpg") as opened:
        return opened.convert("RGB")


def _clouds(kind, width, height):
    source = _stock_source(kind)
    # Preserve the physical cloud scale in a shorter reading area as well.
    scale = width / source.width
    target_height = max(height, round(source.height * scale))
    source = source.resize((width, target_height), Image.Resampling.LANCZOS)
    offset = (target_height - height) // 2
    return source.crop((0, offset, width, offset + height))


def _spectrum(phase):
    return tuple(
        round(193 + 52 * math.cos(math.tau * (phase - shift)))
        for shift in (0, 1 / 3, 2 / 3)
    )


def _pearl_wash(base, amount, *, quiet_title=False):
    """A low-opacity optical tint preserves the reference's white silver base."""
    width, height = base.size
    sw = min(width, 180)
    sh = min(height, max(1, round(sw * height / width)))
    glaze = Image.new("RGB", (sw, sh))
    values, opacities = [], []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            # Broad diagonal iridescence bends through the original paper's
            # clouds; the material stays silver-white instead of flat stripes.
            values.append(
                _spectrum(u * 1.04 - v * 0.52 + 0.055 * math.sin(v * 9 + u * 3))
            )
            if quiet_title:
                fade = 1 - math.exp(-((u / 0.85) ** 6 + (v / 0.11) ** 6))
                opacities.append(round(255 * amount * (0.10 + 0.90 * fade)))
    glaze.putdata(values)
    glaze = glaze.resize((width, height), Image.Resampling.BICUBIC)
    if quiet_title:
        mask = Image.new("L", (sw, sh))
        mask.putdata(opacities)
        return Image.composite(
            glaze,
            base.convert("RGB"),
            mask.resize((width, height), Image.Resampling.BICUBIC),
        )
    return Image.blend(base.convert("RGB"), glaze, amount)


@lru_cache(maxsize=2)
def _illustration_light(optical=False):
    # Bare light rays cropped from the supplied C reference; no character or
    # text is present. These retain the source's fine painted/printed texture.
    with Image.open(
        _ASSETS / ("optical-light.jpg" if optical else "illustration-light.jpg")
    ) as image:
        return image.convert("RGB")


@lru_cache(maxsize=10)
def _print_sparkles(kind, width, height):
    image = Image.new("RGBA", (width, height))
    if kind not in {"SR", "SSR", "SHINY"}:
        return image
    rng = random.Random(_SEEDS[kind])
    draw = ImageDraw.Draw(image)
    count = {"SR": 190, "SSR": 290, "SHINY": 3800}[kind]
    for index in range(count):
        x, y = rng.randrange(width), rng.randrange(height)
        phase = x / width + y / height * 0.37
        band = 0.5 + 0.5 * math.sin(phase * 24)
        if kind == "SHINY" and rng.random() > 0.18 + band * 0.82:
            continue
        hue = phase + rng.uniform(-0.11, 0.11)
        colour = (
            tuple(
                round(153 + 102 * math.cos(math.tau * (hue - shift)))
                for shift in (0, 1 / 3, 2 / 3)
            )
            if kind == "SHINY"
            else (248, 220, 158)
        )
        opacity = rng.randint(38, 165)
        length = rng.choice((1, 1, 1, 2, 2, 3))
        if length == 1:
            draw.point((x, y), fill=(*colour, opacity))
        else:
            draw.polygon(
                ((x, y), (x + length, y - 1), (x + length + 1, y + 1), (x + 1, y + 2)),
                fill=(*colour, opacity),
            )
        if index % 137 == 0:
            draw.line((x - 3, y, x + 3, y), fill=(*colour, 190), width=1)
            draw.line((x, y - 3, x, y + 3), fill=(*colour, 190), width=1)
    return image


def _artwork_field(kind, type_name, width, height):
    """Printed illumination with depth and fine reflection instead of fog."""
    source = _illustration_light(kind == "R")
    if kind == "SR":
        source = source.transpose(Image.Transpose.ROTATE_90)
    source = source.resize((width, height), Image.Resampling.LANCZOS)
    luminance = ImageOps.grayscale(source)
    dark, light = {
        "C": ((28, 57, 39), (234, 220, 151)),
        "R": ((13, 21, 66), (149, 211, 248)),
        "SR": ((35, 26, 78), (226, 229, 255)),
        "SSR": ((4, 10, 17), (218, 191, 112)),
        "SHINY": ((129, 146, 170), (229, 239, 233)),
    }[kind]
    if kind == "C":
        dark = _mix(dark, TYPE_COLORS[type_name], 0.10)
    field = ImageOps.colorize(luminance, dark, light)
    if kind == "SSR":
        field = ImageEnhance.Brightness(field).enhance(0.80)
        field = Image.blend(_clouds("SSR", width, height), field, 0.72)
    if kind == "SHINY":
        # The supplied early preview uses broad pastel foil reflections. Keep
        # that silver-rainbow light while retaining the reference paper grain
        # and printed rays underneath, rather than a dark coloured sky.
        field = Image.blend(field, _clouds("SHINY", width, height), 0.18)
        field = ImageEnhance.Color(_pearl_wash(field, 0.32)).enhance(1.08)
    field = field.convert("RGBA")
    field.alpha_composite(_print_sparkles(kind, width, height))
    return field


def apply_print_finish(base, box, kind):
    """A light common varnish joins the portrait to the illustration's foil."""
    x, y, width, height = map(round, box)
    layer = _print_sparkles(kind, width, height).copy()
    layer.putalpha(layer.getchannel("A").point(lambda value: round(value * 0.18)))
    base.alpha_composite(layer, (x, y))


@lru_cache(maxsize=15)
def _material(kind, layer, width, height, type_name):
    if layer == "art":
        return _artwork_field(kind, type_name, width, height)
    base = _clouds(kind, width, height)
    if layer == "reading":
        if kind == "SSR":
            base = _clouds("C", width, height)
        gray = ImageOps.autocontrast(ImageOps.grayscale(base), cutoff=2)
        gray = Image.blend(gray, Image.new("L", gray.size, 128), 0.26)
        # Keep effect boxes light, with compressed cloud contrast for black type.
        base = ImageOps.colorize(gray, *_PAPER[kind])
        if kind == "SHINY":
            base = _pearl_wash(base, 0.04)
    elif kind == "SSR":
        base = Image.blend(base, Image.new("RGB", base.size, (7, 11, 13)), 0.27)
    elif kind == "SHINY":
        base = _pearl_wash(base, 0.20, quiet_title=True)
    return base.convert("RGBA")


def render_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return the reference's clouded card stock as an opaque RGBA copy."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "stock", width, height, type_name).copy()


def render_art_scene(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return a calm printed picture background with a species-type tint."""
    kind, width, height, type_name = _normalise(kind, width, height, type_name)
    return _material(kind, "art", width, height, type_name).copy()


def render_reading_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return lightly clouded, stock-matched paper for the effect text box."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "reading", width, height, type_name).copy()
