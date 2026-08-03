from __future__ import annotations

import asyncio
import random
import re
import time
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

PLUGIN_ID = "astrbot_plugin_kirby_catalog"
LEGACY_PLUGIN_ID = "astrbot_plugin_AnimeWife"
IMAGE_BASE_URL = "http://save.my996.top/?/img/"


@register(
    PLUGIN_ID,
    "Whereis-Alice",
    "星之卡比盟友抽取、图鉴、猜名与排行榜插件",
    "2.1.3",
    "https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog",
)
class KirbyCatalogPlugin(Star):
    """星之卡比盟友抽取和收藏图鉴。"""

    def __init__(self, context: Context, config: Optional[Any] = None):
        super().__init__(context)
        self.config = config or {}
        data_dir = Path(StarTools.get_data_dir(PLUGIN_ID))
        legacy_dirs = self._legacy_data_dirs()
        self.store = CatalogStore(
            data_dir,
            legacy_dirs=legacy_dirs,
            image_base_url=self._config_value("image_base_url", IMAGE_BASE_URL),
        )
        self._draw_lock = asyncio.Lock()
        self._cooldowns: Dict[str, Dict[str, float]] = {}
        self._guess_sessions: Dict[str, Dict[str, Any]] = {}
        self._guess_timeout_tasks: Dict[str, asyncio.Task[None]] = {}

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
                    f"猜盟友超时，本轮结束。正确答案是 #{entry['id']} "
                    f"{self._display_name(entry)}。"
                )
            else:
                text = "猜盟友超时，本轮结束，但答案素材已经失效。"
            await self.context.send_message(umo, MessageChain([Comp.Plain(text)]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[%s] 猜盟友超时揭晓失败: %s", PLUGIN_ID, exc)
        finally:
            if self._guess_timeout_tasks.get(group_id) is current_task:
                self._guess_timeout_tasks.pop(group_id, None)

    async def terminate(self) -> None:
        tasks = list(self._guess_timeout_tasks.values())
        self._guess_timeout_tasks.clear()
        self._guess_sessions.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
            for section_name in ("draw_settings", "data_settings"):
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
            return event.get_sender_name() or "用户"
        except Exception:
            return "用户"

    @staticmethod
    def _command_remainder(event: AstrMessageEvent, names: Iterable[str]) -> str:
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        for name in sorted(names, key=len, reverse=True):
            if text == name:
                return ""
            if text.startswith(f"{name} "):
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
        components = getattr(getattr(event, "message_obj", None), "message", []) or []
        for component in cls._nested_components(components):
            if isinstance(component, Comp.Reply) or (
                isinstance(component, dict)
                and component.get("type") in {"reply", "Reply"}
            ):
                parts.append(plain_text_from_component(component))
            elif isinstance(component, Comp.Plain):
                parts.append(str(getattr(component, "text", "") or ""))
        return " ".join(part.strip() for part in parts if part.strip())

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
        match = re.search(r"(?:#|编号\s*[:：]?)\s*(\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"(?:名称|盟友)\s*[:：]\s*([^\n]+)", text)
        return match.group(1).strip() if match else ""

    def _entry_or_error(
        self, target: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        self.store.refresh()
        matches = self.store.find_entries(target)
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, f"没有找到盟友「{target}」，请使用图鉴编号或完整名称。"
        preview = "、".join(f"#{item['id']} {item['name']}" for item in matches[:8])
        return None, f"匹配到多个盟友，请改用编号：{preview}"

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

    async def _ally_chain(
        self, entry: Dict[str, Any], text: str, download: bool = True
    ) -> List[Any]:
        data = await asyncio.to_thread(self.store.asset_bytes, entry, download)
        chain: List[Any] = [Comp.Plain(text)]
        if data:
            chain.append(Comp.Image.fromBytes(data))
        else:
            chain[0] = Comp.Plain(f"{text}\n图片暂时不可用，请管理员检查素材。")
        return chain

    @staticmethod
    def _display_name(entry: Dict[str, Any]) -> str:
        return str(entry.get("name") or "未命名盟友")

    @staticmethod
    def _normalise_guess(value: str) -> str:
        return re.sub(r"[\s\W_]+", "", value.casefold())

    def _guess_matches(self, entry: Dict[str, Any], answer: str) -> bool:
        answer = self._normalise_guess(answer)
        candidates = [
            self._display_name(entry),
            str(entry.get("filename", "")),
            *[str(alias) for alias in entry.get("aliases", [])],
        ]
        return any(
            answer and answer == self._normalise_guess(candidate)
            for candidate in candidates
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
                "当前没有有效的猜盟友题目，请先发送“猜盟友”。"
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
                    f"猜盟友超时，本轮结束。正确答案是 #{entry['id']} "
                    f"{self._display_name(entry)}。"
                )
            return "猜盟友超时，本轮结束，但答案素材已经失效。"

        entry = self.store.resolve_entry(session["filename"])
        if not entry:
            self._guess_sessions.pop(group_id, None)
            self._cancel_guess_timeout(group_id)
            return "题目素材已失效，请重新出题。"
        if not self._guess_matches(entry, answer):
            self._guess_sessions.pop(group_id, None)
            self._cancel_guess_timeout(group_id)
            return (
                f"猜错了，本轮结束。正确答案是 #{entry['id']} "
                f"{self._display_name(entry)}。"
            )

        self._guess_sessions.pop(group_id, None)
        self._cancel_guess_timeout(group_id)
        return f"答对啦！答案是 #{entry['id']} {self._display_name(entry)}。"

    async def _draw_ally_impl(self, event: AstrMessageEvent):
        """每天抽取盟友，重复时使用连续未出新保底。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("该功能仅支持群聊。")
            return
        user_id = self._sender_id(event)
        nickname = self._sender_name(event)
        if not user_id:
            yield event.plain_result("无法获取用户信息，请稍后再试。")
            return

        limit = max(1, int(self._config_value("daily_draw_limit", 3)))
        cooldown = max(0.0, float(self._config_value("draw_cooldown_seconds", 3)))
        now = time.monotonic()
        last_draw = self._cooldowns.get(group_id, {}).get(user_id, 0.0)
        if now - last_draw < cooldown:
            yield event.plain_result(
                f"{nickname}，抽卡太快啦，请稍等 "
                f"{cooldown - (now - last_draw):.1f} 秒。"
            )
            return

        async with self._draw_lock:
            self.store.refresh()
            today = get_today()
            count = self.store.draw_count(group_id, user_id, today)
            if count >= limit:
                yield event.plain_result(
                    f"{nickname}，你今天已经抽了 {limit} 次，明天再来吧。"
                )
                return
            pool = self.store.get_draw_pool()
            if not pool:
                yield event.plain_result("当前没有可用盟友素材，请管理员先添加图片。")
                return
            config = self.store.load_group(group_id)
            user = self._user_data(config, user_id, nickname)
            unlocked = set(self.store.unlocked_filenames(user))
            new_pool = [entry for entry in pool if entry["filename"] not in unlocked]
            no_new_count = int(user.get("no_new_count", 0) or 0)
            pity = bool(new_pool and no_new_count >= 2)
            entry = random.choice(new_pool if pity else pool)
            repeated = entry["filename"] in unlocked
            user["no_new_count"] = no_new_count + 1 if repeated else 0
            user["current"] = {"ally_filename": entry["filename"], "date": today}
            self.store.unlock(user, entry["filename"], today)
            user["nickname"] = nickname or user.get("nickname", "用户")
            config[user_id] = user
            self.store.save_group(group_id, config)
            self.store.increment_draw(group_id, user_id, today)
            self._cooldowns.setdefault(group_id, {})[user_id] = time.monotonic()

        remaining = limit - count - 1
        flags = ("（重复）" if repeated else "") + ("（保底）" if pity else "")
        source = f"，来自《{entry['source']}》" if entry.get("source") else ""
        text = (
            f"{nickname}，你今天的盟友是 #{entry['id']} "
            f"{self._display_name(entry)}{source}{flags}。\n"
            f"今日剩余次数：{remaining}"
        )
        yield event.chain_result(await self._ally_chain(entry, text))

    @filter.command("今日盟友", alias={"抽盟友", "抽取盟友"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def draw_ally(self, event: AstrMessageEvent):
        async for result in self._draw_ally_impl(event):
            yield result

    @filter.regex(r"^今日盟友$")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def draw_ally_plain(self, event: AstrMessageEvent):
        """让普通文本“今日盟友”也能触发抽取。"""
        async for result in self._draw_ally_impl(event):
            yield result

    @filter.command("随机盟友", alias={"随机查看盟友"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def random_ally(self, event: AstrMessageEvent):
        """随机展示一位盟友，不写入用户抽取或解锁记录。"""
        self.store.refresh()
        pool = self.store.get_draw_pool()
        if not pool:
            yield event.plain_result("当前没有可用盟友素材，请管理员先添加图片。")
            return
        entry = random.choice(pool)
        source = f"，来自《{entry['source']}》" if entry.get("source") else ""
        text = f"随机盟友：#{entry['id']} {self._display_name(entry)}{source}"
        yield event.chain_result(await self._ally_chain(entry, text))

    @filter.command("查盟友", alias={"我的盟友"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def query_ally(self, event: AstrMessageEvent):
        """查看自己或指定成员今天的盟友。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("该功能仅支持群聊。")
            return
        remainder = self._command_remainder(event, {"查盟友", "我的盟友"})
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
                    yield event.plain_result("匹配到多个成员，请直接 @ 对方。")
                    return
        target_id = target_id or self._sender_id(event)
        user = config.get(str(target_id))
        today = get_today()
        current = user.get("current", {}) if user else {}
        filename = current.get("ally_filename", "")
        if not user or not filename or current.get("date") != today:
            yield event.plain_result("今天还没有查到盟友记录。")
            return
        entry = self.store.resolve_entry(filename)
        if not entry:
            yield event.plain_result("这条盟友记录对应的素材已经不存在，请联系管理员。")
            return
        unlock_date = next(
            (
                item.get("unlock_date")
                for item in user.get("unlocked", [])
                if item.get("ally_filename") == filename
            ),
            "",
        )
        owner = user.get("nickname") or "用户"
        source = f"，来自《{entry['source']}》" if entry.get("source") else ""
        date_text = f"，解锁于 {unlock_date}" if unlock_date else ""
        text = (
            f"{owner} 的盟友是 #{entry['id']} "
            f"{self._display_name(entry)}{source}{date_text}。"
        )
        yield event.chain_result(await self._ally_chain(entry, text))

    @filter.command("星之卡比图鉴", alias={"群盟友图鉴"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def group_gallery(self, event: AstrMessageEvent):
        """查看本群带编号、带名字的盟友图鉴。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("该功能仅支持群聊。")
            return
        self.store.refresh()
        config = self.store.load_group(group_id)
        unlocked = {
            filename
            for user in config.values()
            for filename in self.store.unlocked_filenames(user)
        }
        if not self.store.entries():
            yield event.plain_result("图鉴中还没有盟友素材。")
            return
        output = self.store.gallery_dir / f"group_{Path(group_id).name}.png"
        title = f"星之卡比盟友图鉴  已解锁 {len(unlocked)}/{len(self.store.entries())}"
        try:
            await asyncio.to_thread(
                self.store.render_gallery,
                output,
                unlocked,
                title,
                int(self._config_value("gallery_columns", 10)),
                False,
            )
            yield event.chain_result(
                [Comp.Plain(title), Comp.Image.fromFileSystem(str(output))]
            )
        except Exception as exc:
            logger.exception("[%s] 生成群图鉴失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("图鉴生成失败，请稍后再试。")

    @filter.command("我的盟友图鉴", alias={"盟友图鉴"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def personal_gallery(self, event: AstrMessageEvent):
        """查看自己的盟友收藏图鉴。"""
        group_id = self._group_id(event)
        user_id = self._sender_id(event)
        config = self.store.load_group(group_id)
        user = config.get(user_id)
        unlocked = set(self.store.unlocked_filenames(user or {}))
        if not unlocked:
            yield event.plain_result("你还没有解锁任何盟友。")
            return
        output = (
            self.store.gallery_dir
            / f"personal_{Path(group_id).name}_{Path(user_id).name}.png"
        )
        title = f"{self._sender_name(event)} 的盟友图鉴  已解锁 {len(unlocked)}"
        try:
            await asyncio.to_thread(
                self.store.render_gallery,
                output,
                unlocked,
                title,
                int(self._config_value("gallery_columns", 10)),
                True,
            )
            yield event.chain_result(
                [Comp.Plain(title), Comp.Image.fromFileSystem(str(output))]
            )
        except Exception as exc:
            logger.exception("[%s] 生成个人图鉴失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("个人图鉴生成失败，请稍后再试。")

    @filter.command("猜盟友")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_ally(self, event: AstrMessageEvent):
        """发起或回答一轮猜盟友，答对只公布答案，不改变图鉴。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("该功能仅支持群聊。")
            return
        remainder = self._command_remainder(event, {"猜盟友"})
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
                        f"猜盟友超时，本轮结束。正确答案是 #{entry['id']} "
                        f"{self._display_name(entry)}。"
                    )
            else:
                yield event.plain_result(
                    "本群已有一轮猜盟友正在进行，请直接引用题目图片并发送名字作答。"
                )
                return

        pool = self.store.get_draw_pool()
        if not pool:
            yield event.plain_result("当前没有可用盟友素材，请管理员先添加图片。")
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
            f"来源：{entry['source']}"
            if entry.get("source")
            else "这位盟友正在等待被认出"
        )
        text = (
            f"猜盟友开始！{clue}\n请回复：猜盟友 <名字>\n"
            f"题目编号不会显示，{timeout} 秒后失效。"
        )
        yield event.chain_result(await self._ally_chain(entry, text))

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_ally_by_quoted_image(self, event: AstrMessageEvent):
        """允许引用题目图片并直接发送名字作答。"""
        group_id = self._group_id(event)
        if not self._guess_sessions.get(group_id):
            return
        text = (event.message_str or "").strip()
        command_text = text[1:].lstrip() if text.startswith("/") else text
        if command_text == "猜盟友" or command_text.startswith("猜盟友 "):
            return
        if not text or not self._quoted_contains_image(event):
            return
        result = await self._answer_guess(event, text)
        if result:
            yield event.plain_result(result)

    @filter.command("盟友排行榜", alias={"星之卡比排行榜", "图鉴排行榜"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def leaderboard(self, event: AstrMessageEvent):
        """查看本群盟友解锁数量排行榜。"""
        group_id = self._group_id(event)
        rows = self.store.leaderboard(group_id, limit=10)
        if not rows:
            yield event.plain_result("本群还没有收藏记录。")
            return
        lines = ["星之卡比盟友图鉴排行榜（按已解锁数量）"]
        for index, (_user_id, nickname, count) in enumerate(rows, start=1):
            lines.append(f"{index}. {nickname}：{count} 个")
        yield event.plain_result("\n".join(lines))

    @filter.command("盟友名单", alias={"星之卡比图鉴名单"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def ally_list(self, event: AstrMessageEvent):
        """按编号查看图鉴名字，适合素材较多时检索。"""
        remainder = self._command_remainder(event, {"盟友名单", "星之卡比图鉴名单"})
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
            yield event.plain_result("没有匹配到盟友。")
            return
        lines = [f"盟友名单：共 {len(entries)} 个，显示前 100 个"]
        lines.extend(
            f"#{entry['id']} {entry.get('name') or '未命名盟友'}"
            + (f"（{entry['source']}）" if entry.get("source") else "")
            for entry in entries[:100]
        )
        if len(entries) > 100:
            lines.append("结果较多，请附关键词检索。")
        yield event.plain_result("\n".join(lines))

    @filter.command("星之卡比图鉴帮助", alias={"盟友帮助"})
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def help_command(self, event: AstrMessageEvent):
        """查看星之卡比图鉴的使用说明。"""
        yield event.plain_result(
            "星之卡比图鉴\n"
            "今日盟友：每天抽取盟友\n"
            "查盟友：查看自己今天的盟友，可 @ 成员\n"
            "我的盟友图鉴：查看个人收藏\n"
            "星之卡比图鉴：查看本群图鉴（编号和名字）\n"
            "随机盟友：随机查看一位盟友，不计入抽取记录\n"
            "猜盟友：发起猜名，答对只公布答案，不改变图鉴\n"
            "盟友排行榜：查看本群收藏排行\n"
            "盟友名单 [关键词]：检索图鉴编号和名字\n"
            "管理员命令：星之卡比图鉴添加、换图、改名、迁移、清理旧名、删除重复"
        )

    @filter.command("星之卡比图鉴换图", alias={"星之卡比图鉴替换图片"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def replace_image(self, event: AstrMessageEvent):
        """管理员引用盟友消息中的图片，替换对应图鉴素材。"""
        remainder = self._command_remainder(
            event, {"星之卡比图鉴换图", "星之卡比图鉴替换图片"}
        )
        target = remainder or self._quoted_target(event)
        if not target:
            yield event.plain_result(
                "请指定编号，或直接引用一条盟友消息执行：星之卡比图鉴换图 <编号>。"
            )
            return
        entry, error = self._entry_or_error(target)
        if error:
            yield event.plain_result(error)
            return
        data = await asyncio.to_thread(self._image_bytes_from_event, event)
        if not data:
            yield event.plain_result("没有在当前消息或引用消息中找到图片。")
            return
        try:
            self.store.replace_asset(entry or {}, data)
        except Exception as exc:
            logger.warning("[%s] 替换素材失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("图片格式无法识别，请引用 PNG、JPG 或 GIF 图片。")
            return
        yield event.plain_result(
            f"已替换 #{entry['id']} {self._display_name(entry)} 的素材。"
            "历史图鉴记录不会丢失。"
        )

    @filter.command("星之卡比图鉴添加")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def add_ally(self, event: AstrMessageEvent):
        """管理员引用一张图片并手动添加新的盟友。"""
        remainder = self._command_remainder(event, {"星之卡比图鉴添加"})
        if not remainder:
            yield event.plain_result(
                "用法：星之卡比图鉴添加 名字，可引用一张图片。"
                "名字和来源可写成“名字 | 来源”。"
            )
            return
        name, separator, source = remainder.partition("|")
        data = await asyncio.to_thread(self._image_bytes_from_event, event)
        if not data:
            yield event.plain_result("请在命令中附带或引用一张盟友图片。")
            return
        try:
            entry = self.store.add_asset(
                name.strip(), data, source.strip() if separator else ""
            )
        except Exception as exc:
            logger.warning("[%s] 添加素材失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("图片格式无法识别，请使用 PNG、JPG 或 GIF。")
            return
        yield event.plain_result(
            f"已添加 #{entry['id']} {self._display_name(entry)}，现在可以抽取和猜名了。"
        )

    @filter.command("星之卡比图鉴改名")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def rename_ally(self, event: AstrMessageEvent):
        """管理员修改盟友名字，并同步所有用户的历史记录。"""
        remainder = self._command_remainder(event, {"星之卡比图鉴改名"})
        target, separator, new_value = remainder.partition(" ")
        if not separator:
            quoted_target = self._quoted_target(event)
            if quoted_target:
                target = quoted_target
                new_value = remainder
        if not target or not new_value.strip():
            yield event.plain_result(
                "用法：星之卡比图鉴改名 <编号> <新名字>，"
                "或引用盟友消息后使用：星之卡比图鉴改名 <新名字>。"
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
            logger.warning("[%s] 修改名字失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("修改名字失败，请检查素材文件和新名字。")
            return
        yield event.plain_result(
            f"已将 #{updated['id']} 改为 {self._display_name(updated)}，"
            "所有用户解锁记录已同步。"
        )

    @filter.command("星之卡比图鉴迁移")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def migrate_command(self, event: AstrMessageEvent):
        """管理员重新扫描旧插件数据并补齐迁移。"""
        try:
            self.store.migrate_legacy()
            self.store.refresh()
        except Exception as exc:
            logger.exception("[%s] 迁移失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("迁移失败，请查看 AstrBot 日志。")
            return
        yield event.plain_result("旧插件数据扫描完成，已有收藏记录已保留。")

    @filter.command("星之卡比图鉴清理旧名")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def cleanup_renamed_names(self, event: AstrMessageEvent):
        """管理员一次性合并改名前缀，并保留指定的旧名。"""
        remainder = self._command_remainder(event, {"星之卡比图鉴清理旧名"})
        parts = remainder.split()
        if len(parts) < 2:
            yield event.plain_result(
                "用法：星之卡比图鉴清理旧名 <旧前缀> <新前缀> [保留名]"
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
            logger.exception("[%s] 清理旧名失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("清理失败，请查看 AstrBot 日志。")
            return
        lines = [f"旧名清理完成，已合并 {len(result['removed'])} 个条目。"]
        if result["kept"]:
            lines.append("保留：" + "、".join(result["kept"]))
        if result["unresolved"]:
            lines.append(
                "未处理（找不到唯一的新名）：" + "、".join(result["unresolved"])
            )
        yield event.plain_result("\n".join(lines))

    @filter.command(
        "星之卡比图鉴删除重复", alias={"星之卡比图鉴合并重复"}
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def remove_duplicate_entries(self, event: AstrMessageEvent):
        """管理员按“重复编号 正确编号”合并重复条目。"""
        remainder = self._command_remainder(
            event, {"星之卡比图鉴删除重复", "星之卡比图鉴合并重复"}
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
                "用法：星之卡比图鉴删除重复 <重复编号> <正确编号> [重复编号] [正确编号]"
            )
            return
        mappings = list(zip(ids[::2], ids[1::2]))
        try:
            result = await asyncio.to_thread(
                self.store.merge_duplicate_entries, mappings
            )
        except Exception as exc:
            logger.exception("[%s] 删除重复条目失败: %s", PLUGIN_ID, exc)
            yield event.plain_result("删除失败，请查看 AstrBot 日志。")
            return
        lines = [f"重复条目合并完成，已处理 {len(result['removed'])} 个。"]
        if result["unresolved"]:
            lines.append("未处理：" + "、".join(result["unresolved"]))
        yield event.plain_result("\n".join(lines))
