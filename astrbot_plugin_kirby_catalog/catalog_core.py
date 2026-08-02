from __future__ import annotations

import base64
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
SHANGHAI = timezone(timedelta(hours=8))


def get_today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=path.suffix or ".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u3400-\u9fff_-]+", "_", value.strip())
    return value.strip("._")[:80] or "ally"


def _parse_filename(filename: str) -> Tuple[str, str]:
    """Infer the old source/name convention without making it mandatory."""
    parts = Path(filename).name.split(".")
    if len(parts) >= 3:
        return parts[1] or Path(filename).stem, parts[0]
    stem = Path(filename).stem
    match = re.match(r"ally_\d+_(.+)$", stem)
    return (match.group(1) if match else stem), ""


def _normalise_date(value: Any) -> str:
    return _as_text(value) or get_today()


def _normalise_unlocked(items: Any) -> List[Dict[str, str]]:
    if not isinstance(items, list):
        return []
    result: List[Dict[str, str]] = []
    seen: Dict[str, Dict[str, str]] = {}
    for item in items:
        if isinstance(item, str):
            filename = item.strip()
            unlock_date = get_today()
        elif isinstance(item, dict):
            filename = _as_text(
                item.get("ally_filename")
                or item.get("wife_name")
                or item.get("filename")
                or item.get("name")
            )
            unlock_date = _normalise_date(item.get("unlock_date") or item.get("date"))
        else:
            continue
        if not filename:
            continue
        record = {"ally_filename": Path(filename).name, "unlock_date": unlock_date}
        previous = seen.get(record["ally_filename"])
        if previous is None or record["unlock_date"] < previous["unlock_date"]:
            seen[record["ally_filename"]] = record
    result.extend(seen.values())
    return result


