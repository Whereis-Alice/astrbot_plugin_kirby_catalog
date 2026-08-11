from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
if str(PLUGIN_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT.parent))

from astrbot_plugin_kirby_catalog.terminology import (  # noqa: E402
    AMBIGUOUS_ENGLISH_ALIASES,
    KirbyTerminologyStore,
    TerminologyEntry,
    terminology_document,
)


BWIKI_API_URL = "https://wiki.biligame.com/kirby/api.php"
USER_AGENT = (
    "astrbot-plugin-kirby-catalog-terminology-builder/1.0 "
    "(+https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)"
)
DEFAULT_CATALOG_PATH = PLUGIN_ROOT / "resources" / "catalog_profiles.json"
DEFAULT_SHINKAKU_PATH = PLUGIN_ROOT / "resources" / "shinkaku_page_names.json"
DEFAULT_OFFICIAL_NOTES_PATH = WORKSPACE_ROOT / "星之卡比官方资料.md"
DEFAULT_WIKIRBY_CACHE_PATH = PLUGIN_ROOT / ".tmp" / "terminology" / "wikirby_pages.json.gz"
DEFAULT_FORMS_PATH = PLUGIN_ROOT / ".tmp" / "terminology" / "catalog_forms.json"
DEFAULT_BWIKI_CACHE_PATH = PLUGIN_ROOT / ".tmp" / "terminology" / "bwiki_pages.json.gz"
DEFAULT_JSON_PATH = PLUGIN_ROOT / "resources" / "kirby_terminology.json"
DEFAULT_CSV_PATH = PLUGIN_ROOT / "resources" / "kirby_terminology.csv"
DEFAULT_AUDIT_PATH = PLUGIN_ROOT / "resources" / "kirby_terminology_audit.json"

