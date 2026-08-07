import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api import message_components as Comp
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star_handler import star_handlers_registry
from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import get_today
from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.media_delivery import stage_local_image


class FakeStore:
    def __init__(self, entry):
        self.entry = entry
        self.group = {}
        self.draws = {}
        self.bonuses = {}
        self.refresh_calls = 0
        self.description = "?????????????"
        self.description_is_override = False

    def resolve_entry(self, filename):
        return self.entry if filename == self.entry["filename"] else None

    def find_entries(self, target):
        target = str(target)
        return (
            [self.entry]
            if target in {str(self.entry["id"]), self.entry["name"]}
            else []
        )

    def rename_entry(self, entry, new_name, source=None):
        updated = dict(entry)
        updated["name"] = new_name
        if source is not None:
            updated["source"] = source
        return updated

    def load_group(self, group_id):
        return self.group

    def unlock(self, user, filename, _today=None):
        user.setdefault("unlocked", []).append(
            {"ally_filename": filename, "unlock_date": "2026-08-03"}
        )
        return True

    def save_group(self, group_id, config):
        self.group = config

    def refresh(self):
        self.refresh_calls += 1

    def get_draw_pool(self):
        return [self.entry]

    def unlocked_filenames(self, user):
        return [item["ally_filename"] for item in user.get("unlocked", [])]

    def draw_count(self, group_id, user_id, today=None):
        return self.draws.get((str(group_id), str(user_id), str(today)), 0)

    def increment_draw(self, group_id, user_id, today=None):
        key = (str(group_id), str(user_id), str(today))
        self.draws[key] = self.draws.get(key, 0) + 1
        return self.draws[key]

    def draw_bonus(self, group_id, user_id, today=None):
        return self.bonuses.get((str(group_id), str(user_id), str(today)), 0)

    def add_draw_bonus(self, group_id, user_id, amount=1, today=None):
        key = (str(group_id), str(user_id), str(today))
        self.bonuses[key] = self.bonuses.get(key, 0) + amount
        return self.bonuses[key]

    def reset_group_draws(self, group_id, today=None):
        group_id = str(group_id)
        today = str(today)
        draw_keys = [
            key for key in self.draws if key[0] == group_id and key[2] == today
        ]
        bonus_keys = [
            key for key in self.bonuses if key[0] == group_id and key[2] == today
        ]
        users = {key[1] for key in (*draw_keys, *bonus_keys)}
        for key in draw_keys:
            self.draws.pop(key)
        for key in bonus_keys:
            self.bonuses.pop(key)
        return {
            "users": len(users),
            "draw_records": len(draw_keys),
            "bonus_records": len(bonus_keys),
        }

    def asset_bytes(self, entry, download=False):
        return None

    def profile_for(self, entry):
        return {
            "name_zh": "?????",
            "name_en": str(entry.get("page_title") or entry.get("name") or ""),
            "display_name": str(entry.get("name") or ""),
            "description_zh": self.description,
            "description_origin": (
                "override" if self.description_is_override else "bundled"
            ),
            "source_url": "https://wikirby.com/wiki/Test",
        }

    def description_for(self, entry):
        return self.description

    def set_description(self, entry, description, updated_by=""):
        self.description = description.strip()
        self.description_is_override = True
        return self.profile_for(entry)

    def restore_description(self, entry):
        removed = self.description_is_override
        self.description_is_override = False
        self.description = "?????????????"
        return removed, self.profile_for(entry)

    def user_progress(self, _user):
        return {
            "unlocked": 102,
            "total": 409,
            "missing": [],
            "unlocked_filenames": [],
        }


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, message_chain):
        self.sent.append((umo, message_chain))
        return True


class FakeEvent:
    def __init__(
        self,
        message_str,
        message=None,
        group_id="group-1",
        sender_id="user-1",
        sender_name="????",
    ):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id=group_id, message=message or [])
        self.unified_msg_origin = f"test:group:{group_id}"
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.sent_chains = []

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain

    async def send(self, chain):
        self.sent_chains.append(chain)
        return True


