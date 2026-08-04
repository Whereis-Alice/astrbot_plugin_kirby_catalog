from __future__ import annotations

import copy
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from PIL import Image, ImageOps


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
RECORDS_DIRNAME = "_收集记录"
MANIFEST_FILENAME = "候选清单.json"
COLLECTION_FILENAME = "收集清单.csv"
DEFAULT_RELEASE_ORDER_FILENAME = "kirby_work_release_order.json"
DEFAULT_OVERRIDES_FILENAME = "kirby_legacy_overrides.json"
CATALOG_METADATA_KEYS = (
    "pageid",
    "page_title",
    "entry_key",
    "variant_key",
    "catalog_kind",
    "asset_set",
    "debut_work",
    "debut_year",
    "kind",
)

KIND_PRIORITY = {
    "Infobox-Character": 10,
    "Infobox-AnimeCharacter": 20,
    "Infobox-NovelCharacter": 30,
    "Infobox-Copy Ability": 40,
    "Infobox-AirRideMachine": 50,
    "Infobox-Enemy": 60,
    "Infobox-MidBoss": 70,
    "Infobox-Boss": 80,
    "Infobox-Monster": 90,
    "Infobox-Item": 100,
    "Infobox-Object": 110,
    "Infobox-Place": 120,
    "": 130,
}
KIND_PRIORITY_FOLDED = {key.casefold(): value for key, value in KIND_PRIORITY.items()}
PAGE_PRIORITY = {"Kirby": 0}

_QUALIFIER_TRANSLATIONS = {
    "second form": "第二形态",
    "anime character": "动画版",
    "novel character": "小说版",
}

_CHAR_FOLD = str.maketrans(
    {
        "裝": "装",
        "亂": "乱",
        "鬥": "斗",
        "體": "体",
        "臺": "台",
        "颱": "台",
        "髮": "发",
        "裏": "里",
        "裡": "里",
        "隻": "只",
        "衝": "冲",
        "擊": "击",
        "劍": "剑",
        "騎": "骑",
        "龍": "龙",
        "鳥": "鸟",
        "獸": "兽",
        "靈": "灵",
        "夢": "梦",
        "島": "岛",
        "寶": "宝",
        "貓": "猫",
        "龜": "龟",
        "電": "电",
        "風": "风",
        "雲": "云",
        "葉": "叶",
        "飛": "飞",
        "團": "团",
        "樂": "乐",
        "會": "会",
        "號": "号",
        "將": "将",
        "戰": "战",
        "術": "术",
        "機": "机",
        "廣": "广",
        "場": "场",
        "館": "馆",
        "員": "员",
        "長": "长",
        "門": "门",
        "車": "车",
        "圓": "圆",
        "畫": "画",
        "聲": "声",
        "萬": "万",
        "與": "与",
        "內": "内",
        "學": "学",
        "師": "师",
        "獅": "狮",
        "蘭": "兰",
        "爾": "尔",
        "羅": "罗",
        "烏": "乌",
        "魯": "鲁",
        "迪": "迪",
    }
)

_VARIANT_PREFIXES = ("结晶化",)

_VARIANT_SUFFIX_PATTERNS = (
    re.compile(r"(?:[·・ ]?幻)$", re.IGNORECASE),
    re.compile(r"(?:[ _-]?EX)$", re.IGNORECASE),
    re.compile(r"[（(](?:红温版|无头盔|冰形态|火形态|电形态)[）)]$", re.IGNORECASE),
)


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filename_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^ally_\d+_", "", stem, flags=re.IGNORECASE)
    if "." in stem:
        stem = stem.split(".", 1)[1]
    return stem.strip(" .")


def normalise_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_CHAR_FOLD)
    text = re.sub(r"^ally_\d+_", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.(?:png|jpe?g|gif|bmp|webp)$", "", text, flags=re.IGNORECASE)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text.casefold())


def _has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def _is_kirby_form_name(old: "OldEntry") -> bool:
    raw_names = {old.name, _filename_stem(old.filename), *old.aliases}
    for value in raw_names:
        text = unicodedata.normalize("NFKC", str(value or "")).translate(_CHAR_FOLD)
        text = text.strip(" .")
        if text.endswith("卡比") and normalise_name(text) != normalise_name("卡比"):
            return True
    return False


def variant_base_names(value: str) -> Set[str]:
    raw = unicodedata.normalize("NFKC", str(value or "")).translate(_CHAR_FOLD).strip()
    values: Set[str] = set()
    pending = [raw]
    while pending:
        current = pending.pop()
        key = normalise_name(current)
        if not key or key in values:
            continue
        values.add(key)
        for prefix in _VARIANT_PREFIXES:
            if current.startswith(prefix) and len(current) > len(prefix):
                pending.append(current[len(prefix) :].strip())
        for pattern in _VARIANT_SUFFIX_PATTERNS:
            stripped = pattern.sub("", current).strip()
            if stripped and stripped != current:
                pending.append(stripped)
    values.discard(normalise_name(raw))
    return values


def _extract_year(value: str) -> Optional[int]:
    years = [
        int(match)
        for match in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value or "")
    ]
    return min(years) if years else None


def _safe_group_filename(path: Path) -> bool:
    return path.suffix.lower() == ".json" and bool(
        re.fullmatch(r"[A-Za-z0-9_-]+", path.stem)
    )


@dataclass
class ImageSignature:
    fit_white: int
    fit_black: int
    stretch: int
    aspect: float


@dataclass
class NewAsset:
    filename: str
    path: Path
    pageid: int
    page_title: str
    entry_key: str
    variant_key: str
    catalog_kind: str
    asset_set: str
    name: str
    source: str
    debut_work: str
    debut_year: int
    kind: str
    names: Set[str] = field(default_factory=set)
    sha256: str = ""
    signature: Optional[ImageSignature] = None
    catalog_id: int = 0

    def public_record(self) -> Dict[str, Any]:
        return {
            "id": self.catalog_id,
            "filename": self.filename,
            "name": self.name,
            "source": self.source,
            "aliases": [],
            "pageid": self.pageid,
            "page_title": self.page_title,
            "entry_key": self.entry_key,
            "variant_key": self.variant_key,
            "catalog_kind": self.catalog_kind,
            "asset_set": self.asset_set,
            "debut_work": self.debut_work,
            "debut_year": None if self.debut_year >= 9999 else self.debut_year,
            "kind": self.kind,
        }


@dataclass
class OldEntry:
    entry_id: int
    filename: str
    name: str
    source: str
    aliases: List[str]
    path: Optional[Path]
    entry_key: str = ""
    variant_key: str = ""
    page_title: str = ""
    sha256: str = ""
    signature: Optional[ImageSignature] = None

    def names(self) -> Set[str]:
        values = {
            self.name,
            self.filename,
            _filename_stem(self.filename),
            self.variant_key,
            self.page_title,
            *self.aliases,
        }
        return {normalise_name(value) for value in values if normalise_name(value)}


@dataclass
class MatchResult:
    old_id: int
    old_filename: str
    old_name: str
    old_source: str
    old_file_exists: bool
    old_entry_key: str = ""
    old_variant_key: str = ""
    old_page_title: str = ""
    source_data: str = ""
    source_role: str = "active"
    new_filename: str = ""
    new_id: int = 0
    new_name: str = ""
    page_title: str = ""
    method: str = "unresolved"
    confidence: float = 0.0
    note: str = ""
    additional_targets: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.new_filename)

    @property
    def target_filenames(self) -> List[str]:
        return [
            self.new_filename,
            *[
                str(target.get("filename", ""))
                for target in self.additional_targets
                if target.get("filename")
            ],
        ]


@dataclass
class MigrationPlan:
    old_root: Path
    history_roots: List[Path]
    new_assets_root: Path
    supplemental_assets_roots: List[Path]
    assets: List[NewAsset]
    report_dir: Path
    catalog_items: List[Dict[str, Any]]
    excluded_new_pages: List[Dict[str, str]]
    migrated_groups: Dict[str, Dict[str, Any]]
    copied_config_files: Dict[str, Any]
    matches: List[MatchResult]
    unresolved_references: List[Dict[str, Any]]
    user_rows: List[Dict[str, Any]]
    history_merge_rows: List[Dict[str, Any]]
    history_summaries: List[Dict[str, Any]]
    baseline_reconciliation: Dict[str, Any]
    summary: Dict[str, Any]


class SignatureCache:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        raw = _read_json(path, {}) if path else {}
        self.values: Dict[str, Dict[str, Any]] = (
            raw.get("items", {})
            if isinstance(raw, dict) and isinstance(raw.get("items"), dict)
            else {}
        )
        self.changed = False

    def get(self, path: Path) -> ImageSignature:
        stat = path.stat()
        key = str(path.resolve())
        cached = self.values.get(key, {})
        if (
            cached.get("size") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
        ):
            value = cached.get("signature", {})
            try:
                return ImageSignature(
                    fit_white=int(value["fit_white"], 16),
                    fit_black=int(value["fit_black"], 16),
                    stretch=int(value["stretch"], 16),
                    aspect=float(value["aspect"]),
                )
            except (KeyError, TypeError, ValueError):
                pass
        signature = image_signature(path)
        self.values[key] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "signature": {
                "fit_white": f"{signature.fit_white:x}",
                "fit_black": f"{signature.fit_black:x}",
                "stretch": f"{signature.stretch:x}",
                "aspect": signature.aspect,
            },
        }
        self.changed = True
        return signature

    def save(self) -> None:
        if self.path and self.changed:
            _atomic_write_json(self.path, {"version": 1, "items": self.values})


def _composite(image: Image.Image, background: int) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    box = alpha.getbbox()
    if box:
        rgba = rgba.crop(box)
    base = Image.new("RGBA", rgba.size, (background, background, background, 255))
    base.alpha_composite(rgba)
    return base.convert("L")


def _fit_square(image: Image.Image, background: int, size: int = 32) -> Image.Image:
    fitted = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    output = Image.new("L", (size, size), background)
    output.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return output


_DCT_COS = tuple(
    tuple(
        math.cos((2 * coordinate + 1) * frequency * math.pi / 64.0)
        for coordinate in range(32)
    )
    for frequency in range(8)
)


