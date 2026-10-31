"""Printed card layout without a bot SDK, database, downloads or game rules."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
import io
import math
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import CardData, JewelSpec, RenderOptions, ImageSource
from .fonts import load_card_font, load_card_name_mask
from .materials import (
    render_stock,
    render_art_scene,
    render_reading_stock,
    apply_print_finish,
)
from .frames import paint_frame
from .symbols import (
    render_attribute_icon,
    render_custom_attribute_icon,
    render_level_star,
)
from .foil import render_foil_name

CARD_W, CARD_H = 1080, 1680
READING_BOX = (60, 1123, 1020, 1600)
READING_PAPER = (76, 1140, 1004, 1583)
STATS_RULE_Y = 1533
FOOTER_BOTTOM = 1644
INKS = {
    "C": ((34, 24, 19), (63, 47, 37), (127, 100, 79)),
    "R": ((36, 23, 32), (68, 43, 60), (140, 104, 124)),
    "SR": ((31, 25, 39), (57, 45, 69), (126, 109, 142)),
    "SSR": ((29, 25, 23), (58, 48, 37), (131, 114, 81)),
    "SHINY": ((28, 27, 34), (53, 50, 65), (132, 127, 143)),
}
TITLE_INKS = {"C": (252, 236, 216), "R": (251, 245, 250), "SR": (30, 19, 44)}
DEFAULT_STARS = {"C": 2, "R": 4, "SR": 6, "SSR": 8, "SHINY": 12}


@lru_cache(maxsize=32)
def _font(size):
    return load_card_font(size, "body")


@lru_cache(maxsize=24)
def _bold_font(size):
    return load_card_font(size, "title")


def _open_image(source: ImageSource) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.copy()
    with Image.open(
        io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    ) as image:
        return image.copy()


def _title_mask(source: ImageSource) -> Image.Image:
    image = _open_image(source)
    alpha = (
        image.convert("RGBA").getchannel("A")
        if image.mode in ("RGBA", "LA") or "transparency" in image.info
        else None
    )
    if alpha is not None and alpha.getextrema() != (255, 255):
        mask = alpha
    else:
        mask = image.convert("L")
    bounds = mask.getbbox()
    if not bounds or mask.getextrema() == (255, 255):
        raise ValueError("TitleArtwork contains no visible lettering")
    return mask.crop(bounds)


def _fit(draw, value, face, width):
    value = str(value)
    if draw.textlength(value, font=face) <= width:
        return value, False
    while value and draw.textlength(value + "…", font=face) > width:
        value = value[:-1]
    return (value + "…" if width >= draw.textlength("…", font=face) else ""), True


def _text(draw, x, y, value, size=26, ink=(34, 24, 19), *, font=None):
    face = font if font is not None else _font(size)
    draw.text((round(x), round(y)), str(value), font=face, anchor="lt", fill=ink)


def _wrap(draw, text, face, width, cap):
    """Preserve paragraphs and Latin words in the bounded printed excerpt."""
    if not text or cap < 1:
        return (), bool(text)
    text = str(text).replace("\r", "")
    lines, current = [], ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’&:./_+-]*|[^\S\n]+|\n|.", text[:10000])
    for token in tokens:
        if token == "\n":
            lines.append(current.rstrip())
            current = ""
            continue
        pieces = list(token) if draw.textlength(token, font=face) > width else [token]
        for piece in pieces:
            if current and draw.textlength(current + piece, font=face) > width:
                tail = ""
                if piece in "，。；：、！？）】》」”" or current[-1] in "（【《「“":
                    match = re.search(r"[A-Za-z0-9][A-Za-z0-9'’&:./_+-]*$|.$", current)
                    tail = match.group() if match else ""
                head = current[: -len(tail)] if tail else current
                if head:
                    lines.append(head.rstrip())
                    current = tail + piece.lstrip() if tail else piece.lstrip()
                else:
                    lines.append(current.rstrip())
                    current = piece.lstrip()
            else:
                current += piece if current else piece.lstrip()
    if current or not lines:
        lines.append(current.rstrip())
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    clipped = len(lines) > cap or len(text) > 10000
    if clipped:
        lines = lines[:cap]
        last = lines[-1].rstrip()
        while last and draw.textlength(last + "…", font=face) > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return tuple(lines), clipped


def _line_height(lines, face, step):
    return (
        (len(lines) - 1) * step + face.getbbox(lines[-1], anchor="lt")[3]
        if lines
        else 0
    )


def _quiet_rail(base, top, bottom, kind):
    if kind not in {"C", "R", "SR"}:
        return
    if kind == "C":
        color, opacity = (
            ((247, 220, 188), 100) if top > 1550 else ((237, 202, 165), 130)
        )
    else:
        color, opacity = (27, 9, 33), 103 if kind == "SR" else 40
    margin, height = 12, bottom - top + 24
    strip = Image.new("RGBA", (1, height))
    strip.putdata(
        [
            (
                *color,
                round(opacity * math.exp(-(((y - height / 2) / (height * 0.45)) ** 4))),
            )
            for y in range(height)
        ]
    )
    strip = strip.resize((CARD_W - 88, height))
    strip = strip.crop((0, 0, strip.width, min(height, CARD_H - 44 - (top - margin))))
    base.alpha_composite(strip, (44, top - margin))


def render_frame(edition="C", *, scene_type="一般") -> Image.Image:
    """Return an empty 1080×1680 print frame, without names, levels or values."""
    if not isinstance(edition, str) or edition.upper() not in INKS:
        raise ValueError("edition must be C, R, SR, SSR or SHINY")
    kind = edition.upper()
    base = render_stock(kind, CARD_W, CARD_H)
    paint_frame(base, "outer", (0, 0, CARD_W, CARD_H), kind)
    title = base.crop((67, 66, 1012, 147))
    paint_frame(base, "title", (54, 54, 1026, 159), kind)
    if kind in {"C", "R"}:
        title.alpha_composite(
            Image.new("RGBA", title.size, (39, 21, 27, 98 if kind == "C" else 35))
        )
    elif kind == "SR":
        title.alpha_composite(Image.new("RGBA", title.size, (235, 213, 247, 112)))
    base.alpha_composite(title, (67, 66))
    paint_frame(base, "picture", (75, 253, 1005, 1080), kind)
    base.alpha_composite(render_art_scene(kind, scene_type, 888, 785), (96, 274))
    _quiet_rail(base, 1082, 1120, kind)
    paint_frame(base, "description", READING_BOX, kind)
    left, top, right, bottom = READING_PAPER
    base.alpha_composite(
        render_reading_stock(kind, right - left, bottom - top), (left, top)
    )
    _quiet_rail(base, 1608, 1641, kind)
    return base


def _icon(data, name, size):
    source = data.icons.get(name)
    if isinstance(source, JewelSpec):
        return render_custom_attribute_icon(
            source.glyph,
            size,
            tint=source.tint,
            reflected=source.reflected,
            upper_light=source.upper_light,
            lower_light=source.lower_light,
            font_path=source.font_path,
        )
    if source is not None:
        image = _open_image(source).convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        if bounds:
            image = image.crop(bounds)
            ratio = min(size / image.width, size / image.height)
            image = image.resize(
                (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                ),
                Image.Resampling.LANCZOS,
            )
            target = Image.new("RGBA", (size, size))
            target.alpha_composite(
                image, ((size - image.width) // 2, (size - image.height) // 2)
            )
            return target
        raise ValueError(f"Icon {name!r} contains no visible pixels")
    return render_attribute_icon(name, size)


def _name(data, draw, base):
    width = 748 if data.attributes else 924
    height_limit = 62 if data.edition == "SHINY" else 81
    raw_mask = _title_mask(data.title_artwork.image) if data.title_artwork else None
    design_size = data.title_artwork.design_size if data.title_artwork else 66
    if raw_mask is None and data.title_font_path is None:
        raw_mask = load_card_name_mask(data.title, 66)
        # An unusually long catalogue title may exceed even the smallest
        # readable whole-name artwork. Use the existing two-line font layout
        # for that complete title instead of failing the entire card.
        if raw_mask is not None and (
            round(raw_mask.width * 26 / 66) > width
            or round(raw_mask.height * 26 / 66) > height_limit
        ):
            raw_mask = None
    if raw_mask is not None:
        for size in range(66, 25, -1):
            scale = size / design_size
            dimensions = (
                max(1, round(raw_mask.width * scale)),
                max(1, round(raw_mask.height * scale)),
            )
            if dimensions[0] <= width and dimensions[1] <= height_limit:
                masks, lines = (
                    (raw_mask.resize(dimensions, Image.Resampling.LANCZOS),),
                    (data.title,),
                )
                face = load_card_font(size, "name", text=data.title)
                break
        else:
            raise ValueError(
                "The complete title artwork cannot fit the title rail at a readable size"
            )
    else:

        def name_font(size):
            return (  # Use the same complete face while measuring and painting.
                ImageFont.truetype(str(data.title_font_path), size)
                if data.title_font_path
                else load_card_font(size, "name", text=data.title)
            )

        for size in range(66, 35, -2):
            face = name_font(size)
            if (
                draw.textlength(data.title, font=face) <= width
                and face.getbbox(data.title, anchor="lt")[3] <= height_limit
            ):
                lines = (data.title,)
                break
        else:
            for size in range(38, 27, -2):
                face = name_font(size)
                lines, overflow = _wrap(draw, data.title, face, width, 2)
                if (
                    not overflow
                    and sum(face.getbbox(line, anchor="lt")[3] for line in lines)
                    + (len(lines) - 1) * 4
                    <= height_limit
                ):
                    break
            else:
                raise ValueError(
                    "The complete title cannot fit the title rail at a readable size"
                )
        masks = (None,) * len(lines)
    heights = [
        mask.height if mask is not None else face.getbbox(line, anchor="lt")[3]
        for line, mask in zip(lines, masks)
    ]
    title_center = 97 if data.edition == "SHINY" else (66 + 147) / 2
    top = title_center - (sum(heights) + (len(lines) - 1) * 4) / 2
    for index, (line, mask) in enumerate(zip(lines, masks)):
        y = top + sum(heights[:index]) + index * 4
        if data.edition in {"SSR", "SHINY"}:
            # A caller-supplied face overrides any same-named built-in artwork.
            if mask is None and data.title_font_path:
                mask = Image.new(
                    "L",
                    (
                        max(1, math.ceil(face.getlength(line))),
                        max(1, face.getbbox(line, anchor="lt")[3]),
                    ),
                )
                ImageDraw.Draw(mask).text(
                    (0, 0), line, font=face, anchor="lt", fill=255
                )
            render_foil_name(
                base, line, 78, y, size, data.edition, font=face, artwork=mask
            )
        elif mask is None:
            _text(
                ImageDraw.Draw(base),
                78,
                y,
                line,
                size,
                TITLE_INKS[data.edition],
                font=face,
            )
        else:
            lettering = Image.new("RGBA", mask.size, (*TITLE_INKS[data.edition], 0))
            lettering.putalpha(mask)
            base.alpha_composite(lettering, (78, round(y)))
    for index, name in enumerate(data.attributes):
        x = 925 - (len(data.attributes) - index - 1) * 83
        base.alpha_composite(_icon(data, name, 78), (x, round(title_center - 39)))


def _illustration(base, source):
    if source is None:
        return
    x, y, width, height = 114, 292, 852, 749
    art = _open_image(source).convert("RGBA")
    bounds = art.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("illustration contains no visible pixels")
    art = art.crop(bounds)
    scale = min(3.0, width / art.width, height / art.height)
    art = art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        Image.Resampling.LANCZOS,
    )
    tx, ty = x + (width - art.width) // 2, y + (height - art.height) // 2
    padding = 22
    alpha = Image.new("L", (art.width + padding * 2, art.height + padding * 2))
    alpha.paste(art.getchannel("A"), (padding, padding))
    shadow = Image.new("RGBA", alpha.size, (12, 15, 22, 0))
    shadow.putalpha(
        alpha.filter(ImageFilter.GaussianBlur(2)).point(lambda a: round(a * 0.12))
    )
    base.alpha_composite(shadow, (tx - padding + 1, ty - padding + 2))
    base.alpha_composite(art, (tx, ty))


def _descriptor(base, data, overflow):
    parts = [text.strip() for text in data.descriptor_parts if text.strip()]
    if data.immune_icons:
        parts.append(data.immune_label.strip())
    if not parts and not data.immune_icons:
        return READING_PAPER[1]
    draw = ImageDraw.Draw(base)
    prefix = "【" + " / ".join(parts) + (" " if data.immune_icons else "")
    size = 28
    while (
        size > 24
        and draw.textlength(prefix + "】", font=_bold_font(size))
        + len(data.immune_icons) * 34
        > 898
    ):
        size -= 1
    face = _bold_font(size)
    width = 898 - len(data.immune_icons) * 34 - draw.textlength("】", font=face)
    printed, clipped = _fit(draw, prefix, face, width)
    if clipped:
        overflow.append("descriptor: " + " / ".join(parts))
    _text(draw, 89, 1150, printed, size, INKS[data.edition][0], font=face)
    x = 89 + round(draw.textlength(printed, font=face))
    for name in data.immune_icons:
        base.alpha_composite(_icon(data, name, 29), (x + 2, 1148))
        x += 34
    _text(draw, x, 1150, "】", size, INKS[data.edition][0], font=face)
    return max(
        1150 + face.getbbox(printed + "】", anchor="lt")[3],
        1177 if data.immune_icons else 0,
    )


def _rail(base, left, right, center, kind, overflow, section, *, footer=False):
    if not left and not right:
        return
    draw, face = ImageDraw.Draw(base), _font(26)
    ink = (253, 242, 233) if kind in {"R", "SR", "SSR"} else (17, 8, 5)
    left_x, right_x = (62, 1009) if footer else (91, 988)
    total_width = right_x - left_x
    right_limit = min(400, total_width // 2) if left else total_width
    printed_right, clipped = _fit(draw, right, face, right_limit)
    if clipped:
        overflow.append(f"{section}.right: {right}")
    right_width = draw.textlength(printed_right, font=face)
    left_width = total_width - right_width - (24 if printed_right else 0)
    printed_left, clipped = _fit(draw, left, face, left_width)
    if clipped:
        overflow.append(f"{section}.left: {left}")
    for x, text in ((left_x, printed_left), (right_x - right_width, printed_right)):
        if text:
            _text(
                draw,
                x,
                center - face.getbbox(text, anchor="lt")[3] / 2,
                text,
                26,
                ink,
                font=face,
            )


def _relation_layout(draw, data, overflow):
    layout, used_rows = [], 0
    for group_index, group in enumerate(data.relations):
        rows, current, used = [], [], 0
        available = (
            635 if data.total and group_index == len(data.relations) - 1 else 806
        )
        for token in group.tokens:
            icon_width = 41 if token.icon else 0
            text, clipped = _fit(
                draw, token.text, _font(26), available - icon_width - 18
            )
            if clipped:
                overflow.append(f"relation.{group.label}: {token.text}")
            width = icon_width + round(draw.textlength(text, font=_font(26))) + 18
            if current and used + width > available:
                rows.append(tuple(current))
                current, used = [], 0
            current.append((token.icon, text, width))
            used += width
        rows.append(tuple(current))
        # Reserve room for the readable paragraph; excess remains available to callers.
        capacity = max(0, 5 - used_rows)
        if len(rows) > capacity:
            overflow.append(
                f"relation.{group.label}: "
                + (
                    "; ".join((t.icon + " " + t.text).strip() for t in group.tokens)
                    or group.empty_text
                )
            )
            rows = rows[:capacity]
            if rows:
                rows[-1] = (
                    (*rows[-1], ("", "…", 40))
                    if sum(t[2] for t in rows[-1]) + 40 <= available
                    else (*rows[-1][:-1], ("", "…", 40))
                )
        if rows:
            label, clipped = _fit(draw, group.label, _font(26), 83)
            if clipped:
                overflow.append("relation.label: " + group.label)
            layout.append((label, tuple(rows), group.empty_text))
            used_rows += len(rows)
    return tuple(layout)


def _relations(base, data, layout, top, bottom):
    if not layout:
        return
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[data.edition]
    separator = top - 13
    draw.line((91, separator, 989, separator), fill=rule, width=1)
    bounds, offset = [], 0
    for label, rows, empty in layout:
        bounds.append(
            (offset + 5, offset + 5 + _font(26).getbbox(label, anchor="lt")[3])
        )
        for index, row in enumerate(rows):
            yy = offset + index * 40
            if row:
                height = max(36 if icon else 0 for icon, _text_value, _width in row)
                height = max(
                    height,
                    max(
                        _font(26).getbbox(text, anchor="lt")[3] + 5
                        for _icon_value, text, _width in row
                    ),
                )
                bounds.append((yy, yy + height))
            else:
                bounds.append(
                    (yy + 5, yy + 5 + _font(26).getbbox(empty, anchor="lt")[3])
                )
        offset += len(rows) * 40 + 4
    first, last = min(b[0] for b in bounds), max(b[1] for b in bounds)
    y = round((separator + 1 + bottom - first - last) / 2)
    for label, rows, empty in layout:
        _text(draw, 92, y + 5, label, 26, ink)
        for index, row in enumerate(rows):
            yy, x = y + index * 40, 182
            if not row:
                printed, _ = _fit(draw, empty, _font(26), 806)
                _text(draw, x, yy + 5, printed, 26, muted)
            for icon, text, width in row:
                if icon:
                    base.alpha_composite(_icon(data, icon, 36), (x, yy))
                _text(draw, x + (41 if icon else 0), yy + 5, text, 26, ink)
                x += width
        y += len(rows) * 40 + 4


def _stats(base, data, overflow):
    if not data.small_stats and not data.attack and not data.defense:
        return
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[data.edition]
    draw.line((91, STATS_RULE_Y, 989, STATS_RULE_Y), fill=rule, width=2)
    center = (STATS_RULE_Y + 2 + READING_PAPER[3]) / 2
    combat = "   ".join(
        f"{label}/{value}"
        for label, value in (
            (data.attack_label, data.attack),
            (data.defense_label, data.defense),
        )
        if value
    )
    combat_face = _bold_font(34)
    combat, clipped = _fit(draw, combat, combat_face, 340 if data.small_stats else 898)
    if clipped:
        overflow.append(
            f"combat: {data.attack_label}/{data.attack} {data.defense_label}/{data.defense}"
        )
    if combat:
        _text(
            draw,
            988 - draw.textlength(combat, font=combat_face),
            center - combat_face.getbbox(combat, anchor="lt")[3] / 2,
            combat,
            34,
            ink,
            font=combat_face,
        )
    entries = [f"{label} {value}" for label, value in data.small_stats]
    if len(entries) == 4 and combat:
        positions, widths = [93 + index * 119 for index in range(4)], [113] * 4
    elif entries:
        available = 540 if combat else 898
        step = available / len(entries)
        widths = [step - 12] * len(entries)
        positions = [91 + i * step for i in range(len(entries))]
    else:
        positions, widths = [], []
    for text, x, width in zip(entries, positions, widths):
        printed, clipped = _fit(draw, text, _font(24), width)
        if clipped:
            overflow.append("small_stat: " + text)
        _text(
            draw,
            x,
            center - _font(24).getbbox(printed, anchor="lt")[3] / 2,
            printed,
            24,
            muted,
        )


def render_card(data: CardData, options: RenderOptions | None = None) -> Image.Image:
    """Render one full-resolution card. No paths or source images are changed."""
    if not isinstance(data, CardData):
        raise TypeError("data must be CardData")
    options = RenderOptions() if options is None else options
    if not isinstance(options, RenderOptions):
        raise TypeError("options must be RenderOptions")
    overflow = []
    base = render_frame(data.edition, scene_type=data.scene_type)
    _name(data, ImageDraw.Draw(base), base)
    stars = data.stars if data.stars is not None else DEFAULT_STARS[data.edition]
    for index in range(stars):
        base.alpha_composite(render_level_star(64), (930 - index * 68, 172))
    _illustration(base, data.illustration)
    apply_print_finish(base, (96, 274, 888, 785), data.edition)
    _rail(
        base,
        data.rail_left,
        data.rail_right,
        (1080 + 1123) / 2,
        data.edition,
        overflow,
        "rail",
    )
    descriptor_bottom = _descriptor(base, data, overflow)
    draw = ImageDraw.Draw(base)
    layout = _relation_layout(draw, data, overflow)
    stats_present = bool(data.small_stats or data.attack or data.defense)
    bottom = STATS_RULE_Y if stats_present else READING_PAPER[3] - 8
    matchup_height = sum(len(rows) * 40 + 4 for _label, rows, _empty in layout)
    matchup_top = bottom - 15 - matchup_height if layout else bottom
    body_bottom = matchup_top - 13 if layout else bottom - 12
    y = descriptor_bottom + 12
    description, secondary = data.description.strip(), data.secondary_text.strip()
    face = _bold_font(28) if options.description_black_bold else _font(28)
    ink, muted, _rule = INKS[data.edition]
    paragraph_ink = (0, 0, 0) if options.description_black_bold else ink
    if not layout and data.total:
        body_bottom = min(body_bottom, bottom - 44)
    reserve = 49 if secondary else 15
    capacity = max(
        0, min(options.description_max_lines, (matchup_top - y - reserve) // 37)
    )
    lines, clipped = _wrap(draw, description, face, 894, capacity)
    if clipped:
        overflow.append("description: " + description)
    line_block = len(lines) * 37
    secondary_capacity = max(0, min(2, (matchup_top - y - line_block - 12) // 35))
    # A standalone total has its own lower-right space even without relation rows.
    if not layout and data.total:
        capacity = max(
            0, min(options.description_max_lines, (body_bottom - y - reserve) // 37)
        )
        lines, clipped = _wrap(draw, description, face, 894, capacity)
        if clipped and "description: " + description not in overflow:
            overflow.append("description: " + description)
        line_block = len(lines) * 37
        secondary_capacity = max(0, min(2, (body_bottom - y - line_block - 7) // 35))
    secondary_lines, clipped = _wrap(
        draw, secondary, _font(26), 894, secondary_capacity
    )
    if clipped:
        overflow.append("secondary_text: " + secondary)
    height = _line_height(lines, face, 37)
    if secondary_lines:
        height = (
            line_block
            + (7 if lines else 0)
            + _line_height(secondary_lines, _font(26), 35)
        )
    y = descriptor_bottom + max(12, (body_bottom - descriptor_bottom - height) / 2)
    for index, line in enumerate(lines):
        _text(draw, 92, y + index * 37, line, 28, paragraph_ink, font=face)
    if lines:
        y += line_block + 7
    for index, line in enumerate(secondary_lines):
        _text(draw, 92, y + index * 35, line, 26, muted)
    _relations(base, data, layout, matchup_top, bottom)
    if data.total:
        printed, clipped = _fit(draw, data.total, _font(25), 180)
        if clipped:
            overflow.append("total: " + data.total)
        _text(
            draw,
            988 - draw.textlength(printed, font=_font(25)),
            bottom - 32,
            printed,
            25,
            ink,
        )
    _stats(base, data, overflow)
    _rail(
        base,
        data.footer_left,
        data.footer_right,
        (READING_BOX[3] + FOOTER_BOTTOM) / 2,
        data.edition,
        overflow,
        "footer",
        footer=True,
    )
    base.info["overflow"] = tuple(dict.fromkeys(overflow))
    if options.details_out is not None:
        options.details_out.extend(
            f"{data.title}: {text}" for text in base.info["overflow"]
        )
    return base


def _encode(image, format):
    format = str(format).upper()
    output = io.BytesIO()
    if format == "PNG":
        image.save(output, "PNG", optimize=True)
    elif format in {"JPEG", "JPG"}:
        image.convert("RGB").save(
            output, "JPEG", quality=96, subsampling=0, optimize=False
        )
    else:
        raise ValueError("format must be PNG or JPEG")
    return output.getvalue()


def render_card_bytes(
    data: CardData, options: RenderOptions | None = None, *, format="PNG"
) -> bytes:
    return _encode(render_card(data, options), format)


def render_sheet(
    cards: Sequence[CardData],
    options: RenderOptions | None = None,
    *,
    tile_width=518,
    gap=20,
    columns=2,
) -> Image.Image:
    """Render up to ten complete cards, centring an incomplete last row."""
    if (
        isinstance(cards, (str, bytes))
        or not isinstance(cards, Sequence)
        or not 1 <= len(cards) <= 10
    ):
        raise ValueError("cards must contain from one to ten CardData objects")
    if type(tile_width) is not int or not 128 <= tile_width <= CARD_W:
        raise ValueError("tile_width must be an integer from 128 to 1080")
    if type(gap) is not int or not 0 <= gap <= 128:
        raise ValueError("gap must be an integer from 0 to 128")
    if type(columns) is not int or not 1 <= columns <= 4:
        raise ValueError("columns must be an integer from 1 to 4")
    if len(cards) == 1:
        return render_card(cards[0], options)
    columns = min(columns, len(cards))
    tile_height, margin = round(CARD_H * tile_width / CARD_W), 12
    width = columns * tile_width + (columns - 1) * gap + margin * 2
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new(
        "RGBA", (width, rows * (tile_height + gap) + gap), (35, 33, 40, 255)
    )
    details = []
    for index, card in enumerate(cards):
        row, column = divmod(index, columns)
        count = min(columns, len(cards) - row * columns)
        left = (width - count * tile_width - (count - 1) * gap) // 2
        image = render_card(card, options)
        details.extend(
            f"{card.title}: {text}" for text in image.info.get("overflow", ())
        )
        sheet.alpha_composite(
            image.resize((tile_width, tile_height), Image.Resampling.LANCZOS),
            (left + column * (tile_width + gap), gap + row * (tile_height + gap)),
        )
    sheet.info["overflow"] = tuple(details)
    return sheet


def render_sheet_bytes(
    cards: Sequence[CardData],
    options: RenderOptions | None = None,
    *,
    format="PNG",
    **layout,
) -> bytes:
    return _encode(render_sheet(cards, options, **layout), format)
