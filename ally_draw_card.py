"""Local, bounded Pillow artwork for the daily ally collection card."""

from __future__ import annotations

import math
import random
import re
import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from .wiki_content import inline_markup_plain

ALLY_DRAW_CARD_THEMES = ("星愿收藏", "银河档案")
CARD_WIDTH = 1024
CARD_HEIGHT = 1440
MAX_CARD_HEIGHT = 3000
MAX_CARD_DESCRIPTION_CHARS = 2000
MAX_CARD_DESCRIPTION_LINES = 40

_PALETTES = {
    "星愿收藏": {
        "outer": "#eedfe8",
        "paper": "#ecd0e0",
        "ink": "#43314f",
        "muted": "#6f5979",
        "accent": "#ac3c77",
        "line": "#ba8cac",
        "soft": "#d3cfe5",
        "gold": "#ffd65a",
        "glow": "#b8ddf6",
        "chip": "#ffe1ee",
        "chip_ink": "#ae4f81",
        "shadow": "#b76a91",
        "sky": "#cce9fa",
        "cloud": "#fffdf6",
        "cloud_shadow": "#d3e7f3",
        "star_bottom": "#ffb94b",
    },
    "银河档案": {
        "outer": "#0b1532",
        "paper": "#263354",
        "ink": "#fff4dd",
        "muted": "#b2c1dc",
        "accent": "#f2a6d0",
        "line": "#40517b",
        "soft": "#3e426b",
        "gold": "#ffda6a",
        "glow": "#6775ce",
        "chip": "#64426d",
        "chip_ink": "#ffdaed",
        "shadow": "#0b1434",
        "sky": "#273c7b",
        "cloud": "#263a65",
        "cloud_shadow": "#1b2c50",
        "star_bottom": "#eea743",
    },
}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _description(value: str, name: str, limit: int) -> str:
    plain = inline_markup_plain(value)
    # Some wiki leads begin with a quotation and its game citation. Prefer the
    # introductory paragraph when it can be identified without inventing text.
    if plain.lstrip().startswith(("“", '"', "「")):
        closing = re.search(r'[”"」]', plain.lstrip()[1:])
        if closing:
            tail = plain.lstrip()[closing.end() + 1 :]
            introduction = re.search(
                re.escape(name) + r"(?:[（(][^）)]*[）)])?(?:是|为|首次)", tail
            )
            if introduction:
                plain = tail[introduction.start() :]
    plain = re.sub(
        r"(?<=[\u3400-\u9fff])[（(][A-Za-z][A-Za-z0-9'’:&.,/\s-]*[）)]",
        "",
        plain,
    )
    plain = re.sub(r"[^\S\n]+", " ", plain.replace("\r\n", "\n")).strip()
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain if len(plain) <= limit else plain[:limit].rstrip() + "…"


