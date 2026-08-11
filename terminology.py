from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
DEFAULT_CATEGORY = "special"
DEFAULT_ZH_STATUS = "project"
VALID_ZH_STATUSES = {
    "official",
    "official_reused",
    "project",
    "transliterated",
    "unchanged",
    "unknown",
}
CATEGORY_PRIORITY = {
    "character": 100,
    "form": 95,
    "ability": 90,
    "work": 85,
    "location": 80,
    "mechanic": 75,
    "mode": 70,
    "title": 65,
    "special": 60,
}
AMBIGUOUS_ENGLISH_ALIASES = {
    "ability",
    "animal",
    "arena",
    "artist",
    "ball",
    "baton",
    "beam",
    "bell",
    "blade",
    "bomb",
    "box",
    "bubble",
    "button",
    "cannon",
    "capsule",
    "cell",
    "chain",
    "clean",
    "copy",
    "crash",
    "current",
    "dash",
    "deep",
    "dodge",
    "doctor",
    "drill",
    "fighter",
    "fire",
    "flash",
    "freeze",
    "gear",
    "ghost",
    "hammer",
    "head",
    "ice",
    "iron",
    "jelly",
    "jump",
    "jet",
    "laser",
    "large",
    "leaf",
    "light",
    "magic",
    "metal",
    "mirror",
    "mix",
    "needle",
    "ninja",
    "normal",
    "outfit",
    "paint",
    "parasol",
    "poison",
    "queen",
    "ranger",
    "reactor",
    "rock",
    "saucer",
    "security",
    "seaside",
    "sleep",
    "slide",
    "space",
    "spark",
    "spear",
    "spring",
    "star",
    "spider",
    "staff",
    "stone",
    "sword",
    "throw",
    "tornado",
    "top",
    "trophy",
    "ufo",
    "water",
    "wheel",
    "whip",
    "wing",
    "wire",
    "wood",
    "wrestler",
    "yo-yo",
}
URL_RE = re.compile(r"(?:https?://|ftp://|www\.)[^\s<>\]\[)）]+", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"__KTERM_[A-F0-9]{8}_[0-9]{4}__")
ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]")
SPACE_RE = re.compile(r"\s+")
CSV_FIELDS = (
    "term_id",
    "category",
    "zh_cn",
    "en",
    "ja",
    "aliases_zh",
    "aliases_en",
    "aliases_ja",
    "zh_status",
    "sources",
    "notes",
    "priority",
    "enabled",
    "match_case",
)


class TerminologyError(ValueError):
    pass


class TerminologyPlaceholderError(TerminologyError):
    pass


