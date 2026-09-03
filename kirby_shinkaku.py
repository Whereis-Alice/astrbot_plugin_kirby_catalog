from __future__ import annotations

import asyncio
import html
import json
import re
import time
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

DEFAULT_SHINKAKU_SITE_URL = "https://seesaawiki.jp/kirby_shinkaku"
SHINKAKU_SITE_LABEL = "卡比真格攻略 Wiki"
SHINKAKU_ENGLISH_CORNER_TITLE = "英語のコーナー"
DEFAULT_SHINKAKU_PAGE_NAMES_PATH = (
    Path(__file__).resolve().parent / "resources" / "shinkaku_page_names.json"
)

# Seesaa Wiki rejects generic script clients more often than a normal browser
# request. This identifies the client as a compatible HTML reader without
# relying on browser-only cookies or JavaScript.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
_RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
_PROXY_FALLBACK_HTTP_CODES = _RETRYABLE_HTTP_CODES | {403}
_PROXY_PREFERENCE_SECONDS = 300.0
_IMAGE_HOST_RE = re.compile(r"^image0[1-9]\.seesaawiki\.jp$", re.IGNORECASE)
_SEESAA_UPLOAD_PREFIX = "/k/u/kirby_shinkaku/"
# 真格攻略 Wiki 的实机记录截图几乎全部托管在站外图床（記録集 页面实测
# 61 张 i.imgur.com 对 1 张 Seesaa 图床），只认 Seesaa 自带图床会让整张
# 记录表退化成空单元格。
_EXTERNAL_IMAGE_HOST_SUFFIXES = (
    "imgur.com",
    "gyazo.com",
    "gyazo.jp",
    "twimg.com",
    "discordapp.com",
    "discordapp.net",
    "nicoseiga.jp",
    "googleusercontent.com",
    "githubusercontent.com",
    "postimg.cc",
    "ibb.co",
    "imgbb.com",
    "imgbox.com",
    "prntscr.com",
    "steamusercontent.com",
    "cloudfront.net",
    "b-cdn.net",
)
# Wiki 皮肤装饰、访问统计像素和广告位同样是 <img>，必须显式排除。
_IMAGE_HOST_DENYLIST = (
    "static.seesaawiki.jp",
    "rainman.seesaawiki.jp",
    "img.seesaawiki.jp",
    "creativecarrer.com",
)
_UI_IMAGE_PATH_HINTS = (
    "/profile_icon/",
    "/img/icon/",
    "/emoji/",
    "/skin/",
    "/spacer",
    "/blank",
)
_ALBUM_PAGE_PATH_RE = re.compile(r"^/(?:a|gallery|t)/", re.IGNORECASE)
_NON_IMAGE_SUFFIXES = (".html", ".htm", ".php", ".asp", ".aspx")
# 表格里区分「行内小图标」与「需要放大展示的实机截图」的像素阈值。
_ICON_MAX_DIMENSION = 120
SHOT_MIN_DIMENSION = 200
_SECTION_CLASS_RE = re.compile(r"^wiki-section-body-(\d+)$")
_SOURCE_ACCENT_COLOR_RE = re.compile(
    r"(?:^|;)\s*color\s*:\s*(?:#(?:f00|ff0000)|red|"
    r"rgb\(\s*255\s*,\s*0\s*,\s*0\s*\))\s*(?:;|$)",
    re.IGNORECASE,
)
_SKIPPED_PAGE_TITLES = {"menubar1", "トップページ"}
_INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "big",
    "cite",
    "code",
    "del",
    "em",
    "font",
    "i",
    "ins",
    "kbd",
    "mark",
    "q",
    "s",
    "samp",
    "small",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "time",
    "tt",
    "u",
    "var",
}
_UNWANTED_SELECTOR = (
    "script, style, noscript, .part-edit, .history, .adsense-box, "
    ".page-social-link, .page-social-link-top, .page-social-link-bottom"
)
_MEDIA_HOST_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "nicovideo.jp",
    "nico.ms",
)