def _artwork_bytes(raw: bytes | None) -> bytes | None:
    """Freeze animation and trim the empty border, without cropping the subject."""
    if not raw or len(raw) > 16 * 1024 * 1024:
        return None
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 24_000_000:
                return None
            source.seek(0)
            source.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            art = source.convert("RGBA")
        # For white-backed JPGs, remove only background connected to a corner.
        # Interior white eyes and clothes remain behind their colored outline.
        if art.getchannel("A").getextrema() == (255, 255):
            for corner in (
                (0, 0),
                (art.width - 1, 0),
                (0, art.height - 1),
                (art.width - 1, art.height - 1),
            ):
                if min(art.getpixel(corner)[:3]) >= 248:
                    ImageDraw.floodfill(art, corner, (255, 255, 255, 0), thresh=12)
        bbox = art.getchannel("A").getbbox()
        if not bbox:
            return None
        art = art.crop(bbox)
        stream = BytesIO()
        art.save(stream, format="PNG")
        return stream.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def build_ally_draw_card_data(
    entry: dict[str, Any],
    *,
    nickname: str,
    date: str,
    remaining: int,
    description: str = "",
    description_max_chars: int = 1000,
    image_bytes: bytes | None = None,
    repeated: bool = False,
    pity: bool = False,
    existing_today: bool = False,
    all_unlocked: bool = False,
    theme: str = "星愿收藏",
    wiki_hint: str = "",
    collected: int | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    display_name = _text(
        entry.get("name")
        or entry.get("display_name")
        or entry.get("name_zh")
        or "未命名盟友",
        250,
    )
    english_match = re.search(r"[（(]([^（）()]*[A-Za-z][^（）()]*)[）)]", display_name)
    english = _text(
        entry.get("name_en")
        or (english_match.group(1) if english_match else entry.get("page_title")),
        250,
    )
    name = display_name
    if english_match:
        name = (name[: english_match.start()] + name[english_match.end() :]).strip()
    source = _text(
        entry.get("source")
        or entry.get("debut_work_zh")
        or entry.get("display_work")
        or entry.get("debut_work"),
        300,
    )
    source = re.sub(r"[（(][^（）()]*[A-Za-z][^（）()]*[）)]", "", source).strip()
    if existing_today:
        status, note = "今日已相遇", "这位伙伴，今天已在身边。"
    elif all_unlocked:
        status, note = "图鉴已集齐", "每一次重逢，都值得珍藏。"
    elif pity:
        status, note = "保底邂逅", "新的伙伴，终于如约而至。"
    elif repeated:
        status, note = "再度同行", "熟悉的身影，也有新的故事。"
    else:
        status, note = "初次相遇", "把今天的惊喜，留在这一页。"
    return {
        "name": name or display_name,
        "english_name": english,
        "catalog_id": _number(entry.get("id") or entry.get("catalog_id")),
        "source": source,
        "nickname": _text(nickname, 150) or "旅行者",
        "date": _text(date, 32).replace("-", "."),
        "remaining": _number(remaining),
        "description": _description(
            description,
            name or display_name,
            min(
                _number(description_max_chars) or MAX_CARD_DESCRIPTION_CHARS,
                MAX_CARD_DESCRIPTION_CHARS,
            ),
        ),
        "artwork": _artwork_bytes(image_bytes),
        "theme": theme if theme in ALLY_DRAW_CARD_THEMES else ALLY_DRAW_CARD_THEMES[0],
        "status": status,
        "status_note": note,
        "wiki_hint": _text(wiki_hint, 200),
        "collected": _number(collected) if collected is not None else None,
        "total": _number(total) if total is not None else None,
    }


@lru_cache(maxsize=48)
def _font(size: int, bold: bool = False, latin: bool = False) -> ImageFont.ImageFont:
    cjk = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    latin_paths = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in latin_paths + cjk if latin else cjk:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        except OSError:
            continue
    if latin:
        return ImageFont.load_default()
    raise OSError("今日盟友卡片需要中文字体：请安装 Noto Sans CJK 或文泉驿微米黑")


def _lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
    max_lines: int,
) -> list[str]:
    """Bound mixed Chinese and English text by measured pixels, with ellipsis."""
    # Keep Latin words together and preserve paragraphs. The former _text()
    # call silently discarded everything after 500 characters before wrapping.
    result: list[str] = []
    current = ""
    tokens = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9'’&:./_+-]*|[^\S\n]+|\n|.", str(text or "")[:10000]
    )
    for token in tokens:
        if token == "\n":
            result.append(current.rstrip())
            current = ""
            continue
        pieces = list(token) if draw.textlength(token, font=font) > width else [token]
        for piece in pieces:
            if current and draw.textlength(current + piece, font=font) > width:
                tail = ""
                if piece in "，。；：、！？）】》」”" or current[-1] in "（【《「“":
                    match = re.search(r"[A-Za-z0-9][A-Za-z0-9'’&:./_+-]*$|.$", current)
                    tail = match.group() if match else ""
                head = current[: -len(tail)] if tail else current
                if head:
                    result.append(head.rstrip())
                    current = tail + piece.lstrip() if tail else piece.lstrip()
                else:
                    result.append(current.rstrip())
                    current = piece.lstrip()
            else:
                current += piece if current else piece.lstrip()
    if current:
        result.append(current.rstrip())
    while result and not result[-1]:
        result.pop()
    if len(result) > max_lines:
        result = result[:max_lines]
        tail = result[-1].rstrip()
        while tail and draw.textlength(tail + "…", font=font) > width:
            tail = tail[:-1]
        result[-1] = tail + "…"
    return result


def _center(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.ImageFont,
    color: str,
    center: int = 512,
) -> None:
    draw.text((center, y), text, font=font, fill=color, anchor="mt")


