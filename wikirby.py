from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_API_URL = "https://wikirby.com/w/api.php"
FALLBACK_API_URL = "https://www.wikirby.com/w/api.php"
DEFAULT_REST_URL = "https://wikirby.com/w/rest.php"
FALLBACK_REST_URL = "https://www.wikirby.com/w/rest.php"
USER_AGENT = (
    "astrbot-plugin-kirby-catalog/2.4.0 "
    "(+https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)"
)
_RETRYABLE_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}

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
        proxy_url: str = "",
        proxy_token: str = "",
    ) -> None:
        self.api_url = api_url.strip() or DEFAULT_API_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_summary_chars = max(300, int(max_summary_chars))
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

    def _read_urls_sync(
        self,
        urls: tuple[str, ...],
        query: dict[str, Any] | None = None,
    ) -> bytes:
        """Read one of several equivalent endpoints with short WAF retries."""
        raw: bytes | None = None
        last_http_error: HTTPError | None = None
        last_url_error: URLError | None = None
        query = query or {}
        request_bases = urls[:1] if self.proxy_url else urls
        for base_url in request_bases:
            if self.proxy_url:
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
            if self.proxy_token:
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
    def _summary_from_wikitext(wikitext: str, max_chars: int) -> str:
        """Extract a short readable introduction from REST wikitext."""
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
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0].rstrip() + "..."

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
            "summary": self._summary_from_wikitext(
                source, self.max_summary_chars
            ),
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
            "summary": self._summary_from_wikitext(
                wikitext, self.max_summary_chars
            ),
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
                    "exchars": min(self.max_summary_chars, 1200),
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
                    "prop": "info|extracts|pageimages",
                    "inprop": "url",
                    "exintro": 1,
                    "explaintext": 1,
                    "exchars": min(self.max_summary_chars, 1200),
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
        if len(ranked) == 1 or ranked[0]["score"] >= 80 or ranked[0]["score"] - ranked[1]["score"] >= 12:
            return {"kind": "page", "page": ranked[0]}
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
        self._cache_set(key, result)
        return result