def _perceptual_hash(image: Image.Image) -> int:
    resized = image.resize((32, 32), Image.Resampling.LANCZOS)
    if hasattr(resized, "get_flattened_data"):
        pixels = list(resized.get_flattened_data())
    else:  # Pillow < 12
        pixels = list(resized.getdata())
    horizontal = [[0.0] * 32 for _ in range(8)]
    for frequency in range(8):
        cosines = _DCT_COS[frequency]
        for y in range(32):
            horizontal[frequency][y] = sum(
                cosines[x] * pixels[y * 32 + x] for x in range(32)
            )
    coefficients: List[float] = []
    for vertical_frequency in range(8):
        vertical_cosines = _DCT_COS[vertical_frequency]
        for horizontal_frequency in range(8):
            coefficients.append(
                sum(
                    horizontal[horizontal_frequency][y] * vertical_cosines[y]
                    for y in range(32)
                )
            )
    median = statistics.median(coefficients[1:])
    result = 0
    for coefficient in coefficients[1:]:
        result = (result << 1) | int(coefficient > median)
    return result


def image_signature(path: Path) -> ImageSignature:
    with Image.open(path) as opened:
        try:
            opened.seek(0)
        except EOFError:
            pass
        image = ImageOps.exif_transpose(opened.copy())
    white = _composite(image, 255)
    black = _composite(image, 0)
    aspect = white.width / max(1, white.height)
    return ImageSignature(
        fit_white=_perceptual_hash(_fit_square(white, 255)),
        fit_black=_perceptual_hash(_fit_square(black, 0)),
        stretch=_perceptual_hash(white),
        aspect=aspect,
    )


def signature_distance(
    first: ImageSignature, second: ImageSignature
) -> Tuple[int, float]:
    distances = (
        (first.fit_white ^ second.fit_white).bit_count(),
        (first.fit_black ^ second.fit_black).bit_count(),
        (first.stretch ^ second.stretch).bit_count(),
    )
    aspect_delta = abs(math.log(max(first.aspect, 0.001) / max(second.aspect, 0.001)))
    return min(distances), aspect_delta


def _kind_priority(kind: str) -> int:
    return KIND_PRIORITY_FOLDED.get(str(kind or "").casefold(), 125)


def _load_release_config(
    path: Path,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str]]:
    raw = _read_json(path, {})
    items = raw.get("works", raw) if isinstance(raw, dict) else {}
    result: Dict[str, Dict[str, Any]] = {}
    if isinstance(items, dict):
        for sequence, (work, value) in enumerate(items.items(), 1):
            if isinstance(value, int):
                result[str(work)] = {"year": value, "date": "", "sequence": sequence}
            elif isinstance(value, dict):
                result[str(work)] = {
                    "year": int(value.get("year", 9999) or 9999),
                    "date": str(value.get("date", "") or ""),
                    "sequence": int(value.get("sequence", sequence) or sequence),
                }
    page_overrides: Dict[str, Dict[str, Any]] = {}
    raw_overrides = raw.get("page_overrides", {}) if isinstance(raw, dict) else {}
    if isinstance(raw_overrides, dict):
        page_overrides = {
            str(title): dict(value)
            for title, value in raw_overrides.items()
            if isinstance(value, dict)
        }
    excluded_pages: Dict[str, str] = {}
    raw_excluded = raw.get("excluded_pages", {}) if isinstance(raw, dict) else {}
    if isinstance(raw_excluded, dict):
        excluded_pages = {
            str(title): str(reason or "非角色页面")
            for title, reason in raw_excluded.items()
        }
    elif isinstance(raw_excluded, list):
        excluded_pages = {str(title): "非角色页面" for title in raw_excluded}
    return result, page_overrides, excluded_pages


def load_release_order(path: Path) -> Dict[str, Dict[str, Any]]:
    return _load_release_config(path)[0]


def _collection_hashes(records_dir: Path) -> Dict[str, str]:
    path = records_dir / COLLECTION_FILENAME
    if not path.is_file():
        return {}
    result: Dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            filename = Path(str(row.get("filename", ""))).name
            digest = str(row.get("sha256", "")).strip().lower()
            if filename and re.fullmatch(r"[0-9a-f]{64}", digest):
                result[filename] = digest
    return result


def load_new_assets(
    new_assets_root: Path,
    release_order_path: Path,
    supplemental_assets_roots: Sequence[Path] = (),
) -> Tuple[List[NewAsset], List[Dict[str, str]]]:
    collections = [
        (new_assets_root.resolve(), "base"),
        *[
            (root.resolve(), f"supplemental-{index}")
            for index, root in enumerate(supplemental_assets_roots, 1)
        ],
    ]
    release_order, page_overrides, excluded_pages = _load_release_config(
        release_order_path
    )
    assets: List[NewAsset] = []
    excluded_assets: List[Dict[str, str]] = []
    seen_filenames: Set[str] = set()
    seen_entry_keys: Set[str] = set()
    for collection_root, default_asset_set in collections:
        records_dir = collection_root / RECORDS_DIRNAME
        manifest_path = records_dir / MANIFEST_FILENAME
        manifest = _read_json(manifest_path, [])
        if not isinstance(manifest, list) or not manifest:
            raise RuntimeError(f"找不到有效的新素材清单：{manifest_path}")
        known_hashes = _collection_hashes(records_dir)
        for raw in manifest:
            if not isinstance(raw, dict):
                continue
            page_title = str(raw.get("page_title", "") or "").strip()
            filename = Path(str(raw.get("filename", ""))).name
            if page_title in excluded_pages:
                excluded_assets.append(
                    {
                        "page_title": page_title,
                        "filename": filename,
                        "reason": excluded_pages[page_title],
                    }
                )
                continue
            path = collection_root / filename
            if (
                not filename
                or path.suffix.lower() not in IMAGE_EXTENSIONS
                or not path.is_file()
            ):
                raise RuntimeError(f"新素材清单中的文件不存在：{path}")
            folded_filename = filename.casefold()
            if folded_filename in seen_filenames:
                raise RuntimeError(f"多个素材目录含重复文件名：{filename}")
            seen_filenames.add(folded_filename)
            pageid = int(raw.get("pageid", 0) or 0)
            variant_key = str(
                raw.get("variant_key") or page_title or _filename_stem(filename)
            ).strip()
            entry_key = str(raw.get("entry_key", "") or "").strip()
            if not entry_key:
                entry_key = (
                    f"wikirby-page:{pageid}"
                    if pageid
                    else f"asset-file:{normalise_name(filename)}"
                )
            folded_entry_key = entry_key.casefold()
            if folded_entry_key in seen_entry_keys:
                raise RuntimeError(
                    f"图鉴 entry_key 重复：{entry_key}；同页变体必须使用独立 entry_key"
                )
            seen_entry_keys.add(folded_entry_key)
            character_filename = Path(str(raw.get("character_filename", ""))).name
            display_name = (
                Path(character_filename).stem
                if character_filename
                else _filename_stem(filename)
            )
            page_override = page_overrides.get(page_title, {})
            debut_work = str(
                page_override.get("debut_work")
                or raw.get("earliest_work", "")
                or ""
            ).strip()
            work_order = release_order.get(debut_work, {})
            year = int(page_override.get("year") or work_order.get("year", 0) or 0)
            if not year:
                year = (
                    _extract_year(str(raw.get("earliest_work_raw", "") or ""))
                    or 9999
                )
            source = str(
                page_override.get("source")
                or raw.get("work_display_name")
                or raw.get("official_chinese_work")
                or debut_work
                or ""
            ).strip()
            catalog_kind = str(raw.get("catalog_kind", "") or "").strip()
            names = {
                display_name,
                page_title,
                variant_key,
                str(raw.get("chinese_name", "") or ""),
                str(raw.get("english_name", "") or ""),
                _filename_stem(filename),
            }
            if catalog_kind and default_asset_set != "base":
                names.update(
                    f"{name}卡比"
                    for name in list(names)
                    if _has_chinese(name) and not str(name).endswith("卡比")
                )
            asset = NewAsset(
                filename=filename,
                path=path,
                pageid=pageid,
                page_title=page_title,
                entry_key=entry_key,
                variant_key=variant_key,
                catalog_kind=catalog_kind,
                asset_set=str(raw.get("asset_set") or default_asset_set),
                name=display_name,
                source=source,
                debut_work=debut_work,
                debut_year=year,
                kind=str(raw.get("infobox_template", "") or "").strip(),
                names={name.strip() for name in names if name.strip()},
                sha256=known_hashes.get(filename, ""),
            )
            assets.append(asset)

    by_page_title = {
        asset.page_title.casefold(): asset for asset in assets if asset.page_title
    }
    for asset in assets:
        match = re.fullmatch(r"(.+?)\s+\(([^)]+)\)", asset.page_title)
        if not match:
            continue
        base = by_page_title.get(match.group(1).casefold())
        qualifier = _QUALIFIER_TRANSLATIONS.get(match.group(2).casefold())
        if base and qualifier:
            for base_name in base.names:
                if _has_chinese(base_name):
                    asset.names.add(f"{base_name}（{qualifier}）")
                    asset.names.add(f"{base_name}{qualifier}")

    def sort_key(asset: NewAsset) -> Tuple[Any, ...]:
        order = release_order.get(asset.debut_work, {})
        date = str(order.get("date", "") or "9999-99-99")
        return (
            asset.debut_year,
            date,
            int(order.get("sequence", 999999) or 999999),
            normalise_name(asset.debut_work),
            0
            if asset.page_title == "Kirby" and asset.asset_set == "base"
            else 1
            if asset.asset_set != "base" or asset.variant_key != asset.page_title
            else PAGE_PRIORITY.get(asset.page_title, 100),
            _kind_priority(asset.kind),
            asset.page_title.casefold(),
            int(asset.variant_key.casefold() != asset.page_title.casefold()),
            asset.filename.casefold(),
        )

    assets.sort(key=sort_key)
    for index, asset in enumerate(assets, 1):
        asset.catalog_id = index
    return assets, excluded_assets


def load_old_entries(old_root: Path) -> List[OldEntry]:
    catalog = _read_json(old_root / "catalog.json", {})
    raw_items = catalog.get("items", []) if isinstance(catalog, dict) else []
    if not isinstance(raw_items, list):
        raise RuntimeError(f"旧 catalog.json 格式无效：{old_root / 'catalog.json'}")
    assets_dir = old_root / "img" / "allies"
    entries: List[OldEntry] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        filename = Path(str(raw.get("filename", ""))).name
        if not filename:
            continue
        path = assets_dir / filename
        entries.append(
            OldEntry(
                entry_id=int(raw.get("id", 0) or 0),
                filename=filename,
                name=str(raw.get("name", "") or _filename_stem(filename)).strip(),
                source=str(raw.get("source", "") or "").strip(),
                aliases=[
                    str(value) for value in raw.get("aliases", []) if str(value).strip()
                ],
                path=path if path.is_file() else None,
                entry_key=str(raw.get("entry_key", "") or "").strip(),
                variant_key=str(raw.get("variant_key", "") or "").strip(),
                page_title=str(raw.get("page_title", "") or "").strip(),
            )
        )
    return sorted(entries, key=lambda item: (item.entry_id, item.filename.casefold()))


