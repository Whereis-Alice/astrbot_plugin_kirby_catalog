from __future__ import annotations

import asyncio
import html
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import Tag

from .wikirby import parse_rendered_sections

DEFAULT_FANDOM_API_URL = "https://kirby.fandom.com/api.php"
FANDOM_SITE_URL = "https://kirby.fandom.com"
USER_AGENT = (
    "astrbot-plugin-kirby-catalog/2.10.1 "
    "(+https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)"
)
_RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}

_INFOBOX_LABELS = {
    "name(ja)": "日文名",
    "name (ja)": "日文名",
    "japanese name": "日文名",
    "gender": "性别",
    "species": "种类",
    "affiliation": "所属",
    "occupation": "身份",
    "first appearance": "首次登场",
    "latest appearance": "最近登场",
    "in games": "游戏登场",
    "games": "游戏登场",
    "copy ability": "提供能力",
    "ability": "能力",
    "abilities": "能力",
    "location": "地点",
    "locations": "地点",
    "homeworld": "故乡",
    "family": "亲属",
    "creator": "创作者",
    "voice actor": "配音",
    "voice actors": "配音",
    "theme music": "主题曲",
}
_LANGUAGE_LABELS = {
    "en": "英语",
    "ja": "日语",
    "zh": "中文",
    "zh-tw": "繁体中文",
    "zh-cn": "简体中文",
    "ko": "韩语",
    "de": "德语",
    "es": "西班牙语",
    "fr": "法语",
    "it": "意大利语",
    "nl": "荷兰语",
    "pl": "波兰语",
    "pt": "葡萄牙语",
    "ru": "俄语",
}
_SKIPPED_SECTIONS = {
    "artwork",
    "concept art",
    "gallery",
    "sprites and models",
    "screenshots",
    "references",
    "external links",
    "navigation",
}
_RICH_SECTION_KINDS = {
    "related quote": "quotes",
    "related quotes": "quotes",
    "quote": "quotes",
    "quotes": "quotes",
    "moves": "techniques",
    "moveset": "techniques",
    "technique": "techniques",
    "techniques": "techniques",
}
_TECHNIQUE_COLUMN_NAMES = {
    "move": {"move", "moves", "technique", "techniques", "attack"},
    "controls": {"control", "controls", "input", "inputs", "command"},
    "description": {"description", "effect", "effects", "notes"},
    "damage": {"damage", "power"},
}
_CONTROL_TRANSLATIONS = (
    ("Press and release", "按下再松开"),
    ("Press and hold", "长按"),
    ("Repeatedly press", "连续按"),
    ("Press repeatedly", "连续按"),
    ("Tap repeatedly", "连续轻按"),
    ("Quickly tap", "快速轻按"),
    ("Left Stick Down", "左摇杆↓"),
    ("Left Stick Up", "左摇杆↑"),
    ("Left Stick Left", "左摇杆←"),
    ("Left Stick Right", "左摇杆→"),
    ("Right Stick Down", "右摇杆↓"),
    ("Right Stick Up", "右摇杆↑"),
    ("Right Stick Left", "右摇杆←"),
    ("Right Stick Right", "右摇杆→"),
    ("Control Stick Down", "摇杆↓"),
    ("Control Stick Up", "摇杆↑"),
    ("Control Stick Left", "摇杆←"),
    ("Control Stick Right", "摇杆→"),
    ("D-Pad Down", "下方向键"),
    ("D-Pad Up", "上方向键"),
    ("D-Pad Left", "左方向键"),
    ("D-Pad Right", "右方向键"),
    ("Down Button", "下方向键"),
    ("Up Button", "上方向键"),
    ("Left Button", "左方向键"),
    ("Right Button", "右方向键"),
    ("Nintendo Switch Pro Controller", "Nintendo Switch Pro 手柄"),
    ("Pro Controller", "Pro 手柄"),
    ("Wii Remote", "Wii 遥控器"),
    ("Control Stick", "摇杆"),
    ("Left Stick", "左摇杆"),
    ("Right Stick", "右摇杆"),
    ("on the ground", "在地面时"),
    ("in midair", "在空中"),
    ("in the air", "在空中"),
    ("while airborne", "在空中时"),
    ("while dashing", "冲刺时"),
    ("while running", "奔跑时"),
    ("near an enemy", "靠近敌人时"),
    ("attack with", "使用"),
    ("and then", "然后"),
    ("repeatedly", "连续"),
    ("During", "在"),
    ("after", "之后"),
    ("before", "之前"),
    ("then", "然后"),
    ("Release", "松开"),
    ("Dash", "冲刺"),
    ("Press", "按"),
    ("Hold", "长按"),
    ("Tap", "轻按"),
    ("Button", "键"),
)