def make_plugin(entry):
    plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
    plugin.config = {}
    plugin.store = FakeStore(entry)
    plugin.context = FakeContext()
    plugin._guess_sessions = {}
    plugin._guess_timeout_tasks = {}
    plugin._draw_lock = asyncio.Lock()
    plugin._cooldowns = {}
    return plugin


class GuessFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = {"filename": "ally.png", "id": 12, "name": "????"}

    async def test_wrong_guess_reveals_answer_and_ends_round(self):
        plugin = make_plugin(self.entry)
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": time.monotonic(),
        }

        results = [
            result async for result in plugin.guess_ally(FakeEvent("??? ????"))
        ]

        self.assertEqual(
            results,
            ["?????????????? #12 ?????"],
        )
        self.assertNotIn("group-1", plugin._guess_sessions)

    async def test_timeout_worker_sends_answer_without_new_event(self):
        plugin = make_plugin(self.entry)
        started_at = time.monotonic()
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": started_at,
        }

        await plugin._guess_timeout_worker(
            "group-1", "ally.png", started_at, 0, "aiocqhttp:group:group-1"
        )

        self.assertNotIn("group-1", plugin._guess_sessions)
        self.assertEqual(len(plugin.context.sent), 1)
        umo, message_chain = plugin.context.sent[0]
        self.assertEqual(umo, "aiocqhttp:group:group-1")
        self.assertIn("????? #12 ?????", message_chain.chain[0].text)

    async def test_terminate_cancels_pending_guess_timeout(self):
        plugin = make_plugin(self.entry)
        task = asyncio.create_task(asyncio.sleep(60))
        plugin._guess_timeout_tasks["group-1"] = task
        plugin._guess_sessions["group-1"] = {"filename": "ally.png"}

        await plugin.terminate()

        self.assertTrue(task.cancelled())
        self.assertFalse(plugin._guess_timeout_tasks)
        self.assertFalse(plugin._guess_sessions)

    async def test_rename_can_use_quoted_ally_id(self):
        plugin = make_plugin(self.entry)
        quoted_message = Comp.Reply(
            id="reply-1",
            message_str="???????????? #12 ?????",
        )

        results = [
            result
            async for result in plugin.rename_ally(
                FakeEvent("???????? ????????", [quoted_message])
            )
        ]

        self.assertEqual(
            results,
            ["?? #12 ?? ?????????????????????"],
        )

    async def test_quoted_image_can_answer_without_guess_command(self):
        plugin = make_plugin(self.entry)
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": time.monotonic(),
        }
        quoted_image = Comp.Image.fromURL("https://example.com/ally.png")
        quoted_message = Comp.Reply(id="reply-2", chain=[quoted_image])

        results = [
            result
            async for result in plugin.guess_ally_by_quoted_image(
                FakeEvent("????", [quoted_message])
            )
        ]

        self.assertEqual(
            results,
            ["??????? #12 ?????"],
        )
        self.assertNotIn("group-1", plugin._guess_sessions)
        self.assertFalse(plugin.store.group)

    async def test_active_guess_cannot_be_replaced_by_new_command(self):
        plugin = make_plugin(self.entry)
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": time.monotonic(),
        }

        results = [result async for result in plugin.guess_ally(FakeEvent("???"))]

        self.assertEqual(
            results,
            ["???????????????????????????????"],
        )
        self.assertEqual(plugin._guess_sessions["group-1"]["filename"], "ally.png")

    async def test_random_ally_only_displays_entry(self):
        plugin = make_plugin(self.entry)

        results = [result async for result in plugin.random_ally(FakeEvent("????"))]

        self.assertTrue(
            results[0][0].text.startswith("???????? ????????? #12")
        )
        self.assertIn("???\n?????????????", results[0][0].text)
        self.assertFalse(plugin.store.group)

    async def test_personal_progress_reports_percentage_and_remaining_count(self):
        plugin = make_plugin(self.entry)

        results = [
            result
            async for result in plugin.personal_progress(FakeEvent("??????"))
        ]

        self.assertIn("????102/409", results[0])
        self.assertIn("24.9%", results[0])
        self.assertIn("?? 307 ???", results[0])

    async def test_chinese_and_english_names_both_answer_correctly(self):
        entry = {
            "filename": "???? ????.????Driblee?.png",
            "id": 88,
            "name": "????Driblee?",
            "source": "???? ????",
            "page_title": "Driblee",
            "variant_key": "Driblee",
            "aliases": [],
        }
        for answer in ("???", "Driblee", "driblee"):
            with self.subTest(answer=answer):
                plugin = make_plugin(entry)
                plugin._guess_sessions["group-1"] = {
                    "filename": entry["filename"],
                    "started_at": time.monotonic(),
                }
                results = [
                    result
                    async for result in plugin.guess_ally(FakeEvent(f"??? {answer}"))
                ]
                self.assertEqual(
                    results,
                    ["??????? #88 ????Driblee??"],
                )

    def test_variant_does_not_accept_base_page_title_as_answer(self):
        entry = {
            "filename": "???? Wii.??EX?Moundo EX?.png",
            "id": 842,
            "name": "??EX?Moundo EX?",
            "source": "???? Wii",
            "page_title": "Moundo",
            "variant_key": "Moundo EX",
            "aliases": ["Moundo", "Moundo EX", "??EX"],
        }
        plugin = make_plugin(entry)

        self.assertTrue(plugin._guess_matches(entry, "??EX"))
        self.assertTrue(plugin._guess_matches(entry, "moundo ex"))
        self.assertFalse(plugin._guess_matches(entry, "Moundo"))


