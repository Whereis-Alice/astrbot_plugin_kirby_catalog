import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_kirby_catalog.qq_official import identity_key

from .test_identity_links import StoreFixture
from .test_main import FakeEvent, make_plugin
from .test_qq_official import OfficialEvent


class DailyDrawWhitelistTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.plugin = make_plugin(self.store.resolve_entry("a.png"))
        self.plugin.store = self.store
        self.plugin.config = {"daily_ally_group_whitelist": ["123"]}
        self.plugin._draw_for_identity = AsyncMock(return_value=(None, "allowed"))

    async def test_outside_group_is_silent_without_store_or_migration_changes(self):
        event = FakeEvent("今日盟友", group_id="124")
        event.stop_event = Mock()
        with patch.object(
            self.plugin,
            "_data_context",
            side_effect=AssertionError("must not recover identities"),
        ):
            self.assertEqual([r async for r in self.plugin._draw_ally_impl(event)], [])
        self.plugin._draw_for_identity.assert_not_called()
        event.stop_event.assert_called_once()

    async def test_allowed_group_reaches_the_normal_draw_flow(self):
        event = FakeEvent("今日盟友", group_id="123", sender_id="456")
        self.assertEqual(
            [r async for r in self.plugin._draw_ally_impl(event)], ["allowed"]
        )
        self.plugin._draw_for_identity.assert_awaited_once()

    async def test_bot_command_outside_group_is_silent_and_tool_does_not_draw(self):
        event = FakeEvent("Bot今日盟友", group_id="124")
        self.assertEqual([r async for r in self.plugin.draw_bot_ally(event)], [])
        self.assertIn("未抽取或发送", await self.plugin.draw_bot_ally_tool(event))
        self.plugin._draw_for_identity.assert_not_called()

    def test_empty_default_allows_all_groups_and_private_sessions(self):
        for config in (
            {},
            {"daily_ally_group_whitelist": []},
            {"daily_ally_group_whitelist": [" "]},
        ):
            self.plugin.config = config
            self.assertTrue(
                self.plugin._daily_draw_group_allowed(
                    FakeEvent("今日盟友", group_id="elsewhere")
                )
            )
            self.assertTrue(
                self.plugin._daily_draw_group_allowed(
                    FakeEvent("今日盟友", group_id="")
                )
            )

    def test_populated_whitelist_blocks_private_and_invalid_values(self):
        self.assertFalse(
            self.plugin._daily_draw_group_allowed(FakeEvent("今日盟友", group_id=""))
        )
        self.plugin.config["daily_ally_group_whitelist"] = {"bad": "config"}
        self.assertFalse(
            self.plugin._daily_draw_group_allowed(FakeEvent("今日盟友", group_id="123"))
        )

    def test_live_nested_config_accepts_trimmed_group_numbers(self):
        self.plugin.config = {
            "draw_settings": {"daily_ally_group_whitelist": [" 123 "]}
        }
        self.assertTrue(
            self.plugin._daily_draw_group_allowed(FakeEvent("今日盟友", group_id="123"))
        )
        self.assertFalse(
            self.plugin._daily_draw_group_allowed(FakeEvent("今日盟友", group_id="124"))
        )

    def test_official_matches_raw_openid_or_linked_old_group(self):
        event = OfficialEvent()
        self.bind_group(identity_key(event), "group-openid")
        self.assertTrue(self.plugin._daily_draw_group_allowed(event))
        self.plugin.config["daily_ally_group_whitelist"] = ["group-openid"]
        self.assertTrue(self.plugin._daily_draw_group_allowed(event))
        self.plugin.config["daily_ally_group_whitelist"] = ["other-group"]
        self.assertFalse(self.plugin._daily_draw_group_allowed(event))
