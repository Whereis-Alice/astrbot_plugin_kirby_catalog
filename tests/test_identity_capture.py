"""One-time nickname capture and migration feedback without touching progress."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api import message_components as Comp
from astrbot_plugin_kirby_catalog.identity_capture import (
    parse_member_names,
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
        self.event.bot = SimpleNamespace(call_action=AsyncMock(return_value=MEMBERS))

    async def test_one_group_command_fetches_once_and_reports_summary(self):
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.event.bot.call_action.assert_awaited_once_with(
            "get_group_member_list", group_id=123
        )
        self.assertIn("采集 2 位", result[-1])
        self.assertIn("保持不变", result[-1])

    async def test_all_groups_filters_official_ids_and_continues_after_api_error(self):
        self.store.save_group("124", {"456": record()})
        self.store.save_group("official-group", {"openid": record()})
        self.event.message_str = "采集盟友迁移资料 全部"
        self.event.bot.call_action.side_effect = [MEMBERS, RuntimeError("unavailable")]
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertEqual(self.event.bot.call_action.await_count, 2)
        self.assertIn("成功 1/2", result[-1])
        self.assertIn("124", result[-1])

    async def test_private_command_accepts_explicit_group_id(self):
        self.event.message_obj.group_id = ""
        self.event.message_str = "采集盟友迁移资料 123"
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("采集完成", result[-1])

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
        async def fetch(*args, **kwargs):
            fresh = self.store.load_group("123")
            fresh["456"]["unlocked"].append(
                {"ally_filename": "b.png", "unlock_date": "2026-11-11"}
            )
            self.store.save_group("123", fresh)
            return MEMBERS

        self.event.bot.call_action.side_effect = fetch
        [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertEqual(len(self.store.load_group("123")["456"]["unlocked"]), 2)

    async def test_concurrent_capture_is_rejected_and_closing_releases_lock(self):
        running = self.plugin.capture_ally_identity_names(self.event)
        await anext(running)
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("已有", result[0])
        await running.aclose()
        result = [r async for r in self.plugin.capture_ally_identity_names(self.event)]
        self.assertIn("采集完成", result[-1])


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
