from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import csv
import gzip
import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
TRANSLATOR_VERSION = "google-gtx-zh-cn-v1"
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
BING_TRANSLATOR_URL = "https://www.bing.com/translator"
BING_TRANSLATE_URL = "https://www.bing.com/ttranslatev3"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
TOKEN_RE = re.compile(r"ZXQ\d{6}QXZ")
ENGLISH_PROSE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "but",
        "by",
        "can",
        "for",
        "from",
        "has",
        "have",
        "he",
        "her",
        "his",
        "in",
        "is",
        "it",
        "of",
        "on",
        "she",
        "that",
        "the",
        "their",
        "they",
        "this",
        "to",
        "was",
        "were",
        "when",
        "while",
        "will",
        "with",
    }
)
ENGLISH_PROSE_ANCHORS = frozenset(
    {
        "are",
        "can",
        "has",
        "have",
        "he",
        "her",
        "his",
        "is",
        "it",
        "she",
        "that",
        "their",
        "they",
        "this",
        "was",
        "we",
        "were",
        "will",
        "you",
    }
)
HEADING_RE = re.compile(r"^==+\s*", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
REF_RE = re.compile(r"<ref\b[^>/]*>.*?</ref\s*>|<ref\b[^>]*/\s*>", re.I | re.S)
LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
EXTERNAL_LINK_RE = re.compile(r"\[https?://[^\s\]]+(?:\s+([^\]]+))?\]")
MANUAL_TERMS = {
    "Copy Ability": "复制能力（Copy Ability）",
    "Friend Ability": "盟友能力（Friend Ability）",
    "Kirby (series)": "星之卡比系列（Kirby series）",
    "Kirby series": "星之卡比系列（Kirby series）",
    "Mouthful Mode": "塞满嘴（Mouthful Mode）",
    "Officiant of Doom": "魔神官（Officiant of Doom）",
    "Planet Popstar": "波普之星（Planet Popstar）",
    "Popstar": "波普之星（Popstar）",
    "Power Effect": "能力效果（Power Effect）",
    "Story Mode": "故事模式（Story Mode）",
    "Warp Star": "传送之星（Warp Star）",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_page_cache(path: Path) -> dict[int, dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    pages = raw.get("pages", {}) if isinstance(raw, dict) else {}
    result: dict[int, dict[str, Any]] = {}
    for key, value in pages.items():
        if not isinstance(value, dict):
            continue
        try:
            pageid = int(key)
        except (TypeError, ValueError):
            continue
        result[pageid] = value
    return result


def fetch_rest_page(title: str) -> dict[str, Any]:
    url = "https://wikirby.com/w/rest.php/v1/page/" + urllib.parse.quote(
        title.strip(), safe=""
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "astrbot-plugin-kirby-catalog-profile-builder/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = json.loads(response.read().decode("utf-8"))
    latest = raw.get("latest", {}) if isinstance(raw, dict) else {}
    return {
        "title": str(raw.get("title") or title).strip(),
        "wikitext": str(raw.get("source") or ""),
        "revid": int(latest.get("id", 0) or 0),
        "timestamp": str(latest.get("timestamp") or ""),
    }


def catalog_items(path: Path) -> list[dict[str, Any]]:
    raw = read_json(path)
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    if isinstance(items, dict):
        items = list(items.values())
    return [item for item in items if isinstance(item, dict)]


def manifest_items(path: Path) -> list[dict[str, Any]]:
    raw = read_json(path)
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    if isinstance(items, dict):
        items = list(items.values())
    return [item for item in items if isinstance(item, dict)]


def normalise(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").casefold())


def bilingual(chinese: Any, english: Any) -> str:
    chinese_text = str(chinese or "").strip()
    english_text = str(english or "").strip()
    if (
        chinese_text
        and english_text
        and normalise(chinese_text) != normalise(english_text)
    ):
        return f"{chinese_text}（{english_text}）"
    return chinese_text or english_text


def find_template_end(text: str, start: int) -> int:
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
            if depth <= 0:
                return index
            continue
        index += 1
    return len(text)


def split_template(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    braces = 0
    brackets = 0
    index = 0
    while index < len(value):
        pair = value[index : index + 2]
        if pair == "{{":
            braces += 1
            index += 2
            continue
        if pair == "}}" and braces:
            braces -= 1
            index += 2
            continue
        if pair == "[[":
            brackets += 1
            index += 2
            continue
        if pair == "]]" and brackets:
            brackets -= 1
            index += 2
            continue
        if value[index] == "|" and braces == 0 and brackets == 0:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    parts.append(value[start:])
    return parts


def template_values(raw: str) -> tuple[str, list[str], dict[str, str]]:
    inner = raw[2:-2] if raw.startswith("{{") and raw.endswith("}}") else raw
    parts = split_template(inner)
    name = parts[0].strip().casefold() if parts else ""
    positional: list[str] = []
    named: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            if re.fullmatch(r"\s*(?:\d+|[A-Za-z][\w -]*)\s*", key):
                named[key.strip().casefold()] = value.strip()
                continue
        positional.append(part.strip())
    return name, positional, named


def onlyinclude_lead(wikitext: str) -> str:
    for match in re.finditer(
        r"<onlyinclude>(.*?)</onlyinclude>", wikitext, re.I | re.S
    ):
        block = match.group(1)
        marker = block.find("|lead|")
        if marker < 0:
            continue
        lead = block[marker + len("|lead|") :].strip()
        if lead.endswith("}}"):
            lead = lead[:-2].rstrip()
        return lead
    return ""


def lead_region(wikitext: str) -> str:
    source = COMMENT_RE.sub("", wikitext or "")
    transcluded = onlyinclude_lead(source)
    if transcluded:
        return transcluded
    return HEADING_RE.split(source, maxsplit=1)[0]


def first_quote(wikitext: str) -> tuple[str, str]:
    region = HEADING_RE.split(COMMENT_RE.sub("", wikitext or ""), maxsplit=1)[0]
    for match in re.finditer(r"\{\{\s*quote\s*\|", region, re.I):
        end = find_template_end(region, match.start())
        raw = region[match.start() : end]
        name, positional, named = template_values(raw)
        if name != "quote":
            continue
        quote = named.get("1") or (positional[0] if positional else "")
        attribution = named.get("2") or (positional[1] if len(positional) > 1 else "")
        return quote.strip(), attribution.strip()
    return "", ""


def template_replacement(raw: str) -> str:
    name, positional, named = template_values(raw)
    if name in {"color", "colour", "font color"}:
        return (positional[-1] if positional else named.get("text", "")).strip()
    if name in {"nowrap", "small", "big", "plainlist", "center", "tooltip"}:
        return (positional[-1] if positional else named.get("1", "")).strip()
    if name in {"nihongo", "ruby"}:
        return (positional[0] if positional else named.get("1", "")).strip()
    if name == "!":
        return "|"
    if name in {"'", "-"}:
        return "'" if name == "'" else "-"
    return " "


def remove_templates(text: str) -> str:
    result = text
    guard = 0
    while "{{" in result and guard < 10000:
        start = result.rfind("{{")
        end = find_template_end(result, start)
        raw = result[start:end]
        result = result[:start] + template_replacement(raw) + result[end:]
        guard += 1
    return result


def looks_proper(value: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", value)
    if not words:
        return False
    common = {
        "a",
        "an",
        "and",
        "ancient",
        "ability",
        "ally",
        "boss",
        "character",
        "enemy",
        "friend",
        "in",
        "mode",
        "of",
        "series",
        "the",
        "times",
    }
    significant = [word for word in words if word.casefold() not in common]
    return bool(significant) and all(word[0].isupper() for word in significant)


@dataclass
class TermProtector:
    names: dict[str, str]
    terms: dict[str, str] = field(default_factory=dict)
    global_pattern: re.Pattern[str] | None = None
    global_terms: dict[str, str] = field(default_factory=dict)
    replacements: dict[str, str] = field(default_factory=dict)
    next_token: int = 1000

    def token(self, replacement: str) -> str:
        for token, existing in self.replacements.items():
            if existing == replacement:
                return token
        token = f"ZXQ{self.next_token:06d}QXZ"
        self.next_token += 1
        self.replacements[token] = replacement
        return token

    def linked(self, raw: str) -> str:
        parts = raw.split("|", 1)
        target = parts[0].split("#", 1)[0].strip()
        display = (parts[1] if len(parts) > 1 else parts[0]).strip()
        display = re.sub(r"^(?:File|Image|Category):", "", display, flags=re.I)
        target_key = normalise(re.sub(r"^[a-z]+:", "", target, flags=re.I))
        display_key = normalise(display)
        display_replacement = self.names.get(display_key)
        if display_replacement:
            return self.token(display_replacement)
        target_replacement = self.names.get(target_key)
        target_label = re.sub(r"\s*\([^)]+\)\s*$", "", target).strip()
        if target_replacement and (
            normalise(display) == normalise(target_label) or looks_proper(display)
        ):
            return self.token(target_replacement)
        if looks_proper(display):
            return self.token(display)
        return display

    def protect_plain_terms(self, value: str) -> str:
        result = value
        ordered = sorted(
            ((source, target) for source, target in self.terms.items() if source),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for source, replacement in ordered:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])", re.I
            )
            result = pattern.sub(lambda _match: self.token(replacement), result)
        if self.global_pattern:
            result = self.global_pattern.sub(
                lambda match: self.token(
                    self.global_terms.get(match.group(0), match.group(0))
                ),
                result,
            )
        return result

    def restore(self, value: str) -> str:
        result = value
        for token, replacement in self.replacements.items():
            result = result.replace(token, replacement)
        return result


def plain_markup(raw: str, protector: TermProtector) -> str:
    text = COMMENT_RE.sub("", raw or "")
    text = REF_RE.sub("", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = LINK_RE.sub(lambda match: protector.linked(match.group(1)), text)
    text = EXTERNAL_LINK_RE.sub(lambda match: match.group(1) or "", text)
    text = remove_templates(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = protector.protect_plain_terms(text)
    text = html.unescape(text)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        cleaned = re.sub(r"\s+", " ", paragraph).strip(" \t:;—-")
        if cleaned and not cleaned.startswith(("__", "[[Category:")):
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


def limit_intro(value: str, max_chars: int = 1800, max_paragraphs: int = 6) -> str:
    selected: list[str] = []
    length = 0
    for paragraph in value.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if selected and (
            len(selected) >= max_paragraphs or length + len(paragraph) > max_chars
        ):
            break
        if not selected and len(paragraph) > max_chars:
            truncated = paragraph[:max_chars]
            sentence = max(
                truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? ")
            )
            paragraph = (
                truncated[: sentence + 1] if sentence >= max_chars // 2 else truncated
            )
        selected.append(paragraph)
        length += len(paragraph)
    return "\n\n".join(selected)


def clean_quote(value: str, max_chars: int = 600) -> str:
    value = re.sub(r"\s+", " ", value).strip().strip('"“”')
    if len(value) <= max_chars:
        return value
    truncated = value[:max_chars]
    sentence = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    return (
        truncated[: sentence + 1] if sentence >= max_chars // 2 else truncated
    ).strip()


def normalise_chinese_spacing(value: str) -> str:
    value = re.sub(r"[ \t]+", " ", value)
    chinese_side = r"\u3400-\u9fff，。！？；：、“”‘’（）《》【】"
    value = re.sub(rf"(?<=[{chinese_side}]) +", "", value)
    value = re.sub(rf" +(?=[{chinese_side}])", "", value)
    return value.strip()


class GoogleTranslator:
    def __init__(
        self,
        cache_path: Path,
        batch_size: int = 4,
        request_delay: float = 0.35,
        batch_chars: int = 4500,
        engine: str = "auto",
    ) -> None:
        self.cache_path = cache_path
        self.batch_size = max(1, min(int(batch_size), 8))
        self.request_delay = max(0.0, float(request_delay))
        self.batch_chars = max(1000, int(batch_chars))
        self.engine = engine if engine in {"auto", "google", "bing"} else "auto"
        self.lock = threading.RLock()
        self.completed_since_save = 0
        self.last_request_at = 0.0
        self.bing_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.bing_ig = ""
        self.bing_iid = ""
        self.bing_key = ""
        self.bing_token = ""
        self.bing_request_index = 0
        if cache_path.is_file():
            raw = read_json(cache_path)
            self.cache = raw if isinstance(raw, dict) else {}
        else:
            self.cache: dict[str, str] = {}

    @staticmethod
    def cache_key(value: str) -> str:
        payload = f"{TRANSLATOR_VERSION}\0{value}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _throttle(self) -> None:
        with self.lock:
            wait = self.request_delay - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()

    def request_google(self, value: str) -> str:
        body = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": value}
        ).encode("utf-8")
        request = urllib.request.Request(
            TRANSLATE_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "astrbot-plugin-kirby-catalog-profile-builder/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        rows = raw[0] if isinstance(raw, list) and raw else []
        return "".join(str(row[0]) for row in rows if row and row[0]).strip()

    def _refresh_bing_session(self) -> None:
        request = urllib.request.Request(
            BING_TRANSLATOR_URL,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with self.bing_opener.open(request, timeout=45) as response:
            page = response.read().decode("utf-8", errors="replace")
        ig_match = re.search(r'IG:"([^"]+)"', page)
        iid_match = re.search(r'data-iid="(translator\.[^"]+)"', page)
        token_match = re.search(
            r'params_AbusePreventionHelper\s*=\s*\[(\d+),"([^"]+)"', page
        )
        if not ig_match or not iid_match or not token_match:
            raise RuntimeError("unable to read Bing Translator session tokens")
        self.bing_ig = ig_match.group(1)
        self.bing_iid = iid_match.group(1)
        self.bing_key = token_match.group(1)
        self.bing_token = token_match.group(2)
        self.bing_request_index = 0

    def request_bing(self, value: str) -> str:
        for session_attempt in range(2):
            with self.lock:
                if not self.bing_ig or not self.bing_token:
                    self._refresh_bing_session()
                self.bing_request_index += 1
                bing_ig = self.bing_ig
                bing_iid = self.bing_iid
                bing_key = self.bing_key
                bing_token = self.bing_token
                request_index = self.bing_request_index
            query = urllib.parse.urlencode(
                {
                    "isVertical": "1",
                    "IG": bing_ig,
                    "IID": f"{bing_iid}.{request_index}",
                }
            )
            body = urllib.parse.urlencode(
                {
                    "fromLang": "en",
                    "to": "zh-Hans",
                    "text": value,
                    "token": bing_token,
                    "key": bing_key,
                    "tryFetchingGenderDebiasedTranslations": "true",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{BING_TRANSLATE_URL}?{query}",
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": BROWSER_USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": BING_TRANSLATOR_URL,
                },
            )
            try:
                with self.bing_opener.open(request, timeout=45) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                payload = raw[0] if isinstance(raw, list) and raw else raw
                translations = (
                    payload.get("translations", {}) if isinstance(payload, dict) else {}
                )
                if isinstance(translations, list) and translations:
                    translated = str(translations[0].get("text") or "").strip()
                    if translated:
                        return translated
                raise RuntimeError("Bing Translator returned no translation")
            except urllib.error.HTTPError as exc:
                if exc.code not in {401, 403} or session_attempt:
                    raise
                with self.lock:
                    self.bing_ig = ""
                    self.bing_token = ""
        raise RuntimeError("Bing Translator session refresh failed")

    def request(self, value: str) -> str:
        self._throttle()
        if self.engine == "google":
            return self.request_google(value)
        if self.engine == "bing":
            return self.request_bing(value)
        try:
            return self.request_google(value)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            self.engine = "bing"
            return self.request_bing(value)

    def lookup(self, value: str) -> str:
        key = self.cache_key(value)
        with self.lock:
            cached = self.cache.get(key)
        return cached if isinstance(cached, str) else ""

    def store(self, source: str, translated: str) -> None:
        with self.lock:
            self.cache[self.cache_key(source)] = translated
            self.completed_since_save += 1
            if self.completed_since_save >= 20:
                self.save()

    def remove(self, source: str) -> None:
        with self.lock:
            self.cache.pop(self.cache_key(source), None)

    def request_with_retry(self, value: str) -> str:
        error = ""
        for attempt in range(5):
            try:
                translated = self.request(value)
                if not translated:
                    raise RuntimeError("translation returned empty text")
                return translated
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
                error = str(exc)
                time.sleep(min(8.0, 0.75 * (2**attempt)))
        raise RuntimeError(error or "translation failed")

    @staticmethod
    def split_value(value: str, max_chars: int) -> list[str]:
        value = value.strip()
        if len(value) <= max_chars:
            return [value]
        chunks: list[str] = []
        remaining = value
        boundaries = ("\n\n", ". ", "! ", "? ", "; ", ", ", " ")
        while len(remaining) > max_chars:
            window = remaining[: max_chars + 1]
            cut = max(window.rfind(boundary) + len(boundary) for boundary in boundaries)
            if cut < max_chars // 2:
                cut = max_chars
            token_start = remaining.rfind("ZXQ", 0, cut)
            token_end = remaining.rfind("QXZ", 0, cut)
            if token_start > token_end and token_start >= max_chars // 2:
                cut = token_start
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            chunks.append(remaining)
        return [chunk for chunk in chunks if chunk]

    def request_batch(self, values: list[str]) -> list[str]:
        markers = [f"ZXQ{970000 + index:06d}QXZ" for index in range(len(values) + 1)]
        payload_parts: list[str] = []
        for marker, value in zip(markers, values):
            payload_parts.extend([marker, value])
        payload_parts.append(markers[-1])
        translated = self.request_with_retry("".join(payload_parts))
        result: list[str] = []
        try:
            for position in range(len(values)):
                start = translated.index(markers[position]) + len(markers[position])
                end = translated.index(markers[position + 1], start)
                value = translated[start:end].strip()
                if not value:
                    raise ValueError("empty translated batch item")
                result.append(value)
        except ValueError:
            result = [self.request_with_retry(value) for value in values]
        return result

    def translate_batch(self, values: list[str]) -> list[str]:
        result = [self.lookup(value) for value in values]
        missing = [index for index, value in enumerate(result) if not value]
        if not missing:
            return result

        request_limit = 850 if self.engine in {"auto", "bing"} else self.batch_chars
        chunk_rows: list[tuple[int, int, str]] = []
        chunks_by_value: dict[int, list[str]] = {}
        for index in missing:
            chunks = self.split_value(values[index], max(500, request_limit - 80))
            chunks_by_value[index] = [""] * len(chunks)
            chunk_rows.extend(
                (index, chunk_index, chunk) for chunk_index, chunk in enumerate(chunks)
            )

        request_groups: list[list[tuple[int, int, str]]] = []
        position = 0
        while position < len(chunk_rows):
            batch_rows: list[tuple[int, int, str]] = []
            size = 0
            while position < len(chunk_rows):
                row = chunk_rows[position]
                marker_overhead = 16 * (len(batch_rows) + 2)
                if batch_rows and (
                    len(batch_rows) >= self.batch_size
                    or size + len(row[2]) + marker_overhead > request_limit
                ):
                    break
                batch_rows.append(row)
                size += len(row[2])
                position += 1
            request_groups.append(batch_rows)

        def translate_rows(rows: list[tuple[int, int, str]]) -> list[str]:
            return self.request_batch([row[2] for row in rows])

        translated_groups: list[list[str]] = []
        if self.engine == "auto" and request_groups:
            translated_groups.append(translate_rows(request_groups[0]))
            request_groups = request_groups[1:]
        if self.engine == "bing" and len(request_groups) > 1:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.batch_size
            ) as executor:
                translated_groups.extend(executor.map(translate_rows, request_groups))
        else:
            translated_groups.extend(map(translate_rows, request_groups))

        all_groups = chunk_rows
        offset = 0
        for translated in translated_groups:
            batch_rows = all_groups[offset : offset + len(translated)]
            for row, value in zip(batch_rows, translated):
                chunks_by_value[row[0]][row[1]] = value
            offset += len(translated)

        for index in missing:
            translated = " ".join(chunks_by_value[index]).strip()
            if not translated:
                raise RuntimeError("translated page payload is empty")
            result[index] = translated
            self.store(values[index], translated)
        return result

    def batches(self, values: list[str]) -> Iterable[list[str]]:
        current: list[str] = []
        size = 0
        for value in values:
            if current and (
                len(current) >= self.batch_size or size + len(value) > self.batch_chars
            ):
                yield current
                current = []
                size = 0
            current.append(value)
            size += len(value)
        if current:
            yield current

    def save(self) -> None:
        with self.lock:
            write_json(self.cache_path, self.cache)
            self.completed_since_save = 0


@dataclass
class PageDraft:
    pageid: int
    page_title: str
    source_url: str
    source_revision: int
    source_timestamp: str
    summary_en: str
    quote_en: str
    quote_attribution_en: str
    protector: TermProtector
    summary_zh: str = ""
    quote_zh: str = ""
    quote_attribution_zh: str = ""
    error: str = ""

    @staticmethod
    def pack_translation_fields(fields: Iterable[str]) -> str:
        separator_a = "ZXQ900001QXZ"
        separator_b = "ZXQ900002QXZ"
        paragraph = "ZXQ900003QXZ"
        return (
            separator_a.join(
                str(field or "").replace("\n\n", paragraph) for field in fields
            )
            + separator_b
        )

    def translation_fields(self) -> tuple[str, str, str]:
        return self.quote_en, self.quote_attribution_en, self.summary_en

    def translation_payload(self) -> str:
        return self.pack_translation_fields(self.translation_fields())

    def apply_field_translations(self, fields: Iterable[str]) -> None:
        values = list(fields)
        if len(values) != 3:
            raise ValueError("translation field count was changed")
        restored = [
            normalise_chinese_spacing(self.protector.restore(value))
            for value in values
        ]
        self.quote_zh, self.quote_attribution_zh, self.summary_zh = (
            part.strip() for part in restored
        )
        unresolved = TOKEN_RE.findall("\n".join(restored))
        if unresolved:
            raise ValueError(
                f"unresolved placeholders: {', '.join(sorted(set(unresolved)))}"
            )

    def apply_translation(self, value: str) -> None:
        separator_a = "ZXQ900001QXZ"
        separator_b = "ZXQ900002QXZ"
        paragraph = "ZXQ900003QXZ"
        if separator_b not in value:
            raise ValueError("translation boundary marker was changed")
        value = value.split(separator_b, 1)[0]
        parts = value.split(separator_a)
        if len(parts) != 3:
            raise ValueError("translation field marker was changed")
        self.apply_field_translations(
            part.replace(paragraph, "\n\n") for part in parts
        )


def translate_draft_fields(
    translator: GoogleTranslator, draft: PageDraft
) -> None:
    """Translate one draft without relying on cross-field boundary markers."""
    source_groups: list[list[int]] = []
    sources: list[str] = []
    for field in draft.translation_fields():
        indexes: list[int] = []
        for paragraph in str(field or "").split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            indexes.append(len(sources))
            sources.append(paragraph)
        source_groups.append(indexes)

    translated = translator.translate_batch(sources) if sources else []
    fields = [
        "\n\n".join(translated[index] for index in indexes)
        for indexes in source_groups
    ]
    draft.apply_field_translations(fields)
    translator.store(
        draft.translation_payload(), draft.pack_translation_fields(fields)
    )


def draft_translation_looks_incomplete(draft: PageDraft) -> bool:
    text = "\n".join(
        (draft.quote_zh, draft.quote_attribution_zh, draft.summary_zh)
    ).strip()
    return contains_english_prose(text)


def contains_english_prose(value: str) -> bool:
    without_parenthetical_names = re.sub(
        r"[（(][^（）()\n]*[A-Za-z][^（）()\n]*[）)]", "", str(value or "")
    )
    for span in re.split(r"[\u3400-\u9fff]", without_parenthetical_names):
        words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", span)
        if len(words) < 8:
            continue
        folded = [word.casefold() for word in words]
        common = sum(word in ENGLISH_PROSE_WORDS for word in folded)
        anchors = sum(word in ENGLISH_PROSE_ANCHORS for word in folded)
        if common >= 3 and anchors:
            return True
    return False


def combined_description(quote: str, attribution: str, summary: str) -> str:
    parts: list[str] = []
    if quote:
        parts.append(f"“{quote.strip().strip('“”')}”")
        if attribution:
            parts.append(f"— {attribution.strip()}")
    if summary:
        parts.append(summary.strip())
    return (
        "\n".join(parts[:2]) + ("\n" if len(parts) > 2 else "") + "\n".join(parts[2:])
    )


def variant_context(
    entry: dict[str, Any], meta: dict[str, Any], name_map: dict[str, str]
) -> tuple[str, str]:
    page_title = str(entry.get("page_title") or "").strip()
    variant = str(entry.get("variant_key") or "").strip()
    if not variant or normalise(variant) == normalise(page_title):
        return "", ""
    display_name = str(meta.get("display_name") or variant)
    display_work = str(meta.get("display_work") or "")
    base_name = name_map.get(normalise(page_title), page_title)
    work_zh = f"，首次登场于《{display_work}》" if display_work else ""
    work_en = f" in {meta.get('debut_work_en')}" if meta.get("debut_work_en") else ""
    folded = variant.casefold()
    if folded.endswith(", " + page_title.casefold()):
        relation_zh = "带有特殊称号的版本"
        relation_en = "a titled version of"
    elif "crystal" in folded or "结晶化" in display_name:
        relation_zh = "的结晶化形态"
        relation_en = "a crystallized form of"
    elif re.search(r"(?:^|\s)ex(?:$|\s|[,&])", folded):
        relation_zh = "的 EX 版本"
        relation_en = "an EX version of"
    elif "phase" in folded or "阶段" in display_name:
        relation_zh = "的独立阶段形态"
        relation_en = "a distinct phase of"
    elif "form" in folded or "形态" in display_name:
        relation_zh = "的特殊形态"
        relation_en = "a special form of"
    elif any(
        word in folded for word in ("sizzle", "blizzard", "bluster", "splash", "zap")
    ):
        relation_zh = "的元素强化形态"
        relation_en = "an element-enhanced form related to"
    else:
        relation_zh = "的独立形态或变体"
        relation_en = "a distinct form or variant of"
    return (
        f"{display_name}是{base_name}{relation_zh}{work_zh}。",
        f"{variant} is {relation_en} {page_title}{work_en}.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the bundled Kirby catalog profile and introduction database."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--supplemental-manifest", type=Path, required=True)
    parser.add_argument("--base-cache", type=Path, required=True)
    parser.add_argument("--supplemental-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--translation-cache", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--request-delay", type=float, default=0.35)
    parser.add_argument("--batch-chars", type=int, default=4500)
    parser.add_argument(
        "--translator", choices=("auto", "google", "bing"), default="auto"
    )
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--fetch-missing", action="store_true")
    return parser


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = build_parser().parse_args()
    entries = catalog_items(args.catalog)
    base_manifest = manifest_items(args.base_manifest)
    supplemental_manifest = manifest_items(args.supplemental_manifest)
    pages = read_page_cache(args.base_cache)
    pages.update(read_page_cache(args.supplemental_cache))
    if args.fetch_missing:
        missing_pages = {
            int(entry.get("pageid", 0) or 0): str(entry.get("page_title") or "")
            for entry in entries
            if int(entry.get("pageid", 0) or 0) not in pages
            and str(entry.get("page_title") or "").strip()
        }
        for pageid, title in missing_pages.items():
            try:
                pages[pageid] = fetch_rest_page(title)
                print(f"fetched missing page {pageid}: {title}", flush=True)
            except Exception as exc:  # noqa: BLE001 - retained in unmatched report
                print(f"failed to fetch missing page {pageid} ({title}): {exc}")

    supplemental_by_key = {
        str(item.get("entry_key")): item
        for item in supplemental_manifest
        if item.get("entry_key")
    }
    base_by_pageid = {
        int(item.get("pageid", 0) or 0): item
        for item in base_manifest
        if item.get("pageid")
    }

    matched_manifest: dict[str, dict[str, Any]] = {}
    profile_meta: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_key = str(entry.get("entry_key") or "").strip()
        manifest = supplemental_by_key.get(entry_key) or base_by_pageid.get(
            int(entry.get("pageid", 0) or 0)
        )
        if manifest:
            matched_manifest[entry_key] = manifest
        name_zh = str((manifest or {}).get("chinese_name") or "").strip()
        name_en = str(
            (manifest or {}).get("english_name")
            or entry.get("variant_key")
            or entry.get("page_title")
            or ""
        ).strip()
        work_zh = str((manifest or {}).get("official_chinese_work") or "").strip()
        work_en = str(
            (manifest or {}).get("earliest_work")
            or entry.get("debut_work")
            or entry.get("source")
            or ""
        ).strip()
        profile_meta[entry_key] = {
            "name_zh": name_zh,
            "name_en": name_en,
            "display_name": bilingual(name_zh, name_en),
            "debut_work_zh": work_zh,
            "debut_work_en": work_en,
            "display_work": bilingual(work_zh, work_en),
        }

    name_map: dict[str, str] = {}
    global_terms: dict[str, str] = {}
    for entry in entries:
        entry_key = str(entry.get("entry_key") or "")
        meta = profile_meta[entry_key]
        page_title = str(entry.get("page_title") or "").strip()
        variant_key = str(entry.get("variant_key") or "").strip()
        is_base = (
            normalise(page_title) == normalise(variant_key)
            or str(entry.get("asset_set")) == "base"
        )
        if is_base and page_title:
            name_map.setdefault(normalise(page_title), meta["display_name"])
            global_terms.setdefault(page_title, meta["display_name"])
        if meta["name_en"]:
            name_map.setdefault(normalise(meta["name_en"]), meta["display_name"])
            global_terms.setdefault(meta["name_en"], meta["display_name"])
        if variant_key:
            name_map.setdefault(normalise(variant_key), meta["display_name"])
            global_terms.setdefault(variant_key, meta["display_name"])

    work_map: dict[str, str] = {}
    for meta in profile_meta.values():
        if meta["debut_work_en"]:
            work_map.setdefault(normalise(meta["debut_work_en"]), meta["display_work"])
            global_terms.setdefault(meta["debut_work_en"], meta["display_work"])
    name_map.update(work_map)
    for source, target in MANUAL_TERMS.items():
        name_map[normalise(source)] = target
        global_terms[source] = target
    global_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:"
        + "|".join(
            re.escape(source)
            for source in sorted(global_terms, key=len, reverse=True)
            if source
        )
        + r")(?![A-Za-z0-9])"
    )

    title_aliases: dict[str, str] = {}
    for entry in entries:
        entry_key = str(entry.get("entry_key") or "")
        meta = profile_meta[entry_key]
        page_title = str(entry.get("page_title") or "").strip()
        variant = str(entry.get("variant_key") or "").strip()
        if not variant.casefold().endswith(", " + page_title.casefold()):
            continue
        english_title = variant[: -(len(page_title) + 2)].strip()
        chinese_full = meta["name_zh"]
        base_display = name_map.get(normalise(page_title), "")
        base_chinese = base_display.split("（", 1)[0].strip() if base_display else ""
        chinese_title = chinese_full
        if base_chinese and chinese_full.endswith(base_chinese):
            chinese_title = chinese_full[: -len(base_chinese)].strip()
        if english_title and chinese_title:
            title_aliases[english_title] = bilingual(chinese_title, english_title)

    entries_by_page: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        entries_by_page.setdefault(int(entry.get("pageid", 0) or 0), []).append(entry)

    drafts: dict[int, PageDraft] = {}
    for pageid, page_entries in entries_by_page.items():
        page = pages.get(pageid)
        if not page:
            continue
        base_entry = next(
            (
                entry
                for entry in page_entries
                if normalise(entry.get("variant_key"))
                == normalise(entry.get("page_title"))
            ),
            page_entries[0],
        )
        entry_key = str(base_entry.get("entry_key") or "")
        meta = profile_meta[entry_key]
        page_title = str(
            page.get("title") or base_entry.get("page_title") or ""
        ).strip()
        page_display = name_map.get(normalise(page_title), page_title)
        terms = {
            page_title: page_display,
            meta["name_en"]: meta["display_name"],
            meta["debut_work_en"]: meta["display_work"],
            **title_aliases,
        }
        terms = {key: value for key, value in terms.items() if key and value}
        protector = TermProtector(
            name_map,
            terms,
            global_pattern=global_pattern,
            global_terms=global_terms,
        )
        wikitext = str(page.get("wikitext") or "")
        quote_raw, attribution_raw = first_quote(wikitext)
        quote_en = clean_quote(plain_markup(quote_raw, protector))
        attribution_en = clean_quote(plain_markup(attribution_raw, protector), 400)
        summary_en = limit_intro(plain_markup(lead_region(wikitext), protector))
        drafts[pageid] = PageDraft(
            pageid=pageid,
            page_title=page_title,
            source_url="https://wikirby.com/wiki/"
            + urllib.parse.quote(page_title.replace(" ", "_"), safe="():"),
            source_revision=int(page.get("revid", 0) or 0),
            source_timestamp=str(page.get("timestamp") or ""),
            summary_en=summary_en,
            quote_en=quote_en,
            quote_attribution_en=attribution_en,
            protector=protector,
        )

    if not args.no_translate:
        cache_path = args.translation_cache or args.report_dir / "翻译断点缓存.json"
        translator = GoogleTranslator(
            cache_path,
            batch_size=args.workers,
            request_delay=args.request_delay,
            batch_chars=args.batch_chars,
            engine=args.translator,
        )
        pending = [
            draft for draft in drafts.values() if draft.summary_en or draft.quote_en
        ]
        completed = 0
        payload_to_drafts: dict[str, list[PageDraft]] = {}
        for draft in pending:
            payload_to_drafts.setdefault(draft.translation_payload(), []).append(draft)
        payloads = list(payload_to_drafts)
        for batch in translator.batches(payloads):
            try:
                translated_batch = translator.translate_batch(batch)
            except Exception as exc:  # noqa: BLE001 - report individual page failures
                translated_batch = [""] * len(batch)
                for payload in batch:
                    for draft in payload_to_drafts[payload]:
                        draft.error = f"翻译失败：{exc}"
            for payload, translated in zip(batch, translated_batch):
                drafts_for_payload = payload_to_drafts[payload]
                for draft in drafts_for_payload:
                    if not draft.error:
                        try:
                            draft.apply_translation(translated)
                        except Exception as exc:  # noqa: BLE001 - report page failures
                            draft.error = f"翻译失败：{exc}"
                            translator.remove(payload)
                    completed += 1
                if completed % 50 == 0 or completed == len(pending):
                    print(f"translated {completed}/{len(pending)}", flush=True)
        failed_drafts = [draft for draft in pending if draft.error]
        recovered = 0
        for draft in failed_drafts:
            combined_error = draft.error
            try:
                translate_draft_fields(translator, draft)
                draft.error = ""
                recovered += 1
            except Exception as exc:  # noqa: BLE001 - retain the page in the report
                draft.error = f"{combined_error}；分字段重试失败：{exc}"
        if failed_drafts:
            print(
                f"field fallback recovered {recovered}/{len(failed_drafts)}",
                flush=True,
            )
        incomplete_drafts = [
            draft
            for draft in pending
            if not draft.error and draft_translation_looks_incomplete(draft)
        ]
        improved = 0
        for draft in incomplete_drafts:
            previous = (
                draft.quote_zh,
                draft.quote_attribution_zh,
                draft.summary_zh,
            )
            translator.remove(draft.translation_payload())
            try:
                translate_draft_fields(translator, draft)
                improved += 1
            except Exception:  # noqa: BLE001 - keep the usable first translation
                (
                    draft.quote_zh,
                    draft.quote_attribution_zh,
                    draft.summary_zh,
                ) = previous
        if incomplete_drafts:
            print(
                f"incomplete translation retry processed "
                f"{improved}/{len(incomplete_drafts)}",
                flush=True,
            )
        translator.save()
    else:
        for draft in drafts.values():
            draft.summary_zh = draft.protector.restore(draft.summary_en)
            draft.quote_zh = draft.protector.restore(draft.quote_en)
            draft.quote_attribution_zh = draft.protector.restore(
                draft.quote_attribution_en
            )

    profiles: dict[str, dict[str, Any]] = {}
    report_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: int(item.get("id", 0) or 0)):
        entry_key = str(entry.get("entry_key") or "").strip()
        pageid = int(entry.get("pageid", 0) or 0)
        meta = profile_meta[entry_key]
        draft = drafts.get(pageid)
        reasons: list[str] = []
        if entry_key not in matched_manifest:
            reasons.append("未匹配到素材收集清单")
        if not draft:
            reasons.append("本地页面源码缓存缺失")
        elif not draft.summary_en and not draft.quote_en:
            reasons.append("WiKirby 页面开头没有可读简介")
        elif draft.error:
            reasons.append(draft.error)
        description_en = (
            combined_description(
                draft.protector.restore(draft.quote_en),
                draft.protector.restore(draft.quote_attribution_en),
                draft.protector.restore(draft.summary_en),
            )
            if draft
            else ""
        )
        description_zh = (
            combined_description(
                draft.quote_zh, draft.quote_attribution_zh, draft.summary_zh
            )
            if draft and not draft.error
            else ""
        )
        context_zh, context_en = variant_context(entry, meta, name_map)
        if context_zh and description_zh:
            description_zh = f"{context_zh}\n{description_zh}"
        elif context_zh:
            description_zh = context_zh
        if context_en and description_en:
            description_en = f"{context_en}\n{description_en}"
        elif context_en:
            description_en = context_en
        status = "matched" if description_zh and not reasons else "unmatched"
        profile = {
            "catalog_id": int(entry.get("id", 0) or 0),
            "entry_key": entry_key,
            "pageid": pageid,
            "page_title": str(entry.get("page_title") or ""),
            "variant_key": str(entry.get("variant_key") or ""),
            **meta,
            "description_zh": description_zh,
            "description_en": description_en,
            "source_url": draft.source_url if draft else "",
            "source_revision": draft.source_revision if draft else 0,
            "source_timestamp": draft.source_timestamp if draft else "",
            "description_scope": (
                "variant_context_and_page_lead" if context_zh else "page_lead"
            ),
            "status": status,
            "reason": "；".join(reasons),
        }
        profiles[entry_key] = profile
        row = {
            "图鉴编号": profile["catalog_id"],
            "entry_key": entry_key,
            "中文名": meta["name_zh"],
            "英文名": meta["name_en"],
            "显示名称": meta["display_name"],
            "作品中文名": meta["debut_work_zh"],
            "作品英文名": meta["debut_work_en"],
            "作品显示名称": meta["display_work"],
            "WiKirby页面": profile["page_title"],
            "来源链接": profile["source_url"],
            "来源修订": profile["source_revision"],
            "简介状态": status,
            "未匹配原因": profile["reason"],
            "简体中文简介": description_zh,
        }
        report_rows.append(row)
        if status != "matched":
            unresolved.append(row)

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": {
            "name": "WiKirby",
            "url": "https://wikirby.com/",
            "license": "GNU Free Documentation License 1.3 or later",
            "license_url": "https://www.gnu.org/licenses/fdl-1.3.html",
            "notice": "Descriptions are translated and terminology-normalized derivatives of WiKirby page leads.",
        },
        "stats": {
            "catalog_entries": len(entries),
            "unique_pages": len(entries_by_page),
            "manifest_matches": len(matched_manifest),
            "profiles_with_description": sum(
                bool(profile["description_zh"]) for profile in profiles.values()
            ),
            "unmatched_profiles": len(unresolved),
        },
        "items": profiles,
    }
    write_json(args.output, output)
    fields = [
        "图鉴编号",
        "entry_key",
        "中文名",
        "英文名",
        "显示名称",
        "作品中文名",
        "作品英文名",
        "作品显示名称",
        "WiKirby页面",
        "来源链接",
        "来源修订",
        "简介状态",
        "未匹配原因",
        "简体中文简介",
    ]
    write_csv(args.report_dir / "图鉴中英文名称与简介.csv", report_rows, fields)
    write_csv(args.report_dir / "未匹配简介.csv", unresolved, fields)
    write_json(
        args.report_dir / "生成汇总.json",
        output["stats"] | {"generated_at": generated_at},
    )
    print(json.dumps(output["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