def load_overrides(path: Path) -> Dict[str, Any]:
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raise RuntimeError(f"迁移覆盖表格式无效：{path}")
    return raw


def _resolve_override(target: str, assets: Sequence[NewAsset]) -> Optional[NewAsset]:
    target = str(target or "").strip()
    if not target:
        return None
    folded = target.casefold()
    normalised = normalise_name(target)
    selectors = (
        lambda asset: asset.entry_key.casefold() == folded,
        lambda asset: asset.variant_key.casefold() == folded,
        lambda asset: asset.filename.casefold() == folded,
        lambda asset: asset.page_title.casefold() == folded,
        lambda asset: normalise_name(asset.name) == normalised,
    )
    for selector in selectors:
        matches = [asset for asset in assets if selector(asset)]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def _parse_override_spec(value: Any) -> Tuple[List[str], str]:
    reason = ""
    if isinstance(value, str):
        return ([value.strip()] if value.strip() else []), reason
    if isinstance(value, list):
        return [str(target).strip() for target in value if str(target).strip()], reason
    if not isinstance(value, Mapping):
        return [], reason
    primary = str(value.get("primary", "") or "").strip()
    raw_targets = value.get("unlock_targets", [])
    targets = (
        [str(target).strip() for target in raw_targets if str(target).strip()]
        if isinstance(raw_targets, list)
        else []
    )
    reason = str(value.get("reason", "") or "").strip()
    ordered = [primary, *targets] if primary else targets
    return list(dict.fromkeys(target for target in ordered if target)), reason


def _candidate_priority(asset: NewAsset) -> Tuple[int, int, int, str]:
    qualifier_penalty = int(bool(re.search(r"\([^)]+\)$", asset.page_title)))
    return (
        qualifier_penalty,
        len(asset.page_title),
        _kind_priority(asset.kind),
        asset.page_title.casefold(),
    )


def _best_name_candidates(
    old: OldEntry, assets: Sequence[NewAsset]
) -> Tuple[List[NewAsset], str]:
    exact_index: Dict[str, List[NewAsset]] = {}
    for asset in assets:
        for value in asset.names:
            key = normalise_name(value)
            if key:
                exact_index.setdefault(key, []).append(asset)
    exact: Dict[str, NewAsset] = {}
    for key in old.names():
        for asset in exact_index.get(key, []):
            exact[asset.filename] = asset
    if exact:
        return sorted(exact.values(), key=_candidate_priority), "exact_name"

    variants: Dict[str, NewAsset] = {}
    raw_old_names = {old.name, _filename_stem(old.filename), *old.aliases}
    for value in raw_old_names:
        for key in variant_base_names(value):
            for asset in exact_index.get(key, []):
                variants[asset.filename] = asset
    if variants:
        return sorted(variants.values(), key=_candidate_priority), "variant_base"
    return [], ""


def _decorated_name_candidates(
    old: OldEntry, assets: Sequence[NewAsset]
) -> List[Tuple[float, str, NewAsset]]:
    """Match a short legacy name inside an official name carrying a title.

    Forgotten Land commonly prefixes names with labels such as "Ultimate
    Life-Form" or "Possessed Beast".  Legacy files often retained only the
    character part, so exact matching alone misses them.
    """
    raw_names = {old.name, _filename_stem(old.filename), *old.aliases}
    if any(re.search(r"(?:和|与|&|＆|、|\+)", value) for value in raw_names):
        return []
    old_keys = {key for key in old.names() if len(key) >= 2}
    rows: Dict[str, Tuple[float, str, NewAsset]] = {}
    for asset in assets:
        if asset.page_title == "Kirby" and _is_kirby_form_name(old):
            continue
        best = 0.0
        relation = ""
        for value in asset.names:
            asset_key = normalise_name(value)
            if len(asset_key) < 2:
                continue
            for old_key in old_keys:
                if old_key == asset_key:
                    continue
                if asset_key.endswith(old_key):
                    short, long = sorted((len(old_key), len(asset_key)))
                    score = short / long
                    if score > best or (
                        score == best and relation != "official_prefix"
                    ):
                        best = score
                        relation = "official_prefix"
                elif old_key.endswith(asset_key):
                    short, long = sorted((len(old_key), len(asset_key)))
                    score = short / long
                    if score > best:
                        best = score
                        relation = "canonical_base"
        if best:
            rows[asset.filename] = (best, relation, asset)
    return sorted(
        rows.values(),
        key=lambda item: (
            -item[0],
            item[1] != "official_prefix",
            _candidate_priority(item[2]),
        ),
    )


def _canonical_rule_target(
    old: OldEntry, assets: Sequence[NewAsset]
) -> Optional[NewAsset]:
    """Resolve a small set of legacy character labels to canonical pages.

    Kirby abilities and forms are deliberately excluded from this fallback:
    they must resolve to their own supplemental entry or remain unresolved.
    Composite pictures are excluded because selecting one member would be
    arbitrary.
    """
    raw_names = {old.name, _filename_stem(old.filename), *old.aliases}
    if any(re.search(r"(?:和|与|&|＆|、|\+)", value) for value in raw_names):
        return None
    name = unicodedata.normalize("NFKC", old.name).translate(_CHAR_FOLD).strip(" .")
    target = ""
    if name == "瓦豆鲁迪":
        target = "Waddle Dee"
    return _resolve_override(target, assets) if target else None


def _fuzzy_candidates(
    old: OldEntry, assets: Sequence[NewAsset], limit: int = 5
) -> List[Tuple[float, NewAsset]]:
    old_keys = [key for key in old.names() if len(key) >= 3]
    if not old_keys:
        return []
    rows: List[Tuple[float, NewAsset]] = []
    for asset in assets:
        asset_keys = {
            normalise_name(value)
            for value in asset.names
            if len(normalise_name(value)) >= 3
        }
        score = max(
            (
                SequenceMatcher(None, old_key, asset_key).ratio()
                for old_key in old_keys
                for asset_key in asset_keys
            ),
            default=0.0,
        )
        if score >= 0.55:
            rows.append((score, asset))
    rows.sort(key=lambda item: (-item[0], _candidate_priority(item[1])))
    return rows[:limit]


def _visual_candidates(
    old: OldEntry,
    assets: Sequence[NewAsset],
    cache: SignatureCache,
    limit: int = 5,
) -> List[Tuple[int, float, NewAsset]]:
    if old.path is None:
        return []
    if old.signature is None:
        old.signature = cache.get(old.path)
    rows: List[Tuple[int, float, NewAsset]] = []
    for asset in assets:
        if asset.signature is None:
            asset.signature = cache.get(asset.path)
        distance, aspect_delta = signature_distance(old.signature, asset.signature)
        rows.append((distance, aspect_delta, asset))
    rows.sort(
        key=lambda item: (
            item[0] + min(item[1], 1.5) * 3.0,
            item[0],
            item[1],
            item[2].catalog_id,
        )
    )
    return rows[:limit]


