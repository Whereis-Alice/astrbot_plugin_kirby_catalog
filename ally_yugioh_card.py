"""Map catalogue facts to the user's five-stock Yu-Gi-Oh print template."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from .ally_draw_card import _fit_artwork, _number, _text
from .yugioh_template import CardData, JewelSpec, RenderOptions, render_card
from .yugioh_template.fonts import load_card_font
from .yugioh_template.symbols import render_level_star


def _portrait(raw: Any) -> Image.Image:
    if isinstance(raw, bytes) and raw:
        with Image.open(BytesIO(raw)) as image:
            return _fit_artwork(image, (852, 749))
    image = Image.new("RGBA", (852, 749))
    image.alpha_composite(render_level_star(180), (336, 242))
    ImageDraw.Draw(image).text(
        (426, 463),
        "素材暂时不可用",
        font=load_card_font(28),
        fill="#f5e1b0",
        anchor="mt",
    )
    return image


def yugioh_ally_card_data(data: dict[str, Any]) -> CardData:
    """Keep every printed fact grounded in the already prepared catalogue data."""
    name = _text(data.get("name"), 250) or "未命名盟友"
    english = _text(data.get("english_name"), 250)
    source = _text(data.get("source"), 300)
    description = (
        str(data.get("description") or "").replace("\u200b", "").replace("❤", "")
    )
    return CardData(
        title=name,
        edition=str(data.get("yugioh_edition") or "C"),
        stars=min(12, max(1, _number(data.get("yugioh_stars"), 1))),
        attributes=("星",),
        icons={
            "星": JewelSpec(
                glyph="星",
                tint=(113, 76, 15),
                reflected=(241, 217, 147),
                upper_light=(218, 173, 56),
                lower_light=(255, 240, 188),
            )
        },
        illustration=_portrait(data.get("artwork")),
        descriptor_parts=("简介",) if description else (),
        description=description,
        rail_left=english if english.casefold() != name.casefold() else "",
        footer_left=f"首次登场 · {source}" if source else "",
        footer_right=f"#{_number(data.get('catalog_id')):04d}",
    )


def render_yugioh_ally_card(data: dict[str, Any]) -> Image.Image:
    # The compact printed card can devote ten lines to real description text
    # because Kirby has no combat statistics or invented relation rows.
    return render_card(
        yugioh_ally_card_data(data), RenderOptions(description_max_lines=10)
    )
