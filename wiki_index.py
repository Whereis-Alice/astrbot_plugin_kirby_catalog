from __future__ import annotations

import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .catalog_core import (
    CatalogStore,
    WIKI_INDEX_OVERRIDES_FILENAME,
    _atomic_write_json,
    _read_json,
)

WIKI_SITES = ("wikirby", "fandom", "shinkaku")
WIKI_SITE_LABELS = {
    "wikirby": "WiKirby",
    "fandom": "Kirby Fandom",
    "shinkaku": "真格攻略 Wiki",
}


def parse_wiki_number(value: Any) -> Optional[int]:
    match = re.fullmatch(
        r"\s*(?:(?:#|编号|序号)\s*)?(\d+)\s*",
        str(value or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().casefold() not in {"0", "false", "no", "off"}


def _english_from_bilingual(value: Any) -> str:
    text = _text(value)
    matches = re.findall(r"[（(]([^（）()\n]*[A-Za-z][^（）()\n]*)[）)]", text)
    return _text(matches[-1]) if matches else ""


class WikiIndexStore:
    """Maintain per-wiki quick lookup numbers without mutating catalog data."""

    def __init__(
        self,
        catalog: CatalogStore,
        shinkaku_names_path: Path,
        overrides_path: Optional[Path] = None,
    ) -> None:
        self.catalog = catalog
        self.shinkaku_names_path = Path(shinkaku_names_path)
        self.overrides_path = Path(
            overrides_path
            or catalog.config_dir / WIKI_INDEX_OVERRIDES_FILENAME
        )
        self._lock = threading.RLock()
        self._overrides: Dict[str, Dict[str, Dict[str, Any]]] = {
            site: {} for site in WIKI_SITES
        }
        self._load()

    @staticmethod
    def normalize_site(site: Any) -> str:
        normalized = _text(site).casefold().replace("_", "").replace("-", "")
        aliases = {
            "wikirby": "wikirby",
            "wiki": "wikirby",
            "fandom": "fandom",
            "kirbyfandom": "fandom",
            "shinkaku": "shinkaku",
            "真格": "shinkaku",
        }
        result = aliases.get(normalized, normalized)
        if result not in WIKI_SITES:
            raise ValueError("百科类型无效")
        return result

    def _load(self) -> None:
        raw = _read_json(self.overrides_path, {})
        raw_items = raw.get("items", {}) if isinstance(raw, dict) else {}
        if not isinstance(raw_items, dict):
            raw_items = {}
        for site in WIKI_SITES:
            site_items = raw_items.get(site, {})
            if not isinstance(site_items, dict):
                continue
            self._overrides[site] = {
                _text(key): dict(value)
                for key, value in site_items.items()
                if _text(key) and isinstance(value, dict)
            }

    def _save(self) -> None:
        _atomic_write_json(
            self.overrides_path,
            {"version": 1, "items": self._overrides},
        )

    def reload(self) -> None:
        """重新从磁盘读取序号覆盖，用于整包导入后刷新内存状态。"""

        with self._lock:
            self._overrides = {site: {} for site in WIKI_SITES}
            self._load()

    def _catalog_entries(self, site: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for entry in self.catalog.entries():
            profile = self.catalog.profile_for(entry)
            catalog_id = int(entry.get("id", 0) or 0)
            stable_key = (
                _text(entry.get("entry_key"))
                or _text(entry.get("filename"))
                or str(catalog_id)
            )
            name = _text(entry.get("name")) or "未命名盟友"
            name_en = (
                _text(profile.get("name_en"))
                or _text(entry.get("page_title"))
                or _english_from_bilingual(name)
            )
            target = (
                _text(entry.get("page_title"))
                or name_en
                or _text(entry.get("variant_key"))
                or name
            )
            rows.append(
                {
                    "site": site,
                    "site_label": WIKI_SITE_LABELS[site],
                    "key": f"catalog:{stable_key}",
                    "number": catalog_id,
                    "target": target,
                    "enabled": bool(target),
                    "label_zh": _text(profile.get("name_zh")) or name,
                    "label_en": name_en,
                    "label_ja": "",
                    "context": _text(profile.get("display_work"))
                    or _text(entry.get("source")),
                    "catalog_id": catalog_id,
                    "source_id": stable_key,
                }
            )
        return rows

    def _shinkaku_entries(self) -> List[Dict[str, Any]]:
        raw = _read_json(self.shinkaku_names_path, {})
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        rows: List[Dict[str, Any]] = []
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict):
                continue
            number = int(item.get("catalog_index", 0) or 0)
            key = _text(item.get("id")) or f"index:{number}"
            target = _text(item.get("title_ja"))
            if number <= 0 or not target:
                continue
            rows.append(
                {
                    "site": "shinkaku",
                    "site_label": WIKI_SITE_LABELS["shinkaku"],
                    "key": key,
                    "number": number,
                    "target": target,
                    "enabled": True,
                    "label_zh": _text(item.get("title_zh")) or target,
                    "label_en": _text(item.get("title_en")),
                    "label_ja": target,
                    "context": " / ".join(
                        value
                        for value in (
                            _text(item.get("game_zh")),
                            _text(item.get("section_zh")),
                        )
                        if value
                    ),
                    "catalog_id": 0,
                    "source_id": key,
                    "url": _text(item.get("url")),
                }
            )
        return rows

    def _builtins(self, site: str) -> List[Dict[str, Any]]:
        return (
            self._shinkaku_entries()
            if site == "shinkaku"
            else self._catalog_entries(site)
        )

    def entries(self, site: Any = "") -> List[Dict[str, Any]]:
        sites = WIKI_SITES if not _text(site) else (self.normalize_site(site),)
        result: List[Dict[str, Any]] = []
        with self._lock:
            for site_name in sites:
                overrides = self._overrides.get(site_name, {})
                for builtin in self._builtins(site_name):
                    row = deepcopy(builtin)
                    row["default_number"] = int(builtin["number"])
                    row["default_target"] = _text(builtin["target"])
                    row["default_enabled"] = bool(builtin["enabled"])
                    override = overrides.get(row["key"])
                    if override is not None:
                        row["number"] = int(
                            override.get("number", row["default_number"]) or 0
                        )
                        row["target"] = _text(
                            override.get("target", row["default_target"])
                        )
                        row["enabled"] = _bool(
                            override.get("enabled"), row["default_enabled"]
                        )
                        row["updated_at"] = _text(override.get("updated_at"))
                        row["updated_by"] = _text(override.get("updated_by"))
                        row["origin"] = "override"
                        row["has_override"] = True
                    else:
                        row["origin"] = "bundled"
                        row["has_override"] = False
                    result.append(row)

        counts: Dict[tuple[str, int], int] = {}
        for row in result:
            key = (row["site"], int(row["number"]))
            counts[key] = counts.get(key, 0) + 1
        for row in result:
            row["conflict"] = counts[(row["site"], int(row["number"]))] > 1
        site_order = {name: index for index, name in enumerate(WIKI_SITES)}
        result.sort(
            key=lambda row: (
                site_order[row["site"]],
                int(row["number"]),
                _text(row["label_zh"]).casefold(),
            )
        )
        return result

    def detail(self, site: Any, key: Any) -> Dict[str, Any]:
        site_name = self.normalize_site(site)
        target_key = _text(key)
        for row in self.entries(site_name):
            if row["key"] == target_key:
                return row
        raise ValueError("百科序号条目不存在")

    def resolve(self, site: Any, number: Any) -> Optional[Dict[str, Any]]:
        site_name = self.normalize_site(site)
        parsed = parse_wiki_number(number)
        if parsed is None:
            return None
        matches = [
            row
            for row in self.entries(site_name)
            if row["enabled"] and int(row["number"]) == parsed
        ]
        return matches[0] if len(matches) == 1 else None

    def save(self, payload: Mapping[str, Any], updated_by: str = "") -> Dict[str, Any]:
        site = self.normalize_site(payload.get("site"))
        key = _text(payload.get("key"))
        builtin = self.detail(site, key)
        try:
            number = int(payload.get("number", builtin["number"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("百科序号必须是正整数") from exc
        if number <= 0 or number > 999999:
            raise ValueError("百科序号必须在 1 至 999999 之间")
        target = _text(payload.get("target", builtin["target"]))
        if not target:
            raise ValueError("查询目标不能为空")
        if len(target) > 500:
            raise ValueError("查询目标不能超过 500 个字符")
        enabled = _bool(payload.get("enabled"), bool(builtin["enabled"]))

        for row in self.entries(site):
            if row["key"] != key and int(row["number"]) == number:
                raise ValueError(
                    f"序号 #{number} 已由“{row['label_zh']}”使用，请先修改另一条记录"
                )

        with self._lock:
            self._overrides.setdefault(site, {})[key] = {
                "number": number,
                "target": target,
                "enabled": enabled,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "updated_by": _text(updated_by) or "dashboard",
            }
            self._save()
        return self.detail(site, key)

    def restore(self, site: Any, key: Any) -> Dict[str, Any]:
        site_name = self.normalize_site(site)
        target_key = _text(key)
        with self._lock:
            removed = self._overrides.get(site_name, {}).pop(target_key, None)
            if removed is None:
                raise ValueError("该条目没有可恢复的覆盖版本")
            self._save()
        return self.detail(site_name, target_key)

    WIKI_IMPORT_FIELDS = ("site", "key", "number", "target", "enabled")

    def export_rows(
        self,
        site: Any = "",
        *,
        overrides_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return the merged (or override-only) index rows for a transfer export."""
        rows = self.entries(site)
        if overrides_only:
            rows = [row for row in rows if row.get("has_override")]
        return rows

    def _row_states(self):
        current: Dict[tuple, tuple] = {}
        defaults: Dict[tuple, tuple] = {}
        labels: Dict[tuple, str] = {}
        for row in self.entries():
            ident = (row["site"], row["key"])
            current[ident] = (
                int(row["number"]),
                _text(row["target"]),
                bool(row["enabled"]),
            )
            defaults[ident] = (
                int(row["default_number"]),
                _text(row["default_target"]),
                bool(row["default_enabled"]),
            )
            labels[ident] = _text(row["label_zh"]) or _text(row["target"]) or row["key"]
        return current, defaults, labels

    @staticmethod
    def _override_state(payload: Mapping[str, Any]) -> tuple:
        return (
            int(payload.get("number", 0) or 0),
            _text(payload.get("target")),
            _bool(payload.get("enabled"), True),
        )

    def _plan_import(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        replace_overrides: bool = False,
        updated_by: str = "",
    ) -> Dict[str, Any]:
        """Validate bulk rows and compute the resulting override table.

        Unlike :meth:`save` this never raises on a single bad row: a bulk import
        has to report per-row problems instead of aborting the whole file. Number
        collisions are resolved after every row has been applied, so swapping two
        numbers in one file works even though each half would clash on its own.
        """
        current, defaults, labels = self._row_states()
        with self._lock:
            existing = {
                (site, key): dict(value)
                for site, items in self._overrides.items()
                for key, value in items.items()
            }
        baseline = dict(current)
        if replace_overrides:
            for ident in existing:
                if ident in defaults:
                    baseline[ident] = defaults[ident]
        working = dict(baseline)
        desired: Dict[tuple, tuple] = {}
        warnings: List[str] = []
        skipped = 0
        total = 0

        def _warn(message: str) -> None:
            if len(warnings) < 12:
                warnings.append(message)

        for index, raw in enumerate(rows, start=1):
            total += 1
            if not isinstance(raw, Mapping):
                skipped += 1
                _warn(f"第 {index} 行不是有效记录，已跳过")
                continue
            try:
                site = self.normalize_site(raw.get("site"))
            except ValueError:
                skipped += 1
                _warn(f"第 {index} 行的百科类型无效，已跳过")
                continue
            key = _text(raw.get("key"))
            ident = (site, key)
            if not key or ident not in current:
                skipped += 1
                _warn(f"第 {index} 行的条目“{key or chr(32)}”不在当前索引中，已跳过")
                continue
            fallback = baseline[ident]
            number = parse_wiki_number(raw.get("number"))
            if number is None:
                try:
                    number = int(raw.get("number", fallback[0]) or 0)
                except (TypeError, ValueError):
                    number = 0
            if number <= 0 or number > 999999:
                skipped += 1
                _warn(f"第 {index} 行的序号必须在 1 至 999999 之间，已跳过")
                continue
            target = _text(raw.get("target")) or fallback[1]
            if not target:
                skipped += 1
                _warn(f"第 {index} 行缺少查询目标，已跳过")
                continue
            enabled = _bool(raw.get("enabled"), fallback[2])
            state = (number, target[:500], enabled)
            desired[ident] = state
            working[ident] = state

        applied = set(desired)
        for _ in range(6):
            counts: Dict[tuple, int] = {}
            for ident, state in working.items():
                bucket = (ident[0], state[0])
                counts[bucket] = counts.get(bucket, 0) + 1
            clashing = [
                ident
                for ident in desired
                if ident in applied
                and counts.get((ident[0], working[ident][0]), 0) > 1
            ]
            if not clashing:
                break
            for ident in clashing:
                _warn(
                    f"序号 #{desired[ident][0]} 与其它条目冲突，"
                    f"“{labels.get(ident, ident[1])}”已跳过"
                )
                working[ident] = baseline[ident]
                applied.discard(ident)
                skipped += 1

        overrides: Dict[str, Dict[str, Dict[str, Any]]] = {site: {} for site in WIKI_SITES}
        if not replace_overrides:
            for (site, key), payload in existing.items():
                overrides.setdefault(site, {})[key] = dict(payload)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        author = _text(updated_by) or "dashboard"
        for ident in desired:
            if ident not in applied:
                continue
            site, key = ident
            state = working[ident]
            if state == defaults.get(ident):
                overrides.setdefault(site, {}).pop(key, None)
                continue
            overrides.setdefault(site, {})[key] = {
                "number": state[0],
                "target": state[1],
                "enabled": state[2],
                "updated_at": stamp,
                "updated_by": author,
            }

        before = {ident: self._override_state(payload) for ident, payload in existing.items()}
        after = {
            (site, key): self._override_state(payload)
            for site, items in overrides.items()
            for key, payload in items.items()
        }
        added = sum(1 for ident in after if ident not in before)
        updated = sum(
            1 for ident, state in after.items() if ident in before and before[ident] != state
        )
        removed = sum(1 for ident in before if ident not in after)
        unchanged = 0
        samples: List[Dict[str, str]] = []
        for ident in desired:
            if ident not in applied:
                continue
            in_before = ident in before
            in_after = ident in after
            if in_after and in_before and before[ident] == after[ident]:
                state_name = "unchanged"
            elif not in_after and not in_before:
                state_name = "unchanged"
            elif in_after and not in_before:
                state_name = "added"
            elif in_after:
                state_name = "updated"
            else:
                state_name = "removed"
            if state_name == "unchanged":
                unchanged += 1
            elif len(samples) < 8:
                samples.append(
                    {
                        "id": f"{ident[0]}/{ident[1]}",
                        "label": f"#{working[ident][0]} {labels.get(ident, ident[1])}",
                        "state": state_name,
                    }
                )
        return {
            "overrides": overrides,
            "summary": {
                "total": total,
                "added": added,
                "updated": updated,
                "unchanged": unchanged,
                "removed": removed,
                "skipped": skipped,
            },
            "warnings": warnings,
            "samples": samples,
        }

    def preview_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        replace_overrides: bool = False,
    ) -> Dict[str, Any]:
        """Report what a bulk import would change without touching the override file."""
        plan = self._plan_import(rows, replace_overrides=replace_overrides)
        result = dict(plan["summary"])
        result["warnings"] = plan["warnings"]
        result["samples"] = plan["samples"]
        return result

    def import_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        replace_overrides: bool = False,
        updated_by: str = "",
    ) -> Dict[str, Any]:
        """Apply a bulk override import in a single atomic write."""
        plan = self._plan_import(
            rows,
            replace_overrides=replace_overrides,
            updated_by=updated_by,
        )
        with self._lock:
            self._overrides = plan["overrides"]
            self._save()
        result = dict(plan["summary"])
        result["warnings"] = plan["warnings"]
        return result

    def stats(self) -> Dict[str, Any]:
        rows = self.entries()
        sites: Dict[str, Dict[str, Any]] = {}
        for site in WIKI_SITES:
            site_rows = [row for row in rows if row["site"] == site]
            sites[site] = {
                "label": WIKI_SITE_LABELS[site],
                "total": len(site_rows),
                "enabled": sum(bool(row["enabled"]) for row in site_rows),
                "overrides": sum(bool(row["has_override"]) for row in site_rows),
                "conflicts": sum(bool(row["conflict"]) for row in site_rows),
            }
        return {
            "total": len(rows),
            "overrides": sum(bool(row["has_override"]) for row in rows),
            "conflicts": sum(bool(row["conflict"]) for row in rows),
            "sites": sites,
        }