def match_old_entries(
    old_entries: Sequence[OldEntry],
    assets: Sequence[NewAsset],
    overrides: Mapping[str, Any],
    signature_cache: SignatureCache,
    progress: Optional[Any] = None,
    source_data: str = "",
    source_role: str = "active",
) -> List[MatchResult]:
    by_digest: Dict[str, List[NewAsset]] = {}
    for asset in assets:
        if asset.sha256:
            by_digest.setdefault(asset.sha256, []).append(asset)
    filename_overrides = (
        overrides.get("by_old_filename", {}) if isinstance(overrides, Mapping) else {}
    )
    name_overrides = (
        overrides.get("by_old_name", {}) if isinstance(overrides, Mapping) else {}
    )
    ignored = {
        normalise_name(value)
        for value in (
            overrides.get("ignored", []) if isinstance(overrides, Mapping) else []
        )
    }
    results: List[MatchResult] = []
    for index, old in enumerate(old_entries, 1):
        result = MatchResult(
            old_id=old.entry_id,
            old_filename=old.filename,
            old_name=old.name,
            old_source=old.source,
            old_file_exists=old.path is not None,
            old_entry_key=old.entry_key,
            old_variant_key=old.variant_key,
            old_page_title=old.page_title,
            source_data=source_data,
            source_role=source_role,
        )
        identity_target = _resolve_override(old.entry_key, assets)
        if old.entry_key and identity_target is not None:
            _set_match(
                result,
                identity_target,
                "entry_key",
                1.0,
                "沿用规范 catalog 的稳定 entry_key",
            )
            results.append(result)
            if progress:
                progress(index, len(old_entries), result)
            continue
        override_spec: Any = None
        if isinstance(filename_overrides, Mapping):
            override_spec = filename_overrides.get(old.filename)
        if override_spec in (None, "") and isinstance(name_overrides, Mapping):
            override_spec = name_overrides.get(old.name)
        override_targets, override_reason = _parse_override_spec(override_spec)
        if override_spec not in (None, "") and not override_targets:
            raise RuntimeError(f"覆盖表条目格式无效：{old.filename}")
        if override_targets:
            resolved_targets: List[NewAsset] = []
            for override_target in override_targets:
                target = _resolve_override(override_target, assets)
                if target is None:
                    raise RuntimeError(
                        f"覆盖表目标不存在或不唯一：{old.filename} -> {override_target}"
                    )
                if all(
                    existing.filename != target.filename
                    for existing in resolved_targets
                ):
                    resolved_targets.append(target)
            method = "override_expansion" if len(resolved_targets) > 1 else "override"
            note = override_reason or (
                "人工核对组合素材并展开到规范角色"
                if len(resolved_targets) > 1
                else "人工核对覆盖表"
            )
            _set_match(result, resolved_targets[0], method, 1.0, note)
            result.additional_targets = [
                {
                    "filename": target.filename,
                    "id": target.catalog_id,
                    "name": target.name,
                    "page_title": target.page_title,
                }
                for target in resolved_targets[1:]
            ]
            results.append(result)
            continue
        if (
            normalise_name(old.filename) in ignored
            or normalise_name(old.name) in ignored
        ):
            result.note = "覆盖表标记为不迁移"
            results.append(result)
            continue

        filename_targets = [
            asset
            for asset in assets
            if asset.filename.casefold() == old.filename.casefold()
        ]
        if len(filename_targets) == 1:
            _set_match(
                result,
                filename_targets[0],
                "canonical_filename",
                1.0,
                "规范素材文件名完全一致",
            )
            results.append(result)
            if progress:
                progress(index, len(old_entries), result)
            continue

        candidate_assets = (
            [
                asset
                for asset in assets
                if asset.asset_set != "base" or bool(asset.catalog_kind)
            ]
            if _is_kirby_form_name(old)
            else list(assets)
        )
        if old.path is not None:
            old.sha256 = _sha256(old.path)
            allowed_filenames = {asset.filename for asset in candidate_assets}
            digest_matches = [
                asset
                for asset in by_digest.get(old.sha256, [])
                if asset.filename in allowed_filenames
            ]
            if len(digest_matches) == 1:
                _set_match(
                    result, digest_matches[0], "sha256", 1.0, "新旧图片字节完全一致"
                )
                results.append(result)
                continue

        name_matches, name_method = _best_name_candidates(old, candidate_assets)
        if len(name_matches) == 1 and name_method == "exact_name":
            confidence = 0.97 if name_method == "exact_name" else 0.88
            note = (
                "名称规范化后唯一"
                if name_method == "exact_name"
                else "去除 EX/幻/结晶化等版本标记后唯一"
            )
            _set_match(result, name_matches[0], name_method, confidence, note)
            results.append(result)
            continue
        if len(name_matches) > 1:
            ordered_candidates = sorted(name_matches, key=_candidate_priority)
            if (
                _candidate_priority(ordered_candidates[0])[:-1]
                < _candidate_priority(ordered_candidates[1])[:-1]
            ):
                confidence = 0.95 if name_method == "exact_name" else 0.86
                _set_match(
                    result,
                    ordered_candidates[0],
                    name_method,
                    confidence,
                    "同名候选中优先采用无括号限定的基础主页面",
                )
                results.append(result)
                continue

        visual: List[Tuple[int, float, NewAsset]] = []
        if old.path is not None:
            visual = _visual_candidates(old, candidate_assets, signature_cache)
            if visual:
                best_distance, best_aspect, best_asset = visual[0]
                next_distance = visual[1][0] if len(visual) > 1 else 64
                if (
                    best_distance <= 3
                    and next_distance - best_distance >= 2
                    and best_aspect <= 0.35
                ):
                    _set_match(
                        result,
                        best_asset,
                        "perceptual_hash",
                        0.95,
                        f"感知哈希距离 {best_distance}",
                    )
                    results.append(result)
                    continue

        decorated = _decorated_name_candidates(old, candidate_assets)
        if decorated:
            best_score, relation, best_asset = decorated[0]
            next_score = decorated[1][0] if len(decorated) > 1 else 0.0
            if best_score >= 0.45 and best_score - next_score >= 0.08:
                method = (
                    "official_decorated_name"
                    if relation == "official_prefix"
                    else "canonical_character"
                )
                note = (
                    f"旧名是官方完整名称的唯一角色后缀（{best_score:.2f}）"
                    if relation == "official_prefix"
                    else f"旧版本/职业形态归并到规范角色页面（{best_score:.2f}）"
                )
                _set_match(
                    result,
                    best_asset,
                    method,
                    0.95 if relation == "official_prefix" else 0.84,
                    note,
                )
                results.append(result)
                continue

        canonical_target = _canonical_rule_target(old, candidate_assets)
        if canonical_target is not None:
            _set_match(
                result,
                canonical_target,
                "canonical_character",
                0.84,
                "旧素材是能力、服装或职业版本，归并到规范角色页面",
            )
            results.append(result)
            continue

        if len(name_matches) == 1 and name_method == "variant_base":
            if visual:
                best_distance, best_aspect, best_asset = visual[0]
                next_distance = visual[1][0] if len(visual) > 1 else 64
                if (
                    best_asset.filename != name_matches[0].filename
                    and best_distance <= 3
                    and next_distance - best_distance >= 2
                    and best_aspect <= 0.35
                ):
                    _set_match(
                        result,
                        best_asset,
                        "variant_perceptual_hash",
                        0.97,
                        f"版本名可归并，但图片精确指向独立页面（感知距离 {best_distance}）",
                    )
                    results.append(result)
                    continue
            _set_match(
                result,
                name_matches[0],
                name_method,
                0.88,
                "去除 EX/幻/结晶化等版本标记后唯一",
            )
            results.append(result)
            continue

        fuzzy = _fuzzy_candidates(old, candidate_assets)
        if not visual and old.path is not None:
            visual = _visual_candidates(old, candidate_assets, signature_cache)
        fuzzy_by_filename = {asset.filename: score for score, asset in fuzzy}
        visual_rows: List[Dict[str, Any]] = []
        for distance, aspect_delta, asset in visual:
            visual_rows.append(
                {
                    "filename": asset.filename,
                    "id": asset.catalog_id,
                    "name": asset.name,
                    "page_title": asset.page_title,
                    "phash_distance": distance,
                    "aspect_delta": round(aspect_delta, 4),
                    "name_score": round(fuzzy_by_filename.get(asset.filename, 0.0), 4),
                }
            )
        if visual_rows:
            result.candidates = visual_rows
        else:
            result.candidates = [
                {
                    "filename": asset.filename,
                    "id": asset.catalog_id,
                    "name": asset.name,
                    "page_title": asset.page_title,
                    "name_score": round(score, 4),
                }
                for score, asset in fuzzy
            ]

        if visual:
            best_distance, best_aspect, best_asset = visual[0]
            next_distance = visual[1][0] if len(visual) > 1 else 64
            name_score = fuzzy_by_filename.get(best_asset.filename, 0.0)
            if (
                best_distance <= 3
                and next_distance - best_distance >= 2
                and best_aspect <= 0.35
            ):
                _set_match(
                    result,
                    best_asset,
                    "perceptual_hash",
                    0.95,
                    f"感知哈希距离 {best_distance}",
                )
            elif (
                best_distance <= 7
                and next_distance - best_distance >= 3
                and name_score >= 0.72
            ):
                _set_match(
                    result,
                    best_asset,
                    "name_and_perceptual_hash",
                    0.91,
                    f"名称相似度 {name_score:.2f}，感知哈希距离 {best_distance}",
                )
        if not result.matched and fuzzy:
            best_score, best_asset = fuzzy[0]
            next_score = fuzzy[1][0] if len(fuzzy) > 1 else 0.0
            if best_score >= 0.94 and best_score - next_score >= 0.08:
                _set_match(
                    result,
                    best_asset,
                    "fuzzy_name",
                    0.90,
                    f"唯一高相似名称 {best_score:.2f}",
                )
        if not result.matched:
            if name_matches:
                result.note = "名称命中多个候选，未自动猜测"
                result.candidates = [
                    {
                        "filename": asset.filename,
                        "id": asset.catalog_id,
                        "name": asset.name,
                        "page_title": asset.page_title,
                        "name_score": 1.0,
                    }
                    for asset in name_matches[:5]
                ]
            elif not result.note:
                result.note = "没有达到自动迁移阈值"
        results.append(result)
        if progress:
            progress(index, len(old_entries), result)
    signature_cache.save()
    return results


def _set_match(
    result: MatchResult,
    asset: NewAsset,
    method: str,
    confidence: float,
    note: str,
) -> None:
    result.new_filename = asset.filename
    result.new_id = asset.catalog_id
    result.new_name = asset.name
    result.page_title = asset.page_title
    result.method = method
    result.confidence = confidence
    result.note = note


def _old_reference(value: Any) -> str:
    if isinstance(value, str):
        return Path(value).name
    if isinstance(value, dict):
        raw = (
            value.get("ally_filename")
            or value.get("wife_name")
            or value.get("filename")
            or value.get("name")
        )
        return Path(str(raw or "")).name
    return ""


def _unlock_date(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("unlock_date") or value.get("date") or "").strip()
    return ""


def migrate_groups(
    old_root: Path,
    matches: Sequence[MatchResult],
    catalog_ids: Mapping[str, int],
    source_role: str = "active",
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Any],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Set[str],
]:
    match_by_old: Dict[str, MatchResult] = {}
    for match in matches:
        match_by_old[match.old_filename] = match
        match_by_old.setdefault(Path(match.old_filename).name, match)
    config_dir = old_root / "config"
    groups: Dict[str, Dict[str, Any]] = {}
    copied: Dict[str, Any] = {}
    unresolved_rows: List[Dict[str, Any]] = []
    user_rows: List[Dict[str, Any]] = []
    referenced_old_filenames: Set[str] = set()
    for config_path in sorted(
        config_dir.glob("*.json"), key=lambda path: path.name.casefold()
    ):
        raw = _read_json(config_path, {})
        if config_path.name == "draw_limits.json" or not _safe_group_filename(
            config_path
        ):
            copied[config_path.name] = raw
            continue
        if not isinstance(raw, dict):
            copied[config_path.name] = raw
            continue
        migrated_users: Dict[str, Any] = {}
        for user_id, raw_user in raw.items():
            if not isinstance(raw_user, dict):
                continue
            user = copy.deepcopy(raw_user)
            original_unlocks = raw_user.get("unlocked", [])
            if not isinstance(original_unlocks, list):
                original_unlocks = []
            by_new_filename: Dict[str, Dict[str, str]] = {}
            unresolved_for_user = 0
            mapped_rows = 0
            generated_target_rows = 0
            expanded_unlock_targets = 0
            for item in original_unlocks:
                old_filename = _old_reference(item)
                if old_filename:
                    referenced_old_filenames.add(old_filename)
                match = match_by_old.get(old_filename)
                if match and match.matched:
                    mapped_rows += 1
                    targets = match.target_filenames
                    generated_target_rows += len(targets)
                    expanded_unlock_targets += max(0, len(targets) - 1)
                    date = _unlock_date(item)
                    for target_filename in targets:
                        previous = by_new_filename.get(target_filename)
                        if previous is None or (
                            date
                            and (
                                not previous["unlock_date"]
                                or date < previous["unlock_date"]
                            )
                        ):
                            by_new_filename[target_filename] = {
                                "ally_filename": target_filename,
                                "unlock_date": date,
                            }
                elif old_filename:
                    unresolved_for_user += 1
                    unresolved_rows.append(
                        {
                            "source_role": source_role,
                            "source_data": str(old_root),
                            "group_id": config_path.stem,
                            "user_id": str(user_id),
                            "nickname": str(raw_user.get("nickname", "") or ""),
                            "location": "unlocked",
                            "old_filename": old_filename,
                            "unlock_date": _unlock_date(item),
                        }
                    )
            user["unlocked"] = sorted(
                by_new_filename.values(),
                key=lambda item: (
                    item.get("unlock_date") or "9999-99-99",
                    catalog_ids.get(item["ally_filename"], 999999),
                ),
            )

            current = raw_user.get("current", {})
            if isinstance(current, str):
                current = {"ally_filename": current, "date": ""}
            if not isinstance(current, dict):
                current = {}
            old_current = _old_reference(current)
            if old_current:
                referenced_old_filenames.add(old_current)
            current_match = match_by_old.get(old_current)
            current_mapped = bool(current_match and current_match.matched)
            if current_mapped and current_match:
                user["current"] = {
                    **current,
                    "ally_filename": current_match.new_filename,
                }
                user["current"].pop("wife_name", None)
                user["current"].pop("filename", None)
            else:
                user["current"] = {**current, "ally_filename": ""}
                user["current"].pop("wife_name", None)
                user["current"].pop("filename", None)
                if old_current:
                    unresolved_rows.append(
                        {
                            "source_role": source_role,
                            "source_data": str(old_root),
                            "group_id": config_path.stem,
                            "user_id": str(user_id),
                            "nickname": str(raw_user.get("nickname", "") or ""),
                            "location": "current",
                            "old_filename": old_current,
                            "unlock_date": str(current.get("date", "") or ""),
                        }
                    )
            migrated_users[str(user_id)] = user
            user_rows.append(
                {
                    "source_role": source_role,
                    "source_data": str(old_root),
                    "group_id": config_path.stem,
                    "user_id": str(user_id),
                    "nickname": str(raw_user.get("nickname", "") or ""),
                    "old_unlock_rows": len(original_unlocks),
                    "mapped_unlock_rows": mapped_rows,
                    "generated_unlock_targets": generated_target_rows,
                    "expanded_unlock_targets": expanded_unlock_targets,
                    "new_unique_unlocks": len(by_new_filename),
                    "merged_unlock_rows": max(0, mapped_rows - len(by_new_filename)),
                    "deduplicated_unlock_targets": max(
                        0, generated_target_rows - len(by_new_filename)
                    ),
                    "unresolved_unlock_rows": unresolved_for_user,
                    "old_current": old_current,
                    "new_current": current_match.new_filename
                    if current_mapped and current_match
                    else "",
                    "current_mapped": current_mapped or not old_current,
                }
            )
        groups[config_path.name] = migrated_users
    return groups, copied, unresolved_rows, user_rows, referenced_old_filenames


