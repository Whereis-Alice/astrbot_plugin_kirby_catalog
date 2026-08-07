from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star import StarTools
from astrbot.core.star.filter.event_message_type import EventMessageType

from .catalog_core import (
    CatalogStore,
    extract_image_bytes_from_value,
    get_today,
    plain_text_from_component,
)
from .kirby_fandom import (
    DEFAULT_FANDOM_API_URL,
    KirbyFandomClient,
    KirbyFandomError,
)
from .media_delivery import (
    cleanup_staged_media_if_due,
    image_limit_reasons,
    inspect_image,
    local_path_from_image_file,
    normalise_jpeg,
    prepare_image_for_delivery,
    stage_local_image,
)
from .wikirby import DEFAULT_API_URL, WikirbyClient, WikirbyError
from .wikirby_card import (
    DEFAULT_CARD_TEMPLATE,
    WIKIRBY_CARD_TEMPLATE,
    build_card_layout,
    build_card_pages,
    resolve_card_template,
)
from .webui import KirbyCatalogWebUI

PLUGIN_ID = "astrbot_plugin_kirby_catalog"
LEGACY_PLUGIN_ID = "astrbot_plugin_AnimeWife"
IMAGE_BASE_URL = "http://save.my996.top/?/img/"
DEFAULT_DRAW_MESSAGE_TEMPLATE = (
    "{nickname}???????? {name}{flags}????? #{id}{source_text}?\n"
    "???????{remaining}"
)
DEFAULT_RANDOM_MESSAGE_TEMPLATE = (
    "???????? {name}????? #{id}{source_text}?"
)
DEFAULT_QUERY_MESSAGE_TEMPLATE = (
    "{nickname} ?????? {name}????? #{id}{source_text}{unlock_text}?"
)
DEFAULT_ALLY_DETAIL_TEMPLATE = "{base}{description_block}{wiki_hint_block}"
DEFAULT_ALLY_WIKI_HINT = "??????????????????????????????"
DEFAULT_ALLY_DESCRIPTION_MAX_CHARS = 600
DESCRIPTION_TRUNCATION_SUFFIX = "... ..."
DEFAULT_FORWARD_NODE_MAX_CHARS = 3000
DEFAULT_FORWARD_MAX_NODES = 20
DEFAULT_FORWARD_MAX_IMAGES = 2
DEFAULT_FORWARD_RETRY_COUNT = 1
DEFAULT_FORWARD_RETRY_DELAY_SECONDS = 0.5
DEFAULT_FORWARD_BATCH_DELAY_SECONDS = 0.2
DEFAULT_WIKI_CARD_PAGE_LINE_BUDGET = 110
MAX_WIKI_CARD_PAGE_LINE_BUDGET = 3000
DEFAULT_WIKI_CARD_MAX_WIDTH_PX = 2160
DEFAULT_WIKI_CARD_MAX_HEIGHT_PX = 8000
DEFAULT_WIKI_CARD_MAX_MEGAPIXELS = 18.0
DEFAULT_WIKI_CARD_MAX_BYTES_MB = 8.0
DEFAULT_WIKI_CARD_JPEG_QUALITY = 92
DEFAULT_ALLY_MEDIA_MAX_WIDTH_PX = 2160
DEFAULT_ALLY_MEDIA_MAX_HEIGHT_PX = 8000
DEFAULT_ALLY_MEDIA_MAX_MEGAPIXELS = 18.0
DEFAULT_ALLY_MEDIA_MAX_BYTES_MB = 8.0
DEFAULT_ALLY_MEDIA_JPEG_QUALITY = 92
DEFAULT_MEDIA_CLEANUP_INTERVAL_MINUTES = 5.0
DEFAULT_GALLERY_MAX_HEIGHT_PX = 7600
DEFAULT_BOT_DRAW_MESSAGE_TEMPLATE = (
    "{nickname}?????? {name}{flags}????? #{id}{source_text}?\n"
    "???????{remaining}"
)
MAX_WIKI_TRANSLATION_CACHE_ITEMS = 128
CATALOG_PROFILES_PATH = Path(__file__).parent / "resources" / "catalog_profiles.json"


@dataclass(frozen=True)
class AllyDrawOutcome:
    entry: Dict[str, Any]
    remaining: int
    repeated: bool
    pity: bool
    existing_today: bool = False


