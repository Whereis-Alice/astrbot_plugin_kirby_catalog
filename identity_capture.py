"""Collect OneBot profile nicknames and group cards without changing progress."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog_core import _atomic_write_json, get_today


def parse_member_names(payload: Any) -> dict[str, dict[str, str]]:
    if isinstance(payload, dict):
        if payload.get("status") == "failed" or payload.get("retcode", 0) not in (
            0,
            None,
        ):
            raise ValueError("协议端获取群成员失败")
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ValueError("协议端未返回群成员列表")
    result: dict[str, dict[str, str]] = {}
    conflicts: set[str] = set()
    for member in payload:
        if not isinstance(member, dict):
            continue
        user = str(member.get("user_id") or "")
        profile = str(member.get("nickname") or "").strip()
        if not user.isdigit() or not profile:
            continue
        names = {
            "profile": profile,
            "group_card": str(member.get("card") or "").strip(),
        }
        if user in result and result[user] != names:
            conflicts.add(user)
        result[user] = names
    for user in conflicts:
        result.pop(user, None)
    return result


def save_member_names(
    store: Any, group: str, names: dict[str, dict[str, str]]
) -> dict[str, int]:
    """Reload under the store lock so draws during the API call aren't lost."""
    with store._lock:
        config = store.load_group(group)
        users = {user for user in config if user.isdigit()}
        stats = {"records": len(users), "captured": 0, "updated": 0, "missing": 0}
        for user in users:
            profile = names.get(user)
            if not profile:
                stats["missing"] += 1
                continue
            stats["captured"] += 1
            recorded = {
                **profile,
                "source": profile.get("source", "onebot_member_list"),
                "date": get_today(),
            }
            if config[user].get("identity_names") != recorded:
                config[user]["identity_names"] = recorded
                stats["updated"] += 1
        if stats["updated"]:
            store.save_group(group, config)
        return stats


class _InvalidResponse(ValueError):
    """Only our validation messages are safe to include in a user report."""


def _api_data(payload: Any, user: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        if payload.get("status") == "failed" or payload.get("retcode", 0) not in (
            0,
            None,
        ):
            code = payload.get("retcode")
            detail = f"（retcode={code}）" if isinstance(code, int) else ""
            raise _InvalidResponse("接口返回失败" + detail)
        payload = payload.get("data", payload)
    if not isinstance(payload, dict):
        raise _InvalidResponse("接口返回格式无效")
    if payload.get("user_id") is not None and str(payload["user_id"]) != user:
        raise _InvalidResponse("接口返回了其他 QQ 的资料")
    return payload


class FreshIdentityCapture:
    """Fresh profile request per QQ; fresh group-card request per group/QQ pair."""

    def __init__(self, bot: Any):
        self.bot = bot
        self.limit = asyncio.Semaphore(2)
        self.profiles: dict[str, asyncio.Task] = {}
        self.calls = {"get_group_member_info": 0, "get_stranger_info": 0}

    async def _request(self, action: str, user: str, **params: Any) -> tuple[dict, str]:
        async with self.limit:
            self.calls[action] += 1
            try:
                response = await asyncio.wait_for(
                    self.bot.call_action(
                        action, user_id=int(user), no_cache=True, **params
                    ),
                    15,
                )
                return _api_data(response, user), ""
            except Exception as exc:  # noqa: BLE001 - OneBot protocol boundary
                reason = (
                    "接口超时" if isinstance(exc, TimeoutError) else type(exc).__name__
                )
                if isinstance(exc, _InvalidResponse):
                    reason = str(exc)
                elif isinstance(getattr(exc, "retcode", None), int):
                    reason += f"（retcode={exc.retcode}）"
                return {}, reason

    async def _profile(self, user: str) -> tuple[dict, str]:
        if user not in self.profiles:
            self.profiles[user] = asyncio.create_task(
                self._request("get_stranger_info", user)
            )
        return await asyncio.shield(self.profiles[user])

    async def record(self, group: str, user: str, old: dict) -> dict[str, Any]:
        (member, member_error), (profile, profile_error) = await asyncio.gather(
            self._request("get_group_member_info", user, group_id=int(group)),
            self._profile(user),
        )
        if member.get("group_id") is not None and str(member["group_id"]) != group:
            member, member_error = {}, "接口返回了其他群的资料"
        nickname = profile.get("nickname")
        nickname = nickname.strip() if isinstance(nickname, str) else ""
        profile_source = "get_stranger_info"
        if not nickname:
            profile_error = profile_error or "QQ 资料接口没有返回昵称"
            nickname = member.get("nickname")
            nickname = nickname.strip() if isinstance(nickname, str) else ""
            profile_source = "get_group_member_info" if nickname else "unavailable"
        has_card = "card" in member and isinstance(member["card"], (str, type(None)))
        if not has_card:
            member_error = member_error or "群资料接口没有返回群名片"
        previous = old.get("identity_names")
        previous = previous if isinstance(previous, dict) else {}
        card = (
            str(member.get("card") or "").strip()
            if has_card
            else str(previous.get("group_card") or "")
        )
        status = (
            "full"
            if nickname and has_card
            else "profile_only"
            if nickname
            else "failed"
        )
        return {
            "group_id": group,
            "user_id": user,
            "old_nickname": str(old.get("nickname") or ""),
            "profile": nickname,
            "group_card": card,
            "status": status,
            "profile_source": profile_source,
            "group_card_source": "get_group_member_info" if has_card else "previous",
            "member_error": member_error,
            "profile_error": profile_error,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    async def close(self) -> None:
        for task in self.profiles.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.profiles.values(), return_exceptions=True)


def save_fresh_record(store: Any, row: dict) -> tuple[bool, bool]:
    """Persist API values only; never count an old nickname as fetched data."""
    if not row["profile"]:
        return False, False
    with store._lock:
        config = store.load_group(row["group_id"])
        old = config.get(row["user_id"])
        if old is None:
            row["status"] = "record_removed"
            return False, False
        previous = old.get("identity_names")
        previous = previous if isinstance(previous, dict) else {}
        # A failed card lookup must not erase a card updated by a concurrent draw.
        if row["group_card_source"] != "get_group_member_info":
            row["group_card"] = str(previous.get("group_card") or "")
        changed = (
            previous.get("profile") != row["profile"]
            or previous.get("group_card") != row["group_card"]
        )
        old["identity_names"] = {
            key: row[key]
            for key in (
                "profile",
                "group_card",
                "profile_source",
                "group_card_source",
                "captured_at",
            )
        }
        old["identity_names"].update(
            source="onebot_user_api",
            date=get_today(),
            group_card_current=row["status"] == "full",
        )
        store.save_group(row["group_id"], config)
        return True, changed


def save_capture_report(store: Any, report: dict) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(report.get("id", ""))):
        raise ValueError("采集报告 ID 无效")
    directory = store.root / "identity-capture"
    path = directory / f"{report['id']}.json"
    _atomic_write_json(path, report)
    _atomic_write_json(directory / "latest.json", report)
    return path