class KirbyFandomError(RuntimeError):
    """Raised when the Kirby Fandom read-only API cannot answer a request."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _clean_text(element: Tag | None) -> str:
    if element is None:
        return ""
    fragment = BeautifulSoup(str(element), "html.parser")
    for unwanted in fragment.select(
        "script, style, sup.reference, .reference, .mw-editsection, .noprint"
    ):
        unwanted.decompose()
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")
    text = html.unescape(fragment.get_text(" ", strip=True))
    text = re.sub(r"\[\s*\d+(?:\.\d+)?\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _image_labels(element: Tag | None) -> list[str]:
    if element is None:
        return []
    values: list[str] = []
    for link in element.select("a[title]"):
        value = _clean_text(link)
        title = str(link.get("title", "") or "").strip()
        candidate = value or title
        if candidate and candidate not in values:
            values.append(candidate)
    for image in element.select("img[alt]"):
        value = str(image.get("alt", "") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _trim_text(value: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 3)].rstrip()
    return shortened + "..."


def _normalise_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", value.casefold()).strip()


def _rich_section_kind(title: str) -> str:
    return _RICH_SECTION_KINDS.get(_normalise_heading(title), "")


def _heading_text(heading: Tag) -> str:
    return _clean_text(heading.select_one(".mw-headline") or heading)


def _section_fragment(heading: Tag) -> BeautifulSoup:
    level = int(heading.name[1]) if heading.name and heading.name[1:].isdigit() else 6
    nodes: list[str] = []
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
            sibling_level = int(sibling.name[1])
            if sibling_level <= level:
                break
        if isinstance(sibling, Tag):
            nodes.append(str(sibling))
    return BeautifulSoup("".join(nodes), "html.parser")


def _rich_cell_text(element: Tag | None) -> str:
    if element is None:
        return ""
    fragment = BeautifulSoup(str(element), "html.parser")
    for unwanted in fragment.select(
        "script, style, sup.reference, .reference, .mw-editsection, .noprint"
    ):
        unwanted.decompose()
    for image in fragment.find_all("img"):
        label = str(image.get("alt", "") or image.get("title", "") or "").strip()
        if not label:
            titled_parent = image.find_parent(attrs={"title": True})
            if titled_parent is not None:
                label = str(titled_parent.get("title", "") or "").strip()
        image.replace_with(f" {label} " if label else " ")
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")
    text = html.unescape(fragment.get_text(" ", strip=True))
    text = re.sub(r"\[\s*\d+(?:\.\d+)?\s*\]", "", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\+\s*", " + ", text)
    return text.strip()


def _parse_quote_table(table: Tag) -> dict[str, str] | None:
    rows = table.find_all("tr")
    if not rows:
        return None
    first_cells = rows[0].find_all(["th", "td"], recursive=False)
    if not first_cells:
        return None
    quote_cell = first_cells[-1]
    italic = quote_cell.select_one("span[style*='font-style: italic'], i, em")
    quote = _rich_cell_text(italic or quote_cell)
    quote = re.sub(r"^[\s\"'‘’“”«»]+|[\s\"'‘’“”«»]+$", "", quote).strip()
    if not quote:
        return None

    attribution = ""
    source = ""
    if len(rows) > 1:
        meta_cell = rows[1].find(["th", "td"])
        meta_text = _rich_cell_text(meta_cell).lstrip("—–- ").strip()
        parts = re.split(r"\s*[•·]\s*", meta_text, maxsplit=1)
        attribution = parts[0].strip() if parts else ""
        source = parts[1].strip() if len(parts) > 1 else ""
    return {"text": quote, "attribution": attribution, "source": source}


def _parse_quotes_section(
    fragment: BeautifulSoup, max_quotes: int
) -> tuple[list[dict[str, str]], int]:
    quotes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for table in fragment.select("table.br-5px"):
        quote = _parse_quote_table(table)
        if quote is None:
            continue
        key = (quote["text"], quote["attribution"], quote["source"])
        if key in seen:
            continue
        seen.add(key)
        quotes.append(quote)
    limit = max(1, max_quotes)
    return quotes[:limit], max(0, len(quotes) - limit)


def _expanded_table(table: Tag) -> tuple[list[list[str]], list[list[str]]]:
    values: dict[tuple[int, int], str] = {}
    tags: dict[tuple[int, int], str] = {}
    max_column = 0
    rows = table.find_all("tr")
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while (row_index, column) in values:
                column += 1
            try:
                rowspan = max(1, int(cell.get("rowspan", 1) or 1))
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = max(1, int(cell.get("colspan", 1) or 1))
            except (TypeError, ValueError):
                colspan = 1
            value = _rich_cell_text(cell)
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    position = (row_index + row_offset, column + column_offset)
                    values[position] = value
                    tags[position] = cell.name or "td"
            column += colspan
            max_column = max(max_column, column)

    matrix: list[list[str]] = []
    tag_matrix: list[list[str]] = []
    for row_index in range(len(rows)):
        matrix.append([values.get((row_index, column), "") for column in range(max_column)])
        tag_matrix.append([tags.get((row_index, column), "") for column in range(max_column)])
    return matrix, tag_matrix


def _column_matches(label: str, kind: str) -> bool:
    folded = _normalise_heading(label)
    return any(token in folded.split() for token in _TECHNIQUE_COLUMN_NAMES[kind])


def _localise_controls(value: str) -> str:
    translated = value
    for source, target in _CONTROL_TRANSLATIONS:
        translated = re.sub(re.escape(source), target, translated, flags=re.IGNORECASE)
    translated = re.sub(r"[ \t]+", " ", translated)
    translated = re.sub(r"[ \t]*\n[ \t]*", "\n", translated)
    return translated.strip()


def _parse_technique_table(table: Tag) -> list[dict[str, str]]:
    matrix, tag_matrix = _expanded_table(table)
    if not matrix:
        return []
    header_rows = 0
    for tags in tag_matrix:
        if "td" in tags:
            break
        header_rows += 1
    header_rows = max(1, header_rows)
    column_count = max((len(row) for row in matrix), default=0)
    header_labels: list[str] = []
    for column in range(column_count):
        labels: list[str] = []
        for row in matrix[:header_rows]:
            if column >= len(row):
                continue
            value = row[column].strip()
            if value and value not in labels:
                labels.append(value)
        header_labels.append(" / ".join(labels))

    indices: dict[str, list[int]] = {kind: [] for kind in _TECHNIQUE_COLUMN_NAMES}
    for column, label in enumerate(header_labels):
        for kind in indices:
            if _column_matches(label, kind):
                indices[kind].append(column)
    if not indices["move"] and column_count:
        indices["move"] = [0]
    if not indices["description"] and column_count >= 3:
        indices["description"] = [column_count - 2]
    if not indices["damage"] and column_count >= 2:
        indices["damage"] = [column_count - 1]
    if not indices["controls"] and column_count >= 4:
        reserved = {
            *(indices["move"] or []),
            *(indices["description"] or []),
            *(indices["damage"] or []),
        }
        indices["controls"] = [
            column for column in range(column_count) if column not in reserved
        ]

    def value_at(row: list[str], columns: list[int]) -> str:
        values = [row[index].strip() for index in columns if index < len(row)]
        return next((value for value in values if value), "")

    rows: list[dict[str, str]] = []
    for row in matrix[header_rows:]:
        move = value_at(row, indices["move"])
        description = value_at(row, indices["description"])
        damage = value_at(row, indices["damage"])
        control_values: list[tuple[str, str]] = []
        for column in indices["controls"]:
            if column >= len(row) or not row[column].strip():
                continue
            platform = header_labels[column]
            platform = re.sub(r"^Controls?\s*/\s*", "", platform, flags=re.I)
            control_values.append((platform.strip(), row[column].strip()))
        unique_controls = list(dict.fromkeys(value for _, value in control_values))
        if len(unique_controls) <= 1:
            controls = unique_controls[0] if unique_controls else ""
        else:
            controls = "\n".join(
                f"{platform}：{value}" if platform else value
                for platform, value in control_values
            )
        if not any((move, controls, description, damage)):
            continue
        if _column_matches(move, "move") and _column_matches(description, "description"):
            continue
        rows.append(
            {
                "move": _trim_text(move, 160),
                "controls": _trim_text(_localise_controls(controls), 260),
                "description": _trim_text(description, 1200),
                "damage": _trim_text(damage, 260),
            }
        )
    return rows


def _parse_techniques_section(
    fragment: BeautifulSoup, max_rows: int
) -> tuple[str, list[dict[str, Any]], int]:
    intro_parts = [
        _clean_text(paragraph)
        for paragraph in fragment.find_all("p", recursive=False)
        if _clean_text(paragraph)
    ]
    intro = _trim_text("\n\n".join(intro_parts), 1000)

    groups: list[dict[str, Any]] = []
    tab_contents = fragment.select(".wds-tab__content")
    tab_labels = [
        _clean_text(label)
        for label in fragment.select(".wds-tabs__tab .wds-tabs__tab-label")
    ]
    if tab_contents:
        for index, content in enumerate(tab_contents):
            rows: list[dict[str, str]] = []
            for table in content.select("table.wikitable"):
                rows.extend(_parse_technique_table(table))
            if rows:
                groups.append(
                    {
                        "label": tab_labels[index] if index < len(tab_labels) else "",
                        "rows": rows,
                    }
                )
    else:
        for table_index, table in enumerate(fragment.select("table.wikitable")):
            rows = _parse_technique_table(table)
            if rows:
                groups.append(
                    {
                        "label": "" if table_index == 0 else f"表 {table_index + 1}",
                        "rows": rows,
                    }
                )

    remaining = max(1, max_rows)
    omitted = 0
    selected: list[dict[str, Any]] = []
    for group in groups:
        rows = list(group["rows"])
        kept = rows[:remaining]
        omitted += len(rows) - len(kept)
        if kept:
            selected.append({"label": group["label"], "rows": kept})
            remaining -= len(kept)
        if remaining <= 0:
            omitted += sum(len(item["rows"]) for item in groups[len(selected) :])
            break
    return intro, selected, omitted


def parse_fandom_rich_sections(
    rendered_html: str,
    *,
    max_quotes: int = 20,
    max_technique_rows: int = 32,
) -> list[dict[str, Any]]:
    """Preserve quote cards and technique tables that plain text would flatten."""
    soup = BeautifulSoup(rendered_html or "", "html.parser")
    root = soup.select_one(".mw-parser-output")
    if root is None:
        return []

    output: list[dict[str, Any]] = []
    heading_stack: dict[int, str] = {}
    for heading in root.find_all(re.compile(r"^h[2-4]$"), recursive=False):
        level = int(heading.name[1])
        title = _heading_text(heading)
        heading_stack = {
            key: value for key, value in heading_stack.items() if key < level
        }
        ancestors = [heading_stack[key] for key in sorted(heading_stack)]
        heading_stack[level] = title
        kind = _rich_section_kind(title)
        if not kind:
            continue
        fragment = _section_fragment(heading)
        base: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "context": " · ".join(ancestors),
            "ancestors": ancestors,
        }
        if kind == "quotes":
            quotes, omitted = _parse_quotes_section(fragment, max_quotes)
            if quotes:
                output.append({**base, "quotes": quotes, "omitted_count": omitted})
            continue
        intro, groups, omitted = _parse_techniques_section(
            fragment, max_technique_rows
        )
        if groups:
            output.append(
                {
                    **base,
                    "intro": intro,
                    "groups": groups,
                    "omitted_count": omitted,
                }
            )
    return output


def parse_fandom_intro(rendered_html: str, max_chars: int = 1800) -> str:
    """Extract the readable lead paragraphs before the first main heading."""
    soup = BeautifulSoup(rendered_html or "", "html.parser")
    root = soup.select_one(".mw-parser-output")
    if root is None:
        return ""
    paragraphs: list[str] = []
    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "h2":
            break
        if child.name != "p":
            continue
        value = _clean_text(child)
        if not value or len(value) < 20:
            continue
        if value.casefold().startswith(("this article is about", "redirects here")):
            continue
        paragraphs.append(value)
        if sum(len(item) for item in paragraphs) >= max_chars:
            break
    return _trim_text("\n\n".join(paragraphs), max_chars)


def parse_fandom_infobox(rendered_html: str) -> list[dict[str, str]]:
    """Read Fandom's portable infobox into compact label/value rows."""
    soup = BeautifulSoup(rendered_html or "", "html.parser")
    infobox = soup.select_one("aside.portable-infobox")
    if infobox is None:
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in infobox.select(".pi-data"):
        label_node = item.select_one(".pi-data-label")
        value_node = item.select_one(".pi-data-value")
        label = _clean_text(label_node).rstrip(":：")
        if not label:
            continue
        value = _clean_text(value_node)
        if not value:
            value = "、".join(_image_labels(value_node))
        value = re.sub(r"\s+", " ", value).strip(" 、")
        if not value:
            continue
        display_label = _INFOBOX_LABELS.get(label.casefold(), label)
        key = (display_label, value)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"label": display_label, "value": value})
        if len(rows) >= 18:
            break
    return rows