@register(
    PLUGIN_ID,
    "Whereis-Alice",
    "?????????????????????",
    "3.5.4",
    "https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog",
)
class KirbyCatalogPlugin(Star):
    """??????????????"""

    def __init__(self, context: Context, config: Optional[Any] = None):
        super().__init__(context)
        self.config = config or {}
        data_dir = Path(StarTools.get_data_dir(PLUGIN_ID))
        legacy_dirs = self._legacy_data_dirs()
        self.store = CatalogStore(
            data_dir,
            legacy_dirs=legacy_dirs,
            image_base_url=self._config_value("image_base_url", IMAGE_BASE_URL),
            profiles_path=CATALOG_PROFILES_PATH,
        )
        self._draw_lock = asyncio.Lock()
        self.webui: Optional[KirbyCatalogWebUI] = None
        try:
            self.webui = KirbyCatalogWebUI(
                self.context,
                self.store,
                self._draw_lock,
            )
            self.webui.register()
        except Exception as exc:
            self.webui = None
            logger.exception(
                "[%s] WebUI ????????????????: %s", PLUGIN_ID, exc
            )
        self._cooldowns: Dict[str, Dict[str, float]] = {}
        self._guess_sessions: Dict[str, Dict[str, Any]] = {}
        self._guess_timeout_tasks: Dict[str, asyncio.Task[None]] = {}
        self._wiki_translation_cache: Dict[Tuple[str, str, str], Tuple[float, str]] = {}
        self.wikirby = WikirbyClient(
            api_url=str(self._config_value("wikirby_api_url", DEFAULT_API_URL)),
            timeout_seconds=float(self._config_value("wikirby_timeout_seconds", 12)),
            cache_ttl_seconds=int(
                self._config_value("wikirby_cache_ttl_seconds", 3600)
            ),
            proxy_url=str(self._config_value("wikirby_proxy_url", "")),
            proxy_token=str(self._config_value("wikirby_proxy_token", "")),
        )
        self.fandom = KirbyFandomClient(
            api_url=str(self._config_value("fandom_api_url", DEFAULT_FANDOM_API_URL)),
            timeout_seconds=float(self._config_value("fandom_timeout_seconds", 15)),
            cache_ttl_seconds=int(self._config_value("fandom_cache_ttl_seconds", 3600)),
        )

    def _cancel_guess_timeout(self, group_id: str) -> None:
        task = self._guess_timeout_tasks.pop(group_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _schedule_guess_timeout(
        self,
        group_id: str,
        filename: str,
        started_at: float,
        timeout: int,
        umo: str,
    ) -> None:
        self._cancel_guess_timeout(group_id)
        self._guess_timeout_tasks[group_id] = asyncio.create_task(
            self._guess_timeout_worker(
                group_id,
                filename,
                started_at,
                timeout,
                umo,
            )
        )

    async def _guess_timeout_worker(
        self,
        group_id: str,
        filename: str,
        started_at: float,
        timeout: int,
        umo: str,
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(timeout)
            session = self._guess_sessions.get(group_id)
            if (
                not session
                or session.get("filename") != filename
                or session.get("started_at") != started_at
            ):
                return
            self._guess_sessions.pop(group_id, None)
            entry = self.store.resolve_entry(filename)
            if entry:
                text = (
                    f"???????????????? #{entry['id']} "
                    f"{self._display_name(entry)}?"
                )
            else:
                text = "?????????????????????"
            await self.context.send_message(umo, MessageChain([Comp.Plain(text)]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[%s] ?????????: %s", PLUGIN_ID, exc)
        finally:
            if self._guess_timeout_tasks.get(group_id) is current_task:
                self._guess_timeout_tasks.pop(group_id, None)

    async def terminate(self) -> None:
        tasks = list(self._guess_timeout_tasks.values())
        self._guess_timeout_tasks.clear()
        self._guess_sessions.clear()
        getattr(self, "_wiki_translation_cache", {}).clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        clients = [
            client
            for client in (
                getattr(self, "wikirby", None),
                getattr(self, "fandom", None),
            )
            if client is not None
        ]
        if clients:
            await asyncio.gather(
                *(client.close() for client in clients), return_exceptions=True
            )

    def _legacy_data_dirs(self) -> List[Path]:
        candidates = [Path("data") / "plugins" / LEGACY_PLUGIN_ID]
        configured = str(self._config_value("legacy_data_dir", "") or "").strip()
        if configured:
            candidates.insert(0, Path(configured))
        try:
            candidates.append(Path(StarTools.get_data_dir(LEGACY_PLUGIN_ID)))
        except Exception:
            pass
        result: List[Path] = []
        for path in candidates:
            path = path.resolve()
            if (
                path != Path(StarTools.get_data_dir(PLUGIN_ID)).resolve()
                and path not in result
            ):
                result.append(path)
        return result

    def _config_value(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            try:
                value = self.config[key]
            except (KeyError, TypeError):
                value = default
        if value == default and hasattr(self.config, "get"):
            for section_name in (
                "draw_settings",
                "delivery_settings",
                "data_settings",
                "wikirby_settings",
                "fandom_settings",
            ):
                section = self.config.get(section_name, {})
                if hasattr(section, "get") and key in section:
                    value = section[key]
                    break
        return default if value is None else value

    @staticmethod
    def _group_id(event: AstrMessageEvent) -> str:
        return str(getattr(getattr(event, "message_obj", None), "group_id", "") or "")

    @staticmethod
    def _sender_id(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id())
        except Exception:
            return ""

    @staticmethod
    def _sender_name(event: AstrMessageEvent) -> str:
        try:
            return event.get_sender_name() or "??"
        except Exception:
            return "??"

    @staticmethod
    def _command_remainder(event: AstrMessageEvent, names: Iterable[str]) -> str:
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        for name in sorted(names, key=len, reverse=True):
            if text == name:
                return ""
            if text.startswith(name) and text[len(name) : len(name) + 1].isspace():
                return text[len(name) :].strip()
        return ""

    @staticmethod
    def _at_target(event: AstrMessageEvent) -> Optional[str]:
        for component in (
            getattr(getattr(event, "message_obj", None), "message", []) or []
        ):
            if isinstance(component, Comp.At):
                return str(
                    getattr(component, "qq", "") or getattr(component, "user_id", "")
                )
        return None

    @staticmethod
    def _nested_components(value: Any, depth: int = 0) -> Iterable[Any]:
        if depth > 4 or value is None:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                yield from KirbyCatalogPlugin._nested_components(item, depth + 1)
            return
        yield value
        if isinstance(value, Comp.Reply):
            yield from KirbyCatalogPlugin._nested_components(value.chain, depth + 1)
        elif isinstance(value, dict):
            if value.get("type") in {"reply", "Reply"}:
                yield from KirbyCatalogPlugin._nested_components(
                    value.get("chain"), depth + 1
                )

    @classmethod
    def _quoted_text(cls, event: AstrMessageEvent) -> str:
        parts: List[str] = []
        seen: set[str] = set()

        def append_text(value: Any) -> None:
            text = plain_text_from_component(value)
            if isinstance(value, dict) and not text:
                data = value.get("data")
                if isinstance(data, dict):
                    text = str(
                        data.get("text") or data.get("message_str") or ""
                    ).strip()
            if text and text not in seen:
                seen.add(text)
                parts.append(text)

        def append_chain(value: Any) -> None:
            for nested in cls._nested_components(value):
                if isinstance(nested, Comp.Plain):
                    append_text(nested)
                elif isinstance(nested, dict) and str(
                    nested.get("type", "")
                ).casefold() in {"plain", "text"}:
                    append_text(nested)

        components = getattr(getattr(event, "message_obj", None), "message", []) or []
        for component in components:
            if isinstance(component, Comp.Reply):
                append_text(component)
                append_chain(component.chain)
            elif (
                isinstance(component, dict)
                and str(component.get("type", "")).casefold() == "reply"
            ):
                append_text(component)
                append_chain(component.get("chain") or component.get("message"))

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            reply = raw_message.get("reply")
            if isinstance(reply, dict):
                append_text(reply)
                append_chain(reply.get("message") or reply.get("chain"))
        return "\n".join(parts)

    @classmethod
    def _image_bytes_from_event(cls, event: AstrMessageEvent) -> Optional[bytes]:
        components = getattr(getattr(event, "message_obj", None), "message", []) or []
        for component in cls._nested_components(components):
            if isinstance(component, Comp.Image):
                for value in (
                    getattr(component, "path", ""),
                    getattr(component, "file", ""),
                    getattr(component, "url", ""),
                ):
                    data = extract_image_bytes_from_value(value)
                    if data:
                        return data
            elif (
                isinstance(component, dict)
                and str(component.get("type", "")).lower() == "image"
            ):
                data = extract_image_bytes_from_value(component)
                if data:
                    return data

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        raw_candidates = []
        if isinstance(raw_message, dict):
            raw_candidates.extend(
                [raw_message.get("message", []), raw_message.get("reply", {})]
            )
        for candidate in raw_candidates:
            segments = (
                candidate.get("message", [])
                if isinstance(candidate, dict)
                else candidate
            )
            for segment in segments or []:
                if (
                    isinstance(segment, dict)
                    and str(segment.get("type", "")).lower() == "image"
                ):
                    data = extract_image_bytes_from_value(segment.get("data", segment))
                    if data:
                        return data
        return None

    def _quoted_target(self, event: AstrMessageEvent) -> str:
        text = self._quoted_text(event)
        match = re.search(r"(?:#|??\s*[:?]?)\s*(\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"(?:??|??)\s*[:?]\s*([^\n]+)", text)
        return match.group(1).strip() if match else ""

    def _entry_or_error(
        self, target: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        matches = self.store.find_entries(target)
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, f"???????{target}???????????????"
        preview = "?".join(f"#{item['id']} {item['name']}" for item in matches[:8])
        return None, f"??????????????{preview}"

    @staticmethod
    def _user_data(
        config: Dict[str, Dict[str, Any]], user_id: str, nickname: str
    ) -> Dict[str, Any]:
        return config.setdefault(
            str(user_id),
            {
                "current": {"ally_filename": "", "date": ""},
                "unlocked": [],
                "nickname": nickname,
                "no_new_count": 0,
            },
        )

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _bounded_float(
        value: Any, default: float, minimum: float, maximum: float
    ) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _media_send_mode(self) -> str:
        normalized = str(
            self._config_value("media_send_mode", "??????") or ""
        ).strip().casefold()
        return {
            "??????": "auto",
            "??": "auto",
            "auto": "auto",
            "astrbot????": "standard",
            "????": "standard",
            "standard": "standard",
            "napcat??????": "direct",
            "??????": "direct",
            "direct": "direct",
        }.get(normalized, "auto")

    def _media_cache_dir(self) -> Path:
        root = getattr(getattr(self, "store", None), "root", None)
        if root is None:
            try:
                root = Path(StarTools.get_data_dir(PLUGIN_ID))
            except Exception:
                root = Path.cwd()
        path = Path(root) / "media_cache"
        path.mkdir(parents=True, exist_ok=True)
        retention_minutes = self._bounded_float(
            self._config_value("media_stage_retention_minutes", 30),
            30,
            1,
            1440,
        )
        cleanup_staged_media_if_due(
            path,
            retention_seconds=retention_minutes * 60,
            min_interval_seconds=self._media_cleanup_interval_seconds(),
        )
        return path

    def _media_delivery_limits(
        self, media_profile: str
    ) -> Tuple[int, int, float, int, int]:
        if media_profile == "wiki_card":
            prefix = "wiki_card"
            defaults = (
                DEFAULT_WIKI_CARD_MAX_WIDTH_PX,
                DEFAULT_WIKI_CARD_MAX_HEIGHT_PX,
                DEFAULT_WIKI_CARD_MAX_MEGAPIXELS,
                DEFAULT_WIKI_CARD_MAX_BYTES_MB,
                DEFAULT_WIKI_CARD_JPEG_QUALITY,
            )
        else:
            prefix = "ally_media"
            defaults = (
                DEFAULT_ALLY_MEDIA_MAX_WIDTH_PX,
                DEFAULT_ALLY_MEDIA_MAX_HEIGHT_PX,
                DEFAULT_ALLY_MEDIA_MAX_MEGAPIXELS,
                DEFAULT_ALLY_MEDIA_MAX_BYTES_MB,
                DEFAULT_ALLY_MEDIA_JPEG_QUALITY,
            )
        width, height, megapixels, bytes_mb, quality = defaults
        max_width = self._bounded_int(
            self._config_value(f"{prefix}_max_width_px", width), width, 0, 20000
        )
        max_height = self._bounded_int(
            self._config_value(f"{prefix}_max_height_px", height), height, 0, 50000
        )
        max_megapixels = self._bounded_float(
            self._config_value(f"{prefix}_max_megapixels", megapixels),
            megapixels,
            0,
            200,
        )
        max_bytes = int(
            self._bounded_float(
                self._config_value(f"{prefix}_max_bytes_mb", bytes_mb),
                bytes_mb,
                0,
                100,
            )
            * 1024
            * 1024
        )
        jpeg_quality = self._bounded_int(
            self._config_value(f"{prefix}_jpeg_quality", quality),
            quality,
            60,
            98,
        )
        return max_width, max_height, max_megapixels, max_bytes, jpeg_quality

    def _media_cleanup_interval_seconds(self) -> float:
        return (
            self._bounded_float(
                self._config_value(
                    "media_cleanup_interval_minutes",
                    DEFAULT_MEDIA_CLEANUP_INTERVAL_MINUTES,
                ),
                DEFAULT_MEDIA_CLEANUP_INTERVAL_MINUTES,
                0,
                1440,
            )
            * 60
        )

    async def _direct_image_value(
        self, component: Any, *, media_profile: str = "ally"
    ) -> str | None:
        file_value = str(getattr(component, "file", "") or "").strip()
        if file_value.startswith(("http://", "https://")):
            return file_value
        if file_value.startswith("base64://"):
            return None

        source = local_path_from_image_file(
            file_value,
            str(getattr(component, "path", "") or ""),
        )
        if source is None:
            return None

        metrics = await asyncio.to_thread(inspect_image, source)
        if metrics:
            logger.info(
                "[%s] ?????: file=%s, size=%dx%d, megapixels=%.2f, "
                "bytes=%d, format=%s",
                PLUGIN_ID,
                source.name,
                metrics.width,
                metrics.height,
                metrics.megapixels,
                metrics.byte_size,
                metrics.image_format,
            )

        shared_directory = str(
            self._config_value("media_shared_directory", "") or ""
        ).strip()
        if not shared_directory:
            return source.as_uri()

        napcat_directory = str(
            self._config_value("media_napcat_directory", "") or ""
        ).strip()
        normalize_enabled = self._bool_value(
            self._config_value("media_normalize_jpeg", True)
        ) and not source.name.startswith(("kirby-delivery-", "kirby-card-"))
        _, _, _, _, jpeg_quality = self._media_delivery_limits(media_profile)
        retention_minutes = self._bounded_float(
            self._config_value("media_stage_retention_minutes", 30),
            30,
            1,
            1440,
        )
        _, onebot_value = await asyncio.to_thread(
            stage_local_image,
            source,
            shared_directory,
            napcat_directory=napcat_directory,
            normalize_jpeg_enabled=normalize_enabled,
            jpeg_quality=jpeg_quality,
            retention_seconds=retention_minutes * 60,
            cleanup_interval_seconds=self._media_cleanup_interval_seconds(),
        )
        return onebot_value

    async def _prepare_media_components(
        self, components: List[Any], *, media_profile: str = "ally"
    ) -> List[Any]:
        cache_dir = self._media_cache_dir()
        max_width, max_height, max_megapixels, max_bytes, jpeg_quality = (
            self._media_delivery_limits(media_profile)
        )
        normalize_enabled = self._bool_value(
            self._config_value("media_normalize_jpeg", True)
        )
        async def prepare_component(component: Any) -> Any:
            if isinstance(component, Comp.Node):
                content = [
                    await prepare_component(item)
                    for item in list(getattr(component, "content", []) or [])
                ]
                return Comp.Node(
                    name=getattr(component, "name", "??????"),
                    uin=getattr(component, "uin", "0"),
                    content=content,
                )
            if isinstance(component, Comp.Nodes):
                nodes = [
                    await prepare_component(node)
                    for node in list(getattr(component, "nodes", []) or [])
                ]
                return Comp.Nodes(nodes=nodes)
            if not isinstance(component, Comp.Image):
                return component

            file_value = str(getattr(component, "file", "") or "")
            source = local_path_from_image_file(
                file_value,
                str(getattr(component, "path", "") or ""),
            )
            if source is None and file_value.startswith("base64://"):
                try:
                    source = Path(await component.convert_to_file_path())
                except Exception as exc:
                    logger.warning(
                        "[%s] Base64 ????????????: %s", PLUGIN_ID, exc
                    )
            if source is None:
                return component
            try:
                source_normalize = normalize_enabled and not source.name.startswith(
                    ("kirby-delivery-", "kirby-card-")
                )
                prepared = await asyncio.to_thread(
                    prepare_image_for_delivery,
                    source,
                    cache_dir,
                    max_width=max_width,
                    max_height=max_height,
                    max_megapixels=max_megapixels,
                    max_bytes=max_bytes,
                    normalize_jpeg_enabled=source_normalize,
                    jpeg_quality=jpeg_quality,
                )
                if prepared != source:
                    before = await asyncio.to_thread(inspect_image, source)
                    after = await asyncio.to_thread(inspect_image, prepared)
                    logger.info(
                        "[%s] ?????????: source=%s, before=%s, after=%s",
                        PLUGIN_ID,
                        source.name,
                        (
                            f"{before.width}x{before.height}/{before.byte_size}B"
                            if before
                            else "unknown"
                        ),
                        (
                            f"{after.width}x{after.height}/{after.byte_size}B"
                            if after
                            else "unknown"
                        ),
                    )
                return Comp.Image.fromFileSystem(str(prepared))
            except Exception as exc:
                logger.warning(
                    "[%s] ????????????????: %s", PLUGIN_ID, exc
                )
                return component

        return [await prepare_component(component) for component in components]

    async def _try_direct_media_send(
        self,
        event: AstrMessageEvent,
        components: List[Any],
        *,
        media_profile: str = "ally",
    ) -> bool:
        if self._media_send_mode() == "standard":
            return False
        umo = str(getattr(event, "unified_msg_origin", "") or "").casefold()
        if not umo.startswith("aiocqhttp:"):
            return False
        bot = getattr(event, "bot", None)
        if bot is None:
            return False

        segments: List[Dict[str, Any]] = []
        image_count = 0
        for component in components:
            if isinstance(component, Comp.Plain):
                if component.text:
                    segments.append(
                        {"type": "text", "data": {"text": component.text}}
                    )
                continue
            if isinstance(component, Comp.Image):
                image_value = await self._direct_image_value(
                    component, media_profile=media_profile
                )
                if not image_value:
                    return False
                segments.append(
                    {"type": "image", "data": {"file": image_value}}
                )
                image_count += 1
                continue
            return False
        if not segments or image_count == 0:
            return False

        group_id = self._group_id(event)
        user_id = self._sender_id(event)
        if group_id and group_id.isdigit():
            send = getattr(bot, "send_group_msg", None)
            routing = {"group_id": int(group_id), "message": segments}
        elif user_id and user_id.isdigit():
            send = getattr(bot, "send_private_msg", None)
            routing = {"user_id": int(user_id), "message": segments}
        else:
            return False
        if not callable(send):
            return False

        raw_event = getattr(getattr(event, "message_obj", None), "raw_message", None)
        try:
            self_id = raw_event.get("self_id") if hasattr(raw_event, "get") else None
        except Exception:
            self_id = None
        if self_id:
            routing["self_id"] = self_id

        retries = self._bounded_int(
            self._config_value("media_direct_retry_count", 1), 1, 0, 3
        )
        retry_delay = self._bounded_float(
            self._config_value("media_direct_retry_delay_seconds", 0.8),
            0.8,
            0,
            10,
        )
        for attempt in range(retries + 1):
            try:
                await send(**routing)
                logger.info(
                    "[%s] NapCat ????????: images=%d, attempt=%d",
                    PLUGIN_ID,
                    image_count,
                    attempt + 1,
                )
                return True
            except Exception as exc:
                if attempt >= retries:
                    logger.warning(
                        "[%s] NapCat ??????????? AstrBot ????: %s",
                        PLUGIN_ID,
                        exc,
                    )
                    return False
                logger.warning(
                    "[%s] NapCat ????????????? %d/%d: %s",
                    PLUGIN_ID,
                    attempt + 1,
                    retries,
                    exc,
                )
                if retry_delay:
                    await asyncio.sleep(retry_delay)
        return False

    def _forward_max_images(self) -> int:
        return self._bounded_int(
            self._config_value(
                "forward_max_images_per_message", DEFAULT_FORWARD_MAX_IMAGES
            ),
            DEFAULT_FORWARD_MAX_IMAGES,
            1,
            10,
        )

    def _forward_retry_count(self) -> int:
        return self._bounded_int(
            self._config_value("forward_retry_count", DEFAULT_FORWARD_RETRY_COUNT),
            DEFAULT_FORWARD_RETRY_COUNT,
            0,
            3,
        )

    def _forward_retry_delay(self) -> float:
        return self._bounded_float(
            self._config_value(
                "forward_retry_delay_seconds", DEFAULT_FORWARD_RETRY_DELAY_SECONDS
            ),
            DEFAULT_FORWARD_RETRY_DELAY_SECONDS,
            0,
            10,
        )

    def _forward_batch_delay(self) -> float:
        return self._bounded_float(
            self._config_value(
                "forward_batch_delay_seconds", DEFAULT_FORWARD_BATCH_DELAY_SECONDS
            ),
            DEFAULT_FORWARD_BATCH_DELAY_SECONDS,
            0,
            10,
        )

    async def _forward_component_segment(
        self, component: Any, *, media_profile: str = "ally"
    ) -> Dict[str, Any]:
        if isinstance(component, Comp.Plain):
            return {"type": "text", "data": {"text": component.text}}
        if isinstance(component, Comp.Image):
            if self._media_send_mode() != "standard":
                image_value = await self._direct_image_value(
                    component, media_profile=media_profile
                )
                if image_value:
                    return {"type": "image", "data": {"file": image_value}}
            encoded = await component.convert_to_base64()
            return {"type": "image", "data": {"file": f"base64://{encoded}"}}

        to_dict = getattr(component, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
            if asyncio.iscoroutine(value):
                value = await value
            if isinstance(value, dict):
                return value
        to_legacy_dict = getattr(component, "toDict", None)
        if callable(to_legacy_dict):
            value = to_legacy_dict()
            if isinstance(value, dict):
                return value
        raise TypeError(f"??????????: {type(component).__name__}")

    async def _forward_payload(
        self, nodes: List[Any], *, media_profile: str = "ally"
    ) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = []
        for node in nodes:
            content = [
                await self._forward_component_segment(
                    component, media_profile=media_profile
                )
                for component in list(getattr(node, "content", []) or [])
            ]
            messages.append(
                {
                    "type": "node",
                    "data": {
                        "user_id": str(getattr(node, "uin", "0") or "0"),
                        "nickname": str(
                            getattr(node, "name", "??????")
                            or "??????"
                        ),
                        "content": content,
                    },
                }
            )
        return {"messages": messages}

    async def _fallback_forward_node(
        self, event: AstrMessageEvent, node: Any, *, media_profile: str = "ally"
    ) -> bool:
        content = list(getattr(node, "content", []) or [])
        if not content:
            return True

        delivered = True
        for component in content:
            if isinstance(component, Comp.Image) and await self._try_direct_media_send(
                event, [component], media_profile=media_profile
            ):
                continue
            send = getattr(event, "send", None)
            if not callable(send):
                logger.error(
                    "[%s] ????????????????????", PLUGIN_ID
                )
                delivered = False
                continue
            try:
                await send(MessageChain([component]))
            except Exception as exc:
                delivered = False
                logger.exception(
                    "[%s] ???????????????: %s", PLUGIN_ID, exc
                )
        return delivered

    async def _deliver_forward_nodes(
        self,
        event: AstrMessageEvent,
        bot: Any,
        action: str,
        route_key: str,
        route_value: int,
        nodes: List[Any],
        *,
        media_profile: str = "ally",
    ) -> bool:
        attempts = self._forward_retry_count() + 1 if len(nodes) == 1 else 1
        retry_delay = self._forward_retry_delay()
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                payload = await self._forward_payload(
                    nodes, media_profile=media_profile
                )
                payload[route_key] = route_value
                await bot.call_action(action, **payload)
                logger.info(
                    "[%s] NapCat ????????: nodes=%d, attempt=%d",
                    PLUGIN_ID,
                    len(nodes),
                    attempt + 1,
                )
                return True
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    logger.warning(
                        "[%s] ?????????????? %d/%d: %s",
                        PLUGIN_ID,
                        attempt + 1,
                        attempts - 1,
                        exc,
                    )
                    if retry_delay:
                        await asyncio.sleep(retry_delay)

        if len(nodes) > 1:
            split_at = max(1, len(nodes) // 2)
            logger.warning(
                "[%s] ???????????????: nodes=%d -> %d+%d, error=%s",
                PLUGIN_ID,
                len(nodes),
                split_at,
                len(nodes) - split_at,
                last_error,
            )
            left_ok = await self._deliver_forward_nodes(
                event,
                bot,
                action,
                route_key,
                route_value,
                nodes[:split_at],
                media_profile=media_profile,
            )
            if self._forward_batch_delay():
                await asyncio.sleep(self._forward_batch_delay())
            right_ok = await self._deliver_forward_nodes(
                event,
                bot,
                action,
                route_key,
                route_value,
                nodes[split_at:],
                media_profile=media_profile,
            )
            return left_ok and right_ok

        logger.warning(
            "[%s] ?????????????????: %s", PLUGIN_ID, last_error
        )
        return await self._fallback_forward_node(
            event, nodes[0], media_profile=media_profile
        )

    async def _try_direct_forward_send(
        self,
        event: AstrMessageEvent,
        components: List[Any],
        *,
        media_profile: str = "ally",
    ) -> bool:
        if not self._bool_value(
            self._config_value("forward_direct_send_enabled", True)
        ):
            return False
        if not components or not all(
            isinstance(component, Comp.Nodes) for component in components
        ):
            return False
        umo = str(getattr(event, "unified_msg_origin", "") or "").casefold()
        if not umo.startswith("aiocqhttp:"):
            return False
        bot = getattr(event, "bot", None)
        if bot is None or not callable(getattr(bot, "call_action", None)):
            return False

        group_id = self._group_id(event)
        user_id = self._sender_id(event)
        if group_id and group_id.isdigit():
            action = "send_group_forward_msg"
            route_key = "group_id"
            route_value = int(group_id)
        elif user_id and user_id.isdigit():
            action = "send_private_forward_msg"
            route_key = "user_id"
            route_value = int(user_id)
        else:
            return False

        delivered = True
        batch_delay = self._forward_batch_delay()
        for index, component in enumerate(components):
            nodes = list(getattr(component, "nodes", []) or [])
            if nodes:
                delivered = (
                    await self._deliver_forward_nodes(
                        event,
                        bot,
                        action,
                        route_key,
                        route_value,
                        nodes,
                        media_profile=media_profile,
                    )
                    and delivered
                )
            if batch_delay and index + 1 < len(components):
                await asyncio.sleep(batch_delay)
        if not delivered:
            logger.error(
                "[%s] ?????????????????????", PLUGIN_ID
            )
        return True

    async def _chain_result_with_media(
        self,
        event: AstrMessageEvent,
        components: List[Any],
        *,
        media_profile: str = "ally",
        timing: Optional[Dict[str, Any]] = None,
        send_immediately: bool = False,
    ) -> Any | None:
        started_at = time.monotonic()
        components = await self._prepare_media_components(
            components, media_profile=media_profile
        )
        if timing is not None:
            timing["prepare_seconds"] = time.monotonic() - started_at

        send_started_at = time.monotonic()
        if await self._try_direct_forward_send(
            event, components, media_profile=media_profile
        ):
            if timing is not None:
                timing["send_seconds"] = time.monotonic() - send_started_at
                timing["delivery_mode"] = "direct_forward"
            return None
        if await self._try_direct_media_send(
            event, components, media_profile=media_profile
        ):
            if timing is not None:
                timing["send_seconds"] = time.monotonic() - send_started_at
                timing["delivery_mode"] = "direct_media"
            return None
        if timing is not None:
            timing["send_seconds"] = time.monotonic() - send_started_at
            timing["delivery_mode"] = "standard_queued"
        if send_immediately:
            send = getattr(event, "send", None)
            if callable(send):
                await send(MessageChain(components))
                if timing is not None:
                    timing["delivery_mode"] = "event_send"
                return None
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                raise RuntimeError("?????????????")
            await self.context.send_message(umo, MessageChain(components))
            if timing is not None:
                timing["delivery_mode"] = "context_send"
            return None
        return event.chain_result(components)

    async def _ally_chain(
        self, entry: Dict[str, Any], text: str, download: bool = True
    ) -> List[Any]:
        chain: List[Any] = [Comp.Plain(text)]
        asset_path = None
        asset_path_getter = getattr(self.store, "asset_path", None)
        if callable(asset_path_getter):
            asset_path = await asyncio.to_thread(asset_path_getter, entry)
        if asset_path is None and download:
            data = await asyncio.to_thread(self.store.asset_bytes, entry, True)
            if callable(asset_path_getter):
                asset_path = await asyncio.to_thread(asset_path_getter, entry)
            if asset_path is None and data:
                chain.append(Comp.Image.fromBytes(data))
                return chain
        if asset_path is not None:
            chain.append(Comp.Image.fromFileSystem(str(asset_path)))
        elif len(chain) == 1:
            chain[0] = Comp.Plain(f"{text}\n?????????????????")
        return chain

    @staticmethod
    def _truncate_ally_description(description: str, max_chars: int) -> str:
        description = str(description or "").strip()
        if max_chars <= 0 or len(description) <= max_chars:
            return description
        if max_chars <= len(DESCRIPTION_TRUNCATION_SUFFIX):
            return DESCRIPTION_TRUNCATION_SUFFIX[:max_chars]
        body = description[: max_chars - len(DESCRIPTION_TRUNCATION_SUFFIX)].rstrip()
        return f"{body}{DESCRIPTION_TRUNCATION_SUFFIX}"

    def _ally_description_text(
        self, entry: Dict[str, Any], *, respect_enabled: bool = True
    ) -> str:
        if respect_enabled and not self._bool_value(
            self._config_value("ally_description_enabled", True)
        ):
            return ""
        description = self.store.description_for(entry)
        try:
            max_chars = int(
                self._config_value(
                    "ally_description_max_chars", DEFAULT_ALLY_DESCRIPTION_MAX_CHARS
                )
            )
        except (TypeError, ValueError):
            max_chars = DEFAULT_ALLY_DESCRIPTION_MAX_CHARS
        return self._truncate_ally_description(description, max(0, max_chars))

    def _ally_detail_message(self, entry: Dict[str, Any], base: str) -> str:
        description = self._ally_description_text(entry)
        wiki_hint = ""
        if self._bool_value(self._config_value("ally_wiki_hint_enabled", True)):
            wiki_hint = str(
                self._config_value("ally_wiki_hint_text", DEFAULT_ALLY_WIKI_HINT) or ""
            ).strip()
        values = {
            "base": str(base or "").strip(),
            "description": description,
            "description_block": f"\n???\n{description}" if description else "",
            "wiki_hint": wiki_hint,
            "wiki_hint_block": f"\n{wiki_hint}" if wiki_hint else "",
        }
        template = str(
            self._config_value("ally_detail_template", DEFAULT_ALLY_DETAIL_TEMPLATE)
            or DEFAULT_ALLY_DETAIL_TEMPLATE
        )
        try:
            return template.format_map(values).strip()
        except (KeyError, ValueError) as exc:
            logger.warning("[%s] ????????????????: %s", PLUGIN_ID, exc)
            return DEFAULT_ALLY_DETAIL_TEMPLATE.format_map(values).strip()

    @staticmethod
    def _display_name(entry: Dict[str, Any]) -> str:
        return str(entry.get("name") or "?????")

    @staticmethod
    def _english_name_from_text(value: Any) -> str:
        text = str(value or "").strip()
        for match in re.finditer(r"[?(]([^??()\n]*[A-Za-z][^??()\n]*)[?)]", text):
            candidate = match.group(1).strip(" \t,???;?:")
            if candidate and not candidate.casefold().startswith(
                ("http://", "https://")
            ):
                return candidate
        return ""

    @staticmethod
    def _trim_quoted_name(value: Any) -> str:
        candidate = str(value or "").strip()
        candidate = re.split(
            r"\s*(?:?|,)?\s*(?:???|??????|????|??\s*[:?]|??????\s*[:?])",
            candidate,
            maxsplit=1,
        )[0]
        candidate = re.sub(r"^\s*#\s*\d+\s*", "", candidate)
        candidate = re.sub(
            r"\.(?:png|jpe?g|gif|bmp|webp)\s*$", "", candidate, flags=re.IGNORECASE
        )
        if "." in candidate:
            source, possible_name = candidate.split(".", 1)
            if possible_name.strip() and re.search(
                r"(?:????|??|Kirby)", source, re.IGNORECASE
            ):
                candidate = possible_name.strip()
        return candidate.strip(" \t\"'??????;")

    def _entry_wiki_query(self, entry: Dict[str, Any]) -> str:
        page_title = str(entry.get("page_title") or "").strip()
        if page_title:
            return page_title
        for value in (
            entry.get("variant_key"),
            self._display_name(entry),
            *entry.get("aliases", []),
        ):
            english = self._english_name_from_text(value)
            if english:
                return english
            candidate = self._trim_quoted_name(value)
            if re.search(r"[A-Za-z]", candidate) and not re.search(
                r"[\u3400-\u9fff]", candidate
            ):
                return candidate
        return self._display_name(entry)

    def _quoted_wiki_query(self, event: AstrMessageEvent) -> str:
        text = self._quoted_text(event)
        if not text:
            return ""

        id_match = re.search(r"(?:#|??\s*[:?]?)\s*(\d+)", text, re.IGNORECASE)
        if id_match:
            entry, _ = self._entry_or_error(id_match.group(1))
            if entry:
                return self._entry_wiki_query(entry)

        target = self._quoted_target(event)
        if target:
            entry, _ = self._entry_or_error(target)
            if entry:
                return self._entry_wiki_query(entry)

        candidates: List[str] = []
        patterns = (
            r"(?:WiKirby|Kirby Fandom)\s*[:?]\s*([^\n]+)",
            r"(?:??|??)\s*[:?]\s*([^\n]+)",
            r"(?:??????|????\s*[:?])\s*(?:#\s*\d+\s*)?([^\n]+)",
            r"#\s*\d+\s+([^\n]+)",
        )
        for pattern in patterns:
            candidates.extend(match.group(1) for match in re.finditer(pattern, text))
        candidates.extend(
            line
            for line in text.splitlines()
            if line.strip()
            and not re.match(r"^\s*(?:??|??|??|??????|??)\s*[:?]", line)
        )

        fallback = ""
        for value in candidates:
            candidate = self._trim_quoted_name(value)
            if not candidate:
                continue
            english = self._english_name_from_text(candidate)
            if english:
                return english
            if re.search(r"[A-Za-z]", candidate) and not re.search(
                r"[\u3400-\u9fff]", candidate
            ):
                return candidate
            if not fallback:
                fallback = candidate
        return fallback

    @staticmethod
    def _bool_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() not in {"0", "false", "no", "off"}

    def _wikirby_enabled(self) -> bool:
        return self._bool_value(self._config_value("wikirby_enabled", True))

    def _wikirby_translate_enabled(self) -> bool:
        return self._bool_value(self._config_value("wikirby_translate_enabled", False))

    def _fandom_enabled(self) -> bool:
        return self._bool_value(self._config_value("fandom_enabled", True))

    def _fandom_translate_enabled(self) -> bool:
        return self._bool_value(self._config_value("fandom_translate_enabled", False))

    @staticmethod
    def _wiki_output_mode(value: Any) -> str:
        normalized = str(value or "????").strip().casefold()
        return {
            "????": "text",
            "text": "text",
            "????": "forward",
            "forward": "forward",
            "?????": "card",
            "card": "card",
            "????+??": "card_and_text",
            "card_and_text": "card_and_text",
            "??+??????": "card_forward",
            "card_forward": "card_forward",
        }.get(normalized, "text")

    def _wikirby_output_mode(self) -> str:
        return self._wiki_output_mode(
            self._config_value("wikirby_output_mode", "????")
        )

    def _fandom_output_mode(self) -> str:
        return self._wiki_output_mode(
            self._config_value("fandom_output_mode", "????")
        )

    @staticmethod
    def _ally_description_view_mode_value(value: Any) -> str:
        normalized = str(value or "????").strip().casefold()
        return {
            "????": "text",
            "text": "text",
            "????": "forward",
            "forward": "forward",
            "????": "card",
            "card": "card",
        }.get(normalized, "text")

    def _ally_description_view_mode(self) -> str:
        return self._ally_description_view_mode_value(
            self._config_value("ally_description_view_mode", "????")
        )

    @staticmethod
    def _wikirby_image_data_uri(data: bytes | None) -> str:
        if not data:
            return ""
        if data.startswith(b"\x89PNG"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            mime = "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    async def _wiki_card_component(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
        *,
        rich_sections: Optional[List[Dict[str, Any]]] = None,
        template_name: Any,
        wiki_name: str,
        reference_label: str,
    ) -> Any | None:
        components = await self._wiki_card_components(
            page,
            summary,
            detail_text,
            image_bytes,
            rich_sections=rich_sections,
            template_name=template_name,
            wiki_name=wiki_name,
            reference_label=reference_label,
            paginate=False,
        )
        return components[0] if components else None

    def _wiki_card_resolution_level(self) -> str:
        resolution = str(
            self._config_value("wiki_card_resolution", "??????") or ""
        ).strip().casefold()
        return {
            "??": "standard",
            "standard": "standard",
            "??????": "high",
            "??": "high",
            "high": "high",
            "??": "ultra",
            "ultra": "ultra",
        }.get(resolution, "high")

    def _wiki_card_render_options(
        self, resolution_level: str | None = None
    ) -> Dict[str, Any]:
        resolution_level = resolution_level or self._wiki_card_resolution_level()
        image_format = str(
            self._config_value("wiki_card_image_format", "JPEG") or "JPEG"
        ).strip().casefold()
        render_type = "png" if image_format == "png" else "jpeg"
        options: Dict[str, Any] = {
            "viewport_width": 1600,
            "viewport_height": 600,
            "selector": "#kirby-card",
            "full_page": True,
            "type": render_type,
            "scale": "device",
            "device_scale_factor_level": resolution_level,
            "animations": "disabled",
            "wait_until": "load",
        }
        if render_type == "jpeg":
            options["quality"] = self._bounded_int(
                self._config_value(
                    "wiki_card_jpeg_quality", DEFAULT_WIKI_CARD_JPEG_QUALITY
                ),
                DEFAULT_WIKI_CARD_JPEG_QUALITY,
                60,
                98,
            )
        return options

    async def _render_wiki_card_layout(
        self,
        page: Dict[str, Any],
        layout: Dict[str, Any],
        image_bytes: bytes | None,
        *,
        theme: Dict[str, str],
        wiki_name: str,
        reference_label: str,
        resolution_level: str | None = None,
    ) -> Tuple[Any | None, Any | None]:
        image_data_uri = self._wikirby_image_data_uri(
            image_bytes if layout.get("show_summary", True) else None
        )
        try:
            rendered = await self.html_render(
                WIKIRBY_CARD_TEMPLATE,
                {
                    "title": str(page.get("title") or "WiKirby"),
                    "source": str(page.get("url") or "https://wikirby.com"),
                    "theme": theme,
                    "wiki_name": wiki_name,
                    "reference_label": reference_label,
                    **layout,
                    "image_data_uri": image_data_uri,
                },
                return_url=False,
                options=self._wiki_card_render_options(resolution_level),
            )
        except Exception as exc:
            logger.warning("[%s] HTML ??????: %s", PLUGIN_ID, exc)
            return None, None
        if not rendered:
            return None, None
        rendered = str(rendered)
        if rendered.startswith(("http://", "https://")):
            return Comp.Image.fromURL(rendered), None

        rendered_path = Path(rendered)
        metrics = await asyncio.to_thread(inspect_image, rendered_path)
        normalize_enabled = self._bool_value(
            self._config_value("media_normalize_jpeg", True)
        )
        if (
            normalize_enabled
            and metrics is not None
            and metrics.image_format in {"JPEG", "JPG"}
        ):
            try:
                rendered_path = await asyncio.to_thread(
                    normalise_jpeg,
                    rendered_path,
                    self._media_cache_dir(),
                    quality=self._bounded_int(
                        self._config_value(
                            "wiki_card_jpeg_quality",
                            DEFAULT_WIKI_CARD_JPEG_QUALITY,
                        ),
                        DEFAULT_WIKI_CARD_JPEG_QUALITY,
                        60,
                        98,
                    ),
                    prefix="kirby-card",
                )
                metrics = await asyncio.to_thread(inspect_image, rendered_path)
            except Exception as exc:
                logger.warning("[%s] ???? JPEG ?????: %s", PLUGIN_ID, exc)
        return Comp.Image.fromFileSystem(str(rendered_path)), metrics

    async def _wiki_card_components(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
        *,
        rich_sections: Optional[List[Dict[str, Any]]] = None,
        template_name: Any,
        wiki_name: str,
        reference_label: str,
        paginate: bool = True,
    ) -> List[Any]:
        theme = resolve_card_template(template_name)
        auto_paginate = paginate and self._bool_value(
            self._config_value("wiki_card_auto_paginate", True)
        )
        budget = self._bounded_int(
            self._config_value(
                "wiki_card_page_line_budget", DEFAULT_WIKI_CARD_PAGE_LINE_BUDGET
            ),
            DEFAULT_WIKI_CARD_PAGE_LINE_BUDGET,
            60,
            MAX_WIKI_CARD_PAGE_LINE_BUDGET,
        )
        max_width = self._bounded_int(
            self._config_value(
                "wiki_card_max_width_px", DEFAULT_WIKI_CARD_MAX_WIDTH_PX
            ),
            DEFAULT_WIKI_CARD_MAX_WIDTH_PX,
            0,
            20000,
        )
        max_height = self._bounded_int(
            self._config_value(
                "wiki_card_max_height_px", DEFAULT_WIKI_CARD_MAX_HEIGHT_PX
            ),
            DEFAULT_WIKI_CARD_MAX_HEIGHT_PX,
            0,
            50000,
        )
        max_megapixels = self._bounded_float(
            self._config_value(
                "wiki_card_max_megapixels", DEFAULT_WIKI_CARD_MAX_MEGAPIXELS
            ),
            DEFAULT_WIKI_CARD_MAX_MEGAPIXELS,
            0,
            200,
        )
        max_bytes = int(
            self._bounded_float(
                self._config_value(
                    "wiki_card_max_bytes_mb", DEFAULT_WIKI_CARD_MAX_BYTES_MB
                ),
                DEFAULT_WIKI_CARD_MAX_BYTES_MB,
                0,
                100,
            )
            * 1024
            * 1024
        )

        force_paginate = False
        resolution_level = self._wiki_card_resolution_level()
        last_components: List[Any] = []
        max_attempts = 6 if auto_paginate else 3
        for attempt in range(max_attempts):
            if auto_paginate:
                layouts = build_card_pages(
                    summary,
                    detail_text,
                    rich_sections,
                    page_line_budget=budget,
                    has_image=bool(image_bytes),
                    force_paginate=force_paginate,
                )
            else:
                layouts = [build_card_layout(summary, detail_text, rich_sections)]

            components: List[Any] = []
            pageable_violations: List[str] = []
            width_violations: List[str] = []
            for layout in layouts:
                component, metrics = await self._render_wiki_card_layout(
                    page,
                    layout,
                    image_bytes,
                    theme=theme,
                    wiki_name=wiki_name,
                    reference_label=reference_label,
                    resolution_level=resolution_level,
                )
                if component is None:
                    return []
                components.append(component)
                if metrics is None:
                    continue
                reasons = image_limit_reasons(
                    metrics,
                    max_width=max_width,
                    max_height=max_height,
                    max_megapixels=max_megapixels,
                    max_bytes=max_bytes,
                )
                logger.info(
                    "[%s] ????????: wiki=%s, page=%s/%s, "
                    "size=%dx%d, megapixels=%.2f, bytes=%d, limits=%s",
                    PLUGIN_ID,
                    wiki_name,
                    layout.get("page_number", 1),
                    layout.get("page_total", len(layouts)),
                    metrics.width,
                    metrics.height,
                    metrics.megapixels,
                    metrics.byte_size,
                    ",".join(reasons) or "ok",
                )
                pageable_violations.extend(
                    reason for reason in reasons if not reason.startswith("width=")
                )
                width_violations.extend(
                    reason for reason in reasons if reason.startswith("width=")
                )

            last_components = components
            if width_violations and resolution_level != "standard":
                next_resolution = "high" if resolution_level == "ultra" else "standard"
                logger.warning(
                    "[%s] ???????????????????????: "
                    "wiki=%s, resolution=%s->%s, reasons=%s",
                    PLUGIN_ID,
                    wiki_name,
                    resolution_level,
                    next_resolution,
                    ",".join(width_violations),
                )
                resolution_level = next_resolution
                continue
            if not pageable_violations or not auto_paginate:
                return components
            next_budget = max(60, int(budget * 0.72))
            if next_budget >= budget:
                break
            logger.warning(
                "[%s] ??????????????????????: "
                "wiki=%s, pages=%d, budget=%d->%d, reasons=%s",
                PLUGIN_ID,
                wiki_name,
                len(layouts),
                budget,
                next_budget,
                ",".join(pageable_violations),
            )
            budget = next_budget
            force_paginate = True
        return last_components

    async def _wikirby_card_component(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
    ) -> Any | None:
        return await self._wiki_card_component(
            page,
            summary,
            detail_text,
            image_bytes,
            template_name=self._config_value(
                "wikirby_card_template", DEFAULT_CARD_TEMPLATE
            ),
            wiki_name="WiKirby",
            reference_label="WIKIRBY REFERENCE",
        )

    async def _wikirby_card_components(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
    ) -> List[Any]:
        return await self._wiki_card_components(
            page,
            summary,
            detail_text,
            image_bytes,
            template_name=self._config_value(
                "wikirby_card_template", DEFAULT_CARD_TEMPLATE
            ),
            wiki_name="WiKirby",
            reference_label="WIKIRBY REFERENCE",
        )

    async def _fandom_card_component(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
        rich_sections: Optional[List[Dict[str, Any]]] = None,
    ) -> Any | None:
        return await self._wiki_card_component(
            page,
            summary,
            detail_text,
            image_bytes,
            rich_sections=rich_sections,
            template_name=self._config_value(
                "fandom_card_template", DEFAULT_CARD_TEMPLATE
            ),
            wiki_name="Kirby Fandom",
            reference_label="FANDOM REFERENCE",
        )

    async def _fandom_card_components(
        self,
        page: Dict[str, Any],
        summary: str,
        detail_text: str,
        image_bytes: bytes | None,
        rich_sections: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Any]:
        return await self._wiki_card_components(
            page,
            summary,
            detail_text,
            image_bytes,
            rich_sections=rich_sections,
            template_name=self._config_value(
                "fandom_card_template", DEFAULT_CARD_TEMPLATE
            ),
            wiki_name="Kirby Fandom",
            reference_label="FANDOM REFERENCE",
        )

    async def _ally_description_card_component(
        self, entry: Dict[str, Any], description: str
    ) -> Any | None:
        profile = self.store.profile_for(entry)
        detail_lines = [f"?????#{entry['id']}"]
        display_work = str(
            profile.get("display_work") or entry.get("source") or ""
        ).strip()
        if display_work:
            detail_lines.append(f"?????{display_work}")
        source_url = str(profile.get("source_url") or "").strip()
        if not source_url:
            source_url = "https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog"
        return await self._wiki_card_component(
            {
                "title": self._display_name(entry),
                "url": source_url,
            },
            description,
            "\n".join(detail_lines),
            None,
            template_name=self._config_value(
                "ally_description_card_template", "????"
            ),
            wiki_name="??????",
            reference_label="ALLY INTRODUCTION",
        )

    def _forward_node_max_chars(self) -> int:
        try:
            value = int(
                self._config_value(
                    "forward_node_max_chars", DEFAULT_FORWARD_NODE_MAX_CHARS
                )
            )
        except (TypeError, ValueError):
            value = DEFAULT_FORWARD_NODE_MAX_CHARS
        return max(500, min(10000, value))

    def _forward_max_nodes(self) -> int:
        try:
            value = int(
                self._config_value(
                    "forward_max_nodes_per_message", DEFAULT_FORWARD_MAX_NODES
                )
            )
        except (TypeError, ValueError):
            value = DEFAULT_FORWARD_MAX_NODES
        return max(2, min(30, value))

    @staticmethod
    def _split_forward_text(text: str, max_chars: int) -> List[str]:
        text = str(text or "")
        if not text:
            return []
        max_chars = max(1, int(max_chars))
        if len(text) <= max_chars:
            return [text]

        chunks: List[str] = []
        cursor = 0
        minimum_break = max_chars // 2
        break_pattern = re.compile(r"\n{2,}|\n|[????.!?;][??????)]*")
        while cursor < len(text):
            remaining = text[cursor:]
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break
            window = remaining[:max_chars]
            candidates = [
                match.end()
                for match in break_pattern.finditer(window)
                if match.end() >= minimum_break
            ]
            cut = candidates[-1] if candidates else max_chars
            chunks.append(remaining[:cut])
            cursor += cut
        return chunks

    def _forward_nodes(
        self, text: str, trailing_components: Optional[List[Any]] = None
    ) -> List[Comp.Nodes]:
        text_nodes = [
            Comp.Node(name="??????", content=[Comp.Plain(chunk)])
            for chunk in self._split_forward_text(text, self._forward_node_max_chars())
        ]
        trailing_nodes: List[Any] = []
        for component in trailing_components or []:
            if component is not None:
                trailing_nodes.append(
                    Comp.Node(name="??????", content=[component])
                )

        max_nodes = self._forward_max_nodes()
        max_images = self._forward_max_images()
        image_nodes = [
            node
            for node in trailing_nodes
            if any(isinstance(item, Comp.Image) for item in node.content)
        ]
        other_trailing_nodes = [
            node for node in trailing_nodes if node not in image_nodes
        ]
        all_nodes = [*text_nodes, *other_trailing_nodes, *image_nodes]

        # Keep ordinary short replies compact. Long text and image-heavy card sets are
        # separated so NapCat does not upload large text and rich media in one packet.
        if (
            len(text_nodes) <= 1
            and len(all_nodes) <= max_nodes
            and len(image_nodes) <= max_images
        ):
            return [Comp.Nodes(nodes=all_nodes)] if all_nodes else []

        batches: List[Comp.Nodes] = []
        non_image_nodes = [*text_nodes, *other_trailing_nodes]
        batches.extend(
            Comp.Nodes(nodes=non_image_nodes[index : index + max_nodes])
            for index in range(0, len(non_image_nodes), max_nodes)
        )
        image_batch_size = min(max_nodes, max_images)
        batches.extend(
            Comp.Nodes(nodes=image_nodes[index : index + image_batch_size])
            for index in range(0, len(image_nodes), image_batch_size)
        )
        return batches

    def _wiki_response_components(
        self,
        text: str,
        image_bytes: bytes | None,
        output_mode: str,
        card_component: Any | List[Any] | None,
    ) -> List[Any]:
        card_components = (
            list(card_component)
            if isinstance(card_component, (list, tuple))
            else [card_component]
            if card_component is not None
            else []
        )
        if output_mode == "card" and card_components:
            return card_components
        if output_mode == "card_and_text" and card_components:
            return [Comp.Plain(text), *card_components]
        if output_mode == "card_forward" and card_components:
            return self._forward_nodes(text, card_components)
        if output_mode == "forward":
            trailing: List[Any] = []
            if image_bytes:
                trailing.append(Comp.Image.fromBytes(image_bytes))
            return self._forward_nodes(text, trailing)
        chain: List[Any] = [Comp.Plain(text)]
        if image_bytes:
            chain.append(Comp.Image.fromBytes(image_bytes))
        return chain

    @staticmethod
    def _log_wiki_response_ready(
        source_name: str,
        query: str,
        output_mode: str,
        text: str,
        components: List[Any],
        started_at: float,
    ) -> None:
        forward_nodes = sum(
            len(component.nodes)
            for component in components
            if isinstance(component, Comp.Nodes)
        )
        logger.info(
            "[%s] %s ???????: query=%r, mode=%s, chars=%d, "
            "forward_nodes=%d, elapsed=%.2fs",
            PLUGIN_ID,
            source_name,
            query,
            output_mode,
            len(text),
            forward_nodes,
            time.monotonic() - started_at,
        )

    async def _wiki_translate_text(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        enabled: bool,
        provider_key: str,
        source_name: str,
    ) -> str:
        """Translate external wiki text with AstrBot's configured provider."""
        if not text or not enabled:
            return text

        provider_id = str(self._config_value(provider_key, "") or "").strip()
        if not provider_id:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            provider_id = await self.context.get_current_chat_provider_id(umo)
        if not provider_id:
            raise RuntimeError("??????? AstrBot ????")

        ttl_key = (
            "fandom_cache_ttl_seconds"
            if source_name.casefold() == "kirby fandom"
            else "wikirby_cache_ttl_seconds"
        )
        try:
            cache_ttl = max(0, int(self._config_value(ttl_key, 3600)))
        except (TypeError, ValueError):
            cache_ttl = 3600
        cache_key = (
            source_name.casefold(),
            provider_id,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        cache = getattr(self, "_wiki_translation_cache", None)
        if cache is None:
            cache = {}
            self._wiki_translation_cache = cache
        now = time.monotonic()
        cached = cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            cache.pop(cache_key, None)

        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=(
                f"????? {source_name} ??????????????"
                "????????????????????? Markdown?"
                "???????????????????????\n\n"
                f"???\n{text}"
            ),
            system_prompt=(
                "??????????????????????"
                "??????????????????????"
                "??????????"
            ),
        )
        translated = str(getattr(response, "completion_text", "") or "").strip()
        result = translated or text
        if cache_ttl > 0:
            expired = [key for key, item in cache.items() if item[0] <= now]
            for key in expired:
                cache.pop(key, None)
            while len(cache) >= MAX_WIKI_TRANSLATION_CACHE_ITEMS:
                cache.pop(next(iter(cache)))
            cache[cache_key] = (now + cache_ttl, result)
        return result

    async def _wikirby_translate_text(
        self, event: AstrMessageEvent, summary: str
    ) -> str:
        return await self._wiki_translate_text(
            event,
            summary,
            enabled=self._wikirby_translate_enabled(),
            provider_key="wikirby_translate_provider_id",
            source_name="WiKirby",
        )

    async def _fandom_translate_text(self, event: AstrMessageEvent, text: str) -> str:
        return await self._wiki_translate_text(
            event,
            text,
            enabled=self._fandom_translate_enabled(),
            provider_key="fandom_translate_provider_id",
            source_name="Kirby Fandom",
        )

    @staticmethod
    def _control_signature(value: str) -> tuple[str, ...]:
        normalized = str(value or "").upper()
        direction_patterns = (
            (r"\bLEFT\s+STICK\s+DOWN\b|???(?:?)??|????", "LS_DOWN"),
            (r"\bLEFT\s+STICK\s+UP\b|???(?:?)??|????", "LS_UP"),
            (r"\bLEFT\s+STICK\s+LEFT\b|???(?:?)??|????", "LS_LEFT"),
            (r"\bLEFT\s+STICK\s+RIGHT\b|???(?:?)??|????", "LS_RIGHT"),
            (r"\bRIGHT\s+STICK\s+DOWN\b|???(?:?)??|????", "RS_DOWN"),
            (r"\bRIGHT\s+STICK\s+UP\b|???(?:?)??|????", "RS_UP"),
            (r"\bRIGHT\s+STICK\s+LEFT\b|???(?:?)??|????", "RS_LEFT"),
            (r"\bRIGHT\s+STICK\s+RIGHT\b|???(?:?)??|????", "RS_RIGHT"),
            (r"\b(?:CONTROL\s+STICK|STICK)\s+DOWN\b|??(?:?)??|???", "STICK_DOWN"),
            (r"\b(?:CONTROL\s+STICK|STICK)\s+UP\b|??(?:?)??|???", "STICK_UP"),
            (r"\b(?:CONTROL\s+STICK|STICK)\s+LEFT\b|??(?:?)??|???", "STICK_LEFT"),
            (
                r"\b(?:CONTROL\s+STICK|STICK)\s+RIGHT\b|??(?:?)??|???",
                "STICK_RIGHT",
            ),
            (
                r"\b(?:D-?PAD\s+)?DOWN\s+BUTTON\b|(?:????|????|????|???|??|??)",
                "DPAD_DOWN",
            ),
            (
                r"\b(?:D-?PAD\s+)?UP\s+BUTTON\b|(?:????|????|????|???|??|??)",
                "DPAD_UP",
            ),
            (
                r"\b(?:D-?PAD\s+)?LEFT\s+BUTTON\b|(?:????|????|????|???|??|??)",
                "DPAD_LEFT",
            ),
            (
                r"\b(?:D-?PAD\s+)?RIGHT\s+BUTTON\b|(?:????|????|????|???|??|??)",
                "DPAD_RIGHT",
            ),
        )
        for pattern, token in direction_patterns:
            normalized = re.sub(pattern, f" {token} ", normalized)
        return tuple(
            re.findall(
                r"(?:LS|RS|STICK|DPAD)_(?:DOWN|UP|LEFT|RIGHT)|"
                r"(?<![A-Za-z0-9])(?:ZL|ZR|SL|SR|A|B|X|Y|L|R)(?![A-Za-z0-9])|\+",
                normalized,
            )
        )

    @classmethod
    def _translated_controls_are_safe(cls, source: str, candidate: str) -> bool:
        if bool(source.strip()) != bool(candidate.strip()):
            return False
        if source.count("\n") != candidate.count("\n"):
            return False
        return cls._control_signature(source) == cls._control_signature(candidate)

    @classmethod
    def _translated_rich_sections(
        cls, original: List[Dict[str, Any]], translated: Any
    ) -> List[Dict[str, Any]]:
        if isinstance(translated, dict):
            translated = translated.get("sections")
        if not isinstance(translated, list) or len(translated) != len(original):
            raise ValueError("?????????????")

        result: List[Dict[str, Any]] = []
        for source, candidate in zip(original, translated):
            if not isinstance(candidate, dict) or candidate.get("kind") != source.get(
                "kind"
            ):
                raise ValueError("?????????????")
            merged = deepcopy(source)
            for key in ("title", "context", "intro"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    merged[key] = value.strip()

            if source.get("kind") == "quotes":
                source_quotes = list(source.get("quotes", []) or [])
                candidate_quotes = candidate.get("quotes")
                if not isinstance(candidate_quotes, list) or len(
                    candidate_quotes
                ) != len(source_quotes):
                    raise ValueError("?????????????")
                for index, source_quote in enumerate(source_quotes):
                    translated_quote = candidate_quotes[index]
                    if not isinstance(translated_quote, dict):
                        raise ValueError("????????????")
                    for key in ("text", "attribution", "source"):
                        value = translated_quote.get(key)
                        if isinstance(value, str) and value.strip():
                            merged["quotes"][index][key] = value.strip()
                result.append(merged)
                continue

            source_groups = list(source.get("groups", []) or [])
            candidate_groups = candidate.get("groups")
            if not isinstance(candidate_groups, list) or len(candidate_groups) != len(
                source_groups
            ):
                raise ValueError("???????????????")
            for group_index, source_group in enumerate(source_groups):
                translated_group = candidate_groups[group_index]
                if not isinstance(translated_group, dict):
                    raise ValueError("??????????????")
                label = translated_group.get("label")
                if isinstance(label, str) and label.strip():
                    merged["groups"][group_index]["label"] = label.strip()
                source_rows = list(source_group.get("rows", []) or [])
                translated_rows = translated_group.get("rows")
                if not isinstance(translated_rows, list) or len(translated_rows) != len(
                    source_rows
                ):
                    raise ValueError("?????????????")
                for row_index, source_row in enumerate(source_rows):
                    translated_row = translated_rows[row_index]
                    if not isinstance(translated_row, dict):
                        raise ValueError("????????????")
                    for key in ("move", "description"):
                        value = translated_row.get(key)
                        if isinstance(value, str) and value.strip():
                            merged["groups"][group_index]["rows"][row_index][key] = (
                                value.strip()
                            )
                    translated_controls = translated_row.get("controls")
                    if (
                        isinstance(translated_controls, str)
                        and translated_controls.strip()
                    ):
                        source_controls = str(source_row.get("controls", "") or "")
                        if cls._translated_controls_are_safe(
                            source_controls, translated_controls
                        ):
                            merged["groups"][group_index]["rows"][row_index][
                                "controls"
                            ] = translated_controls.strip()
                    merged["groups"][group_index]["rows"][row_index]["damage"] = (
                        source_row.get("damage", "")
                    )
            result.append(merged)
        return result

    async def _fandom_translate_rich_sections(
        self,
        event: AstrMessageEvent,
        rich_sections: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not rich_sections or not self._fandom_translate_enabled():
            return rich_sections

        provider_id = str(
            self._config_value("fandom_translate_provider_id", "") or ""
        ).strip()
        if not provider_id:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            provider_id = await self.context.get_current_chat_provider_id(umo)
        if not provider_id:
            raise RuntimeError("??????? AstrBot ????")

        source_json = json.dumps(
            rich_sections, ensure_ascii=False, separators=(",", ":")
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=(
                "???? Kirby Fandom ?? JSON ????????????????"
                "??????????? JSON ?????????????????"
                "??? title?context?intro???? text/attribution/source?"
                "?? label?????? move?controls ? description?"
                "?? controls ??????????????????????????"
                "A/B/X/Y/L/R/ZL/ZR/SL/SR ?????????????????"
                "???????????????"
                "??????? kind?ancestors?damage?"
                "???? Markdown ????\n\n"
                f"JSON?\n{source_json}"
            ),
            system_prompt=(
                "?????? JSON ??????????????????"
                "??????????????? JSON?"
            ),
        )
        raw = str(getattr(response, "completion_text", "") or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end >= start:
            raw = raw[start : end + 1]
        try:
            translated = json.loads(raw)
            return self._translated_rich_sections(rich_sections, translated)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "[%s] Kirby Fandom ??????????????: %s",
                PLUGIN_ID,
                exc,
            )
            return rich_sections

    @staticmethod
    def _fandom_rich_sections_text(
        rich_sections: List[Dict[str, Any]],
    ) -> str:
        lines: List[str] = []
        for section in rich_sections:
            title = str(section.get("title", "") or "").strip()
            context = str(section.get("context", "") or "").strip()
            heading = " ? ".join(part for part in (context, title) if part)
            if heading:
                lines.append(f"{heading}?")
            if section.get("kind") == "quotes":
                for quote in section.get("quotes", []):
                    lines.append(f"?{quote.get('text', '')}?")
                    attribution = str(quote.get("attribution", "") or "").strip()
                    source = str(quote.get("source", "") or "").strip()
                    credit = " ? ".join(part for part in (attribution, source) if part)
                    if credit:
                        lines.append(f"? {credit}")
            elif section.get("kind") == "techniques":
                intro = str(section.get("intro", "") or "").strip()
                if intro:
                    lines.append(intro)
                for group in section.get("groups", []):
                    label = str(group.get("label", "") or "").strip()
                    if label:
                        lines.append(f"?{label}?")
                    for row in group.get("rows", []):
                        move = str(row.get("move", "") or "").strip() or "?????"
                        controls = str(row.get("controls", "") or "").strip() or "?"
                        damage = str(row.get("damage", "") or "").strip() or "?"
                        description = str(row.get("description", "") or "").strip()
                        lines.append(f"? {move}????{controls}????{damage}")
                        if description:
                            lines.append(description)
            if int(section.get("omitted_count", 0) or 0):
                lines.append("?????????????????????????")
        return "\n".join(lines).strip()

    def _wikirby_query_parts(self, event: AstrMessageEvent) -> Tuple[str, bool]:
        raw = (event.message_str or "").strip()
        command_text = raw[1:].lstrip() if raw.startswith("/") else raw
        names_only = command_text == "??????" or command_text.startswith(
            ("?????? ", "????? ", "?????? ")
        )
        remainder = self._command_remainder(
            event,
            {
                "??????",
                "?????",
                "??????",
                "????",
                "wikirby",
                "WiKirby",
            },
        )
        for prefix in ("??", "??", "??"):
            if remainder == prefix:
                names_only = True
                remainder = ""
                break
            if remainder.startswith(f"{prefix} "):
                names_only = True
                remainder = remainder[len(prefix) :].strip()
                break

        query = remainder.strip()
        numeric_target = query.lstrip("#") if query else ""
        if numeric_target.isdigit():
            entry, _ = self._entry_or_error(numeric_target)
            if entry:
                query = self._entry_wiki_query(entry)
        elif not query:
            query = self._quoted_wiki_query(event)
        return query, names_only

    @staticmethod
    def _wikirby_candidate_text(
        candidates: List[Dict[str, Any]], names_only: bool
    ) -> str:
        lines = ["??????? WiKirby ??????????????"]
        for index, page in enumerate(candidates, start=1):
            title = page.get("title") or "?????"
            lines.append(f"{index}. {title}")
        command = "??????" if names_only else "????"
        lines.append(f"???{command} {candidates[0].get('title', '')}")
        return "\n".join(lines)

    async def _wikirby_names_text(
        self, query: str, resolved: Optional[Dict[str, Any]] = None
    ) -> str:
        """Return the official names text shared by the command and LLM tool."""
        client = getattr(self, "wikirby", None)
        if client is None:
            return "WiKirby ??????????"
        resolved = resolved or await client.resolve(query)
        if resolved.get("kind") == "candidates":
            return self._wikirby_candidate_text(resolved.get("candidates", []), True)
        if resolved.get("kind") != "page":
            return (
                f"???? WiKirby ???{query}\n"
                "?????????????????????????"
            )

        page = resolved["page"]
        names = await client.get_language_names(page)
        if not names:
            return (
                f"????{page['title']}????????????????\n"
                f"???{page.get('url') or 'https://wikirby.com'}"
            )
        lines = [f"{page['title']} ??????"]
        previous_section = ""
        for row in names:
            section = str(row.get("section", "") or "").strip()
            if section and section != previous_section:
                lines.append(f"?{section}?")
                previous_section = section
            value = row["name"]
            if row.get("romanisation"):
                value += f"?{row['romanisation']}?"
            lines.append(f"{row['language']}?{value}")
        lines.append(f"???{page.get('url') or 'https://wikirby.com'}")
        return "\n".join(lines)

    async def _wikirby_page_content(
        self,
        event: AstrMessageEvent,
        page: Dict[str, Any],
        *,
        translate: bool = True,
    ) -> Tuple[str, str, str]:
        """Build the text shared by the user command and the LLM lookup tool."""
        client = getattr(self, "wikirby", None)
        if client is None:
            return "WiKirby ??????????", "", ""

        lines = [f"WiKirby?{page['title']}"]
        summary = str(page.get("summary", "") or "").strip()
        if summary:
            if translate:
                try:
                    summary = await self._wikirby_translate_text(event, summary)
                except Exception as exc:
                    logger.warning(
                        "[%s] WiKirby AI ?????????: %s", PLUGIN_ID, exc
                    )
            lines.extend(["???", summary])

        show_details = self._config_value("wikirby_show_details", True)
        if isinstance(show_details, str):
            show_details = show_details.strip().casefold() not in {
                "0",
                "false",
                "no",
                "off",
            }
        detail_text = ""
        if show_details:
            try:
                details = await client.get_page_details(page)
            except Exception as exc:
                logger.warning("[%s] WiKirby ????????: %s", PLUGIN_ID, exc)
                details = {"infobox": [], "sections": []}
            detail_lines: list[str] = []
            for row in details.get("infobox", []):
                detail_lines.append(f"{row['label']}?{row['value']}")
            for section in details.get("sections", []):
                detail_lines.extend([f"{section['title']}?", section["text"]])
            if detail_lines:
                detail_text = "\n".join(detail_lines)
                if translate and self._wikirby_translate_enabled():
                    try:
                        detail_text = await self._wikirby_translate_text(
                            event, detail_text
                        )
                    except Exception as exc:
                        logger.warning(
                            "[%s] WiKirby ?????????????: %s",
                            PLUGIN_ID,
                            exc,
                        )
                lines.extend(["???", detail_text])
        lines.append(f"???{page.get('url') or 'https://wikirby.com'}")
        return "\n".join(lines), summary, detail_text

    @filter.llm_tool(name="kirby_catalog_lookup_official_names")
    async def wikirby_lookup_official_names(
        self, event: AstrMessageEvent, query: str
    ) -> str:
        """?? WiKirby ???????????

        ???????????????????????????

        Args:
            query(string): ?????????????? WiKirby ?????
        """
        if not self._wikirby_enabled():
            return "WiKirby ??????????"
        try:
            return await self._wikirby_names_text(query.strip())
        except WikirbyError as exc:
            logger.warning("[%s] LLM ?? WiKirby ??????: %s", PLUGIN_ID, exc)
            return f"WiKirby ?????{exc}"
        except Exception as exc:
            logger.exception("[%s] LLM ?? WiKirby ??????: %s", PLUGIN_ID, exc)
            return "WiKirby ???????????"

    @filter.llm_tool(name="kirby_catalog_lookup_wikirby")
    async def wikirby_lookup_page(self, event: AstrMessageEvent, query: str) -> str:
        """?? WiKirby ?????????????????

        ??????????????????????????????????
        ?????????????????

        Args:
            query(string): ?????????????????? WiKirby ?????
        """
        if not self._wikirby_enabled():
            return "WiKirby ??????????"
        client = getattr(self, "wikirby", None)
        if client is None:
            return "WiKirby ??????????"
        try:
            resolved = await client.resolve(query.strip())
            if resolved.get("kind") == "candidates":
                return self._wikirby_candidate_text(
                    resolved.get("candidates", []), False
                )
            if resolved.get("kind") != "page":
                return (
                    f"???? WiKirby ???{query}\n"
                    "?????????????????????????"
                )
            text, _, _ = await self._wikirby_page_content(
                event, resolved["page"], translate=False
            )
            return text
        except WikirbyError as exc:
            logger.warning("[%s] LLM ?? WiKirby ??????: %s", PLUGIN_ID, exc)
            return f"WiKirby ?????{exc}"
        except Exception as exc:
            logger.exception("[%s] LLM ?? WiKirby ??????: %s", PLUGIN_ID, exc)
            return "WiKirby ???????????"

    async def _wikirby_query_impl(self, event: AstrMessageEvent):
        if not self._wikirby_enabled():
            yield event.plain_result("WiKirby ??????????")
            return
        client = getattr(self, "wikirby", None)
        if client is None:
            yield event.plain_result("WiKirby ??????????")
            return

        query, names_only = self._wikirby_query_parts(event)
        if not query:
            yield event.plain_result(
                "??????? <???????>?"
                "??????????????? <???>?"
            )
            return
        started_at = time.monotonic()
        try:
            resolved = await client.resolve(query)
            if resolved.get("kind") == "candidates":
                yield event.plain_result(
                    self._wikirby_candidate_text(
                        resolved.get("candidates", []), names_only
                    )
                )
                return
            if resolved.get("kind") != "page":
                yield event.plain_result(
                    f"???? WiKirby ???{query}\n"
                    "?????????????????????????"
                )
                return

            page = resolved["page"]
            if names_only:
                yield event.plain_result(
                    await self._wikirby_names_text(query, resolved)
                )
                return

            text, summary, detail_text = await self._wikirby_page_content(event, page)
            show_image = self._config_value("wikirby_show_image", True)
            if isinstance(show_image, str):
                show_image = show_image.strip().casefold() not in {
                    "0",
                    "false",
                    "no",
                    "off",
                }
            image_bytes = None
            if show_image and page.get("image_url"):
                image_bytes = await self.wikirby.get_image_bytes(page["image_url"])

            output_mode = self._wikirby_output_mode()
            card_components: List[Any] = []
            if output_mode in {"card", "card_and_text", "card_forward"}:
                card_components = await self._wikirby_card_components(
                    page, summary, detail_text, image_bytes
                )
                if not card_components:
                    output_mode = "forward" if output_mode == "card_forward" else "text"
            components = self._wiki_response_components(
                text, image_bytes, output_mode, card_components
            )
            self._log_wiki_response_ready(
                "WiKirby", query, output_mode, text, components, started_at
            )
            result = await self._chain_result_with_media(
                event, components, media_profile="wiki_card"
            )
            if result is not None:
                yield result
        except WikirbyError as exc:
            logger.warning("[%s] WiKirby ????: %s", PLUGIN_ID, exc)
            yield event.plain_result(f"WiKirby ?????{exc}")
        except Exception as exc:
            logger.exception("[%s] WiKirby ????: %s", PLUGIN_ID, exc)
            yield event.plain_result("WiKirby ???????????")

    def _fandom_query_parts(self, event: AstrMessageEvent) -> Tuple[str, str, str]:
        raw = (event.message_str or "").strip()
        command_text = raw[1:].lstrip() if raw.startswith("/") else raw
        folded = command_text.casefold()
        mode = "page"
        if folded.startswith(("??f??", "??fandom??", "????????")):
            mode = "names"
        elif folded.startswith(("??f??", "??fandom??", "????????")):
            mode = "sections"
        remainder = self._command_remainder(
            event,
            {
                "??F??",
                "??f??",
                "??Fandom??",
                "??fandom??",
                "????????",
                "??F??",
                "??f??",
                "??Fandom??",
                "??fandom??",
                "????????",
                "??F",
                "??f",
                "??Fandom",
                "??fandom",
                "??????",
                "kirbyfandom",
                "KirbyFandom",
            },
        )
        section = ""
        if mode == "page" and "|" in remainder:
            remainder, section = (part.strip() for part in remainder.split("|", 1))
        query = remainder.strip()
        numeric_target = query.lstrip("#") if query else ""
        if numeric_target.isdigit():
            entry, _ = self._entry_or_error(numeric_target)
            if entry:
                query = self._entry_wiki_query(entry)
        elif not query:
            query = self._quoted_wiki_query(event)
        return query, mode, section

    @staticmethod
    def _fandom_candidate_text(
        candidates: List[Dict[str, Any]], mode: str = "page"
    ) -> str:
        lines = ["??????? Kirby Fandom ??????????????"]
        for index, page in enumerate(candidates, start=1):
            lines.append(f"{index}. {page.get('title') or '?????'}")
        command = {
            "names": "??F??",
            "sections": "??F??",
        }.get(mode, "??F")
        lines.append(f"???{command} {candidates[0].get('title', '')}")
        return "\n".join(lines)

    async def _fandom_names_text(
        self, query: str, resolved: Optional[Dict[str, Any]] = None
    ) -> str:
        client = getattr(self, "fandom", None)
        if client is None:
            return "Kirby Fandom ??????????"
        resolved = resolved or await client.resolve(query)
        if resolved.get("kind") == "candidates":
            return self._fandom_candidate_text(resolved.get("candidates", []), "names")
        if resolved.get("kind") != "page":
            return (
                f"???? Kirby Fandom ???{query}\n"
                "???????????????????????"
            )
        page = resolved["page"]
        names = client.get_language_names(page)
        if not names:
            return (
                f"????{page['title']}?????????????\n"
                f"???{page.get('url') or 'https://kirby.fandom.com'}"
            )
        lines = [f"Kirby Fandom?{page['title']}??????????"]
        for row in names:
            value = row.get("name", "")
            if row.get("romanisation"):
                value += f"?{row['romanisation']}?"
            lines.append(f"{row.get('language', '????')}?{value}")
        lines.extend(
            [
                "????????? Fandom ????????????????????",
                f"???{page.get('url') or 'https://kirby.fandom.com'}",
            ]
        )
        return "\n".join(lines)

    async def _fandom_sections_text(
        self, query: str, resolved: Optional[Dict[str, Any]] = None
    ) -> str:
        client = getattr(self, "fandom", None)
        if client is None:
            return "Kirby Fandom ??????????"
        resolved = resolved or await client.resolve(query)
        if resolved.get("kind") == "candidates":
            return self._fandom_candidate_text(
                resolved.get("candidates", []), "sections"
            )
        if resolved.get("kind") != "page":
            return f"???? Kirby Fandom ???{query}"
        page = resolved["page"]
        sections = client.get_section_titles(page)
        if not sections:
            return f"?{page['title']}??????????????"
        lines = [f"Kirby Fandom?{page['title']}?????"]
        for row in sections:
            indent = "  " if row.get("level") not in {"", "2"} else ""
            lines.append(f"{indent}{row.get('index')}. {row.get('title')}")
        lines.extend(
            [
                f"???????F {page['title']} | {sections[0]['title']}",
                f"???{page.get('url') or 'https://kirby.fandom.com'}",
            ]
        )
        return "\n".join(lines)

    async def _fandom_page_content(
        self,
        event: AstrMessageEvent,
        page: Dict[str, Any],
        *,
        section: str = "",
        translate: bool = True,
    ) -> Tuple[str, str, str, List[Dict[str, Any]]]:
        client = getattr(self, "fandom", None)
        if client is None:
            return "Kirby Fandom ??????????", "", "", []

        lines = [f"Kirby Fandom?{page['title']}"]
        summary = str(page.get("summary", "") or "").strip()
        if summary and translate:
            try:
                summary = await self._fandom_translate_text(event, summary)
            except Exception as exc:
                logger.warning(
                    "[%s] Kirby Fandom AI ?????????: %s",
                    PLUGIN_ID,
                    exc,
                )
        if summary and not section:
            lines.extend(["???", summary])

        details = client.get_page_details(page, section)
        detail_lines: list[str] = []
        rich_sections = [dict(row) for row in details.get("rich_sections", [])]
        if section:
            matched_sections = details.get("sections", [])
            if not matched_sections and not rich_sections:
                return (
                    f"Kirby Fandom?{page['title']}????????{section}??",
                    "",
                    "",
                    [],
                )
            for row in matched_sections:
                detail_lines.extend([f"{row['title']}?", row["text"]])
        elif self._bool_value(self._config_value("fandom_show_details", True)):
            for row in details.get("infobox", []):
                detail_lines.append(f"{row['label']}?{row['value']}")
            for row in details.get("categories", []):
                detail_lines.append(f"{row['label']}?{row['value']}")
            for row in details.get("sections", []):
                detail_lines.extend([f"{row['title']}?", row["text"]])

        detail_text = "\n".join(detail_lines).strip()
        if detail_text and translate and self._fandom_translate_enabled():
            try:
                detail_text = await self._fandom_translate_text(event, detail_text)
            except Exception as exc:
                logger.warning(
                    "[%s] Kirby Fandom ?????????????: %s",
                    PLUGIN_ID,
                    exc,
                )

        if rich_sections and translate and self._fandom_translate_enabled():
            try:
                rich_sections = await self._fandom_translate_rich_sections(
                    event, rich_sections
                )
            except Exception as exc:
                logger.warning(
                    "[%s] Kirby Fandom ??/???????????: %s",
                    PLUGIN_ID,
                    exc,
                )

        if not section:
            names = client.get_language_names(page)
            if names:
                name_lines = ["????????"]
                for row in names:
                    value = row.get("name", "")
                    if row.get("romanisation"):
                        value += f"?{row['romanisation']}?"
                    name_lines.append(f"? {row.get('language', '????')}?{value}")
                detail_text = "\n".join(
                    part for part in (detail_text, "\n".join(name_lines)) if part
                )
        rich_text = self._fandom_rich_sections_text(rich_sections)
        response_detail_text = "\n".join(
            part for part in (detail_text, rich_text) if part
        )
        if response_detail_text:
            lines.extend(["???", response_detail_text])
        lines.append(f"???{page.get('url') or 'https://kirby.fandom.com'}")
        return "\n".join(lines), summary, detail_text, rich_sections

    @filter.llm_tool(name="kirby_catalog_lookup_fandom_names")
    async def fandom_lookup_names(self, event: AstrMessageEvent, query: str) -> str:
        """?? Kirby Fandom ???????????

        ???????????????????????????????
        ??????????????????????

        Args:
            query(string): ?????????????? Kirby Fandom ?????
        """
        if not self._fandom_enabled():
            return "Kirby Fandom ??????????"
        try:
            return await self._fandom_names_text(query.strip())
        except KirbyFandomError as exc:
            logger.warning(
                "[%s] LLM ?? Kirby Fandom ??????: %s",
                PLUGIN_ID,
                exc,
            )
            return f"Kirby Fandom ?????{exc}"
        except Exception as exc:
            logger.exception(
                "[%s] LLM ?? Kirby Fandom ??????: %s",
                PLUGIN_ID,
                exc,
            )
            return "Kirby Fandom ???????????"

    @filter.llm_tool(name="kirby_catalog_lookup_fandom")
    async def fandom_lookup_page(
        self, event: AstrMessageEvent, query: str, section: str = ""
    ) -> str:
        """?? Kirby Fandom ??????????????????

        ???????????????????????????????
        section ???????????????????????????

        Args:
            query(string): ?????????????? Kirby Fandom ?????
            section(string): ?????????? Games?Personality ? Trivia?
        """
        if not self._fandom_enabled():
            return "Kirby Fandom ??????????"
        client = getattr(self, "fandom", None)
        if client is None:
            return "Kirby Fandom ??????????"
        try:
            resolved = await client.resolve(query.strip())
            if resolved.get("kind") == "candidates":
                return self._fandom_candidate_text(
                    resolved.get("candidates", []), "page"
                )
            if resolved.get("kind") != "page":
                return f"???? Kirby Fandom ???{query}"
            text, _, _, _ = await self._fandom_page_content(
                event,
                resolved["page"],
                section=section.strip(),
                translate=False,
            )
            return text
        except KirbyFandomError as exc:
            logger.warning("[%s] LLM ?? Kirby Fandom ????: %s", PLUGIN_ID, exc)
            return f"Kirby Fandom ?????{exc}"
        except Exception as exc:
            logger.exception("[%s] LLM ?? Kirby Fandom ????: %s", PLUGIN_ID, exc)
            return "Kirby Fandom ???????????"

    async def _fandom_query_impl(self, event: AstrMessageEvent):
        if not self._fandom_enabled():
            yield event.plain_result("Kirby Fandom ??????????")
            return
        client = getattr(self, "fandom", None)
        if client is None:
            yield event.plain_result("Kirby Fandom ??????????")
            return

        query, mode, section = self._fandom_query_parts(event)
        if not query:
            yield event.plain_result(
                "?????F <???>???F?? <???>?"
                "??F?? <???>?"
                "???????F <???> | <???>?"
            )
            return
        started_at = time.monotonic()
        try:
            resolved = await client.resolve(query)
            if resolved.get("kind") == "candidates":
                yield event.plain_result(
                    self._fandom_candidate_text(resolved.get("candidates", []), mode)
                )
                return
            if resolved.get("kind") != "page":
                yield event.plain_result(
                    f"???? Kirby Fandom ???{query}\n"
                    "???????????????????????"
                )
                return
            if mode == "names":
                yield event.plain_result(await self._fandom_names_text(query, resolved))
                return
            if mode == "sections":
                yield event.plain_result(
                    await self._fandom_sections_text(query, resolved)
                )
                return

            page = resolved["page"]
            text, summary, detail_text, rich_sections = await self._fandom_page_content(
                event, page, section=section
            )
            if section and not detail_text and not rich_sections:
                sections_text = await self._fandom_sections_text(query, resolved)
                yield event.plain_result(f"{text}\n\n{sections_text}")
                return

            show_image = self._bool_value(self._config_value("fandom_show_image", True))
            image_bytes = None
            if show_image and page.get("image_url"):
                image_bytes = await client.get_image_bytes(page["image_url"])
            output_mode = self._fandom_output_mode()
            card_components: List[Any] = []
            if output_mode in {"card", "card_and_text", "card_forward"}:
                card_components = await self._fandom_card_components(
                    page, summary, detail_text, image_bytes, rich_sections
                )
                if not card_components:
                    output_mode = "forward" if output_mode == "card_forward" else "text"
            components = self._wiki_response_components(
                text, image_bytes, output_mode, card_components
            )
            self._log_wiki_response_ready(
                "Kirby Fandom",
                query,
                output_mode,
                text,
                components,
                started_at,
            )
            result = await self._chain_result_with_media(
                event, components, media_profile="wiki_card"
            )
            if result is not None:
                yield result
        except KirbyFandomError as exc:
            logger.warning("[%s] Kirby Fandom ????: %s", PLUGIN_ID, exc)
            yield event.plain_result(f"Kirby Fandom ?????{exc}")
        except Exception as exc:
            logger.exception("[%s] Kirby Fandom ????: %s", PLUGIN_ID, exc)
            yield event.plain_result("Kirby Fandom ???????????")

    @staticmethod
    def _normalise_guess(value: str) -> str:
        return re.sub(r"[\s\W_]+", "", value.casefold())

    def _guess_aliases(self, entry: Dict[str, Any]) -> set[str]:
        name = self._display_name(entry)
        profile = self.store.profile_for(entry)
        variant_key = str(entry.get("variant_key") or "").strip()
        page_title = str(entry.get("page_title") or "").strip()
        filename = str(entry.get("filename") or "").strip()
        source = str(entry.get("source") or "").strip()
        filename_name = Path(filename).stem
        if source and filename_name.startswith(f"{source}."):
            filename_name = filename_name[len(source) + 1 :]

        raw_candidates = [
            name,
            filename,
            filename_name,
            variant_key,
            str(profile.get("name_zh") or ""),
            str(profile.get("name_en") or ""),
            str(profile.get("display_name") or ""),
            *[str(alias) for alias in entry.get("aliases", [])],
        ]
        if not variant_key or self._normalise_guess(
            variant_key
        ) == self._normalise_guess(page_title):
            raw_candidates.append(page_title)

        aliases: set[str] = set()
        blocked_base_name = (
            self._normalise_guess(page_title)
            if variant_key
            and self._normalise_guess(variant_key) != self._normalise_guess(page_title)
            else ""
        )

        def add_alias(value: str) -> None:
            value = value.strip()
            if not value:
                return
            if blocked_base_name and self._normalise_guess(value) == blocked_base_name:
                return
            aliases.add(value)

        for raw_candidate in raw_candidates:
            candidate = str(raw_candidate or "").strip()
            if not candidate:
                continue
            add_alias(candidate)
            stem = re.sub(
                r"\.(?:png|jpe?g|gif|bmp|webp)$", "", candidate, flags=re.IGNORECASE
            )
            add_alias(stem)
            for match in re.finditer(r"[?(]([^??()]+)[?)]", stem):
                bracketed = match.group(1).strip()
                add_alias(bracketed)
            without_brackets = re.sub(r"[?(][^??()]+[?)]", "", stem).strip()
            add_alias(without_brackets)
        return aliases

    def _guess_matches(self, entry: Dict[str, Any], answer: str) -> bool:
        answer = self._normalise_guess(answer)
        return any(
            answer and answer == self._normalise_guess(candidate)
            for candidate in self._guess_aliases(entry)
        )

    @classmethod
    def _quoted_contains_image(cls, event: AstrMessageEvent) -> bool:
        components = getattr(getattr(event, "message_obj", None), "message", []) or []
        for component in components:
            if isinstance(component, Comp.Reply):
                for nested in cls._nested_components(component.chain):
                    if isinstance(nested, Comp.Image) or (
                        isinstance(nested, dict)
                        and str(nested.get("type", "")).lower() == "image"
                    ):
                        return True
            elif isinstance(component, dict) and str(
                component.get("type", "")
            ).lower() in {"reply", "Reply"}:
                for nested in cls._nested_components(component.get("chain")):
                    if isinstance(nested, Comp.Image) or (
                        isinstance(nested, dict)
                        and str(nested.get("type", "")).lower() == "image"
                    ):
                        return True

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            reply = raw_message.get("reply")
            if isinstance(reply, dict):
                segments = reply.get("message", reply.get("chain", []))
                for segment in segments or []:
                    if (
                        isinstance(segment, dict)
                        and str(segment.get("type", "")).lower() == "image"
                    ):
                        return True
        return False

    async def _answer_guess(
        self,
        event: AstrMessageEvent,
        answer: str,
        announce_missing: bool = False,
    ) -> Optional[str]:
        group_id = self._group_id(event)
        session = self._guess_sessions.get(group_id)
        if not session:
            return (
                "???????????????????????"
                if announce_missing
                else None
            )

        timeout = max(30, int(self._config_value("guess_timeout_seconds", 180)))
        if time.monotonic() - session["started_at"] > timeout:
            self._guess_sessions.pop(group_id, None)
            self._cancel_guess_timeout(group_id)
            entry = self.store.resolve_entry(session["filename"])
            if entry:
                return (
                    f"???????????????? #{entry['id']} "
                    f"{self._display_name(entry)}?"
                )
            return "?????????????????????"

        entry = self.store.resolve_entry(session["filename"])
        if not entry:
            self._guess_sessions.pop(group_id, None)
            self._cancel_guess_timeout(group_id)
            return "??????????????"
        if not self._guess_matches(entry, answer):
            self._guess_sessions.pop(group_id, None)
            self._cancel_guess_timeout(group_id)
            return (
                f"?????????????? #{entry['id']} "
                f"{self._display_name(entry)}?"
            )

        self._guess_sessions.pop(group_id, None)
        self._cancel_guess_timeout(group_id)
        return f"??????? #{entry['id']} {self._display_name(entry)}?"

    def _draw_message(
        self,
        entry: Dict[str, Any],
        nickname: str,
        remaining: int,
        flags: str,
    ) -> str:
        values = self._ally_message_values(entry)
        values.update(
            {
                "nickname": nickname,
                "flags": flags,
                "remaining": remaining,
            }
        )
        template = str(
            self._config_value("draw_message_template", DEFAULT_DRAW_MESSAGE_TEMPLATE)
            or DEFAULT_DRAW_MESSAGE_TEMPLATE
        )
        try:
            return template.format_map(values)
        except (KeyError, ValueError) as exc:
            logger.warning("[%s] ????????????????: %s", PLUGIN_ID, exc)
            return DEFAULT_DRAW_MESSAGE_TEMPLATE.format_map(values)

    def _ally_message_values(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        source = str(entry.get("source") or entry.get("debut_work") or "").strip()
        return {
            "name": self._display_name(entry),
            "id": int(entry.get("id", 0) or 0),
            "source": source,
            "source_text": f"???????{source}?" if source else "",
        }

    def _formatted_ally_message(
        self,
        template_key: str,
        default_template: str,
        values: Dict[str, Any],
    ) -> str:
        template = str(
            self._config_value(template_key, default_template) or default_template
        )
        try:
            return template.format_map(values)
        except (KeyError, ValueError) as exc:
            logger.warning(
                "[%s] %s ????????????: %s",
                PLUGIN_ID,
                template_key,
                exc,
            )
            return default_template.format_map(values)

    async def _draw_for_identity(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        base_limit: int,
        cooldown: float = 0,
        include_bonus: bool = True,
        reuse_today: bool = False,
    ) -> Tuple[AllyDrawOutcome | None, str | None]:
        now = time.monotonic()
        last_draw = self._cooldowns.get(group_id, {}).get(user_id, 0.0)
        if now - last_draw < cooldown:
            return None, (
                f"{nickname}?????????? "
                f"{cooldown - (now - last_draw):.1f} ??"
            )

        async with self._draw_lock:
            # The store is kept current by startup, WebUI, and admin mutations.
            # A full filesystem and group-data scan here made every draw wait.
            today = get_today()
            config = self.store.load_group(group_id)
            user = self._user_data(config, user_id, nickname)
            count = self.store.draw_count(group_id, user_id, today)
            bonus = (
                self.store.draw_bonus(group_id, user_id, today)
                if include_bonus
                else 0
            )
            limit = max(1, int(base_limit)) + bonus
            if reuse_today:
                current = user.get("current", {})
                if str(current.get("date") or "") == today:
                    current_entry = self.store.resolve_entry(
                        str(current.get("ally_filename") or "")
                    )
                    if current_entry is not None:
                        return (
                            AllyDrawOutcome(
                                entry=current_entry,
                                remaining=max(0, limit - count),
                                repeated=False,
                                pity=False,
                                existing_today=True,
                            ),
                            None,
                        )
            if count >= limit:
                return None, (
                    f"{nickname}???????? {count} ??"
                    f"??????? {limit} ????????"
                )
            pool = self.store.get_draw_pool()
            if not pool:
                return None, "?????????????????????"
            unlocked = set(self.store.unlocked_filenames(user))
            new_pool = [entry for entry in pool if entry["filename"] not in unlocked]
            no_new_count = int(user.get("no_new_count", 0) or 0)
            pity = bool(new_pool and no_new_count >= 2)
            entry = random.choice(new_pool if pity else pool)
            repeated = entry["filename"] in unlocked
            user["no_new_count"] = no_new_count + 1 if repeated else 0
            user["current"] = {"ally_filename": entry["filename"], "date": today}
            self.store.unlock(user, entry["filename"], today)
            user["nickname"] = nickname or user.get("nickname", "??")
            config[user_id] = user
            self.store.save_group(group_id, config)
            self.store.increment_draw(group_id, user_id, today)
            if cooldown > 0:
                self._cooldowns.setdefault(group_id, {})[user_id] = time.monotonic()

        return (
            AllyDrawOutcome(
                entry=entry,
                remaining=limit - count - 1,
                repeated=repeated,
                pity=pity,
            ),
            None,
        )

    async def _draw_ally_impl(self, event: AstrMessageEvent):
        """????????????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        user_id = self._sender_id(event)
        nickname = self._sender_name(event)
        if not user_id:
            yield event.plain_result("???????????????")
            return

        started_at = time.monotonic()
        outcome, error = await self._draw_for_identity(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            base_limit=max(1, int(self._config_value("daily_draw_limit", 3))),
            cooldown=max(
                0.0, float(self._config_value("draw_cooldown_seconds", 3))
            ),
        )
        if error or outcome is None:
            yield event.plain_result(error or "?????????????")
            return

        selection_seconds = time.monotonic() - started_at
        entry = outcome.entry
        flags = ("????" if outcome.repeated else "") + (
            "????" if outcome.pity else ""
        )
        text = self._ally_detail_message(
            entry, self._draw_message(entry, nickname, outcome.remaining, flags)
        )
        asset_started_at = time.monotonic()
        chain = await self._ally_chain(entry, text)
        asset_seconds = time.monotonic() - asset_started_at
        delivery_timing: Dict[str, Any] = {}
        result = await self._chain_result_with_media(
            event, chain, timing=delivery_timing
        )
        logger.info(
            "[%s] ??????: entry=#%s %s, select=%.3fs, asset=%.3fs, "
            "prepare=%.3fs, send=%.3fs, mode=%s, total=%.3fs",
            PLUGIN_ID,
            entry.get("id", "?"),
            entry.get("name", "?????"),
            selection_seconds,
            asset_seconds,
            float(delivery_timing.get("prepare_seconds", 0.0)),
            float(delivery_timing.get("send_seconds", 0.0)),
            delivery_timing.get("delivery_mode", "unknown"),
            time.monotonic() - started_at,
        )
        if result is not None:
            yield result

    @filter.regex(r"^/?(?:????|???|????)$")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def draw_ally(self, event: AstrMessageEvent):
        """???????????????????? Handler ?????"""
        async for result in self._draw_ally_impl(event):
            yield result

    @staticmethod
    def _bot_identity(event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        raw_event = getattr(message_obj, "raw_message", None)
        candidates = [getattr(message_obj, "self_id", "")]
        if hasattr(raw_event, "get"):
            try:
                candidates.append(raw_event.get("self_id"))
            except Exception:
                pass
        for candidate in candidates:
            normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(candidate or ""))
            if normalized:
                return f"bot_{normalized}"[:128]
        return "bot_astrbot"

    def _bot_draw_message(self, outcome: AllyDrawOutcome) -> str:
        nickname = str(
            self._config_value("bot_draw_nickname", "??????")
            or "??????"
        ).strip()
        values = self._ally_message_values(outcome.entry)
        values.update(
            {
                "nickname": nickname,
                "flags": ("????" if outcome.repeated else "")
                + ("????" if outcome.pity else ""),
                "remaining": outcome.remaining,
            }
        )
        return self._formatted_ally_message(
            "bot_draw_message_template",
            DEFAULT_BOT_DRAW_MESSAGE_TEMPLATE,
            values,
        )

    async def _draw_bot_ally(
        self, event: AstrMessageEvent
    ) -> Tuple[AllyDrawOutcome | None, str | None]:
        if not self._bool_value(self._config_value("bot_draw_enabled", True)):
            return None, "Bot ???????????"
        group_id = self._group_id(event)
        if not group_id:
            return None, "Bot ?????????"
        nickname = str(
            self._config_value("bot_draw_nickname", "??????")
            or "??????"
        ).strip()
        return await self._draw_for_identity(
            group_id=group_id,
            user_id=self._bot_identity(event),
            nickname=nickname,
            base_limit=max(1, int(self._config_value("daily_draw_limit", 3))),
            include_bonus=True,
        )

    @filter.regex(r"(?i)^/?(?:bot????|???????|bot???)$")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def draw_bot_ally(self, event: AstrMessageEvent):
        """? Bot ????????????????"""
        outcome, error = await self._draw_bot_ally(event)
        if error or outcome is None:
            yield event.plain_result(error or "Bot ?????????????")
            return
        text = self._ally_detail_message(
            outcome.entry, self._bot_draw_message(outcome)
        )
        chain = await self._ally_chain(outcome.entry, text)
        result = await self._chain_result_with_media(event, chain)
        if result is not None:
            yield result

    @filter.llm_tool(name="kirby_catalog_draw_bot_ally")
    async def draw_bot_ally_tool(self, event: AstrMessageEvent) -> str:
        """? Bot ??????????????????

        ????????? Bot ????????????????????
        ????? daily_draw_limit?????????????????????
        ??????????????????????????
        """
        outcome, error = await self._draw_bot_ally(event)
        if error or outcome is None:
            return error or "Bot ?????????????"
        text = self._ally_detail_message(
            outcome.entry, self._bot_draw_message(outcome)
        )
        chain = await self._ally_chain(outcome.entry, text)
        try:
            await self._chain_result_with_media(
                event, chain, send_immediately=True
            )
        except Exception as exc:
            logger.exception("[%s] Bot LLM ??????????: %s", PLUGIN_ID, exc)
            return (
                f"?????? #{outcome.entry['id']} "
                f"{self._display_name(outcome.entry)}????????????????"
            )
        return (
            f"????????????? #{outcome.entry['id']} "
            f"{self._display_name(outcome.entry)} ?????????????????"
        )

    @filter.command("?????????", alias={"????????", "???????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def reset_group_draw_counts(self, event: AstrMessageEvent):
        """???????????????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        today = get_today()
        async with self._draw_lock:
            result = self.store.reset_group_draws(group_id, today)
            self._cooldowns.pop(group_id, None)
        if result["users"]:
            yield event.plain_result(
                f"????? {today} ????????? {result['users']} ????"
                f"?? {result['draw_records']} ???????? "
                f"{result['bonus_records']} ????????"
            )
        else:
            yield event.plain_result(f"?? {today} ??????????????")

    @filter.command("????????", alias={"????????", "????????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def add_member_draw_count(self, event: AstrMessageEvent):
        """????????????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        command_names = {
            "????????",
            "????????",
            "????????",
        }
        remainder = self._command_remainder(event, command_names)
        target_id = self._at_target(event) or ""
        amount = 1
        if target_id:
            plain_parts = [
                str(getattr(component, "text", "") or "").strip()
                for component in (
                    getattr(getattr(event, "message_obj", None), "message", []) or []
                )
                if isinstance(component, Comp.Plain)
                and str(getattr(component, "text", "") or "").strip()
            ]
            amount_source = " ".join(plain_parts) or remainder
            amount_match = re.search(r"(?:^|\s)([+-]?\d+)\s*$", amount_source)
            if amount_match and amount_match.group(1) != target_id:
                amount = int(amount_match.group(1))
        else:
            match = re.fullmatch(r"\s*(\d+)(?:\s+([+-]?\d+))?\s*", remainder)
            if match:
                target_id = match.group(1)
                amount = int(match.group(2) or 1)
        if not target_id.isdigit() or amount <= 0:
            yield event.plain_result(
                "??????????? @?? [??]?"
                "?????????????? ??ID [??]?"
            )
            return

        today = get_today()
        base_limit = max(1, int(self._config_value("daily_draw_limit", 3)))
        async with self._draw_lock:
            total_bonus = self.store.add_draw_bonus(
                group_id, target_id, amount=amount, today=today
            )
            used = self.store.draw_count(group_id, target_id, today)
        config = self.store.load_group(group_id)
        nickname = str(config.get(target_id, {}).get("nickname") or target_id)
        total_limit = base_limit + total_bonus
        remaining = max(0, total_limit - used)
        yield event.plain_result(
            f"?? {nickname}?{target_id}??? {amount} ????????"
            f"???? {total_limit} ???? {used} ???? {remaining} ??"
        )

    @filter.command("????", alias={"??????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def random_ally(self, event: AstrMessageEvent):
        """??????????????????????"""
        pool = self.store.get_draw_pool()
        if not pool:
            yield event.plain_result("?????????????????????")
            return
        entry = random.choice(pool)
        values = self._ally_message_values(entry)
        text = self._formatted_ally_message(
            "random_message_template", DEFAULT_RANDOM_MESSAGE_TEMPLATE, values
        )
        text = self._ally_detail_message(entry, text)
        chain = await self._ally_chain(entry, text)
        result = await self._chain_result_with_media(event, chain)
        if result is not None:
            yield result

    @filter.command("???", alias={"????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def query_ally(self, event: AstrMessageEvent):
        """???????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        remainder = self._command_remainder(event, {"???", "????"})
        config = self.store.load_group(group_id)
        target_id = self._at_target(event)
        if not target_id and remainder:
            if remainder in config:
                target_id = remainder
            else:
                matches = self.store.find_user_by_nickname(config, remainder)
                if len(matches) == 1:
                    target_id = matches[0]
                elif len(matches) > 1:
                    yield event.plain_result("??????????? @ ???")
                    return
        target_id = target_id or self._sender_id(event)
        user = config.get(str(target_id))
        today = get_today()
        current = user.get("current", {}) if user else {}
        filename = current.get("ally_filename", "")
        if not user or not filename or current.get("date") != today:
            yield event.plain_result("????????????")
            return
        entry = self.store.resolve_entry(filename)
        if not entry:
            yield event.plain_result("????????????????????????")
            return
        unlock_date = next(
            (
                item.get("unlock_date")
                for item in user.get("unlocked", [])
                if item.get("ally_filename") == filename
            ),
            "",
        )
        owner = user.get("nickname") or "??"
        values = self._ally_message_values(entry)
        values.update(
            {
                "nickname": owner,
                "unlock_date": unlock_date,
                "unlock_text": f"???? {unlock_date}" if unlock_date else "",
            }
        )
        text = self._formatted_ally_message(
            "query_message_template", DEFAULT_QUERY_MESSAGE_TEMPLATE, values
        )
        text = self._ally_detail_message(entry, text)
        chain = await self._ally_chain(entry, text)
        result = await self._chain_result_with_media(event, chain)
        if result is not None:
            yield result

    @filter.regex(r"^/?(?:????|??????)(?:\s+.+)?$")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def view_ally_description(self, event: AstrMessageEvent):
        """?????????????????"""
        command_names = {"????", "??????"}
        target = self._command_remainder(event, command_names) or self._quoted_target(
            event
        )
        if not target:
            yield event.plain_result(
                "????? Bot ?????????????????"
                "?????????? <????>?"
            )
            return
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        assert entry is not None
        description = self._ally_description_text(entry, respect_enabled=False)
        if not description:
            yield event.plain_result(
                f"#{entry['id']} {self._display_name(entry)} ?????"
            )
            return
        text = f"#{entry['id']} {self._display_name(entry)}\n???\n{description}"
        output_mode = self._ally_description_view_mode()
        if output_mode == "forward":
            result = await self._chain_result_with_media(
                event,
                self._wiki_response_components(text, None, "forward", None),
            )
            if result is not None:
                yield result
            return
        if output_mode == "card":
            card_component = await self._ally_description_card_component(
                entry, description
            )
            if card_component is not None:
                result = await self._chain_result_with_media(
                    event, [card_component], media_profile="wiki_card"
                )
                if result is not None:
                    yield result
                return
        yield event.plain_result(text)

    @filter.regex(r"^/?(?:????(?:??|?|??)?|wikirby|WiKirby)(?:\s+.+)?$")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def wikirby_query_plain(self, event: AstrMessageEvent):
        """???????????? WiKirby ?????? Handler ?????"""
        async for result in self._wikirby_query_impl(event):
            yield result

    @filter.regex(
        r"(?i)^/?(?:??f(?:??|??)?|??fandom(?:??|??)?"
        r"|??????(?:??|??)?|kirbyfandom)(?:\s+.+)?$"
    )
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def fandom_query_plain(self, event: AstrMessageEvent):
        """???? Kirby Fandom ????????????"""
        async for result in self._fandom_query_impl(event):
            yield result

    @filter.command("??????", alias={"?????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def group_gallery(self, event: AstrMessageEvent):
        """?????????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        config = self.store.load_group(group_id)
        unlocked = {
            filename
            for user in config.values()
            for filename in self.store.unlocked_filenames(user)
        }
        if not self.store.entries():
            yield event.plain_result("???????????")
            return
        output = self.store.gallery_dir / f"group_{Path(group_id).name}.png"
        title = f"????????  ??? {len(unlocked)}/{len(self.store.entries())}"
        try:
            outputs = await asyncio.to_thread(
                self.store.render_gallery_pages,
                output,
                unlocked,
                title,
                int(self._config_value("gallery_columns", 10)),
                False,
                self._bounded_int(
                    self._config_value(
                        "gallery_max_height_px", DEFAULT_GALLERY_MAX_HEIGHT_PX
                    ),
                    DEFAULT_GALLERY_MAX_HEIGHT_PX,
                    0,
                    30000,
                ),
            )
            components = [
                Comp.Plain(title),
                *(Comp.Image.fromFileSystem(str(path)) for path in outputs),
            ]
            result = await self._chain_result_with_media(event, components)
            if result is not None:
                yield result
        except Exception as exc:
            logger.exception("[%s] ???????: %s", PLUGIN_ID, exc)
            yield event.plain_result("?????????????")

    @filter.command("??????", alias={"????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def personal_gallery(self, event: AstrMessageEvent):
        """????????????"""
        group_id = self._group_id(event)
        user_id = self._sender_id(event)
        config = self.store.load_group(group_id)
        user = config.get(user_id)
        progress = self.store.user_progress(user or {})
        unlocked = set(progress["unlocked_filenames"])
        if not unlocked:
            yield event.plain_result("???????????")
            return
        output = (
            self.store.gallery_dir
            / f"personal_{Path(group_id).name}_{Path(user_id).name}.png"
        )
        title = (
            f"{self._sender_name(event)} ?????  "
            f"??? {progress['unlocked']}/{progress['total']}"
        )
        try:
            outputs = await asyncio.to_thread(
                self.store.render_gallery_pages,
                output,
                unlocked,
                title,
                int(self._config_value("gallery_columns", 10)),
                True,
                self._bounded_int(
                    self._config_value(
                        "gallery_max_height_px", DEFAULT_GALLERY_MAX_HEIGHT_PX
                    ),
                    DEFAULT_GALLERY_MAX_HEIGHT_PX,
                    0,
                    30000,
                ),
            )
            components = [
                Comp.Plain(title),
                *(Comp.Image.fromFileSystem(str(path)) for path in outputs),
            ]
            result = await self._chain_result_with_media(event, components)
            if result is not None:
                yield result
        except Exception as exc:
            logger.exception("[%s] ????????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????????????")

    @filter.command("??????", alias={"????", "????????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def personal_progress(self, event: AstrMessageEvent):
        """?????????????????????"""
        group_id = self._group_id(event)
        user_id = self._sender_id(event)
        user = self.store.load_group(group_id).get(user_id, {})
        progress = self.store.user_progress(user)
        total = int(progress["total"])
        if total <= 0:
            yield event.plain_result("???????????")
            return
        unlocked = int(progress["unlocked"])
        remaining = max(0, total - unlocked)
        percent = unlocked / total * 100
        filled = min(20, round(unlocked / total * 20))
        bar = "?" * filled + "?" * (20 - filled)
        status = "????????" if remaining == 0 else f"?? {remaining} ???"
        yield event.plain_result(
            f"{self._sender_name(event)} ?????\n"
            f"{bar} {percent:.1f}%\n"
            f"????{unlocked}/{total}\n"
            f"{status}"
        )

    @filter.command("???")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_ally(self, event: AstrMessageEvent):
        """?????????????????????????"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("?????????")
            return
        remainder = self._command_remainder(event, {"???"})
        session = self._guess_sessions.get(group_id)
        timeout = max(30, int(self._config_value("guess_timeout_seconds", 180)))
        if remainder:
            result = await self._answer_guess(event, remainder, announce_missing=True)
            if result:
                yield event.plain_result(result)
            return

        if session:
            if time.monotonic() - session["started_at"] > timeout:
                self._guess_sessions.pop(group_id, None)
                self._cancel_guess_timeout(group_id)
                entry = self.store.resolve_entry(session["filename"])
                if entry:
                    yield event.plain_result(
                        f"???????????????? #{entry['id']} "
                        f"{self._display_name(entry)}?"
                    )
            else:
                yield event.plain_result(
                    "???????????????????????????????"
                )
                return

        pool = self.store.get_draw_pool()
        if not pool:
            yield event.plain_result("?????????????????????")
            return
        entry = random.choice(pool)
        started_at = time.monotonic()
        self._guess_sessions[group_id] = {
            "filename": entry["filename"],
            "started_at": started_at,
            "umo": event.unified_msg_origin,
        }
        self._schedule_guess_timeout(
            group_id,
            entry["filename"],
            started_at,
            timeout,
            event.unified_msg_origin,
        )
        clue = (
            f"???{entry['source']}"
            if entry.get("source")
            else "???????????"
        )
        text = (
            f"??????{clue}\n??????? <??>\n"
            f"?????????{timeout} ?????"
        )
        chain = await self._ally_chain(entry, text)
        result = await self._chain_result_with_media(event, chain)
        if result is not None:
            yield result

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_ally_by_quoted_image(self, event: AstrMessageEvent):
        """??????????????????"""
        group_id = self._group_id(event)
        if not self._guess_sessions.get(group_id):
            return
        text = (event.message_str or "").strip()
        command_text = text[1:].lstrip() if text.startswith("/") else text
        if command_text == "???" or command_text.startswith("??? "):
            return
        if not text or not self._quoted_contains_image(event):
            return
        result = await self._answer_guess(event, text)
        if result:
            yield event.plain_result(result)

    @filter.command("?????", alias={"???????", "?????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def leaderboard(self, event: AstrMessageEvent):
        """??????????????"""
        group_id = self._group_id(event)
        excluded = ()
        if not self._bool_value(
            self._config_value("bot_show_in_leaderboard", False)
        ):
            excluded = (self._bot_identity(event),)
        rows = self.store.leaderboard(
            group_id, limit=10, exclude_user_ids=excluded
        )
        if not rows:
            yield event.plain_result("??????????")
            return
        lines = ["???????????????????"]
        for index, (_user_id, nickname, count) in enumerate(rows, start=1):
            lines.append(f"{index}. {nickname}?{count} ?")
        yield event.plain_result("\n".join(lines))

    @filter.command("????", alias={"????????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def ally_list(self, event: AstrMessageEvent):
        """????????????????????"""
        remainder = self._command_remainder(event, {"????", "????????"})
        entries = self.store.entries()
        if remainder:
            folded = remainder.casefold()
            entries = [
                entry
                for entry in entries
                if folded in str(entry.get("name", "")).casefold()
                or folded in str(entry.get("filename", "")).casefold()
            ]
        if not entries:
            yield event.plain_result("????????")
            return
        lines = [f"?????? {len(entries)} ????? 100 ?"]
        lines.extend(
            f"#{entry['id']} {entry.get('name') or '?????'}"
            + (f"?{entry['source']}?" if entry.get("source") else "")
            for entry in entries[:100]
        )
        if len(entries) > 100:
            lines.append("?????????????")
        yield event.plain_result("\n".join(lines))

    @filter.command("????????", alias={"????"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def help_command(self, event: AstrMessageEvent):
        """??????????????"""
        yield event.plain_result(
            "??????\n"
            "???????????\n"
            "??????????????? @ ??\n"
            "??????? Bot ????????????????????\n"
            "?????????????\n"
            "???????????????????????\n"
            "????????????????????\n"
            "?????????????????????\n"
            "Bot?????? Bot ????????????\n"
            "??????????????????????????\n"
            "??????????????\n"
            "???? [???]??????????\n"
            "???? [???]??? WiKirby ???????????????\n"
            "?????? [???]??????????????\n"
            "??F [???]??? Kirby Fandom ?????????????\n"
            "??F?? [???]?????????????? | ??????????\n"
            "??F?? [???]?????????????????????\n"
            "????????????????????????????????"
            "??????????????????????????"
        )

    @filter.command("????????", alias={"??????????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def replace_image(self, event: AstrMessageEvent):
        """???????????????????????"""
        remainder = self._command_remainder(
            event, {"????????", "??????????"}
        )
        target = remainder or self._quoted_target(event)
        if not target:
            yield event.plain_result(
                "???????????????????????????? <??>?"
            )
            return
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        data = await asyncio.to_thread(self._image_bytes_from_event, event)
        if not data:
            yield event.plain_result("??????????????????")
            return
        try:
            self.store.replace_asset(entry or {}, data)
        except Exception as exc:
            logger.warning("[%s] ??????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????????? PNG?JPG ? GIF ???")
            return
        yield event.plain_result(
            f"??? #{entry['id']} {self._display_name(entry)} ????"
            "???????????"
        )

    @filter.command("????????")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def add_ally(self, event: AstrMessageEvent):
        """???????????????????"""
        remainder = self._command_remainder(event, {"????????"})
        if not remainder:
            yield event.plain_result(
                "??????????? ???????????"
                "??????????? | ????"
            )
            return
        name, separator, source = remainder.partition("|")
        data = await asyncio.to_thread(self._image_bytes_from_event, event)
        if not data:
            yield event.plain_result("?????????????????")
            return
        try:
            entry = self.store.add_asset(
                name.strip(), data, source.strip() if separator else ""
            )
        except Exception as exc:
            logger.warning("[%s] ??????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????????? PNG?JPG ? GIF?")
            return
        yield event.plain_result(
            f"??? #{entry['id']} {self._display_name(entry)}????????????"
        )

    @filter.command("????????")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def rename_ally(self, event: AstrMessageEvent):
        """???????????????????????"""
        remainder = self._command_remainder(event, {"????????"})
        target, separator, new_value = remainder.partition(" ")
        if not separator:
            quoted_target = self._quoted_target(event)
            if quoted_target:
                target = quoted_target
                new_value = remainder
        if not target or not new_value.strip():
            yield event.plain_result(
                "??????????? <??> <???>?"
                "??????????????????? <???>?"
            )
            return
        new_name, separator, source = new_value.partition("|")
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        try:
            updated = await asyncio.to_thread(
                self.store.rename_entry,
                entry or {},
                new_name.strip(),
                source.strip() if separator else None,
            )
        except Exception as exc:
            logger.warning("[%s] ??????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????????????????")
            return
        yield event.plain_result(
            f"?? #{updated['id']} ?? {self._display_name(updated)}?"
            "????????????"
        )

    @filter.command("????????", alias={"????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def edit_ally_description(self, event: AstrMessageEvent):
        """?????????????????????"""
        command_names = {"????????", "????"}
        remainder = self._command_remainder(event, command_names)
        quoted_target = self._quoted_target(event)
        target = ""
        description = ""
        if quoted_target:
            parts = remainder.split(maxsplit=1)
            if len(parts) == 2 and parts[0].lstrip("#").isdigit():
                target = parts[0]
                description = parts[1].strip()
            else:
                target = quoted_target
                description = remainder.strip()
        elif remainder:
            parts = remainder.split(maxsplit=1)
            target = parts[0]
            description = parts[1].strip() if len(parts) == 2 else ""

        if not target:
            yield event.plain_result(
                "??????????? <??> [???]?\n"
                "?????????????????????????????????"
            )
            return
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        assert entry is not None

        if not description:
            profile = self.store.profile_for(entry)
            current = str(profile.get("description_zh") or "").strip()
            if not current:
                yield event.plain_result(
                    f"#{entry['id']} {self._display_name(entry)} ?????\n"
                    "????????????????????????"
                )
                return
            origin = (
                "???????"
                if profile.get("description_origin") == "override"
                else "????"
            )
            source_url = str(profile.get("source_url") or "").strip()
            source_text = f"\n???{source_url}" if source_url else ""
            yield event.plain_result(
                f"#{entry['id']} {self._display_name(entry)}?{origin}?\n"
                f"{current}{source_text}"
            )
            return

        try:
            self.store.set_description(
                entry, description, updated_by=self._sender_id(event)
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(
            f"??? #{entry['id']} {self._display_name(entry)} ??????\n"
            "??????????????????????"
        )

    @filter.command("??????????", alias={"??????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def restore_ally_description(self, event: AstrMessageEvent):
        """???????????????????????"""
        command_names = {"??????????", "??????"}
        target = self._command_remainder(event, command_names) or self._quoted_target(
            event
        )
        if not target:
            yield event.plain_result(
                "????????????? <??>?????????????"
            )
            return
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        assert entry is not None
        removed, profile = self.store.restore_description(entry)
        if not removed:
            yield event.plain_result(
                f"#{entry['id']} {self._display_name(entry)} ????????????"
            )
            return
        if profile.get("description_zh"):
            yield event.plain_result(
                f"??? #{entry['id']} {self._display_name(entry)} ??????"
            )
        else:
            yield event.plain_result(
                f"??? #{entry['id']} {self._display_name(entry)} ??????"
                "??????????"
            )

    @filter.command("????????")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def migrate_command(self, event: AstrMessageEvent):
        """??????????????????"""
        try:
            self.store.migrate_legacy()
            self.store.refresh()
        except Exception as exc:
            logger.exception("[%s] ????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????? AstrBot ???")
            return
        yield event.plain_result("????????????????????")

    @filter.command("??????????")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def cleanup_renamed_names(self, event: AstrMessageEvent):
        """??????????????????????"""
        remainder = self._command_remainder(event, {"??????????"})
        parts = remainder.split()
        if len(parts) < 2:
            yield event.plain_result(
                "????????????? <???> <???> [???]"
            )
            return
        old_prefix, new_prefix = parts[:2]
        keep_names = parts[2:]
        try:
            result = await asyncio.to_thread(
                self.store.cleanup_renamed_prefix,
                old_prefix,
                new_prefix,
                keep_names,
            )
        except Exception as exc:
            logger.exception("[%s] ??????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????? AstrBot ???")
            return
        lines = [f"?????????? {len(result['removed'])} ????"]
        if result["kept"]:
            lines.append("???" + "?".join(result["kept"]))
        if result["unresolved"]:
            lines.append(
                "??????????????" + "?".join(result["unresolved"])
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("??????????", alias={"??????????"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def remove_duplicate_entries(self, event: AstrMessageEvent):
        """????????? ????????????"""
        remainder = self._command_remainder(
            event, {"??????????", "??????????"}
        )
        parts = remainder.split()
        ids: List[int] = []
        for part in parts:
            try:
                ids.append(int(part.lstrip("#")))
            except ValueError:
                pass
        if not ids or len(ids) % 2:
            yield event.plain_result(
                "????????????? <????> <????> [????] [????]"
            )
            return
        mappings = list(zip(ids[::2], ids[1::2]))
        try:
            result = await asyncio.to_thread(
                self.store.merge_duplicate_entries, mappings
            )
        except Exception as exc:
            logger.exception("[%s] ????????: %s", PLUGIN_ID, exc)
            yield event.plain_result("???????? AstrBot ???")
            return
        lines = [f"???????????? {len(result['removed'])} ??"]
        if result["unresolved"]:
            lines.append("????" + "?".join(result["unresolved"]))
        yield event.plain_result("\n".join(lines))