class KirbyShinkakuError(RuntimeError):
    """Raised when the public Seesaa Wiki cannot answer a read-only query."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _normalise_term(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", value)


def _catalog_index_from_query(value: str) -> int | None:
    """Read the public quick-reference number without treating it as a web title."""

    match = re.fullmatch(r"\s*(?:#|编号|序号)?\s*(\d+)\s*", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _load_page_name_entries(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return []
    output: list[dict[str, Any]] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = dict(raw_entry)
        if not all(
            str(entry.get(field) or "").strip()
            for field in ("title_ja", "title_zh", "title_en", "url")
        ):
            continue
        entry["primary_aliases"] = [
            str(value).strip()
            for value in entry.get("primary_aliases", [])
            if str(value).strip()
        ]
        entry["aliases"] = [
            str(value).strip()
            for value in entry.get("aliases", [])
            if str(value).strip()
        ]
        output.append(entry)
    return output


def _clean_text(element: Tag | None) -> str:
    if element is None:
        return ""
    fragment = BeautifulSoup(str(element), "html.parser")
    for unwanted in fragment.select(_UNWANTED_SELECTOR):
        unwanted.decompose()
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")
    text = html.unescape(fragment.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*(?:編集|履歴)\s*$", "", text).strip()


def _normalise_multiline_text(value: str) -> str:
    value = html.unescape(str(value or "")).replace("\xa0", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    blank_pending = False
    for raw_line in value.split("\n"):
        expanded = raw_line.replace("\t", "  ")
        list_match = re.match(r"^(?P<indent> +)(?P<body>(?:[-*•]|\d+[.、])\s+.+)$", expanded)
        if list_match:
            indent = " " * min(12, len(list_match.group("indent")))
            line = indent + re.sub(r"[\f\v ]+", " ", list_match.group("body")).strip()
        else:
            line = re.sub(r"[\t\f\v ]+", " ", expanded).strip()
        if not line:
            blank_pending = bool(lines)
            continue
        if blank_pending and lines and lines[-1] != "":
            lines.append("")
        lines.append(line)
        blank_pending = False
    return "\n".join(lines).strip()


def _inline_markup_text(element: Tag | None) -> str:
    """Keep meaningful inline emphasis as a small, renderer-owned dialect."""

    if element is None:
        return ""

    def walk(node: Tag | NavigableString) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        name = str(node.name or "").casefold()
        if name == "br":
            return "\n"
        value = "".join(walk(child) for child in node.children)
        if name in {"strong", "b"} and value.strip():
            return f"**{value.strip()}**"
        if name in {"em", "i"} and value.strip():
            return f"*{value.strip()}*"
        style = str(node.get("style") or "")
        colour = str(node.get("color") or "").strip().casefold()
        if value.strip() and (
            name == "mark"
            or bool(_SOURCE_ACCENT_COLOR_RE.search(style))
            or colour in {"#f00", "#ff0000", "red"}
        ):
            return f"=={value.strip()}=="
        return value

    value = html.unescape(walk(element)).replace("\xa0", " ")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _list_item_data(item: Tag) -> dict[str, Any]:
    text_parts: list[str] = []
    children: list[dict[str, Any]] = []
    for child in item.children:
        if isinstance(child, NavigableString):
            text_parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = str(child.name or "").casefold()
        if name in {"ul", "ol"}:
            for nested_item in child.find_all("li", recursive=False):
                children.append(_list_item_data(nested_item))
            continue
        text_parts.append(_inline_markup_text(child))
    text = _normalise_multiline_text("".join(text_parts))
    return {"text": text, "children": children}


def _list_data(element: Tag) -> dict[str, Any]:
    return {
        "kind": "list",
        "ordered": str(element.name or "").casefold() == "ol",
        "items": [
            _list_item_data(item)
            for item in element.find_all("li", recursive=False)
        ],
    }


def _list_lines(items: list[dict[str, Any]], depth: int = 0) -> list[str]:
    output: list[str] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if text:
            output.append(f"{'  ' * depth}- {text}")
        output.extend(_list_lines(list(item.get("children", []) or []), depth + 1))
    return output


def _host_matches(hostname: str, suffixes: tuple[str, ...]) -> bool:
    host = str(hostname or "").casefold()
    return any(
        host == suffix or host.endswith(f".{suffix}") for suffix in suffixes
    )


def _is_seesaa_upload_image(hostname: str, pathname: str) -> bool:
    return bool(
        _IMAGE_HOST_RE.fullmatch(str(hostname or "").casefold())
        and str(pathname or "").startswith(_SEESAA_UPLOAD_PREFIX)
    )


def _normalise_image_url(value: str) -> str:
    """把图床的页面型域名换成直链域名，避免下载时拿到 HTML。"""

    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").casefold()
    if hostname == "imgur.com" and not _ALBUM_PAGE_PATH_RE.match(parsed.path or ""):
        return parsed._replace(netloc="i.imgur.com").geturl()
    return raw


def _is_content_image_url(value: str) -> bool:
    """判断一个 <img> 是否是正文内容图，而不是皮肤装饰 / 统计像素 / 广告位。"""

    parsed = urlparse(str(value or ""))
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").casefold()
    pathname = parsed.path or "/"
    if not hostname or _host_matches(hostname, _IMAGE_HOST_DENYLIST):
        return False
    lowered = pathname.casefold()
    if any(hint in lowered for hint in _UI_IMAGE_PATH_HINTS):
        return False
    if _is_seesaa_upload_image(hostname, pathname):
        return True
    if not _host_matches(hostname, _EXTERNAL_IMAGE_HOST_SUFFIXES):
        return False
    # imgur 的 /a/、/gallery/ 是相册 HTML 页而不是原图直链。
    if _ALBUM_PAGE_PATH_RE.match(pathname):
        return False
    return not lowered.endswith(_NON_IMAGE_SUFFIXES)


def _leading_int(value: Any) -> int:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _image_kind_hint(image: Tag, source: str, *, sibling_count: int) -> str:
    """区分行内小图标（icon）与需要放大的实机截图（shot）。

    解析阶段只能看 HTML 属性，下载图片字节后 classify_image_kind() 会用真实
    像素尺寸复核并覆盖这里的猜测。
    """

    largest = max(
        _leading_int(image.get("width")), _leading_int(image.get("height"))
    )
    if largest:
        return "icon" if largest <= _ICON_MAX_DIMENSION else "shot"
    style = str(image.get("style") or "").casefold().replace(" ", "")
    if "max-width" in style or "width:100%" in style:
        # seesaawiki 只给按原尺寸插入的大图加这个内联样式，行内小图标不会有，
        # 所以它比「同格多图」更能说明这是一张截图。
        return "shot"
    if sibling_count > 1:
        # 同一格里并排多张没有尺寸线索的图，基本都是能力 / 角色图标组合。
        return "icon"
    if _host_matches(
        urlparse(source).hostname or "", _EXTERNAL_IMAGE_HOST_SUFFIXES
    ):
        return "shot"
    return "icon"


def _meaningful_image_urls(
    root: Tag, base_url: str, *, skip_tables: bool = False
) -> list[str]:
    urls: list[str] = []
    for image in root.select("img[src]"):
        if skip_tables and image.find_parent("table") is not None:
            continue
        source = _normalise_image_url(
            _safe_http_url(str(image.get("src") or ""), base_url)
        )
        if not source or not _is_content_image_url(source) or source in urls:
            continue
        urls.append(source)
    return urls


def _safe_http_url(value: str, base_url: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw.casefold().startswith("javascript:"):
        return ""
    resolved = urljoin(base_url, raw)
    return resolved if urlparse(resolved).scheme.casefold() in {"http", "https"} else ""


def _is_media_url(value: str) -> bool:
    host = (urlparse(str(value or "")).hostname or "").casefold()
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _MEDIA_HOST_SUFFIXES
    )


def _media_platform(value: str) -> str:
    host = (urlparse(str(value or "")).hostname or "").casefold()
    if host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com":
        return "YouTube"
    if host == "nico.ms" or host.endswith(".nicovideo.jp") or host == "nicovideo.jp":
        return "Niconico"
    return "媒体"


def _meaningful_media_links(root: Tag, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in root.select("iframe[src], video[src], source[src], a[href]"):
        source = _safe_http_url(
            str(element.get("src") or element.get("href") or ""), base_url
        )
        if not source or not _is_media_url(source) or source in seen:
            continue
        seen.add(source)
        label = _clean_text(element) if str(element.name or "") == "a" else ""
        if not label:
            label = str(element.get("title") or "").strip()
        links.append(
            {
                "url": source,
                "label": label,
                "platform": _media_platform(source),
            }
        )
    return links


def _meaningful_media_urls(root: Tag, base_url: str) -> list[str]:
    return [row["url"] for row in _meaningful_media_links(root, base_url)]


def _is_table_edit_cell(cell: Tag) -> bool:
    classes = {str(value) for value in cell.get("class", [])}
    if "table_edit_link" in classes:
        return True
    link = cell.select_one("a[href*='/e/edit']")
    return link is not None and _clean_text(cell) == ""


def classify_image_kind(width: int, height: int, fallback: str = "icon") -> str:
    """按真实像素尺寸判定图片类型，下载完成后用来复核解析期的猜测。"""

    largest = max(int(width or 0), int(height or 0))
    if largest <= 0:
        return fallback if fallback in {"icon", "shot"} else "icon"
    if largest >= SHOT_MIN_DIMENSION:
        return "shot"
    return "icon"


def _table_icon_data(cell: Tag, base_url: str) -> list[dict[str, str]]:
    candidates: list[tuple[Tag, str]] = []
    for image in cell.select("img[src]"):
        source = _normalise_image_url(
            _safe_http_url(str(image.get("src") or ""), base_url)
        )
        if source and _is_content_image_url(source):
            candidates.append((image, source))

    icons: list[dict[str, str]] = []
    for image, source in candidates:
        link_url = ""
        parent_link = image.find_parent("a", href=True)
        if (
            isinstance(parent_link, Tag)
            and parent_link.find_parent(["td", "th"]) is cell
        ):
            link_url = _safe_http_url(str(parent_link.get("href") or ""), base_url)
        icons.append(
            {
                "url": source,
                "link_url": link_url or source,
                "alt": str(image.get("alt") or "").strip(),
                "kind": _image_kind_hint(
                    image, source, sibling_count=len(candidates)
                ),
            }
        )
    return icons


def _table_links(cell: Tag, base_url: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in cell.select("a[href]"):
        url = _safe_http_url(str(anchor.get("href") or ""), base_url)
        label = _clean_text(anchor)
        if not url or not label or "/e/edit" in urlparse(url).path or url in seen:
            continue
        seen.add(url)
        links.append(
            {
                "url": url,
                "label": label,
                "is_media": _is_media_url(url),
                "platform": _media_platform(url) if _is_media_url(url) else "",
            }
        )
    return links


def _table_media_urls(tables: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for table in tables:
        for row in table.get("rows", []) or []:
            for cell in row if isinstance(row, list) else []:
                if not isinstance(cell, dict):
                    continue
                for link in cell.get("links", []) or []:
                    if isinstance(link, dict) and link.get("is_media"):
                        url = str(link.get("url") or "").strip()
                        if url:
                            urls.add(url)
    return urls


def _table_cell(cell: Tag, base_url: str) -> dict[str, Any]:
    """Extract table text, every Seesaa icon, and cell-owned links."""

    icons = _table_icon_data(cell, base_url)
    text = _clean_text(cell)
    icon_separator = ""
    if icons and re.fullmatch(r"[\s\u00a0×✕xX*+＋&＆/／・]+", text or ""):
        icon_separator = "×" if any(value in text for value in "×✕xX") else text.strip()
        text = ""
    elif len(icons) > 1:
        icon_separator = "×"
    return {
        "text": text,
        "icon_url": str(icons[0]["url"] if icons else ""),
        "icons": icons,
        "icon_separator": icon_separator,
        "links": _table_links(cell, base_url),
    }


def _table_data(table: Tag, base_url: str) -> dict[str, Any] | None:
    """Keep tables rectangular, including Seesaa rowspan/colspan cells."""

    grid: list[list[dict[str, Any] | None]] = []
    for row_index, row in enumerate(table.find_all("tr")):
        while len(grid) <= row_index:
            grid.append([])
        column = 0
        cells = [
            cell
            for cell in row.find_all(["th", "td"], recursive=False)
            if not _is_table_edit_cell(cell)
        ]
        for cell in cells:
            while column < len(grid[row_index]) and grid[row_index][column] is not None:
                column += 1
            try:
                row_span = max(1, int(str(cell.get("rowspan") or "1")))
            except ValueError:
                row_span = 1
            try:
                column_span = max(1, int(str(cell.get("colspan") or "1")))
            except ValueError:
                column_span = 1
            value = _table_cell(cell, base_url)
            for target_row in range(row_index, row_index + row_span):
                while len(grid) <= target_row:
                    grid.append([])
                while len(grid[target_row]) < column + column_span:
                    grid[target_row].append(None)
                for target_column in range(column, column + column_span):
                    if grid[target_row][target_column] is None:
                        grid[target_row][target_column] = deepcopy(value)
            column += column_span

    if not grid:
        return None

    column_count = max(len(row) for row in grid)
    padding = {
        "text": "",
        "icon_url": "",
        "icons": [],
        "icon_separator": "",
        "links": [],
    }
    matrix: list[list[dict[str, Any]]] = []
    for row in grid:
        row.extend([None] * (column_count - len(row)))
        matrix.append(
            [deepcopy(cell) if isinstance(cell, dict) else deepcopy(padding) for cell in row]
        )
    headers = [
        str(cell.get("text") or f"第 {index + 1} 列").strip()
        for index, cell in enumerate(matrix[0])
    ]
    return {
        "headers": headers,
        "rows": matrix[1:],
        "column_count": column_count,
    }


def table_cell_shot_urls(cell: dict[str, Any]) -> list[str]:
    """取出单元格里当作内容看的截图直链（能力小图标不算）。"""

    urls: list[str] = []
    for icon in cell.get("icons") or []:
        if not isinstance(icon, dict):
            continue
        if str(icon.get("kind") or "icon") != "shot":
            continue
        url = str(icon.get("url") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _table_lines(table_data: dict[str, Any]) -> list[str]:
    """Keep a readable plain-text fallback without losing any table rows."""

    header = [str(value or "—") for value in table_data.get("headers", [])]
    if not header:
        return []

    output = ["表格：" + " | ".join(header)]
    for row in table_data.get("rows", []):
        values: list[str] = []
        for cell in row:
            if not isinstance(cell, dict):
                values.append(str(cell or "—"))
                continue
            text = str(cell.get("text") or "").strip()
            has_icons = bool(cell.get("icons") or cell.get("icon_url"))
            # 記録集 这类页面把成绩直接写在截图里，单元格没有任何文字。
            # 丢掉图片链接等于丢掉全部数据，所以纯文本回退也要带上直链。
            shot_urls = table_cell_shot_urls(cell)
            if text:
                value = text
            elif shot_urls:
                value = "图片：" + "、".join(shot_urls)
            elif has_icons:
                value = "[图标]"
            else:
                value = "—"
            if text and shot_urls:
                value += "（图片：" + "、".join(shot_urls) + "）"
            media_links = [
                str(link.get("url") or "").strip()
                for link in cell.get("links", []) or []
                if isinstance(link, dict) and link.get("is_media")
            ]
            if media_links:
                value += "（视频：" + "、".join(media_links) + "）"
            values.append(value)
        output.append("- " + " | ".join(values))
    return output


def _toggle_parts(container: Tag) -> tuple[Tag | None, Tag | None]:
    title = container.find(class_="toggle-title", recursive=False)
    display = container.find(class_="toggle-display", recursive=False)
    return (
        title if isinstance(title, Tag) else None,
        display if isinstance(display, Tag) else None,
    )


def _section_content(body: Tag, base_url: str) -> dict[str, Any]:
    """Read a Seesaa section without dropping bare text or hidden toggle data."""

    parts: list[str] = []
    narrative_parts: list[str] = []
    tables: list[dict[str, Any]] = []
    toggles: list[dict[str, Any]] = []
    content_blocks: list[dict[str, Any]] = []
    inline_buffer: list[str] = []

    def append_block(target: list[str], value: str) -> None:
        value = _normalise_multiline_text(value)
        if value:
            target.append(value)

    def flush_inline() -> None:
        if not inline_buffer:
            return
        value = _normalise_multiline_text("".join(inline_buffer))
        inline_buffer.clear()
        if value:
            parts.append(value)
            narrative_parts.append(value)
            content_blocks.append({"kind": "prose", "text": value})

    for child in body.children:
        if isinstance(child, NavigableString):
            inline_buffer.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = str(child.name or "").casefold()
        classes = {str(value) for value in child.get("class", [])}
        if name in {"script", "style", "noscript"} or classes.intersection(
            {
                "part-edit",
                "history",
                "adsense-box",
                "page-social-link",
                "page-social-link-top",
                "page-social-link-bottom",
            }
        ):
            continue
        if name == "br":
            flush_inline()
            continue
        if name in _INLINE_TAGS:
            inline_buffer.append(_inline_markup_text(child))
            continue

        flush_inline()
        if name == "table":
            table_data = _table_data(child, base_url)
            if table_data is not None:
                tables.append(table_data)
                content_blocks.append({"kind": "table", "table": table_data})
                parts.extend(_table_lines(table_data))
            continue
        if name in {"ul", "ol"}:
            list_block = _list_data(child)
            list_lines = _list_lines(list_block["items"])
            if list_lines:
                content_blocks.append(list_block)
                parts.extend(list_lines)
                narrative_parts.extend(list_lines)
            continue
        if name == "dl":
            pending_label = ""
            for item in child.find_all(["dt", "dd"], recursive=False):
                value = _clean_text(item)
                if not value:
                    continue
                if item.name == "dt":
                    pending_label = value
                    continue
                line = f"{pending_label}：{value}" if pending_label else value
                parts.append(line)
                narrative_parts.append(line)
                content_blocks.append({"kind": "prose", "text": line})
                pending_label = ""
            if pending_label:
                parts.append(pending_label)
                narrative_parts.append(pending_label)
                content_blocks.append({"kind": "prose", "text": pending_label})
            continue

        toggle_title_element, toggle_display = _toggle_parts(child)
        if toggle_display is not None:
            toggle_title = _clean_text(toggle_title_element) or "折叠资料"
            parsed = _section_content(toggle_display, base_url)
            toggle_tables: list[dict[str, Any]] = []
            for index, raw_table in enumerate(parsed["tables"], start=1):
                table = dict(raw_table)
                if not str(table.get("title") or "").strip():
                    table["title"] = (
                        toggle_title
                        if len(parsed["tables"]) == 1
                        else f"{toggle_title}（表 {index}）"
                    )
                toggle_tables.append(table)
            toggle = {
                "title": toggle_title,
                "text": parsed["text"],
                "text_without_tables": parsed["text_without_tables"],
                "tables": toggle_tables,
                "content_blocks": parsed["content_blocks"],
                "images": parsed["images"],
                "media_links": parsed["media_links"],
                "media_urls": parsed["media_urls"],
            }
            toggles.append(toggle)
            heading = f"【{toggle_title}】"
            append_block(parts, heading)
            append_block(narrative_parts, heading)
            content_blocks.append(
                {"kind": "subheading", "text": toggle_title, "level": 1}
            )
            content_blocks.extend(parsed["content_blocks"])
            append_block(parts, parsed["text"])
            append_block(narrative_parts, parsed["text_without_tables"])
            tables.extend(toggle_tables)
            continue

        if name in {"img", "iframe", "video", "source"}:
            continue
        if name == "pre":
            value = _normalise_multiline_text(child.get_text("\n", strip=False))
            append_block(parts, value)
            append_block(narrative_parts, value)
            if value:
                content_blocks.append({"kind": "pre", "text": value})
            continue
        if child.select_one("div[class*='wiki-section-body-']"):
            continue

        nested = _section_content(child, base_url)
        append_block(parts, nested["text"])
        append_block(narrative_parts, nested["text_without_tables"])
        tables.extend(nested["tables"])
        toggles.extend(nested["toggles"])
        content_blocks.extend(nested["content_blocks"])

    flush_inline()
    media_links = _meaningful_media_links(body, base_url)
    table_media_urls = _table_media_urls(tables)
    media_links = [
        row for row in media_links if str(row.get("url") or "") not in table_media_urls
    ]
    return {
        "text": "\n".join(parts).strip(),
        "text_without_tables": "\n".join(narrative_parts).strip(),
        "tables": tables,
        "toggles": toggles,
        "content_blocks": content_blocks,
        "images": _meaningful_image_urls(body, base_url),
        "media_links": media_links,
        "media_urls": [row["url"] for row in media_links],
    }


def _section_level(body: Tag) -> str:
    for class_name in body.get("class", []):
        match = _SECTION_CLASS_RE.match(str(class_name))
        if match:
            return match.group(1)
    return "1"


def _page_sections(root: Tag, base_url: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    heading_stack: dict[int, str] = {}
    for order, body in enumerate(
        root.select("div[class*='wiki-section-body-']"), start=1
    ):
        title_element = body.find_previous(["h3", "h4", "h5", "h6"])
        title = _clean_text(title_element)
        if not title:
            continue
        content = _section_content(body, base_url)
        try:
            level = max(1, int(_section_level(body)))
        except ValueError:
            level = 1
        context = " › ".join(
            heading_stack[index]
            for index in sorted(heading_stack)
            if index < level and heading_stack[index]
        )
        for index in [value for value in heading_stack if value >= level]:
            heading_stack.pop(index, None)
        heading_stack[level] = title
        group_only = not any(
            (
                content["text"],
                content["tables"],
                content["toggles"],
                content["images"],
                content["media_links"],
                content["media_urls"],
            )
        )
        sections.append(
            {
                "title": title,
                "text": content["text"],
                "text_without_tables": content["text_without_tables"],
                "tables": content["tables"],
                "toggles": content["toggles"],
                "content_blocks": content["content_blocks"],
                "images": content["images"],
                "media_links": content["media_links"],
                "media_urls": content["media_urls"],
                "level": str(level),
                "context": context,
                "group_only": group_only,
                "order": order,
            }
        )
    return sections


def _page_lead(root: Tag, base_url: str) -> dict[str, Any]:
    """Read article content placed before the first named Wiki section."""

    user_area = root.select_one("#page-body-inner .user-area") or root.select_one(
        ".user-area"
    )
    if not isinstance(user_area, Tag):
        return {}

    fragments: list[str] = []
    for child in user_area.children:
        if isinstance(child, Tag):
            name = str(child.name or "").casefold()
            classes = {str(value) for value in child.get("class", [])}
            if (
                name in {"h3", "h4", "h5", "h6"}
                or any(_SECTION_CLASS_RE.match(value) for value in classes)
                or child.select_one("div[class*='wiki-section-body-']") is not None
            ):
                break
        fragments.append(str(child))

    if not "".join(fragments).strip():
        return {}
    fragment = BeautifulSoup("<div>" + "".join(fragments) + "</div>", "html.parser")
    body = fragment.div
    if not isinstance(body, Tag):
        return {}
    content = _section_content(body, base_url)
    if not any(
        (
            content["text"],
            content["images"],
            content["media_links"],
            content["media_urls"],
        )
    ):
        return {}
    return content


def _page_image_url(root: Tag, base_url: str) -> str:
    # 表格里的图片是记录截图，随手拿第一张当页头图会很突兀。
    images = _meaningful_image_urls(root, base_url, skip_tables=True)
    return images[0] if images else ""


def _page_title(root: Tag, fallback: str) -> str:
    heading = root.select_one("#page-header h2") or root.select_one("h2")
    title = _clean_text(heading)
    return title or fallback


def _term_variants(value: str) -> set[str]:
    value = str(value or "").strip()
    if not value:
        return set()
    values = {value}
    for part in re.split(r"\s*/\s*", value):
        part = re.sub(r"\s*\([^)]*\)\s*", " ", part).strip()
        if part:
            values.add(part)
    values.add(re.sub(r"\s*\([^)]*\)\s*", " ", value).strip())
    return {item for item in values if item}


class KirbyShinkakuClient:
    """Read-only HTML client for the Seesaa 真格斗攻略 Wiki."""

    def __init__(
        self,
        site_url: str = DEFAULT_SHINKAKU_SITE_URL,
        timeout_seconds: float = 15.0,
        cache_ttl_seconds: int = 3600,
        proxy_url: str = "",
        proxy_token: str = "",
        page_names_path: str | Path | None = None,
    ) -> None:
        self.site_url = site_url.strip().rstrip("/") or DEFAULT_SHINKAKU_SITE_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.proxy_url = proxy_url.strip().rstrip("/")
        self.proxy_token = proxy_token.strip()
        names_path = (
            Path(page_names_path)
            if page_names_path is not None
            else DEFAULT_SHINKAKU_PAGE_NAMES_PATH
        )
        self._page_name_entries = _load_page_name_entries(names_path)
        self._page_names_by_title = {
            _normalise_term(str(entry["title_ja"])): entry
            for entry in self._page_name_entries
        }
        self._page_names_by_url = {
            str(entry["url"]).strip(): entry for entry in self._page_name_entries
        }
        self._proxy_preferred_until = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._request_limit = asyncio.Semaphore(2)

    async def close(self) -> None:
        self.clear_cache()

    def clear_cache(self) -> int:
        count = len(self._cache)
        self._cache.clear()
        return count

    @property
    def page_name_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._page_name_entries]

    def get_page_name_by_index(self, index: int) -> dict[str, Any] | None:
        """Return one page from the public catalog number used by the reference image."""

        try:
            target = int(index)
        except (TypeError, ValueError):
            return None
        if target <= 0:
            return None
        for entry in self._page_name_entries:
            try:
                entry_index = int(entry.get("catalog_index") or 0)
            except (TypeError, ValueError):
                continue
            if entry_index == target:
                return dict(entry)
        return None

    @staticmethod
    def _page_name_match_score(entry: dict[str, Any], query: str) -> int:
        target = _normalise_term(query)
        if not target:
            return 0
        primary = {
            _normalise_term(value)
            for value in entry.get("primary_aliases", [])
            if _normalise_term(value)
        }
        aliases = {
            _normalise_term(value)
            for value in entry.get("aliases", [])
            if _normalise_term(value)
        }
        if target in primary:
            return 400
        if target in aliases:
            return 300
        if any(target in value for value in primary):
            return 180
        if any(target in value for value in aliases):
            return 120
        return 0

    def lookup_page_names(
        self,
        query: str,
        limit: int = 20,
        *,
        exact_only: bool = False,
    ) -> list[dict[str, Any]]:
        catalog_index = _catalog_index_from_query(query)
        if catalog_index is not None:
            entry = self.get_page_name_by_index(catalog_index)
            if entry is None:
                return []
            entry["score"] = 500
            return [entry]
        target = _normalise_term(query)
        if not target:
            return []
        matches: list[dict[str, Any]] = []
        for entry in self._page_name_entries:
            score = self._page_name_match_score(entry, query)
            if score <= 0 or (exact_only and score < 300):
                continue
            row = dict(entry)
            row["score"] = score
            matches.append(row)
        if exact_only and any(int(row["score"]) >= 400 for row in matches):
            matches = [row for row in matches if int(row["score"]) >= 400]
        matches.sort(
            key=lambda row: (
                -int(row.get("score", 0)),
                int(row.get("catalog_index") or row.get("source_index") or 0),
                str(row.get("title_ja") or ""),
            )
        )
        return matches[: max(1, int(limit))]

    def _page_name_entry(
        self, *, title: str = "", url: str = ""
    ) -> dict[str, Any] | None:
        if url:
            entry = self._page_names_by_url.get(url.strip())
            if entry is not None:
                return dict(entry)
        if title:
            entry = self._page_names_by_title.get(_normalise_term(title))
            if entry is not None:
                return dict(entry)
        return None

    @staticmethod
    def _page_name_candidate(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "catalog_index": int(entry.get("catalog_index") or 0),
            "title": str(entry.get("title_ja") or ""),
            "title_ja": str(entry.get("title_ja") or ""),
            "title_zh": str(entry.get("title_zh") or ""),
            "title_en": str(entry.get("title_en") or ""),
            "game_zh": str(entry.get("game_zh") or ""),
            "section_zh": str(entry.get("section_zh") or ""),
            "url": str(entry.get("url") or ""),
            "score": int(entry.get("score") or 0),
        }

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

    def _proxy_configured(self) -> bool:
        return bool(self.proxy_url and self.proxy_token)

    @staticmethod
    def _is_page_path(pathname: str) -> bool:
        return pathname.startswith("/kirby_shinkaku/d/")

    @staticmethod
    def _is_image_path(hostname: str, pathname: str) -> bool:
        return _is_content_image_url(f"https://{hostname}{pathname}")

    def _proxy_url_for(self, target_url: str, *, image: bool) -> str:
        if not self._proxy_configured():
            return ""
        parsed = urlparse(target_url)
        hostname = (parsed.hostname or "").casefold()
        pathname = parsed.path or "/"
        if image:
            if not self._is_image_path(hostname, pathname):
                return ""
        elif hostname != "seesaawiki.jp" or not (
            self._is_page_path(pathname) or pathname == "/kirby_shinkaku/search"
        ):
            return ""

        query: list[tuple[str, str]] = [("site", "shinkaku"), ("path", pathname)]
        if image:
            query.extend((("asset", "image"), ("image_host", hostname)))
        if parsed.query:
            # Seesaa search parameters are EUC-JP percent bytes. Decoding them
            # as normal UTF-8 query values and encoding them again corrupts the
            # lookup text before it reaches the Worker. Pass the original query
            # string as one escaped control value so the Worker can restore it
            # byte-for-byte for the upstream request.
            query.append(("raw_query", parsed.query))
        separator = "&" if "?" in self.proxy_url else "?"
        return f"{self.proxy_url}{separator}{urlencode(query)}"

    @staticmethod
    def _should_fallback_to_proxy(error: Exception) -> bool:
        if isinstance(error, HTTPError):
            return error.code in _PROXY_FALLBACK_HTTP_CODES
        return isinstance(error, (URLError, TimeoutError))

    def _request_headers(
        self, *, image: bool, target_url: str = ""
    ) -> dict[str, str]:
        referer = self.site_url + "/"
        if image and target_url:
            parsed = urlparse(target_url)
            hostname = parsed.hostname or ""
            if hostname and not hostname.casefold().endswith("seesaawiki.jp"):
                # imgur 一类站外图床按 Referer 做防盗链，带 Wiki 的 Referer
                # 会被换成占位图，用图床自身 origin 才能拿到原图。
                referer = f"{parsed.scheme or 'https'}://{hostname}/"
        return {
            "Accept": "image/*" if image else "text/html,application/xhtml+xml",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": referer,
            "User-Agent": _BROWSER_USER_AGENT,
        }

    def _read_url_sync(self, url: str, headers: dict[str, str]) -> bytes:
        request = Request(url, headers=headers)
        for attempt in range(2):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code not in _RETRYABLE_HTTP_CODES or attempt == 1:
                    raise
                time.sleep(self._retry_delay(exc, attempt))
            except (URLError, TimeoutError):
                if attempt == 1:
                    raise
                time.sleep(0.4 * (2**attempt))
        raise RuntimeError("真格攻略 Wiki 请求重试意外结束")

    def _read_target_with_proxy_fallback_sync(
        self, target_url: str, *, image: bool
    ) -> bytes:
        headers = self._request_headers(image=image, target_url=target_url)
        proxy_url = self._proxy_url_for(target_url, image=image)
        proxy_headers = dict(headers)
        if proxy_url:
            proxy_headers["Authorization"] = f"Bearer {self.proxy_token}"

        proxy_failed = False
        if proxy_url and time.monotonic() < self._proxy_preferred_until:
            try:
                return self._read_url_sync(proxy_url, proxy_headers)
            except HTTPError as proxy_error:
                self._proxy_preferred_until = 0.0
                # A preferred Worker is an equivalent read-only upstream. Its
                # 404 is definitive for this page title, so keep it instead of
                # retrying a cloud-server direct request that may be blocked
                # with 403 and hide the normal "not found" result.
                if proxy_error.code == 404:
                    raise
                proxy_failed = True
            except (URLError, TimeoutError):
                self._proxy_preferred_until = 0.0
                proxy_failed = True

        try:
            return self._read_url_sync(target_url, headers)
        except (HTTPError, URLError, TimeoutError) as direct_error:
            if (
                not proxy_url
                or proxy_failed
                or not self._should_fallback_to_proxy(direct_error)
            ):
                raise
            try:
                raw = self._read_url_sync(proxy_url, proxy_headers)
            except (HTTPError, URLError, TimeoutError) as proxy_error:
                raise proxy_error from direct_error
            self._proxy_preferred_until = time.monotonic() + _PROXY_PREFERENCE_SECONDS
            return raw

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        for encoding in ("euc_jp", "euc_jis_2004", "cp932", "utf-8"):
            try:
                return raw.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("euc_jp", errors="replace")

    @staticmethod
    def _title_from_url(value: str) -> str:
        parsed = urlparse(value)
        marker = "/kirby_shinkaku/d/"
        if (parsed.hostname or "").casefold() != "seesaawiki.jp" or not parsed.path.startswith(marker):
            return value.strip()
        encoded = parsed.path[len(marker) :]
        return unquote(encoded, encoding="euc_jp", errors="replace").strip()

    def _page_url(self, title: str) -> str:
        encoded = quote(
            title.strip(), safe="()-.~", encoding="euc_jp", errors="strict"
        )
        return f"{self.site_url}/d/{encoded}"

    def _search_url(self, query: str) -> str:
        encoded = quote(query.strip(), safe="", encoding="euc_jp", errors="strict")
        # Seesaa's default search includes every page that merely mentions the
        # term in its body. Page-name mode gives callers usable Boss/ability
        # candidates instead of unrelated collection and movie pages.
        return (
            f"{self.site_url}/search?keywords={encoded}&search_target=page_name"
        )

    def _parse_page(self, raw: bytes, requested_title: str, source_url: str) -> dict[str, Any] | None:
        soup = BeautifulSoup(self._decode_html(raw), "html.parser")
        root = soup.select_one("#main")
        if root is None:
            return None
        title = _page_title(root, requested_title)
        if not title or "ページが見つかりません" in title:
            return None
        lead = _page_lead(root, source_url)
        sections = _page_sections(root, source_url)
        section_titles = [
            str(row["title"])
            for row in sections
            if str(row.get("level") or "1") == "1"
        ]
        summary = str(lead.get("text_without_tables") or lead.get("text") or "").strip()
        if not summary:
            summary = (
                f"真格攻略 Wiki 的「{title}」资料页"
                + (
                    f"，包含{ '、'.join(section_titles) }等内容。"
                    if section_titles
                    else "。"
                )
            )
        images = _meaningful_image_urls(root, source_url)
        all_tables = list(lead.get("tables", []) or [])
        for section in sections:
            all_tables.extend(section.get("tables", []) or [])
        associated_media_urls = _table_media_urls(all_tables)
        media_links = [
            row
            for row in _meaningful_media_links(root, source_url)
            if str(row.get("url") or "") not in associated_media_urls
        ]
        result = {
            "title": title,
            "summary": summary,
            "lead": lead,
            "url": source_url,
            "image_url": _page_image_url(root, source_url),
            "images": images,
            "media_links": media_links,
            "media_urls": [row["url"] for row in media_links],
            "sections": sections,
            "section_index": [
                {
                    "index": str(index),
                    "title": str(row["title"]),
                    "level": str(row.get("level", "1")),
                }
                for index, row in enumerate(sections, start=1)
            ],
        }
        page_names = self._page_name_entry(title=title, url=source_url)
        if page_names is not None:
            result["title_ja"] = page_names["title_ja"]
            result["title_zh"] = page_names["title_zh"]
            result["title_en"] = page_names["title_en"]
            result["page_name_entry"] = page_names
        return result

    def _get_page_sync(self, title: str) -> dict[str, Any] | None:
        try:
            source_url = self._page_url(title)
        except UnicodeEncodeError:
            return None
        try:
            raw = self._read_target_with_proxy_fallback_sync(source_url, image=False)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise KirbyShinkakuError(
                f"真格攻略 Wiki 返回 HTTP {exc.code}，请稍后再试", str(exc.code)
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise KirbyShinkakuError("无法连接真格攻略 Wiki，请稍后再试") from exc
        return self._parse_page(raw, title, source_url)

    async def get_page(self, title: str) -> dict[str, Any] | None:
        title = self._title_from_url(title.strip())
        if not title:
            return None
        key = f"page:{title.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        async with self._request_limit:
            page = await asyncio.to_thread(self._get_page_sync, title)
        self._cache_set(key, page)
        return page

    def _search_pages_sync(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            source_url = self._search_url(query)
        except UnicodeEncodeError:
            return []
        try:
            raw = self._read_target_with_proxy_fallback_sync(source_url, image=False)
        except HTTPError as exc:
            raise KirbyShinkakuError(
                f"真格攻略 Wiki 搜索返回 HTTP {exc.code}，请稍后再试", str(exc.code)
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise KirbyShinkakuError("无法连接真格攻略 Wiki，请稍后再试") from exc

        soup = BeautifulSoup(self._decode_html(raw), "html.parser")
        root = soup.select_one("#main.page-result") or soup.select_one("#main")
        if root is None:
            return []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        result_links = root.select(".result-box h3.keyword a[href]")
        if not result_links:
            result_links = root.select(".result-box .keyword a[href]")
        # Keep a modest fallback for a future Seesaa markup change, but do not
        # accidentally collect page navigation when the standard result cards
        # are present.
        if not result_links:
            result_links = root.select("a[href]")
        for link in result_links:
            url = urljoin(source_url, str(link.get("href") or ""))
            parsed = urlparse(url)
            if (
                (parsed.hostname or "").casefold() != "seesaawiki.jp"
                or not self._is_page_path(parsed.path)
            ):
                continue
            title = _clean_text(link) or self._title_from_url(url)
            if not title or title.startswith("http"):
                continue
            canonical_url = parsed._replace(query="", fragment="").geturl()
            if canonical_url in seen:
                continue
            seen.add(canonical_url)
            result.append({"title": title, "url": canonical_url, "snippet": ""})
            if len(result) >= max(1, limit):
                break
        return result

    async def search_pages(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        key = f"search:{limit}:{query.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        async with self._request_limit:
            pages = await asyncio.to_thread(self._search_pages_sync, query, limit)
        self._cache_set(key, pages)
        return pages

    def _load_english_terms_sync(self) -> list[dict[str, str]]:
        source_url = self._page_url(SHINKAKU_ENGLISH_CORNER_TITLE)
        try:
            raw = self._read_target_with_proxy_fallback_sync(source_url, image=False)
        except HTTPError as exc:
            raise KirbyShinkakuError(
                f"真格攻略 Wiki 英文对照表返回 HTTP {exc.code}，请稍后再试",
                str(exc.code),
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise KirbyShinkakuError("无法读取真格攻略 Wiki 英文对照表，请稍后再试") from exc

        soup = BeautifulSoup(self._decode_html(raw), "html.parser")
        root = soup.select_one("#main")
        if root is None:
            return []
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for table in root.select("table"):
            table_rows = table.find_all("tr")
            if not table_rows:
                continue
            headers = [
                _normalise_term(_clean_text(cell))
                for cell in table_rows[0].find_all(["th", "td"], recursive=False)
            ]
            japanese_index = next(
                (index for index, value in enumerate(headers) if "日本名" in value),
                -1,
            )
            english_index = next(
                (index for index, value in enumerate(headers) if "英名" in value),
                -1,
            )
            if japanese_index < 0 or english_index < 0:
                continue
            for table_row in table_rows[1:]:
                cells = table_row.find_all(["th", "td"], recursive=False)
                values = [_clean_text(cell) for cell in cells]
                if japanese_index >= len(values) or english_index >= len(values):
                    continue
                japanese = values[japanese_index].strip()
                english = values[english_index].strip()
                if not japanese or not english:
                    continue
                key = (japanese, english)
                if key not in seen:
                    seen.add(key)
                    rows.append({"japanese": japanese, "english": english})
        return rows

    async def english_terms(self) -> list[dict[str, str]]:
        key = "english_terms"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        async with self._request_limit:
            rows = await asyncio.to_thread(self._load_english_terms_sync)
        self._cache_set(key, rows)
        return rows

    async def lookup_terms(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        target = _normalise_term(query)
        if not target:
            return []
        exact: list[dict[str, str]] = []
        partial: list[dict[str, str]] = []
        for row in await self.english_terms():
            variants = {
                _normalise_term(value)
                for value in (*_term_variants(row["japanese"]), *_term_variants(row["english"]))
            }
            if target in variants:
                exact.append(dict(row))
            elif any(target in value for value in variants if value):
                partial.append(dict(row))
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in [*exact, *partial]:
            key = (row["japanese"], row["english"])
            if key not in seen:
                seen.add(key)
                result.append(row)
            if len(result) >= max(1, limit):
                break
        return result

    @staticmethod
    def _term_row_matches(row: dict[str, str], query: str) -> bool:
        query_variants = {
            _normalise_term(value) for value in _term_variants(query) if value
        }
        row_variants = {
            _normalise_term(value)
            for value in (
                *_term_variants(row.get("japanese", "")),
                *_term_variants(row.get("english", "")),
            )
            if value
        }
        return bool(query_variants & row_variants)

    @staticmethod
    def _english_base_and_suffix(value: str) -> tuple[str, str]:
        """Turn ``Meta Knight (WiiDX)`` into a translatable base and suffix."""

        value = value.strip()
        parentheses = re.match(r"^(.*?)(\s*\([^)]*\))$", value)
        trailing_parentheses = parentheses.group(2).replace(" ", "") if parentheses else ""
        base = parentheses.group(1).strip() if parentheses else value
        ex = re.match(r"^(.*?)(?:\s+)(EX)$", base, re.IGNORECASE)
        if ex:
            return ex.group(1).strip(), f"{ex.group(2).upper()}{trailing_parentheses}"
        return base, trailing_parentheses

    @staticmethod
    def _candidate_score(
        title: str, aliases: set[str], query: str
    ) -> int:
        target = _normalise_term(title)
        query_target = _normalise_term(query)
        score = 0
        if target in aliases:
            score += 100
        elif any(alias and alias in target for alias in aliases):
            score += 50
        if query_target and query_target == target:
            score += 35
        elif query_target and query_target in target:
            score += 12
        if "menubar" in target:
            score -= 100
        return score

    async def resolve(
        self, query: str, aliases: list[str] | None = None
    ) -> dict[str, Any]:
        query = self._title_from_url(query.strip())
        if not query:
            return {"error": "empty_query"}

        catalog_index = _catalog_index_from_query(query)
        if catalog_index is not None:
            entry = self.get_page_name_by_index(catalog_index)
            if entry is None:
                return {"error": "not_found", "query": query}
            page = await self.get_page(str(entry.get("title_ja") or ""))
            if page is not None:
                return {"kind": "page", "page": page}
            return {"error": "not_found", "query": query}

        supplied_aliases = [query, *(aliases or [])]
        ambiguous_name_matches: dict[str, dict[str, Any]] = {}
        for candidate in supplied_aliases:
            exact_name_matches = self.lookup_page_names(
                candidate, limit=100, exact_only=True
            )
            if len(exact_name_matches) == 1:
                page = await self.get_page(exact_name_matches[0]["title_ja"])
                if page is not None:
                    return {"kind": "page", "page": page}
            for entry in exact_name_matches:
                url = str(entry.get("url") or "")
                if url:
                    ambiguous_name_matches.setdefault(url, entry)
        if len(ambiguous_name_matches) > 1:
            candidates = [
                self._page_name_candidate(entry)
                for entry in ambiguous_name_matches.values()
            ]
            candidates.sort(
                key=lambda row: (
                    -int(row.get("score", 0)),
                    str(row.get("title_ja") or ""),
                )
            )
            return {"kind": "candidates", "candidates": candidates[:20]}

        japanese_aliases: list[str] = []
        try:
            for candidate in supplied_aliases:
                term_rows = await self.lookup_terms(candidate, limit=20)
                exact_rows = [
                    row for row in term_rows if self._term_row_matches(row, candidate)
                ]
                japanese_aliases.extend(row["japanese"] for row in exact_rows)

                base, suffix = self._english_base_and_suffix(candidate)
                if base.casefold() == candidate.casefold():
                    continue
                for row in await self.lookup_terms(base, limit=20):
                    if not self._term_row_matches(row, base):
                        continue
                    japanese_aliases.append(row["japanese"])
                    if suffix:
                        japanese_aliases.append(f"{row['japanese']}{suffix}")
        except KirbyShinkakuError:
            # An unavailable optional English table must not prevent a direct
            # Japanese page title from working.
            japanese_aliases = []
        direct_titles = [*supplied_aliases, *japanese_aliases]
        seen_direct: set[str] = set()
        for title in direct_titles:
            title = title.strip()
            key = title.casefold()
            if not title or key in seen_direct:
                continue
            seen_direct.add(key)
            page = await self.get_page(title)
            if page is not None:
                return {"kind": "page", "page": page}

        normalised_aliases = {
            _normalise_term(value)
            for value in (*supplied_aliases, *japanese_aliases)
            if _normalise_term(value)
        }
        candidates: dict[str, dict[str, Any]] = {}
        for search_term in [*japanese_aliases, *supplied_aliases]:
            try:
                pages = await self.search_pages(search_term, limit=20)
            except KirbyShinkakuError:
                continue
            for page in pages:
                page_url = str(page.get("url") or "")
                if page_url:
                    page_names = self._page_name_entry(
                        title=str(page.get("title") or ""), url=page_url
                    )
                    if page_names is not None:
                        page["title_ja"] = page_names["title_ja"]
                        page["title_zh"] = page_names["title_zh"]
                        page["title_en"] = page_names["title_en"]
                        page["catalog_index"] = page_names.get("catalog_index", 0)
                    candidates.setdefault(page_url, page)
        ranked = sorted(
            candidates.values(),
            key=lambda page: self._candidate_score(
                str(page.get("title") or ""), normalised_aliases, query
            ),
            reverse=True,
        )
        ranked = [
            page
            for page in ranked
            if str(page.get("title") or "").casefold() not in _SKIPPED_PAGE_TITLES
        ]
        if not ranked:
            return {"error": "not_found", "query": query}
        for page in ranked:
            page["score"] = self._candidate_score(
                str(page.get("title") or ""), normalised_aliases, query
            )
        if (
            len(ranked) == 1
            or ranked[0]["score"] >= 100
            or ranked[0]["score"] - ranked[1]["score"] >= 20
        ):
            page = await self.get_page(str(ranked[0]["url"]))
            if page is not None:
                return {"kind": "page", "page": page}
        return {"kind": "candidates", "candidates": ranked[:8]}

    def get_section_titles(self, page: dict[str, Any]) -> list[dict[str, str]]:
        return [dict(row) for row in page.get("section_index", [])]

    def get_page_details(
        self, page: dict[str, Any], section: str = ""
    ) -> dict[str, list[dict[str, str]]]:
        sections = [dict(row) for row in page.get("sections", [])]
        if not section.strip():
            return {"sections": sections}
        target = _normalise_term(section)
        exact = [
            row for row in sections if _normalise_term(str(row.get("title") or "")) == target
        ]
        partial = [
            row
            for row in sections
            if target and target in _normalise_term(str(row.get("title") or ""))
        ]
        return {"sections": exact or partial}

    async def get_image_bytes(self, image_url: str) -> bytes | None:
        if not image_url:
            return None
        parsed = urlparse(image_url)
        if not self._is_image_path(
            (parsed.hostname or "").casefold(), parsed.path or "/"
        ):
            return None
        try:
            async with self._request_limit:
                return await asyncio.to_thread(
                    self._read_target_with_proxy_fallback_sync, image_url, image=True
                )
        except (HTTPError, URLError, TimeoutError, KirbyShinkakuError):
            return None