def _star(
    canvas: Image.Image,
    center: tuple[int, int],
    radius: int,
    color: str,
    *,
    angle: float = -90,
    outline: str | None = None,
) -> None:
    padding = 6
    size = (radius + padding) * 2
    tile = Image.new("RGBA", (size * 3, size * 3))
    draw = ImageDraw.Draw(tile)
    points = []
    for index in range(10):
        theta = math.radians(angle + index * 36)
        r = radius * (1 if index % 2 == 0 else 0.46) * 3
        points.append(
            (size * 1.5 + math.cos(theta) * r, size * 1.5 + math.sin(theta) * r)
        )
    draw.polygon(_rounded_points(points), fill=color)
    if outline:
        rounded = _rounded_points(points)
        draw.line(rounded + [rounded[0]], fill=outline, width=3, joint="curve")
    tile = tile.resize((size, size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(tile, (center[0] - size // 2, center[1] - size // 2))


def _rounded_points(
    points: list[tuple[float, float]], rounding: float = 0.17
) -> list[tuple[float, float]]:
    """Round the star's tips and valleys using short quadratic curves."""
    result = []
    for index, point in enumerate(points):
        previous, following = points[index - 1], points[(index + 1) % len(points)]
        start = tuple(point[j] + (previous[j] - point[j]) * rounding for j in (0, 1))
        end = tuple(point[j] + (following[j] - point[j]) * rounding for j in (0, 1))
        for step in range(9):
            t = step / 8
            result.append(
                tuple(
                    (1 - t) ** 2 * start[j] + 2 * (1 - t) * t * point[j] + t**2 * end[j]
                    for j in (0, 1)
                )
            )
    return result


def _gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    a, b = ImageColor.getrgb(top), ImageColor.getrgb(bottom)
    strip = Image.new("RGB", (1, size[1]))
    strip.putdata(
        [
            tuple(
                round(a[c] + (b[c] - a[c]) * y / max(1, size[1] - 1)) for c in range(3)
            )
            for y in range(size[1])
        ]
    )
    return strip.resize(size).convert("RGBA")


def _cloud(
    canvas: Image.Image, x: int, y: int, width: int, colors: dict[str, str]
) -> None:
    tile = Image.new("RGBA", (width, max(64, width // 3)))
    mask = Image.new("L", tile.size)
    d = ImageDraw.Draw(mask)
    height = tile.height
    d.rounded_rectangle(
        (0, height * 0.36, width, height * 0.86), radius=height // 3, fill=255
    )
    for cx, cy, r in (
        (0.18, 0.43, 0.26),
        (0.37, 0.32, 0.32),
        (0.61, 0.40, 0.29),
        (0.80, 0.45, 0.24),
    ):
        cx, cy, r = cx * width, cy * height, r * height
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    shadow = Image.new("RGBA", tile.size, colors["cloud_shadow"])
    shadow.putalpha(mask)
    tile.alpha_composite(shadow, (0, 8))
    face = _gradient(tile.size, colors["cloud"], colors["cloud_shadow"])
    face.putalpha(mask)
    tile.alpha_composite(face)
    bevel = Image.new("RGBA", tile.size, colors["cloud"])
    bevel.putalpha(ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(5))))
    tile.alpha_composite(bevel)
    canvas.alpha_composite(tile, (x, y))


def _glow(
    canvas: Image.Image,
    center: tuple[int, int],
    radii: tuple[int, int],
    color: str,
    opacity: int,
) -> None:
    layer = Image.new("RGBA", canvas.size, color)
    alpha = Image.new("L", canvas.size)
    draw = ImageDraw.Draw(alpha)
    x, y = center
    rx, ry = radii
    draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=opacity)
    alpha = alpha.filter(ImageFilter.GaussianBlur(65))
    layer.putalpha(alpha)
    canvas.alpha_composite(layer)


@lru_cache(maxsize=2)
def _pearl_tile(dark: bool) -> Image.Image:
    """A reusable pastel nacre surface, generated locally at a small resolution."""
    palette = (
        ("#809cc4", "#b3a6d2", "#edd1b1", "#76b6c4", "#809cc4")
        if dark
        else ("#fff0c8", "#f4bad7", "#c1e8ef", "#d1bfea", "#fff0c8")
    )
    stops = [ImageColor.getrgb(value) for value in palette]
    pixels = []
    for y in range(192):
        v = y / 191
        for x in range(192):
            u = x / 191
            phase = (u * 0.87 + v * 0.71 + math.sin(v * 5) * 0.09) % 1
            index = phase * (len(stops) - 1)
            i, mix = int(index), index % 1
            reflection = math.exp(-(((u * 0.82 + v * 0.57 - 0.69) / 0.064) ** 2)) * 0.72
            grain = ((x * 47 + y * 31 + x * y * 13) % 11 - 5) * 0.25
            pixels.append(
                tuple(
                    round(
                        min(
                            255,
                            max(
                                0,
                                (stops[i][c] * (1 - mix) + stops[i + 1][c] * mix)
                                * (1 - reflection)
                                + 255 * reflection
                                + grain,
                            ),
                        )
                    )
                    for c in range(3)
                )
            )
    tile = Image.new("RGB", (192, 192))
    tile.putdata(pixels)
    return tile.convert("RGBA")


def _material_frame(canvas: Image.Image, dark: bool) -> None:
    """Pearl laminate, a warm foil seam, and the beveled edge of a keepsake."""
    w, h = canvas.size
    frame_mask = Image.new("L", canvas.size)
    d = ImageDraw.Draw(frame_mask)
    d.rounded_rectangle((16, 14, w - 16, h - 14), radius=52, fill=255)
    surface = _pearl_tile(dark).resize(canvas.size, Image.Resampling.BICUBIC)
    surface.putalpha(frame_mask)
    canvas.alpha_composite(surface)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        (17, 15, w - 17, h - 15),
        radius=51,
        outline="#8295c0" if dark else "#cfb7cc",
        width=2,
    )
    d.rounded_rectangle(
        (22, 20, w - 22, h - 20),
        radius=47,
        outline="#d1ddf5" if dark else "#fffdf2",
        width=2,
    )
    d.rounded_rectangle(
        (34, 32, w - 34, h - 32),
        radius=40,
        outline="#cbb581" if dark else "#d9b76b",
        width=2,
    )
    # Closely spaced foil cuts catch the light along the narrow outer rim.
    for inset in (26, 29):
        d.rounded_rectangle(
            (inset, inset - 2, w - inset, h - inset + 2),
            radius=48 - (inset - 22),
            outline="#b5cbd6" if dark else "#f7deb4",
            width=1,
        )


@lru_cache(maxsize=2)
def _pigment_tile(dark: bool) -> Image.Image:
    """Soft overlapping ink washes, kept small before scaling to the card."""
    pigments = (
        ("#4a356d", "#20375b", "#385f76", "#564777", "#273352")
        if dark
        else ("#eeb6cd", "#d9c3e8", "#b5dbe2", "#e9cbbb", "#d6c4df")
    )
    colors = [ImageColor.getrgb(value) for value in pigments]
    centers = ((0.04, 0.05), (0.95, 0.08), (0.08, 0.80), (0.98, 0.72), (0.5, 1.0))
    pixels = []
    for y in range(192):
        v = y / 191
        for x in range(128):
            u = x / 127
            weights = [
                math.exp(-((u - cx) ** 2 * 3.8 + (v - cy) ** 2 * 6))
                for cx, cy in centers
            ]
            weight = sum(weights)
            sheen = math.sin(u * 8 + v * 6 + math.sin(v * 9)) * (3 if dark else 5)
            pixels.append(
                tuple(
                    max(
                        0,
                        min(
                            255,
                            round(
                                sum(c[k] * w for c, w in zip(colors, weights)) / weight
                                + sheen
                            ),
                        ),
                    )
                    for k in range(3)
                )
            )
    tile = Image.new("RGB", (128, 192))
    tile.putdata(pixels)
    return tile.convert("RGBA")


@lru_cache(maxsize=1)
def _paper_grain() -> Image.Image:
    """Fixed fine fibers: the same character must keep the same image fingerprint."""
    rng = random.Random(7319)
    tile = Image.frombytes("L", (128, 128), rng.randbytes(128 * 128))
    tile = tile.point(lambda value: 125 + value * 7 // 256)
    draw = ImageDraw.Draw(tile)
    for index in range(160):
        x, y = rng.randrange(128), rng.randrange(128)
        draw.line((x, y, x + 1 + index % 4, y + index % 2), fill=125 + index % 7)
    return tile


def _apply_grain(surface: Image.Image) -> Image.Image:
    grain = Image.new("L", surface.size, 128)
    tile = _paper_grain()
    for y in range(0, surface.height, tile.height):
        for x in range(0, surface.width, tile.width):
            grain.paste(tile, (x, y))
    return ImageChops.add(
        surface.convert("RGB"), grain.convert("RGB"), offset=-128
    ).convert("RGBA")


@lru_cache(maxsize=2)
def _emboss_tile(dark: bool) -> Image.Image:
    """An original staggered pattern of pressed stars and tiny diamond stitches."""
    tile = Image.new("RGBA", (180, 180))
    draw = ImageDraw.Draw(tile)
    shadow = (8, 17, 40, 50) if dark else (111, 65, 112, 36)
    light = (189, 208, 238, 48) if dark else (255, 246, 217, 115)
    for x, y, radius in ((38, 34, 16), (128, 124, 16)):
        points = _rounded_points(
            [
                (
                    x
                    + math.cos(math.radians(-90 + i * 36))
                    * radius
                    * (1 if i % 2 == 0 else 0.48),
                    y
                    + math.sin(math.radians(-90 + i * 36))
                    * radius
                    * (1 if i % 2 == 0 else 0.48),
                )
                for i in range(10)
            ]
        )
        draw.line(
            [(px, py + 1.5) for px, py in points + points[:1]], fill=light, width=2
        )
        draw.line(points + points[:1], fill=shadow, width=1)
    for x, y in ((128, 34), (38, 124)):
        draw.line(
            ((x, y - 5), (x + 4, y), (x, y + 5), (x - 4, y), (x, y - 5)),
            fill=light,
            width=1,
        )
    return tile


def _dream_surface(size: tuple[int, int], dark: bool) -> Image.Image:
    """Tinted pearlescent stock with a woven star pattern and sweeping foil threads."""
    width, height = size
    surface = _pigment_tile(dark).resize(size, Image.Resampling.BICUBIC)
    pattern = _emboss_tile(dark)
    for y in range(0, height, pattern.height):
        for x in range(0, width, pattern.width):
            surface.alpha_composite(pattern, (x, y))
    lines = Image.new("RGBA", size)
    draw = ImageDraw.Draw(lines)
    # Curving parallel threads run through the sky, title and archive stock.
    for band, origin in enumerate((-0.22, 0.44, 0.93)):
        for index in range(8):
            points = [
                (
                    x,
                    origin * height
                    + 98 * math.sin(x / width * 4.2 + band)
                    + x * 0.28
                    + index * 9,
                )
                for x in range(-20, width + 21, 8)
            ]
            draw.line(
                points,
                fill=(202, 217, 243, 35) if dark else (255, 246, 214, 96),
                width=1,
            )
    surface.alpha_composite(lines)
    return _apply_grain(surface)


def _foil_star(canvas: Image.Image, center: tuple[int, int], radius: int) -> None:
    """A rounded gold-foil star with an engraved edge and a small specular glint."""
    size = radius * 2 + 20
    mask_canvas = Image.new("RGBA", (size, size))
    _star(mask_canvas, (size // 2, size // 2), radius, "white", angle=-78)
    mask = mask_canvas.getchannel("A")
    face = _gradient(mask.size, "#fff0ab", "#bd8542")
    face.putalpha(mask)
    bevel = _gradient(mask.size, "#fffadc", "#9e6b3d")
    bevel.putalpha(ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(5))))
    face.alpha_composite(bevel)
    position = (center[0] - size // 2, center[1] - size // 2)
    shadow = Image.new("RGBA", mask.size, "#724866")
    shadow.putalpha(
        mask.filter(ImageFilter.GaussianBlur(3)).point(lambda a: a * 70 // 255)
    )
    canvas.alpha_composite(shadow, (position[0], position[1] + 3))
    canvas.alpha_composite(face, position)


def _raised_panel(
    canvas: Image.Image, box: tuple[int, int, int, int], colors: dict[str, str]
) -> None:
    left, top, right, bottom = box
    size = (right - left + 32, bottom - top + 32)
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (16, 16, size[0] - 16, size[1] - 16), radius=26, fill=255
    )
    # Take the actual card surface beneath the text, so the inlay shares its
    # pigments and motifs rather than covering them with a white rectangle.
    face = canvas.crop((left - 16, top - 16, right + 16, bottom + 16))
    tint = Image.new("RGBA", face.size, colors["paper"])
    tint.putalpha(156)
    face.alpha_composite(tint)
    shadow = Image.new("RGBA", size, colors["shadow"])
    shadow.putalpha(
        mask.filter(ImageFilter.GaussianBlur(8)).point(lambda a: a * 35 // 255)
    )
    canvas.alpha_composite(shadow, (left - 16, top - 10))
    face = _apply_grain(face)
    face.putalpha(mask)
    canvas.alpha_composite(face, (left - 16, top - 16))
    d = ImageDraw.Draw(canvas)
    dark = colors["paper"] == _PALETTES["银河档案"]["paper"]
    d.rounded_rectangle(
        box, radius=26, outline="#9c95aa" if dark else "#bd92ac", width=2
    )
    d.rounded_rectangle(
        (left + 5, top + 5, right - 5, bottom - 5),
        radius=22,
        outline="#56617b" if dark else "#f4dbc6",
        width=1,
    )
    d.line(
        (left + 27, top + 3, right - 27, top + 3),
        fill="#bec7d7" if dark else "#f9e7c7",
        width=1,
    )


def _fit_artwork(art: Image.Image, bounds: tuple[int, int]) -> Image.Image:
    """Fill the available portrait area without cropping or distorting the art."""
    art = art.convert("RGBA")
    scale = min(bounds[0] / art.width, bounds[1] / art.height)
    size = (
        min(bounds[0], max(1, round(art.width * scale))),
        min(bounds[1], max(1, round(art.height * scale))),
    )
    # Small, limited-color game sprites retain their pixel edges. Illustrated
    # and photographic assets use smooth resampling, including enlargement.
    pixel_art = max(art.size) <= 128 and art.getcolors(256) is not None
    return art.resize(
        size, Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS
    )


def _character_scene(
    size: tuple[int, int], colors: dict[str, str], dark: bool
) -> Image.Image:
    """An original Dream Land window: starlit sky, drifting clouds and star trails."""
    width, height = size
    scene = _dream_surface(size, dark)
    _glow(
        scene,
        (width // 3, height // 3),
        (220, 270),
        "#997bde" if dark else "#ffe9b9",
        105,
    )
    _glow(
        scene,
        (width * 4 // 5, height * 2 // 3),
        (270, 180),
        "#66acc8" if dark else "#efafd2",
        80,
    )
    ribbons = Image.new("RGBA", size)
    ribbon_draw = ImageDraw.Draw(ribbons)
    for index, hue in enumerate(
        ((167, 132, 221, 54), (105, 193, 209, 55), (234, 181, 193, 36))
        if dark
        else ((255, 225, 159, 105), (234, 177, 218, 82), (179, 222, 225, 110))
    ):
        points = [
            (x, height * 0.36 + math.sin(x / width * 4.5 - 0.8) * 140 + index * 72)
            for x in range(-40, width + 41, 8)
        ]
        ribbon_draw.line(points, fill=hue, width=110)
    scene.alpha_composite(ribbons.filter(ImageFilter.GaussianBlur(28)))
    d = ImageDraw.Draw(scene)
    # Two broad cloud banks form a landscape, rather than a pedestal for art.
    d.ellipse(
        (-190, height - 110, width * 3 // 4, height + 200),
        fill="#3d5378" if dark else "#b6d6e0",
    )
    d.ellipse(
        (width // 3, height - 75, width + 160, height + 180),
        fill="#2f4469" if dark else "#d1dde5",
    )
    d.arc(
        (-95, 80, width + 180, height - 60),
        192,
        325,
        fill="#7280b655" if dark else "#fff4dba0",
        width=3,
    )
    d.arc(
        (-35, 116, width + 126, height - 35),
        18,
        133,
        fill="#99c2d380" if dark else "#fff8eac0",
        width=2,
    )
    for i in range(34):
        x, y = 35 + (i * 191) % (width - 70), 30 + (i * 113) % (height - 95)
        radius = 2 if i % 4 else 3
        d.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill="#a8bbdd" if dark else "#fffdf5",
        )
    for x, y, radius in (
        (70, 84, 17),
        (width - 91, 185, 13),
        (87, height - 177, 16),
        (width - 66, height - 95, 20),
    ):
        _foil_star(scene, (x, y), radius)
    _cloud(scene, 16, height - 135, 204, colors)
    _cloud(scene, width - 237, height - 96, 220, colors)
    if dark:
        cx, cy = width - 116, 91
        d.ellipse(
            (cx - 58, cy - 58, cx + 58, cy + 58),
            fill="#436295",
            outline="#90a5c2",
            width=1,
        )
        _foil_star(scene, (cx, cy), 47)
        d.ellipse((cx - 18, cy - 7, cx - 1, cy + 4), fill="#89b78a")
        d.ellipse((cx + 5, cy + 9, cx + 23, cy + 20), fill="#89b78a")
        d.arc((cx - 78, cy - 30, cx + 78, cy + 39), 12, 165, fill="#c3d4d8", width=2)
    film = Image.new("RGBA", size)
    ImageDraw.Draw(film).polygon(
        ((0, 10), (90, 0), (width, height - 90), (width, height)),
        fill=(255, 255, 249, 28),
    )
    scene.alpha_composite(film.filter(ImageFilter.GaussianBlur(20)))
    return _apply_grain(scene)


def _card_layout(data: dict[str, Any]) -> dict[str, Any]:
    """Size an intrinsic character card, independent of the person drawing it."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name = _text(data.get("name"), 250) or "未命名盟友"
    size = 54
    while size > 30 and draw.textlength(name, font=_font(size, True)) > 708:
        size -= 2
    name_lines = _lines(draw, name, _font(size, True), 708, 2)
    english = _text(data.get("english_name"), 250)
    english_lines = (
        []
        if english.casefold() == name.casefold()
        else _lines(draw, english, _font(23, latin=True), 718, 2)
    )
    english_y = 68 + len(name_lines) * (size + 8) + 8
    hero_top = max(176, english_y + len(english_lines) * 29 + 24)
    source = _text(data.get("source"), 300).removeprefix("《").removesuffix("》")
    source_lines = _lines(draw, source, _font(23), 812, 2)
    source_height = 74 + max(1, len(source_lines)) * 32
    description = str(data.get("description") or "")
    hero_height = (
        800 if description else max(800, CARD_HEIGHT - hero_top - source_height - 54)
    )
    source_top = hero_top + hero_height + 22
    profile_top = source_top + source_height
    maximum = min(
        MAX_CARD_DESCRIPTION_LINES, (MAX_CARD_HEIGHT - profile_top - 132) // 38
    )
    paragraph = _lines(draw, description, _font(26), 832, max(1, maximum))
    needed = (
        profile_top + 88 + len(paragraph) * 38 + 54 if paragraph else profile_top + 32
    )
    height = min(MAX_CARD_HEIGHT, max(CARD_HEIGHT, (needed + 7) // 8 * 8))
    return {
        "height": height,
        "name_size": size,
        "name_lines": name_lines,
        "english_y": english_y,
        "english_lines": english_lines,
        "hero_top": hero_top,
        "hero_height": hero_height,
        "source_top": source_top,
        "source_lines": source_lines,
        "profile_top": profile_top,
        "profile_bottom": height - 54,
        "paragraph": paragraph,
        "excerpt": bool(paragraph and paragraph[-1].endswith("…")),
    }


def render_ally_draw_card(data: dict[str, Any], output_path: Path | str) -> Path:
    """Render a pure character card; all draw and reply instructions stay outside."""
    theme = (
        data.get("theme")
        if data.get("theme") in ALLY_DRAW_CARD_THEMES
        else ALLY_DRAW_CARD_THEMES[0]
    )
    colors, dark = _PALETTES[theme], theme == "银河档案"
    layout = _card_layout(data)
    height = layout["height"]
    canvas = Image.new("RGBA", (CARD_WIDTH, height), colors["outer"])
    _material_frame(canvas, dark)
    paper_mask = Image.new("L", canvas.size)
    ImageDraw.Draw(paper_mask).rounded_rectangle(
        (44, 42, 980, height - 42), radius=32, fill=255
    )
    paper = _dream_surface(canvas.size, dark)
    paper.putalpha(paper_mask)
    canvas.alpha_composite(paper)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (48, 46, 976, height - 46), radius=29, outline=colors["line"], width=2
    )
    # The character's own name is the title of the card.
    for index, line in enumerate(layout["name_lines"]):
        draw.text(
            (78, 68 + index * (layout["name_size"] + 8)),
            line,
            font=_font(layout["name_size"], True),
            fill=colors["ink"],
            anchor="lt",
        )
    for index, line in enumerate(layout["english_lines"]):
        draw.text(
            (80, layout["english_y"] + index * 29),
            line,
            font=_font(23, latin=True),
            fill=colors["accent"],
            anchor="lt",
        )
    draw.rounded_rectangle(
        (828, 66, 950, 116),
        radius=17,
        fill=colors["soft"],
        outline=colors["line"],
        width=2,
    )
    draw.text(
        (889, 77),
        f"#{_number(data.get('catalog_id')):04d}",
        font=_font(24, True, True),
        fill=colors["accent"],
        anchor="mt",
    )

    top, scene_height = layout["hero_top"], layout["hero_height"]
    scene = _character_scene((904, scene_height), colors, dark)
    mask = Image.new("L", scene.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, 903, scene_height - 1), radius=28, fill=255
    )
    scene.putalpha(mask)
    canvas.alpha_composite(scene, (60, top))
    draw.rounded_rectangle(
        (60, top, 964, top + scene_height),
        radius=28,
        outline="#8d8eba" if dark else "#d6c1cd",
        width=2,
    )
    draw.line(
        (89, top + 3, 935, top + 3), fill="#ccd7eb" if dark else "#fffdf3", width=2
    )
    artwork = data.get("artwork")
    if isinstance(artwork, bytes) and artwork:
        with Image.open(BytesIO(artwork)) as source:
            art = _fit_artwork(source, (816, scene_height - 80))
        padded = Image.new("RGBA", (art.width + 40, art.height + 40))
        padded.alpha_composite(art, (20, 20))
        alpha = padded.getchannel("A")
        position = (
            (CARD_WIDTH - padded.width) // 2,
            top + (scene_height - padded.height) // 2,
        )
        shadow = Image.new("RGBA", padded.size, colors["shadow"])
        shadow.putalpha(
            alpha.filter(ImageFilter.GaussianBlur(12)).point(lambda a: a * 105 // 255)
        )
        canvas.alpha_composite(shadow, (position[0], position[1] + 12))
        border = Image.new("RGBA", padded.size, "#fffbed")
        border.putalpha(alpha.filter(ImageFilter.MaxFilter(9)))
        canvas.alpha_composite(border, position)
        canvas.alpha_composite(padded, position)
    else:
        _star(canvas, (512, top + scene_height // 2 - 20), 118, colors["gold"])
        _center(
            draw,
            "素材暂时不可用",
            top + scene_height // 2 + 122,
            _font(24),
            colors["muted"],
        )

    source_top = layout["source_top"]
    if layout["source_lines"]:
        for left, right in ((116, 420), (604, 908)):
            draw.line(
                (left, source_top + 15, right, source_top + 15),
                fill="#8993b1" if dark else "#be94aa",
                width=1,
            )
        _foil_star(canvas, (98, source_top + 15), 7)
        _foil_star(canvas, (926, source_top + 15), 7)
        _center(draw, "首次登场", source_top + 5, _font(16), colors["muted"])
        for index, line in enumerate(layout["source_lines"]):
            _center(
                draw,
                f"《{line}》" if len(layout["source_lines"]) == 1 else line,
                source_top + 33 + index * 32,
                _font(23),
                colors["ink"],
            )
    if layout["paragraph"]:
        profile_top, bottom = layout["profile_top"], layout["profile_bottom"]
        _raised_panel(canvas, (66, profile_top, 958, bottom), colors)
        draw.text(
            (96, profile_top + 24),
            "简介",
            font=_font(24, True),
            fill=colors["accent"],
            anchor="lt",
        )
        draw.line(
            (174, profile_top + 40, 926, profile_top + 40), fill=colors["line"], width=1
        )
        for index, line in enumerate(layout["paragraph"]):
            draw.text(
                (96, profile_top + 78 + index * 38),
                line,
                font=_font(26),
                fill=colors["ink"],
                anchor="lt",
            )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}-tmp-{uuid.uuid4().hex}.png")
    try:
        canvas.convert("RGB").save(temporary, format="PNG", optimize=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
