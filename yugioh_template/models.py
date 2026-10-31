"""Typed, platform-independent inputs for the printed card template."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from PIL import Image

ImageSource = str | Path | bytes | Image.Image
EDITIONS = ("C", "R", "SR", "SSR", "SHINY")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be text or a number")
    return str(value)


def _texts(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple")
    return tuple(_text(item, name) for item in value)


def _rgb(value: Any, name: str) -> tuple[int, int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(type(v) is not int or not 0 <= v <= 255 for v in value)
    ):
        raise ValueError(f"{name} must contain three integers from 0 to 255")
    return tuple(value)


def _image(value: Any, name: str, base: Path | None = None) -> ImageSource | None:
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        path = Path(value)
        if base is not None and not path.is_absolute():
            path = base / path
        return path
    if isinstance(value, (bytes, Image.Image)):
        return value
    raise TypeError(f"{name} must be a local path, bytes or a Pillow image")


@dataclass(frozen=True)
class JewelSpec:
    """A custom engraved jewel. All colours are RGB; the glyph is one character."""

    glyph: str
    tint: tuple[int, int, int] = (60, 59, 69)
    reflected: tuple[int, int, int] = (180, 177, 190)
    upper_light: tuple[int, int, int] = (133, 132, 166)
    lower_light: tuple[int, int, int] = (223, 223, 249)
    font_path: str | Path | None = None

    def __post_init__(self):
        if not isinstance(self.glyph, str) or len(self.glyph.strip()) != 1:
            raise ValueError("JewelSpec.glyph must be one visible character")
        object.__setattr__(self, "glyph", self.glyph.strip())
        for name in ("tint", "reflected", "upper_light", "lower_light"):
            object.__setattr__(self, name, _rgb(getattr(self, name), name))
        if self.font_path is not None:
            object.__setattr__(self, "font_path", Path(self.font_path))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, base: Path | None = None):
        data = dict(value)
        if data.get("font_path") is not None:
            data["font_path"] = _image(data["font_path"], "font_path", base)
        return cls(**data)


@dataclass(frozen=True)
class TitleArtwork:
    """A whole-title white-on-black L mask or transparent PNG at design_size."""

    image: ImageSource
    design_size: int = 66

    def __post_init__(self):
        if type(self.design_size) is not int or self.design_size < 1:
            raise ValueError("TitleArtwork.design_size must be a positive integer")
        object.__setattr__(self, "image", _image(self.image, "title_artwork.image"))
        if self.image is None:
            raise ValueError("TitleArtwork.image is required")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, base: Path | None = None):
        data = dict(value)
        if "path" in data and "image" not in data:
            data["image"] = data.pop("path")
        data["image"] = _image(data.get("image"), "title_artwork.image", base)
        return cls(**data)


@dataclass(frozen=True)
class RelationToken:
    icon: str = ""
    text: str = ""

    def __post_init__(self):
        object.__setattr__(self, "icon", _text(self.icon, "token.icon"))
        object.__setattr__(self, "text", _text(self.text, "token.text"))
        if not self.icon.strip() and not self.text.strip():
            raise ValueError("A relation token needs an icon or text")


@dataclass(frozen=True)
class RelationRow:
    label: str
    tokens: tuple[RelationToken, ...] = ()
    empty_text: str = "无"

    def __post_init__(self):
        object.__setattr__(self, "label", _text(self.label, "relation.label"))
        object.__setattr__(
            self, "empty_text", _text(self.empty_text, "relation.empty_text")
        )
        if isinstance(self.tokens, str) or not isinstance(self.tokens, (list, tuple)):
            raise TypeError("relation.tokens must be a list or tuple")
        tokens = tuple(
            t if isinstance(t, RelationToken) else RelationToken(**t)
            for t in self.tokens
        )
        object.__setattr__(self, "tokens", tokens)


@dataclass(frozen=True)
class CardData:
    title: str
    edition: str = "C"
    stars: int | None = None
    attributes: tuple[str, ...] = ()
    illustration: ImageSource | None = None
    scene_type: str = "一般"
    descriptor_parts: tuple[str, ...] = ()
    immune_icons: tuple[str, ...] = ()
    immune_label: str = "免疫"
    description: str = ""
    secondary_text: str = ""
    rail_left: str = ""
    rail_right: str = ""
    relations: tuple[RelationRow, ...] = ()
    small_stats: tuple[tuple[str, str], ...] = ()
    attack: str = ""
    defense: str = ""
    attack_label: str = "攻"
    defense_label: str = "守"
    total: str = ""
    footer_left: str = ""
    footer_right: str = ""
    title_artwork: TitleArtwork | None = None
    title_font_path: str | Path | None = None
    icons: Mapping[str, JewelSpec | ImageSource] = field(default_factory=dict)

    def __post_init__(self):
        for name in (
            "title",
            "scene_type",
            "immune_label",
            "description",
            "secondary_text",
            "rail_left",
            "rail_right",
            "attack",
            "defense",
            "attack_label",
            "defense_label",
            "total",
            "footer_left",
            "footer_right",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not self.title.strip():
            raise ValueError("CardData.title must not be empty")
        if not isinstance(self.edition, str) or self.edition.upper() not in EDITIONS:
            raise ValueError(f"edition must be one of {', '.join(EDITIONS)}")
        object.__setattr__(self, "edition", self.edition.upper())
        if self.stars is not None and (
            type(self.stars) is not int or not 0 <= self.stars <= 12
        ):
            raise ValueError("stars must be an integer from 0 to 12, or None")
        for name in ("attributes", "descriptor_parts", "immune_icons"):
            object.__setattr__(self, name, _texts(getattr(self, name), name))
        if len(self.attributes) > 2:
            raise ValueError("The title rail supports at most two attribute icons")
        if len(self.immune_icons) > 18:
            raise ValueError("The descriptor rail supports at most eighteen icons")
        object.__setattr__(
            self, "illustration", _image(self.illustration, "illustration")
        )
        if not isinstance(self.relations, (list, tuple)):
            raise TypeError("relations must be a list or tuple")
        relations = tuple(
            r if isinstance(r, RelationRow) else RelationRow(**r)
            for r in self.relations
        )
        if len(relations) > 4:
            raise ValueError(
                "The information box supports at most four relation groups"
            )
        object.__setattr__(self, "relations", relations)
        if not isinstance(self.small_stats, (list, tuple)) or len(self.small_stats) > 4:
            raise ValueError("small_stats must contain at most four label/value pairs")
        stats = []
        for pair in self.small_stats:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise TypeError("Each small_stats item must be a label/value pair")
            stats.append((_text(pair[0], "stat.label"), _text(pair[1], "stat.value")))
        object.__setattr__(self, "small_stats", tuple(stats))
        if self.title_artwork is not None and not isinstance(
            self.title_artwork, TitleArtwork
        ):
            object.__setattr__(
                self, "title_artwork", TitleArtwork(**self.title_artwork)
            )
        if self.title_font_path is not None:
            object.__setattr__(self, "title_font_path", Path(self.title_font_path))
        if not isinstance(self.icons, Mapping):
            raise TypeError(
                "icons must be a mapping of names to images or JewelSpec objects"
            )
        icons = {}
        for name, source in self.icons.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Each icon must have a non-empty name")
            icons[name] = (
                source
                if isinstance(source, JewelSpec)
                else _image(source, f"icons.{name}")
            )
            if icons[name] is None:
                raise ValueError(f"icons.{name} cannot be None")
        object.__setattr__(self, "icons", icons)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, base: str | Path | None = None):
        """Read JSON data; resolve every supplied file path against base."""
        if not isinstance(value, Mapping):
            raise TypeError("A card must be a JSON object")
        data = dict(value)
        base = Path(base) if base is not None else None
        if data.get("illustration") is not None:
            data["illustration"] = _image(data["illustration"], "illustration", base)
        if data.get("title_font_path") is not None:
            data["title_font_path"] = _image(
                data["title_font_path"], "title_font_path", base
            )
        if data.get("title_artwork") is not None:
            data["title_artwork"] = TitleArtwork.from_dict(
                data["title_artwork"], base=base
            )
        raw_icons = data.get("icons", {})
        if not isinstance(raw_icons, Mapping):
            raise TypeError("icons must be a JSON object")
        icons = {}
        for name, source in raw_icons.items():
            icons[name] = (
                JewelSpec.from_dict(source, base=base)
                if isinstance(source, Mapping)
                else _image(source, f"icons.{name}", base)
            )
        data["icons"] = icons
        return cls(**data)


@dataclass(frozen=True)
class RenderOptions:
    description_black_bold: bool = False
    details_out: list[str] | None = None
    description_max_lines: int = 7

    def __post_init__(self):
        if type(self.description_black_bold) is not bool:
            raise TypeError("description_black_bold must be a boolean")
        if self.details_out is not None and not isinstance(self.details_out, list):
            raise TypeError("details_out must be a list or None")
        if (
            type(self.description_max_lines) is not int
            or not 1 <= self.description_max_lines <= 12
        ):
            raise ValueError("description_max_lines must be an integer from 1 to 12")