class QuotedWikiQueryTests(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "filename": "Kirby: Meta Knight and the Knight of Yomi.Papi.png",
            "id": 1202,
            "name": "Papi",
            "source": "Kirby: Meta Knight and the Knight of Yomi",
            "page_title": "Papi",
            "variant_key": "Papi",
            "aliases": [],
        }
        self.plugin = make_plugin(self.entry)

    def test_all_wiki_commands_use_catalog_page_title_from_quoted_draw(self):
        quoted = Comp.Reply(
            id="reply-wiki",
            message_str=(
                "?????????????? Papi????? #1202?"
                "??????Kirby: Meta Knight and the Knight of Yomi??\n"
                "???????2"
            ),
        )

        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("????", [quoted])),
            ("Papi", False),
        )
        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("??????", [quoted])),
            ("Papi", True),
        )
        self.assertEqual(
            self.plugin._fandom_query_parts(FakeEvent("??F", [quoted])),
            ("Papi", "page", ""),
        )

    def test_filename_style_quote_skips_english_work_prefix(self):
        quoted = Comp.Reply(
            id="reply-prefix",
            message_str="Kirby's Dream Land.Benny.png",
        )

        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("????", [quoted])),
            ("Benny", False),
        )
        self.assertEqual(
            self.plugin._fandom_query_parts(FakeEvent("??F", [quoted])),
            ("Benny", "page", ""),
        )

    def test_command_text_is_not_mistaken_for_quoted_content(self):
        quoted = Comp.Reply(id="reply-name", message_str="???Driblee?")
        current_command = Comp.Plain("????")

        self.assertEqual(
            self.plugin._wikirby_query_parts(
                FakeEvent("????", [quoted, current_command])
            ),
            ("Driblee", False),
        )


class DrawManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = {
            "filename": "Papi.png",
            "id": 1202,
            "name": "Papi",
            "source": "Kirby: Meta Knight and the Knight of Yomi",
        }

    def test_default_and_custom_draw_message_templates(self):
        plugin = make_plugin(self.entry)

        default_text = plugin._draw_message(self.entry, "??????", 2, "")
        self.assertEqual(
            default_text,
            "?????????????? Papi????? #1202?"
            "??????Kirby: Meta Knight and the Knight of Yomi??\n"
            "???????2",
        )

        plugin.config = {
            "draw_message_template": "{nickname} ??? {name} / #{id} / ?? {remaining}"
        }
        self.assertEqual(
            plugin._draw_message(self.entry, "???", 4, "????"),
            "??? ??? Papi / #1202 / ?? 4",
        )

        plugin.config = {"draw_message_template": "{unknown_placeholder}"}
        self.assertEqual(
            plugin._draw_message(self.entry, "???", 2, ""),
            "??????????? Papi????? #1202?"
            "??????Kirby: Meta Knight and the Knight of Yomi??\n"
            "???????2",
        )

    async def test_bot_draw_has_three_independent_daily_chances_and_tool_sends_image(self):
        plugin = make_plugin(self.entry)
        plugin.store.asset_bytes = lambda _entry, _download=False: b"image-bytes"
        event = FakeEvent("Bot????", group_id="100")

        first = [result async for result in plugin.draw_bot_ally(event)]
        second = [result async for result in plugin.draw_bot_ally(event)]
        tool_text = await plugin.draw_bot_ally_tool(event)
        fourth_outcome, fourth_error = await plugin._draw_bot_ally(event)

        today = get_today()
        self.assertEqual(plugin.store.draw_count("100", "bot_astrbot", today), 3)
        self.assertEqual(plugin.store.draw_count("100", "user-1", today), 0)
        self.assertIn("bot_astrbot", plugin.store.group)
        self.assertIn("Papi", first[0][0].text)
        self.assertIn("???????1", second[0][0].text)
        self.assertEqual(len(event.sent_chains), 1)
        self.assertIn("???????0", event.sent_chains[0].chain[0].text)
        self.assertIsInstance(event.sent_chains[0].chain[1], Comp.Image)
        self.assertIn("???????", tool_text)
        self.assertIsNone(fourth_outcome)
        self.assertIn("?????? 3 ?", fourth_error)

    async def test_draw_uses_loaded_catalog_without_full_refresh(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"draw_cooldown_seconds": 0}
        plugin.store.asset_bytes = lambda _entry, _download=False: b"image-bytes"

        results = [
            result async for result in plugin.draw_ally(FakeEvent("????"))
        ]

        self.assertEqual(plugin.store.refresh_calls, 0)
        self.assertEqual(len(results), 1)

    async def test_napcat_direct_send_uses_file_uri_instead_of_base64(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def send_group_msg(self, **kwargs):
                self.calls.append(kwargs)

        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "NapCat??????",
            "media_normalize_jpeg": False,
            "media_direct_retry_count": 0,
        }
        event = FakeEvent("??", group_id="123456")
        event.unified_msg_origin = "aiocqhttp:group:123456"
        event.bot = FakeBot()
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "ally.jpg"
            Image.new("RGB", (64, 64), "pink").save(image_path, format="JPEG")

            sent = await plugin._try_direct_media_send(
                event,
                [
                    Comp.Plain("????"),
                    Comp.Image.fromFileSystem(str(image_path)),
                ],
            )

        self.assertTrue(sent)
        self.assertEqual(len(event.bot.calls), 1)
        payload = event.bot.calls[0]["message"]
        self.assertEqual(payload[0]["data"]["text"], "????")
        self.assertTrue(payload[1]["data"]["file"].startswith("file:///"))
        self.assertNotIn("base64://", payload[1]["data"]["file"])

    async def test_napcat_direct_send_failure_returns_to_standard_path(self):
        class FailingBot:
            def __init__(self):
                self.calls = 0

            async def send_group_msg(self, **_kwargs):
                self.calls += 1
                raise RuntimeError("rich media transfer failed")

        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "??????",
            "media_normalize_jpeg": False,
            "media_direct_retry_count": 1,
            "media_direct_retry_delay_seconds": 0,
        }
        event = FakeEvent("??", group_id="123456")
        event.unified_msg_origin = "aiocqhttp:group:123456"
        event.bot = FailingBot()
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "ally.png"
            Image.new("RGB", (32, 32), "pink").save(image_path, format="PNG")
            sent = await plugin._try_direct_media_send(
                event, [Comp.Image.fromFileSystem(str(image_path))]
            )

        self.assertFalse(sent)
        self.assertEqual(event.bot.calls, 2)

    async def test_napcat_direct_forward_uses_file_uri_and_consumes_result(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **kwargs):
                self.calls.append((action, kwargs))

        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "NapCat??????",
            "media_normalize_jpeg": False,
            "forward_direct_send_enabled": True,
            "forward_retry_count": 0,
            "forward_batch_delay_seconds": 0,
        }
        event = FakeEvent("??", group_id="123456")
        event.unified_msg_origin = "aiocqhttp:group:123456"
        event.bot = FakeBot()
        with tempfile.TemporaryDirectory() as temp:
            plugin.store.root = Path(temp) / "plugin-data"
            image_path = Path(temp) / "card.jpg"
            Image.new("RGB", (64, 64), "pink").save(image_path, format="JPEG")
            components = plugin._forward_nodes(
                "????", [Comp.Image.fromFileSystem(str(image_path))]
            )

            result = await plugin._chain_result_with_media(event, components)

        self.assertIsNone(result)
        self.assertEqual(len(event.bot.calls), 1)
        action, payload = event.bot.calls[0]
        self.assertEqual(action, "send_group_forward_msg")
        self.assertEqual(payload["group_id"], 123456)
        self.assertEqual(len(payload["messages"]), 2)
        image_segment = payload["messages"][1]["data"]["content"][0]
        self.assertTrue(image_segment["data"]["file"].startswith("file:///"))
        self.assertNotIn("base64://", image_segment["data"]["file"])

    async def test_failed_forward_splits_then_falls_back_to_normal_messages(self):
        class FailingForwardBot:
            def __init__(self):
                self.forward_sizes = []
                self.normal_messages = []

            async def call_action(self, _action, **kwargs):
                self.forward_sizes.append(len(kwargs["messages"]))
                raise RuntimeError("Cannot read properties of undefined (reading 'resId')")

            async def send_group_msg(self, **kwargs):
                self.normal_messages.append(kwargs["message"])

        class SendingEvent(FakeEvent):
            def __init__(self):
                super().__init__("??", group_id="123456")
                self.unified_msg_origin = "aiocqhttp:group:123456"
                self.sent_chains = []

            async def send(self, chain):
                self.sent_chains.append(chain)

        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "NapCat??????",
            "media_normalize_jpeg": False,
            "media_direct_retry_count": 0,
            "forward_direct_send_enabled": True,
            "forward_retry_count": 0,
            "forward_retry_delay_seconds": 0,
            "forward_batch_delay_seconds": 0,
        }
        event = SendingEvent()
        event.bot = FailingForwardBot()
        with tempfile.TemporaryDirectory() as temp:
            plugin.store.root = Path(temp) / "plugin-data"
            image_path = Path(temp) / "card.jpg"
            Image.new("RGB", (64, 64), "pink").save(image_path, format="JPEG")
            components = plugin._forward_nodes(
                "????", [Comp.Image.fromFileSystem(str(image_path))]
            )

            result = await plugin._chain_result_with_media(event, components)

        self.assertIsNone(result)
        self.assertEqual(event.bot.forward_sizes, [2, 1, 1])
        self.assertEqual(len(event.sent_chains), 1)
        self.assertEqual(event.sent_chains[0].chain[0].text, "????")
        self.assertEqual(len(event.bot.normal_messages), 1)
        self.assertEqual(event.bot.normal_messages[0][0]["type"], "image")

    async def test_direct_send_ignores_non_aiocqhttp_events(self):
        class FakeBot:
            def __init__(self):
                self.calls = 0

            async def send_group_msg(self, **_kwargs):
                self.calls += 1

        plugin = make_plugin(self.entry)
        plugin.config = {"media_send_mode": "NapCat??????"}
        event = FakeEvent("??", group_id="123456")
        event.bot = FakeBot()

        sent = await plugin._try_direct_media_send(event, [Comp.Plain("??")])

        self.assertFalse(sent)
        self.assertEqual(event.bot.calls, 0)

    def test_shared_media_stage_reuses_unchanged_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "ally.png"
            shared = root / "shared"
            Image.new("RGB", (64, 64), "pink").save(source, format="PNG")

            first_path, first_uri = stage_local_image(
                source, shared, normalize_jpeg_enabled=False
            )
            second_path, second_uri = stage_local_image(
                source, shared, normalize_jpeg_enabled=False
            )

            self.assertEqual(first_path, second_path)
            self.assertEqual(first_uri, second_uri)
            self.assertTrue(first_path.is_file())

    async def test_standard_fallback_uses_safe_delivery_copy_for_oversized_image(self):
        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "AstrBot????",
            "media_normalize_jpeg": False,
            "ally_media_max_width_px": 1000,
            "ally_media_max_height_px": 8000,
            "ally_media_max_megapixels": 18,
            "ally_media_max_bytes_mb": 8,
        }
        with tempfile.TemporaryDirectory() as temp:
            plugin.store.root = Path(temp) / "plugin-data"
            image_path = Path(temp) / "wide.png"
            Image.new("RGB", (2400, 120), "pink").save(image_path, format="PNG")

            result = await plugin._chain_result_with_media(
                FakeEvent("??"), [Comp.Image.fromFileSystem(str(image_path))]
            )
            delivered_path = Path(result[0].path)
            with Image.open(delivered_path) as delivered:
                self.assertLessEqual(delivered.width, 1000)
                self.assertEqual(delivered.height, 50)

        self.assertNotEqual(delivered_path, image_path)

    async def test_wiki_card_delivery_limits_do_not_expand_ally_images(self):
        plugin = make_plugin(self.entry)
        plugin.config = {
            "media_send_mode": "AstrBot????",
            "media_normalize_jpeg": False,
            "ally_media_max_width_px": 1000,
            "ally_media_max_height_px": 8000,
            "ally_media_max_megapixels": 18,
            "ally_media_max_bytes_mb": 8,
            "wiki_card_max_width_px": 3000,
            "wiki_card_max_height_px": 8000,
            "wiki_card_max_megapixels": 18,
            "wiki_card_max_bytes_mb": 8,
        }
        with tempfile.TemporaryDirectory() as temp:
            plugin.store.root = Path(temp) / "plugin-data"
            image_path = Path(temp) / "wide.png"
            Image.new("RGB", (2400, 120), "pink").save(image_path, format="PNG")

            ally_result = await plugin._chain_result_with_media(
                FakeEvent("??"), [Comp.Image.fromFileSystem(str(image_path))]
            )
            wiki_result = await plugin._chain_result_with_media(
                FakeEvent("??"),
                [Comp.Image.fromFileSystem(str(image_path))],
                media_profile="wiki_card",
            )

            with Image.open(Path(ally_result[0].path)) as ally_image:
                self.assertEqual(ally_image.width, 1000)
            with Image.open(Path(wiki_result[0].path)) as wiki_image:
                self.assertEqual(wiki_image.width, 2400)

    def test_ally_detail_template_and_visibility_switches(self):
        plugin = make_plugin(self.entry)

        self.assertEqual(
            plugin._ally_detail_message(self.entry, "????"),
            "????\n"
            "???\n"
            "?????????????\n"
            "??????????????????????????????",
        )

        plugin.config = {
            "ally_description_enabled": False,
            "ally_wiki_hint_enabled": False,
        }
        self.assertEqual(
            plugin._ally_detail_message(self.entry, "????"), "????"
        )

        plugin.config = {
            "ally_detail_template": "{base}\n???{description}\n{wiki_hint}",
            "ally_wiki_hint_text": "?????????",
        }
        self.assertEqual(
            plugin._ally_detail_message(self.entry, "????"),
            "????\n????????????????\n?????????",
        )

    def test_ally_description_limit_includes_suffix_and_zero_is_unlimited(self):
        plugin = make_plugin(self.entry)
        plugin.store.description = "?" * 40
        plugin.config = {"ally_description_max_chars": 16}

        truncated = plugin._ally_description_text(self.entry)

        self.assertEqual(len(truncated), 16)
        self.assertTrue(truncated.endswith("... ..."))

        plugin.config = {"ally_description_max_chars": 0}
        self.assertEqual(plugin._ally_description_text(self.entry), "?" * 40)

    async def test_view_description_works_when_automatic_display_is_disabled(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_enabled": False}
        quoted = Comp.Reply(
            id="reply-view-description",
            message_str="???????? Papi????? #1202?",
        )

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("????", [quoted])
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("#1202 Papi", results[0])
        self.assertIn("?????????????", results[0])

    async def test_view_description_can_use_forward_message(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_view_mode": "????"}

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("???? 1202")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertIsInstance(results[0][0], Comp.Nodes)
        node = results[0][0].nodes[0]
        self.assertEqual(node.name, "??????")
        self.assertIn("#1202 Papi", node.content[0].text)

    async def test_view_description_can_render_single_card(self):
        plugin = make_plugin(self.entry)
        plugin.store.description = "????" * 20
        plugin.config = {
            "ally_description_max_chars": 30,
            "ally_description_view_mode": "????",
            "ally_description_card_template": "????",
        }
        plugin.html_render = AsyncMock(return_value="C:\\temp\\ally-introduction.png")

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("/???? 1202")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertIsInstance(results[0][0], Comp.Image)
        plugin.html_render.assert_awaited_once()
        payload = plugin.html_render.await_args.args[1]
        payload_text = str(payload)
        expected = plugin._truncate_ally_description(plugin.store.description, 30)
        self.assertIn(expected, payload_text)
        self.assertEqual(payload["title"], "Papi")
        self.assertEqual(payload["theme"]["label"], "????")
        self.assertEqual(payload["image_data_uri"], "")
        self.assertIn("????", payload_text)
        self.assertIn("#1202", payload_text)
        self.assertIn("Kirby: Meta Knight and the Knight of Yomi", payload_text)
        self.assertEqual(payload["source"], "https://wikirby.com/wiki/Test")

    async def test_view_description_card_failure_falls_back_to_text(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_view_mode": "????"}
        plugin.html_render = AsyncMock(side_effect=RuntimeError("render failed"))

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("???? 1202")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], str)
        self.assertIn("?????????????", results[0])

    async def test_draw_message_places_description_before_hint(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"draw_cooldown_seconds": 0}
        plugin.store.asset_bytes = lambda _entry, _download=False: b"image-bytes"

        results = [result async for result in plugin.draw_ally(FakeEvent("????"))]

        text = results[0][0].text
        self.assertLess(text.index("???"), text.index("??????????"))
        self.assertIn("?????????????", text)
        self.assertIsInstance(results[0][-1], Comp.Image)

    async def test_query_ally_uses_query_template_and_description(self):
        plugin = make_plugin(self.entry)
        plugin.store.group = {
            "user-1": {
                "current": {"ally_filename": "Papi.png", "date": get_today()},
                "unlocked": [
                    {"ally_filename": "Papi.png", "unlock_date": "2026-08-01"}
                ],
                "nickname": "???",
            }
        }

        results = [result async for result in plugin.query_ally(FakeEvent("???"))]

        text = results[0][0].text
        self.assertIn("??? ?????? Papi????? #1202", text)
        self.assertIn("??? 2026-08-01", text)
        self.assertIn("???\n?????????????", text)

    async def test_admin_can_edit_and_restore_description_from_quote(self):
        plugin = make_plugin(self.entry)
        quoted = Comp.Reply(
            id="reply-description",
            message_str="???????? Papi????? #1202?",
        )

        updated = [
            result
            async for result in plugin.edit_ally_description(
                FakeEvent("???????? ???????", [quoted])
            )
        ]
        self.assertIn("??? #1202 Papi ?????", updated[0])
        self.assertEqual(plugin.store.description, "???????")

        viewed = [
            result
            async for result in plugin.edit_ally_description(
                FakeEvent("???????? 1202")
            )
        ]
        self.assertIn("???????", viewed[0])
        self.assertIn("???????", viewed[0])

        restored = [
            result
            async for result in plugin.restore_ally_description(
                FakeEvent("??????????", [quoted])
            )
        ]
        self.assertIn("??? #1202 Papi ?????", restored[0])

    async def test_granted_opportunity_extends_effective_draw_limit(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"daily_draw_limit": 1, "draw_cooldown_seconds": 0}
        today = get_today()
        plugin.store.draws[("group-1", "user-1", today)] = 1
        plugin.store.bonuses[("group-1", "user-1", today)] = 1

        results = [result async for result in plugin.draw_ally(FakeEvent("????"))]

        self.assertEqual(plugin.store.draw_count("group-1", "user-1", today), 2)
        self.assertIn("???? #1202", results[0][0].text)
        self.assertIn("???????0", results[0][0].text)

    async def test_admin_can_add_member_opportunities_by_at(self):
        plugin = make_plugin(self.entry)
        plugin.store.group = {"42": {"nickname": "??"}}
        event = FakeEvent(
            "???????? 2",
            [Comp.At(qq="42"), Comp.Plain("2")],
        )

        results = [result async for result in plugin.add_member_draw_count(event)]

        self.assertEqual(len(results), 1)
        self.assertIn("?? ???42??? 2 ???????", results[0])
        self.assertIn("???? 5 ?", results[0])
        self.assertEqual(sum(plugin.store.bonuses.values()), 2)

    async def test_admin_can_add_member_opportunity_by_user_id(self):
        plugin = make_plugin(self.entry)

        results = [
            result
            async for result in plugin.add_member_draw_count(
                FakeEvent("???????? 2127074778")
            )
        ]

        self.assertIn("?? 1 ???????", results[0])
        self.assertEqual(sum(plugin.store.bonuses.values()), 1)

    async def test_admin_rejects_non_positive_opportunity_amount(self):
        plugin = make_plugin(self.entry)
        event = FakeEvent(
            "???????? -2",
            [Comp.At(qq="42"), Comp.Plain("-2")],
        )

        results = [result async for result in plugin.add_member_draw_count(event)]

        self.assertIn("???????????", results[0])
        self.assertFalse(plugin.store.bonuses)

    async def test_admin_reset_only_clears_current_group_today(self):
        plugin = make_plugin(self.entry)
        today = get_today()
        plugin.store.draws[("group-1", "42", today)] = 3
        plugin.store.bonuses[("group-1", "42", today)] = 2
        plugin.store.draws[("group-2", "42", today)] = 1
        plugin.store.draws[("group-1", "42", "2026-08-04")] = 1
        plugin._cooldowns["group-1"] = {"42": time.monotonic()}

        results = [
            result
            async for result in plugin.reset_group_draw_counts(
                FakeEvent("?????????")
            )
        ]

        self.assertIn("??? 1 ???", results[0])
        self.assertEqual(plugin.store.draw_count("group-1", "42", today), 0)
        self.assertEqual(plugin.store.draw_bonus("group-1", "42", today), 0)
        self.assertEqual(plugin.store.draw_count("group-2", "42", today), 1)
        self.assertEqual(plugin.store.draw_count("group-1", "42", "2026-08-04"), 1)
        self.assertNotIn("group-1", plugin._cooldowns)


class DrawHandlerRegistrationTests(unittest.TestCase):
    def test_draw_commands_share_one_regex_handler(self):
        handler_name = f"{KirbyCatalogPlugin.draw_ally.__module__}_draw_ally"
        handler = star_handlers_registry.get_handler_by_full_name(handler_name)

        self.assertIsNotNone(handler)
        regex_filters = [
            item for item in handler.event_filters if isinstance(item, RegexFilter)
        ]
        self.assertEqual(len(regex_filters), 1)

        command_pattern = regex_filters[0].regex
        for message in (
            "????",
            "/????",
            "???",
            "/???",
            "????",
            "/????",
        ):
            with self.subTest(message=message):
                self.assertIsNotNone(command_pattern.fullmatch(message))

        old_handler_name = f"{KirbyCatalogPlugin.draw_ally.__module__}_draw_ally_plain"
        self.assertIsNone(
            star_handlers_registry.get_handler_by_full_name(old_handler_name)
        )


if __name__ == "__main__":
    unittest.main()
