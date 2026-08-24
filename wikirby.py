from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import Tag

DEFAULT_API_URL = "https://wikirby.com/w/api.php"
FALLBACK_API_URL = "https://www.wikirby.com/w/api.php"
DEFAULT_REST_URL = "https://wikirby.com/w/rest.php"
FALLBACK_REST_URL = "https://www.wikirby.com/w/rest.php"
USER_AGENT = (
    "astrbot-plugin-kirby-catalog/2.10.3 "
    "(+https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)"
)
_RETRYABLE_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}

_NAMES_HEADING = re.compile(
    r"^==+\s*Names in other languages\s*==+\s*$", re.IGNORECASE | re.MULTILINE
)
_SECTION_HEADING = re.compile(r"^==\s*([^=\n]+?)\s*==\s*$", re.MULTILINE)
_DETAIL_SECTION_LABELS = {
    "locations": "出现地点",
    "trivia": "趣闻",
}
_SKIPPED_DETAIL_SECTIONS = {
    "gallery",
    "names in other languages",
    "references",
}
_INFOBOX_LABELS = {
    "game1": "出现作品",
    "game2": "出现作品",
    "game3": "出现作品",
    "copy ability": "提供能力",
    "similar": "相似角色",
    "species": "种类",
    "gender": "性别",
    "location": "出现地点",
}
_LANGUAGE_FIELDS = (
    ("ja", "日语", "jaR"),
    ("en", "英语", "enR"),
    ("zhTrad", "繁体中文", "zhTradR"),
    ("zhSimp", "简体中文", "zhSimpR"),
    ("ko", "韩语", "koR"),
    ("nl", "荷兰语", "nlR"),
    ("fr", "法语", "frR"),
    ("de", "德语", "deR"),
    ("it", "意大利语", "itR"),
    ("es", "西班牙语", "esR"),
    ("ptA", "葡萄牙语", "ptAR"),
    ("pl", "波兰语", "plR"),
    ("ru", "俄语", "ruR"),
    ("th", "泰语", "thR"),
)
_RENDERED_LANGUAGE_LABELS = {
    "japanese": "日语",
    "english": "英语",
    "traditional chinese": "繁体中文",
    "simplified chinese": "简体中文",
    "chinese": "中文",
    "korean": "韩语",
    "dutch": "荷兰语",
    "french": "法语",
    "german": "德语",
    "italian": "意大利语",
    "spanish": "西班牙语",
    "latin american spanish": "拉丁美洲西班牙语",
    "european spanish": "欧洲西班牙语",
    "portuguese": "葡萄牙语",
    "brazilian portuguese": "巴西葡萄牙语",
    "polish": "波兰语",
    "russian": "俄语",
    "thai": "泰语",
}


class WikirbyError(RuntimeError):
    """Raised when WiKirby cannot answer a read-only API request."""


def _clean_wiki_value(value: str) -> str:
    """Turn a small Names-template value into safe plain text."""
    text = value or ""
    text = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"\{\{\s*furi\s*\|\s*([^|{}]+)(?:\|[^{}]*)?\}\}",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\{\{\s*(?:small|nowrap|ruby)\s*\|\s*([^{}]*)\}\}",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    text = re.sub(r"<br\s*/?>", " / ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"\[\d+\]", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s*/\s*", " / ", text)
    return re.sub(r"\s+", " ", text).strip(" /")


def _clean_wiki_block(value: str) -> str:
    """Convert a small wikitext section to readable plain text."""
    text = value or ""
    while "{{" in text:
        start = text.find("{{")
        end = _find_template_end(text, start)
        text = text[:start] + " " + text[end:]
    text = re.sub(
        r"\{\|[\s\S]*?\|\}",
        lambda match: "\n" + _clean_wiki_table(match.group(0)) + "\n",
        text,
    )
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<gallery[\s\S]*?</gallery>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<ref[^>]*>[\s\S]*?</ref>|<ref[^>]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^={3,6}\s*(.*?)\s*={3,6}\s*$",
        r"\1：",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^\s*(?:(?:File|Image):|thumb(?:\||$)|left\s*$|right\s*$).*$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(r"\[https?://[^\s\]]+\s+([^]]+)\]", r"\1", text)
    text = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        if line.startswith(("*", "#")):
            line = "• " + line.lstrip("*#").strip()
        lines.append(line)
    return "\n".join(lines)