def _sorted_unlocks(
    items: Iterable[Mapping[str, Any]], catalog_ids: Mapping[str, int]
) -> List[Dict[str, str]]:
    by_filename: Dict[str, Dict[str, str]] = {}
    for item in items:
        filename = _old_reference(item)
        if not filename:
            continue
        date = _unlock_date(item)
        previous = by_filename.get(filename)
        if previous is None or (
            date
            and (
                not previous["unlock_date"]
                or date < previous["unlock_date"]
            )
        ):
            by_filename[filename] = {
                "ally_filename": filename,
                "unlock_date": date,
            }
    return sorted(
        by_filename.values(),
        key=lambda item: (
            item.get("unlock_date") or "9999-99-99",
            catalog_ids.get(item["ally_filename"], 999999),
        ),
    )


def _portable_basename(value: Any) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _match_from_report(raw: Mapping[str, Any], source_root: Path) -> MatchResult:
    allowed = {item.name for item in fields(MatchResult)}
    payload = {key: value for key, value in raw.items() if key in allowed}
    payload["source_data"] = str(source_root)
    payload["source_role"] = "baseline"
    try:
        return MatchResult(**payload)
    except TypeError as exc:
        raise RuntimeError("上次迁移报告中的匹配记录格式无效") from exc


def _remap_baseline_groups(
    groups: Mapping[str, Mapping[str, Any]],
    filename_targets: Mapping[str, Sequence[str]],
    catalog_ids: Mapping[str, int],
) -> Dict[str, Dict[str, Any]]:
    remapped: Dict[str, Dict[str, Any]] = {}
    for group_name, users in groups.items():
        remapped_users: Dict[str, Any] = {}
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            user = copy.deepcopy(raw_user)
            unlocks: List[Dict[str, str]] = []
            for item in raw_user.get("unlocked", []):
                old_filename = _old_reference(item)
                for target in filename_targets.get(old_filename, ()):
                    unlocks.append(
                        {
                            "ally_filename": target,
                            "unlock_date": _unlock_date(item),
                        }
                    )
            user["unlocked"] = _sorted_unlocks(unlocks, catalog_ids)

            current = raw_user.get("current", {})
            old_current = _old_reference(current)
            current_targets = filename_targets.get(old_current, ())
            current_dict = copy.deepcopy(current) if isinstance(current, dict) else {}
            current_dict["ally_filename"] = current_targets[0] if current_targets else ""
            current_dict.pop("wife_name", None)
            current_dict.pop("filename", None)
            user["current"] = current_dict
            remapped_users[str(user_id)] = user
        remapped[group_name] = remapped_users
    return remapped


def load_active_migration_baseline(
    active_root: Path,
    history_roots: Sequence[Path],
    active_entries: Sequence[OldEntry],
    active_matches: Sequence[MatchResult],
    catalog_ids: Mapping[str, int],
) -> Tuple[
    Optional[Dict[str, Dict[str, Any]]],
    Optional[Path],
    Dict[str, Any],
]:
    """Rebuild the active catalog state created by the previous migration."""
    if not history_roots:
        return None, None, {"status": "not_needed"}
    state_path = active_root / "migration_state.json"
    state = _read_json(state_path, {})
    if not isinstance(state, dict) or not isinstance(state.get("summary"), dict):
        return None, None, {
            "status": "not_available",
            "reason": "活动数据没有规范迁移状态，历史源按普通增量数据合并",
        }

    generated_at = str(state["summary"].get("generated_at", "") or "")
    report_candidates: List[Tuple[Path, Dict[str, Any]]] = []
    reports_root = active_root / "migration_reports"
    if reports_root.is_dir():
        for report_path in reports_root.rglob("migration_plan.json"):
            raw = _read_json(report_path, {})
            if not isinstance(raw, dict):
                continue
            summary = raw.get("summary", {})
            if isinstance(summary, dict) and str(
                summary.get("generated_at", "") or ""
            ) == generated_at:
                report_candidates.append((report_path, raw))
    if not report_candidates:
        raise RuntimeError(
            "检测到活动数据已经执行过规范迁移，但找不到与 migration_state.json "
            "对应的 migration_plan.json；为避免重复计算旧映射，已停止迁移"
        )
    report_path, report = sorted(
        report_candidates, key=lambda item: str(item[0]).casefold()
    )[-1]
    raw_matches = report.get("matches", [])
    raw_catalog = report.get("catalog", {})
    prior_catalog_items = (
        raw_catalog.get("items", []) if isinstance(raw_catalog, dict) else []
    )
    if not isinstance(raw_matches, list) or not isinstance(prior_catalog_items, list):
        raise RuntimeError("上次迁移报告缺少有效的 matches 或 catalog")

    report_old_filenames = {
        Path(str(item.get("old_filename", "") or "")).name
        for item in raw_matches
        if isinstance(item, dict) and item.get("old_filename")
    }
    backup_name = _portable_basename(state.get("backup_path", ""))
    ranked_history_roots: List[Tuple[int, float, Path]] = []
    for history_root in history_roots:
        history_filenames = {
            entry.filename for entry in load_old_entries(history_root)
        }
        coverage = (
            len(report_old_filenames & history_filenames)
            / len(report_old_filenames)
            if report_old_filenames
            else 0.0
        )
        ranked_history_roots.append(
            (int(history_root.name == backup_name), coverage, history_root)
        )
    ranked_history_roots.sort(key=lambda item: (-item[0], -item[1], str(item[2])))
    backup_matched, history_coverage, baseline_history_root = ranked_history_roots[0]
    if history_coverage < 0.9:
        raise RuntimeError(
            "历史数据源与上次迁移报告不匹配；请传入 migration_state.json "
            "中 backup_path 对应的 .before-v3-* 目录"
        )

    legacy_matches = [
        _match_from_report(item, baseline_history_root)
        for item in raw_matches
        if isinstance(item, dict)
    ]
    prior_catalog_ids = {
        Path(str(item.get("filename", "") or "")).name: int(item.get("id", 0) or 0)
        for item in prior_catalog_items
        if isinstance(item, dict) and item.get("filename")
    }
    baseline_prior_groups, _, _, _, _ = migrate_groups(
        baseline_history_root,
        legacy_matches,
        prior_catalog_ids,
        source_role="baseline",
    )

    active_by_filename = {entry.filename.casefold(): entry for entry in active_entries}
    active_by_entry_key: Dict[str, List[OldEntry]] = {}
    for entry in active_entries:
        if entry.entry_key:
            active_by_entry_key.setdefault(entry.entry_key.casefold(), []).append(entry)
    active_match_by_filename = {
        match.old_filename.casefold(): match for match in active_matches
    }
    prior_to_new: Dict[str, Sequence[str]] = {}
    mapped_prior_items = 0
    for item in prior_catalog_items:
        if not isinstance(item, dict):
            continue
        prior_filename = Path(str(item.get("filename", "") or "")).name
        entry_key = str(item.get("entry_key", "") or "").casefold()
        candidates = active_by_entry_key.get(entry_key, []) if entry_key else []
        active_entry = (
            candidates[0]
            if len(candidates) == 1
            else active_by_filename.get(prior_filename.casefold())
        )
        if active_entry is None:
            continue
        active_match = active_match_by_filename.get(active_entry.filename.casefold())
        if active_match is None or not active_match.matched:
            continue
        targets = tuple(active_match.target_filenames)
        if targets:
            prior_to_new[prior_filename] = targets
            mapped_prior_items += 1

    baseline_groups = _remap_baseline_groups(
        baseline_prior_groups, prior_to_new, catalog_ids
    )
    prior_unique_unlocks = sum(
        len(user.get("unlocked", []))
        for users in baseline_prior_groups.values()
        for user in users.values()
        if isinstance(user, dict)
    )
    mapped_unique_unlocks = sum(
        len(user.get("unlocked", []))
        for users in baseline_groups.values()
        for user in users.values()
        if isinstance(user, dict)
    )
    return baseline_groups, baseline_history_root, {
        "status": "reconstructed",
        "migration_state": str(state_path),
        "migration_report": str(report_path),
        "history_source": str(baseline_history_root),
        "backup_path_matched": bool(backup_matched),
        "history_catalog_coverage": round(history_coverage * 100, 2),
        "prior_catalog_entries": len(prior_catalog_items),
        "prior_catalog_entries_mapped": mapped_prior_items,
        "prior_unique_unlocks": prior_unique_unlocks,
        "mapped_baseline_unique_unlocks": mapped_unique_unlocks,
        "previous_migration_generated_at": generated_at,
        "previous_migration_applied_at": str(state.get("applied_at", "") or ""),
    }


