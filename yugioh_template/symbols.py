"""Yu-Gi-Oh-style attribute jewels and the user's reference level star.

Each of the eighteen Pokémon types has a separate single-character jewel. Its
glyph is part of the icon; callers should not repeat the type name beside it.
The level orb is isolated from the user-provided C.png (1359, 289, 1461, 391).
All resources are local, and public functions return independent RGBA copies.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

try:
    from .fonts import load_card_font
except ImportError:
    from .fonts import load_card_font


# Core tint, reflected edge and white engraved glyph. Distinct Pokémon types
# deliberately remain separate rather than being mapped to seven TCG attributes.
ATTRIBUTES = {
    "草": ("草", (12, 98, 26), (139, 207, 79)),
    "火": ("火", (177, 28, 9), (255, 143, 35)),
    "水": ("水", (11, 60, 147), (101, 181, 242)),
    "电": ("电", (141, 95, 5), (255, 209, 52)),
    "超能力": ("超", (122, 21, 98), (244, 131, 200)),
    "格斗": ("斗", (130, 45, 16), (216, 125, 59)),
    "恶": ("恶", (37, 12, 40), (154, 83, 163)),
    "钢": ("钢", (45, 60, 65), (181, 205, 207)),
    "龙": ("龙", (53, 23, 122), (152, 126, 218)),
    "一般": ("普", (79, 66, 53), (215, 199, 169)),
    "冰": ("冰", (4, 99, 126), (145, 233, 244)),
    "地面": ("地", (113, 65, 20), (199, 151, 67)),
    "飞行": ("飞", (64, 74, 142), (170, 190, 243)),
    "毒": ("毒", (91, 21, 112), (200, 112, 224)),
    "岩石": ("岩", (91, 87, 36), (185, 180, 108)),
    "虫": ("虫", (66, 99, 9), (185, 212, 60)),
    "幽灵": ("灵", (48, 29, 91), (155, 127, 183)),
    "妖精": ("妖", (145, 44, 93), (250, 162, 198)),
}

# The printed jewels have a bright upper pole, an almost black waist and a
# saturated lower reflection. Hue shifts distinguish the two lit surfaces.
_POLES = {
    "草": ((47, 230, 58), (170, 255, 86)),
    "火": ((255, 49, 12), (255, 191, 37)),
    "水": ((23, 103, 255), (66, 229, 255)),
    "电": ((255, 173, 5), (255, 247, 61)),
    "超能力": ((255, 31, 84), (231, 108, 255)),
    "格斗": ((240, 74, 21), (255, 188, 90)),
    "恶": ((147, 22, 155), (213, 106, 255)),
    "钢": ((105, 153, 185), (194, 247, 255)),
    "龙": ((113, 47, 252), (154, 164, 255)),
    "一般": ((189, 145, 91), (255, 240, 191)),
    "冰": ((0, 177, 210), (155, 255, 255)),
    "地面": ((215, 117, 25), (253, 211, 102)),
    "飞行": ((99, 113, 236), (185, 223, 255)),
    "毒": ((185, 39, 224), (240, 143, 255)),
    "岩石": ((162, 145, 35), (238, 229, 123)),
    "虫": ((138, 187, 10), (224, 255, 58)),
    "幽灵": ((116, 51, 197), (195, 150, 255)),
    "妖精": ((255, 79, 150), (255, 192, 235)),
}

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
        ATTRIBUTES,
    )
)
_ALIASES.update(
    {"lightning": "电", "darkness": "恶", "metal": "钢", "colorless": "一般"}
)
_ICON_DIR = Path(__file__).resolve().parent / "assets" / "yugioh" / "icons"
_STAR_PATH = _ICON_DIR / "level-star.png"
_FILES = dict(
    zip(
        (*ATTRIBUTES, "未知"),
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
            "unknown",
        ),
    )
)


def _size(value: int) -> int:
    value = int(value)
    if not 1 <= value <= 1024:
        raise ValueError("属性和星级图标尺寸应介于 1 和 1024 之间")
    return value


def _mix(a, b, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _draw_jewel(
    type_name: str,
    *,
    face=None,
    glyph=None,
    tint=None,
    reflected=None,
    upper_light=None,
    lower_light=None,
) -> Image.Image:
    """Asset-preparation source; live rendering reads the bundled PNGs below."""
    default_glyph, default_tint, default_reflected = ATTRIBUTES.get(
        type_name, ("?", (60, 59, 69), (180, 177, 190))
    )
    default_upper, default_lower = _POLES.get(
        type_name, ((133, 132, 166), (223, 223, 249))
    )
    glyph, tint, reflected = (
        glyph or default_glyph,
        tint or default_tint,
        reflected or default_reflected,
    )
    upper_light, lower_light = (
        upper_light or default_upper,
        lower_light or default_lower,
    )
    size, centre, radius = 256, 127.5, 124.0
    rng = random.Random(901 + sum(ord(char) for char in type_name))
    data = []
    for y in range(size):
        v = (y - centre) / radius
        for x in range(size):
            u = (x - centre) / radius
            r = math.hypot(u, v)
            if r >= 1:
                data.append((0, 0, 0, 0))
                continue
            # Broad opposing luminous poles leave a dark waist behind the
            # lettering, as in the reference's red-to-purple DARK emblem.
            lower = math.exp(-((u + 0.18) ** 2 / 0.90 + (v - 0.74) ** 2 / 0.075))
            upper = math.exp(-((u + 0.08) ** 2 / 1.45 + (v + 0.77) ** 2 / 0.115))
            hollow = math.exp(-((u - 0.07) ** 2 / 1.6 + (v + 0.01) ** 2 / 0.14))
            cloud = 0.5 + 0.25 * math.sin(u * 17 + 1.3 * math.sin(v * 11))
            cloud += 0.25 * math.sin(v * 21 - u * 9)
            color = _mix(tint, (5, 2, 10), 0.80 * hollow)
            color = _mix(color, upper_light, 0.98 * upper)
            color = _mix(color, lower_light, 0.90 * lower)
            color = _mix(color, reflected, cloud * 0.018)
            if r > 0.947:
                # Only the lower-left cut edge catches light. A complete pale
                # ring would turn the reference's jewel into a metal badge.
                direction = max(0.0, min(1.0, -0.52 * u + 0.48 * v))
                edge = _mix(
                    _mix(tint, (16, 10, 19), 0.54),
                    _mix(lower_light, (245, 249, 255), 0.38),
                    direction,
                )
                highlight = math.exp(-(((r - 0.971) / 0.013) ** 2))
                color = _mix(color, edge, highlight * (0.3 + 0.7 * direction))
                color = _mix(color, (12, 8, 17), max(0.0, (r - 0.986) / 0.014) * 0.64)
            # Fine, quiet pigment variation remains visible at source size.
            grain = rng.choice((-2, -1, 0, 0, 0, 1, 2))
            color = tuple(max(0, min(255, c + grain)) for c in color)
            alpha = round(255 * min(1, (1 - r) * radius))
            data.append((*color, alpha))
    result = Image.new("RGBA", (size, size))
    result.putdata(data)
    disc_alpha = result.getchannel("A")
    # The Noto Serif SC 500 inscription has the official card reference's
    # slanted cross-strokes, tapered curves and sharp Song/Ming endings.
    # This generator is offline; the live path below only reads cached images.
    face = face or load_card_font(210, "symbol")
    mask = Image.new("L", result.size)
    draw = ImageDraw.Draw(mask)
    draw.text((8, 8), glyph, font=face, anchor="lt", fill=255, stroke_width=1)
    mask = mask.crop(mask.getbbox())
    # Rectangular bounds are insufficient: a sweeping lower-left stroke can
    # cross a circle even when both width and height fit. Fit the actual ink
    # radially, leaving room for the engraving shadow inside the rim.
    half_w, half_h = (mask.width - 1) / 2, (mask.height - 1) / 2
    farthest = max(
        math.hypot(x - half_w, y - half_h)
        for y in range(mask.height)
        for x in range(mask.width)
        if mask.getpixel((x, y)) > 8
    )
    scale = min(177 / mask.width, 181 / mask.height, 104 / max(1, farthest))
    mask = mask.resize(
        (round(mask.width * scale), round(mask.height * scale)),
        Image.Resampling.LANCZOS,
    )
    centred = Image.new("L", result.size)
    centred.paste(mask, ((size - mask.width) // 2, (size - mask.height) // 2))
    mask = centred
    shade = Image.new("RGBA", result.size, (17, 7, 21, 0))
    shade.putalpha(
        mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
    )
    result.alpha_composite(shade, (1, 2))
    ink = Image.new("RGBA", (1, size))
    ink.putdata(
        [
            (*_mix((255, 254, 239), (237, 233, 209), y / (size - 1)), 255)
            for y in range(size)
        ]
    )
    ink = ink.resize(result.size)
    ink.putalpha(mask)
    result.alpha_composite(ink)
    result.putalpha(disc_alpha)
    return result


@lru_cache(maxsize=20)
def _jewel(type_name: str) -> Image.Image:
    """Load prepared artwork so a first draw never builds 18 pixel fields."""
    with Image.open(_ICON_DIR / f"attribute-{_FILES[type_name]}.png") as source:
        return source.convert("RGBA")


@lru_cache(maxsize=128)
def _attribute(type_name: str, size: int) -> Image.Image:
    return _jewel(type_name).resize((size, size), Image.Resampling.LANCZOS)


def render_attribute_icon(type_name: str, size: int = 80) -> Image.Image:
    """Return the specified Pokémon type's standalone attribute jewel."""
    type_name = str(type_name).strip()
    type_name = _ALIASES.get(type_name.lower(), type_name)
    if type_name not in ATTRIBUTES:
        type_name = "未知"
    return _attribute(type_name, _size(size)).copy()