def _clean_wiki_table(table_text: str) -> str:
    """Convert a basic MediaWiki table into compact readable rows."""
    rows: list[list[str]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        values = [_clean_wiki_table_cell(value) for value in current]
        values = [value for value in values if value]
        if values:
            rows.append(values)
        current = []

    for raw_line in table_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{| ") or line == "{|" or line == "|}":
            continue
        if line.startswith("|-"):
            flush()
            continue
        if line.startswith("!"):
            current.extend(line[1:].split("!!"))
            continue
        if line.startswith("|"):
            current.extend(line[1:].split("||"))
            continue
        if current:
            current[-1] += " " + line
    flush()

    output: list[str] = []
    for values in rows:
        if len(values) == 1:
            output.append(values[0])
        else:
            output.append("• " + " — ".join(dict.fromkeys(values)))
    return "\n".join(output)


def _clean_wiki_table_cell(value: str) -> str:
    text = value.strip()
    if "|" in text:
        attributes, content = text.split("|", 1)
        if "=" in attributes:
            text = content
    text = re.sub(
        r"(?:thumb|left|right|center|\d+px)(?:\|[^\]\n]*)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = _clean_wiki_value(text)
    if re.fullmatch(r"(?:\d+px|yes|no)", text, re.IGNORECASE):
        return ""
    return text


def parse_rendered_sections(rendered_html: str) -> list[dict[str, Any]]:
    """Extract readable article sections from MediaWiki's rendered HTML."""
    if not rendered_html.strip():
        return []
    soup = BeautifulSoup(rendered_html, "html.parser")
    root = soup.select_one(".mw-parser-output")
    if root is None:
        return []

    sections: list[dict[str, Any]] = []
    title = ""
    level = 2
    source_position = 0
    heading_position = 0
    parts: list[str] = []
    active = False

    def flush() -> None:
        nonlocal parts
        body = "\n".join(dict.fromkeys(part for part in parts if part)).strip()
        if title and body:
            sections.append(
                {
                    "title": title,
                    "text": body,
                    "level": str(level),
                    "source_position": source_position,
                }
            )
        parts = []

    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.name in {"h2", "h3", "h4"}:
            heading_position += 1
            heading = _rendered_heading_text(child)
            if child.name == "h2":
                flush()
                folded = heading.casefold()
                active = bool(heading) and folded not in _SKIPPED_DETAIL_SECTIONS
                title = _DETAIL_SECTION_LABELS.get(folded, heading) if active else ""
                level = 2
                source_position = heading_position
            elif active and heading:
                flush()
                title = heading
                level = int(child.name[1])
                source_position = heading_position
            continue
        if not active:
            continue

        if child.name == "p":
            value = _rendered_text(child)
            if value:
                parts.append(value)
            continue
        if child.name in {"ul", "ol"}:
            for item in child.find_all("li", recursive=False):
                value = _rendered_text(item)
                if value:
                    parts.append(f"• {value}")
            continue
        if child.name == "dl":
            value = _rendered_text(child)
            if value:
                parts.append(value)
            continue
        if child.name == "table":
            table_lines = _rendered_table_lines(child)
            if table_lines:
                parts.extend(table_lines)
            elif parts and re.search(
                r"(?:following|can be found|locations?)[^.!?]*[:：]\s*$",
                parts[-1],
                re.IGNORECASE,
            ):
                parts.pop()
            continue
        if child.name == "div" and "display:flex" in str(child.get("style", "")):
            value = _rendered_text(child)
            if value:
                parts.append(value)
    flush()
    return sections


def _rendered_heading_text(heading: Tag) -> str:
    headline = heading.select_one(".mw-headline")
    return _rendered_text(headline or heading).removesuffix("[ edit ]").strip()


def _rendered_text(element: Tag) -> str:
    text = html.unescape(element.get_text(" ", strip=True))
    text = re.sub(r"\[\s*edit\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\d+(?:\.\d+)?\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _rendered_table_lines(table: Tag) -> list[str]:
    classes = {str(value).casefold() for value in table.get("class", [])}
    if classes & {"navbox", "wikirby-infobox", "metadata", "mbox-small"}:
        return []

    sample = _rendered_text(table)[:800].casefold()
    if "locations in " in sample or (
        "appearance?" in sample and "stage" in sample
    ):
        return []

    output: list[str] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        values = [_rendered_text(cell) for cell in cells]
        values = [
            value
            for value in dict.fromkeys(values)
            if value and not re.fullmatch(r"\d+px", value, re.IGNORECASE)
        ]
        if not values:
            continue
        if all(cell.name == "th" for cell in cells):
            if len(values) == 1 and not values[0].casefold().endswith("appearances"):
                output.append(values[0])
            continue
        line = " — ".join(values)
        output.append(f"• {line}")
    return output


def parse_rendered_language_names(rendered_html: str) -> list[dict[str, str]]:
    """Read hand-authored Language/Name tables below WiKirby's names heading."""
    if not rendered_html.strip():
        return []
    soup = BeautifulSoup(rendered_html, "html.parser")
    root = soup.select_one(".mw-parser-output")
    if root is None:
        return []

    rows: list[dict[str, str]] = []
    active = False
    section = ""
    seen: set[tuple[str, str, str, str]] = set()
    for child in root.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "h2":
            heading = _rendered_heading_text(child).casefold()
            active = "names in other languages" in heading
            section = ""
            continue
        if not active:
            continue
        if child.name in {"h3", "h4"}:
            section = _rendered_heading_text(child)
            continue
        if child.name != "table":
            continue
        for row in _rendered_language_table_rows(child, section):
            key = (
                row.get("section", ""),
                row["language"],
                row["name"],
                row.get("romanisation", ""),
            )
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def _rendered_language_table_rows(
    table: Tag, section: str
) -> list[dict[str, str]]:
    table_rows = table.find_all("tr")
    header_index = -1
    language_index = -1
    name_index = -1
    for index, table_row in enumerate(table_rows):
        cells = table_row.find_all(["th", "td"], recursive=False)
        headers = [_rendered_text(cell).casefold().rstrip(":") for cell in cells]
        language_index = next(
            (position for position, value in enumerate(headers) if "language" in value),
            -1,
        )
        name_index = next(
            (position for position, value in enumerate(headers) if value == "name"),
            -1,
        )
        if language_index >= 0 and name_index >= 0:
            header_index = index
            break
    if header_index < 0:
        return []

    rows: list[dict[str, str]] = []
    for table_row in table_rows[header_index + 1 :]:
        cells = table_row.find_all(["th", "td"], recursive=False)
        if len(cells) <= max(language_index, name_index):
            continue
        language = _rendered_language_label(_rendered_text(cells[language_index]))
        name, romanisation = _rendered_language_name_value(cells[name_index])
        if not language or not name:
            continue
        row = {"language": language, "name": name}
        if section:
            row["section"] = section
        if romanisation:
            row["romanisation"] = romanisation
        rows.append(row)
    return rows


def _rendered_language_label(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return _RENDERED_LANGUAGE_LABELS.get(normalized, value.strip())


def _rendered_language_name_value(cell: Tag) -> tuple[str, str]:
    """Separate visible names from italic romanisation in one table cell."""
    fragment = BeautifulSoup(str(cell), "html.parser")
    for element in fragment.select("sup, .reference, small, rt"):
        element.decompose()
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")

    romanisation_lines = _unique_rendered_lines(
        _rendered_text(element) for element in fragment.find_all(["i", "em"])
    )
    # Keep inline markup inside one official name; only <br> separates variants.
    lines = _unique_rendered_lines(fragment.get_text().splitlines())
    names: list[str] = []
    romanisation: list[str] = []
    for line in lines:
        if line in romanisation_lines or (
            names and _contains_non_latin_letter(names[-1]) and _is_latin_name(line)
        ):
            romanisation.append(line)
        else:
            names.append(line)
    return " / ".join(names), " / ".join(dict.fromkeys(romanisation))


def _unique_rendered_lines(values: Any) -> list[str]:
    lines: list[str] = []
    for value in values:
        text = html.unescape(str(value or ""))
        text = re.sub(r"\[\s*\d+(?:\.\d+)?\s*\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text and text not in lines:
            lines.append(text)
    return lines


def _contains_non_latin_letter(value: str) -> bool:
    return any(char.isalpha() and not char.isascii() for char in value)


def _is_latin_name(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z\u00c0-\u024f'’ .-]+", value.strip())
    )


def parse_page_details(
    wikitext: str, rendered_html: str = ""
) -> dict[str, list[dict[str, str]]]:
    """Extract selected infobox fields and useful prose sections."""
    source = wikitext or ""
    infobox_rows: list[dict[str, str]] = []
    infobox_match = re.search(r"\{\{\s*Infobox[^\n]*", source, re.IGNORECASE)
    if infobox_match:
        end = _find_template_end(source, infobox_match.start())
        block = source[infobox_match.start() : end]
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line.startswith("|") or "=" not in line:
                continue
            key, value = line[1:].split("=", 1)
            label = _INFOBOX_LABELS.get(key.strip().casefold())
            clean_value = _clean_wiki_value(value)
            if label and clean_value:
                if infobox_rows and infobox_rows[-1]["label"] == label:
                    infobox_rows[-1]["value"] += "、" + clean_value
                else:
                    infobox_rows.append({"label": label, "value": clean_value})

    sections = parse_rendered_sections(rendered_html)
    if not sections:
        headings = list(_SECTION_HEADING.finditer(source))
        for index, heading in enumerate(headings):
            title = heading.group(1).strip()
            folded_title = title.casefold()
            if folded_title in _SKIPPED_DETAIL_SECTIONS:
                continue
            section_end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(source)
            )
            content = _clean_wiki_block(source[heading.end() : section_end])
            if not content:
                continue
            sections.append(
                {
                    "title": _DETAIL_SECTION_LABELS.get(folded_title, title),
                    "text": content,
                }
            )
    locations = parse_locations_html(rendered_html)
    if locations:
        for section in sections:
            if section["title"] == "出现地点":
                section["text"] = (
                    f'{section["text"].rstrip()}\n'
                    + "\n".join(f"• {location}" for location in locations)
                )
                break
    language_names = parse_language_names(source) or parse_rendered_language_names(
        rendered_html
    )
    if language_names:
        name_lines: list[str] = []
        previous_section = ""
        for row in language_names:
            section = str(row.get("section", "") or "").strip()
            if section and section != previous_section:
                name_lines.append(f"【{section}】")
                previous_section = section
            value = row["name"]
            if row.get("romanisation"):
                value += f'（{row["romanisation"]}）'
            name_lines.append(f'• {row["language"]}：{value}')
        sections.append(
            {
                "title": "其他语言名称",
                "text": "\n".join(name_lines),
            }
        )
    return {"infobox": infobox_rows, "sections": sections}


def _find_template_end(text: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(text) - 1:
        if text.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if text.startswith("}}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return len(text)


class _RenderedTableParser(HTMLParser):
    """Collect simple cells from rendered MediaWiki tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict[str, Any]]]] = []
        self._table_depth = 0
        self._current_table: list[list[dict[str, Any]]] | None = None
        self._current_row: list[dict[str, Any]] | None = None
        self._current_cell: dict[str, Any] | None = None
        self._current_link: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            if self._table_depth == 0 and "wikitable" in {
                value for value in (attributes.get("class") or "").split()
            }:
                self._current_table = []
            self._table_depth += 1
            return
        if self._table_depth != 1 or self._current_table is None:
            return
        if tag == "tr":
            self._current_row = []
            return
        if tag in {"th", "td"} and self._current_row is not None:
            self._current_cell = {
                "tag": tag,
                "text": [],
                "links": [],
                "image_alts": [],
            }
            self._current_row.append(self._current_cell)
            return
        if tag == "a" and self._current_cell is not None:
            self._current_link = []
            return
        if tag == "img" and self._current_cell is not None:
            alt = (attributes.get("alt") or "").strip()
            if alt:
                self._current_cell["image_alts"].append(alt)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
            if self._table_depth == 0 and self._current_table is not None:
                self.tables.append(self._current_table)
                self._current_table = None
            return
        if self._table_depth != 1:
            return
        if tag == "a" and self._current_cell is not None and self._current_link is not None:
            link_text = " ".join(self._current_link).strip()
            if link_text:
                self._current_cell["links"].append(link_text)
            self._current_link = None
        elif tag in {"th", "td"}:
            self._current_cell = None
            self._current_link = None
        elif tag == "tr" and self._current_table is not None and self._current_row is not None:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is None:
            return
        self._current_cell["text"].append(data)
        if self._current_link is not None:
            self._current_link.append(data)


def parse_locations_html(rendered_html: str) -> list[str]:
    """Extract stages marked Yes from the rendered Locations table."""
    if not rendered_html:
        return []
    parser = _RenderedTableParser()
    parser.feed(rendered_html)
    parser.close()

    locations: list[str] = []
    for table in parser.tables:
        table_headers = {
            re.sub(r"\s+", " ", " ".join(cell["text"])).strip().casefold()
            for row in table
            for cell in row
            if cell["tag"] == "th"
        }
        if not any("stage" == header for header in table_headers):
            continue
        if not any("appearance" in header for header in table_headers):
            continue
        for row in table:
            for index in range(len(row) - 1):
                stage_cell = row[index]
                appearance_cell = row[index + 1]
                if stage_cell["tag"] != "td" or appearance_cell["tag"] != "td":
                    continue
                if not any(
                    str(value).casefold() == "yes"
                    for value in appearance_cell["image_alts"]
                ):
                    continue
                names = stage_cell["links"] or [
                    re.sub(r"\s+", " ", " ".join(stage_cell["text"])).strip()
                ]
                name = names[0].strip() if names else ""
                if name and name not in locations:
                    locations.append(name)
    return locations


def _names_template_block(wikitext: str) -> str:
    if not wikitext:
        return ""
    heading = _NAMES_HEADING.search(wikitext)
    search_start = heading.end() if heading else 0
    next_heading = re.search(r"^==+\s*.+?\s*==+\s*$", wikitext[search_start:], re.MULTILINE)
    section_end = search_start + next_heading.start() if next_heading else len(wikitext)
    section = wikitext[search_start:section_end]
    match = re.search(r"\{\{\s*Names\b", section, re.IGNORECASE)
    if not match:
        return ""
    return section[match.start() : _find_template_end(section, match.start())]


def parse_language_names(wikitext: str) -> list[dict[str, str]]:
    """Extract the visible language/name rows from WiKirby's Names template."""
    block = _names_template_block(wikitext)
    if not block:
        return []

    fields: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "=" not in line:
            continue
        key, value = line[1:].split("=", 1)
        key = key.strip()
        if key:
            fields[key] = value.strip()

    rows: list[dict[str, str]] = []
    for field, language, romanisation_field in _LANGUAGE_FIELDS:
        name = _clean_wiki_value(fields.get(field, ""))
        romanisation = _clean_wiki_value(fields.get(romanisation_field, ""))
        if not name:
            continue
        row = {"language": language, "name": name}
        if romanisation:
            row["romanisation"] = romanisation
        rows.append(row)
    return rows


class WikirbyClient:
    """Small, read-only MediaWiki client specialized for WiKirby."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 12.0,
        cache_ttl_seconds: int = 3600,
        max_summary_chars: int | None = None,
        proxy_url: str = "",
        proxy_token: str = "",
    ) -> None:
        self.api_url = api_url.strip() or DEFAULT_API_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.proxy_url = proxy_url.strip().rstrip("/")
        self.proxy_token = proxy_token.strip()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._request_limit = asyncio.Semaphore(2)

    def _api_urls(self) -> tuple[str, ...]:
        """Return the configured API and the alternate WiKirby hostname."""
        if self.api_url == DEFAULT_API_URL:
            return (DEFAULT_API_URL, FALLBACK_API_URL)
        urls = [self.api_url]
        parsed = urlparse(self.api_url)
        hostname = (parsed.hostname or "").lower()
        if hostname in {"wikirby.com", "www.wikirby.com"}:
            alternate_host = "www.wikirby.com" if hostname == "wikirby.com" else "wikirby.com"
            alternate = parsed._replace(netloc=alternate_host).geturl()
            if alternate not in urls:
                urls.append(alternate)
        return tuple(urls)

    def _rest_urls(self) -> tuple[str, ...]:
        """Return REST API bases matching the configured MediaWiki hostnames."""
        if self.api_url == DEFAULT_API_URL:
            return (DEFAULT_REST_URL, FALLBACK_REST_URL)
        urls: list[str] = []
        for api_url in self._api_urls():
            parsed = urlparse(api_url)
            path = parsed.path or "/w/api.php"
            if path.endswith("/api.php"):
                path = f"{path[:-len('api.php')]}rest.php"
            else:
                path = "/w/rest.php"
            rest_url = parsed._replace(
                path=path, params="", query="", fragment=""
            ).geturl().rstrip("/")
            if rest_url not in urls:
                urls.append(rest_url)
        return tuple(urls)

    def _site_urls(self) -> tuple[str, ...]:
        """Return the public site roots used by raw page requests."""
        urls: list[str] = []
        for api_url in self._api_urls():
            parsed = urlparse(api_url)
            site_url = parsed._replace(
                path="", params="", query="", fragment=""
            ).geturl().rstrip("/")
            if site_url not in urls:
                urls.append(site_url)
        return tuple(urls)

    @staticmethod
    def _retry_delay(error: HTTPError, attempt: int) -> float:
        retry_after = error.headers.get("Retry-After") if error.headers else None
        try:
            return max(0.2, min(float(retry_after), 3.0))
        except (TypeError, ValueError):
            return 0.4 * (2**attempt)

    async def close(self) -> None:
        self.clear_cache()

    def clear_cache(self) -> int:
        count = len(self._cache)
        self._cache.clear()
        return count

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
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, value)

    def _read_urls_sync(
        self,
        urls: tuple[str, ...],
        query: dict[str, Any] | None = None,
        use_proxy: bool = True,
    ) -> bytes:
        """Read one of several equivalent endpoints with short WAF retries."""
        raw: bytes | None = None
        last_http_error: HTTPError | None = None
        last_url_error: URLError | None = None
        query = query or {}
        request_bases = urls[:1] if self.proxy_url and use_proxy else urls
        for base_url in request_bases:
            if self.proxy_url and use_proxy:
                target_path = urlparse(base_url).path or "/"
                proxy_query = {"path": target_path, **query}
                separator = "&" if "?" in self.proxy_url else "?"
                url = f"{self.proxy_url}{separator}{urlencode(proxy_query)}"
            else:
                separator = "&" if "?" in base_url else "?"
                suffix = urlencode(query)
                url = f"{base_url}{separator}{suffix}" if suffix else base_url
            headers = {
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://wikirby.com/",
                "User-Agent": USER_AGENT,
            }
            if self.proxy_token and self.proxy_url and use_proxy:
                headers["Authorization"] = f"Bearer {self.proxy_token}"
            request = Request(
                url,
                headers=headers,
            )
            for attempt in range(2):
                try:
                    with urlopen(request, timeout=self.timeout_seconds) as response:
                        raw = response.read()
                except HTTPError as exc:
                    last_http_error = exc
                    if exc.code not in _RETRYABLE_HTTP_CODES or attempt == 1:
                        break
                    time.sleep(self._retry_delay(exc, attempt))
                except URLError as exc:
                    last_url_error = exc
                    if attempt == 1:
                        break
                    time.sleep(0.4 * (2**attempt))
                except TimeoutError as exc:
                    last_url_error = URLError(str(exc))
                    if attempt == 1:
                        break
                    time.sleep(0.4 * (2**attempt))
                else:
                    break
            if raw is not None:
                break

        if raw is None:
            if last_http_error is not None:
                raise WikirbyError(
                    f"WiKirby API 返回 HTTP {last_http_error.code}，请稍后再试"
                ) from last_http_error
            if last_url_error is not None:
                raise WikirbyError("无法连接 WiKirby，请稍后再试") from last_url_error
            raise WikirbyError("无法连接 WiKirby，请稍后再试")
        return raw

    @staticmethod
    def _decode_json(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikirbyError("WiKirby 返回了无法识别的数据") from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("info", "WiKirby API 请求失败") if isinstance(error, dict) else str(error)
            raise WikirbyError(message)
        if not isinstance(data, dict):
            raise WikirbyError("WiKirby 返回了无法识别的数据")
        return data

    def _request_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {"format": "json", "formatversion": "2", **params}
        raw = self._read_urls_sync(self._api_urls(), query)
        return self._decode_json(raw)

    def _image_bytes_sync(self, image_url: str) -> bytes:
        parsed = urlparse(image_url)
        if parsed.hostname not in {"cdn.wikirby.com", "wikirby.com"}:
            raise WikirbyError("图片来源不是 WiKirby CDN")
        query = {"asset": "image"} if self.proxy_url else None
        return self._read_urls_sync((image_url,), query, use_proxy=bool(self.proxy_url))

    async def get_image_bytes(self, image_url: str) -> bytes | None:
        """Download a WiKirby image, using the configured relay when present."""
        if not image_url:
            return None
        try:
            return await asyncio.to_thread(self._image_bytes_sync, image_url)
        except WikirbyError:
            return None

    def _rest_request_sync(
        self, endpoint: str, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        urls = tuple(
            f"{base.rstrip('/')}/{endpoint.lstrip('/')}"
            for base in self._rest_urls()
        )
        return self._decode_json(self._read_urls_sync(urls, query))

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        async with self._request_limit:
            return await asyncio.to_thread(self._request_sync, params)

    @staticmethod
    def _page_values(data: dict[str, Any]) -> list[dict[str, Any]]:
        pages = data.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            return [value for value in pages.values() if isinstance(value, dict)]
        return [value for value in pages if isinstance(value, dict)]

    @staticmethod
    def _normalise_page(page: dict[str, Any]) -> dict[str, Any] | None:
        if page.get("missing") is not None or page.get("pageid") in {-1, None}:
            return None
        thumbnail = page.get("thumbnail") or {}
        return {
            "pageid": int(page.get("pageid", 0) or 0),
            "title": str(page.get("title", "")).strip(),
            "summary": str(page.get("extract", "") or "").strip(),
            "url": str(page.get("fullurl", "") or "").strip(),
            "image_url": str(thumbnail.get("source", "") or "").strip(),
            "lastrevid": int(page.get("lastrevid", 0) or 0),
        }

    @staticmethod
    def _page_url(title: str) -> str:
        return "https://wikirby.com/wiki/" + quote(
            title.replace(" ", "_"), safe="():"
        )

    @staticmethod
    def _summary_from_wikitext(
        wikitext: str, max_chars: int | None = None
    ) -> str:
        """Extract the complete readable introduction from REST wikitext.

        ``max_chars`` remains in the signature for compatibility and is ignored.
        """
        text = re.split(r"^==+\s*", wikitext or "", maxsplit=1, flags=re.MULTILINE)[0]
        while "{{" in text:
            start = text.find("{{")
            end = _find_template_end(text, start)
            text = text[:start] + " " + text[end:]
        text = re.sub(r"\[https?://[^\s\]]+\s+([^]]+)\]", r"\1", text)
        text = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", text)
        text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("'''", "").replace("''", "")
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _image_url_from_wikitext(wikitext: str) -> str:
        match = re.search(
            r"\[\[\s*(?:File|Image)\s*:\s*([^|\]\n]+)",
            wikitext or "",
            re.IGNORECASE,
        )
        if not match:
            return ""
        filename = match.group(1).strip().replace(" ", "_")
        digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
        return (
            f"https://cdn.wikirby.com/{digest[0]}/{digest[:2]}/"
            f"{quote(filename, safe='')}"
        )

    def _rest_page_sync(self, title: str) -> dict[str, Any]:
        title = self._title_from_url(title.strip())
        data = self._rest_request_sync(
            f"v1/page/{quote(title, safe='')}"
        )
        actual_title = str(data.get("title") or title).strip()
        source = str(data.get("source") or "")
        latest = data.get("latest") or {}
        return {
            "pageid": int(data.get("id", 0) or 0),
            "title": actual_title,
            "summary": self._summary_from_wikitext(source),
            "url": self._page_url(actual_title),
            "image_url": self._image_url_from_wikitext(source),
            "lastrevid": int(latest.get("id", 0) or 0),
            "wikitext": source,
        }

    def _rest_search_sync(self, query: str, limit: int) -> list[dict[str, Any]]:
        data = self._rest_request_sync(
            "v1/search/page",
            {"q": query, "limit": max(1, min(limit, 20))},
        )
        rows = data.get("pages", [])
        if not isinstance(rows, list):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            title = str(row.get("title") or row.get("key") or "").strip()
            if not title:
                continue
            excerpt = str(row.get("excerpt") or "")
            thumbnail = row.get("thumbnail") or {}
            result.append(
                {
                    "pageid": int(row.get("id", 0) or 0),
                    "title": title,
                    "summary": self._clean_search_text(excerpt),
                    "url": self._page_url(title),
                    "image_url": str(thumbnail.get("url", "") or ""),
                    "lastrevid": 0,
                    "snippet": excerpt,
                    "wordcount": 0,
                }
            )
        return result

    def _raw_page_sync(self, title: str) -> dict[str, Any]:
        """Read a page through MediaWiki's static raw endpoint."""
        raw_urls = tuple(
            f"{site}/w/index.php" for site in self._site_urls()
        )
        raw = self._read_urls_sync(
            raw_urls, {"title": title, "action": "raw"}
        )
        wikitext = raw.decode("utf-8", errors="replace").strip()
        if not wikitext or re.search(
            r"<title>\s*Just a moment", wikitext[:2000], re.IGNORECASE
        ):
            raise WikirbyError("WiKirby raw 页面也被 Cloudflare 拦截")
        return {
            "pageid": 0,
            "title": title,
            "summary": self._summary_from_wikitext(wikitext),
            "url": self._page_url(title),
            "image_url": self._image_url_from_wikitext(wikitext),
            "lastrevid": 0,
            "wikitext": wikitext,
        }

    @staticmethod
    def _title_from_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {"wikirby.com", "www.wikirby.com"}:
            return value
        marker = "/wiki/"
        if marker not in parsed.path:
            return value
        return unquote(parsed.path.split(marker, 1)[1]).replace("_", " ").strip()

    async def get_page(self, title: str) -> dict[str, Any] | None:
        title = self._title_from_url(title.strip())
        key = f"page:{title.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            data = await self._request(
                {
                    "action": "query",
                    "titles": title,
                    "redirects": 1,
                    "prop": "info|extracts|pageimages",
                    "inprop": "url",
                    "exintro": 1,
                    "explaintext": 1,
                    "piprop": "thumbnail",
                    "pithumbsize": 600,
                }
            )
            pages = [
                self._normalise_page(page) for page in self._page_values(data)
            ]
            result = next((page for page in pages if page), None)
        except WikirbyError:
            try:
                result = await asyncio.to_thread(self._rest_page_sync, title)
            except WikirbyError:
                try:
                    result = await asyncio.to_thread(self._raw_page_sync, title)
                except WikirbyError:
                    result = None
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
        try:
            search_data = await self._request(
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
        except WikirbyError:
            result = await asyncio.to_thread(
                self._rest_search_sync, query, limit
            )
            self._cache_set(key, result)
            return result
        rows = search_data.get("query", {}).get("search", [])
        if not isinstance(rows, list):
            return []
        pageids = [str(row.get("pageid")) for row in rows if row.get("pageid")]
        if not pageids:
            self._cache_set(key, [])
            return []

        try:
            details = await self._request(
                {
                    "action": "query",
                    "pageids": "|".join(pageids),
                    "redirects": 1,
                    "prop": "info|pageimages",
                    "inprop": "url",
                    "piprop": "thumbnail",
                    "pithumbsize": 600,
                }
            )
        except WikirbyError:
            result = await asyncio.to_thread(
                self._rest_search_sync, query, limit
            )
            self._cache_set(key, result)
            return result
        by_id = {
            str(page.get("pageid")): page
            for page in (
                self._normalise_page(value) for value in self._page_values(details)
            )
            if page
        }
        result: list[dict[str, Any]] = []
        for row in rows:
            page = by_id.get(str(row.get("pageid")))
            if not page:
                continue
            page["snippet"] = str(row.get("snippet", "") or "")
            page["wordcount"] = int(row.get("wordcount", 0) or 0)
            result.append(page)
        self._cache_set(key, result)
        return result

    @staticmethod
    def _clean_search_text(value: str) -> str:
        text = html.unescape(value or "")
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _score_page(cls, query: str, page: dict[str, Any]) -> int:
        folded_query = query.casefold().strip()
        folded_title = str(page.get("title", "")).casefold()
        snippet = cls._clean_search_text(str(page.get("snippet", ""))).casefold()
        score = 0
        if folded_title == folded_query:
            score += 100
        elif folded_query and folded_query in folded_title:
            score += 55
        if folded_query and folded_query in snippet:
            score += 30
        tokens = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", folded_query)
        for token in tokens:
            if token in folded_title:
                score += 12
            elif token in snippet:
                score += 4
        title = str(page.get("title", "")).casefold()
        if " (" in title and " (" not in folded_query:
            score -= 8
        if folded_query not in folded_title:
            score += min(int(page.get("wordcount", 0) or 0) // 3000, 4)
        if title.endswith("/gallery") or " (level)" in title:
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

    async def get_wikitext(self, title: str) -> str:
        key = f"wikitext:{title.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return str(cached)
        try:
            data = await self._request(
                {
                    "action": "query",
                    "titles": title,
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                }
            )
        except WikirbyError:
            try:
                page = await asyncio.to_thread(self._rest_page_sync, title)
            except WikirbyError:
                page = await asyncio.to_thread(self._raw_page_sync, title)
            content = str(page.get("wikitext", "") or "")
            self._cache_set(key, content)
            return content
        page = next(iter(self._page_values(data)), {})
        revisions = page.get("revisions", [])
        content = ""
        if revisions:
            revision = revisions[0]
            slots = revision.get("slots", {})
            main = slots.get("main", {}) if isinstance(slots, dict) else {}
            content = str(main.get("content", "") or "")
            if not content and isinstance(revision.get("content"), str):
                content = revision["content"]
        self._cache_set(key, content)
        return content

    async def get_language_names(self, page: dict[str, Any]) -> list[dict[str, str]]:
        pageid = page.get("pageid", 0)
        revision = page.get("lastrevid", 0)
        key = f"names:{pageid}:{revision}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        wikitext = str(page.get("wikitext", "") or "")
        if not wikitext:
            wikitext = await self.get_wikitext(str(page.get("title", "")))
        result = parse_language_names(wikitext)
        if not result:
            try:
                rendered_html = await self._get_rendered_page_html(
                    str(page.get("title", ""))
                )
                result = parse_rendered_language_names(rendered_html)
            except WikirbyError:
                # The template parser remains useful when rendering is blocked.
                pass
        self._cache_set(key, result)
        return result

    async def _get_rendered_page_html(self, title: str) -> str:
        """Render a page through MediaWiki so template-backed tables are readable."""
        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "disabletoc": 1,
            }
        )
        parsed = data.get("parse", {})
        rendered = parsed.get("text", "") if isinstance(parsed, dict) else ""
        if isinstance(rendered, dict):
            rendered = rendered.get("*", "")
        return str(rendered or "")

    async def get_page_details(self, page: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        """Load selected readable details, preferring rendered MediaWiki HTML."""
        pageid = page.get("pageid", 0)
        revision = page.get("lastrevid", 0)
        key = f"details:{pageid}:{revision}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        wikitext = str(page.get("wikitext", "") or "")
        if not wikitext:
            wikitext = await self.get_wikitext(str(page.get("title", "")))
        rendered_html = ""
        try:
            rendered_html = await self._get_rendered_page_html(
                str(page.get("title", ""))
            )
        except WikirbyError:
            # Wikitext still provides readable content when page rendering is blocked.
            pass
        result = parse_page_details(wikitext, rendered_html)
        self._cache_set(key, result)
        return result