def merge_history_groups(
    active_groups: Mapping[str, Mapping[str, Any]],
    history_groups: Sequence[Tuple[Path, Mapping[str, Mapping[str, Any]]]],
    catalog_ids: Mapping[str, int],
    baseline_groups: Optional[Mapping[str, Mapping[str, Any]]] = None,
    baseline_history_root: Optional[Path] = None,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, Any],
]:
    """Merge migrated backups into the active data without rolling it back."""
    merged: Dict[str, Dict[str, Any]] = copy.deepcopy(dict(active_groups))
    active_counts = {
        (group_name, str(user_id)): len(user.get("unlocked", []))
        for group_name, users in active_groups.items()
        for user_id, user in users.items()
        if isinstance(user, dict)
    }
    baseline_removed: Dict[Tuple[str, str], int] = {}
    post_migration_counts = dict(active_counts)
    baseline_users_reconciled = 0
    baseline_current_rows_identified = 0
    baseline_current_rows_changed = 0
    active_current_rows_preserved = 0

    if baseline_groups is not None:
        corrected_history_groups: Mapping[str, Mapping[str, Any]] = {}
        for history_root, groups in history_groups:
            if baseline_history_root is not None and history_root == baseline_history_root:
                corrected_history_groups = groups
                break
        if not corrected_history_groups:
            raise RuntimeError("找不到用于基线校正的历史迁移结果")

        for group_name, users in merged.items():
            baseline_users = baseline_groups.get(group_name, {})
            corrected_users = corrected_history_groups.get(group_name, {})
            for user_id, active_user in users.items():
                if not isinstance(active_user, dict):
                    continue
                user_key = (group_name, str(user_id))
                baseline_user = baseline_users.get(str(user_id))
                if not isinstance(baseline_user, dict):
                    active_current_rows_preserved += bool(
                        _old_reference(active_user.get("current", {}))
                    )
                    continue

                baseline_users_reconciled += 1
                baseline_filenames = {
                    _old_reference(item)
                    for item in baseline_user.get("unlocked", [])
                    if _old_reference(item)
                }
                active_unlocks = _sorted_unlocks(
                    active_user.get("unlocked", []), catalog_ids
                )
                kept_unlocks = [
                    item
                    for item in active_unlocks
                    if item["ally_filename"] not in baseline_filenames
                ]
                removed = len(active_unlocks) - len(kept_unlocks)
                active_user["unlocked"] = kept_unlocks
                baseline_removed[user_key] = removed
                post_migration_counts[user_key] = len(kept_unlocks)

                active_current = _old_reference(active_user.get("current", {}))
                baseline_current = _old_reference(baseline_user.get("current", {}))
                if active_current == baseline_current:
                    baseline_current_rows_identified += 1
                    corrected_user = corrected_users.get(str(user_id), {})
                    corrected_current = (
                        corrected_user.get("current", {})
                        if isinstance(corrected_user, dict)
                        else {}
                    )
                    if isinstance(corrected_current, dict):
                        active_user["current"] = copy.deepcopy(corrected_current)
                    else:
                        active_user["current"] = {
                            "ally_filename": _old_reference(corrected_current),
                            "date": "",
                        }
                    if _old_reference(active_user["current"]) != active_current:
                        baseline_current_rows_changed += 1
                else:
                    active_current_rows_preserved += bool(active_current)

    additions: Dict[Tuple[str, str], int] = {}
    recovered_dates: Dict[Tuple[str, str], int] = {}
    contributing_sources: Dict[Tuple[str, str], Set[str]] = {}
    source_stats: List[Dict[str, Any]] = []

    for history_root, groups in history_groups:
        source_added = 0
        source_dates = 0
        source_users_added = 0
        source_groups_added = 0
        source_name = str(history_root)
        for group_name, history_users in groups.items():
            if group_name not in merged:
                merged[group_name] = {}
                source_groups_added += 1
            target_users = merged[group_name]
            for user_id, raw_history_user in history_users.items():
                if not isinstance(raw_history_user, dict):
                    continue
                user_key = (group_name, str(user_id))
                history_user = copy.deepcopy(raw_history_user)
                history_unlocks = _sorted_unlocks(
                    history_user.get("unlocked", []), catalog_ids
                )
                if str(user_id) not in target_users:
                    history_user["unlocked"] = history_unlocks
                    target_users[str(user_id)] = history_user
                    added = len(history_unlocks)
                    source_added += added
                    source_users_added += 1
                    additions[user_key] = additions.get(user_key, 0) + added
                    if added:
                        contributing_sources.setdefault(user_key, set()).add(
                            source_name
                        )
                    continue

                active_user = target_users[str(user_id)]
                if not isinstance(active_user, dict):
                    active_user = {}
                    target_users[str(user_id)] = active_user
                before = {
                    _old_reference(item): _unlock_date(item)
                    for item in active_user.get("unlocked", [])
                    if _old_reference(item)
                }
                combined = _sorted_unlocks(
                    [*active_user.get("unlocked", []), *history_unlocks],
                    catalog_ids,
                )
                after = {
                    item["ally_filename"]: item.get("unlock_date", "")
                    for item in combined
                }
                added = len(set(after) - set(before))
                date_updates = sum(
                    filename in before
                    and bool(after_date)
                    and (not before[filename] or after_date < before[filename])
                    for filename, after_date in after.items()
                )
                active_user["unlocked"] = combined
                source_added += added
                source_dates += date_updates
                additions[user_key] = additions.get(user_key, 0) + added
                recovered_dates[user_key] = (
                    recovered_dates.get(user_key, 0) + date_updates
                )
                if added or date_updates:
                    contributing_sources.setdefault(user_key, set()).add(source_name)
        source_stats.append(
            {
                "source_data": source_name,
                "history_groups_added": source_groups_added,
                "history_users_added": source_users_added,
                "history_unlocks_added": source_added,
                "history_unlock_dates_recovered": source_dates,
            }
        )

    merge_rows: List[Dict[str, Any]] = []
    for group_name, users in sorted(merged.items()):
        for user_id, user in sorted(users.items()):
            if not isinstance(user, dict):
                continue
            user_key = (group_name, str(user_id))
            final_count = len(user.get("unlocked", []))
            merge_rows.append(
                {
                    "group_id": Path(group_name).stem,
                    "user_id": str(user_id),
                    "nickname": str(user.get("nickname", "") or ""),
                    "active_unique_unlocks": active_counts.get(user_key, 0),
                    "active_baseline_unlocks_removed": baseline_removed.get(
                        user_key, 0
                    ),
                    "active_post_migration_unlocks_preserved": post_migration_counts.get(
                        user_key, 0
                    ),
                    "history_unlocks_added": additions.get(user_key, 0),
                    "history_unlock_dates_recovered": recovered_dates.get(
                        user_key, 0
                    ),
                    "final_unique_unlocks": final_count,
                    "net_unlock_change": final_count
                    - active_counts.get(user_key, 0),
                    "contributing_history_sources": " | ".join(
                        sorted(contributing_sources.get(user_key, set()))
                    ),
                }
            )
    reconciliation = {
        "enabled": baseline_groups is not None,
        "baseline_users_reconciled": baseline_users_reconciled,
        "active_baseline_unlocks_removed": sum(baseline_removed.values()),
        "active_post_migration_unlocks_preserved": sum(
            post_migration_counts.values()
        ),
        "baseline_current_rows_identified": baseline_current_rows_identified,
        "baseline_current_rows_changed": baseline_current_rows_changed,
        "active_current_rows_preserved": active_current_rows_preserved,
    }
    return merged, merge_rows, source_stats, reconciliation


def build_catalog(
    assets: Sequence[NewAsset], matches: Sequence[MatchResult]
) -> List[Dict[str, Any]]:
    aliases: Dict[str, Set[str]] = {
        asset.filename: set(asset.names) for asset in assets
    }
    for match in matches:
        if not match.matched:
            continue
        values = aliases.setdefault(match.new_filename, set())
        values.update({match.old_filename, match.old_name})
    items: List[Dict[str, Any]] = []
    for asset in assets:
        item = asset.public_record()
        item["aliases"] = sorted(
            {
                value.strip()
                for value in aliases.get(asset.filename, set())
                if value.strip() and value.strip() not in {asset.filename, asset.name}
            },
            key=lambda value: (normalise_name(value), value),
        )
        items.append(item)
    return items


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _sum_user_rows(rows: Sequence[Mapping[str, Any]], key: str) -> int:
    return sum(int(row.get(key, 0) or 0) for row in rows)


