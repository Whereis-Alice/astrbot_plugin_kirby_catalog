"""Local printed-name artwork and OFL reading/inscription fonts.

Known names use whole-phrase raster artwork in the official reference's clerical
style. No Windows font file or reusable character atlas is distributed. Reading
fonts and the fallback face are portable static subsets; fontTools is only used
during asset preparation.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageFont


_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_NAME_ARCHIVE = Path(__file__).resolve().parent / "assets" / "yugioh" / "names.zip"
_FILES = {
    "body": "PartnerCardSerif-Medium.ttf",
    "title": "PartnerCardSerif-ExtraBold.ttf",
    "name": "PartnerDuelKai-Regular.ttf",
    "symbol": "PartnerDuelSymbols-Regular.ttf",
}


@lru_cache(maxsize=48)
def _load_face(size: int, role: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_DIR / _FILES[role]), int(size))


@lru_cache(maxsize=1)
def _name_coverage() -> frozenset[str]:
    return frozenset((_FONT_DIR / "name-coverage.txt").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _name_artwork_archive() -> ZipFile:
    # Read once into memory: no extraction, temporary files, or persistent
    # Windows file handle that could interfere with a plugin update.
    return ZipFile(BytesIO(_NAME_ARCHIVE.read_bytes()))


@lru_cache(maxsize=1)
def _name_artwork_manifest() -> dict:
    return json.loads(_name_artwork_archive().read("manifest.json"))


def card_name_artwork_metrics(text: str, size: int) -> tuple[int, int] | None:
    """Return ink bounds for an existing complete name at the requested size.

    Entries are complete words/phrases only. Unknown names are deliberately not
    assembled from pieces of the supplied artwork.
    """
    manifest = _name_artwork_manifest()
    entry = manifest["names"].get(str(text))
    if entry is None:
        return None
    scale = max(1, int(size)) / manifest["design_size"]
    return max(1, round(entry[1] * scale)), max(1, round(entry[2] * scale))


@lru_cache(maxsize=48)
def _card_name_mask(text: str, size: int) -> Image.Image | None:
    dimensions = card_name_artwork_metrics(text, size)
    if dimensions is None:
        return None
    filename = _name_artwork_manifest()["names"][text][0]
    with Image.open(BytesIO(_name_artwork_archive().read(filename))) as source:
        return source.convert("L").resize(dimensions, Image.Resampling.LANCZOS)


def load_card_name_mask(text: str, size: int) -> Image.Image | None:
    """Load a complete printed-name mask, or None for the font fallback path."""
    mask = _card_name_mask(str(text), int(size))
    return mask.copy() if mask is not None else None


def load_card_font(
    size: int, role: str = "body", *, text: str = ""
) -> ImageFont.FreeTypeFont:
    """Load portable print faces; custom names fall back as a complete line.

    Card names first use ``load_card_name_mask`` at the drawing layer. This Kai
    face is a fallback for custom/unlisted text, not the official-style headline.
    Pass the complete name when measuring and drawing fallback text so a name
    outside the subset consistently uses the general Chinese reading face.
    """
    if role not in _FILES:
        raise ValueError("未知的游戏王卡片字体角色")
    if role == "name" and text and not set(text).issubset(_name_coverage()):
        role = "body"
    return _load_face(int(size), role)