def _normalise_space(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _normalise_alias(value: str) -> str:
    return _normalise_space(value).lower()


def _unique_text(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalise_space(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _string_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[\r\n|]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = ()
    return _unique_text(values)


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


@dataclass(frozen=True)
class TerminologyEntry:
    term_id: str
    category: str = DEFAULT_CATEGORY
    zh_cn: str = ""
    en: str = ""
    ja: str = ""
    aliases_zh: tuple[str, ...] = field(default_factory=tuple)
    aliases_en: tuple[str, ...] = field(default_factory=tuple)
    aliases_ja: tuple[str, ...] = field(default_factory=tuple)
    zh_status: str = DEFAULT_ZH_STATUS
    sources: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    priority: int = 100
    enabled: bool = True
    match_case: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TerminologyEntry":
        term_id = _normalise_space(raw.get("term_id"))
        if not term_id:
            raise TerminologyError("术语 ID 不能为空")
        if len(term_id) > 180:
            raise TerminologyError("术语 ID 不能超过 180 个字符")
        category = _normalise_space(raw.get("category")) or DEFAULT_CATEGORY
        zh_cn = _normalise_space(raw.get("zh_cn"))
        en = _normalise_space(raw.get("en"))
        ja = _normalise_space(raw.get("ja"))
        if not any((zh_cn, en, ja)):
            raise TerminologyError("中文、英文和日文名称不能全部为空")
        status = _normalise_space(raw.get("zh_status")) or DEFAULT_ZH_STATUS
        if status not in VALID_ZH_STATUSES:
            raise TerminologyError(f"不支持的中文名称状态：{status}")
        try:
            priority = max(-1000, min(1000, int(raw.get("priority", 100))))
        except (TypeError, ValueError) as exc:
            raise TerminologyError("术语优先级必须是整数") from exc
        notes = str(raw.get("notes") or "").strip()
        if len(notes) > 5000:
            raise TerminologyError("术语备注不能超过 5000 个字符")
        return cls(
            term_id=term_id,
            category=category,
            zh_cn=zh_cn,
            en=en,
            ja=ja,
            aliases_zh=_string_list(raw.get("aliases_zh")),
            aliases_en=_string_list(raw.get("aliases_en")),
            aliases_ja=_string_list(raw.get("aliases_ja")),
            zh_status=status,
            sources=_string_list(raw.get("sources")),
            notes=notes,
            priority=priority,
            enabled=_bool_value(raw.get("enabled"), True),
            match_case=_bool_value(raw.get("match_case"), False),
        )

    @property
    def canonical_label(self) -> str:
        if self.zh_cn and self.en:
            if self.zh_cn.casefold() == self.en.casefold():
                return self.en
            return f"{self.zh_cn}（{self.en}）"
        return self.zh_cn or self.en or self.ja

    def language_aliases(self) -> dict[str, tuple[str, ...]]:
        canonical_variants: tuple[str, ...] = ()
        if self.zh_cn and self.en and self.zh_cn.casefold() != self.en.casefold():
            canonical_variants = (
                self.canonical_label,
                f"{self.zh_cn} ({self.en})",
            )
        english_aliases: list[str] = []
        for value in (self.en, *self.aliases_en):
            text = _normalise_space(value)
            if (
                text
                and text.casefold() in AMBIGUOUS_ENGLISH_ALIASES
                and text[:1].islower()
            ):
                # Source infoboxes occasionally store a generic English name
                # in lowercase. Keep the title form matchable while avoiding
                # accidental replacements in ordinary prose.
                text = text[:1].upper() + text[1:]
            english_aliases.append(text)
        return {
            "zh": _unique_text((self.zh_cn, *self.aliases_zh, *canonical_variants)),
            "en": _unique_text(english_aliases),
            "ja": _unique_text((self.ja, *self.aliases_ja)),
        }

    def aliases(self) -> tuple[str, ...]:
        by_language = self.language_aliases()
        return _unique_text((*by_language["zh"], *by_language["en"], *by_language["ja"]))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "category": self.category,
            "zh_cn": self.zh_cn,
            "en": self.en,
            "ja": self.ja,
            "aliases_zh": list(self.aliases_zh),
            "aliases_en": list(self.aliases_en),
            "aliases_ja": list(self.aliases_ja),
            "zh_status": self.zh_status,
            "sources": list(self.sources),
            "notes": self.notes,
            "priority": self.priority,
            "enabled": self.enabled,
            "match_case": self.match_case,
        }


@dataclass(frozen=True)
class PlaceholderBinding:
    token: str
    term_id: str
    category: str
    label: str
    count: int


@dataclass(frozen=True)
class ProtectedTerminologyText:
    source_text: str
    protected_text: str
    bindings: tuple[PlaceholderBinding, ...]
    revision: str

    @property
    def matched_terms(self) -> int:
        return len(self.bindings)

    @property
    def matched_occurrences(self) -> int:
        return sum(binding.count for binding in self.bindings)

    def glossary(self) -> str:
        if not self.bindings:
            return ""
        lines = [
            "术语占位符（每个占位符必须逐字、逐次保留，不要翻译或改写）："
        ]
        for binding in self.bindings:
            lines.append(
                f"{binding.token} = {binding.label} [{binding.category}]"
            )
        return "\n".join(lines)

    def validate(self, value: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for binding in self.bindings:
            found = str(value or "").count(binding.token)
            if found != binding.count:
                errors.append(
                    f"{binding.token}: expected={binding.count}, actual={found}"
                )
        return not errors, errors

    def restore(self, value: str, *, strict: bool = True) -> str:
        text = str(value or "")
        if strict:
            valid, errors = self.validate(text)
            if not valid:
                raise TerminologyPlaceholderError(
                    "术语占位符数量不一致：" + "; ".join(errors)
                )
        for binding in self.bindings:
            text = text.replace(binding.token, binding.label)
        return text

    def restore_object(self, value: Any, *, strict: bool = True) -> Any:
        if strict:
            serialised = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            valid, errors = self.validate(serialised)
            if not valid:
                raise TerminologyPlaceholderError(
                    "结构化翻译中的术语占位符数量不一致：" + "; ".join(errors)
                )

        def restore_item(item: Any) -> Any:
            if isinstance(item, str):
                return self.restore(item, strict=False)
            if isinstance(item, list):
                return [restore_item(child) for child in item]
            if isinstance(item, tuple):
                return tuple(restore_item(child) for child in item)
            if isinstance(item, dict):
                return {key: restore_item(child) for key, child in item.items()}
            return item

        return restore_item(value)

    def canonical_source(self) -> str:
        return self.restore(self.protected_text, strict=True)


@dataclass(frozen=True)
class _AliasCandidate:
    alias: str
    normalised: str
    entry: TerminologyEntry
    exact_case: bool
    language: str


@dataclass(frozen=True)
class TerminologyMatch:
    start: int
    end: int
    alias: str
    language: str
    entry: TerminologyEntry


class _AhoCorasickMatcher:
    def __init__(self, entries: Iterable[TerminologyEntry]):
        self._next: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._outputs: list[list[_AliasCandidate]] = [[]]
        for entry in entries:
            if not entry.enabled:
                continue
            for language, aliases in entry.language_aliases().items():
                for alias in aliases:
                    if not self._matchable_alias(alias):
                        continue
                    exact_case = entry.match_case or (
                        language == "en"
                        and alias.casefold() in AMBIGUOUS_ENGLISH_ALIASES
                    )
                    self._insert(
                        _AliasCandidate(
                            alias=alias,
                            normalised=_normalise_alias(alias),
                            entry=entry,
                            exact_case=exact_case,
                            language=language,
                        )
                    )
        self._build_failures()

    @staticmethod
    def _matchable_alias(alias: str) -> bool:
        text = _normalise_space(alias)
        if len(text) < 2 or text.isdigit():
            return False
        return not PLACEHOLDER_RE.fullmatch(text)

    def _insert(self, candidate: _AliasCandidate) -> None:
        state = 0
        for char in candidate.normalised:
            target = self._next[state].get(char)
            if target is None:
                target = len(self._next)
                self._next[state][char] = target
                self._next.append({})
                self._fail.append(0)
                self._outputs.append([])
            state = target
        duplicate = any(
            row.entry.term_id == candidate.entry.term_id
            and row.alias == candidate.alias
            for row in self._outputs[state]
        )
        if not duplicate:
            self._outputs[state].append(candidate)

    def _build_failures(self) -> None:
        queue: deque[int] = deque()
        for state in self._next[0].values():
            queue.append(state)
        while queue:
            state = queue.popleft()
            for char, target in self._next[state].items():
                queue.append(target)
                fallback = self._fail[state]
                while fallback and char not in self._next[fallback]:
                    fallback = self._fail[fallback]
                self._fail[target] = self._next[fallback].get(char, 0)
                inherited = self._outputs[self._fail[target]]
                if inherited:
                    self._outputs[target].extend(inherited)

    @staticmethod
    def _ascii_boundary_ok(text: str, start: int, end: int, alias: str) -> bool:
        if alias and alias[0].isascii() and ASCII_WORD_RE.fullmatch(alias[0]):
            if start > 0 and ASCII_WORD_RE.fullmatch(text[start - 1]):
                return False
        if alias and alias[-1].isascii() and ASCII_WORD_RE.fullmatch(alias[-1]):
            if end < len(text) and ASCII_WORD_RE.fullmatch(text[end]):
                return False
        return True

    @staticmethod
    def _excluded_intervals(text: str) -> list[tuple[int, int]]:
        intervals = [match.span() for match in URL_RE.finditer(text)]
        intervals.extend(match.span() for match in PLACEHOLDER_RE.finditer(text))
        return sorted(intervals)

    @staticmethod
    def _inside_excluded(
        start: int, end: int, intervals: Sequence[tuple[int, int]]
    ) -> bool:
        for left, right in intervals:
            if right <= start:
                continue
            if left >= end:
                return False
            return True
        return False

    @staticmethod
    def _rank(match: TerminologyMatch) -> tuple[int, int, int, str]:
        return (
            match.end - match.start,
            match.entry.priority,
            CATEGORY_PRIORITY.get(match.entry.category, 0),
            match.entry.term_id,
        )

    def find(self, text: str) -> list[TerminologyMatch]:
        if not text:
            return []
        normalised = text.lower()
        excluded = self._excluded_intervals(text)
        candidates: list[TerminologyMatch] = []
        state = 0
        for index, char in enumerate(normalised):
            while state and char not in self._next[state]:
                state = self._fail[state]
            state = self._next[state].get(char, 0)
            if not self._outputs[state]:
                continue
            for output in self._outputs[state]:
                end = index + 1
                start = end - len(output.normalised)
                if start < 0:
                    continue
                actual = text[start:end]
                if output.exact_case and actual != output.alias:
                    continue
                if not self._ascii_boundary_ok(text, start, end, output.alias):
                    continue
                if self._inside_excluded(start, end, excluded):
                    continue
                candidates.append(
                    TerminologyMatch(
                        start=start,
                        end=end,
                        alias=actual,
                        language=output.language,
                        entry=output.entry,
                    )
                )
        if not candidates:
            return []
        by_start: dict[int, list[TerminologyMatch]] = {}
        for candidate in candidates:
            by_start.setdefault(candidate.start, []).append(candidate)
        result: list[TerminologyMatch] = []
        cursor = 0
        for start in sorted(by_start):
            if start < cursor:
                continue
            best = max(by_start[start], key=self._rank)
            result.append(best)
            cursor = best.end
        return result


class KirbyTerminologyStore:
    """Versioned terminology library with persistent server-side overrides."""

    def __init__(
        self,
        bundled_path: Path | str,
        overrides_path: Path | str,
        *,
        protection_cache_size: int = 256,
    ) -> None:
        self.bundled_path = Path(bundled_path)
        self.overrides_path = Path(overrides_path)
        self.protection_cache_size = max(16, int(protection_cache_size))
        self._lock = threading.RLock()
        self._base: dict[str, TerminologyEntry] = {}
        self._overrides: dict[str, TerminologyEntry] = {}
        self._entries: dict[str, TerminologyEntry] = {}
        self._matcher = _AhoCorasickMatcher(())
        self._revision = "empty"
        self._protect_cache: OrderedDict[str, ProtectedTerminologyText] = OrderedDict()
        self.reload()

    @staticmethod
    def _read_entries(path: Path) -> dict[str, TerminologyEntry]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TerminologyError(f"无法读取名称库文件 {path}: {exc}") from exc
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        if isinstance(items, Mapping):
            rows = []
            for term_id, value in items.items():
                if not isinstance(value, Mapping):
                    continue
                rows.append({"term_id": term_id, **value})
        elif isinstance(items, list):
            rows = items
        else:
            raise TerminologyError(f"名称库文件结构无效：{path}")
        result: dict[str, TerminologyEntry] = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                continue
            try:
                entry = TerminologyEntry.from_mapping(row)
            except TerminologyError as exc:
                raise TerminologyError(f"{path} 第 {index} 条术语无效：{exc}") from exc
            result[entry.term_id] = entry
        return result

    @staticmethod
    def _revision_for(entries: Iterable[TerminologyEntry]) -> str:
        payload = [
            entry.to_mapping()
            for entry in sorted(entries, key=lambda item: item.term_id.casefold())
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    def reload(self) -> str:
        base = self._read_entries(self.bundled_path)
        overrides = self._read_entries(self.overrides_path)
        entries = {**base, **overrides}
        matcher = _AhoCorasickMatcher(entries.values())
        revision = self._revision_for(entries.values())
        with self._lock:
            self._base = base
            self._overrides = overrides
            self._entries = entries
            self._matcher = matcher
            self._revision = revision
            self._protect_cache.clear()
        return revision

    @property
    def revision(self) -> str:
        with self._lock:
            return self._revision

    def entries(self) -> list[TerminologyEntry]:
        with self._lock:
            return sorted(
                self._entries.values(),
                key=lambda item: (
                    item.category,
                    item.zh_cn.casefold(),
                    item.en.casefold(),
                    item.term_id.casefold(),
                ),
            )

    def entry(self, term_id: str) -> TerminologyEntry | None:
        with self._lock:
            return self._entries.get(str(term_id or "").strip())

    def origin(self, term_id: str) -> str:
        with self._lock:
            if term_id in self._overrides:
                return "override" if term_id in self._base else "custom"
            return "bundled" if term_id in self._base else "unknown"

    def has_override(self, term_id: str) -> bool:
        with self._lock:
            return term_id in self._overrides

    def _write_overrides(self, entries: Mapping[str, TerminologyEntry]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": [
                entry.to_mapping()
                for entry in sorted(entries.values(), key=lambda item: item.term_id)
            ],
        }
        _atomic_write_json(self.overrides_path, payload)

    def upsert(self, raw: Mapping[str, Any]) -> TerminologyEntry:
        payload = dict(raw)
        term_id = _normalise_space(payload.get("term_id"))
        if not term_id:
            term_id = f"custom:{uuid.uuid4().hex}"
            payload["term_id"] = term_id
        entry = TerminologyEntry.from_mapping(payload)
        with self._lock:
            overrides = dict(self._overrides)
            overrides[entry.term_id] = entry
            self._write_overrides(overrides)
        self.reload()
        return self.entry(entry.term_id) or entry

    def restore(self, term_id: str) -> TerminologyEntry | None:
        key = _normalise_space(term_id)
        if not key:
            raise TerminologyError("术语 ID 不能为空")
        with self._lock:
            if key not in self._overrides:
                raise TerminologyError("该术语没有可恢复的自定义覆盖")
            overrides = dict(self._overrides)
            overrides.pop(key, None)
            self._write_overrides(overrides)
        self.reload()
        return self.entry(key)

    def import_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        replace_overrides: bool = False,
    ) -> dict[str, int]:
        parsed: dict[str, TerminologyEntry] = {}
        for index, row in enumerate(rows, start=1):
            payload = dict(row)
            if not _normalise_space(payload.get("term_id")):
                payload["term_id"] = f"custom:{uuid.uuid4().hex}"
            try:
                entry = TerminologyEntry.from_mapping(payload)
            except TerminologyError as exc:
                raise TerminologyError(f"导入文件第 {index} 条术语无效：{exc}") from exc
            parsed[entry.term_id] = entry
        with self._lock:
            overrides = {} if replace_overrides else dict(self._overrides)
            overrides.update(parsed)
            self._write_overrides(overrides)
        self.reload()
        return {"imported": len(parsed), "overrides": len(overrides)}

    def import_json(self, data: bytes | str, *, replace_overrides: bool = False) -> dict[str, int]:
        try:
            text = data.decode("utf-8-sig") if isinstance(data, bytes) else str(data)
            raw = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TerminologyError(f"名称库 JSON 无效：{exc}") from exc
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        if isinstance(items, Mapping):
            rows = [
                {"term_id": term_id, **dict(value)}
                for term_id, value in items.items()
                if isinstance(value, Mapping)
            ]
        elif isinstance(items, list):
            rows = items
        else:
            raise TerminologyError("名称库 JSON 必须包含 items 数组或对象")
        return self.import_rows(rows, replace_overrides=replace_overrides)

    def import_csv(self, data: bytes | str, *, replace_overrides: bool = False) -> dict[str, int]:
        try:
            text = data.decode("utf-8-sig") if isinstance(data, bytes) else str(data)
        except UnicodeDecodeError as exc:
            raise TerminologyError("名称库 CSV 必须使用 UTF-8 编码") from exc
        reader = csv.DictReader(StringIO(text))
        rows: list[dict[str, Any]] = []
        for raw in reader:
            row = dict(raw)
            for key in ("aliases_zh", "aliases_en", "aliases_ja", "sources"):
                row[key] = _string_list(row.get(key))
            rows.append(row)
        return self.import_rows(rows, replace_overrides=replace_overrides)

    def export_json(self, *, overrides_only: bool = False) -> bytes:
        with self._lock:
            entries = self._overrides if overrides_only else self._entries
            payload = {
                "schema_version": SCHEMA_VERSION,
                "revision": self._revision,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "scope": "overrides" if overrides_only else "merged",
                "items": [
                    entry.to_mapping()
                    for entry in sorted(entries.values(), key=lambda item: item.term_id)
                ],
            }
        return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def export_csv(self, *, overrides_only: bool = False) -> bytes:
        with self._lock:
            entries = list(
                (self._overrides if overrides_only else self._entries).values()
            )
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in sorted(entries, key=lambda item: item.term_id):
            row = entry.to_mapping()
            for key in ("aliases_zh", "aliases_en", "aliases_ja", "sources"):
                row[key] = " | ".join(row[key])
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})
        return ("\ufeff" + output.getvalue()).encode("utf-8")

    def find(self, text: str) -> list[TerminologyMatch]:
        with self._lock:
            matcher = self._matcher
        return matcher.find(str(text or ""))

    def protect(self, text: str) -> ProtectedTerminologyText:
        source = str(text or "")
        with self._lock:
            revision = self._revision
            cache_key = hashlib.sha256(
                f"{revision}\0{source}".encode("utf-8")
            ).hexdigest()
            cached = self._protect_cache.get(cache_key)
            if cached is not None:
                self._protect_cache.move_to_end(cache_key)
                return cached
            matcher = self._matcher
        matches = matcher.find(source)
        if not matches:
            protected = ProtectedTerminologyText(source, source, (), revision)
        else:
            nonce = hashlib.sha256(
                f"{revision}\0{source}".encode("utf-8")
            ).hexdigest()[:8].upper()
            token_by_id: dict[str, str] = {}
            binding_data: OrderedDict[str, dict[str, Any]] = OrderedDict()
            pieces: list[str] = []
            cursor = 0
            for match in matches:
                pieces.append(source[cursor : match.start])
                token = token_by_id.get(match.entry.term_id)
                if token is None:
                    token = f"__KTERM_{nonce}_{len(token_by_id) + 1:04d}__"
                    token_by_id[match.entry.term_id] = token
                    binding_data[token] = {
                        "term_id": match.entry.term_id,
                        "category": match.entry.category,
                        "label": match.entry.canonical_label,
                        "count": 0,
                    }
                pieces.append(token)
                binding_data[token]["count"] += 1
                cursor = match.end
            pieces.append(source[cursor:])
            bindings = tuple(
                PlaceholderBinding(token=token, **value)
                for token, value in binding_data.items()
            )
            protected = ProtectedTerminologyText(
                source_text=source,
                protected_text="".join(pieces),
                bindings=bindings,
                revision=revision,
            )
        with self._lock:
            self._protect_cache[cache_key] = protected
            self._protect_cache.move_to_end(cache_key)
            while len(self._protect_cache) > self.protection_cache_size:
                self._protect_cache.popitem(last=False)
        return protected

    def canonicalize(self, text: str) -> str:
        protected = self.protect(text)
        return protected.canonical_source()

    def canonicalize_object(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.canonicalize(value)
        if isinstance(value, list):
            return [self.canonicalize_object(child) for child in value]
        if isinstance(value, tuple):
            return tuple(self.canonicalize_object(child) for child in value)
        if isinstance(value, dict):
            return {
                key: self.canonicalize_object(child) for key, child in value.items()
            }
        return value

    def conflicts(self) -> list[dict[str, Any]]:
        alias_index: dict[str, dict[str, Any]] = {}
        for entry in self.entries():
            if not entry.enabled:
                continue
            for language, aliases in entry.language_aliases().items():
                for alias in aliases:
                    if not _AhoCorasickMatcher._matchable_alias(alias):
                        continue
                    key = _normalise_alias(alias)
                    item = alias_index.setdefault(
                        key,
                        {"alias": alias, "languages": set(), "entries": {}},
                    )
                    item["languages"].add(language)
                    item["entries"][entry.term_id] = entry.canonical_label
        conflicts: list[dict[str, Any]] = []
        for item in alias_index.values():
            if len(item["entries"]) < 2:
                continue
            conflicts.append(
                {
                    "alias": item["alias"],
                    "languages": sorted(item["languages"]),
                    "entries": [
                        {"term_id": term_id, "label": label}
                        for term_id, label in sorted(item["entries"].items())
                    ],
                }
            )
        return sorted(
            conflicts,
            key=lambda item: (-len(item["entries"]), item["alias"].casefold()),
        )

    def stats(self) -> dict[str, Any]:
        entries = self.entries()
        category_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for entry in entries:
            category_counts[entry.category] = category_counts.get(entry.category, 0) + 1
            status_counts[entry.zh_status] = status_counts.get(entry.zh_status, 0) + 1
        with self._lock:
            override_count = len(self._overrides)
            base_ids = set(self._base)
            custom_count = sum(term_id not in base_ids for term_id in self._overrides)
        return {
            "revision": self.revision,
            "entries": len(entries),
            "enabled": sum(entry.enabled for entry in entries),
            "missing_zh": sum(not entry.zh_cn for entry in entries),
            "missing_en": sum(not entry.en for entry in entries),
            "missing_ja": sum(not entry.ja for entry in entries),
            "overrides": override_count,
            "custom": custom_count,
            "conflicts": len(self.conflicts()),
            "categories": category_counts,
            "statuses": status_counts,
        }


def terminology_document(entries: Iterable[TerminologyEntry], *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = sorted(entries, key=lambda item: (item.category, item.term_id.casefold()))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [entry.to_mapping() for entry in rows],
    }
    if metadata:
        payload["metadata"] = dict(metadata)
    payload["revision"] = KirbyTerminologyStore._revision_for(rows)
    return payload


__all__ = [
    "CSV_FIELDS",
    "KirbyTerminologyStore",
    "ProtectedTerminologyText",
    "TerminologyEntry",
    "TerminologyError",
    "TerminologyMatch",
    "TerminologyPlaceholderError",
    "terminology_document",
]
