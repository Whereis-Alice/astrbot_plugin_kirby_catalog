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
    "astrbot-plugin-kirby-catalog/2.9.1 "
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
        output.append({"title": title, "text": text})
    return output


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
    ) -> dict[str, list[dict[str, str]]]:
        sections = [dict(row) for row in page.get("sections", [])]
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
            if matched:
                selected: list[dict[str, str]] = []
                remaining = self.max_detail_chars
                for value in matched:
                    if remaining <= 0:
                        break
                    row = dict(value)
                    row["text"] = _trim_text(row["text"], remaining)
                    selected.append(row)
                    remaining -= len(row["title"]) + len(row["text"])
                return {"infobox": [], "sections": selected, "categories": []}
            return {"infobox": [], "sections": [], "categories": []}

        selected: list[dict[str, str]] = []
        remaining = self.max_detail_chars
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
