"""One-time nickname capture and migration feedback without touching progress."""

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api import message_components as Comp
from astrbot_plugin_kirby_catalog.identity_capture import (
    FreshIdentityCapture,
    capture_report_lines,
    parse_member_names,
    read_capture_report,
    save_capture_report,
    save_fresh_record,
    save_member_names,
)
from astrbot_plugin_kirby_catalog.qq_official import identity_key

from .test_identity_links import StoreFixture, record
from .test_main import FakeEvent, make_plugin
from .test_qq_official import OfficialEvent

MEMBERS = [
    {"user_id": 456, "nickname": "资料昵称", "card": "群名片"},
    {"user_id": 789, "nickname": "另一个资料昵称", "card": ""},
    {"user_id": 999, "nickname": "没有收藏的成员", "card": ""},
]


async def api_info(action, **params):
    user = params["user_id"]
    data = {"user_id": user, "nickname": f"接口昵称{user}"}
    if action == "get_group_member_info":
        data.update(group_id=params["group_id"], card=f"接口名片{user}")
    elif action != "get_stranger_info":
        raise AssertionError(f"Unexpected API: {action}")
    return {"status": "ok", "retcode": 0, "data": data}


class FreshApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(call_action=AsyncMock(side_effect=api_info))
        self.client = FreshIdentityCapture(self.bot)
        self.addAsyncCleanup(self.client.close)

    async def test_queries_both_apis_with_no_cache_and_prefers_profile(self):
        async def query(action, **params):
            data = await api_info(action, **params)
            data["data"]["nickname"] = (
                "资料昵称" if action == "get_stranger_info" else "群接口昵称"
            )
            return data

        self.bot.call_action.side_effect = query
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "full")
        self.assertEqual(row["profile"], "资料昵称")
        self.assertEqual(row["profile_source"], "get_stranger_info")
        self.bot.call_action.assert_any_await(
            "get_stranger_info", user_id=456, no_cache=True
        )
        self.bot.call_action.assert_any_await(
            "get_group_member_info", user_id=456, group_id=123, no_cache=True
        )

    async def test_profile_is_queried_once_per_qq_and_card_once_per_group(self):
        rows = await asyncio.gather(
            self.client.record("123", "456", record()),
            self.client.record("124", "456", record()),
        )
        self.assertTrue(all(row["status"] == "full" for row in rows))
        self.assertEqual(
            self.client.calls, {"get_group_member_info": 2, "get_stranger_info": 1}
        )
        next_round = FreshIdentityCapture(self.bot)
        try:
            await next_round.record("123", "456", record())
            self.assertEqual(next_round.calls["get_stranger_info"], 1)
        finally:
            await next_round.close()

    async def test_member_failure_still_fetches_profile_without_inventing_card(self):
        async def query(action, **params):
            if action == "get_group_member_info":
                raise RuntimeError("private token")
            return await api_info(action, **params)

        self.bot.call_action.side_effect = query
        old = record()
        old["identity_names"] = {"profile": "旧资料", "group_card": "旧名片"}
        row = await self.client.record("123", "456", old)
        self.assertEqual(row["status"], "profile_only")
        self.assertEqual(row["group_card"], "旧名片")
        self.assertEqual(row["group_card_source"], "previous")
        self.assertEqual(row["member_error"], "RuntimeError")
        self.assertNotIn("private token", str(row))

    async def test_failed_profile_can_use_this_round_member_nickname(self):
        async def query(action, **params):
            return (
                {"status": "failed", "retcode": 100}
                if action == "get_stranger_info"
                else await api_info(action, **params)
            )

        self.bot.call_action.side_effect = query
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "full")
        self.assertEqual(row["profile_source"], "get_group_member_info")
        self.assertIn("retcode=100", row["profile_error"])

    async def test_both_fail_never_count_old_profile_as_fetched(self):
        self.bot.call_action.side_effect = ValueError("private authentication detail")
        old = record()
        old["identity_names"] = {"profile": "旧资料", "group_card": "旧名片"}
        row = await self.client.record("123", "456", old)
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["profile"])
        self.assertEqual(row["profile_error"], "ValueError")
        self.assertNotIn("authentication", str(row))

    async def test_protocol_failure_reports_code_without_sensitive_message(self):
        from aiocqhttp.exceptions import ActionFailed

        self.bot.call_action.side_effect = ActionFailed(
            {"retcode": 1200, "message": "private token"}
        )
        row = await self.client.record("123", "456", record())
        self.assertIn("retcode=1200", row["member_error"])
        self.assertNotIn("private token", str(row))

    async def test_non_string_nickname_is_not_saved_as_identity(self):
        self.bot.call_action.side_effect = None
        self.bot.call_action.return_value = {
            "user_id": 456,
            "nickname": {"name": "invalid"},
            "card": "",
        }
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["profile"])

    async def test_wrong_qq_response_is_rejected(self):
        self.bot.call_action.side_effect = None
        self.bot.call_action.return_value = {
            "user_id": 999,
            "nickname": "其他人",
            "card": "其他名片",
        }
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "failed")
        self.assertIn("其他 QQ", row["member_error"])

    async def test_wrong_group_card_is_rejected(self):
        async def query(action, **params):
            data = await api_info(action, **params)
            if action == "get_group_member_info":
                data["data"]["group_id"] = 999
            return data

        self.bot.call_action.side_effect = query
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "profile_only")
        self.assertIn("其他群", row["member_error"])

    async def test_empty_profile_does_not_substitute_card(self):
        self.bot.call_action.side_effect = None
        self.bot.call_action.return_value = {
            "user_id": 456,
            "nickname": " ",
            "card": "只返回名片",
        }
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["profile"])

    async def test_missing_card_is_partial_but_empty_card_is_full(self):
        self.bot.call_action.side_effect = None
        self.bot.call_action.return_value = {"user_id": 456, "nickname": "资料"}
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "profile_only")
        self.bot.call_action.return_value["card"] = ""
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["status"], "full")
        self.assertEqual(row["group_card"], "")

    async def test_timeout_is_reported_and_concurrency_is_limited(self):
        self.bot.call_action.side_effect = TimeoutError()
        row = await self.client.record("123", "456", record())
        self.assertEqual(row["member_error"], "接口超时")
        active, peak = 0, 0

        async def query(action, **params):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return await api_info(action, **params)

        self.bot.call_action.side_effect = query
        await asyncio.gather(
            *(
                self.client.record("123", str(user), record())
                for user in range(600, 605)
            )
        )
        self.assertLessEqual(peak, 2)

    async def test_close_cancels_pending_profile_tasks(self):
        entered = asyncio.Event()

        async def query(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.bot.call_action.side_effect = query
        task = asyncio.create_task(self.client._profile("456"))
        await entered.wait()
        await self.client.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(task.done() for task in self.client.profiles.values()))

    async def test_cancel_one_record_does_not_cancel_shared_profile(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def query(action, **params):
            if action == "get_stranger_info":
                entered.set()
                await release.wait()
            return await api_info(action, **params)

        self.bot.call_action.side_effect = query
        first = asyncio.create_task(self.client.record("123", "456", record()))
        await entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(self.client.record("124", "456", record()))
        release.set()
        self.assertEqual((await second)["status"], "full")


class FreshStoreTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def row(self):
        client = FreshIdentityCapture(
            SimpleNamespace(call_action=AsyncMock(side_effect=api_info))
        )
        try:
            return await client.record(
                "123", "456", self.store.load_group("123")["456"]
            )
        finally:
            await client.close()

    async def test_fresh_write_preserves_draws_collection_and_other_users(self):
        row = await self.row()
        current = self.store.load_group("123")
        current["456"]["unlocked"].append(
            {"ally_filename": "b.png", "unlock_date": "2026-11-11"}
        )
        self.store.save_group("123", current)
        self.store.set_draw_count("123", "456", 3)
        self.assertEqual(save_fresh_record(self.store, row), (True, True))
        after = self.store.load_group("123")
        after["456"].pop("identity_names")
        self.assertEqual(after, current)
        self.assertEqual(self.store.draw_count("123", "456"), 3)

    async def test_partial_write_keeps_concurrently_updated_card(self):
        row = await self.row()
        row.update(
            status="profile_only", group_card_source="previous", group_card="过时名片"
        )
        current = self.store.load_group("123")
        current["456"]["identity_names"] = {
            "profile": "旧资料",
            "group_card": "并发新名片",
        }
        self.store.save_group("123", current)
        save_fresh_record(self.store, row)
        saved = self.store.load_group("123")["456"]["identity_names"]
        self.assertEqual(saved["group_card"], "并发新名片")
        self.assertFalse(saved["group_card_current"])

    async def test_deleted_record_is_not_recreated(self):
        row = await self.row()
        current = self.store.load_group("123")
        del current["456"]
        self.store.save_group("123", current)
        self.assertEqual(save_fresh_record(self.store, row), (False, False))
        self.assertEqual(row["status"], "record_removed")
        self.assertNotIn("456", self.store.load_group("123"))

    async def test_failed_row_does_not_overwrite_previous_names(self):
        row = await self.row()
        row.update(profile="", status="failed")
        before = self.store.load_group("123")
        self.assertEqual(save_fresh_record(self.store, row), (False, False))
        self.assertEqual(self.store.load_group("123"), before)

    async def test_repeat_refresh_writes_timestamp_without_claiming_name_change(self):
        row = await self.row()
        save_fresh_record(self.store, row)
        row["captured_at"] = "2026-11-12T00:00:00+00:00"
        self.assertEqual(save_fresh_record(self.store, row), (True, False))

    async def test_report_persists_complete_records_and_filters_failures(self):
        full = await self.row()
        failed = dict(
            full,
            group_id="124",
            user_id="999",
            status="failed",
            profile="",
            member_error="接口超时",
        )
        report = {
            "id": uuid.uuid4().hex,
            "status": "complete",
            "records": [full, failed],
        }
        path = save_capture_report(self.store, report)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
        self.assertEqual(read_capture_report(self.store), report)
        lines = "\n".join(capture_report_lines(report))
        self.assertIn("QQ 999", lines)
        self.assertIn("接口超时", lines)
        self.assertNotIn("QQ 456", lines)
        self.assertIn(
            "QQ 456", "\n".join(capture_report_lines(report, all_records=True))
        )
        self.assertNotIn(
            "QQ 999",
            "\n".join(capture_report_lines(report, group="123", all_records=True)),
        )

    async def test_report_missing_corrupt_or_invalid_id_is_handled(self):
        with self.assertRaisesRegex(ValueError, "还没有"):
            read_capture_report(self.store)
        with self.assertRaisesRegex(ValueError, "ID"):
            save_capture_report(self.store, {"id": "../outside", "records": []})
        path = self.store.root / "identity-capture/latest.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"records": [null]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "格式"):
            read_capture_report(self.store)


class CaptureStoreTests(StoreFixture, unittest.TestCase):
    def test_records_both_names_without_creating_users_or_changing_progress(self):
        before = self.store.load_group("123")
        self.store.set_draw_count("123", "456", 3)
        result = save_member_names(self.store, "123", parse_member_names(MEMBERS))
        after = self.store.load_group("123")
        self.assertEqual(
            result, {"records": 2, "captured": 2, "updated": 2, "missing": 0}
        )
        self.assertEqual(after["456"]["identity_names"]["profile"], "资料昵称")
        self.assertEqual(after["456"]["identity_names"]["group_card"], "群名片")
        self.assertNotIn("999", after)
        for user in before:
            after[user].pop("identity_names", None)
        self.assertEqual(after, before)
        self.assertEqual(self.store.draw_count("123", "456"), 3)

    def test_missing_members_keep_their_previous_names(self):
        old = self.store.load_group("123")
        old["789"]["identity_names"] = {
            "profile": "已离群旧资料",
            "group_card": "旧名片",
        }
        self.store.save_group("123", old)
        result = save_member_names(self.store, "123", parse_member_names(MEMBERS[:1]))
        self.assertEqual(result["missing"], 1)
        self.assertEqual(self.store.load_group("123")["789"], old["789"])

    def test_failed_or_invalid_payload_is_rejected(self):
        for payload in (
            {"retcode": 100, "data": MEMBERS},
            {"status": "failed"},
            {"data": {}},
            None,
        ):
            with self.assertRaises(ValueError):
                parse_member_names(payload)

    def test_wrapped_payload_and_empty_card_are_supported(self):
        parsed = parse_member_names({"status": "ok", "retcode": 0, "data": MEMBERS})
        self.assertEqual(parsed["789"]["group_card"], "")

    def test_conflicting_duplicate_member_id_is_skipped(self):
        conflicting = MEMBERS + [{"user_id": 456, "nickname": "冲突昵称", "card": ""}]
        self.assertNotIn("456", parse_member_names(conflicting))

    def test_empty_profile_never_uses_group_card_as_profile(self):
        self.assertFalse(
            parse_member_names([{"user_id": 456, "nickname": "", "card": "只有名片"}])
        )

    def test_repeat_capture_on_same_day_has_no_writes(self):
        first = save_member_names(self.store, "123", parse_member_names(MEMBERS))
        second = save_member_names(self.store, "123", parse_member_names(MEMBERS))
        self.assertEqual(first["updated"], 2)
        self.assertEqual(second["updated"], 0)


class CaptureCommandTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.plugin = make_plugin(self.store.resolve_entry("a.png"))
        self.plugin.store = self.store
        self.event = FakeEvent("采集盟友迁移资料", group_id="123")
        self.event.get_platform_name = lambda: "aiocqhttp"
        self.event.is_admin = lambda: True
        self.event.bot = SimpleNamespace(call_action=AsyncMock(side_effect=api_info))

    async def test_one_group_command_fetches_each_user_and_reports_summary(self):
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertEqual(self.event.bot.call_action.await_count, 4)
        self.assertTrue(
            all(
                call.kwargs["no_cache"]
                for call in self.event.bot.call_action.await_args_list
            )
        )
        self.assertIn("2 条账号记录（2 个不同 QQ）", result[-1])
        self.assertIn("齐全 2 条", result[-1])
        self.assertIn("保持不变", result[-1])
        self.assertEqual(len(read_capture_report(self.store)["records"]), 2)

    async def test_all_groups_filters_official_ids_and_continues_after_api_error(self):
        self.store.save_group("124", {"654": record()})
        self.store.save_group("official-group", {"openid": record()})
        self.event.message_str = "采集盟友迁移资料 全部"

        async def query(action, **params):
            if action == "get_group_member_info" and params["group_id"] == 124:
                raise RuntimeError("unavailable")
            return await api_info(action, **params)

        self.event.bot.call_action.side_effect = query
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertEqual(self.event.bot.call_action.await_count, 6)
        self.assertIn("仅取得资料昵称 1 条", "\n".join(result))
        self.assertNotIn("QQ 654", "\n".join(result))
        rows = read_capture_report(self.store)["records"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["status"], "profile_only")

    async def test_private_command_accepts_explicit_group_id(self):
        self.event.message_obj.group_id = ""
        self.event.is_private_chat = lambda: True
        self.event.message_str = "采集盟友迁移资料 123"
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("查询完成", result[-1])

    async def test_permission_and_platform_are_checked_before_api(self):
        self.event.is_admin = lambda: False
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("仅限", result[0])
        self.event.is_admin = lambda: True
        self.event.get_platform_name = lambda: "qq_official"
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("旧服务器", result[0])
        self.event.bot.call_action.assert_not_called()

    async def test_draws_while_api_is_pending_are_preserved(self):
        changed = False

        async def fetch(action, **kwargs):
            nonlocal changed
            fresh = self.store.load_group("123")
            if not changed:
                changed = True
                fresh["456"]["unlocked"].append(
                    {"ally_filename": "b.png", "unlock_date": "2026-11-11"}
                )
                self.store.save_group("123", fresh)
            return await api_info(action, **kwargs)

        self.event.bot.call_action.side_effect = fetch
        [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertEqual(len(self.store.load_group("123")["456"]["unlocked"]), 2)

    async def test_concurrent_capture_is_rejected_and_closing_releases_lock(self):
        running = self.plugin.capture_ally_identity_names(self.event)
        await anext(running)
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("已有", result[0])
        await running.aclose()
        report = read_capture_report(self.store)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(len(report["records"]), 2)
        self.assertTrue(all(row["status"] == "pending" for row in report["records"]))
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("查询完成", result[-1])

    async def test_failed_records_are_listed_and_old_names_are_not_successes(self):
        self.event.bot.call_action.side_effect = RuntimeError("unavailable")
        result = "\n".join(
            [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        )
        self.assertIn("未取得资料昵称 2 条", result)
        for user in ("456", "789"):
            self.assertIn(f"QQ {user}", result)
        self.assertIn("旧显示名：旧昵称", result)
        self.assertIn("RuntimeError", result)
        self.assertNotIn("已离群", result)

    async def test_report_survives_plugin_restart_and_private_scope_sees_all(self):
        self.store.save_group("124", {"654": record()})
        self.event.message_str = "采集盟友迁移资料 全部"
        self.event.bot.call_action.side_effect = RuntimeError()
        [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.plugin = make_plugin(self.store.resolve_entry("a.png"))
        self.plugin.store = self.store
        self.event.message_str = "迁移采集报告 全部"
        result = "\n".join(
            [r async for r in self.plugin.show_identity_capture_report(self.event)]
        )
        self.assertIn("QQ 456", result)
        self.assertNotIn("QQ 654", result)
        self.event.message_obj.group_id = ""
        self.event.is_private_chat = lambda: True
        result = "\n".join(
            [r async for r in self.plugin.show_identity_capture_report(self.event)]
        )
        self.assertIn("QQ 654", result)

    async def test_report_permission_and_missing_report(self):
        self.event.is_admin = lambda: False
        result = [r async for r in self.plugin.show_identity_capture_report(self.event)]
        self.assertIn("仅限", result[0])
        self.event.is_admin = lambda: True
        result = [r async for r in self.plugin.show_identity_capture_report(self.event)]
        self.assertIn("还没有", result[0])

    async def test_cancel_in_flight_records_keeps_report_and_releases_lock(self):
        started = asyncio.Event()
        active = 0

        async def query(*args, **kwargs):
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        self.event.bot.call_action.side_effect = query

        async def run():
            return [
                r async for r in self.plugin.capture_ally_identity_names(self.event)
            ]

        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(active, 0)
        self.assertFalse(self.plugin._identity_capture_lock.locked())
        report = read_capture_report(self.store)
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(len(report["records"]), report["target_records"])


class MigrationFeedbackTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.plugin = make_plugin(self.store.resolve_entry("a.png"))
        self.plugin.store = self.store
        self.plugin.config = {
            "daily_draw_limit": 5,
            "draw_cooldown_seconds": 0,
            "qq_official_auto_migrate_by_name": True,
        }
        self.plugin._daily_ally_chain = AsyncMock(return_value=[])
        self.plugin._chain_result_with_media = AsyncMock(return_value="sent")
        self.event = OfficialEvent()
        self.bot = identity_key(self.event)
        self.bind_group(self.bot, "group-openid")

    async def draw_caption(self):
        [r async for r in self.plugin._draw_ally_impl(self.event)]
        return "".join(
            item.text
            for item in self.plugin._chain_result_with_media.call_args.args[1]
            if isinstance(item, Comp.Plain)
        )

    async def test_missing_name_notice_shown_once_and_no_false_restore(self):
        first = await self.draw_caption()
        second = await self.draw_caption()
        self.assertIn("没有提供", first)
        self.assertNotIn("没有提供", second)
        self.assertNotEqual(self.plugin._data_context(self.event), ("123", "456"))

    async def test_missing_legacy_capture_and_changed_reason_are_explained(self):
        self.event.message_obj.raw_message.author.username = "新昵称"
        self.assertIn("尚未采集", await self.draw_caption())
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "另一个昵称"}
        self.store.save_group("123", old)
        self.assertIn("没有找到", await self.draw_caption())

    async def test_same_name_never_selects_an_arbitrary_old_user(self):
        old = self.store.load_group("123")
        for user in ("456", "789"):
            old[user]["identity_names"] = {"profile": "同名"}
        self.store.save_group("123", old)
        self.event.message_obj.raw_message.author.username = "同名"
        self.assertIn("多个相同", await self.draw_caption())

    async def test_success_message_once_and_later_draws_do_not_merge_again(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称"}
        self.store.save_group("123", old)
        self.event.message_obj.raw_message.author.username = "资料昵称"
        self.assertIn("已恢复", await self.draw_caption())
        self.assertNotIn("已恢复", await self.draw_caption())
        self.assertEqual(self.store.draw_count("123", "456"), 2)

    async def test_rich_card_summary_keeps_migration_notice(self):
        self.plugin.config["ally_draw_output_mode"] = "精美卡片"
        image = Comp.Image.fromFileSystem("D:/fake/kirby-ally-card-test.png")
        self.plugin._daily_ally_chain.return_value = [image]
        await self.draw_caption()
        view = self.plugin._chain_result_with_media.call_args.kwargs["qq_view"]
        self.assertTrue(view.replace_caption)
        self.assertIn("没有提供", view.summary)