def create_plan(
    old_root: Path,
    new_assets_root: Path,
    report_dir: Path,
    release_order_path: Path,
    overrides_path: Path,
    progress: Optional[Any] = None,
    supplemental_assets_roots: Sequence[Path] = (),
    history_roots: Sequence[Path] = (),
) -> MigrationPlan:
    old_root = old_root.resolve()
    new_assets_root = new_assets_root.resolve()
    supplemental_assets_roots = [root.resolve() for root in supplemental_assets_roots]
    history_roots = [root.resolve() for root in history_roots]
    report_dir = report_dir.resolve()
    asset_roots = [new_assets_root, *supplemental_assets_roots]
    for index, asset_root in enumerate(asset_roots):
        if any(_paths_overlap(asset_root, other) for other in asset_roots[index + 1 :]):
            raise RuntimeError("基础素材目录与补充素材目录必须彼此独立")
        if _paths_overlap(old_root, asset_root):
            raise RuntimeError("活动插件数据目录与新素材目录必须彼此独立")
    for index, history_root in enumerate(history_roots):
        if not history_root.is_dir():
            raise RuntimeError(f"历史数据源不存在：{history_root}")
        if not (history_root / "catalog.json").is_file():
            raise RuntimeError(f"历史数据源缺少 catalog.json：{history_root}")
        if _paths_overlap(old_root, history_root):
            raise RuntimeError("活动插件数据目录与历史数据源必须彼此独立")
        if any(_paths_overlap(history_root, root) for root in asset_roots):
            raise RuntimeError("历史数据源与新素材目录必须彼此独立")
        if any(
            _paths_overlap(history_root, other)
            for other in history_roots[index + 1 :]
        ):
            raise RuntimeError("多个历史数据源必须彼此独立且不能重复")
    protected_roots = [old_root, *asset_roots, *history_roots]
    if any(_paths_overlap(report_dir, root) for root in protected_roots):
        raise RuntimeError("报告目录必须独立于活动数据、历史数据和素材目录")
    assets, excluded_new_pages = load_new_assets(
        new_assets_root,
        release_order_path,
        supplemental_assets_roots,
    )
    overrides = load_overrides(overrides_path)
    signature_cache = SignatureCache(report_dir / "image_signatures.json")

    old_entries = load_old_entries(old_root)
    active_matches = match_old_entries(
        old_entries,
        assets,
        overrides,
        signature_cache,
        progress,
        source_data=str(old_root),
        source_role="active",
    )
    history_runs: List[Dict[str, Any]] = []
    for history_root in history_roots:
        history_entries = load_old_entries(history_root)
        history_matches = match_old_entries(
            history_entries,
            assets,
            overrides,
            signature_cache,
            progress,
            source_data=str(history_root),
            source_role="history",
        )
        history_runs.append(
            {
                "root": history_root,
                "entries": history_entries,
                "matches": history_matches,
            }
        )

    all_matches = [
        *active_matches,
        *[
            match
            for history_run in history_runs
            for match in history_run["matches"]
        ],
    ]
    catalog_items = build_catalog(assets, all_matches)
    catalog_ids = {item["filename"]: int(item["id"]) for item in catalog_items}
    (
        active_groups,
        active_copied,
        active_unresolved_rows,
        active_user_rows,
        active_referenced_filenames,
    ) = migrate_groups(old_root, active_matches, catalog_ids, source_role="active")

    history_group_sets: List[Tuple[Path, Mapping[str, Mapping[str, Any]]]] = []
    for history_run in history_runs:
        (
            history_run["groups"],
            history_run["copied"],
            history_run["unresolved_rows"],
            history_run["user_rows"],
            history_run["referenced_filenames"],
        ) = migrate_groups(
            history_run["root"],
            history_run["matches"],
            catalog_ids,
            source_role="history",
        )
        history_group_sets.append(
            (history_run["root"], history_run["groups"])
        )

    (
        baseline_groups,
        baseline_history_root,
        baseline_reconciliation,
    ) = load_active_migration_baseline(
        old_root,
        history_roots,
        old_entries,
        active_matches,
        catalog_ids,
    )
    (
        groups,
        history_merge_rows,
        merge_source_stats,
        merge_reconciliation,
    ) = merge_history_groups(
        active_groups,
        history_group_sets,
        catalog_ids,
        baseline_groups=baseline_groups,
        baseline_history_root=baseline_history_root,
    )
    baseline_reconciliation = {
        **baseline_reconciliation,
        **merge_reconciliation,
    }
    copied = copy.deepcopy(active_copied)
    for history_run in history_runs:
        for filename, value in history_run["copied"].items():
            copied.setdefault(filename, copy.deepcopy(value))

    unresolved_rows = [
        *active_unresolved_rows,
        *[
            row
            for history_run in history_runs
            for row in history_run["unresolved_rows"]
        ],
    ]
    user_rows = [
        *active_user_rows,
        *[
            row
            for history_run in history_runs
            for row in history_run["user_rows"]
        ],
    ]

    history_summaries: List[Dict[str, Any]] = []
    for history_run, merge_stats in zip(history_runs, merge_source_stats):
        history_user_rows = history_run["user_rows"]
        history_matches = history_run["matches"]
        history_old_unlock_rows = _sum_user_rows(
            history_user_rows, "old_unlock_rows"
        )
        history_mapped_unlock_rows = _sum_user_rows(
            history_user_rows, "mapped_unlock_rows"
        )
        history_old_current_rows = sum(
            bool(row["old_current"]) for row in history_user_rows
        )
        history_mapped_current_rows = sum(
            bool(row["old_current"]) and bool(row["new_current"])
            for row in history_user_rows
        )
        history_summary = {
            **merge_stats,
            "old_catalog_entries": len(history_run["entries"]),
            "old_asset_files": sum(
                entry.path is not None for entry in history_run["entries"]
            ),
            "matched_old_entries": sum(
                match.matched for match in history_matches
            ),
            "unmatched_old_entries": sum(
                not match.matched for match in history_matches
            ),
            "groups": len(history_run["groups"]),
            "users": len(history_user_rows),
            "old_unlock_rows": history_old_unlock_rows,
            "mapped_unlock_rows": history_mapped_unlock_rows,
            "unresolved_unlock_rows": _sum_user_rows(
                history_user_rows, "unresolved_unlock_rows"
            ),
            "old_current_rows": history_old_current_rows,
            "mapped_current_rows": history_mapped_current_rows,
            "unresolved_current_rows": sum(
                bool(row["old_current"]) and not bool(row["new_current"])
                for row in history_user_rows
            ),
        }
        history_summary["unlock_row_recovery_rate"] = (
            round(
                history_mapped_unlock_rows / history_old_unlock_rows * 100,
                2,
            )
            if history_old_unlock_rows
            else 100.0
        )
        history_summary["current_row_recovery_rate"] = (
            round(
                history_mapped_current_rows / history_old_current_rows * 100,
                2,
            )
            if history_old_current_rows
            else 100.0
        )
        history_summaries.append(history_summary)

    referenced_unresolved = {row["old_filename"] for row in unresolved_rows}
    active_old_unlock_rows = _sum_user_rows(active_user_rows, "old_unlock_rows")
    active_mapped_unlock_rows = _sum_user_rows(
        active_user_rows, "mapped_unlock_rows"
    )
    active_old_current_rows = sum(bool(row["old_current"]) for row in active_user_rows)
    active_mapped_current_rows = sum(
        bool(row["old_current"]) and bool(row["new_current"])
        for row in active_user_rows
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_data_root": str(old_root),
        "history_source_count": len(history_roots),
        "history_sources": history_summaries,
        "baseline_reconciliation": baseline_reconciliation,
        "old_catalog_entries": len(old_entries),
        "old_asset_files": sum(entry.path is not None for entry in old_entries),
        "source_manifest_entries": len(assets) + len(excluded_new_pages),
        "base_catalog_entries": sum(asset.asset_set == "base" for asset in assets),
        "supplemental_catalog_entries": sum(
            asset.asset_set != "base" for asset in assets
        ),
        "excluded_non_character_pages": len(excluded_new_pages),
        "new_catalog_entries": len(catalog_items),
        "matched_old_entries": sum(match.matched for match in active_matches),
        "unmatched_old_entries": sum(not match.matched for match in active_matches),
        "matched_old_entries_referenced_by_users": sum(
            match.matched and match.old_filename in active_referenced_filenames
            for match in active_matches
        ),
        "referenced_old_filenames": len(active_referenced_filenames),
        "unresolved_reference_rows": len(unresolved_rows),
        "unresolved_reference_filenames": len(referenced_unresolved),
        "groups": len(groups),
        "users": len(history_merge_rows),
        "active_groups": len(active_groups),
        "active_users": len(active_user_rows),
        "old_unlock_rows": active_old_unlock_rows,
        "mapped_unlock_rows": active_mapped_unlock_rows,
        "generated_unlock_targets": sum(
            int(row["generated_unlock_targets"]) for row in active_user_rows
        ),
        "expanded_unlock_targets": sum(
            int(row["expanded_unlock_targets"]) for row in active_user_rows
        ),
        "new_unique_unlocks": _sum_user_rows(
            active_user_rows, "new_unique_unlocks"
        ),
        "history_unlocks_added": sum(
            int(row["history_unlocks_added"]) for row in history_merge_rows
        ),
        "history_unlock_dates_recovered": sum(
            int(row["history_unlock_dates_recovered"])
            for row in history_merge_rows
        ),
        "final_unique_unlocks": sum(
            int(row["final_unique_unlocks"]) for row in history_merge_rows
        ),
        "merged_unlock_rows": _sum_user_rows(
            active_user_rows, "merged_unlock_rows"
        ),
        "deduplicated_unlock_targets": sum(
            int(row["deduplicated_unlock_targets"]) for row in active_user_rows
        ),
        "unresolved_unlock_rows": sum(
            int(row["unresolved_unlock_rows"]) for row in user_rows
        ),
        "old_current_rows": active_old_current_rows,
        "mapped_current_rows": active_mapped_current_rows,
        "unresolved_current_rows": sum(
            bool(row["old_current"]) and not bool(row["new_current"])
            for row in active_user_rows
        ),
        "match_methods": {
            method: sum(match.method == method for match in active_matches)
            for method in sorted({match.method for match in active_matches})
        },
        "all_match_methods": {
            method: sum(match.method == method for match in all_matches)
            for method in sorted({match.method for match in all_matches})
        },
        "undated_new_entries": sum(
            not item.get("debut_year") for item in catalog_items
        ),
    }
    summary["old_entry_match_rate"] = (
        round(summary["matched_old_entries"] / summary["old_catalog_entries"] * 100, 2)
        if summary["old_catalog_entries"]
        else 100.0
    )
    summary["unlock_row_recovery_rate"] = (
        round(summary["mapped_unlock_rows"] / summary["old_unlock_rows"] * 100, 2)
        if summary["old_unlock_rows"]
        else 100.0
    )
    summary["current_row_recovery_rate"] = (
        round(summary["mapped_current_rows"] / summary["old_current_rows"] * 100, 2)
        if summary["old_current_rows"]
        else 100.0
    )
    return MigrationPlan(
        old_root=old_root,
        history_roots=list(history_roots),
        new_assets_root=new_assets_root,
        supplemental_assets_roots=list(supplemental_assets_roots),
        assets=list(assets),
        report_dir=report_dir,
        catalog_items=catalog_items,
        excluded_new_pages=excluded_new_pages,
        migrated_groups=groups,
        copied_config_files=copied,
        matches=all_matches,
        unresolved_references=unresolved_rows,
        user_rows=user_rows,
        history_merge_rows=history_merge_rows,
        history_summaries=history_summaries,
        baseline_reconciliation=baseline_reconciliation,
        summary=summary,
    )


