"""Independent intro schedules, durable deduplication and persona handoff."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api import message_components as Comp
from astrbot_plugin_kirby_catalog.daily_ally_intro import (
    INTRO_NOTE,
    JOB_PREFIX,
    IntroductionState,
    intro_sessions,
    intro_times,
    sync_intro_jobs,
)

from .test_main import FakeEvent, make_plugin

SESSION = "default:GroupMessage:932436510"
ENTRY = {"id": 12, "filename": "a.png", "name": "瓦豆鲁迪", "source": "星之卡比"}


class IntroductionFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin = make_plugin(ENTRY)
        self.plugin.store.root = Path(self.temp.name)
        self.plugin.config = {
            "daily_ally_intro_enabled": True,
            "daily_ally_intro_group_whitelist": [SESSION],
            "daily_ally_intro_times": ["12:20", "21:05"],
            "daily_ally_group_whitelist": ["1035995227"],
            "bot_draw_llm_vision_enabled": False,
        }
        self.event = FakeEvent("每日盟友介绍", group_id="932436510")
        self.event.unified_msg_origin = SESSION
        self.event._extras = {
            "cron_job": {},
            "cron_payload": {"kirby_intro_slot": "12:20"},
        }
        self.plugin._daily_ally_chain = AsyncMock(
            return_value=[Comp.Image.fromFileSystem("/card.png")]
        )
        self.plugin._chain_result_with_media = AsyncMock(return_value=None)

    async def test_intro_uses_own_whitelist_and_original_facts_without_draws(self):
        result = await self.plugin.draw_bot_ally_tool(self.event)
        self.assertIn(self.plugin.store.description, result)
        self.assertIn("此会话配置的人格", result)
        self.assertNotIn("爱丽丝", result)
        self.plugin._daily_ally_chain.assert_awaited_once()
        self.assertTrue(self.plugin._daily_ally_chain.call_args.kwargs["force_card"])
        self.assertTrue(
            self.plugin._chain_result_with_media.call_args.kwargs["send_immediately"]
        )
        self.assertFalse(self.plugin.store.group)
        self.assertFalse(self.plugin.store.draws)
        self.assertIn("无需发送", await self.plugin.daily_ally_intro_tool(self.event))
        self.assertEqual(self.plugin._chain_result_with_media.await_count, 1)

    async def test_two_slots_select_distinct_characters_and_survive_reload(self):
        other = {**ENTRY, "id": 13, "filename": "b.png", "name": "卡比"}
        self.plugin.store.get_draw_pool = lambda: [ENTRY, other]
        await self.plugin.daily_ally_intro_tool(self.event)
        first = self.plugin._daily_ally_chain.call_args.args[0].entry
        self.event._extras["cron_payload"]["kirby_intro_slot"] = "21:05"
        await self.plugin.daily_ally_intro_tool(self.event)
        second = self.plugin._daily_ally_chain.call_args.args[0].entry
        self.assertNotEqual(first["filename"], second["filename"])
        del self.plugin._intro_lock
        self.assertIn("已介绍过", await self.plugin.daily_ally_intro_tool(self.event))
        self.assertEqual(self.plugin._chain_result_with_media.await_count, 2)

    async def test_concurrent_calls_and_uncertain_send_never_repeat_card(self):
        self.plugin._chain_result_with_media.side_effect = TimeoutError()
        result = await asyncio.gather(
            *(self.plugin.daily_ally_intro_tool(self.event) for _ in range(3))
        )
        self.assertEqual(self.plugin._chain_result_with_media.await_count, 1)
        self.assertTrue(all("无需发送" in text for text in result))
        self.assertFalse(self.plugin.store.draws)

    async def test_disabled_empty_and_removed_scope_gate_before_selection(self):
        for changes in (
            {"daily_ally_intro_enabled": False},
            {"daily_ally_intro_group_whitelist": []},
            {"daily_ally_intro_group_whitelist": ["default:GroupMessage:1035995227"]},
            {"daily_ally_intro_times": ["21:05"]},
        ):
            with self.subTest(changes=changes), patch.dict(self.plugin.config, changes):
                self.assertIn(
                    "无需发送", await self.plugin.daily_ally_intro_tool(self.event)
                )
        self.plugin._daily_ally_chain.assert_not_awaited()
        self.assertFalse((self.plugin.store.root / "daily_introductions.json").exists())

    async def test_corrupt_state_does_not_send_and_next_day_slot_resets(self):
        state = IntroductionState(self.plugin.store.root / "daily_introductions.json")
        self.assertEqual(state.claim(SESSION, "2026-11-10", "12:20", [ENTRY]), ENTRY)
        self.assertIsNone(state.claim(SESSION, "2026-11-10", "12:20", [ENTRY]))
        self.assertEqual(state.claim(SESSION, "2026-11-11", "12:20", [ENTRY]), ENTRY)
        state.path.write_text("[]", encoding="utf-8")
        self.assertIn("记录不可用", await self.plugin.daily_ally_intro_tool(self.event))
        self.plugin._chain_result_with_media.assert_not_awaited()


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_preserves_other_jobs_and_retains_persona_scope(self):
        jobs = [
            SimpleNamespace(
                job_id="legacy",
                name="爱丽丝的抽卡时间",
                enabled=True,
                payload={"session": SESSION, "note": "调用kirby_catalog_draw_bot_ally"},
            ),
            SimpleNamespace(job_id="other", name="每日提醒", enabled=True, payload={}),
        ]

        async def update(job_id, **kwargs):
            job = next(job for job in jobs if job.job_id == job_id)
            job.__dict__.update(kwargs)
            return job

        async def add(**kwargs):
            job = SimpleNamespace(job_id=f"job-{len(jobs)}", **kwargs)
            jobs.append(job)
            return job

        manager = SimpleNamespace(
            list_jobs=AsyncMock(side_effect=lambda: list(jobs)),
            update_job=AsyncMock(side_effect=update),
            add_active_job=AsyncMock(side_effect=add),
        )
        sessions = [SESSION, "default:GroupMessage:1061036726"]
        ids = await sync_intro_jobs(
            manager, sessions, ["12:20", "21:05"], True, "Asia/Shanghai"
        )
        self.assertEqual(len(ids), 4)
        self.assertFalse(jobs[0].enabled)
        self.assertTrue(jobs[1].enabled)
        self.assertEqual(
            {job.cron_expression for job in jobs[2:]}, {"20 12 * * *", "5 21 * * *"}
        )
        self.assertEqual({job.payload["session"] for job in jobs[2:]}, set(sessions))
        self.assertTrue(all(job.payload["note"] == INTRO_NOTE for job in jobs[2:]))
        await sync_intro_jobs(
            manager, sessions, ["12:20", "21:05"], True, "Asia/Shanghai"
        )
        self.assertEqual(manager.add_active_job.await_count, 4)
        await sync_intro_jobs(manager, [], [], False, "Asia/Shanghai")
        self.assertTrue(jobs[1].enabled)
        self.assertTrue(
            all(not job.enabled for job in jobs if job.name.startswith(JOB_PREFIX))
        )
        self.assertEqual(
            intro_sessions([SESSION, SESSION, "123", "x:FriendMessage:y"]), [SESSION]
        )
        self.assertEqual(
            intro_times(["12:20", "21:05", "24:00", "1:02", "12:20"]),
            ["12:20", "21:05"],
        )
