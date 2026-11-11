"""Independent daily introductions and AstrBot's persona-aware active jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JOB_PREFIX = "卡比每日盟友介绍 · "
INTRO_NOTE = (
    "每日盟友介绍时间到了。先调用 kirby_catalog_daily_ally_intro，只调用一次。"
    "若人格工具列表只开放旧工具 kirby_catalog_draw_bot_ally，可改调用旧工具，"
    "定时场景会自动改为介绍。"
    "工具先发送角色卡，并提供已保存的真实角色资料。接着使用此会话当前配置的"
    "AstrBot 人格，向当前群发送一段简洁的角色介绍和你自己的感想。"
    "必须调用 send_message_to_user，以 plain 消息发送这段正文，session 留空；"
    "定时任务的最终回答不会自动发送，不能只在最终回答中写介绍。"
    "事实仅以工具资料为依据，感想明确作为自己的看法；不要再次抽卡或重复发图。"
    "如果工具返回无需发送、功能关闭或白名单不允许，静默结束，不向群发送说明。"
)


def introduction_day(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def intro_sessions(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if re.fullmatch(r"[^:\s]+:GroupMessage:[^:\s]+", str(value).strip())
        )
    )


def intro_times(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return sorted(
        set(
            str(value).strip()
            for value in values
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value).strip())
        )
    )


async def sync_intro_jobs(
    manager: Any, sessions: list[str], times: list[str], enabled: bool, timezone: str
) -> list[str]:
    """Reconcile only owned jobs; retire legacy draws when opting into intros."""
    if enabled:
        ZoneInfo(timezone)
    jobs = await manager.list_jobs()
    desired = {}
    if enabled:
        for session in sessions:
            for clock in times:
                hour, minute = map(int, clock.split(":"))
                name = f"{JOB_PREFIX}{session} · {clock}"
                desired[name] = dict(
                    name=name,
                    cron_expression=f"{minute} {hour} * * *",
                    payload={
                        "session": session,
                        "note": INTRO_NOTE,
                        "kirby_intro_slot": clock,
                        "origin": "kirby_daily_intro",
                    },
                    description="角色卡 → 真实资料介绍 → 当前人格的感想",
                    timezone=timezone,
                    enabled=True,
                    persistent=True,
                    run_once=False,
                )
    installed = []
    for job in jobs:
        if job.name.startswith(JOB_PREFIX):
            config = desired.pop(job.name, None)
            if config:
                await manager.update_job(job.job_id, **config)
                installed.append(job.job_id)
            elif job.enabled:
                await manager.update_job(job.job_id, enabled=False)
        elif enabled and job.enabled:
            payload = job.payload if isinstance(job.payload, dict) else {}
            if payload.get(
                "session"
            ) in sessions and "kirby_catalog_draw_bot_ally" in str(
                payload.get("note", "")
            ):
                await manager.update_job(job.job_id, enabled=False)
    for config in desired.values():
        job = await manager.add_active_job(**config)
        installed.append(job.job_id)
    return installed


class IntroductionState:
    """Durable per-session/time claims; never touches draws or collections."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid daily introduction state")
        return value

    def claim(
        self, session: str, date: str, slot: str, entries: list[dict]
    ) -> dict | None:
        state = self._load()
        key = hashlib.sha256(session.encode()).hexdigest()[:32]
        current = state.get(key, {})
        if not isinstance(current, dict):
            raise ValueError("Invalid daily introduction record")
        slots = current.get("slots", {}) if current.get("date") == date else {}
        if not isinstance(slots, dict) or any(
            not isinstance(value, dict) for value in slots.values()
        ):
            raise ValueError("Invalid daily introduction slots")
        if slot in slots:
            return None
        used = {value.get("filename") for value in slots.values()}
        fresh = [entry for entry in entries if entry.get("filename") not in used]
        entry = secrets.choice(fresh or entries)
        slots[slot] = {"filename": entry["filename"], "status": "claimed"}
        state[key] = {"date": date, "slots": slots}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            self.path.name + "." + secrets.token_hex(8) + ".tmp"
        )
        try:
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return entry