def normalise_group_config(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Read both the old wife-shaped format and the new ally-shaped format."""
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for user_id, value in raw.items():
        if not isinstance(value, (dict, list)):
            continue
        user_id = str(user_id)
        if isinstance(value, list):
            current_filename = _as_text(value[0]) if value else ""
            current_date = _normalise_date(value[1]) if len(value) > 1 else ""
            nickname = _as_text(value[2], "用户") if len(value) > 2 else "用户"
            unlocked = (
                [{"ally_filename": current_filename, "unlock_date": current_date}]
                if current_filename
                else []
            )
            no_new_count = 0
        else:
            current = value.get("current") or {}
            if isinstance(current, str):
                current = {"ally_filename": current, "date": ""}
            current_filename = _as_text(
                current.get("ally_filename")
                or current.get("wife_name")
                or current.get("filename")
            )
            current_date = _as_text(current.get("date"))
            nickname = _as_text(value.get("nickname"), "用户")
            unlocked = _normalise_unlocked(value.get("unlocked"))
            try:
                no_new_count = max(0, int(value.get("no_new_count", 0)))
            except (TypeError, ValueError):
                no_new_count = 0

        result[user_id] = {
            "current": {
                "ally_filename": Path(current_filename).name
                if current_filename
                else "",
                "date": current_date,
            },
            "unlocked": unlocked,
            "nickname": nickname,
            "no_new_count": no_new_count,
        }
    return result


def _merge_group_configs(
    current: Dict[str, Dict[str, Any]], legacy: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    merged = normalise_group_config(current)
    for user_id, old_user in normalise_group_config(legacy).items():
        if user_id not in merged:
            merged[user_id] = old_user
            continue
        new_user = merged[user_id]
        if not new_user.get("current", {}).get("ally_filename"):
            new_user["current"] = old_user.get("current", {})
        if not new_user.get("nickname") or new_user["nickname"] == "用户":
            new_user["nickname"] = old_user.get("nickname", "用户")
        by_filename = {
            item["ally_filename"]: item
            for item in _normalise_unlocked(new_user.get("unlocked"))
        }
        for item in _normalise_unlocked(old_user.get("unlocked")):
            previous = by_filename.get(item["ally_filename"])
            if previous is None or item["unlock_date"] < previous["unlock_date"]:
                by_filename[item["ally_filename"]] = item
        new_user["unlocked"] = list(by_filename.values())
        new_user["no_new_count"] = max(
            int(new_user.get("no_new_count", 0) or 0),
            int(old_user.get("no_new_count", 0) or 0),
        )
    return merged


class CatalogStore:
    """Persistent catalogue, legacy migration, media storage and gallery rendering."""

    def __init__(
        self,
        data_dir: Path,
        legacy_dirs: Sequence[Path] = (),
        image_base_url: str = "",
    ) -> None:
        self.root = Path(data_dir)
        self.config_dir = self.root / "config"
        self.assets_dir = self.root / "img" / "allies"
        self.gallery_dir = self.root / "gallery"
        self.catalog_path = self.root / "catalog.json"
        self.draw_limits_path = self.config_dir / "draw_limits.json"
        self.legacy_dirs = [Path(path) for path in legacy_dirs]
        self.image_base_url = image_base_url.strip()
        self._catalog: Dict[str, Dict[str, Any]] = {}
        self._draw_limits: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._prepare()

    def _prepare(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self._load_catalog()
        self._migrate_legacy_data()
        self._load_draw_limits()
        self._refresh_catalog()

    @property
    def catalog_path_value(self) -> Path:
        return self.catalog_path

    def _load_catalog(self) -> None:
        raw = _read_json(self.catalog_path, {})
        items: Iterable[Any]
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            items = raw["items"]
        elif isinstance(raw, dict):
            items = [
                dict(value, filename=key)
                if isinstance(value, dict)
                else {"filename": key, "id": value}
                for key, value in raw.items()
            ]
        else:
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            filename = Path(_as_text(item.get("filename"))).name
            if not filename:
                continue
            self._set_entry(
                filename,
                item.get("id"),
                _as_text(item.get("name")),
                _as_text(item.get("source")),
                item.get("aliases", []),
            )

    def _set_entry(
        self,
        filename: str,
        entry_id: Any = None,
        name: str = "",
        source: str = "",
        aliases: Any = None,
    ) -> Dict[str, Any]:
        filename = Path(filename).name
        inferred_name, inferred_source = _parse_filename(filename)
        try:
            numeric_id = int(entry_id)
        except (TypeError, ValueError):
            numeric_id = 0
        if numeric_id <= 0:
            numeric_id = int(self._catalog.get(filename, {}).get("id", 0) or 0)
            if numeric_id <= 0:
                numeric_id = self._next_id()
        entry = self._catalog.get(filename, {})
        alias_values = entry.get("aliases", []) if aliases is None else aliases
        entry.update(
            {
                "id": numeric_id,
                "filename": filename,
                "name": name or entry.get("name") or inferred_name,
                "source": source or entry.get("source") or inferred_source,
                "aliases": sorted(
                    {
                        _as_text(alias)
                        for alias in (alias_values or [])
                        if _as_text(alias)
                    }
                ),
            }
        )
        self._catalog[filename] = entry
        return entry

    def _next_id(self) -> int:
        ids = [int(item.get("id", 0)) for item in self._catalog.values()]
        return max(ids, default=0) + 1

    def _save_catalog(self) -> None:
        items = sorted(self._catalog.values(), key=lambda item: int(item["id"]))
        _atomic_write_json(self.catalog_path, {"version": 1, "items": items})

    def _legacy_config_dirs(self) -> List[Path]:
        return [
            path / "config" for path in self.legacy_dirs if (path / "config").is_dir()
        ]

    def _migrate_legacy_data(self) -> None:
        with self._lock:
            self._migrate_legacy_assets()
            for legacy_config_dir in self._legacy_config_dirs():
                for old_file in legacy_config_dir.glob("*.json"):
                    if old_file.name in {
                        "wife_draw_limit.json",
                        "ntr_limit.json",
                        "ntr_status.json",
                    }:
                        continue
                    group_id = old_file.stem
                    if not group_id or not re.match(r"^[A-Za-z0-9_-]+$", group_id):
                        continue
                    old_config = normalise_group_config(_read_json(old_file, {}))
                    if not old_config:
                        continue
                    target_file = self.config_dir / f"{group_id}.json"
                    current_config = normalise_group_config(_read_json(target_file, {}))
                    merged = _merge_group_configs(current_config, old_config)
                    _atomic_write_json(target_file, merged)

                old_limits = legacy_config_dir / "wife_draw_limit.json"
                if old_limits.exists() and not self.draw_limits_path.exists():
                    limits = _read_json(old_limits, {})
                    if isinstance(limits, dict):
                        _atomic_write_json(self.draw_limits_path, limits)

            for legacy_dir in self.legacy_dirs:
                old_index = legacy_dir / "wife_index.json"
                raw_index = _read_json(old_index, {})
                if isinstance(raw_index, dict):
                    for filename, entry_id in raw_index.items():
                        self._set_entry(filename, entry_id)
            self._save_catalog()

    def _migrate_legacy_assets(self) -> None:
        """Copy legacy local assets so the new plugin is self-contained."""
        for legacy_dir in self.legacy_dirs:
            legacy_assets_dir = legacy_dir / "img" / "wife"
            if not legacy_assets_dir.is_dir():
                continue
            for source in legacy_assets_dir.iterdir():
                if (
                    not source.is_file()
                    or source.suffix.lower() not in IMAGE_EXTENSIONS
                ):
                    continue
                target = self.assets_dir / source.name
                if target.exists():
                    continue
                try:
                    shutil.copy2(source, target)
                except OSError:
                    continue

    def _load_draw_limits(self) -> None:
        raw = _read_json(self.draw_limits_path, {})
        self._draw_limits = raw if isinstance(raw, dict) else {}

    def _save_draw_limits(self) -> None:
        _atomic_write_json(self.draw_limits_path, self._draw_limits)

    def _refresh_catalog(self) -> None:
        with self._lock:
            for asset_dir in [
                self.assets_dir,
                *[legacy / "img" / "wife" for legacy in self.legacy_dirs],
            ]:
                if not asset_dir.is_dir():
                    continue
                for path in asset_dir.iterdir():
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                        self._set_entry(path.name)

            for group_file in self.config_dir.glob("*.json"):
                if group_file.name in {"draw_limits.json"}:
                    continue
                group = normalise_group_config(_read_json(group_file, {}))
                for user in group.values():
                    filenames = [
                        user.get("current", {}).get("ally_filename", ""),
                        *[
                            item.get("ally_filename", "")
                            for item in user.get("unlocked", [])
                        ],
                    ]
                    for filename in filenames:
                        if filename:
                            self._set_entry(filename)
            self._save_catalog()

    def refresh(self) -> None:
        self._refresh_catalog()

    def migrate_legacy(self) -> None:
        """Run the idempotent legacy scan again after an administrator requests it."""
        self._migrate_legacy_data()
        self._load_draw_limits()

    def entries(self) -> List[Dict[str, Any]]:
        return sorted(
            (dict(item) for item in self._catalog.values()),
            key=lambda item: int(item.get("id", 0)),
        )

    def find_entries(self, target: str) -> List[Dict[str, Any]]:
        target = _as_text(target)
        if not target:
            return []
        try:
            numeric_id = int(target.lstrip("#"))
        except ValueError:
            numeric_id = 0
        if numeric_id:
            return [
                dict(item)
                for item in self._catalog.values()
                if int(item.get("id", 0)) == numeric_id
            ]
        folded = target.casefold()
        exact: List[Dict[str, Any]] = []
        partial: List[Dict[str, Any]] = []
        for item in self._catalog.values():
            candidates = {
                _as_text(item.get("filename")).casefold(),
                _as_text(item.get("name")).casefold(),
                *[_as_text(alias).casefold() for alias in item.get("aliases", [])],
            }
            if folded in candidates:
                exact.append(dict(item))
            elif any(folded in candidate for candidate in candidates if candidate):
                partial.append(dict(item))
        return sorted(exact or partial, key=lambda item: int(item["id"]))

    def resolve_entry(self, target: str) -> Optional[Dict[str, Any]]:
        matches = self.find_entries(target)
        return matches[0] if len(matches) == 1 else None

    def get_draw_pool(self) -> List[Dict[str, Any]]:
        return [
            entry
            for entry in self.entries()
            if self.asset_path(entry) is not None or self.image_base_url
        ]

    def asset_path(self, entry: Dict[str, Any]) -> Optional[Path]:
        filename = Path(_as_text(entry.get("filename"))).name
        if not filename:
            return None
        new_path = self.assets_dir / filename
        if new_path.is_file():
            return new_path
        for legacy in self.legacy_dirs:
            legacy_path = legacy / "img" / "wife" / filename
            if legacy_path.is_file():
                return legacy_path
        return None

    def asset_bytes(
        self, entry: Dict[str, Any], download: bool = False
    ) -> Optional[bytes]:
        path = self.asset_path(entry)
        if path is not None:
            try:
                return path.read_bytes()
            except OSError:
                return None
        if not download or not self.image_base_url:
            return None
        filename = quote(Path(_as_text(entry.get("filename"))).name)
        url = f"{self.image_base_url}{filename}"
        try:
            request = Request(url, headers={"User-Agent": "KirbyCatalog/1.0"})
            with urlopen(request, timeout=15) as response:
                data = response.read()
            self._validate_image(data)
            cache_path = self.assets_dir / Path(_as_text(entry.get("filename"))).name
            _atomic_write_bytes(cache_path, data)
            return data
        except Exception:
            return None

    @staticmethod
    def _validate_image(data: bytes) -> None:
        with Image.open(BytesIO(data)) as image:
            image.verify()

    def load_group(self, group_id: str) -> Dict[str, Dict[str, Any]]:
        path = self.config_dir / f"{Path(str(group_id)).name}.json"
        return normalise_group_config(_read_json(path, {}))

    def save_group(self, group_id: str, config: Dict[str, Dict[str, Any]]) -> None:
        path = self.config_dir / f"{Path(str(group_id)).name}.json"
        _atomic_write_json(path, normalise_group_config(config))

    def draw_count(
        self, group_id: str, user_id: str, today: Optional[str] = None
    ) -> int:
        today = today or get_today()
        return int(
            self._draw_limits.get(str(group_id), {}).get(str(user_id), {}).get(today, 0)
            or 0
        )

    def increment_draw(
        self, group_id: str, user_id: str, today: Optional[str] = None
    ) -> int:
        with self._lock:
            today = today or get_today()
            group = self._draw_limits.setdefault(str(group_id), {})
            user = group.setdefault(str(user_id), {})
            user[today] = self.draw_count(group_id, user_id, today) + 1
            self._save_draw_limits()
            return int(user[today])

    def rename_entry(
        self,
        entry: Dict[str, Any],
        new_name: str,
        new_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            old_filename = Path(_as_text(entry["filename"])).name
            old_path = self.asset_path(entry)
            suffix = old_path.suffix if old_path else ".png"
            new_filename = (
                f"ally_{int(entry['id']):04d}_{_safe_filename(new_name)}"
                f"{suffix.lower()}"
            )
            if new_filename != old_filename:
                new_path = self.assets_dir / new_filename
                if new_path.exists():
                    raise FileExistsError(f"素材文件已存在：{new_filename}")
                if old_path is not None:
                    if old_path.parent == self.assets_dir:
                        old_path.replace(new_path)
                    else:
                        shutil.copy2(old_path, new_path)
                self._replace_references(old_filename, new_filename)
                self._catalog.pop(old_filename, None)
                aliases = list(entry.get("aliases", [])) + [old_filename]
            else:
                aliases = list(entry.get("aliases", []))
            updated = dict(entry)
            updated.update(
                {
                    "filename": new_filename,
                    "name": _as_text(new_name) or entry.get("name", "未命名盟友"),
                    "source": _as_text(new_source) or entry.get("source", ""),
                    "aliases": sorted(set(aliases)),
                }
            )
            self._catalog[new_filename] = updated
            self._save_catalog()
            return dict(updated)

    def _replace_references(self, old_filename: str, new_filename: str) -> None:
        for group_file in self.config_dir.glob("*.json"):
            if group_file.name == self.draw_limits_path.name:
                continue
            config = normalise_group_config(_read_json(group_file, {}))
            changed = False
            for user in config.values():
                current = user.get("current", {})
                if current.get("ally_filename") == old_filename:
                    current["ally_filename"] = new_filename
                    changed = True
                for item in user.get("unlocked", []):
                    if item.get("ally_filename") == old_filename:
                        item["ally_filename"] = new_filename
                        changed = True
            if changed:
                _atomic_write_json(group_file, config)

    def replace_asset(self, entry: Dict[str, Any], data: bytes) -> Dict[str, Any]:
        self._validate_image(data)
        with self._lock:
            filename = Path(_as_text(entry["filename"])).name
            target = self.assets_dir / filename
            _atomic_write_bytes(target, data)
            return dict(self._catalog.get(filename, entry))

    def add_asset(
        self,
        name: str,
        data: bytes,
        source: str = "",
    ) -> Dict[str, Any]:
        self._validate_image(data)
        with self._lock:
            entry_id = self._next_id()
            image_format = "png"
            with Image.open(BytesIO(data)) as image:
                image_format = (image.format or "PNG").lower()
            extension = (
                ".jpg" if image_format in {"jpeg", "jpg"} else f".{image_format}"
            )
            filename = f"ally_{entry_id:04d}_{_safe_filename(name)}{extension}"
            _atomic_write_bytes(self.assets_dir / filename, data)
            entry = self._set_entry(filename, entry_id, name, source)
            self._save_catalog()
            return dict(entry)

    def unlocked_filenames(self, user: Dict[str, Any]) -> List[str]:
        return [
            item["ally_filename"] for item in _normalise_unlocked(user.get("unlocked"))
        ]

    def unlock(
        self, user: Dict[str, Any], filename: str, unlock_date: Optional[str] = None
    ) -> bool:
        filename = Path(filename).name
        unlocked = _normalise_unlocked(user.get("unlocked"))
        if any(item["ally_filename"] == filename for item in unlocked):
            user["unlocked"] = unlocked
            return False
        unlocked.append(
            {"ally_filename": filename, "unlock_date": unlock_date or get_today()}
        )
        user["unlocked"] = unlocked
        return True

    def find_user_by_nickname(
        self, config: Dict[str, Dict[str, Any]], target: str
    ) -> List[str]:
        folded = _as_text(target).casefold()
        if not folded:
            return []
        return [
            user_id
            for user_id, user in config.items()
            if folded in _as_text(user.get("nickname"), "用户").casefold()
        ]

    def leaderboard(self, group_id: str, limit: int = 10) -> List[Tuple[str, str, int]]:
        config = self.load_group(group_id)
        rows = [
            (
                user_id,
                _as_text(user.get("nickname"), "用户"),
                len(set(self.unlocked_filenames(user))),
            )
            for user_id, user in config.items()
        ]
        rows.sort(key=lambda row: (-row[2], row[1].casefold(), row[0]))
        return rows[:limit]

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = [
            Path(
                "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
            ),
            Path(
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
                if bold
                else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
            ),
            Path(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ),
        ]
        for path in candidates:
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _fit_text(
        draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int
    ) -> str:
        text = text or "未命名盟友"
        if draw.textlength(text, font=font) <= width:
            return text
        suffix = "..."
        while text and draw.textlength(text + suffix, font=font) > width:
            text = text[:-1]
        return (text or "?") + suffix

    def _thumbnail(
        self, entry: Dict[str, Any], size: Tuple[int, int], unlocked: bool
    ) -> Image.Image:
        data = self.asset_bytes(entry, download=False)
        if not data:
            image = Image.new("RGB", size, (225, 225, 225))
            draw = ImageDraw.Draw(image)
            draw.text(
                (size[0] // 2 - 8, size[1] // 2 - 8),
                "?",
                fill=(100, 100, 100),
                font=self._font(22, True),
            )
            return image
        try:
            with Image.open(BytesIO(data)) as source:
                source.seek(0)
                image = source.convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            if not unlocked:
                background = ImageOps.grayscale(background).convert("RGB")
            background.thumbnail(size)
            canvas = Image.new("RGB", size, "white")
            canvas.paste(
                background,
                ((size[0] - background.width) // 2, (size[1] - background.height) // 2),
            )
            return canvas
        except Exception:
            return Image.new("RGB", size, (225, 225, 225))

    def render_gallery(
        self,
        output_path: Path,
        unlocked: Set[str],
        title: str,
        columns: int = 10,
        personal: bool = False,
    ) -> Path:
        entries = self.entries()
        if personal:
            entries = [entry for entry in entries if entry["filename"] in unlocked]
        if not entries:
            raise ValueError("图鉴中没有可显示的盟友")

        columns = max(4, min(12, int(columns)))
        cell_width, thumb_size, label_height = 142, (126, 112), 32
        cell_height = thumb_size[1] + label_height + 8
        header_height = 58
        rows = (len(entries) + columns - 1) // columns
        canvas = Image.new(
            "RGB",
            (columns * cell_width, header_height + rows * cell_height),
            (248, 249, 251),
        )
        draw = ImageDraw.Draw(canvas)
        title_font = self._font(22, True)
        label_font = self._font(13)
        draw.text((14, 10), title, fill=(28, 32, 38), font=title_font)
        for index, entry in enumerate(entries):
            row, col = divmod(index, columns)
            x = col * cell_width + 8
            y = header_height + row * cell_height + 4
            item_unlocked = personal or entry["filename"] in unlocked
            thumb = self._thumbnail(entry, thumb_size, item_unlocked)
            canvas.paste(thumb, (x, y))
            draw.rectangle(
                (x, y, x + thumb_size[0] - 1, y + thumb_size[1] - 1),
                outline=(185, 190, 198),
            )
            label = self._fit_text(
                draw,
                f"#{entry['id']} {entry.get('name') or '未命名盟友'}",
                label_font,
                cell_width - 16,
            )
            draw.text(
                (x, y + thumb_size[1] + 5), label, fill=(38, 42, 48), font=label_font
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG", optimize=True)
        return output_path


def extract_image_bytes_from_value(value: Any) -> Optional[bytes]:
    """Decode an image component or raw adapter image field."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, dict):
        value = (
            value.get("path")
            or value.get("file")
            or value.get("url")
            or value.get("data")
        )
    if not value:
        return None
    text = str(value)
    try:
        if text.startswith("base64://"):
            return base64.b64decode(text[9:])
        if text.startswith("data:image/") and "," in text:
            return base64.b64decode(text.split(",", 1)[1])
        path = Path(text.removeprefix("file://"))
        if path.is_file():
            return path.read_bytes()
        if text.startswith(("http://", "https://")):
            request = Request(text, headers={"User-Agent": "KirbyCatalog/1.0"})
            with urlopen(request, timeout=15) as response:
                return response.read()
    except Exception:
        return None
    return None


def plain_text_from_component(component: Any) -> str:
    if isinstance(component, dict):
        return _as_text(component.get("text") or component.get("message_str"))
    return _as_text(
        getattr(component, "text", "") or getattr(component, "message_str", "")
    )
