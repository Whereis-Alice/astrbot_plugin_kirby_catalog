from __future__ import annotations

import asyncio
import html
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_API_URL = "https://wikirby.com/w/api.php"

_NAMES_HEADING = re.compile(
    r"^==+\s*Names in other languages\s*==+\s*$", re.IGNORECASE | re.MULTILINE
)
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
        max_summary_chars: int = 1800,
    ) -> None:
        self.api_url = api_url.strip() or DEFAULT_API_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_summary_chars = max(300, int(max_summary_chars))
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
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, value)

    def _request_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {"format": "json", "formatversion": "2", **params}
        separator = "&" if "?" in self.api_url else "?"
        url = f"{self.api_url}{separator}{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "astrbot-plugin-kirby-catalog/2.2",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raise WikirbyError(f"WiKirby API 返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise WikirbyError("无法连接 WiKirby，请稍后再试") from exc
        except TimeoutError as exc:
            raise WikirbyError("WiKirby 请求超时，请稍后再试") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikirbyError("WiKirby 返回了无法识别的数据") from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("info", "WiKirby API 请求失败") if isinstance(error, dict) else str(error)
            raise WikirbyError(message)
        return data

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
        data = await self._request(
            {
                "action": "query",
                "titles": title,
                "redirects": 1,
                "prop": "info|extracts|pageimages",
                "inprop": "url",
                "exintro": 1,
                "explaintext": 1,
                "exchars": self.max_summary_chars,
                "piprop": "thumbnail",
                "pithumbsize": 600,
            }
        )
        pages = [self._normalise_page(page) for page in self._page_values(data)]
        result = next((page for page in pages if page), None)
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
        rows = search_data.get("query", {}).get("search", [])
        if not isinstance(rows, list):
            return []
        pageids = [str(row.get("pageid")) for row in rows if row.get("pageid")]
        if not pageids:
            self._cache_set(key, [])
            return []

        details = await self._request(
            {
                "action": "query",
                "pageids": "|".join(pageids),
                "redirects": 1,
                "prop": "info|extracts|pageimages",
                "inprop": "url",
                "exintro": 1,
                "explaintext": 1,
                "exchars": self.max_summary_chars,
                "piprop": "thumbnail",
                "pithumbsize": 600,
            }
        )
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
        if len(ranked) == 1 or ranked[0]["score"] >= 80 or ranked[0]["score"] - ranked[1]["score"] >= 12:
            return {"kind": "page", "page": ranked[0]}
        return {"kind": "candidates", "candidates": ranked[:5]}

    async def get_wikitext(self, title: str) -> str:
        key = f"wikitext:{title.casefold()}"
        cached = self._cache_get(key)
        if cached is not None:
            return str(cached)
        data = await self._request(
            {
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
            }
        )
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
        wikitext = await self.get_wikitext(str(page.get("title", "")))
        result = parse_language_names(wikitext)
        self._cache_set(key, result)
        return result
