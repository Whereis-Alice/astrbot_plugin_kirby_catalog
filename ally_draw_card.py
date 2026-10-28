"""Local, bounded Pillow artwork for the daily ally collection card."""

from __future__ import annotations

import math
import re
import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from .wiki_content import inline_markup_plain

ALLY_DRAW_CARD_THEMES = ("星愿收藏", "银河档案")
CARD_WIDTH = 1024
CARD_HEIGHT = 1456

_PALETTES = {
    "星愿收藏": {
        "outer": "#eedfe8",
        "paper": "#fff9ee",
        "ink": "#493957",
        "muted": "#7f738a",
        "accent": "#d66098",
        "line": "#ecdae5",
        "soft": "#f9eaf0",
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
        "paper": "#192c57",
        "ink": "#fff4dd",
        "muted": "#b2c1dc",
        "accent": "#f2a6d0",
        "line": "#40517b",
        "soft": "#263b66",
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


def _description(value: str, name: str) -> str:
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
    return _text(
        re.sub(
            r"(?<=[\u3400-\u9fff])[（(][A-Za-z][A-Za-z0-9'’:&.,/\s-]*[）)]",
            "",
            plain,
        ),
        600,
    )


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
        "description": _description(description, name or display_name),
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
    remaining = _text(text)
    result: list[str] = []
    while remaining and len(result) < max_lines:
        if draw.textlength(remaining, font=font) <= width:
            result.append(remaining)
            break
        last = len(result) == max_lines - 1
        suffix = "…" if last else ""
        low, high, best = 1, len(remaining), 1
        while low <= high:
            middle = (low + high) // 2
            if (
                draw.textlength(remaining[:middle].rstrip() + suffix, font=font)
                <= width
            ):
                best, low = middle, middle + 1
            else:
                high = middle - 1
        cut = best
        if not last:
            natural = [
                index + 1
                for index, char in enumerate(remaining[:best])
                if char.isspace() or char in "，。；、/（("
            ]
            if natural and natural[-1] >= best * 0.65:
                cut = natural[-1]
        result.append(remaining[:cut].rstrip() + suffix)
        remaining = remaining[cut:].lstrip()
        if last:
            break
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


def _tracked(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    y: int,
    size: int,
    color: str,
    tracking: float = 2,
) -> None:
    font = _font(size, latin=True)
    for char in text:
        draw.text((x, y), char, font=font, fill=color, anchor="lt")
        x += draw.textlength(char, font=font) + tracking


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


def _warp_star(canvas: Image.Image, colors: dict[str, str]) -> None:
    """A pillowy golden Warp Star, with a warm rim and a glossy upper face."""
    size = 556
    mask = Image.new("L", (size * 3, size * 3))
    points = []
    for index in range(10):
        theta = math.radians(-90 + index * 36)
        radius = (248 if index % 2 == 0 else 134) * 3
        points.append(
            (
                size * 1.5 + math.cos(theta) * radius,
                size * 1.5 + math.sin(theta) * radius,
            )
        )
    ImageDraw.Draw(mask).polygon(_rounded_points(points, 0.20), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    position = (234, 254)
    shadow = Image.new("RGBA", (size, size), colors["shadow"])
    shadow.putalpha(
        mask.filter(ImageFilter.GaussianBlur(16)).point(lambda a: a * 85 // 255)
    )
    canvas.alpha_composite(shadow, (position[0], position[1] + 24))
    rim = Image.new("RGBA", (size, size), colors["star_bottom"])
    rim.putalpha(mask)
    canvas.alpha_composite(rim, (position[0], position[1] + 9))
    face = _gradient((size, size), "#fff495", colors["gold"])
    face.putalpha(mask)
    canvas.alpha_composite(face, position)
    shine = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(shine)
    d.ellipse((227, 70, 256, 100), fill="#fffde0")
    d.ellipse((107, 227, 154, 239), fill="#fffde0")
    shine.putalpha(
        Image.composite(shine.getchannel("A"), Image.new("L", shine.size), mask)
    )
    canvas.alpha_composite(shine, position)


def _cloud(
    canvas: Image.Image, x: int, y: int, width: int, colors: dict[str, str]
) -> None:
    tile = Image.new("RGBA", (width, max(64, width // 3)))
    d = ImageDraw.Draw(tile)
    height = tile.height
    for offset, fill in ((9, colors["cloud_shadow"]), (0, colors["cloud"])):
        d.rounded_rectangle(
            (0, height * 0.36 + offset, width, height * 0.86 + offset),
            radius=height // 3,
            fill=fill,
        )
        for cx, cy, r in (
            (0.18, 0.43, 0.26),
            (0.37, 0.32, 0.32),
            (0.61, 0.40, 0.29),
            (0.80, 0.45, 0.24),
        ):
            cx, cy, r = cx * width, cy * height + offset, r * height
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    canvas.alpha_composite(tile, (x, y))


def _pop_star(canvas: Image.Image, colors: dict[str, str]) -> None:
    """Star-shaped Pop Star in its blue halo, inspired by the official portal."""
    d = ImageDraw.Draw(canvas)
    d.ellipse((742, 312, 934, 504), fill="#375b96", outline="#6283b9", width=2)
    _star(canvas, (838, 408), 82, "#ffe076", angle=-76)
    # Small land patches stay within the star's broad central area.
    d.ellipse((800, 389, 831, 410), fill="#8cbf83")
    d.ellipse((842, 416, 878, 440), fill="#8cbf83")
    d.arc((720, 356, 954, 475), 12, 165, fill="#a6c6d2", width=3)
    d.arc((720, 356, 954, 475), 192, 338, fill="#a6c6d2", width=2)


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


def render_ally_draw_card(data: dict[str, Any], output_path: Path | str) -> Path:
    """Draw a local Dream Land keepsake and atomically publish its PNG."""
    theme = (
        data.get("theme")
        if data.get("theme") in ALLY_DRAW_CARD_THEMES
        else ALLY_DRAW_CARD_THEMES[0]
    )
    colors = _PALETTES[theme]
    dark = theme == "银河档案"
    canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), colors["outer"])
    paper_mask = Image.new("L", canvas.size)
    ImageDraw.Draw(paper_mask).rounded_rectangle(
        (24, 20, 1000, 1436), radius=44, fill=255
    )
    paper = Image.new("RGBA", canvas.size, colors["paper"])
    paper.putalpha(paper_mask)
    canvas.alpha_composite(paper)
    sky = Image.new("RGBA", (CARD_WIDTH, 820))
    sky.alpha_composite(_gradient((CARD_WIDTH, 600), colors["paper"], colors["sky"]))
    sky.alpha_composite(
        _gradient((CARD_WIDTH, 220), colors["sky"], colors["paper"]), (0, 600)
    )
    sky_mask = paper_mask.crop((0, 0, CARD_WIDTH, 820))
    sky.putalpha(sky_mask)
    canvas.alpha_composite(sky)
    _glow(canvas, (480, 476), (245, 140), "#a59fee" if dark else "#f9cadd", 52)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (40, 36, 984, 1420), radius=34, outline=colors["line"], width=2
    )
    _star(canvas, (84, 78), 18, colors["gold"])
    _tracked(draw, "KIRBY  /  DAILY ALLY", 118, 68, 16, colors["muted"], 1.7)
    draw.text(
        (940, 67),
        _text(data.get("date"), 32),
        font=_font(20, latin=True),
        fill=colors["muted"],
        anchor="rt",
    )
    # Puffy lettering echoes the round forms on Nintendo's official Kirby site.
    title_font = _font(60, True)
    draw.text(
        (81, 140),
        "今日盟友",
        font=title_font,
        fill="#a86396" if dark else "#ebbacb",
        stroke_width=3,
        stroke_fill="#a86396" if dark else "#ebbacb",
        anchor="lt",
    )
    draw.text(
        (78, 134),
        "今日盟友",
        font=title_font,
        fill="#ffc7e5" if dark else "#df70a5",
        stroke_width=2,
        stroke_fill="#744675" if dark else "#fffdf6",
        anchor="lt",
    )
    draw.text(
        (80, 220),
        "把今天的相遇，收藏成一颗星。",
        font=_font(23),
        fill=colors["muted"],
        anchor="lt",
    )
    draw.rounded_rectangle(
        (772, 132, 940, 208),
        radius=25,
        fill=colors["soft"],
        outline=colors["line"],
        width=2,
    )
    draw.text((856, 146), "图鉴编号", font=_font(15), fill=colors["muted"], anchor="mt")
    draw.text(
        (856, 173),
        f"#{_number(data.get('catalog_id')):04d}",
        font=_font(26, True, True),
        fill=colors["accent"],
        anchor="mt",
    )

    if dark:
        for index in range(48):
            x = 76 + (index * 173) % 872
            y = 265 + (index * 97) % 451
            r = 2 if index % 4 else 3
            draw.ellipse(
                (x - r, y - r, x + r, y + r), fill="#7d97c9" if index % 3 else "#edd5a8"
            )
        _pop_star(canvas, colors)
        draw.arc((106, 348, 910, 671), 18, 160, fill="#7283b3", width=2)
        draw.arc((158, 286, 864, 700), 203, 325, fill="#566b9c", width=2)
    else:
        _cloud(canvas, 70, 333, 210, colors)
        _cloud(canvas, 760, 393, 194, colors)
        draw.arc((140, 307, 894, 650), 201, 328, fill="#f3e6eb", width=3)
    for x, y, r, angle in (
        (158, 279, 20, -78),
        (738, 282, 16, -72),
        (896, 563, 29, -76),
        (131, 570, 14, -90),
        (798, 691, 12, -73),
    ):
        _star(canvas, (x, y), r, colors["gold"], angle=angle)
    _warp_star(canvas, colors)
    artwork = data.get("artwork")
    if isinstance(artwork, bytes) and artwork:
        with Image.open(BytesIO(artwork)) as source:
            art = source.convert("RGBA")
        art.thumbnail((570, 404), Image.Resampling.LANCZOS)
        # Extra transparent padding lets the sticker rim and shadow extend past
        # the original bounding box rather than cutting off hands or feet.
        padded = Image.new("RGBA", (art.width + 48, art.height + 48))
        padded.alpha_composite(art, (24, 24))
        alpha = padded.getchannel("A")
        position = ((CARD_WIDTH - padded.width) // 2, 503 - padded.height // 2)
        shadow = Image.new("RGBA", padded.size, colors["shadow"])
        shadow.putalpha(
            alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda a: a * 100 // 255)
        )
        canvas.alpha_composite(shadow, (position[0], position[1] + 14))
        outline = Image.new("RGBA", padded.size, "#fff9e9")
        outline.putalpha(alpha.filter(ImageFilter.MaxFilter(9)))
        canvas.alpha_composite(outline, position)
        canvas.alpha_composite(padded, position)
    else:
        draw = ImageDraw.Draw(canvas)
        _center(draw, "?", 442, _font(96, True, True), "#c88332")
        _center(draw, "素材暂时不可用", 580, _font(22), "#96622e")
    _cloud(canvas, 72, 690, 308, colors)
    _cloud(canvas, 643, 704, 308, colors)
    draw = ImageDraw.Draw(canvas)
    # A tiny dotted trail joins the starry scene to the keepsake page.
    for x in range(406, 619, 26):
        draw.ellipse((x, 733, x + 4, 737), fill=colors["accent"])
    badge = _text(data.get("status"))
    badge_font = _font(21, True)
    badge_width = max(166, int(draw.textlength(badge, font=badge_font)) + 62)
    left = (CARD_WIDTH - badge_width) // 2
    pity = badge == "保底邂逅"
    chip_fill = ("#715332" if dark else "#ffedb9") if pity else colors["chip"]
    chip_ink = ("#ffdf83" if dark else "#9b682c") if pity else colors["chip_ink"]
    draw.rounded_rectangle(
        (left, 760, left + badge_width, 806), radius=23, fill=chip_fill
    )
    _center(draw, badge, 770, badge_font, chip_ink)

    name = _text(data.get("name"), 250)
    name_size = 54
    while name_size > 30 and draw.textlength(name, font=_font(name_size, True)) > 844:
        name_size -= 2
    name_font = _font(name_size, True)
    name_lines = _lines(draw, name, name_font, 844, 2)
    for index, line in enumerate(name_lines):
        _center(draw, line, 829 + index * (name_size + 7), name_font, colors["ink"])
    english_y = max(904, 829 + len(name_lines) * (name_size + 7) + 8)
    english_lines = _lines(
        draw, _text(data.get("english_name")), _font(22, latin=True), 830, 1
    )
    if english_lines:
        _center(
            draw, english_lines[0], english_y, _font(22, latin=True), colors["accent"]
        )
    source = _text(data.get("source"), 300)
    if source:
        source = source.removeprefix("《").removesuffix("》")
        source_font = _font(23)
        source_lines = _lines(draw, source, source_font, 790, 1)
        _center(draw, "初次登场", 952, _font(16), colors["muted"])
        _center(draw, f"《{source_lines[0]}》", 978, source_font, colors["ink"])

    draw.rounded_rectangle((78, 1030, 946, 1204), radius=28, fill=colors["soft"])
    _star(canvas, (114, 1068), 13, colors["accent"])
    draw.text(
        (139, 1052),
        "伙伴小档案",
        font=_font(18, True),
        fill=colors["accent"],
        anchor="lt",
    )
    description = _text(data.get("description"), 600)
    paragraph = _lines(
        draw, description or _text(data.get("status_note")), _font(24), 810, 3
    )
    y = 1092 + (3 - len(paragraph)) * 16
    for line in paragraph:
        draw.text((108, y), line, font=_font(24), fill=colors["ink"], anchor="lt")
        y += 33
    draw.ellipse((80, 1230, 136, 1286), fill=colors["chip"])
    owner = _text(data.get("nickname"), 150) or "旅行者"
    draw.text(
        (108, 1243), owner[:1], font=_font(25, True), fill=colors["accent"], anchor="mt"
    )
    draw.text(
        (154, 1229), "今日收藏者", font=_font(16), fill=colors["muted"], anchor="lt"
    )
    owner_lines = _lines(draw, owner, _font(26, True), 488, 1)
    draw.text(
        (154, 1257),
        owner_lines[0],
        font=_font(26, True),
        fill=colors["ink"],
        anchor="lt",
    )
    draw.text(
        (936, 1229), "今日剩余", font=_font(16), fill=colors["muted"], anchor="rt"
    )
    draw.text(
        (936, 1257),
        f"{_number(data.get('remaining'))} 次",
        font=_font(30, True),
        fill=colors["accent"],
        anchor="rt",
    )
    draw.line((80, 1306, 944, 1306), fill=colors["line"], width=2)
    total = _number(data.get("total"))
    collected = min(total, _number(data.get("collected")))
    if total and data.get("collected") is not None:
        draw.text(
            (80, 1324),
            "收藏进度",
            font=_font(18, True),
            fill=colors["muted"],
            anchor="lt",
        )
        draw.text(
            (936, 1322),
            f"{collected:,} / {total:,}",
            font=_font(22, True, True),
            fill=colors["accent"],
            anchor="rt",
        )
        draw.rounded_rectangle((80, 1358, 944, 1368), radius=5, fill=colors["soft"])
        if collected:
            end = 80 + max(10, round(864 * collected / total))
            draw.rounded_rectangle(
                (80, 1358, end, 1368), radius=5, fill=colors["accent"]
            )
            _star(canvas, (end, 1362), 10, colors["gold"])
    else:
        _center(draw, _text(data.get("status_note")), 1338, _font(21), colors["muted"])
    hint = _text(data.get("wiki_hint"), 200)
    if hint:
        for index, line in enumerate(_lines(draw, hint, _font(16), 850, 2)):
            _center(draw, line, 1386 + index * 23, _font(16), colors["muted"])
    else:
        _center(
            draw,
            "A LITTLE STAR, A NEW STORY.",
            1392,
            _font(14, latin=True),
            colors["muted"],
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
