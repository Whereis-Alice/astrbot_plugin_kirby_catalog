"""Daily draw card routing, saved-result safety and quoted lookup regressions."""

import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from astrbot.api import message_components as Comp
from astrbot_plugin_kirby_catalog import main as main_module
from astrbot_plugin_kirby_catalog.main import AllyDrawOutcome
from PIL import Image

from .test_main import FakeEvent, make_plugin


class DailyDrawCardOutputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.entry = {
            "filename": "Waddle_Dee.png",
            "id": 12,
            "name": "瓦豆鲁迪",
            "page_title": "Waddle Dee",
            "source": "星之卡比",
        }
        self.plugin = make_plugin(self.entry)
        self.plugin.store.root = Path(self.temporary.name)
        image = BytesIO()
        Image.new("RGBA", (48, 48), (250, 160, 50, 255)).save(image, format="PNG")
        self.plugin.store.asset_bytes = lambda _entry, _download=False: image.getvalue()
        self.outcome = AllyDrawOutcome(
            self.entry, remaining=2, repeated=False, pity=False
        )
        self.text = "自定义今日文案\n简介：这是完整的盟友说明。"

    def render(self, data, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 48), "pink").save(output, format="PNG")
        return output

    async def test_existing_default_and_unknown_setting_keep_original_output(self):
        for mode in (None, "未知方式"):
            with self.subTest(mode=mode):
                self.plugin.config = (
                    {} if mode is None else {"ally_draw_output_mode": mode}
                )
                with patch.object(main_module, "render_ally_draw_card") as renderer:
                    chain = await self.plugin._daily_ally_chain(
                        self.outcome, "收藏者", self.text
                    )
                renderer.assert_not_called()
                self.assertEqual(chain[0].text, self.text)
                self.assertIsInstance(chain[-1], Comp.Image)

    async def test_card_caption_preserves_exact_quoted_catalogue_lookup(self):
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}
        with patch.object(
            main_module, "render_ally_draw_card", side_effect=self.render
        ):
            chain = await self.plugin._daily_ally_chain(
                self.outcome, "收藏者", self.text
            )
        self.assertEqual(chain[0].text, "今日盟友：瓦豆鲁迪，图鉴编号 #12")
        self.assertEqual(len(chain), 2)
        quoted = Comp.Reply(id="card-message", chain=chain)
        event = FakeEvent("卡比百科", [quoted])
        self.assertEqual(self.plugin._quoted_target(event), "12")
        self.assertEqual(
            self.plugin._wikirby_query_parts(event), ("Waddle Dee", False, "")
        )

    async def test_card_plus_text_retains_full_custom_message(self):
        self.plugin.config = {"ally_draw_output_mode": "卡片＋文字"}
        with patch.object(
            main_module, "render_ally_draw_card", side_effect=self.render
        ):
            chain = await self.plugin._daily_ally_chain(
                self.outcome, "收藏者", self.text
            )
        self.assertEqual(chain[0].text, self.text)
        self.assertIsInstance(chain[-1], Comp.Image)

    async def test_payload_honours_theme_description_hint_and_draw_state(self):
        self.plugin.config = {
            "ally_draw_output_mode": "精美卡片",
            "ally_draw_card_theme": "银河档案",
            "ally_description_enabled": False,
            "ally_wiki_hint_enabled": False,
        }
        outcome = AllyDrawOutcome(
            self.entry,
            remaining=0,
            repeated=True,
            pity=True,
            existing_today=True,
            all_unlocked=True,
        )
        with (
            patch.object(
                main_module,
                "build_ally_draw_card_data",
                side_effect=lambda _entry, **kwargs: kwargs,
            ) as builder,
            patch.object(main_module, "render_ally_draw_card", side_effect=self.render),
        ):
            await self.plugin._daily_ally_chain(outcome, "真实收藏者", self.text)
        payload = builder.call_args.kwargs
        self.assertEqual(payload["theme"], "银河档案")
        self.assertEqual(payload["nickname"], "真实收藏者")
        self.assertEqual(payload["remaining"], 0)
        self.assertEqual(payload["description"], "")
        self.assertEqual(payload["wiki_hint"], "")
        for state in ("repeated", "pity", "existing_today", "all_unlocked"):
            self.assertTrue(payload[state])
        self.assertEqual(builder.call_args.args[0]["name_en"], "Waddle Dee")
        self.assertEqual(builder.call_args.args[0]["name"], "瓦豆鲁迪")

    async def test_card_description_respects_existing_length_limit(self):
        self.plugin.config = {
            "ally_draw_output_mode": "精美卡片",
            "ally_description_max_chars": 15,
        }
        with (
            patch.object(
                main_module,
                "build_ally_draw_card_data",
                side_effect=lambda _entry, **kwargs: kwargs,
            ) as builder,
            patch.object(main_module, "render_ally_draw_card", side_effect=self.render),
        ):
            await self.plugin._daily_ally_chain(self.outcome, "收藏者", self.text)
        self.assertEqual(
            builder.call_args.kwargs["description"],
            self.plugin._truncate_ally_description(self.plugin.store.description, 15),
        )

    async def test_render_failure_keeps_committed_draw_and_does_not_redraw(self):
        self.plugin.config = {
            "ally_draw_output_mode": "精美卡片",
            "draw_cooldown_seconds": 0,
        }
        self.plugin._chain_result_with_media = AsyncMock(
            side_effect=lambda _event, chain, **kwargs: chain
        )

        def fail_after_save(_data, _output):
            self.assertFalse(self.plugin._draw_lock.locked())
            self.assertEqual(sum(self.plugin.store.draws.values()), 1)
            self.assertEqual(
                self.plugin.store.group["user-1"]["current"]["ally_filename"],
                self.entry["filename"],
            )
            raise RuntimeError("encoder unavailable")

        with patch.object(
            main_module, "render_ally_draw_card", side_effect=fail_after_save
        ):
            results = [
                result async for result in self.plugin.draw_ally(FakeEvent("今日盟友"))
            ]
        self.assertEqual(sum(self.plugin.store.draws.values()), 1)
        self.assertEqual(len(self.plugin.store.group["user-1"]["unlocked"]), 1)
        self.assertIn("今日剩余次数：2", results[0][0].text)
        self.assertIsInstance(results[0][-1], Comp.Image)

    async def test_empty_renderer_result_falls_back_to_original_message(self):
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}
        with patch.object(main_module, "render_ally_draw_card", return_value=None):
            chain = await self.plugin._daily_ally_chain(
                self.outcome, "收藏者", self.text
            )
        self.assertEqual(chain[0].text, self.text)
        self.assertIsInstance(chain[-1], Comp.Image)

    async def test_bot_command_and_tool_share_card_mode_and_bot_name(self):
        self.plugin.config = {
            "ally_draw_output_mode": "精美卡片",
            "bot_draw_nickname": "爱丽丝",
            "bot_draw_llm_vision_enabled": False,
        }
        self.plugin._draw_bot_ally = AsyncMock(return_value=(self.outcome, None))
        self.plugin._chain_result_with_media = AsyncMock(
            side_effect=lambda _event, chain, **kwargs: chain
        )
        with (
            patch.object(
                main_module,
                "build_ally_draw_card_data",
                side_effect=lambda _entry, **kwargs: kwargs,
            ) as builder,
            patch.object(main_module, "render_ally_draw_card", side_effect=self.render),
        ):
            results = [
                result
                async for result in self.plugin.draw_bot_ally(FakeEvent("Bot今日盟友"))
            ]
            tool_result = await self.plugin.draw_bot_ally_tool(FakeEvent("Bot今日盟友"))
        self.assertEqual(len(results[0]), 2)
        self.assertEqual(
            [call.kwargs["nickname"] for call in builder.call_args_list],
            ["爱丽丝", "爱丽丝"],
        )
        self.assertIn("爱丽丝今天的盟友是 瓦豆鲁迪", tool_result)
        self.assertIn("这是一段简体中文盟友简介。", tool_result)
        self.assertTrue(
            self.plugin._chain_result_with_media.call_args.kwargs["send_immediately"]
        )

    async def test_card_uses_draw_date_and_saved_collection_snapshot(self):
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}
        outcome = AllyDrawOutcome(
            self.entry,
            remaining=2,
            repeated=False,
            pity=False,
            collected=7,
            total=42,
            draw_date="2026-10-28",
        )
        with (
            patch.object(main_module, "get_today", return_value="2026-10-29"),
            patch.object(
                main_module,
                "build_ally_draw_card_data",
                side_effect=lambda _entry, **kwargs: kwargs,
            ) as builder,
            patch.object(main_module, "render_ally_draw_card", side_effect=self.render),
        ):
            await self.plugin._daily_ally_chain(outcome, "收藏者", self.text)
        self.assertEqual(builder.call_args.kwargs["date"], "2026-10-28")
        self.assertEqual(builder.call_args.kwargs["collected"], 7)
        self.assertEqual(builder.call_args.kwargs["total"], 42)

    async def test_draw_and_reuse_capture_progress_after_unlock(self):
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}
        self.plugin.store.user_progress = lambda user: {
            "unlocked": len(set(self.plugin.store.unlocked_filenames(user))),
            "total": 10,
        }
        first, error = await self.plugin._draw_for_identity(
            group_id="qa",
            user_id="qa",
            nickname="收藏者",
            base_limit=3,
        )
        self.assertIsNone(error)
        self.assertEqual((first.collected, first.total), (1, 10))
        self.assertEqual(first.draw_date, main_module.get_today())
        reused, error = await self.plugin._draw_for_identity(
            group_id="qa",
            user_id="qa",
            nickname="收藏者",
            base_limit=3,
            reuse_today=True,
        )
        self.assertIsNone(error)
        self.assertTrue(reused.existing_today)
        self.assertEqual((reused.collected, reused.total), (1, 10))
        self.assertEqual(sum(self.plugin.store.draws.values()), 1)

    async def test_optional_progress_failure_does_not_hide_or_repeat_saved_draw(self):
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}

        def fail_progress(_user):
            raise ValueError("unavailable statistics")

        self.plugin.store.user_progress = fail_progress
        outcome, error = await self.plugin._draw_for_identity(
            group_id="qa",
            user_id="qa",
            nickname="收藏者",
            base_limit=3,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(outcome)
        self.assertIsNone(outcome.collected)
        self.assertEqual(sum(self.plugin.store.draws.values()), 1)
