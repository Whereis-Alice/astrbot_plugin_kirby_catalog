"""Four independently composed Kirby cards, drawn locally from catalogue data.

Loaded by ally_draw_card only when an alternate design is requested; common
text measurement, artwork preparation and atomic saving stay in that module.
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .ally_draw_card import (
    CARD_HEIGHT,
    CARD_WIDTH,
    MAX_CARD_DESCRIPTION_LINES,
    MAX_CARD_HEIGHT,
    _apply_grain,
    _fit_artwork,
    _foil_star,
    _font,
    _gradient,
    _lines,
    _number,
    _star,
    _text,
)


def alternate_card_layout(data: dict[str, Any]) -> dict[str, Any]:
    theme = data["theme"]
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name = _text(data.get("name"), 250) or "未命名盟友"
    size = 54
    name_width = 708 if theme == "星海航记" else 816
    while size > 30 and draw.textlength(name, font=_font(size, True)) > name_width:
        size -= 2
    name_lines = _lines(draw, name, _font(size, True), name_width, 2)
    english = _text(data.get("english_name"), 250)
    english_lines = (
        []
        if english.casefold() == name.casefold()
        else _lines(draw, english, _font(23, latin=True), name_width, 2)
    )
    title_height = len(name_lines) * (size + 8) + len(english_lines) * 29 + 28
    description = str(data.get("description") or "")
    hero_height = 760 if theme in {"梦泉琉璃", "毛线物语"} else 720
    if not description:
        hero_height += 120
    below = theme in {"梦泉琉璃", "波普漫画"}
    title_top = 84
    hero_top = 58 if below else title_top + title_height + 30
    if below:
        title_top = hero_top + hero_height + 30
    source_top = title_top + title_height + 32 if below else hero_top + hero_height + 28
    source = _text(data.get("source"), 300).removeprefix("《").removesuffix("》")
    source_lines = _lines(draw, source, _font(23), 808, 2)
    profile_top = source_top + (40 + len(source_lines) * 32 if source_lines else 8) + 26
    max_lines = min(
        MAX_CARD_DESCRIPTION_LINES, (MAX_CARD_HEIGHT - profile_top - 140) // 38
    )
    paragraph = _lines(draw, description, _font(26), 816, max(1, max_lines))
    needed = profile_top + (102 + len(paragraph) * 38 if paragraph else 0) + 58
    height = min(MAX_CARD_HEIGHT, max(CARD_HEIGHT, (needed + 7) // 8 * 8))
    return {
        "height": height,
        "name_size": size,
        "name_lines": name_lines,
        "english_lines": english_lines,
        "title_top": title_top,
        "title_height": title_height,
        "hero_top": hero_top,
        "hero_height": hero_height,
        "source_top": source_top,
        "source_lines": source_lines,
        "profile_top": profile_top,
        "profile_bottom": height - 52,
        "paragraph": paragraph,
        "excerpt": bool(paragraph and paragraph[-1].endswith("…")),
    }


def _art(
    canvas: Image.Image,
    data: dict[str, Any],
    box: tuple[int, int, int, int],
    *,
    edge: str = "#fff0cd",
    shadow: str = "#24334c",
) -> None:
    left, top, right, bottom = box
    raw = data.get("artwork")
    if not isinstance(raw, bytes) or not raw:
        _foil_star(canvas, ((left + right) // 2, (top + bottom) // 2), 98)
        ImageDraw.Draw(canvas).text(
            ((left + right) // 2, (top + bottom) // 2 + 120),
            "素材暂时不可用",
            font=_font(24),
            fill=edge,
            anchor="mt",
        )
        return
    with Image.open(BytesIO(raw)) as image:
        art = _fit_artwork(image, (right - left, bottom - top))
    padded = Image.new("RGBA", (art.width + 36, art.height + 36))
    padded.alpha_composite(art, (18, 18))
    alpha = padded.getchannel("A")
    position = ((left + right - padded.width) // 2, (top + bottom - padded.height) // 2)
    shade = Image.new("RGBA", padded.size, shadow)
    shade.putalpha(
        alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda a: a * 95 // 255)
    )
    canvas.alpha_composite(shade, (position[0] + 4, position[1] + 12))
    outline = Image.new("RGBA", padded.size, edge)
    outline.putalpha(alpha.filter(ImageFilter.MaxFilter(9)))
    canvas.alpha_composite(outline, position)
    canvas.alpha_composite(padded, position)


def _title(
    canvas: Image.Image,
    layout: dict[str, Any],
    ink: str,
    secondary: str,
    *,
    x: int = 512,
    centered: bool = True,
) -> None:
    draw = ImageDraw.Draw(canvas)
    y = layout["title_top"]
    anchor = "mt" if centered else "lt"
    for line in layout["name_lines"]:
        draw.text(
            (x, y), line, font=_font(layout["name_size"], True), fill=ink, anchor=anchor
        )
        y += layout["name_size"] + 8
    for line in layout["english_lines"]:
        draw.text(
            (x, y + 5), line, font=_font(23, latin=True), fill=secondary, anchor=anchor
        )
        y += 29


def _source(
    canvas: Image.Image, layout: dict[str, Any], ink: str, secondary: str
) -> None:
    if not layout["source_lines"]:
        return
    draw = ImageDraw.Draw(canvas)
    y = layout["source_top"]
    draw.text((512, y), "首次登场", font=_font(17), fill=secondary, anchor="mt")
    for index, line in enumerate(layout["source_lines"]):
        label = f"《{line}》" if len(layout["source_lines"]) == 1 else line
        draw.text(
            (512, y + 31 + index * 32), label, font=_font(23), fill=ink, anchor="mt"
        )


def _profile_text(
    canvas: Image.Image, layout: dict[str, Any], ink: str, accent: str
) -> None:
    if not layout["paragraph"]:
        return
    draw = ImageDraw.Draw(canvas)
    top = layout["profile_top"]
    draw.text((104, top + 25), "简介", font=_font(24, True), fill=accent, anchor="lt")
    for index, line in enumerate(layout["paragraph"]):
        draw.text(
            (104, top + 79 + index * 38), line, font=_font(26), fill=ink, anchor="lt"
        )


def _number_badge(
    canvas: Image.Image,
    data: dict[str, Any],
    xy: tuple[int, int],
    fill: str,
    ink: str,
    *,
    radius: int = 12,
) -> None:
    x, y = xy
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (x, y, x + 126, y + 50), radius=radius, fill=fill, outline=ink, width=2
    )
    draw.text(
        (x + 63, y + 13),
        f"#{_number(data.get('catalog_id')):04d}",
        font=_font(23, True, True),
        fill=ink,
        anchor="mt",
    )


def _inset(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    top_color: str,
    bottom_color: str,
    outline: str,
    *,
    radius: int = 22,
) -> None:
    left, top, right, bottom = box
    face = _apply_grain(
        _gradient((right - left, bottom - top), top_color, bottom_color)
    )
    mask = Image.new("L", face.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, face.width - 1, face.height - 1), radius=radius, fill=255
    )
    face.putalpha(mask)
    canvas.alpha_composite(face, (left, top))
    ImageDraw.Draw(canvas).rounded_rectangle(
        box, radius=radius, outline=outline, width=2
    )


def _glass(data: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    height, top = layout["height"], layout["hero_top"]
    bottom = top + layout["hero_height"]
    canvas = _apply_grain(_gradient((CARD_WIDTH, height), "#143c4d", "#316567"))
    d = ImageDraw.Draw(canvas)
    for inset, color in ((18, "#977148"), (28, "#edcf86"), (40, "#728a72")):
        d.rounded_rectangle(
            (inset, inset, 1024 - inset, height - inset),
            radius=40,
            outline=color,
            width=2,
        )
    for y in range(95, height, 130):
        for x in (54, 970):
            d.polygon(
                ((x, y - 18), (x + 10, y), (x, y + 18), (x - 10, y)), outline="#b99560"
            )
    scene = _gradient((872, layout["hero_height"]), "#749d99", "#296878")
    sd = ImageDraw.Draw(scene)
    colors = ("#9bb5a5", "#b98994", "#dfbc86", "#72b3b7", "#577c9a", "#b7b184")
    center = (436, layout["hero_height"] - 105)
    for index in range(12):
        a, b = math.radians(180 + index * 15), math.radians(195 + index * 15)
        points = (
            center,
            (436 + math.cos(a) * 610, 436 + math.sin(a) * 610),
            (436 + math.cos(b) * 610, 436 + math.sin(b) * 610),
        )
        sd.polygon(points, fill=colors[index % len(colors)])
        sd.line(points + (center,), fill="#d7b677", width=3)
    for radius in (235, 320, 411, 487):
        sd.ellipse(
            (436 - radius, 440 - radius, 436 + radius, 440 + radius),
            outline="#dabf86",
            width=3,
        )
    for y in range(layout["hero_height"] - 120, layout["hero_height"], 19):
        sd.arc((65, y - 24, 807, y + 48), 3, 177, fill="#a4ccc4", width=3)
    scene = _apply_grain(scene)
    mask = Image.new("L", scene.size)
    md = ImageDraw.Draw(mask)
    md.pieslice((0, 0, 871, 871), 180, 360, fill=255)
    md.rectangle((0, 435, 871, scene.height), fill=255)
    scene.putalpha(mask)
    canvas.alpha_composite(scene, (76, top))
    d.arc((76, top, 948, top + 872), 180, 360, fill="#efcf87", width=7)
    d.line(
        ((76, top + 436), (76, bottom), (948, bottom), (948, top + 436)),
        fill="#efcf87",
        width=7,
    )
    _foil_star(canvas, (512, top + 68), 31)
    _art(
        canvas,
        data,
        (138, top + 148, 886, bottom - 38),
        edge="#f4dca4",
        shadow="#173e50",
    )
    y, h = layout["title_top"], layout["title_height"]
    d.polygon(
        (
            (91, y - 16),
            (933, y - 16),
            (960, y + h // 2),
            (933, y + h + 4),
            (91, y + h + 4),
            (64, y + h // 2),
        ),
        fill="#263e4c",
        outline="#d3ad72",
        width=3,
    )
    _title(canvas, layout, "#f9e1ad", "#bfcec0")
    _number_badge(canvas, data, (822, 72), "#274d59", "#f1d59b")
    _source(canvas, layout, "#f6dfb6", "#adcac6")
    if layout["paragraph"]:
        p, b = layout["profile_top"], layout["profile_bottom"]
        _inset(canvas, (76, p, 948, b), "#c0d4bb", "#a7c9c4", "#dfbf87")
        d.line((178, p + 41, 918, p + 41), fill="#678e86", width=1)
        for x in (89, 935):
            for y in range(p + 25, b - 14, 40):
                d.line(((x, y), (x + 5, y + 8), (x, y + 16)), fill="#7ba89d", width=1)
        _profile_text(canvas, layout, "#24444c", "#4e6870")
    return canvas


def _stitches(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: str
) -> None:
    left, top, right, bottom = box
    for x in range(left + 10, right - 10, 17):
        draw.line((x, top, x + 9, top), fill=color, width=2)
        draw.line((x, bottom, x + 9, bottom), fill=color, width=2)
    for y in range(top + 10, bottom - 10, 17):
        draw.line((left, y, left, y + 9), fill=color, width=2)
        draw.line((right, y, right, y + 9), fill=color, width=2)


def _weave(canvas: Image.Image, color: tuple[int, int, int, int]) -> None:
    weave = Image.new("RGBA", canvas.size)
    d = ImageDraw.Draw(weave)
    for x in range(0, canvas.width, 5):
        d.line((x, 0, x, canvas.height), fill=color, width=1)
    for y in range(0, canvas.height, 5):
        d.line((0, y, canvas.width, y), fill=color, width=1)
    canvas.alpha_composite(weave)


def _yarn(data: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    height, top = layout["height"], layout["hero_top"]
    bottom = top + layout["hero_height"]
    canvas = _apply_grain(_gradient((1024, height), "#d3a997", "#debda0"))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(
        (26, 24, 998, height - 24),
        radius=44,
        fill="#ead1ab",
        outline="#a87b6e",
        width=3,
    )
    swatches = ("#bbd1c6", "#dea6ad", "#cac7a5", "#c5b4cd")
    for index, y in enumerate(range(44, height - 60, 148)):
        for x in (43, 939):
            d.rounded_rectangle(
                (x, y, x + 42, min(height - 42, y + 140)),
                radius=8,
                fill=swatches[index % 4],
            )
    _stitches(d, (36, 36, 988, height - 36), "#fff0d0")
    d.rounded_rectangle((99, top - 4, 925, bottom + 7), radius=250, fill="#b89d94")
    d.rounded_rectangle(
        (92, top - 11, 918, bottom),
        radius=250,
        fill="#c3d3bd",
        outline="#8ba595",
        width=3,
    )
    for index in range(4):
        x = 155 + index * 190
        d.rectangle(
            (x, top + 200, x + 95, bottom - 110), fill=("#cdddd0", "#b3caba")[index % 2]
        )
    for angle in range(0, 360, 5):
        d.arc(
            (105, top + 2, 905, bottom - 13),
            angle,
            angle + 2.7,
            fill="#f3e5ba",
            width=4,
        )
    _weave(canvas, (255, 248, 215, 24))
    y, h = layout["title_top"], layout["title_height"]
    d.polygon(((110, y - 23), (914, y - 23), (944, y + h), (80, y + h)), fill="#ae777e")
    d.rounded_rectangle(
        (98, y - 20, 926, y + h - 6),
        radius=20,
        fill="#dba5a8",
        outline="#a86c74",
        width=2,
    )
    _stitches(d, (110, y - 9, 914, y + h - 17), "#fce4bb")
    _title(canvas, layout, "#613f49", "#7a525e")
    _art(
        canvas,
        data,
        (136, top + 55, 888, bottom - 55),
        edge="#fae5be",
        shadow="#6c7973",
    )
    for cx, cy, color in ((104, top + 147, "#d08a8c"), (919, bottom - 94, "#8fa7b4")):
        d.ellipse(
            (cx - 26, cy - 26, cx + 26, cy + 26), fill=color, outline="#efe0b9", width=3
        )
        for dx in (-7, 7):
            for dy in (-7, 7):
                d.ellipse(
                    (cx + dx - 2, cy + dy - 2, cx + dx + 2, cy + dy + 2), fill="#665354"
                )
        d.line((cx - 7, cy - 7, cx + 7, cy + 7), fill="#f8e6c1", width=2)
    _number_badge(canvas, data, (826, 30), "#b8cdc0", "#516e68", radius=7)
    _source(canvas, layout, "#66494b", "#8b6661")
    if layout["paragraph"]:
        p, b = layout["profile_top"], layout["profile_bottom"]
        _inset(canvas, (78, p, 948, b), "#e8c1b4", "#e4cbae", "#a87a77", radius=12)
        _stitches(d, (88, p + 10, 938, b - 10), "#fae6c2")
        d.line((180, p + 40, 918, p + 40), fill="#bb9190", width=1)
        # The pocket has its own textile grain, below the text.
        cloth = Image.new("RGBA", canvas.size)
        cd = ImageDraw.Draw(cloth)
        for y in range(p + 62, b, 6):
            cd.line((92, y, 932, y), fill=(117, 81, 75, 10), width=1)
        canvas.alpha_composite(cloth)
        _profile_text(canvas, layout, "#614747", "#975768")
    return canvas


def _halftone(
    canvas: Image.Image, box: tuple[int, int, int, int], color: str, step: int = 15
) -> None:
    left, top, right, bottom = box
    d = ImageDraw.Draw(canvas)
    for row, y in enumerate(range(top, bottom, step)):
        for x in range(left + (row % 2) * (step // 2), right, step):
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)


def _comic(data: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    height, top = layout["height"], layout["hero_top"]
    bottom = top + layout["hero_height"]
    canvas = _apply_grain(_gradient((1024, height), "#f2d779", "#efc69a"))
    d = ImageDraw.Draw(canvas)
    _halftone(canvas, (20, 20, 1004, height - 20), "#d8b572", 18)
    d.rounded_rectangle(
        (23, 22, 1000, height - 22), radius=29, outline="#303b50", width=6
    )
    box = ((50, top + 15), (959, top), (977, bottom - 15), (67, bottom + 10))
    d.polygon([(x + 9, y + 10) for x, y in box], fill="#333f54")
    d.polygon(box, fill="#91ced6", outline="#334256", width=5)
    rays = Image.new("RGBA", canvas.size)
    rd = ImageDraw.Draw(rays)
    for index in range(20):
        a, b = index * math.tau / 20, (index + 0.50) * math.tau / 20
        rd.polygon(
            (
                (512, top + 340),
                (512 + math.cos(a) * 1100, top + 340 + math.sin(a) * 1100),
                (512 + math.cos(b) * 1100, top + 340 + math.sin(b) * 1100),
            ),
            fill="#b8e0ce",
        )
    mask = Image.new("L", canvas.size)
    ImageDraw.Draw(mask).polygon(box, fill=255)
    rays.putalpha(
        Image.composite(rays.getchannel("A"), Image.new("L", canvas.size), mask)
    )
    canvas.alpha_composite(rays)
    _halftone(canvas, (82, top + 38, 224, bottom - 30), "#6bafbf", 16)
    _halftone(canvas, (810, top + 30, 940, bottom - 30), "#6bafbf", 16)
    for cx, cy, radius in ((125, top + 133, 41), (900, bottom - 97, 50)):
        _star(canvas, (cx + 4, cy + 5), radius, "#35475d")
        _star(canvas, (cx, cy), radius, "#ffe38e", outline="#374257")
    _art(
        canvas,
        data,
        (122, top + 45, 902, bottom - 44),
        edge="#fff0be",
        shadow="#35577c",
    )
    y, h = layout["title_top"], layout["title_height"]
    band = ((54, y - 11), (949, y - 28), (970, y + h - 1), (74, y + h + 12))
    d.polygon([(x + 6, yy + 7) for x, yy in band], fill="#303b50")
    d.polygon(band, fill="#ce6464", outline="#303b50", width=4)
    _title(canvas, layout, "#fff1b7", "#ffe0b9")
    _number_badge(canvas, data, (831, top + 26), "#ffe193", "#344058", radius=2)
    _source(canvas, layout, "#3c4253", "#806450")
    if layout["paragraph"]:
        p, b = layout["profile_top"], layout["profile_bottom"]
        d.rounded_rectangle((84, p + 8, 960, b + 7), radius=20, fill="#3d4255")
        _inset(canvas, (72, p, 948, b), "#f3dca5", "#efd1a0", "#3d4255", radius=20)
        d.polygon(((782, p + 2), (841, p - 23), (829, p + 2)), fill="#f3dca5")
        d.line(((782, p), (841, p - 23), (829, p)), fill="#3d4255", width=2)
        d.rounded_rectangle((91, p + 14, 174, p + 59), radius=8, fill="#eeba72")
        _profile_text(canvas, layout, "#3f3e4b", "#895344")
    return canvas


def _ticket(data: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    height, top = layout["height"], layout["hero_top"]
    bottom = top + layout["hero_height"]
    canvas = _apply_grain(_gradient((1024, height), "#173c50", "#284f59"))
    d = ImageDraw.Draw(canvas)
    for x in range(36, 1000, 40):
        d.line((x, 36, x, height - 36), fill="#345e68", width=1)
    for y in range(36, height - 30, 40):
        d.line((36, y, 988, y), fill="#345e68", width=1)
    d.rounded_rectangle(
        (22, 20, 1002, height - 20), radius=22, outline="#c4ac78", width=4
    )
    d.rounded_rectangle(
        (34, 32, 990, height - 32), radius=14, outline="#78938b", width=1
    )
    y, h = layout["title_top"], layout["title_height"]
    _inset(
        canvas, (61, y - 20, 963, y + h + 3), "#ddc696", "#c8b58d", "#ecd6a4", radius=5
    )
    _title(canvas, layout, "#294857", "#657066", x=87, centered=False)
    _number_badge(canvas, data, (828, 46), "#254a58", "#ead5a8", radius=3)
    center = (548, top + layout["hero_height"] // 2)
    cx, cy = center
    d.ellipse(
        (cx - 380, cy - 322, cx + 380, cy + 322),
        fill="#2d6172",
        outline="#b7bea0",
        width=3,
    )
    for radius in (120, 224, 304):
        d.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline="#6597a0",
            width=1,
        )
    d.line((95, cy, 957, cy), fill="#69989c", width=1)
    d.line((cx, top + 18, cx, bottom - 18), fill="#69989c", width=1)
    for angle in range(0, 360, 6):
        rad = math.radians(angle)
        length = 16 if angle % 30 == 0 else 7
        x1, y1 = cx + math.cos(rad) * 339, cy + math.sin(rad) * 299
        x2, y2 = (
            cx + math.cos(rad) * (339 - length),
            cy + math.sin(rad) * (299 - length),
        )
        d.line((x1, y1, x2, y2), fill="#c9cba7", width=2)
    route = ((85, top + 130), (204, top + 62), (812, bottom - 41), (938, bottom - 110))
    d.line(route, fill="#b99162", width=3)
    for point in (route[0], route[-1]):
        _foil_star(canvas, point, 18)
    _art(
        canvas,
        data,
        (146, top + 28, 918, bottom - 34),
        edge="#e2d7b4",
        shadow="#102e45",
    )
    _source(canvas, layout, "#e6d7b5", "#aabdb4")
    perforation = layout["profile_top"] - 14
    for x in range(52, 972, 22):
        d.line((x, perforation, x + 11, perforation), fill="#b5b993", width=2)
    for x in (22, 1002):
        d.ellipse((x - 17, perforation - 17, x + 17, perforation + 17), fill="#d5c8b0")
    if layout["paragraph"]:
        p, b = layout["profile_top"], layout["profile_bottom"]
        _inset(canvas, (74, p, 950, b), "#b8cec6", "#a7c1bd", "#769f9b", radius=3)
        for x in range(88, 940, 26):
            d.line((x, p + 10, x, b - 10), fill="#b0c9c1", width=1)
        for y in range(p + 10, b - 5, 26):
            d.line((85, y, 939, y), fill="#b0c9c1", width=1)
        d.rectangle((91, p + 14, 174, p + 60), fill="#cdb889")
        _profile_text(canvas, layout, "#28474e", "#526157")
    return canvas


def render_alternate_card(data: dict[str, Any]) -> Image.Image:
    layout = alternate_card_layout(data)
    renderer = {
        "梦泉琉璃": _glass,
        "毛线物语": _yarn,
        "波普漫画": _comic,
        "星海航记": _ticket,
    }[data["theme"]]
    return renderer(data, layout)