def read_capture_report(store: Any) -> dict:
    try:
        report = json.loads(
            (store.root / "identity-capture/latest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("还没有可查看的逐条采集报告，请重新执行采集指令。") from exc
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("records"), list)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("group_id"), str)
            or not isinstance(row.get("user_id"), str)
            or not isinstance(row.get("status"), str)
            for row in report["records"]
        )
    ):
        raise ValueError("采集报告格式无效")
    return report


def capture_report_lines(
    report: dict, *, group: str = "", all_records: bool = False
) -> list[str]:
    statuses = {
        "full": "昵称和群名片已查询",
        "profile_only": "仅补获资料昵称，群名片未更新",
        "failed": "未取得当前资料昵称",
        "record_removed": "旧记录已删除，未写入",
        "pending": "未完成查询和写入，请重试采集",
    }
    rows = [
        row
        for row in report["records"]
        if (not group or row["group_id"] == group)
        and (all_records or row["status"] != "full")
    ]
    state = {"running": "进行中", "complete": "已完成", "interrupted": "已中断"}
    state = state.get(report.get("status"), "未知")
    lines = [
        f"逐条采集报告：{len(rows)} 条记录，涉及 {len({row['user_id'] for row in rows})} 个 QQ。状态：{state}。"
    ]
    for row in rows:
        old_name = str(row.get("old_nickname") or "未知旧显示名").replace("\n", " ")[
            :80
        ]
        line = f"群 {row['group_id']} · QQ {row['user_id']} · 旧显示名：{old_name}\n{statuses.get(row['status'], row['status'])}"
        if row.get("profile"):
            line += "\n接口资料昵称：" + str(row["profile"]).replace("\n", " ")[:80]
            source = {
                "get_stranger_info": "QQ资料接口",
                "get_group_member_info": "群资料接口",
            }.get(row.get("profile_source"), "未知接口")
            line += f"（来源：{source}）"
        if row.get("group_card_source") == "get_group_member_info":
            line += "；接口群名片：" + (
                str(row.get("group_card") or "").replace("\n", " ")[:80] or "（空）"
            )
        elif row.get("group_card"):
            line += "；保留旧群名片：" + str(row["group_card"]).replace("\n", " ")[:80]
        errors = [
            f"{label}：{row[key]}"
            for key, label in (("member_error", "群资料"), ("profile_error", "QQ资料"))
            if row.get(key)
        ]
        if errors:
            line += "；" + "；".join(errors)
        lines.append(line)
    return lines