@lru_cache(maxsize=32)
def _custom_jewel(glyph, tint, reflected, upper_light, lower_light, font_path):
    from PIL import ImageFont

    face = (
        ImageFont.truetype(font_path, 210) if font_path else load_card_font(210, "body")
    )
    return _draw_jewel(
        glyph,
        face=face,
        glyph=glyph,
        tint=tint,
        reflected=reflected,
        upper_light=upper_light,
        lower_light=lower_light,
    )


def render_custom_attribute_icon(
    glyph: str,
    size: int = 80,
    *,
    tint=(80, 39, 104),
    reflected=(190, 171, 237),
    upper_light=(159, 79, 245),
    lower_light=(229, 205, 255),
    font_path=None,
):
    """A new one-character jewel with per-call colours and an optional font."""
    glyph = str(glyph).strip()
    if len(glyph) != 1:
        raise ValueError("自定义属性章必须是一个字符；复杂图标请传入 PNG")
    colours = []
    for value in (tint, reflected, upper_light, lower_light):
        colour = tuple(value)
        if len(colour) != 3 or any(
            type(v) is not int or not 0 <= v <= 255 for v in colour
        ):
            raise ValueError("图标颜色必须是三个 0–255 整数")
        colours.append(colour)
    return _custom_jewel(glyph, *colours, str(font_path) if font_path else None).resize(
        (_size(size), _size(size)), Image.Resampling.LANCZOS
    )


@lru_cache(maxsize=12)
def _level_star(size: int) -> Image.Image:
    with Image.open(_STAR_PATH) as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def render_level_star(size: int = 46) -> Image.Image:
    """Return the reference's orange-red orb and bright yellow five-point star."""
    return _level_star(_size(size)).copy()