def parse_fandom_image_url(rendered_html: str) -> str:
    """Prefer the full-size portable-infobox artwork URL."""
    soup = BeautifulSoup(rendered_html or "", "html.parser")
    candidates: list[str] = []
    for selector, attribute in (
        ("aside.portable-infobox figure.pi-image a[href]", "href"),
        ("aside.portable-infobox figure.pi-image img[data-src]", "data-src"),
        ("aside.portable-infobox figure.pi-image img[src]", "src"),
    ):
        for element in soup.select(selector):
            value = html.unescape(str(element.get(attribute, "") or "")).strip()
            if value.startswith("//"):
                value = "https:" + value
            if value.startswith("https://") and not value.startswith("data:"):
                candidates.append(value)
    return candidates[0] if candidates else ""


def parse_fandom_sections(rendered_html: str) -> list[dict[str, str]]:
    """Extract prose-heavy sections while omitting media galleries and references."""
    output: list[dict[str, str]] = []
    for section in parse_rendered_sections(rendered_html):
        title = str(section.get("title", "") or "").strip()
        text = str(section.get("text", "") or "").strip()
        if not title or not text:
            continue
        folded = _normalise_heading(title)
        if folded in _SKIPPED_SECTIONS:
            continue
        if _rich_section_kind(title):
            continue
        output.append({"title": title, "text": text})
    return output


