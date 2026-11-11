"""QQ official Markdown, command keyboards and temporary platform image uploads.

The authenticated client belongs to AstrBot. No application secrets, adapter
patches or button callbacks are required by this plugin.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import logger
from astrbot.api import message_components as Comp
from PIL import Image

OFFICIAL_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})
MAX_PAGE_CHARS = 2800
PAGE_TTL = 20 * 60
MAX_PAGE_SESSIONS = 64
MAX_SESSION_CHARS = 400_000


def _route(method: str, path: str, **kwargs: Any) -> Any:
    from botpy.http import Route

    return Route(method, path, **kwargs)


def is_official(event: Any) -> bool:
    getter = getattr(event, "get_platform_name", None)
    try:
        name = getter() if callable(getter) else ""
    except (AttributeError, TypeError, RuntimeError):
        name = ""
    name = name or getattr(getattr(event, "platform_meta", None), "name", "")
    return str(name) in OFFICIAL_PLATFORMS


def platform_key(event: Any) -> str:
    getter = getattr(event, "get_platform_id", None)
    try:
        value = getter() if callable(getter) else ""
    except (AttributeError, TypeError, RuntimeError):
        value = ""
    value = value or getattr(getattr(event, "platform_meta", None), "id", "")
    return str(value or getattr(event, "unified_msg_origin", "")).split(":", 1)[0]


def bot_key(event: Any) -> str:
    # App ID is an identity, never the token or secret. Include the selected
    # adapter so a changed account cannot reuse another app's image registry.
    api = getattr(getattr(event, "bot", None), "api", None)
    token = getattr(getattr(api, "_http", None), "_token", None)
    app_id = str(getattr(token, "app_id", "") or "")
    return hashlib.sha256(f"{platform_key(event)}:{app_id}".encode()).hexdigest()[:24]


def identity_key(event: Any) -> str:
    """Portable collection identity for the same QQ app on another server."""
    api = getattr(getattr(event, "bot", None), "api", None)
    token = getattr(getattr(api, "_http", None), "_token", None)
    app_id = str(getattr(token, "app_id", "") or "")
    return hashlib.sha256(f"qq-app:{app_id}".encode()).hexdigest()[:24] if app_id else bot_key(event)


def private_scope(event: Any) -> str:
    if not is_official(event):
        return ""
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    author = getattr(raw, "author", None)
    user = str(getattr(author, "user_openid", "") or "")
    if not user or getattr(raw, "group_openid", "") or getattr(raw, "channel_id", ""):
        return ""
    digest = hashlib.sha256(f"{bot_key(event)}:{user}".encode()).hexdigest()[:32]
    return f"qqdm_{digest}"


def escape(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False).replace("\r", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"([\\`*_{}\[\]()#!~])", r"\\\1", text)
    return re.sub(r"(?m)^(\s*)([-+]|\d+\.)\s", r"\1\\\2 ", text)


@dataclass(frozen=True)
class Action:
    label: str
    command: str
    primary: bool = False
    owner_only: bool = False


@dataclass(frozen=True)
class Presentation:
    heading: str = "星之卡比图鉴"
    intro: str = ""
    summary: str = ""
    actions: tuple[Action, ...] = ()
    replace_caption: bool = False


def character_actions(
    entry: dict[str, Any], *, wiki: bool = True
) -> tuple[Action, ...]:
    number = int(entry.get("id") or entry.get("catalog_id") or 0)
    actions = [
        Action("角色素材", f"素材 #{number}"),
        Action("查看简介", f"查看简介 #{number}"),
    ]
    # Wiki numbers can be overridden independently of catalogue numbers.
    # Use the actual page title to keep the button bound to this character.
    query = str(
        entry.get("page_title") or entry.get("name_en") or entry.get("name") or ""
    ).strip()
    query = " ".join(query.replace("\x00", "").split())[:250]
    if wiki and query:
        actions.append(Action("卡比百科", f"卡比百科 {query}"))
    return tuple(actions)


def draw_presentation(
    outcome: Any, nickname: str, name: str, *, is_bot: bool, limit: int, wiki: bool
) -> Presentation:
    repeated = outcome.repeated or outcome.existing_today or outcome.all_unlocked
    status = "重复" if repeated else "NEW"
    subject = "我今天的盟友是" if is_bot else f"{nickname}，你今天的盟友是"
    summary = f"**今日剩余** {outcome.remaining}/{limit} 次"
    if outcome.collected is not None and outcome.total:
        count = min(outcome.total, max(0, outcome.collected))
        summary += f"　·　**收藏进度** {count:,} / {outcome.total:,}"
    actions = []
    if outcome.remaining > 0:
        actions.append(
            Action(
                "Bot再抽一次" if is_bot else "再抽一次",
                "Bot今日盟友" if is_bot else "今日盟友",
                True,
                True,
            )
        )
    actions.append(
        Action(
            "Bot的图鉴" if is_bot else "我的图鉴",
            "Bot盟友图鉴" if is_bot else "我的盟友图鉴",
            owner_only=True,
        )
    )
    actions.extend(character_actions(outcome.entry, wiki=wiki))
    return Presentation(
        heading="",
        intro=f"{escape(subject)} **{escape(name)}**（{status}）",
        summary=summary,
        actions=tuple(actions),
        replace_caption=True,
    )


def keyboard(
    actions: tuple[Action, ...], owner: str, template_id: str = ""
) -> dict | None:
    if template_id:
        return {"id": template_id}
    if not actions:
        return None
    # Two main actions, followed by character-specific reading actions.
    groups = [actions[:2], actions[2:5], actions[5:8]]
    rows = []
    for group in groups:
        buttons = []
        for item in group:
            permission = (
                {"type": 0, "specify_user_ids": [owner]}
                if item.owner_only and owner
                else {"type": 2}
            )
            buttons.append(
                {
                    "id": f"kirby_{len(rows)}_{len(buttons)}",
                    "render_data": {
                        "label": item.label[:10],
                        "visited_label": item.label[:10],
                        "style": int(item.primary),
                    },
                    "action": {
                        "type": 2,
                        "permission": permission,
                        "data": item.command,
                        "enter": True,
                        "reply": False,
                        "unsupport_tips": f"请发送：{item.command}",
                    },
                }
            )
        if buttons:
            rows.append({"buttons": buttons})
    return {"content": {"rows": rows}}


def fallback_commands(actions: tuple[Action, ...]) -> str:
    return "\n".join(f"{item.label}：{item.command}" for item in actions)


def flatten(components: list[Any], depth: int = 0) -> list[Any] | None:
    if depth > 6:
        return None
    output = []
    for component in components:
        if isinstance(component, (Comp.Plain, Comp.Image)):
            output.append(component)
        elif isinstance(component, Comp.Node):
            nested = flatten(list(component.content), depth + 1)
            if nested is None:
                return None
            output.extend(nested)
        elif isinstance(component, Comp.Nodes):
            nested = flatten(list(component.nodes), depth + 1)
            if nested is None:
                return None
            output.extend(nested)
        elif isinstance(component, (Comp.At, Comp.AtAll, Comp.Reply)):
            continue
        elif isinstance(component, Comp.File):
            output.append(
                Comp.Plain(
                    "QQ 官方 Bot 暂不支持发送本地 HTML 附件。请改用“卡比百科文本”、"
                    "“卡比F文本”或“卡比真格文本”加相应页面名阅读资料。"
                )
            )
        else:
            return None
    return output


def pages_for(components: list[Any]) -> list[list[Any]]:
    """Keep all text and images; QQ receives one readable page per request."""
    pages: list[list[Any]] = [[]]
    chars = 0
    images = 0
    if (
        sum(len(str(item.text)) for item in components if isinstance(item, Comp.Plain))
        > MAX_SESSION_CHARS
    ):
        raise ValueError("QQ reading response exceeds the bounded page cache")
    for component in components:
        if isinstance(component, Comp.Image):
            if images >= 1:
                pages.append([])
                chars = images = 0
            pages[-1].append(component)
            images += 1
            continue
        text = str(component.text)
        if len(text) > MAX_SESSION_CHARS:
            raise ValueError("QQ reading response exceeds the bounded page cache")
        while text:
            available = MAX_PAGE_CHARS - chars
            if available <= 0:
                pages.append([])
                chars = images = 0
                available = MAX_PAGE_CHARS
            cut = min(len(text), available)
            if len(text) > cut:
                boundary = max(text.rfind("\n", 0, cut), text.rfind("。", 0, cut))
                if boundary >= cut // 2:
                    cut = boundary + 1
            pages[-1].append(Comp.Plain(text[:cut]))
            chars += cut
            text = text[cut:]
    return [page for page in pages if page]


@dataclass
class ReadingSession:
    owner: str
    scope: str
    pages: list[list[Any]]
    view: Presentation
    expires: float


def _response_id(response: Any) -> str:
    return (
        str(response.get("id") or "")
        if isinstance(response, dict)
        else str(getattr(response, "id", "") or "")
    )


class APIError(RuntimeError):
    """Keep status/code without logging remote payloads or signed URLs."""

    def __init__(self, status: int, code: int = 0):
        self.status, self.code = status, code
        super().__init__(f"QQ API status={status} code={code}")


async def _request(api: Any, route: Any, **kwargs: Any) -> Any:
    # Reuse AstrBot's authenticated botpy session. botpy 1.x loses HTTP 400
    # status/code and silently retries some POSTs; neither is safe for fallback.
    http = api._http
    await http.check_session()
    route.is_sandbox = http.is_sandbox
    async with http._session.request(
        route.method,
        route.url,
        headers=dict(http._headers),
        timeout=aiohttp.ClientTimeout(total=30),
        allow_redirects=False,
        **kwargs,
    ) as response:
        try:
            data = (
                await response.json(content_type=None) if response.status != 204 else {}
            )
        except ValueError:
            if 200 <= response.status < 300:
                raise RuntimeError("QQ API returned an invalid receipt") from None
            data = {}
        if not 200 <= response.status < 300:
            code = data.get("code", 0) if isinstance(data, dict) else 0
            raise APIError(response.status, int(code) if str(code).isdigit() else 0)
        return data


def _rejected(exc: Exception) -> bool:
    status = getattr(
        exc, "status", getattr(getattr(exc, "response", None), "status", 0)
    )
    if status in {400, 401, 403, 404, 405, 422}:
        return True
    # Some legacy SDK attachment paths still raise these concrete exceptions.
    return type(exc).__module__ == "botpy.errors" and type(exc).__name__ in {
        "AuthenticationFailedError",
        "ForbiddenError",
        "NotFoundError",
        "MethodNotAllowedError",
    }


class QQOfficialDelivery:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.readings: dict[str, ReadingSession] = {}
        self._upload_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(4)
        self._active_owned: set[tuple[str, str]] = set()
        self._owned_path = self.root / "qq_official_media.json"

    @staticmethod
    def _owner(event: Any) -> str:
        return str(event.get_sender_id())

    @staticmethod
    def _scope(event: Any) -> str:
        return f"{platform_key(event)}:{getattr(event, 'unified_msg_origin', '')}"

    def cache_pages(
        self, event: Any, pages: list[list[Any]], view: Presentation
    ) -> str:
        now = time.monotonic()
        self.readings = {
            key: value for key, value in self.readings.items() if value.expires > now
        }
        if len(self.readings) >= MAX_PAGE_SESSIONS:
            oldest = min(self.readings, key=lambda key: self.readings[key].expires)
            del self.readings[oldest]
        token = secrets.token_hex(8)
        self.readings[token] = ReadingSession(
            self._owner(event), self._scope(event), pages, view, now + PAGE_TTL
        )
        return token

    async def _snapshot_pages(self, pages: list[list[Any]]) -> list[list[Any]]:
        # Gallery output names can be reused after a new draw. Preserve the
        # exact images shown by this reading instead of following mutable files.
        copied: dict[str, Any] = {}
        total_bytes = 0
        output = []
        for page in pages:
            snapshot = []
            for item in page:
                if isinstance(item, Comp.Image):
                    source = str(await item.convert_to_file_path())
                    if source not in copied:
                        total_bytes += (
                            await asyncio.to_thread(Path(source).stat)
                        ).st_size
                        if len(copied) >= 256 or total_bytes > 128 * 1024 * 1024:
                            raise ValueError("这份资料的图片过多，请缩小查询范围。")
                        destination = (
                            self.root
                            / "media_cache"
                            / f"kirby-qq-page-{secrets.token_hex(16)}{Path(source).suffix}"
                        )
                        await asyncio.to_thread(
                            destination.parent.mkdir, parents=True, exist_ok=True
                        )
                        await asyncio.to_thread(shutil.copyfile, source, destination)
                        copied[source] = Comp.Image.fromFileSystem(str(destination))
                    item = copied[source]
                snapshot.append(item)
            output.append(snapshot)
        return output

    def resolve_page(self, event: Any, token: str, page: int) -> ReadingSession:
        session = self.readings.get(token)
        if session is None or session.expires <= time.monotonic():
            self.readings.pop(token, None)
            raise ValueError("这份阅读内容已过期，请重新执行原来的查询或图鉴命令。")
        if session.owner != self._owner(event) or session.scope != self._scope(event):
            raise ValueError("请使用你自己消息下方的翻页按钮。")
        if not 1 <= page <= len(session.pages):
            raise ValueError("没有这一页，请使用消息下方的翻页按钮。")
        return session

    @staticmethod
    def _page_actions(token: str, page: int, total: int) -> tuple[Action, ...]:
        output = []
        if page > 1:
            output.append(
                Action("上一页", f"卡比翻页 {token} {page - 1}", owner_only=True)
            )
        if page < total:
            output.append(Action("下一页", f"卡比翻页 {token} {page + 1}", True, True))
        return tuple(output)

    @staticmethod
    def _owned_id(value: Any) -> str:
        value = str(value or "")
        return value if re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value) else ""

    def _load_owned(self) -> dict:
        try:
            document = json.loads(self._owned_path.read_text(encoding="utf-8"))
            return document if isinstance(document, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_owned(self, document: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self._owned_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._owned_path)

    async def _delete_owned(
        self, api: Any, key: str, ids: list[str], *, newly_uploaded: bool = False
    ) -> None:
        async with self._upload_lock:
            await self._delete_locked(api, key, ids, newly_uploaded=newly_uploaded)

    async def _delete_locked(
        self, api: Any, key: str, ids: list[str], *, newly_uploaded: bool = False
    ) -> None:
        document = await asyncio.to_thread(self._load_owned)
        recorded = document.get(key, [])
        recorded = (
            [value for value in recorded if self._owned_id(value)]
            if isinstance(recorded, list)
            else []
        )
        for image_id in ids:
            if not self._owned_id(image_id) or (
                image_id not in recorded and not newly_uploaded
            ):
                continue
            if not newly_uploaded and (key, image_id) in self._active_owned:
                continue
            try:
                await _request(
                    api,
                    _route(
                        "DELETE",
                        "/v2/media-images/{image_id}/delete",
                        image_id=image_id,
                    ),
                )
            except (RuntimeError, aiohttp.ClientError, OSError, ValueError) as exc:
                # Deleting an already missing/expired plugin image is complete.
                if getattr(exc, "code", 0) not in {40035008, 40035009}:
                    logger.warning(
                        "[kirby_catalog] QQ 临时图片清理待重试: %s", type(exc).__name__
                    )
                    if image_id not in recorded:
                        recorded.append(image_id)
                    continue
            if image_id in recorded:
                recorded.remove(image_id)
        document[key] = recorded
        await asyncio.to_thread(self._save_owned, document)

    async def _cleanup(
        self, api: Any, key: str, ids: list[str], *, newly_uploaded: bool = False
    ) -> None:
        try:
            await self._delete_owned(api, key, ids, newly_uploaded=newly_uploaded)
        except (RuntimeError, aiohttp.ClientError, OSError, ValueError) as exc:
            # Cleanup must never mask an already delivered message receipt.
            logger.warning(
                "[kirby_catalog] QQ 临时图片清理记录暂不可用: %s", type(exc).__name__
            )

    @staticmethod
    def _image_data(path: str) -> tuple[bytes, str, str, int, int]:
        source = Path(path)
        if source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("QQ Markdown images must be at most 10 MiB")
        data = source.read_bytes()
        with Image.open(source) as image:
            kind, width, height = image.format, image.width, image.height
            image.verify()
        formats = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "GIF": ("gif", "image/gif"),
            "WEBP": ("webp", "image/webp"),
        }
        if kind not in formats:
            raise ValueError("Unsupported QQ Markdown image format")
        extension, mime = formats[kind]
        return data, extension, mime, width, height

    async def _upload(
        self, event: Any, image: Any, owned: list[str]
    ) -> tuple[str, int, int]:
        api = event.bot.api
        path = await image.convert_to_file_path()
        data, extension, mime, width, height = await asyncio.to_thread(
            self._image_data, path
        )
        md5 = hashlib.md5(data).hexdigest()
        prepared = await _request(
            api,
            _route("POST", "/v2/media-images/prepare"),
            json={
                "file_name": f"kirby-catalog-{md5}.{extension}",
                "file_size": len(data),
                "content_type": mime,
                "md5": md5,
                "sha1": hashlib.sha1(data).hexdigest(),
            },
        )
        parsed = urlsplit(str(prepared["presigned_url"]))
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".myqcloud.com")
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Invalid platform upload URL")
        # COS receives no QQ Authorization header, cookies or application secret.
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as http,
            http.put(
                parsed.geturl(),
                data=data,
                headers={"Content-Type": mime},
                allow_redirects=False,
            ) as response,
        ):
            response.raise_for_status()
            etag = response.headers.get("ETag", "").strip('"').lower()
            if etag != md5:
                raise ValueError("Platform upload ETag does not match the image")
        completed = await _request(
            api,
            _route("POST", "/v2/media-images/complete"),
            json={"upload_id": prepared["upload_id"], "md5": etag},
        )
        image_id = self._owned_id(completed.get("image_id"))
        if not image_id:
            raise ValueError("Missing platform image identity")
        owned.append(image_id)
        key = bot_key(event)
        self._active_owned.add((key, image_id))
        async with self._upload_lock:
            document = await asyncio.to_thread(self._load_owned)
            recorded = document.get(key, [])
            document[key] = list(
                dict.fromkeys(
                    [*(recorded if isinstance(recorded, list) else []), image_id]
                )
            )
            await asyncio.to_thread(self._save_owned, document)
        url = str(completed.get("url") or "")
        if not re.fullmatch(r"qqbot-media://image/v1/[^\s()<>]{1,1500}", url):
            raise ValueError("Invalid platform image reference")
        return url, width, height

    @staticmethod
    async def _post(event: Any, payload: dict) -> Any:
        raw = event.message_obj.raw_message
        api = event.bot.api
        payload = dict(payload, msg_id=str(event.message_obj.message_id))
        group = str(getattr(raw, "group_openid", "") or "")
        user = str(getattr(getattr(raw, "author", None), "user_openid", "") or "")
        channel = str(getattr(raw, "channel_id", "") or "")
        if group or (user and not channel):
            payload["msg_seq"] = secrets.randbelow(10000) + 1
        else:
            payload.pop("msg_type", None)
        if group:
            route = _route(
                "POST", "/v2/groups/{group_openid}/messages", group_openid=group
            )
        elif user and not channel:
            route = _route("POST", "/v2/users/{openid}/messages", openid=user)
        elif getattr(raw, "guild_id", "") and type(raw).__name__.endswith(
            "DirectMessage"
        ):
            route = _route("POST", "/dms/{guild_id}/messages", guild_id=raw.guild_id)
        elif channel:
            route = _route(
                "POST", "/channels/{channel_id}/messages", channel_id=channel
            )
        else:
            raise ValueError("Unsupported QQ message destination")
        response = await _request(api, route, json=payload)
        if not response:
            raise RuntimeError("QQ message API returned no receipt")
        event._has_send_oper = True
        return response

    @staticmethod
    def _markdown(
        view: Presentation,
        components: list[Any],
        images: dict[int, tuple[str, int, int]],
        page: int,
        total: int,
    ) -> str:
        lines = [f"## {escape(view.heading)}"] if view.heading else []
        if view.intro:
            lines.append(view.intro)
        if total > 1:
            lines.append(f"**第 {page} / {total} 页**")
        for index, component in enumerate(components):
            if isinstance(component, Comp.Plain):
                if not view.replace_caption:
                    lines.append(escape(component.text).replace("\n", "  \n"))
            elif index in images:
                url, width, height = images[index]
                printed_width = min(width, 480)
                printed_height = max(1, round(height * printed_width / width))
                lines.append(
                    f"![盟友卡牌 #{printed_width}px #{printed_height}px]({url})"
                )
        if view.summary:
            lines.extend(["***", view.summary])
        return "\n\n".join(lines)

    async def _attachment(self, event: Any, image: Any, text: str) -> str:
        raw = event.message_obj.raw_message
        group = str(getattr(raw, "group_openid", "") or "")
        user = str(getattr(getattr(raw, "author", None), "user_openid", "") or "")
        if group or (user and not getattr(raw, "channel_id", "")):
            routing = {"group_openid": group} if group else {"openid": user}
            media = await event.upload_group_and_c2c_image(
                await image.convert_to_base64(), 1, **routing
            )
            response = await self._post(
                event, {"msg_type": 7, "content": text or None, "media": media}
            )
        else:
            path = await image.convert_to_file_path()
            # SDK handles channel multipart binary uploads (not JSON paths).
            kwargs = {
                "content": text or None,
                "file_image": path,
                "msg_id": str(event.message_obj.message_id),
            }
            if getattr(raw, "guild_id", "") and type(raw).__name__.endswith(
                "DirectMessage"
            ):
                response = await event.bot.api.post_dms(guild_id=raw.guild_id, **kwargs)
            else:
                response = await event.bot.api.post_message(
                    channel_id=raw.channel_id, **kwargs
                )
            if not response:
                raise RuntimeError("QQ image API returned no receipt")
            event._has_send_oper = True
        return _response_id(response)

    async def send(
        self,
        event: Any,
        components: list[Any],
        view: Presentation | None = None,
        *,
        markdown_mode: str = "原生 Markdown",
        markdown_template: str = "",
        template_key: str = "content",
        buttons_enabled: bool = True,
        keyboard_template: str = "",
        page: int = 1,
        token: str = "",
    ) -> list[str] | None:
        if not is_official(event) or not callable(
            getattr(
                getattr(getattr(event, "bot", None), "api", None),
                "post_group_message",
                None,
            )
        ):
            return None
        flattened = flatten(components)
        if flattened is None:
            return None
        view = view or Presentation(
            actions=(
                Action("今日盟友", "今日盟友", True),
                Action("我的图鉴", "我的盟友图鉴"),
                Action("使用帮助", "盟友帮助"),
            )
        )
        if token:
            reading = self.resolve_page(event, token, page)
            pages, view = reading.pages, reading.view
        else:
            pages = pages_for(flattened)
            if not pages:
                return None
            if len(pages) > 1:
                pages = await self._snapshot_pages(pages)
                token = self.cache_pages(event, pages, view)
        current = pages[page - 1]
        page_actions = self._page_actions(token, page, len(pages)) if token else ()
        actions = (*page_actions, *view.actions)
        keys = (
            keyboard(actions, self._owner(event), keyboard_template)
            if buttons_enabled
            else None
        )
        receipts = []
        # Bound in-flight uploads without making unrelated users wait for all
        # message sends. Only the ownership registry is serialized.
        async with self._slots:
            api, key = event.bot.api, bot_key(event)
            if callable(getattr(getattr(api, "_http", None), "request", None)):
                recorded = (await asyncio.to_thread(self._load_owned)).get(key, [])
                if isinstance(recorded, list) and recorded:
                    await self._cleanup(api, key, recorded[:1])
            owned: list[str] = []
            references = {}
            attachment = False
            try:
                attached = set()
                for index, component in enumerate(current):
                    if isinstance(component, Comp.Image):
                        try:
                            references[index] = await self._upload(
                                event, component, owned
                            )
                        except (
                            RuntimeError,
                            aiohttp.ClientError,
                            OSError,
                            ValueError,
                            KeyError,
                            TypeError,
                        ) as exc:
                            logger.warning(
                                "[kirby_catalog] QQ Markdown 图片上传不可用，改用附件: %s",
                                type(exc).__name__,
                            )
                            attachment = True
                body = self._markdown(view, current, references, page, len(pages))
                commands = fallback_commands(actions)
                raw_text = "\n".join(
                    str(item.text) for item in current if isinstance(item, Comp.Plain)
                )
                if attachment:
                    for index, component in enumerate(current):
                        if (
                            isinstance(component, Comp.Image)
                            and index not in references
                        ):
                            receipts.append(
                                await self._attachment(event, component, "")
                            )
                            attached.add(index)
                if (not buttons_enabled or keyboard_template) and commands:
                    body += "\n\n" + escape(commands).replace("\n", "  \n")
                if markdown_mode == "模板 Markdown" and markdown_template:
                    md = {
                        "custom_template_id": markdown_template,
                        "params": [{"key": template_key, "values": [body]}],
                    }
                else:
                    md = {"content": body}
                payload = {"msg_type": 2, "markdown": md}
                if keys:
                    payload["keyboard"] = keys
                try:
                    receipts.append(_response_id(await self._post(event, payload)))
                except (RuntimeError, aiohttp.ClientError, OSError) as exc:
                    if not _rejected(exc):
                        # An ambiguous network error must not send the saved
                        # draw again. The caller can report the send failure.
                        raise
                    if keys:
                        payload.pop("keyboard", None)
                        if not keyboard_template:
                            body += "\n\n" + escape(commands).replace("\n", "  \n")
                        if "content" in md:
                            md["content"] = body
                        else:
                            md["params"][0]["values"] = [body]
                        try:
                            receipts.append(
                                _response_id(await self._post(event, payload))
                            )
                            return receipts
                        except (
                            RuntimeError,
                            aiohttp.ClientError,
                            OSError,
                        ) as retry_error:
                            if not _rejected(retry_error):
                                raise
                    # Only a definite API rejection permits the plain fallback.
                    for index, component in enumerate(current):
                        if isinstance(component, Comp.Image) and index not in attached:
                            receipts.append(
                                await self._attachment(event, component, "")
                            )
                    plain = (
                        raw_text
                        if not view.replace_caption
                        else re.sub(r"[*\\]", "", view.intro + "\n" + view.summary)
                    )
                    if len(pages) > 1:
                        plain = f"第 {page}/{len(pages)} 页\n" + plain
                    if commands:
                        plain += "\n" + commands
                    receipts.append(
                        _response_id(
                            await self._post(
                                event, {"msg_type": 0, "content": plain or view.heading}
                            )
                        )
                    )
                return receipts
            finally:
                if owned:
                    try:
                        await self._cleanup(api, key, owned, newly_uploaded=True)
                    finally:
                        self._active_owned.difference_update(
                            (key, image_id) for image_id in owned
                        )
