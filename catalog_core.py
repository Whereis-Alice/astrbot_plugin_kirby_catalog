from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
SHANGHAI = timezone(timedelta(hours=8))
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
DESCRIPTION_OVERRIDES_FILENAME = "description_overrides.json"
TERMINOLOGY_OVERRIDES_FILENAME = "terminology_overrides.json"
WEBUI_DATA_DIRNAME = "webui"
WEBUI_AUDIT_FILENAME = "audit.json"
WEBUI_TOMBSTONES_FILENAME = "catalog_tombstones.json"
NON_GROUP_CONFIG_FILENAMES = frozenset(
    {
        "draw_limits.json",
        "draw_bonuses.json",
        DESCRIPTION_OVERRIDES_FILENAME,
        TERMINOLOGY_OVERRIDES_FILENAME,
    }
)
_UNSET = object()


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


def _snapshot_file(path: Path) -> Optional[bytes]:
    return path.read_bytes() if path.is_file() else None


def _restore_file(path: Path, snapshot: Optional[bytes]) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    _atomic_write_bytes(path, snapshot)


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u3400-\u9fff_-]+", "_", value.strip())
    return value.strip("._")[:80] or "ally"


def _safe_data_id(value: Any, label: str) -> str:
    candidate = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
        raise ValueError(f"{label}格式无效")
    return candidate