def write_reports(plan: MigrationPlan) -> None:
    report_dir = plan.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(report_dir / "migration_summary.json", plan.summary)
    _atomic_write_json(
        report_dir / "migration_plan.json",
        {
            "summary": plan.summary,
            "matches": [asdict(match) for match in plan.matches],
            "unresolved_references": plan.unresolved_references,
            "users": plan.user_rows,
            "history_merge": plan.history_merge_rows,
            "baseline_reconciliation": plan.baseline_reconciliation,
            "excluded_new_pages": plan.excluded_new_pages,
            "catalog": {"version": 2, "items": plan.catalog_items},
        },
    )
    match_rows = []
    for match in plan.matches:
        row = asdict(match)
        row["additional_targets"] = json.dumps(
            match.additional_targets, ensure_ascii=False
        )
        row["candidates"] = json.dumps(match.candidates, ensure_ascii=False)
        match_rows.append(row)
    _write_csv(
        report_dir / "旧素材匹配报告.csv",
        match_rows,
        (
            "source_role",
            "source_data",
            "old_id",
            "old_filename",
            "old_name",
            "old_source",
            "old_file_exists",
            "old_entry_key",
            "old_variant_key",
            "old_page_title",
            "new_id",
            "new_filename",
            "new_name",
            "page_title",
            "method",
            "confidence",
            "note",
            "additional_targets",
            "candidates",
        ),
    )
    _write_csv(
        report_dir / "用户迁移影响.csv",
        plan.user_rows,
        (
            "source_role",
            "source_data",
            "group_id",
            "user_id",
            "nickname",
            "old_unlock_rows",
            "mapped_unlock_rows",
            "generated_unlock_targets",
            "expanded_unlock_targets",
            "new_unique_unlocks",
            "merged_unlock_rows",
            "deduplicated_unlock_targets",
            "unresolved_unlock_rows",
            "old_current",
            "new_current",
            "current_mapped",
        ),
    )
    _write_csv(
        report_dir / "历史数据合并.csv",
        plan.history_merge_rows,
        (
            "group_id",
            "user_id",
            "nickname",
            "active_unique_unlocks",
            "active_baseline_unlocks_removed",
            "active_post_migration_unlocks_preserved",
            "history_unlocks_added",
            "history_unlock_dates_recovered",
            "final_unique_unlocks",
            "net_unlock_change",
            "contributing_history_sources",
        ),
    )
    _write_csv(
        report_dir / "漏迁用户记录.csv",
        plan.unresolved_references,
        (
            "source_role",
            "source_data",
            "group_id",
            "user_id",
            "nickname",
            "location",
            "old_filename",
            "unlock_date",
        ),
    )
    _write_csv(
        report_dir / "已排除非角色页.csv",
        plan.excluded_new_pages,
        ("page_title", "filename", "reason"),
    )
    order_rows = [
        {
            "id": item["id"],
            "debut_year": item.get("debut_year") or "待确认",
            "source": item["source"],
            "name": item["name"],
            "page_title": item.get("page_title", ""),
            "entry_key": item.get("entry_key", ""),
            "variant_key": item.get("variant_key", ""),
            "catalog_kind": item.get("catalog_kind", ""),
            "asset_set": item.get("asset_set", ""),
            "kind": item.get("kind", ""),
            "filename": item["filename"],
        }
        for item in plan.catalog_items
    ]
    _write_csv(
        report_dir / "新图鉴编号.csv",
        order_rows,
        (
            "id",
            "debut_year",
            "source",
            "name",
            "page_title",
            "entry_key",
            "variant_key",
            "catalog_kind",
            "asset_set",
            "kind",
            "filename",
        ),
    )
    _write_review_html(plan)


def _write_review_html(plan: MigrationPlan) -> None:
    rows: List[str] = []
    new_asset_paths = {asset.filename: asset.path for asset in plan.assets}
    for match in plan.matches:
        if match.matched and match.confidence >= 0.95 and not match.additional_targets:
            continue
        source_root = Path(match.source_data) if match.source_data else plan.old_root
        old_assets = source_root / "img" / "allies"
        old_path = old_assets / match.old_filename
        old_image = (
            f'<img src="{html.escape(old_path.resolve().as_uri())}" alt="旧素材">'
            if old_path.is_file()
            else "<span>旧文件缺失</span>"
        )
        candidates = match.candidates[:5]
        candidate_cells: List[str] = []
        if match.matched:
            selected = [
                {
                    "filename": match.new_filename,
                    "id": match.new_id,
                    "name": match.new_name,
                    "page_title": match.page_title,
                },
                *match.additional_targets,
            ]
            selected_filenames = {item.get("filename") for item in selected}
            candidates = [
                *selected,
                *[
                    item
                    for item in candidates
                    if item.get("filename") not in selected_filenames
                ],
            ]
        for candidate in candidates:
            candidate_filename = str(candidate.get("filename", ""))
            path = new_asset_paths.get(
                candidate_filename, plan.new_assets_root / candidate_filename
            )
            metrics = []
            if "phash_distance" in candidate:
                metrics.append(f"pHash {candidate['phash_distance']}")
            if candidate.get("name_score"):
                metrics.append(f"name {candidate['name_score']}")
            candidate_cells.append(
                '<div class="candidate">'
                f'<img src="{html.escape(path.resolve().as_uri())}" alt="候选">'
                f"<b>#{candidate.get('id', '')} {html.escape(str(candidate.get('name', '')))}</b>"
                f"<span>{html.escape(str(candidate.get('page_title', '')))}</span>"
                f"<small>{html.escape(' / '.join(metrics))}</small>"
                "</div>"
            )
        rows.append(
            "<section>"
            f"<header><b>旧 #{match.old_id} {html.escape(match.old_name)}</b>"
            f"<span>{html.escape(match.source_role)}：{html.escape(str(source_root))}</span>"
            f"<span>{html.escape(match.old_filename)}</span>"
            f"<em>{html.escape(match.method)} / {match.confidence:.2f} / {html.escape(match.note)}</em></header>"
            f'<div class="old">{old_image}</div>'
            f'<div class="candidates">{"".join(candidate_cells) or "<span>没有候选</span>"}</div>'
            "</section>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>星之卡比图鉴迁移复核</title>
<style>
body{{font:14px/1.45 system-ui,"Microsoft YaHei",sans-serif;margin:24px;background:#f4f6f8;color:#17202a}}
h1{{margin:0 0 8px}}p{{margin:0 0 20px;color:#566573}}section{{display:grid;grid-template-columns:220px 180px 1fr;gap:16px;padding:16px 0;border-top:1px solid #ccd4da}}
header{{display:flex;flex-direction:column;gap:5px;overflow-wrap:anywhere}}header em{{font-style:normal;color:#9c3d10}}.old img,.candidate img{{width:160px;height:160px;object-fit:contain;background:white;border:1px solid #d5d8dc}}
.candidates{{display:flex;gap:12px;overflow-x:auto}}.candidate{{width:170px;flex:0 0 170px;display:flex;flex-direction:column;gap:4px}}.candidate span,.candidate small{{overflow-wrap:anywhere;color:#566573}}
</style></head><body><h1>星之卡比图鉴迁移复核</h1>
<p>仅列出未匹配、低于 0.95 或需要合并确认的条目。自动报告不会修改旧数据。</p>{"".join(rows)}</body></html>"""
    (plan.report_dir / "迁移复核.html").write_text(document, encoding="utf-8")


def validate_plan(plan: MigrationPlan) -> None:
    ids = [int(item["id"]) for item in plan.catalog_items]
    filenames = [str(item["filename"]) for item in plan.catalog_items]
    entry_keys = [str(item.get("entry_key", "")) for item in plan.catalog_items]
    if ids != list(range(1, len(ids) + 1)):
        raise RuntimeError("新 catalog 编号不是从 1 开始的连续编号")
    if len(filenames) != len(set(name.casefold() for name in filenames)):
        raise RuntimeError("新 catalog 含重复文件名")
    if any(not value for value in entry_keys) or len(entry_keys) != len(
        set(value.casefold() for value in entry_keys)
    ):
        raise RuntimeError("新 catalog 含空白或重复 entry_key")
    valid = set(filenames)
    for group_name, users in plan.migrated_groups.items():
        for user_id, user in users.items():
            current = _old_reference(user.get("current", {}))
            if current and current not in valid:
                raise RuntimeError(
                    f"迁移后 current 引用不存在：{group_name}/{user_id}/{current}"
                )
            unlocked = [_old_reference(item) for item in user.get("unlocked", [])]
            if any(filename not in valid for filename in unlocked if filename):
                raise RuntimeError(
                    f"迁移后 unlocked 引用不存在：{group_name}/{user_id}"
                )
            if len(unlocked) != len(set(unlocked)):
                raise RuntimeError(f"迁移后 unlocked 仍有重复：{group_name}/{user_id}")


def _copy_new_assets(assets: Sequence[NewAsset], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    ordered = sorted(assets, key=lambda asset: asset.filename.casefold())
    for index, asset in enumerate(ordered, 1):
        filename = asset.filename
        source_path = asset.path
        if not source_path.is_file():
            raise RuntimeError(f"复制前新素材消失：{source_path}")
        shutil.copy2(source_path, target / filename)
        if index % 100 == 0:
            print(f"[copy] {index}/{len(ordered)}", flush=True)


def apply_plan(plan: MigrationPlan, confirmation: str) -> Path:
    if confirmation != "REPLACE_OLD_KIRBY_DATA":
        raise RuntimeError("正式迁移必须传入确认文本 REPLACE_OLD_KIRBY_DATA")
    validate_plan(plan)
    write_reports(plan)
    old_root = plan.old_root
    parent = old_root.parent
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup = parent / f"{old_root.name}.before-v3-{timestamp}"
    stage = parent / f".{old_root.name}.v3-stage-{timestamp}"
    if backup.exists() or stage.exists():
        raise RuntimeError("迁移备份或暂存目录已存在，请稍后重试")
    stage.mkdir(parents=True)
    try:
        for source in old_root.iterdir():
            if source.name in {
                "catalog.json",
                "config",
                "gallery",
                "img",
                "migration_reports",
            }:
                continue
            destination = stage / source.name
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        (stage / "gallery").mkdir()
        (stage / "config").mkdir()
        (stage / "img").mkdir()
        _copy_new_assets(plan.assets, stage / "img" / "allies")
        _atomic_write_json(
            stage / "catalog.json", {"version": 2, "items": plan.catalog_items}
        )
        for filename, users in plan.migrated_groups.items():
            _atomic_write_json(stage / "config" / filename, users)
        for filename, value in plan.copied_config_files.items():
            _atomic_write_json(stage / "config" / filename, value)
        shutil.copytree(plan.report_dir, stage / "migration_reports" / timestamp)
        _atomic_write_json(
            stage / "migration_state.json",
            {
                "version": 1,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "backup_path": str(backup),
                "summary": plan.summary,
            },
        )
        staged_images = {
            path.name
            for path in (stage / "img" / "allies").iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        }
        expected_images = {str(item["filename"]) for item in plan.catalog_items}
        if staged_images != expected_images:
            raise RuntimeError("暂存目录素材集合与新 catalog 不一致")
        os.replace(old_root, backup)
        try:
            os.replace(stage, old_root)
        except BaseException:
            os.replace(backup, old_root)
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return backup