def _rich_section_cost(section: dict[str, Any]) -> int:
    cost = len(str(section.get("title", ""))) + len(str(section.get("context", "")))
    cost += len(str(section.get("intro", "")))
    if section.get("kind") == "quotes":
        for quote in section.get("quotes", []):
            cost += sum(
                len(str(quote.get(key, "")))
                for key in ("text", "attribution", "source")
            ) + 24
    elif section.get("kind") == "techniques":
        for group in section.get("groups", []):
            cost += len(str(group.get("label", ""))) + 20
            for row in group.get("rows", []):
                cost += sum(
                    len(str(row.get(key, "")))
                    for key in ("move", "controls", "description", "damage")
                ) + 32
    return cost


def _trim_rich_sections(
    sections: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    remaining = max(0, budget)
    for section_index, source in enumerate(sections):
        if remaining <= 0:
            break
        kind = str(source.get("kind", "") or "")
        base: dict[str, Any] = {
            "kind": kind,
            "title": str(source.get("title", "") or ""),
            "context": str(source.get("context", "") or ""),
            "ancestors": list(source.get("ancestors", []) or []),
        }
        base_cost = _rich_section_cost(base) + 40
        if kind == "quotes":
            quotes: list[dict[str, str]] = []
            source_quotes = list(source.get("quotes", []) or [])
            for quote in source_quotes:
                row = {
                    "text": str(quote.get("text", "") or ""),
                    "attribution": str(quote.get("attribution", "") or ""),
                    "source": str(quote.get("source", "") or ""),
                }
                row_cost = sum(len(value) for value in row.values()) + 24
                if quotes and base_cost + row_cost > remaining:
                    break
                if not quotes and base_cost + row_cost > remaining:
                    row["text"] = _trim_text(
                        row["text"], max(180, remaining - base_cost - 80)
                    )
                    row_cost = sum(len(value) for value in row.values()) + 24
                quotes.append(row)
                base_cost += row_cost
            if not quotes:
                continue
            omitted = int(source.get("omitted_count", 0) or 0)
            omitted += len(source_quotes) - len(quotes)
            section = {**base, "quotes": quotes, "omitted_count": omitted}
        elif kind == "techniques":
            intro = str(source.get("intro", "") or "")
            groups: list[dict[str, Any]] = []
            source_groups = [
                group
                for group in (source.get("groups", []) or [])
                if group.get("rows")
            ]
            omitted = int(source.get("omitted_count", 0) or 0)
            base_cost += len(intro)
            techniques_left = sum(
                1
                for item in sections[section_index:]
                if item.get("kind") == "techniques"
            )
            section_budget = (
                remaining
                if techniques_left <= 1
                else max(700, remaining // techniques_left)
            )
            headers_cost = sum(
                len(str(group.get("label", "") or "")) + 20
                for group in source_groups
            )
            rows_budget = max(0, section_budget - base_cost - headers_cost)
            group_budget, budget_remainder = divmod(
                rows_budget, max(1, len(source_groups))
            )
            for group_index, group in enumerate(source_groups):
                label = str(group.get("label", "") or "")
                rows: list[dict[str, str]] = []
                source_rows = list(group.get("rows", []) or [])
                quota = group_budget + (1 if group_index < budget_remainder else 0)
                used = 0
                for row in source_rows:
                    value = {
                        "move": str(row.get("move", "") or ""),
                        "controls": str(row.get("controls", "") or ""),
                        "description": str(row.get("description", "") or ""),
                        "damage": str(row.get("damage", "") or ""),
                    }
                    row_cost = sum(len(item) for item in value.values()) + 32
                    if rows and used + row_cost > quota:
                        break
                    if not rows and row_cost > quota:
                        fixed_cost = (
                            len(value["move"])
                            + len(value["controls"])
                            + len(value["damage"])
                            + 32
                        )
                        value["description"] = _trim_text(
                            value["description"],
                            max(120, quota - fixed_cost),
                        )
                        row_cost = sum(len(item) for item in value.values()) + 32
                    rows.append(value)
                    used += row_cost
                omitted += len(source_rows) - len(rows)
                if rows:
                    groups.append({"label": label, "rows": rows})
            if not groups:
                continue
            section = {
                **base,
                "intro": intro,
                "groups": groups,
                "omitted_count": omitted,
            }
        else:
            continue
        cost = _rich_section_cost(section)
        selected.append(section)
        remaining = max(0, remaining - cost)
    return selected, max(0, budget - remaining)


def parse_fandom_language_names(
    page_title: str,
    infobox_rows: list[dict[str, str]],
    langlinks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Collect the English title, Japanese infobox name and language page titles."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def append(language: str, name: str, url: str = "") -> None:
        language = language.strip()
        name = re.sub(r"\s+", " ", name).strip()
        if not language or not name:
            return
        romanisation = ""
        match = re.fullmatch(r"(.+?)\s*[（(]([A-Za-z\u00c0-\u024f'’. -]+)[）)]", name)
        if match and any(not char.isascii() for char in match.group(1)):
            name = match.group(1).strip()
            romanisation = match.group(2).strip()
        key = (language, name)
        if key in seen:
            return
        seen.add(key)
        row = {"language": language, "name": name}
        if romanisation:
            row["romanisation"] = romanisation
        if url:
            row["url"] = url
        rows.append(row)

    append("英语", page_title)
    for row in infobox_rows:
        if row.get("label") == "日文名":
            append("日语", row.get("value", ""))
    for link in langlinks or []:
        if not isinstance(link, dict):
            continue
        code = str(link.get("lang", "") or "").casefold()
        language = _LANGUAGE_LABELS.get(
            code,
            str(link.get("autonym") or link.get("langname") or code).strip(),
        )
        append(
            language, str(link.get("title", "") or ""), str(link.get("url", "") or "")
        )
    return rows


class KirbyFandomClient:
    """Read-only MediaWiki client for the English Kirby Fandom community wiki."""

    def __init__(
        self,
        api_url: str = DEFAULT_FANDOM_API_URL,
        timeout_seconds: float = 15.0,
        cache_ttl_seconds: int = 3600,
        max_summary_chars: int = 1800,
        max_detail_chars: int = 7000,
    ) -> None:
        self.api_url = api_url.strip() or DEFAULT_FANDOM_API_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_summary_chars = max(300, int(max_summary_chars))
        self.max_detail_chars = max(1000, int(max_detail_chars))
        self._cache: dict[str, tuple[float, Any]] = {}
        self._request_limit = asyncio.Semaphore(2)

    async def close(self) -> None:
        self._cache.clear()

    def _cache_get(self, key: str) -> Any:
        item = self._cache.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        if self.cache_ttl_seconds > 0:
            self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, value)

    @staticmethod
    def _retry_delay(error: HTTPError, attempt: int) -> float:
        retry_after = error.headers.get("Retry-After") if error.headers else None
        try:
            return max(0.2, min(float(retry_after), 3.0))
        except (TypeError, ValueError):
            return 0.4 * (2**attempt)

    def _request_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({"format": "json", "formatversion": "2", **params})
        separator = "&" if "?" in self.api_url else "?"
        request = Request(
            f"{self.api_url}{separator}{query}",
            headers={
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": FANDOM_SITE_URL + "/",
                "User-Agent": USER_AGENT,
            },
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code not in _RETRYABLE_HTTP_CODES or attempt == 1:
                    raise KirbyFandomError(
                        f"Kirby Fandom API 返回 HTTP {exc.code}，请稍后再试"
                    ) from exc
                time.sleep(self._retry_delay(exc, attempt))
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 1:
                    raise KirbyFandomError("无法连接 Kirby Fandom，请稍后再试") from exc
                time.sleep(0.4 * (2**attempt))
        else:
            raise KirbyFandomError("无法连接 Kirby Fandom，请稍后再试") from last_error

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KirbyFandomError("Kirby Fandom 返回了无法识别的数据") from exc
        if not isinstance(data, dict):
            raise KirbyFandomError("Kirby Fandom 返回了无法识别的数据")
        error = data.get("error")
        if error:
            if isinstance(error, dict):
                code = str(error.get("code", "") or "")
                message = str(error.get("info", "") or "Kirby Fandom API 请求失败")
            else:
                code = ""
                message = str(error)
            raise KirbyFandomError(message, code)
        return data

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        async with self._request_limit:
            return await asyncio.to_thread(self._request_sync, params)

    @staticmethod
    def _title_from_url(value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.netloc.casefold() != "kirby.fandom.com"
            or "/wiki/" not in parsed.path
        ):
            return value
        return unquote(parsed.path.split("/wiki/", 1)[1]).replace("_", " ").strip()

    @staticmethod
    def _page_url(title: str) -> str:
        return FANDOM_SITE_URL + "/wiki/" + quote(title.replace(" ", "_"), safe="():")

    def _normalise_page(
        self, data: dict[str, Any], requested_title: str
    ) -> dict[str, Any] | None:
        parsed = data.get("parse")
        if not isinstance(parsed, dict) or not parsed.get("pageid"):
            return None
        rendered_html = parsed.get("text", "")
        if isinstance(rendered_html, dict):
            rendered_html = rendered_html.get("*", "")
        rendered_html = str(rendered_html or "")
        title = str(parsed.get("title") or requested_title).strip()
        infobox = parse_fandom_infobox(rendered_html)
        langlinks = parsed.get("langlinks", [])
        if not isinstance(langlinks, list):
            langlinks = []
        categories = parsed.get("categories", [])
        category_names = []
        if isinstance(categories, list):
            for row in categories:
                value = row.get("category", "") if isinstance(row, dict) else row
                value = str(value or "").replace("_", " ").strip()
                if value and value not in category_names:
                    category_names.append(value)
        section_index: list[dict[str, str]] = []
        for row in parsed.get("sections", []) or []:
            if not isinstance(row, dict):
                continue
            title_text = _clean_text(
                BeautifulSoup(str(row.get("line", "") or ""), "html.parser")
            )
            if title_text:
                section_index.append(
                    {
                        "index": str(row.get("index", "") or ""),
                        "title": title_text,
                        "level": str(row.get("level", "") or ""),
                    }
                )
        page = {
            "pageid": int(parsed.get("pageid", 0) or 0),
            "title": title,
            "summary": parse_fandom_intro(rendered_html, self.max_summary_chars),
            "url": self._page_url(title),
            "image_url": parse_fandom_image_url(rendered_html),
            "infobox": infobox,
            "sections": parse_fandom_sections(rendered_html),
            "rich_sections": parse_fandom_rich_sections(rendered_html),
            "section_index": section_index,
            "categories": category_names,
        }
        page["language_names"] = parse_fandom_language_names(title, infobox, langlinks)
        return page

    async def get_page(self, title: str) -> dict[str, Any] | None:
        title = self._title_from_url(title.strip())
        if not title:
            return None
        key = f"page:{title.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            data = await self._request(
                {
                    "action": "parse",
                    "page": title,
                    "redirects": 1,
                    "prop": "text|sections|categories|langlinks",
                }
            )
        except KirbyFandomError as exc:
            if exc.code in {"missingtitle", "invalidtitle"}:
                self._cache_set(key, None)
                return None
            raise
        result = self._normalise_page(data, title)
        self._cache_set(key, result)
        return result

    async def search_pages(
        self, query: str, mode: str = "title", limit: int = 6
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        key = f"search:{mode}:{limit}:{query.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srwhat": mode,
                "srnamespace": 0,
                "srlimit": max(1, min(limit, 20)),
                "srprop": "snippet|wordcount",
            }
        )
        rows = data.get("query", {}).get("search", [])
        result: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or not row.get("pageid"):
                    continue
                title = str(row.get("title", "") or "").strip()
                result.append(
                    {
                        "pageid": int(row.get("pageid", 0) or 0),
                        "title": title,
                        "url": self._page_url(title),
                        "snippet": str(row.get("snippet", "") or ""),
                        "wordcount": int(row.get("wordcount", 0) or 0),
                    }
                )
        self._cache_set(key, result)
        return result

    @staticmethod
    def _clean_search_text(value: str) -> str:
        return re.sub(
            r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ")
        ).strip()

    @classmethod
    def _score_page(cls, query: str, page: dict[str, Any]) -> int:
        folded_query = query.casefold().strip()
        folded_title = str(page.get("title", "") or "").casefold()
        snippet = cls._clean_search_text(str(page.get("snippet", "") or "")).casefold()
        score = 0
        if folded_title == folded_query:
            score += 100
        elif folded_query and folded_query in folded_title:
            score += 55
        if folded_query and folded_query in snippet:
            score += 30
        for token in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", folded_query):
            if token in folded_title:
                score += 12
            elif token in snippet:
                score += 4
        if " (" in folded_title and " (" not in folded_query:
            score -= 8
        if folded_title.endswith("/gallery") or " gallery" in folded_title:
            score -= 25
        return score

    async def resolve(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"error": "empty_query"}
        exact = await self.get_page(query)
        if exact:
            return {"kind": "page", "page": exact}

        candidates: dict[int, dict[str, Any]] = {}
        title_results = await self.search_pages(query, mode="title")
        for page in title_results:
            candidates[page["pageid"]] = page
        if not title_results or re.search(r"[\u3400-\u9fff]", query):
            for page in await self.search_pages(query, mode="text"):
                candidates.setdefault(page["pageid"], page)
        ranked = sorted(
            candidates.values(),
            key=lambda page: self._score_page(query, page),
            reverse=True,
        )
        if not ranked:
            return {"error": "not_found", "query": query}
        for page in ranked:
            page["score"] = self._score_page(query, page)
        if (
            len(ranked) == 1
            or ranked[0]["score"] >= 80
            or ranked[0]["score"] - ranked[1]["score"] >= 12
        ):
            page = await self.get_page(str(ranked[0]["title"]))
            return {"kind": "page", "page": page} if page else {"error": "not_found"}
        return {"kind": "candidates", "candidates": ranked[:5]}

    def get_language_names(self, page: dict[str, Any]) -> list[dict[str, str]]:
        return [dict(row) for row in page.get("language_names", [])]

    def get_section_titles(self, page: dict[str, Any]) -> list[dict[str, str]]:
        return [dict(row) for row in page.get("section_index", [])]

    def get_page_details(
        self, page: dict[str, Any], section: str = ""
    ) -> dict[str, Any]:
        sections = [dict(row) for row in page.get("sections", [])]
        rich_sections = [dict(row) for row in page.get("rich_sections", [])]
        if section.strip():
            target = _normalise_heading(section)
            exact = [
                row for row in sections if _normalise_heading(row["title"]) == target
            ]
            partial = [
                row
                for row in sections
                if target and target in _normalise_heading(row["title"])
            ]
            matched = exact or partial
            rich_exact = [
                row
                for row in rich_sections
                if _normalise_heading(str(row.get("title", ""))) == target
                or target
                in {
                    _normalise_heading(str(ancestor))
                    for ancestor in row.get("ancestors", [])
                }
            ]
            rich_partial = [
                row
                for row in rich_sections
                if target
                and (
                    target in _normalise_heading(str(row.get("title", "")))
                    or any(
                        target in _normalise_heading(str(ancestor))
                        for ancestor in row.get("ancestors", [])
                    )
                )
            ]
            matched_rich = rich_exact or rich_partial
            if not matched:
                index_rows = page.get("section_index", [])
                parent_position = next(
                    (
                        index
                        for index, row in enumerate(index_rows)
                        if _normalise_heading(str(row.get("title", ""))) == target
                    ),
                    -1,
                )
                if parent_position >= 0:
                    try:
                        parent_level = int(
                            index_rows[parent_position].get("level", 2) or 2
                        )
                    except (TypeError, ValueError):
                        parent_level = 2
                    child_titles: set[str] = set()
                    for row in index_rows[parent_position + 1 :]:
                        try:
                            level = int(row.get("level", 2) or 2)
                        except (TypeError, ValueError):
                            level = 2
                        if level <= parent_level:
                            break
                        child_titles.add(_normalise_heading(str(row.get("title", ""))))
                    matched = [
                        row
                        for row in sections
                        if _normalise_heading(row["title"]) in child_titles
                    ]
            if matched or matched_rich:
                selected_rich, rich_cost = _trim_rich_sections(
                    matched_rich,
                    self.max_detail_chars
                    if matched_rich and not matched
                    else max(1200, self.max_detail_chars // 2),
                )
                selected: list[dict[str, str]] = []
                remaining = max(0, self.max_detail_chars - rich_cost)
                for value in matched:
                    if remaining <= 0:
                        break
                    row = dict(value)
                    row["text"] = _trim_text(row["text"], remaining)
                    selected.append(row)
                    remaining -= len(row["title"]) + len(row["text"])
                return {
                    "infobox": [],
                    "sections": selected,
                    "rich_sections": selected_rich,
                    "categories": [],
                }
            return {
                "infobox": [],
                "sections": [],
                "rich_sections": [],
                "categories": [],
            }

        rich_budget = min(4200, max(1600, self.max_detail_chars // 2))
        rich_for_default = sorted(
            rich_sections,
            key=lambda row: 0 if row.get("kind") == "quotes" else 1,
        )
        selected_rich, rich_cost = _trim_rich_sections(
            rich_for_default, rich_budget
        )
        selected: list[dict[str, str]] = []
        remaining = max(0, self.max_detail_chars - rich_cost)
        for row in sections:
            if remaining <= 0:
                break
            body = _trim_text(row["text"], remaining)
            if not body:
                continue
            selected.append({"title": row["title"], "text": body})
            remaining -= len(row["title"]) + len(body)
        categories = (
            [{"label": "分类", "value": "、".join(page.get("categories", [])[:10])}]
            if page.get("categories")
            else []
        )
        return {
            "infobox": [dict(row) for row in page.get("infobox", [])],
            "sections": selected,
            "rich_sections": selected_rich,
            "categories": categories,
        }

    def _image_bytes_sync(self, image_url: str) -> bytes:
        parsed = urlparse(image_url)
        if (parsed.hostname or "").casefold() not in {
            "static.wikia.nocookie.net",
            "vignette.wikia.nocookie.net",
            "kirby.fandom.com",
        }:
            raise KirbyFandomError("图片来源不是 Kirby Fandom CDN")
        request = Request(
            image_url,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": FANDOM_SITE_URL + "/",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise KirbyFandomError("Kirby Fandom 首图下载失败") from exc

    async def get_image_bytes(self, image_url: str) -> bytes | None:
        if not image_url:
            return None
        try:
            return await asyncio.to_thread(self._image_bytes_sync, image_url)
        except KirbyFandomError:
            return None