def _parse_filename(filename: str) -> Tuple[str, str]:
    """Infer the old source/name convention without making it mandatory."""
    stem = Path(filename).stem
    if "." in stem:
        source, name = stem.split(".", 1)
        return name or stem, source
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

        normalised = {
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
        if isinstance(value, dict):
            for key, item_value in value.items():
                if key not in {"current", "unlocked", "nickname", "no_new_count"}:
                    normalised[key] = item_value
        result[user_id] = normalised
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
        profiles_path: Optional[Path] = None,
    ) -> None:
        self.root = Path(data_dir)
        self.config_dir = self.root / "config"
        self.assets_dir = self.root / "img" / "allies"
        self.gallery_dir = self.root / "gallery"
        self.webui_dir = self.root / WEBUI_DATA_DIRNAME
        self.trash_dir = self.webui_dir / "trash"
        self.audit_path = self.webui_dir / WEBUI_AUDIT_FILENAME
        self.tombstones_path = self.webui_dir / WEBUI_TOMBSTONES_FILENAME
        self.catalog_path = self.root / "catalog.json"
        self.draw_limits_path = self.config_dir / "draw_limits.json"
        self.draw_bonuses_path = self.config_dir / "draw_bonuses.json"
        self.description_overrides_path = (
            self.config_dir / DESCRIPTION_OVERRIDES_FILENAME
        )
        self.profiles_path = Path(profiles_path) if profiles_path else None
        self.legacy_dirs = [Path(path) for path in legacy_dirs]
        self.image_base_url = image_base_url.strip()
        self._catalog: Dict[str, Dict[str, Any]] = {}
        self._draw_limits: Dict[str, Any] = {}
        self._draw_bonuses: Dict[str, Any] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._description_overrides: Dict[str, Dict[str, Any]] = {}
        self._audit_entries: List[Dict[str, Any]] = []
        self._tombstones: Dict[str, Dict[str, Any]] = {}
        self._draw_pool_cache: Optional[Tuple[Dict[str, Any], ...]] = None
        self._lock = threading.RLock()
        self._prepare()

    def _prepare(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self._load_tombstones()
        self._load_audit_entries()
        self._load_catalog()
        self._migrate_legacy_data()
        self._load_draw_limits()
        self._load_draw_bonuses()
        self._load_profiles()
        self._load_description_overrides()
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
                item,
            )

    def _set_entry(
        self,
        filename: str,
        entry_id: Any = None,
        name: str = "",
        source: str = "",
        aliases: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
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
        if metadata:
            for key in CATALOG_METADATA_KEYS:
                if key in metadata:
                    entry[key] = metadata[key]
        self._catalog[filename] = entry
        return entry

    def _next_id(self) -> int:
        ids = [int(item.get("id", 0)) for item in self._catalog.values()]
        ids.extend(int(item.get("id", 0) or 0) for item in self._tombstones.values())
        return max(ids, default=0) + 1

    def _load_tombstones(self) -> None:
        raw = _read_json(self.tombstones_path, {})
        items = raw.get("items", {}) if isinstance(raw, dict) else {}
        if not isinstance(items, dict):
            items = {}
        self._tombstones = {
            _as_text(token): dict(value)
            for token, value in items.items()
            if _as_text(token) and isinstance(value, dict)
        }

    def _save_tombstones(self) -> None:
        _atomic_write_json(
            self.tombstones_path,
            {"version": 1, "items": self._tombstones},
        )

    def _load_audit_entries(self) -> None:
        raw = _read_json(self.audit_path, {})
        items = raw.get("items", []) if isinstance(raw, dict) else []
        self._audit_entries = [dict(item) for item in items if isinstance(item, dict)][
            -1000:
        ]

    def _save_audit_entries(self) -> None:
        _atomic_write_json(
            self.audit_path,
            {"version": 1, "items": self._audit_entries[-1000:]},
        )

    def _deduplicate_ids(self) -> None:
        """Repair legacy catalogues where multiple files share one id."""
        entries = list(self._catalog.values())
        ordered: List[Dict[str, Any]] = []
        processed_ids: Set[int] = set()
        for entry in entries:
            try:
                entry_id = int(entry.get("id", 0) or 0)
            except (TypeError, ValueError):
                entry_id = 0
            if entry_id in processed_ids:
                continue
            same_id = [
                candidate
                for candidate in entries
                if int(candidate.get("id", 0) or 0) == entry_id
            ]
            same_id.sort(
                key=lambda candidate: bool(candidate.get("aliases")),
                reverse=True,
            )
            ordered.extend(same_id)
            processed_ids.add(entry_id)

        used: Set[int] = set()
        next_id = (
            max(
                (int(item.get("id", 0) or 0) for item in self._catalog.values()),
                default=0,
            )
            + 1
        )
        for entry in ordered:
            try:
                entry_id = int(entry.get("id", 0) or 0)
            except (TypeError, ValueError):
                entry_id = 0
            if entry_id <= 0 or entry_id in used:
                while next_id in used:
                    next_id += 1
                entry_id = next_id
                next_id += 1
            entry["id"] = entry_id
            used.add(entry_id)

    def _save_catalog(self) -> None:
        self._deduplicate_ids()
        items = sorted(self._catalog.values(), key=lambda item: int(item["id"]))
        _atomic_write_json(self.catalog_path, {"version": 2, "items": items})
        self._draw_pool_cache = None

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
        existing_digests = {
            digest
            for path in self.assets_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            for digest in [self._asset_digest(path)]
            if digest
        }
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
                digest = self._asset_digest(source)
                if digest and digest in existing_digests:
                    continue
                try:
                    shutil.copy2(source, target)
                    if digest:
                        existing_digests.add(digest)
                except OSError:
                    continue

    def _load_draw_limits(self) -> None:
        raw = _read_json(self.draw_limits_path, {})
        self._draw_limits = raw if isinstance(raw, dict) else {}

    def _save_draw_limits(self) -> None:
        _atomic_write_json(self.draw_limits_path, self._draw_limits)

    def _load_draw_bonuses(self) -> None:
        raw = _read_json(self.draw_bonuses_path, {})
        self._draw_bonuses = raw if isinstance(raw, dict) else {}

    def _save_draw_bonuses(self) -> None:
        _atomic_write_json(self.draw_bonuses_path, self._draw_bonuses)

    def _load_profiles(self) -> None:
        raw = _read_json(self.profiles_path, {}) if self.profiles_path else {}
        items = raw.get("items", {}) if isinstance(raw, dict) else {}
        if not isinstance(items, dict):
            items = {}
        self._profiles = {
            _as_text(key): dict(value)
            for key, value in items.items()
            if _as_text(key) and isinstance(value, dict)
        }

    def _load_description_overrides(self) -> None:
        raw = _read_json(self.description_overrides_path, {})
        items = raw.get("items", {}) if isinstance(raw, dict) else {}
        if not isinstance(items, dict):
            items = {}
        self._description_overrides = {
            _as_text(key): dict(value)
            for key, value in items.items()
            if _as_text(key) and isinstance(value, dict)
        }

    def _save_description_overrides(self) -> None:
        _atomic_write_json(
            self.description_overrides_path,
            {"version": 1, "items": self._description_overrides},
        )

    @staticmethod
    def _description_key(entry: Mapping[str, Any]) -> str:
        entry_key = _as_text(entry.get("entry_key"))
        if entry_key:
            return f"entry_key:{entry_key}"
        filename = Path(_as_text(entry.get("filename"))).name.casefold()
        return f"filename:{filename}"

    def profile_for(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """Return bundled metadata with an administrator override applied."""
        entry_key = _as_text(entry.get("entry_key"))
        profile = dict(self._profiles.get(entry_key, {}))
        override = self._description_overrides.get(self._description_key(entry))
        if override is not None:
            profile["description_zh"] = _as_text(override.get("description_zh"))
            profile["description_origin"] = "override"
            profile["description_updated_at"] = _as_text(override.get("updated_at"))
            profile["description_updated_by"] = _as_text(override.get("updated_by"))
        elif profile.get("description_zh"):
            profile["description_origin"] = "bundled"
        else:
            profile["description_origin"] = "missing"
        return profile

    def description_for(self, entry: Mapping[str, Any]) -> str:
        return _as_text(self.profile_for(entry).get("description_zh"))

    def set_description(
        self,
        entry: Mapping[str, Any],
        description: str,
        updated_by: str = "",
    ) -> Dict[str, Any]:
        description = str(description or "").strip()
        if not description:
            raise ValueError("简介不能为空")
        with self._lock:
            key = self._description_key(entry)
            self._description_overrides[key] = {
                "description_zh": description,
                "updated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
                "updated_by": _as_text(updated_by),
                "entry_key": _as_text(entry.get("entry_key")),
                "filename": Path(_as_text(entry.get("filename"))).name,
                "catalog_id": int(entry.get("id", 0) or 0),
                "name": _as_text(entry.get("name")),
            }
            self._save_description_overrides()
            return self.profile_for(entry)

    def restore_description(
        self, entry: Mapping[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        with self._lock:
            removed = self._description_overrides.pop(
                self._description_key(entry), None
            )
            if removed is not None:
                self._save_description_overrides()
            return removed is not None, self.profile_for(entry)

    def append_audit(
        self,
        action: str,
        target: str,
        summary: str,
        username: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            item = {
                "id": uuid.uuid4().hex,
                "timestamp": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
                "username": _as_text(username) or "dashboard",
                "action": _as_text(action)[:80],
                "target": _as_text(target)[:240],
                "summary": _as_text(summary)[:1000],
            }
            self._audit_entries.append(item)
            self._audit_entries = self._audit_entries[-1000:]
            self._save_audit_entries()
            return dict(item)

    def audit_entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._lock:
            return [dict(item) for item in reversed(self._audit_entries[-limit:])]

    def _is_retired_asset(self, filename: str) -> bool:
        candidate = Path(filename).name.casefold()
        if any(
            candidate
            in {
                Path(_as_text(alias)).name.casefold()
                for alias in entry.get("aliases", [])
                if _as_text(alias)
            }
            for entry in self._catalog.values()
        ):
            return True
        for tombstone in self._tombstones.values():
            retired_names = {
                Path(_as_text(tombstone.get("filename"))).name.casefold(),
                *{
                    Path(_as_text(value)).name.casefold()
                    for value in tombstone.get("reference_names", [])
                    if _as_text(value)
                },
            }
            if candidate in retired_names:
                return True
        return False

    @staticmethod
    def _asset_digest(path: Path) -> Optional[str]:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    def _merge_legacy_duplicate_assets(self) -> None:
        """Retire old files that are byte-identical to a current local asset.

        Older releases renamed only the copied file in the new data directory.
        The original file in the legacy directory was then scanned as a new
        catalogue entry on the next reload.  The digest match lets us repair
        those records without guessing from display names.
        """
        local_paths = [
            path
            for path in self.assets_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        by_digest: Dict[str, List[Path]] = {}
        for path in local_paths:
            digest = self._asset_digest(path)
            if digest:
                by_digest.setdefault(digest, []).append(path)

        for legacy_dir in self.legacy_dirs:
            legacy_assets_dir = legacy_dir / "img" / "wife"
            if not legacy_assets_dir.is_dir():
                continue
            for old_path in legacy_assets_dir.iterdir():
                if (
                    not old_path.is_file()
                    or old_path.suffix.lower() not in IMAGE_EXTENSIONS
                    or old_path.name in {path.name for path in local_paths}
                ):
                    continue
                digest = self._asset_digest(old_path)
                matches = by_digest.get(digest or "", [])
                if len(matches) != 1:
                    continue

                current_path = matches[0]
                current = self._catalog.get(current_path.name)
                if current is None:
                    current = self._set_entry(current_path.name)
                stale = self._catalog.pop(old_path.name, None)
                aliases = set(current.get("aliases", []))
                aliases.add(old_path.name)
                if stale:
                    aliases.update(stale.get("aliases", []))
                current["aliases"] = sorted(alias for alias in aliases if alias)
                self._replace_references(old_path.name, current_path.name)

    def _merge_duplicate_local_assets(self) -> None:
        """Retire stale copied files that duplicate a renamed local asset."""
        paths_by_digest: Dict[str, List[Path]] = {}
        for path in self.assets_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            digest = self._asset_digest(path)
            if digest:
                paths_by_digest.setdefault(digest, []).append(path)

        for paths in paths_by_digest.values():
            if len(paths) < 2:
                continue
            parsed_sources = {_parse_filename(path.name)[1] for path in paths}
            if len(parsed_sources) != 1 or not next(iter(parsed_sources)):
                continue
            entries = [self._catalog.get(path.name) for path in paths]
            entries = [entry for entry in entries if entry is not None]
            if len(entries) < 2:
                continue
            entries.sort(
                key=lambda item: (
                    not bool(item.get("aliases")),
                    int(item.get("id", 0) or 0),
                    _as_text(item.get("filename")),
                )
            )
            current = entries[0]
            current_filename = Path(_as_text(current["filename"])).name
            aliases = set(current.get("aliases", []))
            for duplicate in entries[1:]:
                duplicate_filename = Path(_as_text(duplicate["filename"])).name
                aliases.add(duplicate_filename)
                aliases.update(duplicate.get("aliases", []))
                self._replace_references(duplicate_filename, current_filename)
                self._catalog.pop(duplicate_filename, None)
            current["aliases"] = sorted(alias for alias in aliases if alias)

    def _merge_duplicate_named_assets(self) -> None:
        """Merge duplicate local entries with the same source and name."""
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for entry in self._catalog.values():
            filename = Path(_as_text(entry.get("filename"))).name
            if not filename or not (self.assets_dir / filename).is_file():
                continue
            key = (
                _as_text(entry.get("source")).casefold(),
                _as_text(entry.get("name")).casefold(),
            )
            if not key[1]:
                continue
            groups.setdefault(key, []).append(entry)

        for entries in groups.values():
            if len(entries) < 2:
                continue
            entries.sort(
                key=lambda item: (
                    not bool(item.get("aliases")),
                    int(item.get("id", 0) or 0),
                    _as_text(item.get("filename")),
                )
            )
            current = entries[0]
            current_filename = Path(_as_text(current["filename"])).name
            aliases = set(current.get("aliases", []))
            for duplicate in entries[1:]:
                duplicate_filename = Path(_as_text(duplicate["filename"])).name
                aliases.add(duplicate_filename)
                aliases.update(duplicate.get("aliases", []))
                self._replace_references(duplicate_filename, current_filename)
                self._catalog.pop(duplicate_filename, None)
            current["aliases"] = sorted(alias for alias in aliases if alias)

    def _is_retired_legacy_asset(self, filename: str) -> bool:
        """Backward-compatible name for callers from older plugin versions."""
        return self._is_retired_asset(filename)

    def _restore_named_alias_assets(self) -> None:
        """Restore files hidden by the old automatic same-name merge.

        The old release stored the hidden filename as an alias but left the
        file on disk. If its parsed name is the same as the visible entry,
        restore it as a real entry and fill the first missing catalogue id.
        Explicit merges remove their old files, so they remain unaffected.
        """
        used_ids = {int(entry.get("id", 0) or 0) for entry in self._catalog.values()}
        max_id = max(used_ids, default=0)
        for path in list(self.assets_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            owners = [
                entry
                for entry in self._catalog.values()
                if path.name in entry.get("aliases", [])
            ]
            if len(owners) != 1:
                continue
            owner = owners[0]
            inferred_name, inferred_source = _parse_filename(path.name)
            if _as_text(owner.get("name")).casefold() != inferred_name.casefold():
                continue
            owner_source = _as_text(owner.get("source"))
            if (
                owner_source
                and inferred_source
                and owner_source.casefold() != inferred_source.casefold()
            ):
                continue

            while (max_id + 1) in used_ids:
                max_id += 1
            candidate_id = next(
                (value for value in range(1, max_id + 1) if value not in used_ids),
                max_id + 1,
            )
            used_ids.add(candidate_id)
            max_id = max(max_id, candidate_id)
            owner["aliases"] = [
                alias for alias in owner.get("aliases", []) if alias != path.name
            ]
            self._set_entry(
                path.name,
                candidate_id,
                _as_text(owner.get("name")),
                owner_source or inferred_source,
                [],
            )

    def _refresh_catalog(self) -> None:
        with self._lock:
            self._merge_legacy_duplicate_assets()
            self._restore_named_alias_assets()
            for asset_dir in [
                self.assets_dir,
                *[legacy / "img" / "wife" for legacy in self.legacy_dirs],
            ]:
                if not asset_dir.is_dir():
                    continue
                for path in asset_dir.iterdir():
                    if (
                        path.is_file()
                        and path.suffix.lower() in IMAGE_EXTENSIONS
                        and not self._is_retired_asset(path.name)
                    ):
                        self._set_entry(path.name)

            for group_file in self.config_dir.glob("*.json"):
                if group_file.name in NON_GROUP_CONFIG_FILENAMES:
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
        self._load_draw_bonuses()

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
            profile = self._profiles.get(_as_text(item.get("entry_key")), {})
            candidates = {
                _as_text(item.get("filename")).casefold(),
                _as_text(item.get("name")).casefold(),
                _as_text(profile.get("name_zh")).casefold(),
                _as_text(profile.get("name_en")).casefold(),
                _as_text(profile.get("display_name")).casefold(),
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
        with self._lock:
            if self._draw_pool_cache is None:
                self._draw_pool_cache = tuple(
                    entry
                    for entry in self.entries()
                    if self.asset_path(entry) is not None or self.image_base_url
                )
            return [dict(entry) for entry in self._draw_pool_cache]

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
        group_id = _safe_data_id(group_id, "群号")
        path = self.config_dir / f"{group_id}.json"
        with self._lock:
            return normalise_group_config(_read_json(path, {}))

    def save_group(self, group_id: str, config: Dict[str, Dict[str, Any]]) -> None:
        group_id = _safe_data_id(group_id, "群号")
        path = self.config_dir / f"{group_id}.json"
        with self._lock:
            _atomic_write_json(path, normalise_group_config(config))

    def group_ids(self) -> List[str]:
        with self._lock:
            ids = {
                path.stem
                for path in self.config_dir.glob("*.json")
                if path.name not in NON_GROUP_CONFIG_FILENAMES
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", path.stem)
            }
            ids.update(
                str(value)
                for value in (*self._draw_limits.keys(), *self._draw_bonuses.keys())
                if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value))
            )
            return sorted(ids, key=lambda value: (not value.isdigit(), value))

    def update_group_user(
        self,
        group_id: str,
        user_id: str,
        *,
        nickname: Optional[str] = None,
        no_new_count: Optional[int] = None,
        current_filename: Any = _UNSET,
        current_date: Optional[str] = None,
        add_unlock_filename: str = "",
        remove_unlock_filename: str = "",
        unlock_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        group_id = _safe_data_id(group_id, "群号")
        user_id = _safe_data_id(user_id, "用户 ID")
        with self._lock:
            config = self.load_group(group_id)
            user = config.setdefault(
                user_id,
                {
                    "current": {"ally_filename": "", "date": ""},
                    "unlocked": [],
                    "nickname": "用户",
                    "no_new_count": 0,
                },
            )
            if nickname is not None:
                user["nickname"] = _as_text(nickname) or "用户"
            if no_new_count is not None:
                user["no_new_count"] = max(0, int(no_new_count))
            if current_filename is not _UNSET:
                filename = Path(_as_text(current_filename)).name
                if filename and filename not in self._catalog:
                    raise ValueError("当前盟友素材不存在")
                user["current"] = {
                    "ally_filename": filename,
                    "date": _as_text(current_date) if filename else "",
                }

            unlocked = _normalise_unlocked(user.get("unlocked"))
            remove_name = Path(_as_text(remove_unlock_filename)).name
            if remove_name:
                unlocked = [
                    item
                    for item in unlocked
                    if item.get("ally_filename") != remove_name
                ]
            add_name = Path(_as_text(add_unlock_filename)).name
            if add_name:
                if add_name not in self._catalog:
                    raise ValueError("要添加的解锁素材不存在")
                if not any(item.get("ally_filename") == add_name for item in unlocked):
                    unlocked.append(
                        {
                            "ally_filename": add_name,
                            "unlock_date": _normalise_date(unlock_date),
                        }
                    )
            user["unlocked"] = unlocked
            config[user_id] = normalise_group_config({user_id: user})[user_id]
            self.save_group(group_id, config)
            return deepcopy(config[user_id])

    def update_group_user_state(
        self,
        group_id: str,
        user_id: str,
        *,
        nickname: Optional[str] = None,
        no_new_count: Optional[int] = None,
        current_filename: Any = _UNSET,
        current_date: Optional[str] = None,
        draw_count: Any = _UNSET,
        draw_bonus: Any = _UNSET,
        counter_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically update one member record and its daily counters."""

        group_id = _safe_data_id(group_id, "群号")
        user_id = _safe_data_id(user_id, "用户 ID")
        normalized_draw_count = (
            _UNSET if draw_count is _UNSET else max(0, int(draw_count))
        )
        normalized_draw_bonus = (
            _UNSET if draw_bonus is _UNSET else max(0, int(draw_bonus))
        )
        with self._lock:
            group_path = self.config_dir / f"{group_id}.json"
            file_snapshots = {
                group_path: _snapshot_file(group_path),
                self.draw_limits_path: _snapshot_file(self.draw_limits_path),
                self.draw_bonuses_path: _snapshot_file(self.draw_bonuses_path),
            }
            draw_limits_before = deepcopy(self._draw_limits)
            draw_bonuses_before = deepcopy(self._draw_bonuses)
            try:
                updated = self.update_group_user(
                    group_id,
                    user_id,
                    nickname=nickname,
                    no_new_count=no_new_count,
                    current_filename=current_filename,
                    current_date=current_date,
                )
                if normalized_draw_count is not _UNSET:
                    self.set_draw_count(
                        group_id,
                        user_id,
                        normalized_draw_count,
                        counter_date,
                    )
                if normalized_draw_bonus is not _UNSET:
                    self.set_draw_bonus(
                        group_id,
                        user_id,
                        normalized_draw_bonus,
                        counter_date,
                    )
                return updated
            except Exception as exc:
                self._draw_limits = draw_limits_before
                self._draw_bonuses = draw_bonuses_before
                rollback_errors: List[str] = []
                for path, snapshot in file_snapshots.items():
                    try:
                        _restore_file(path, snapshot)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{path.name}: {rollback_exc}")
                if rollback_errors:
                    raise RuntimeError(
                        "成员数据保存失败且自动回滚不完整："
                        + "；".join(rollback_errors)
                    ) from exc
                raise

    def delete_group_user(self, group_id: str, user_id: str) -> bool:
        group_id = _safe_data_id(group_id, "群号")
        user_id = _safe_data_id(user_id, "用户 ID")
        with self._lock:
            config = self.load_group(group_id)
            removed = config.pop(user_id, None) is not None
            if removed:
                self.save_group(group_id, config)
            counters_changed = False
            for data in (self._draw_limits, self._draw_bonuses):
                group = data.get(group_id)
                if not isinstance(group, dict) or user_id not in group:
                    continue
                group.pop(user_id, None)
                counters_changed = True
                if not group:
                    data.pop(group_id, None)
            if counters_changed:
                self._save_draw_limits()
                self._save_draw_bonuses()
            return removed or counters_changed

    def draw_count(
        self, group_id: str, user_id: str, today: Optional[str] = None
    ) -> int:
        today = today or get_today()
        return self._daily_counter_value(
            self._draw_limits, str(group_id), str(user_id), today
        )

    @staticmethod
    def _daily_counter_value(
        data: Mapping[str, Any], group_id: str, user_id: str, today: str
    ) -> int:
        group = data.get(group_id, {})
        if not isinstance(group, dict):
            return 0
        user = group.get(user_id, {})
        if not isinstance(user, dict):
            return 0
        try:
            return max(0, int(user.get(today, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def increment_draw(
        self, group_id: str, user_id: str, today: Optional[str] = None
    ) -> int:
        with self._lock:
            today = today or get_today()
            group = self._draw_limits.setdefault(str(group_id), {})
            if not isinstance(group, dict):
                group = {}
                self._draw_limits[str(group_id)] = group
            user = group.setdefault(str(user_id), {})
            if not isinstance(user, dict):
                user = {}
                group[str(user_id)] = user
            user[today] = self.draw_count(group_id, user_id, today) + 1
            self._save_draw_limits()
            return int(user[today])

    def draw_bonus(
        self, group_id: str, user_id: str, today: Optional[str] = None
    ) -> int:
        today = today or get_today()
        return self._daily_counter_value(
            self._draw_bonuses, str(group_id), str(user_id), today
        )

    def add_draw_bonus(
        self,
        group_id: str,
        user_id: str,
        amount: int = 1,
        today: Optional[str] = None,
    ) -> int:
        amount = int(amount)
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        with self._lock:
            today = today or get_today()
            group = self._draw_bonuses.setdefault(str(group_id), {})
            if not isinstance(group, dict):
                group = {}
                self._draw_bonuses[str(group_id)] = group
            user = group.setdefault(str(user_id), {})
            if not isinstance(user, dict):
                user = {}
                group[str(user_id)] = user
            user[today] = self.draw_bonus(group_id, user_id, today) + amount
            self._save_draw_bonuses()
            return int(user[today])

    def set_draw_count(
        self,
        group_id: str,
        user_id: str,
        value: int,
        today: Optional[str] = None,
    ) -> int:
        return self._set_daily_counter(
            self._draw_limits,
            self._save_draw_limits,
            group_id,
            user_id,
            value,
            today,
        )

    def set_draw_bonus(
        self,
        group_id: str,
        user_id: str,
        value: int,
        today: Optional[str] = None,
    ) -> int:
        return self._set_daily_counter(
            self._draw_bonuses,
            self._save_draw_bonuses,
            group_id,
            user_id,
            value,
            today,
        )

    def _set_daily_counter(
        self,
        data: Dict[str, Any],
        save: Any,
        group_id: str,
        user_id: str,
        value: int,
        today: Optional[str],
    ) -> int:
        group_id = _safe_data_id(group_id, "群号")
        user_id = _safe_data_id(user_id, "用户 ID")
        date = _as_text(today) or get_today()
        value = max(0, int(value))
        with self._lock:
            group = data.setdefault(group_id, {})
            if not isinstance(group, dict):
                group = {}
                data[group_id] = group
            dates = group.setdefault(user_id, {})
            if not isinstance(dates, dict):
                dates = {}
                group[user_id] = dates
            if value:
                dates[date] = value
            else:
                dates.pop(date, None)
                if not dates:
                    group.pop(user_id, None)
                if not group:
                    data.pop(group_id, None)
            save()
            return value

    def reset_group_draws(
        self, group_id: str, today: Optional[str] = None
    ) -> Dict[str, int]:
        """Clear one group's used counts and granted opportunities for one day."""

        group_id = _safe_data_id(group_id, "群号")
        with self._lock:
            today = today or get_today()
            affected_users: Set[str] = set()

            def clear_day(data: Dict[str, Any]) -> int:
                group = data.get(group_id)
                if not isinstance(group, dict):
                    return 0
                cleared = 0
                for user_id, dates in list(group.items()):
                    if not isinstance(dates, dict) or today not in dates:
                        continue
                    dates.pop(today, None)
                    affected_users.add(str(user_id))
                    cleared += 1
                    if not dates:
                        group.pop(user_id, None)
                if not group:
                    data.pop(group_id, None)
                return cleared

            draw_records = clear_day(self._draw_limits)
            bonus_records = clear_day(self._draw_bonuses)
            if draw_records:
                self._save_draw_limits()
            if bonus_records:
                self._save_draw_bonuses()
            return {
                "users": len(affected_users),
                "draw_records": draw_records,
                "bonus_records": bonus_records,
            }

    def _rename_entry_unchecked(
        self,
        entry: Dict[str, Any],
        new_name: str,
        new_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            old_description_key = self._description_key(entry)
            old_filename = Path(_as_text(entry["filename"])).name
            old_path = self.asset_path(entry)
            suffix = (
                old_path.suffix if old_path else Path(old_filename).suffix or ".png"
            )
            source = (
                _as_text(entry.get("source"))
                if new_source is None
                else _as_text(new_source)
            )
            prefix = source
            if new_source is None and not prefix:
                _old_name, prefix = _parse_filename(old_filename)
            if prefix:
                new_filename = f"{prefix}.{_safe_filename(new_name)}{suffix.lower()}"
            else:
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
                    "source": source,
                    "aliases": sorted(set(aliases)),
                }
            )
            self._catalog[new_filename] = updated
            new_description_key = self._description_key(updated)
            if (
                old_description_key != new_description_key
                and old_description_key in self._description_overrides
            ):
                override = self._description_overrides.pop(old_description_key)
                override["filename"] = new_filename
                override["name"] = _as_text(updated.get("name"))
                self._description_overrides[new_description_key] = override
                self._save_description_overrides()
            self._save_catalog()
            return dict(updated)

    def update_entry_details(
        self,
        entry: Mapping[str, Any],
        new_name: str,
        new_source: Optional[str] = None,
        *,
        description_action: str = "keep",
        description: str = "",
        updated_by: str = "",
    ) -> Dict[str, Any]:
        """Atomically update an entry filename, references and description."""

        description_action = _as_text(description_action) or "keep"
        if description_action not in {"keep", "set", "restore"}:
            raise ValueError("简介操作无效")
        description = str(description or "").strip()
        if description_action == "set" and not description:
            raise ValueError("简介不能为空")

        with self._lock:
            old_filename = Path(_as_text(entry.get("filename"))).name
            current = self._catalog.get(old_filename)
            if current is None:
                raise ValueError("图鉴条目不存在或已经删除")
            current = deepcopy(current)
            old_path = self.asset_path(current)
            suffix = (
                old_path.suffix if old_path else Path(old_filename).suffix or ".png"
            )
            source = (
                _as_text(current.get("source"))
                if new_source is None
                else _as_text(new_source)
            )
            prefix = source
            if new_source is None and not prefix:
                _old_name, prefix = _parse_filename(old_filename)
            if prefix:
                new_filename = f"{prefix}.{_safe_filename(new_name)}{suffix.lower()}"
            else:
                new_filename = (
                    f"ally_{int(current['id']):04d}_{_safe_filename(new_name)}"
                    f"{suffix.lower()}"
                )

            tracked_paths = {
                self.catalog_path,
                self.description_overrides_path,
                *[
                    path
                    for path in self.config_dir.glob("*.json")
                    if path.name not in NON_GROUP_CONFIG_FILENAMES
                ],
                self.assets_dir / old_filename,
                self.assets_dir / new_filename,
            }
            file_snapshots = {path: _snapshot_file(path) for path in tracked_paths}
            catalog_before = deepcopy(self._catalog)
            descriptions_before = deepcopy(self._description_overrides)
            try:
                updated = current
                if _as_text(new_name) != _as_text(
                    current.get("name")
                ) or source != _as_text(current.get("source")):
                    updated = self._rename_entry_unchecked(
                        current,
                        new_name,
                        new_source,
                    )
                if description_action == "set":
                    self.set_description(updated, description, updated_by=updated_by)
                elif description_action == "restore":
                    self.restore_description(updated)
                return dict(updated)
            except Exception as exc:
                self._catalog = catalog_before
                self._description_overrides = descriptions_before
                rollback_errors: List[str] = []
                for path, snapshot in file_snapshots.items():
                    try:
                        _restore_file(path, snapshot)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{path.name}: {rollback_exc}")
                if rollback_errors:
                    raise RuntimeError(
                        "素材资料保存失败且自动回滚不完整："
                        + "；".join(rollback_errors)
                    ) from exc
                raise

    def rename_entry(
        self,
        entry: Dict[str, Any],
        new_name: str,
        new_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.update_entry_details(entry, new_name, new_source)

    def cleanup_renamed_prefix(
        self,
        old_prefix: str,
        new_prefix: str,
        keep_names: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """Merge old-prefixed entries into their renamed counterparts.

        This is intended for one-time repairs after an older release left the
        pre-rename files in the new data directory.
        """
        old_prefix = _as_text(old_prefix)
        new_prefix = _as_text(new_prefix)
        keep = {_as_text(name) for name in keep_names if _as_text(name)}
        removed: List[str] = []
        unresolved: List[str] = []
        kept: List[str] = []

        with self._lock:
            self._refresh_catalog()
            candidates = list(self._catalog.values())
            for entry in candidates:
                old_name = _as_text(entry.get("name"))
                if not old_name.startswith(old_prefix) or old_name in keep:
                    continue
                target_name = f"{new_prefix}{old_name[len(old_prefix) :]}"
                matches = [
                    item
                    for item in self._catalog.values()
                    if _as_text(item.get("name")) == target_name
                    and _as_text(item.get("source")) == _as_text(entry.get("source"))
                ]
                if len(matches) != 1:
                    unresolved.append(old_name)
                    continue
                target = matches[0]
                old_filename = Path(_as_text(entry["filename"])).name
                target_filename = Path(_as_text(target["filename"])).name
                if old_filename == target_filename:
                    continue
                aliases = set(target.get("aliases", []))
                aliases.add(old_filename)
                aliases.update(entry.get("aliases", []))
                target["aliases"] = sorted(alias for alias in aliases if alias)
                self._replace_references(old_filename, target_filename)
                old_path = self.assets_dir / old_filename
                if old_path.is_file() and old_path != self.assets_dir / target_filename:
                    try:
                        old_path.unlink()
                    except OSError:
                        pass
                self._catalog.pop(old_filename, None)
                removed.append(f"{old_name} -> {target_name}")

            for keep_name in keep:
                matching = [
                    item
                    for item in self._catalog.values()
                    if _as_text(item.get("name")) == keep_name
                ]
                if len(matching) < 2:
                    if matching:
                        kept.append(keep_name)
                    continue
                matching.sort(key=lambda item: int(item.get("id", 0) or 0))
                target = matching[0]
                target_filename = Path(_as_text(target["filename"])).name
                aliases = set(target.get("aliases", []))
                for duplicate in matching[1:]:
                    duplicate_filename = Path(_as_text(duplicate["filename"])).name
                    aliases.add(duplicate_filename)
                    aliases.update(duplicate.get("aliases", []))
                    self._replace_references(duplicate_filename, target_filename)
                    duplicate_path = self.assets_dir / duplicate_filename
                    if duplicate_path.is_file():
                        try:
                            duplicate_path.unlink()
                        except OSError:
                            pass
                    self._catalog.pop(duplicate_filename, None)
                    removed.append(f"{keep_name}#{duplicate.get('id')}")
                target["aliases"] = sorted(alias for alias in aliases if alias)
                kept.append(keep_name)

            self._save_catalog()
        return {"removed": removed, "unresolved": unresolved, "kept": kept}

    def merge_duplicate_entries(
        self, mappings: Sequence[Tuple[int, int]]
    ) -> Dict[str, Any]:
        """Merge explicitly mapped duplicate ids into their correct entries."""
        removed: List[str] = []
        unresolved: List[str] = []

        with self._lock:
            for duplicate_id, target_id in mappings:
                duplicate = next(
                    (
                        item
                        for item in self._catalog.values()
                        if int(item.get("id", 0) or 0) == int(duplicate_id)
                    ),
                    None,
                )
                target = next(
                    (
                        item
                        for item in self._catalog.values()
                        if int(item.get("id", 0) or 0) == int(target_id)
                    ),
                    None,
                )
                if duplicate is None or target is None or duplicate is target:
                    unresolved.append(f"#{duplicate_id} -> #{target_id}（编号不存在）")
                    continue

                old_filename = Path(_as_text(duplicate["filename"])).name
                target_filename = Path(_as_text(target["filename"])).name
                aliases = set(target.get("aliases", []))
                aliases.add(old_filename)
                aliases.update(duplicate.get("aliases", []))
                target["aliases"] = sorted(alias for alias in aliases if alias)
                self._replace_references(old_filename, target_filename)

                asset_path = self.assets_dir / old_filename
                if asset_path.is_file():
                    try:
                        asset_path.unlink()
                    except OSError:
                        pass
                self._catalog.pop(old_filename, None)
                removed.append(
                    f"#{duplicate_id} {_as_text(duplicate.get('name'))}"
                    f" -> #{target_id} {_as_text(target.get('name'))}"
                )

            self._save_catalog()
        return {"removed": removed, "unresolved": unresolved}

    def delete_entry(
        self,
        entry: Mapping[str, Any],
        deleted_by: str = "",
    ) -> Dict[str, Any]:
        """Archive one entry and remove its references from active group data."""

        with self._lock:
            filename = Path(_as_text(entry.get("filename"))).name
            current = self._catalog.get(filename)
            if current is None:
                raise ValueError("图鉴条目不存在或已经删除")
            current = deepcopy(current)
            entry_id = int(current.get("id", 0) or 0)
            token = (
                datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")
                + f"-{entry_id}-{uuid.uuid4().hex[:8]}"
            )
            archive_dir = self.trash_dir / token
            archive_dir.mkdir(parents=True, exist_ok=False)
            record_path = archive_dir / "record.json"
            asset_path = self.asset_path(current)
            archived_asset = archive_dir / filename
            if asset_path is not None and asset_path.is_file():
                shutil.copy2(asset_path, archived_asset)

            reference_names = {
                filename,
                *[
                    Path(_as_text(alias)).name
                    for alias in current.get("aliases", [])
                    if _as_text(alias)
                ],
            }
            affected_groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
            changed_groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for group_id in self.group_ids():
                group_file = self.config_dir / f"{group_id}.json"
                if not group_file.is_file():
                    continue
                config = self.load_group(group_id)
                changed = False
                snapshots: Dict[str, Dict[str, Any]] = {}
                for user_id, user in config.items():
                    current_ref = Path(
                        _as_text(user.get("current", {}).get("ally_filename"))
                    ).name
                    unlocked = _normalise_unlocked(user.get("unlocked"))
                    removed_unlocks = [
                        item
                        for item in unlocked
                        if Path(_as_text(item.get("ally_filename"))).name
                        in reference_names
                    ]
                    if current_ref not in reference_names and not removed_unlocks:
                        continue
                    snapshots[user_id] = deepcopy(user)
                    if current_ref in reference_names:
                        user["current"] = {"ally_filename": "", "date": ""}
                    if removed_unlocks:
                        user["unlocked"] = [
                            item
                            for item in unlocked
                            if Path(_as_text(item.get("ally_filename"))).name
                            not in reference_names
                        ]
                    changed = True
                if changed:
                    affected_groups[group_id] = snapshots
                    changed_groups[group_id] = config

            description_key = self._description_key(current)
            description_override = deepcopy(
                self._description_overrides.get(description_key)
            )
            deleted_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
            record = {
                "version": 1,
                "status": "prepared",
                "token": token,
                "deleted_at": deleted_at,
                "deleted_by": _as_text(deleted_by) or "dashboard",
                "entry": current,
                "reference_names": sorted(reference_names),
                "description_key": description_key,
                "description_override": description_override,
                "affected_groups": affected_groups,
                "asset_present": archived_asset.is_file(),
            }
            _atomic_write_json(record_path, record)

            try:
                for group_id, config in changed_groups.items():
                    self.save_group(group_id, config)
                self._description_overrides.pop(description_key, None)
                if description_override is not None:
                    self._save_description_overrides()
                self._catalog.pop(filename, None)
                self._save_catalog()
                if (
                    asset_path is not None
                    and asset_path.parent == self.assets_dir
                    and asset_path.is_file()
                ):
                    asset_path.unlink()
                tombstone = {
                    "token": token,
                    "id": entry_id,
                    "name": _as_text(current.get("name")),
                    "source": _as_text(current.get("source")),
                    "filename": filename,
                    "deleted_at": deleted_at,
                    "deleted_by": record["deleted_by"],
                    "reference_names": sorted(reference_names),
                    "affected_users": sum(
                        len(users) for users in affected_groups.values()
                    ),
                    "asset_present": archived_asset.is_file(),
                }
                self._tombstones[token] = tombstone
                self._save_tombstones()
                record["status"] = "deleted"
                _atomic_write_json(record_path, record)
                return dict(tombstone)
            except Exception:
                self._catalog[filename] = current
                self._save_catalog()
                if description_override is not None:
                    self._description_overrides[description_key] = description_override
                    self._save_description_overrides()
                for group_id, snapshots in affected_groups.items():
                    config = self.load_group(group_id)
                    for user_id, user in snapshots.items():
                        config[user_id] = deepcopy(user)
                    self.save_group(group_id, config)
                if (
                    archived_asset.is_file()
                    and asset_path is not None
                    and asset_path.parent == self.assets_dir
                    and not asset_path.exists()
                ):
                    shutil.copy2(archived_asset, asset_path)
                self._tombstones.pop(token, None)
                self._save_tombstones()
                record["status"] = "failed"
                _atomic_write_json(record_path, record)
                raise

    def deleted_entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(
                (dict(item) for item in self._tombstones.values()),
                key=lambda item: _as_text(item.get("deleted_at")),
                reverse=True,
            )

    def restore_deleted_entry(
        self,
        token: str,
        restored_by: str = "",
    ) -> Dict[str, Any]:
        token = _safe_data_id(token, "回收站标识")
        with self._lock:
            tombstone = self._tombstones.get(token)
            if tombstone is None:
                raise ValueError("回收站记录不存在或已经恢复")
            archive_dir = self.trash_dir / token
            record_path = archive_dir / "record.json"
            record = _read_json(record_path, {})
            entry = record.get("entry") if isinstance(record, dict) else None
            if not isinstance(entry, dict):
                raise ValueError("回收站记录损坏，缺少图鉴条目")
            filename = Path(_as_text(entry.get("filename"))).name
            entry_id = int(entry.get("id", 0) or 0)
            if not filename or entry_id <= 0:
                raise ValueError("回收站记录损坏，编号或文件名无效")
            if filename in self._catalog or any(
                int(item.get("id", 0) or 0) == entry_id
                for item in self._catalog.values()
            ):
                raise FileExistsError("当前图鉴已有相同文件名或编号，无法恢复")

            archived_asset = archive_dir / filename
            target_asset = self.assets_dir / filename
            if target_asset.exists():
                raise FileExistsError("素材目录已有同名文件，无法恢复")
            if bool(record.get("asset_present")) and not archived_asset.is_file():
                raise ValueError("回收站记录损坏，归档素材文件缺失")

            description_key = _as_text(record.get("description_key"))
            description_override = record.get("description_override")
            reference_names = {
                Path(_as_text(value)).name
                for value in record.get("reference_names", [])
                if _as_text(value)
            }
            reference_names.add(filename)
            affected_groups = record.get("affected_groups", {})
            group_before: Dict[str, Tuple[bool, Dict[str, Dict[str, Any]]]] = {}
            group_updates: Dict[str, Dict[str, Dict[str, Any]]] = {}
            if isinstance(affected_groups, dict):
                for raw_group_id, snapshots in affected_groups.items():
                    if not isinstance(snapshots, dict):
                        continue
                    group_id = _safe_data_id(raw_group_id, "群号")
                    group_path = self.config_dir / f"{group_id}.json"
                    config = self.load_group(group_id)
                    group_before[group_id] = (group_path.is_file(), deepcopy(config))
                    for user_id, snapshot in snapshots.items():
                        if not isinstance(snapshot, dict):
                            continue
                        current_user = config.get(user_id)
                        if current_user is None:
                            config[user_id] = deepcopy(snapshot)
                            continue
                        current_ref = _as_text(
                            current_user.get("current", {}).get("ally_filename")
                        )
                        snapshot_current = snapshot.get("current", {})
                        snapshot_ref = Path(
                            _as_text(snapshot_current.get("ally_filename"))
                        ).name
                        if not current_ref and snapshot_ref in reference_names:
                            current_user["current"] = deepcopy(snapshot_current)
                        unlocked = _normalise_unlocked(current_user.get("unlocked"))
                        by_filename = {item["ally_filename"]: item for item in unlocked}
                        for item in _normalise_unlocked(snapshot.get("unlocked")):
                            item_name = Path(item["ally_filename"]).name
                            if item_name not in reference_names:
                                continue
                            previous = by_filename.get(item_name)
                            if (
                                previous is None
                                or item["unlock_date"] < previous["unlock_date"]
                            ):
                                by_filename[item_name] = item
                        current_user["unlocked"] = list(by_filename.values())
                    group_updates[group_id] = config

            catalog_before = deepcopy(self._catalog)
            descriptions_before = deepcopy(self._description_overrides)
            tombstones_before = deepcopy(self._tombstones)
            record_before = deepcopy(record)
            asset_copied = False
            try:
                if archived_asset.is_file():
                    shutil.copy2(archived_asset, target_asset)
                    asset_copied = True
                self._catalog[filename] = deepcopy(entry)
                if description_key and isinstance(description_override, dict):
                    self._description_overrides[description_key] = deepcopy(
                        description_override
                    )
                    self._save_description_overrides()
                self._save_catalog()
                for group_id, config in group_updates.items():
                    self.save_group(group_id, config)
                self._tombstones.pop(token, None)
                self._save_tombstones()
                record["status"] = "restored"
                record["restored_at"] = datetime.now(SHANGHAI).isoformat(
                    timespec="seconds"
                )
                record["restored_by"] = _as_text(restored_by) or "dashboard"
                _atomic_write_json(record_path, record)
                return dict(self._catalog[filename])
            except Exception as exc:
                rollback_errors: List[str] = []
                self._catalog = catalog_before
                self._description_overrides = descriptions_before
                self._tombstones = tombstones_before
                for operation, label in (
                    (self._save_catalog, "图鉴目录"),
                    (self._save_description_overrides, "简介覆盖"),
                    (self._save_tombstones, "回收站索引"),
                ):
                    try:
                        operation()
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{label}: {rollback_exc}")
                for group_id, (existed, previous) in group_before.items():
                    group_path = self.config_dir / f"{group_id}.json"
                    try:
                        if existed:
                            _atomic_write_json(group_path, previous)
                        elif group_path.exists():
                            group_path.unlink()
                    except Exception as rollback_exc:
                        rollback_errors.append(f"群 {group_id}: {rollback_exc}")
                if asset_copied and target_asset.exists():
                    try:
                        target_asset.unlink()
                    except Exception as rollback_exc:
                        rollback_errors.append(f"素材文件: {rollback_exc}")
                try:
                    _atomic_write_json(record_path, record_before)
                except Exception as rollback_exc:
                    rollback_errors.append(f"回收站记录: {rollback_exc}")
                if rollback_errors:
                    raise RuntimeError(
                        "恢复失败且自动回滚不完整：" + "；".join(rollback_errors)
                    ) from exc
                raise

    def _replace_references(self, old_filename: str, new_filename: str) -> None:
        for group_file in self.config_dir.glob("*.json"):
            if group_file.name in NON_GROUP_CONFIG_FILENAMES:
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
        description: str = "",
        updated_by: str = "",
    ) -> Dict[str, Any]:
        self._validate_image(data)
        description = str(description or "").strip()
        with self._lock:
            entry_id = self._next_id()
            image_format = "png"
            with Image.open(BytesIO(data)) as image:
                image_format = (image.format or "PNG").lower()
            extension = (
                ".jpg" if image_format in {"jpeg", "jpg"} else f".{image_format}"
            )
            filename = f"ally_{entry_id:04d}_{_safe_filename(name)}{extension}"
            target = self.assets_dir / filename
            catalog_before = deepcopy(self._catalog)
            descriptions_before = deepcopy(self._description_overrides)
            try:
                _atomic_write_bytes(target, data)
                digest = hashlib.sha256(data).hexdigest()[:24]
                entry = self._set_entry(
                    filename,
                    entry_id,
                    name,
                    source,
                    metadata={
                        "entry_key": f"manual:{entry_id}:{digest}",
                        "catalog_kind": "manual",
                        "asset_set": "manual",
                    },
                )
                self._save_catalog()
                if description:
                    self.set_description(entry, description, updated_by=updated_by)
                return dict(entry)
            except Exception as exc:
                self._catalog = catalog_before
                self._description_overrides = descriptions_before
                rollback_errors: List[str] = []
                for operation, label in (
                    (self._save_catalog, "图鉴目录"),
                    (self._save_description_overrides, "简介覆盖"),
                ):
                    try:
                        operation()
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{label}: {rollback_exc}")
                if target.exists():
                    try:
                        target.unlink()
                    except Exception as rollback_exc:
                        rollback_errors.append(f"素材文件: {rollback_exc}")
                if rollback_errors:
                    raise RuntimeError(
                        "新增素材失败且自动回滚不完整：" + "；".join(rollback_errors)
                    ) from exc
                raise

    def unlocked_filenames(self, user: Dict[str, Any]) -> List[str]:
        return [
            item["ally_filename"] for item in _normalise_unlocked(user.get("unlocked"))
        ]

    def user_progress(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """Return progress against current canonical catalogue entries."""
        entries = self.entries()
        recorded = {Path(value).name for value in self.unlocked_filenames(user)}
        unlocked: Set[str] = set()
        for entry in entries:
            candidates = {
                Path(_as_text(entry.get("filename"))).name,
                *[
                    Path(_as_text(alias)).name
                    for alias in entry.get("aliases", [])
                    if _as_text(alias)
                ],
            }
            if candidates & recorded:
                unlocked.add(Path(_as_text(entry.get("filename"))).name)
        missing = [
            dict(entry)
            for entry in entries
            if Path(_as_text(entry.get("filename"))).name not in unlocked
        ]
        return {
            "unlocked": len(unlocked),
            "total": len(entries),
            "missing": missing,
            "unlocked_filenames": sorted(unlocked),
        }

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

    def leaderboard(
        self,
        group_id: str,
        limit: int = 10,
        exclude_user_ids: Iterable[str] = (),
    ) -> List[Tuple[str, str, int]]:
        config = self.load_group(group_id)
        excluded = {str(user_id) for user_id in exclude_user_ids}
        rows = [
            (
                user_id,
                _as_text(user.get("nickname"), "用户"),
                int(self.user_progress(user)["unlocked"]),
            )
            for user_id, user in config.items()
            if user_id not in excluded
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
        return self.render_gallery_pages(
            output_path,
            unlocked,
            title,
            columns=columns,
            personal=personal,
            max_height_px=0,
        )[0]

    def render_gallery_pages(
        self,
        output_path: Path,
        unlocked: Set[str],
        title: str,
        columns: int = 10,
        personal: bool = False,
        max_height_px: int = 7600,
    ) -> List[Path]:
        entries = self.entries()
        if personal:
            entries = [entry for entry in entries if entry["filename"] in unlocked]
        if not entries:
            raise ValueError("图鉴中没有可显示的盟友")

        columns = max(4, min(12, int(columns)))
        cell_width, thumb_size, label_height = 142, (126, 112), 32
        cell_height = thumb_size[1] + label_height + 8
        header_height = 58
        if max_height_px > 0:
            max_rows = max(1, (int(max_height_px) - header_height) // cell_height)
            entries_per_page = max_rows * columns
        else:
            entries_per_page = len(entries)
        page_entries = [
            entries[index : index + entries_per_page]
            for index in range(0, len(entries), entries_per_page)
        ]
        page_total = len(page_entries)
        manifest_path = output_path.with_suffix(f"{output_path.suffix}.cache.json")
        signature_payload = {
            "title": title,
            "columns": columns,
            "personal": bool(personal),
            "max_height_px": int(max_height_px),
            "entries": [
                {
                    "id": int(entry.get("id", 0) or 0),
                    "filename": _as_text(entry.get("filename")),
                    "name": _as_text(entry.get("name")),
                    "unlocked": personal
                    or _as_text(entry.get("filename")) in unlocked,
                    "asset": self._gallery_asset_fingerprint(entry),
                }
                for entry in entries
            ],
        }
        signature = hashlib.sha256(
            json.dumps(
                signature_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cached = _read_json(manifest_path, {})
        if isinstance(cached, dict) and cached.get("signature") == signature:
            cached_outputs = [
                output_path.parent / Path(str(name)).name
                for name in cached.get("outputs", [])
                if str(name).strip()
            ]
            if cached_outputs and all(path.is_file() for path in cached_outputs):
                return cached_outputs

        outputs: List[Path] = []
        for page_number, current_entries in enumerate(page_entries, start=1):
            page_output = (
                output_path
                if page_total == 1
                else output_path.with_name(
                    f"{output_path.stem}_p{page_number:02d}-of-{page_total:02d}"
                    f"{output_path.suffix}"
                )
            )
            page_title = (
                title
                if page_total == 1
                else f"{title}  第 {page_number}/{page_total} 页"
            )
            self._render_gallery_page(
                page_output,
                current_entries,
                unlocked,
                page_title,
                columns,
                personal,
                cell_width,
                thumb_size,
                cell_height,
                header_height,
            )
            outputs.append(page_output)
        old_output_names = cached.get("outputs", []) if isinstance(cached, dict) else []
        old_outputs = [
            output_path.parent / Path(str(name)).name
            for name in old_output_names
            if str(name).strip()
        ]
        for stale_path in old_outputs:
            if stale_path not in outputs:
                try:
                    stale_path.unlink(missing_ok=True)
                except OSError:
                    pass
        _atomic_write_json(
            manifest_path,
            {
                "signature": signature,
                "outputs": [path.name for path in outputs],
            },
        )
        return outputs

    def _gallery_asset_fingerprint(self, entry: Mapping[str, Any]) -> str:
        path = self.asset_path(dict(entry))
        if path is None:
            return "missing"
        try:
            stat = path.stat()
            return f"{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            return "unreadable"

    def _render_gallery_page(
        self,
        output_path: Path,
        entries: Sequence[Dict[str, Any]],
        unlocked: Set[str],
        title: str,
        columns: int,
        personal: bool,
        cell_width: int,
        thumb_size: Tuple[int, int],
        cell_height: int,
        header_height: int,
    ) -> Path:
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
