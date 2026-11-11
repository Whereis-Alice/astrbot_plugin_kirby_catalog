"""Exercise registered handlers through AstrBot's actual wake stage."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot.api import message_components as Comp
from astrbot.core.pipeline.waking_check import stage as waking
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot_plugin_kirby_catalog import main

from .test_main import FakeEvent, make_plugin

COMMANDS = {
    "draw_ally": "今日盟友",
    "draw_bot_ally": "Bot今日盟友",
    "view_ally_asset": "素材 #12",
    "view_ally_description": "查看简介 #12",
    "wikirby_query_plain": "卡比百科文档 Waddle Dee",
    "fandom_query_plain": "卡比F文档 Waddle Dee",
    "shinkaku_query_plain": "卡比真格文档 161",
    "qq_reading_page": "卡比翻页 0123456789abcdef 2",
}


class RoutingEvent(FakeEvent):
    def __init__(self, text, *, private=False, mention=False, quote=False):
        messages = [Comp.Plain(text)]
        if mention:
            messages.append(Comp.At(qq="bot-id"))
        if quote:
            messages.append(Comp.Reply(id="card", sender_id="bot-id"))
        super().__init__(text, messages)
        self.private = private
        self.is_at_or_wake_command = False
        self.is_wake = False
        self.stopped = False
        self._extras = {}
        self.plugins_name = None

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return self.message_obj.message

    def get_self_id(self):
        return "bot-id"

    def get_message_type(self):
        return MessageType.FRIEND_MESSAGE if self.private else MessageType.GROUP_MESSAGE

    def is_private_chat(self):
        return self.private

    def is_button_interaction(self):
        return False

    def get_button_interaction(self):
        return None

    def get_platform_name(self):
        return "qq_official"

    def get_extra(self, key=None, default=None):
        return self._extras if key is None else self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def stop_event(self):
        self.stopped = True


class CommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handlers = [
            handler
            for handler in star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent
            )
            if handler.handler_module_path == main.__name__
            and handler.handler.__name__ in COMMANDS
        ]
        self.assertEqual({h.handler.__name__ for h in self.handlers}, set(COMMANDS))

    async def route(self, text, prefixes, *, needs_private_prefix=False, **options):
        event = RoutingEvent(text, **options)
        stage = waking.WakingCheckStage()
        stage.ctx = SimpleNamespace(
            astrbot_config={
                "wake_prefix": prefixes,
                "admins_id": [],
                "plugin_set": ["*"],
                "platform_settings": {},
            }
        )
        stage.ignore_bot_self_message = False
        stage.unique_session = False
        stage.ignore_at_all = False
        stage.disable_builtin_commands = False
        stage.no_permission_reply = True
        stage.friend_message_needs_wake_prefix = needs_private_prefix
        stage._umo_auto_name_recorder = SimpleNamespace(schedule=MagicMock())
        with (
            patch.object(
                waking.star_handlers_registry,
                "get_handlers_by_event_type",
                return_value=self.handlers,
            ),
            patch.object(
                waking.SessionPluginManager,
                "filter_handlers_by_session",
                AsyncMock(side_effect=lambda event, handlers: handlers),
            ),
        ):
            await stage.process(event)
        self.assertFalse(event.sent_chains)
        return event, {h.handler.__name__ for h in event.get_extra("activated_handlers", [])}

    async def test_all_regex_commands_follow_configured_session_prefix(self):
        for prefix in ("/", "#", "!", "莉耶芙 "):
            for name, command in COMMANDS.items():
                with self.subTest(prefix=prefix, command=command):
                    event, selected = await self.route(prefix + command, [prefix])
                    self.assertEqual(selected, {name})
                    self.assertEqual(event.message_str, command)
                    self.assertFalse(event.stopped)

    async def test_new_bot_rejects_old_bot_slash_even_when_mentioned_or_private(self):
        for command in COMMANDS.values():
            for options in ({}, {"mention": True}, {"private": True}):
                with self.subTest(command=command, options=options):
                    event, selected = await self.route("/" + command, ["#"], **options)
                    self.assertEqual(selected, set())
                    self.assertEqual(event.message_str, "/" + command)
                    if not options:
                        self.assertTrue(event.stopped)

    async def test_plain_draw_is_supported_while_other_commands_require_wake(self):
        for name, command in COMMANDS.items():
            with self.subTest(command=command):
                event, selected = await self.route(command, ["#"])
                if name == "draw_ally":
                    self.assertEqual(selected, {name})
                    self.assertFalse(event.stopped)
                else:
                    self.assertEqual(selected, set())
                    self.assertTrue(event.stopped)
                for options in ({"mention": True}, {"quote": True}):
                    _, selected = await self.route(command, ["#"], **options)
                    self.assertEqual(selected, {name})

    async def test_pure_draw_aliases_activate_once_without_prefix_or_mention(self):
        for prefixes in (["#"], ["/"], ["莉耶芙 "]):
            for text in ("今日盟友", "抽盟友", "抽取盟友"):
                with self.subTest(prefixes=prefixes, text=text):
                    event, selected = await self.route(text, prefixes)
                    self.assertEqual(selected, {"draw_ally"})
                    self.assertEqual(len(event.get_extra("activated_handlers")), 1)
                    self.assertFalse(event.is_at_or_wake_command)
                    self.assertTrue(event.is_wake)
                    self.assertFalse(event.stopped)

    async def test_pure_draw_still_checks_whitelist_before_reading_user_data(self):
        plugin = make_plugin({"id": 12, "filename": "Waddle_Dee.png"})
        plugin.config = {"daily_ally_group_whitelist": ["another-group"]}
        plugin._collection_group_id = MagicMock(return_value="group-1")
        plugin._data_context = MagicMock(side_effect=AssertionError("Unlisted draw read user data"))
        event, selected = await self.route("今日盟友", ["#"])
        self.assertEqual(selected, {"draw_ally"})
        self.assertEqual([item async for item in plugin.draw_ally(event)], [])
        self.assertTrue(event.stopped)
        plugin._data_context.assert_not_called()
        self.assertEqual(plugin.store.draws, {})

    async def test_multiple_prefixes_and_empty_prefix_follow_astrbot_rules(self):
        for text, prefixes in (
            ("#卡比真格文档 161", ["#", "!"]),
            ("!卡比真格文档 161", ["#", "!"]),
            ("卡比真格文档 161", [""]),
        ):
            with self.subTest(text=text, prefixes=prefixes):
                _, selected = await self.route(text, prefixes)
                self.assertEqual(selected, {"shinkaku_query_plain"})

    async def test_private_commands_follow_private_wake_prefix_setting(self):
        _, selected = await self.route("查看简介 #12", ["#"], private=True)
        self.assertEqual(selected, {"view_ally_description"})
        _, selected = await self.route(
            "查看简介 #12", ["#"], private=True, needs_private_prefix=True
        )
        self.assertEqual(selected, set())
        _, selected = await self.route(
            "#查看简介 #12", ["#"], private=True, needs_private_prefix=True
        )
        self.assertEqual(selected, {"view_ally_description"})

    async def test_official_document_commands_stop_before_lookup_and_translation(self):
        plugin = main.KirbyCatalogPlugin.__new__(main.KirbyCatalogPlugin)
        for name, impl, command in (
            ("wikirby_query_plain", "_wikirby_query_impl", "卡比百科文档 Kirby"),
            ("fandom_query_plain", "_fandom_query_impl", "卡比Fandom文档 Kirby"),
            ("shinkaku_query_plain", "_shinkaku_query_impl", "卡比真格文档 161"),
        ):
            with self.subTest(command=command):
                event = RoutingEvent(command)
                lookup = MagicMock()
                setattr(plugin, impl, lookup)
                results = [item async for item in getattr(plugin, name)(event)]
                self.assertEqual(results, [])
                self.assertTrue(event.stopped)
                self.assertFalse(event.sent_chains)
                lookup.assert_not_called()

    async def test_supported_platform_document_and_official_text_queries_still_dispatch(self):
        plugin = main.KirbyCatalogPlugin.__new__(main.KirbyCatalogPlugin)

        async def result(event):
            yield "received"

        for name, impl, command in (
            ("wikirby_query_plain", "_wikirby_query_impl", "卡比百科"),
            ("fandom_query_plain", "_fandom_query_impl", "卡比F"),
            ("shinkaku_query_plain", "_shinkaku_query_impl", "卡比真格"),
        ):
            for suffix, platform in (("文档", "aiocqhttp"), ("文本", "qq_official")):
                with self.subTest(command=command, suffix=suffix, platform=platform):
                    event = RoutingEvent(command + suffix + " Kirby")
                    event.get_platform_name = lambda platform=platform: platform
                    lookup = MagicMock(side_effect=result)
                    setattr(plugin, impl, lookup)
                    results = [item async for item in getattr(plugin, name)(event)]
                    self.assertEqual(results, ["received"])
                    self.assertFalse(event.stopped)
                    lookup.assert_called_once_with(event)
