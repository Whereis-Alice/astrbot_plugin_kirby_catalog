from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import Tag

DEFAULT_SHINKAKU_SITE_URL = "https://seesaawiki.jp/kirby_shinkaku"
SHINKAKU_SITE_LABEL = "卡比真格攻略 Wiki"
SHINKAKU_ENGLISH_CORNER_TITLE = "英語のコーナー"

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
_SECTION_CLASS_RE = re.compile(r"^wiki-section-body-(\d+)$")
_SKIPPED_PAGE_TITLES = {"menubar1", "トップページ"}


class KirbyShinkakuError(RuntimeError):
    """Raised when the public Seesaa Wiki cannot answer a read-only query."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _normalise_term(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").casefold())


def _clean_text(element: Tag | None) -> str:
    if element is None:
        return ""
    fragment = BeautifulSoup(str(element), "html.parser")
    for unwanted in fragment.select(
        "script, style, noscript, .part-edit, .history, .adsense-box, "
        ".page-social-link, .page-social-link-top, .page-social-link-bottom"
    ):
        unwanted.decompose()
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")
    text = html.unescape(fragment.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*(?:編集|履歴)\s*$", "", text).strip()


def _table_lines(table: Tag) -> list[str]:
    """Keep tables readable in messages and cards without dropping any rows."""

    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        values = [_clean_text(cell) for cell in cells]
        values = [value for value in values if value]
        if values:
            rows.append(values)
    if not rows:
        return []

    header = rows[0]
    output = ["表格：" + " | ".join(header)]
    for row in rows[1:]:
        output.append("- " + " | ".join(row))
    return output


def _section_text(body: Tag) -> str:
    """Read a Seesaa section one direct block at a time to avoid duplicates."""

    parts: list[str] = []
    for child in body.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "table":
            parts.extend(_table_lines(child))
            continue
        if child.name in {"ul", "ol"}:
            for item in child.find_all("li", recursive=False):
                value = _clean_text(item)
                if value:
                    parts.append(f"- {value}")
            continue
        if child.name == "dl":
            value = _clean_text(child)
            if value:
                parts.append(value)
            continue
        if child.select_one("table") and child.name in {"div", "section"}:
            # Nested sections are parsed independently below. Only include the
            # direct textual content of a wrapper here.
            direct_text = " ".join(
                _clean_text(grandchild)
                for grandchild in child.find_all(recursive=False)
                if isinstance(grandchild, Tag) and grandchild.name != "table"
            ).strip()
            if direct_text:
                parts.append(direct_text)
            continue
        value = _clean_text(child)
        if value:
            parts.append(value)
    return "\n".join(dict.fromkeys(parts)).strip()


def _section_level(body: Tag) -> str:
    for class_name in body.get("class", []):
        match = _SECTION_CLASS_RE.match(str(class_name))
        if match:
            return match.group(1)
    return "1"


def _page_sections(root: Tag) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for body in root.select("div[class*='wiki-section-body-']"):
        title_element = body.find_previous(["h3", "h4", "h5", "h6"])
        title = _clean_text(title_element)
        text = _section_text(body)
        if not title or not text:
            continue
        key = (title, text)
        if key in seen:
            continue
        seen.add(key)
        sections.append(
            {"title": title, "text": text, "level": _section_level(body)}
        )
    return sections


def _page_image_url(root: Tag, base_url: str) -> str:
    for image in root.select("img[src]"):
        source = urljoin(base_url, str(image.get("src") or "").strip())
        parsed = urlparse(source)
        if not _IMAGE_HOST_RE.fullmatch((parsed.hostname or "").casefold()):
            continue
        if not parsed.path.startswith("/k/u/kirby_shinkaku/"):
            continue
        return source
    return ""


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
    ) -> None:
        self.site_url = site_url.strip().rstrip("/") or DEFAULT_SHINKAKU_SITE_URL
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.proxy_url = proxy_url.strip().rstrip("/")
        self.proxy_token = proxy_token.strip()
        self._proxy_preferred_until = 0.0
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

    def _proxy_configured(self) -> bool:
        return bool(self.proxy_url and self.proxy_token)

    @staticmethod
    def _is_page_path(pathname: str) -> bool:
        return pathname.startswith("/kirby_shinkaku/d/")

    @staticmethod
    def _is_image_path(hostname: str, pathname: str) -> bool:
        return bool(
            _IMAGE_HOST_RE.fullmatch(hostname)
            and pathname.startswith("/k/u/kirby_shinkaku/")
        )

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

    def _request_headers(self, *, image: bool) -> dict[str, str]:
        return {
            "Accept": "image/*" if image else "text/html,application/xhtml+xml",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": self.site_url + "/",
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
        headers = self._request_headers(image=image)
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
        sections = _page_sections(root)
        section_titles = [str(row["title"]) for row in sections[:6]]
        summary = (
            f"真格攻略 Wiki 的「{title}」资料页"
            + (f"，包含{ '、'.join(section_titles) }等内容。" if section_titles else "。")
        )
        return {
            "title": title,
            "summary": summary,
            "url": source_url,
            "image_url": _page_image_url(root, source_url),
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

        supplied_aliases = [query, *(aliases or [])]
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