SOURCE_RANK = {
    "curated-official-notes": 100,
    "wikirby-names": 95,
    "catalog-profiles": 90,
    "catalog-forms": 88,
    "shinkaku-page-names": 82,
    "bwiki-infobox": 70,
    "bwiki-table": 65,
}
STATUS_RANK = {
    "official": 100,
    "official_reused": 90,
    "project": 70,
    "transliterated": 55,
    "unchanged": 40,
    "unknown": 10,
}
CATEGORY_RANK = {
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
PROJECT_NAME_MARKERS = (
    "{{民译",
    "暂无官方译名",
    "暂时使用民译",
    "非官译",
    "暂译",
)
NAMES_HEADING_RE = re.compile(
    r"^==\s*(?:Names in other languages|Other languages|Names in other "
    r"language|Language names)\s*==\s*$",
    re.IGNORECASE | re.MULTILINE,
)
SPACE_RE = re.compile(r"\s+")
ASCII_RE = re.compile(r"[A-Za-z]")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _normalise(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", _normalise(value).casefold())


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalise(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _clean_wikitext(value: Any, *, page_title: str = "") -> str:
    text = str(value or "")
    if page_title:
        text = re.sub(r"\{\{\s*PAGENAME\s*\}\}", page_title, text, flags=re.I)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", "", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"\[\[(?:File|文件|Image):[^]]+\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^]|]+\|([^]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^]#]+)(?:#[^]]*)?\]\]", r"\1", text)
    text = re.sub(r"\[(?:https?://\S+)\s+([^]]+)\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    for _ in range(8):
        updated = re.sub(r"\{\{(?:lang|nowrap|small|ruby|furi)\s*\|\s*([^{}|]+)(?:\|[^{}]*)?\}\}", r"\1", text, flags=re.I)
        updated = re.sub(r"\{\{[^{}]*\}\}", "", updated)
        if updated == text:
            break
        text = updated
    text = html.unescape(text).replace("\xa0", " ")
    lines = [_normalise(line).strip(" -/\t") for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _first_name(value: Any, *, language: str, page_title: str = "") -> str:
    text = _clean_wikitext(value, page_title=page_title)
    parts = _unique(
        part.strip()
        for line in text.splitlines()
        for part in re.split(r"\s+/\s+|\s+or\s+", line, flags=re.I)
    )
    if language == "zh":
        parts = [part for part in parts if CJK_RE.search(part)]
    elif language == "ja":
        parts = [part for part in parts if JAPANESE_RE.search(part)]
    elif language == "en":
        parts = [part for part in parts if ASCII_RE.search(part)]
    if not parts:
        return ""
    value = parts[0]
    value = re.sub(r"\s*[（(][^()（）]*(?:roman|literal|lit\.)[^()（）]*[)）]\s*$", "", value, flags=re.I)
    return _normalise(value)


def _balanced_template(text: str, start: int) -> str:
    depth = 0
    index = start
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                return text[start:index]
            continue
        index += 1
    return ""


def _iter_templates(text: str) -> Iterator[str]:
    for match in re.finditer(r"\{\{", text or ""):
        block = _balanced_template(text, match.start())
        if block:
            yield block


def _template_fields(block: str) -> tuple[str, dict[str, str]]:
    first_line = block[2:].splitlines()[0] if block.startswith("{{") else ""
    template_name = first_line.split("|", 1)[0].strip(" }\t")
    fields: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []
    for raw_line in block.splitlines()[1:]:
        match = re.match(r"^\|+\s*([^=|\n]+?)\s*=\s*(.*)$", raw_line)
        if match:
            if current_key:
                fields[current_key] = "\n".join(current_lines).strip()
            current_key = _normalise(match.group(1))
            current_lines = [match.group(2)]
        elif current_key:
            current_lines.append(raw_line)
    if current_key:
        fields[current_key] = "\n".join(current_lines).strip()
    return template_name, fields


def _names_fields(wikitext: str) -> dict[str, str]:
    heading = NAMES_HEADING_RE.search(wikitext or "")
    if not heading:
        return {}
    section_start = heading.end()
    next_heading = re.search(
        r"^==(?!=)[^\n]*?(?<!\=)==\s*$",
        wikitext[section_start:],
        flags=re.MULTILINE,
    )
    section_end = section_start + next_heading.start() if next_heading else len(wikitext)
    section = wikitext[section_start:section_end]
    match = re.search(r"\{\{\s*Names\b", section, flags=re.I)
    if not match:
        return {}
    _name, fields = _template_fields(_balanced_template(section, match.start()))
    return fields


def _clean_zh_label(value: Any) -> tuple[str, list[str], str]:
    raw = _normalise(value)
    if not raw:
        return "", [], "unknown"
    aliases: list[str] = []
    status = "project"
    explicit = re.search(
        r"(?:官方简中|简中主名|简中常用名?|简中检索名)\s*[:：]\s*([^()（）；;]+)",
        raw,
    )
    base = re.sub(r"\s*[（(].*[)）]\s*$", "", raw).strip()
    if explicit:
        candidate = explicit.group(1).strip()
        if ASCII_RE.search(base) and CJK_RE.search(candidate):
            aliases.append(base)
            base = candidate
    for match in re.finditer(r"(?:官方繁中|港台官方名|繁中)\s*[:：]\s*([^()（）；;]+)", raw):
        aliases.append(match.group(1).strip())
    if "官方" in raw and "非官译" not in raw:
        status = "official"
    elif "常用" in raw or "民译" in raw or "检索名" in raw:
        status = "project"
    if not CJK_RE.search(base) and ASCII_RE.search(base):
        status = "unchanged"
    return base, _unique(aliases), status


def _category_from_context(value: str) -> str:
    text = _normalise(value).casefold()
    if any(token in text for token in ("作品", "游戏", "game", "novel", "anime")):
        return "work"
    if any(token in text for token in ("能力", "形态", "形態", "mouthful", "copy ability", "可操作")):
        return "ability"
    if any(token in text for token in ("地点", "舞台", "location", "world", "planet")):
        return "location"
    if any(token in text for token in ("机制", "动作", "术语", "mechanic", "system")):
        return "mechanic"
    if any(token in text for token in ("模式", "挑战", "mode", "route")):
        return "mode"
    if any(token in text for token in ("角色", "敌", "魔王", "boss", "character", "盟友", "杂兵")):
        return "character"
    return "special"


def _identity(en: str, ja: str, zh: str) -> str:
    return _normalise_key(en) or _normalise_key(ja) or _normalise_key(zh)


@dataclass
class MutableTerm:
    identity: str
    category: str = "special"
    zh_cn: str = ""
    en: str = ""
    ja: str = ""
    zh_status: str = "unknown"
    aliases_zh: set[str] = field(default_factory=set)
    aliases_en: set[str] = field(default_factory=set)
    aliases_ja: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    notes: set[str] = field(default_factory=set)
    priority: int = 100
    field_ranks: dict[str, tuple[int, int]] = field(default_factory=dict)

    def merge(
        self,
        *,
        zh: str = "",
        en: str = "",
        ja: str = "",
        category: str = "special",
        zh_status: str = "project",
        source: str,
        aliases_zh: Sequence[str] = (),
        aliases_en: Sequence[str] = (),
        aliases_ja: Sequence[str] = (),
        note: str = "",
        priority: int = 100,
    ) -> None:
        source_rank = SOURCE_RANK.get(source, 50)
        status_rank = STATUS_RANK.get(zh_status, 0)
        self.sources.add(source)
        if note:
            self.notes.add(_normalise(note))
        if CATEGORY_RANK.get(category, 0) > CATEGORY_RANK.get(self.category, 0):
            self.category = category
        self.priority = max(self.priority, priority)
        for field_name, value, rank in (
            ("zh_cn", zh, (status_rank, source_rank)),
            ("en", en, (source_rank, len(en))),
            ("ja", ja, (source_rank, len(ja))),
        ):
            value = _normalise(value)
            if not value:
                continue
            old_value = str(getattr(self, field_name) or "")
            old_rank = self.field_ranks.get(field_name, (-1, -1))
            if old_value and old_value.casefold() != value.casefold():
                getattr(self, f"aliases_{'zh' if field_name == 'zh_cn' else field_name}").add(value)
            if not old_value or rank > old_rank:
                if old_value and old_value.casefold() != value.casefold():
                    getattr(self, f"aliases_{'zh' if field_name == 'zh_cn' else field_name}").add(old_value)
                setattr(self, field_name, value)
                self.field_ranks[field_name] = rank
                if field_name == "zh_cn":
                    self.zh_status = zh_status
        self.aliases_zh.update(_unique(aliases_zh))
        self.aliases_en.update(_unique(aliases_en))
        self.aliases_ja.update(_unique(aliases_ja))

    def to_entry(self, term_id: str) -> TerminologyEntry:
        if not self.zh_cn:
            self.zh_cn = self.en or self.ja
            self.zh_status = "unchanged"
        if not self.en and ASCII_RE.search(self.zh_cn):
            self.en = self.zh_cn
        return TerminologyEntry.from_mapping(
            {
                "term_id": term_id,
                "category": self.category,
                "zh_cn": self.zh_cn,
                "en": self.en,
                "ja": self.ja,
                "aliases_zh": sorted(self.aliases_zh - {self.zh_cn}, key=str.casefold),
                "aliases_en": sorted(self.aliases_en - {self.en}, key=str.casefold),
                "aliases_ja": sorted(self.aliases_ja - {self.ja}, key=str.casefold),
                "zh_status": self.zh_status,
                "sources": sorted(self.sources),
                "notes": "；".join(sorted(self.notes))[:5000],
                "priority": self.priority,
                "enabled": True,
                "match_case": self.en.casefold() in AMBIGUOUS_ENGLISH_ALIASES,
            }
        )


class TerminologyBuilder:
    def __init__(self) -> None:
        self.terms: dict[str, MutableTerm] = {}
        self.alias_index: dict[str, set[str]] = defaultdict(set)
        self.input_counts: Counter[str] = Counter()

    def add(
        self,
        *,
        zh: str = "",
        en: str = "",
        ja: str = "",
        category: str = "special",
        zh_status: str = "project",
        source: str,
        aliases_zh: Sequence[str] = (),
        aliases_en: Sequence[str] = (),
        aliases_ja: Sequence[str] = (),
        note: str = "",
        priority: int = 100,
        distinct_key: str = "",
    ) -> None:
        zh, en, ja = _normalise(zh), _normalise(en), _normalise(ja)
        if not any((zh, en, ja, *aliases_zh, *aliases_en, *aliases_ja)):
            return
        candidates: set[str] = set()
        for value in (en, ja, zh, *aliases_en, *aliases_ja, *aliases_zh):
            key = _normalise_key(value)
            if key:
                candidates.update(self.alias_index.get(key, set()))
        identity = _normalise_key(distinct_key) if distinct_key else ""
        if not identity:
            identity = sorted(candidates)[0] if candidates else _identity(en, ja, zh)
        if not identity:
            return
        term = self.terms.setdefault(identity, MutableTerm(identity=identity))
        term.merge(
            zh=zh,
            en=en,
            ja=ja,
            category=category,
            zh_status=zh_status,
            source=source,
            aliases_zh=aliases_zh,
            aliases_en=aliases_en,
            aliases_ja=aliases_ja,
            note=note,
            priority=priority,
        )
        for value in (zh, en, ja, *aliases_zh, *aliases_en, *aliases_ja):
            key = _normalise_key(value)
            if key:
                self.alias_index[key].add(identity)
        self.input_counts[source] += 1

    def entries(self) -> list[TerminologyEntry]:
        used_ids: set[str] = set()
        output: list[TerminologyEntry] = []
        for identity, term in sorted(self.terms.items()):
            stem_source = term.en or term.ja or term.zh_cn or identity
            stem = re.sub(r"[^a-z0-9]+", "-", stem_source.casefold()).strip("-")
            if not stem or len(stem) > 80:
                stem = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
            term_id = f"{term.category}:{stem}"
            if term_id in used_ids:
                term_id += "-" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
            used_ids.add(term_id)
            output.append(term.to_entry(term_id))
        return output


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_json_gzip(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8-sig") as handle:
        return json.load(handle)


def add_catalog_profiles(builder: TerminologyBuilder, path: Path) -> None:
    if not path.is_file():
        return
    payload = _read_json(path)
    for row in (payload.get("items", {}) or {}).values():
        if not isinstance(row, Mapping):
            continue
        zh = _normalise(row.get("name_zh"))
        en = _normalise(row.get("name_en") or row.get("page_title"))
        category = "form" if str(row.get("entry_key", "")).startswith("wikirby-form:") else "character"
        builder.add(
            zh=zh,
            en=en,
            category=category,
            zh_status="official" if zh else "unchanged",
            source="catalog-profiles",
            aliases_en=[row.get("page_title", ""), row.get("variant_key", "")],
        )
        builder.add(
            zh=_normalise(row.get("debut_work_zh")),
            en=_normalise(row.get("debut_work_en")),
            category="work",
            zh_status="official_reused",
            source="catalog-profiles",
        )


def add_forms(builder: TerminologyBuilder, path: Path) -> None:
    if not path.is_file():
        return
    payload = _read_json(path)
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("catalog_kind") or "").casefold()
        category = "ability" if "ability" in kind else "form"
        zh = _normalise(row.get("chinese_name"))
        en = _normalise(row.get("english_name") or row.get("page_title"))
        builder.add(
            zh=zh,
            en=en,
            category=category,
            zh_status="official" if zh else "unchanged",
            source="catalog-forms",
            aliases_en=[row.get("page_title", ""), row.get("variant_key", "")],
        )
        builder.add(
            zh=_normalise(row.get("official_chinese_work") or row.get("work_display_name")),
            en=_normalise(row.get("earliest_work")),
            category="work",
            zh_status="official_reused",
            source="catalog-forms",
        )


def add_wikirby_cache(builder: TerminologyBuilder, path: Path) -> None:
    if not path.is_file():
        return
    payload = _read_json_gzip(path)
    pages = payload.get("pages", {}) if isinstance(payload, Mapping) else {}
    for row in pages.values() if isinstance(pages, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        title = _normalise(row.get("title"))
        fields = _names_fields(str(row.get("wikitext") or ""))
        if not fields:
            builder.add(
                en=title,
                category="character",
                zh_status="unchanged",
                source="wikirby-names",
                note="WiKirby 页面没有可解析的多语言名称表",
            )
            continue
        zh_simp = _first_name(fields.get("zhSimp", ""), language="zh")
        zh_trad = _first_name(fields.get("zhTrad", ""), language="zh")
        zh_generic = _first_name(fields.get("zh", ""), language="zh")
        zh = zh_simp or zh_trad or zh_generic
        en = _first_name(fields.get("en", ""), language="en") or re.sub(r"\s+\([^()]+\)$", "", title)
        ja = _first_name(fields.get("ja", ""), language="ja")
        if not ja:
            for key, value in fields.items():
                if key.casefold().startswith("ja"):
                    ja = _first_name(value, language="ja")
                    if ja:
                        break
        builder.add(
            zh=zh,
            en=en,
            ja=ja,
            category="character",
            zh_status="official" if zh else "unchanged",
            source="wikirby-names",
            aliases_zh=[zh_trad] if zh_trad and zh_trad != zh else [],
            aliases_en=[title],
            note=f"WiKirby page: {title}",
        )


def _markdown_tables(text: str) -> Iterator[tuple[str, list[str], list[list[str]]]]:
    heading = ""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|\s*:?-+", lines[index + 1].strip()):
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            yield heading, headers, rows
            continue
        index += 1


def add_official_notes(builder: TerminologyBuilder, path: Path) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8-sig")
    for heading, headers, rows in _markdown_tables(text):
        zh_index = next((i for i, value in enumerate(headers) if "中文" in value or "简中" in value), -1)
        en_index = next((i for i, value in enumerate(headers) if "英文" in value), -1)
        ja_index = next((i for i, value in enumerate(headers) if "日文" in value), -1)
        if min(zh_index, en_index, ja_index) < 0:
            continue
        category = _category_from_context(heading)
        for row in rows:
            if max(zh_index, en_index, ja_index) >= len(row):
                continue
            zh, aliases, status = _clean_zh_label(row[zh_index])
            en = _normalise(row[en_index])
            ja = _normalise(row[ja_index])
            if en in {"—", "-", "未见对应日版主标题"}:
                en = ""
            if ja in {"—", "-", "未见对应日版主标题"}:
                ja = ""
            builder.add(
                zh=zh,
                en=en,
                ja=ja,
                category=category,
                zh_status=status,
                source="curated-official-notes",
                aliases_zh=aliases,
                note=f"星之卡比官方资料.md / {heading}",
                priority=120,
            )


def add_shinkaku(builder: TerminologyBuilder, path: Path) -> None:
    if not path.is_file():
        return
    payload = _read_json(path)
    status_map = {
        "official": "official",
        "official_reused": "official_reused",
        "translated": "project",
        "unchanged": "unchanged",
    }
    for row in payload.get("entries", []) if isinstance(payload, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        source_category = str(row.get("category") or "")
        category = (
            "ability"
            if "ability" in source_category
            else "character"
            if "boss" in source_category
            else "mode"
            if source_category in {"route_or_challenge", "mechanics_or_reference"}
            else "title"
        )
        status = status_map.get(str(row.get("zh_status") or ""), "project")
        base_zh = _normalise(row.get("base_zh"))
        base_en = _normalise(row.get("base_en"))
        base_ja = _normalise(row.get("base_ja"))
        base_names = {
            _normalise_key(value)
            for value in (base_zh, base_en, base_ja)
            if value
        }
        contextual_aliases = [
            value
            for value in row.get("aliases", [])
            if _normalise(value)
            and _normalise_key(value) not in base_names
        ]
        title_zh = _normalise(row.get("title_zh"))
        title_en = _normalise(row.get("title_en"))
        title_ja = _normalise(row.get("title_ja"))
        builder.add(
            zh=base_zh,
            en=base_en,
            ja=base_ja,
            category=category,
            zh_status=status,
            source="shinkaku-page-names",
            priority=115,
        )
        if any((title_zh, title_en, title_ja)) and any((base_zh, base_en, base_ja)):
            # Page titles such as "Fighter (Kirby Star Allies)" are useful
            # lookup aliases, but must not become separate terms or make the
            # bare Japanese/English name resolve to a random game variant.
            page_aliases_zh = [title_zh] if title_zh and _normalise_key(title_zh) != _normalise_key(base_zh) else []
            page_aliases_en = [title_en] if title_en and _normalise_key(title_en) != _normalise_key(base_en) else []
            page_aliases_ja = [title_ja] if title_ja and _normalise_key(title_ja) != _normalise_key(base_ja) else []
            if page_aliases_zh or page_aliases_en or page_aliases_ja or contextual_aliases:
                builder.add(
                    category=category,
                    zh_status=status,
                    source="shinkaku-page-names",
                    aliases_zh=[base_zh, *page_aliases_zh, *[value for value in contextual_aliases if CJK_RE.search(str(value))]],
                    aliases_en=[base_en, *page_aliases_en, *[value for value in contextual_aliases if ASCII_RE.search(str(value))]],
                    aliases_ja=[base_ja, *page_aliases_ja, *[value for value in contextual_aliases if JAPANESE_RE.search(str(value))]],
                    note=f"真格速查 #{row.get('catalog_index', '')}",
                    priority=150,
                )
        elif any((title_zh, title_en, title_ja)):
            # Keep malformed/legacy rows searchable even when no base name was
            # supplied in the source index.
            builder.add(
                zh=title_zh,
                en=title_en,
                ja=title_ja,
                category=category,
                zh_status=status,
                source="shinkaku-page-names",
                aliases_zh=[value for value in contextual_aliases if CJK_RE.search(str(value))],
                aliases_en=[value for value in contextual_aliases if ASCII_RE.search(str(value))],
                aliases_ja=[value for value in contextual_aliases if JAPANESE_RE.search(str(value))],
                note=f"真格速查 #{row.get('catalog_index', '')}",
                priority=150,
                distinct_key=f"shinkaku:{row.get('id') or row.get('url')}",
            )
        game_en = _normalise(row.get("game_en"))
        if game_en and game_en.casefold() != "general":
            builder.add(
                zh=_normalise(row.get("game_zh")),
                en=game_en,
                ja=_normalise(row.get("game_ja")),
                category="work",
                zh_status="official_reused",
                source="shinkaku-page-names",
            )


def fetch_bwiki_pages(cache_path: Path, *, refresh: bool = False) -> list[dict[str, Any]]:
    if cache_path.is_file() and not refresh:
        payload = _read_json_gzip(cache_path)
        return [dict(row) for row in payload.get("pages", []) if isinstance(row, Mapping)]
    pages: list[dict[str, Any]] = []
    continuation: dict[str, str] = {}
    while True:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "allpages",
            "gapnamespace": "0",
            "gaplimit": "max",
            "prop": "revisions",
            "rvprop": "content|ids|timestamp",
            "rvslots": "main",
            **continuation,
        }
        request = Request(
            f"{BWIKI_API_URL}?{urlencode(params)}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urlopen(request, timeout=45) as response:
            payload = json.load(response)
        for row in payload.get("query", {}).get("pages", []):
            revisions = row.get("revisions", [])
            content = ""
            if revisions:
                content = str(revisions[0].get("slots", {}).get("main", {}).get("content", "") or "")
            pages.append(
                {
                    "pageid": row.get("pageid"),
                    "title": row.get("title", ""),
                    "wikitext": content,
                }
            )
        continuation = payload.get("continue", {})
        if not continuation:
            break
        time.sleep(0.15)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache_path, "wt", encoding="utf-8") as handle:
        json.dump(
            {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages": pages},
            handle,
            ensure_ascii=False,
        )
    return pages


def _bwiki_category(template_name: str, page_title: str) -> str:
    return _category_from_context(f"{template_name} {page_title}")


def _extract_bwiki_templates(page: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    title = _normalise(page.get("title"))
    text = str(page.get("wikitext") or "")
    project = any(marker.casefold() in text.casefold() for marker in PROJECT_NAME_MARKERS)
    for block in _iter_templates(text):
        template_name, fields = _template_fields(block)
        lowered = {key.replace(" ", "").casefold(): value for key, value in fields.items()}
        zh_raw = lowered.get("中文名", "") or lowered.get("名称", "")
        en_raw = lowered.get("英文名", "") or lowered.get("英语名", "")
        ja_raw = lowered.get("日文名", "") or lowered.get("日语名", "")
        zh = _first_name(zh_raw, language="zh", page_title=title)
        en = _first_name(en_raw, language="en", page_title=title)
        ja = _first_name(ja_raw, language="ja", page_title=title)
        if sum(bool(value) for value in (zh, en, ja)) < 2:
            continue
        yield {
            "zh": zh,
            "en": en,
            "ja": ja,
            "category": _bwiki_category(template_name, title),
            "status": "project" if project else "official_reused",
            "note": f"BWIKI page: {title}; template: {template_name}",
        }


def _extract_link_label(value: str) -> str:
    links = re.findall(r"\[\[([^]]+)\]\]", value)
    if links:
        target = links[-1]
        return _clean_wikitext(target.split("|", 1)[-1].split("#", 1)[-1])
    return _clean_wikitext(value)


def _extract_bwiki_tables(page: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    title = _normalise(page.get("title"))
    text = str(page.get("wikitext") or "")
    project = any(marker.casefold() in text.casefold() for marker in PROJECT_NAME_MARKERS)
    name_labels = {
        "中文名": "zh",
        "简体中文名": "zh",
        "中文名称": "zh",
        "日文名": "ja",
        "日语名": "ja",
        "日文名称": "ja",
        "英文名": "en",
        "英语名": "en",
        "英文名称": "en",
    }

    # BWIKI commonly stores names as one key/value pair per table row instead
    # of placing all languages in a single cell separated by <br>.
    for table in re.findall(r"(?ms)^\{\|.*?^\|\}", text):
        names: dict[str, str] = {}
        for raw_row in re.split(r"(?m)^\|-\s*$", table):
            pending_key = ""
            for raw_line in raw_row.splitlines():
                line = raw_line.strip()
                if line.startswith("!"):
                    label = line[1:].strip()
                    if "|" in label:
                        prefix, remainder = label.split("|", 1)
                        if "=" in prefix:
                            label = remainder
                    pending_key = name_labels.get(_clean_wikitext(label), "")
                    continue
                if not pending_key or not line.startswith("|") or line.startswith(("|-", "|}")):
                    continue
                value = line[1:].strip()
                if "|" in value:
                    prefix, remainder = value.split("|", 1)
                    if "=" in prefix:
                        value = remainder
                cleaned = _first_name(value, language=pending_key, page_title=title)
                if cleaned:
                    names[pending_key] = cleaned
                pending_key = ""
        if sum(bool(names.get(language)) for language in ("zh", "en", "ja")) >= 2:
            yield {
                "zh": names.get("zh", ""),
                "en": names.get("en", ""),
                "ja": names.get("ja", ""),
                "category": _category_from_context(title),
                "status": "project" if project else "official_reused",
                "note": f"BWIKI vertical name table: {title}",
            }

    for raw_row in re.split(r"(?m)^\|-\s*$", text):
        cells = re.split(r"\|\|", raw_row)
        if len(cells) < 2:
            continue
        for index, cell in enumerate(cells):
            if not re.search(r"<br\s*/?>", cell, flags=re.I):
                continue
            parts = re.split(r"<br\s*/?>", cell, flags=re.I)
            ja = next((_first_name(part, language="ja") for part in parts if JAPANESE_RE.search(part)), "")
            en = next((_first_name(part, language="en") for part in reversed(parts) if ASCII_RE.search(_clean_wikitext(part))), "")
            if not ja or not en:
                continue
            zh = ""
            for previous in reversed(cells[:index]):
                candidate = _extract_link_label(previous)
                if candidate and CJK_RE.search(candidate):
                    zh = candidate
                    break
            if not zh:
                continue
            yield {
                "zh": zh,
                "en": en,
                "ja": ja,
                "category": _category_from_context(title),
                "status": "project" if project else "official_reused",
                "note": f"BWIKI table: {title}",
            }


def add_bwiki(builder: TerminologyBuilder, pages: Sequence[Mapping[str, Any]]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for page in pages:
        for source_name, extractor in (
            ("bwiki-infobox", _extract_bwiki_templates),
            ("bwiki-table", _extract_bwiki_tables),
        ):
            for row in extractor(page):
                key = (
                    _normalise_key(row["zh"]),
                    _normalise_key(row["en"]),
                    _normalise_key(row["ja"]),
                    source_name,
                )
                if key in seen:
                    continue
                seen.add(key)
                builder.add(
                    zh=row["zh"],
                    en=row["en"],
                    ja=row["ja"],
                    category=row["category"],
                    zh_status=row["status"],
                    source=source_name,
                    note=row["note"],
                )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_audit(entries: Sequence[TerminologyEntry], builder: TerminologyBuilder) -> dict[str, Any]:
    source_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    missing: dict[str, list[dict[str, str]]] = {"zh_cn": [], "en": [], "ja": []}
    for entry in entries:
        source_counts.update(entry.sources)
        category_counts[entry.category] += 1
        status_counts[entry.zh_status] += 1
        for field_name in missing:
            if not getattr(entry, field_name):
                missing[field_name].append(
                    {"term_id": entry.term_id, "label": entry.canonical_label}
                )
    temporary = DEFAULT_AUDIT_PATH.with_name(".terminology-audit-overrides.json")
    store = KirbyTerminologyStore(DEFAULT_JSON_PATH.with_name(".__missing__.json"), temporary)
    try:
        store.import_rows([entry.to_mapping() for entry in entries], replace_overrides=True)
        conflicts = store.conflicts()
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "summary": {
            "entries": len(entries),
            "enabled": sum(entry.enabled for entry in entries),
            "input_records": sum(builder.input_counts.values()),
            "alias_conflicts": len(conflicts),
            "missing_zh": len(missing["zh_cn"]),
            "missing_en": len(missing["en"]),
            "missing_ja": len(missing["ja"]),
        },
        "input_counts": dict(sorted(builder.input_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "zh_status_counts": dict(sorted(status_counts.items())),
        "missing": missing,
        "conflicts": conflicts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Kirby trilingual terminology library.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--shinkaku", type=Path, default=DEFAULT_SHINKAKU_PATH)
    parser.add_argument("--official-notes", type=Path, default=DEFAULT_OFFICIAL_NOTES_PATH)
    parser.add_argument("--wikirby-cache", type=Path, default=DEFAULT_WIKIRBY_CACHE_PATH)
    parser.add_argument("--forms", type=Path, default=DEFAULT_FORMS_PATH)
    parser.add_argument("--bwiki-cache", type=Path, default=DEFAULT_BWIKI_CACHE_PATH)
    parser.add_argument("--refresh-bwiki", action="store_true")
    parser.add_argument("--skip-bwiki", action="store_true")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    builder = TerminologyBuilder()
    add_catalog_profiles(builder, args.catalog)
    add_forms(builder, args.forms)
    add_wikirby_cache(builder, args.wikirby_cache)
    add_official_notes(builder, args.official_notes)
    add_shinkaku(builder, args.shinkaku)
    bwiki_pages: list[dict[str, Any]] = []
    if not args.skip_bwiki:
        bwiki_pages = fetch_bwiki_pages(args.bwiki_cache, refresh=args.refresh_bwiki)
        add_bwiki(builder, bwiki_pages)
    entries = builder.entries()
    document = terminology_document(
        entries,
        metadata={
            "description": "Kirby terminology normalized to Simplified Chinese, English and Japanese.",
            "canonical_output": "中文（English）",
            "sources": sorted(builder.input_counts),
            "bwiki_pages": len(bwiki_pages),
        },
    )
    _write_json(args.output_json, document)
    store = KirbyTerminologyStore(args.output_json, args.output_json.with_name(".__empty_overrides__.json"))
    try:
        args.output_csv.write_bytes(store.export_csv())
    finally:
        args.output_json.with_name(".__empty_overrides__.json").unlink(missing_ok=True)
    audit = build_audit(entries, builder)
    _write_json(args.audit, audit)
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
