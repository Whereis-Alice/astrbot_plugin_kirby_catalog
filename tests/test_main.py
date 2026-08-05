import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api import message_components as Comp
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star_handler import star_handlers_registry

from astrbot_plugin_kirby_catalog.catalog_core import get_today
from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin


class FakeStore:
    def __init__(self, entry):
        self.entry = entry
        self.group = {}
        self.draws = {}
        self.bonuses = {}
        self.description = "这是一段简体中文盟友简介。"
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
        return None

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
            "name_zh": "测试中文名",
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
        self.description = "这是一段简体中文盟友简介。"
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
        sender_name="测试用户",
    ):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id=group_id, message=message or [])
        self.unified_msg_origin = f"test:group:{group_id}"
        self.sender_id = sender_id
        self.sender_name = sender_name

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


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
        self.entry = {"filename": "ally.png", "id": 12, "name": "星之卡比"}

    async def test_wrong_guess_reveals_answer_and_ends_round(self):
        plugin = make_plugin(self.entry)
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": time.monotonic(),
        }

        results = [
            result async for result in plugin.guess_ally(FakeEvent("猜盟友 错误答案"))
        ]

        self.assertEqual(
            results,
            ["猜错了，本轮结束。正确答案是 #12 星之卡比。"],
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
        self.assertIn("正确答案是 #12 星之卡比。", message_chain.chain[0].text)

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
            message_str="测试用户，你今天的盟友是 #12 星之卡比。",
        )

        results = [
            result
            async for result in plugin.rename_ally(
                FakeEvent("星之卡比图鉴改名 结晶化天鹅罗利那", [quoted_message])
            )
        ]

        self.assertEqual(
            results,
            ["已将 #12 改为 结晶化天鹅罗利那，所有用户解锁记录已同步。"],
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
                FakeEvent("星之卡比", [quoted_message])
            )
        ]

        self.assertEqual(
            results,
            ["答对啦！答案是 #12 星之卡比。"],
        )
        self.assertNotIn("group-1", plugin._guess_sessions)
        self.assertFalse(plugin.store.group)

    async def test_active_guess_cannot_be_replaced_by_new_command(self):
        plugin = make_plugin(self.entry)
        plugin._guess_sessions["group-1"] = {
            "filename": "ally.png",
            "started_at": time.monotonic(),
        }

        results = [result async for result in plugin.guess_ally(FakeEvent("猜盟友"))]

        self.assertEqual(
            results,
            ["本群已有一轮猜盟友正在进行，请直接引用题目图片并发送名字作答。"],
        )
        self.assertEqual(plugin._guess_sessions["group-1"]["filename"], "ally.png")

    async def test_random_ally_only_displays_entry(self):
        plugin = make_plugin(self.entry)

        results = [result async for result in plugin.random_ally(FakeEvent("随机盟友"))]

        self.assertTrue(
            results[0][0].text.startswith("随机查看的盟友是 星之卡比，图鉴编号 #12")
        )
        self.assertIn("简介：\n这是一段简体中文盟友简介。", results[0][0].text)
        self.assertFalse(plugin.store.group)

    async def test_personal_progress_reports_percentage_and_remaining_count(self):
        plugin = make_plugin(self.entry)

        results = [
            result
            async for result in plugin.personal_progress(FakeEvent("我的图鉴进度"))
        ]

        self.assertIn("已解锁：102/409", results[0])
        self.assertIn("24.9%", results[0])
        self.assertIn("还差 307 个盟友", results[0])

    async def test_chinese_and_english_names_both_answer_correctly(self):
        entry = {
            "filename": "星之卡比 新星同盟.滴水鳗（Driblee）.png",
            "id": 88,
            "name": "滴水鳗（Driblee）",
            "source": "星之卡比 新星同盟",
            "page_title": "Driblee",
            "variant_key": "Driblee",
            "aliases": [],
        }
        for answer in ("滴水鳗", "Driblee", "driblee"):
            with self.subTest(answer=answer):
                plugin = make_plugin(entry)
                plugin._guess_sessions["group-1"] = {
                    "filename": entry["filename"],
                    "started_at": time.monotonic(),
                }
                results = [
                    result
                    async for result in plugin.guess_ally(FakeEvent(f"猜盟友 {answer}"))
                ]
                self.assertEqual(
                    results,
                    ["答对啦！答案是 #88 滴水鳗（Driblee）。"],
                )

    def test_variant_does_not_accept_base_page_title_as_answer(self):
        entry = {
            "filename": "星之卡比 Wii.山石EX（Moundo EX）.png",
            "id": 842,
            "name": "山石EX（Moundo EX）",
            "source": "星之卡比 Wii",
            "page_title": "Moundo",
            "variant_key": "Moundo EX",
            "aliases": ["Moundo", "Moundo EX", "山石EX"],
        }
        plugin = make_plugin(entry)

        self.assertTrue(plugin._guess_matches(entry, "山石EX"))
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
                "爱丽丝的尼酱，你今天的盟友是 Papi，图鉴编号 #1202，"
                "首次登场于《Kirby: Meta Knight and the Knight of Yomi》。\n"
                "今日剩余次数：2"
            ),
        )

        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("卡比百科", [quoted])),
            ("Papi", False),
        )
        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("卡比百科名称", [quoted])),
            ("Papi", True),
        )
        self.assertEqual(
            self.plugin._fandom_query_parts(FakeEvent("卡比F", [quoted])),
            ("Papi", "page", ""),
        )

    def test_filename_style_quote_skips_english_work_prefix(self):
        quoted = Comp.Reply(
            id="reply-prefix",
            message_str="Kirby's Dream Land.Benny.png",
        )

        self.assertEqual(
            self.plugin._wikirby_query_parts(FakeEvent("卡比百科", [quoted])),
            ("Benny", False),
        )
        self.assertEqual(
            self.plugin._fandom_query_parts(FakeEvent("卡比F", [quoted])),
            ("Benny", "page", ""),
        )

    def test_command_text_is_not_mistaken_for_quoted_content(self):
        quoted = Comp.Reply(id="reply-name", message_str="角色（Driblee）")
        current_command = Comp.Plain("卡比百科")

        self.assertEqual(
            self.plugin._wikirby_query_parts(
                FakeEvent("卡比百科", [quoted, current_command])
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

        default_text = plugin._draw_message(self.entry, "爱丽丝的尼酱", 2, "")
        self.assertEqual(
            default_text,
            "爱丽丝的尼酱，你今天的盟友是 Papi，图鉴编号 #1202，"
            "首次登场于《Kirby: Meta Knight and the Knight of Yomi》。\n"
            "今日剩余次数：2",
        )

        plugin.config = {
            "draw_message_template": "{nickname} 抽到了 {name} / #{id} / 剩余 {remaining}"
        }
        self.assertEqual(
            plugin._draw_message(self.entry, "爱丽丝", 4, "（保底）"),
            "爱丽丝 抽到了 Papi / #1202 / 剩余 4",
        )

        plugin.config = {"draw_message_template": "{unknown_placeholder}"}
        self.assertEqual(
            plugin._draw_message(self.entry, "爱丽丝", 2, ""),
            "爱丽丝，你今天的盟友是 Papi，图鉴编号 #1202，"
            "首次登场于《Kirby: Meta Knight and the Knight of Yomi》。\n"
            "今日剩余次数：2",
        )

    def test_ally_detail_template_and_visibility_switches(self):
        plugin = make_plugin(self.entry)

        self.assertEqual(
            plugin._ally_detail_message(self.entry, "基础文案"),
            "基础文案\n"
            "简介：\n"
            "这是一段简体中文盟友简介。\n"
            "详细信息引用本条消息并回复卡比百科即可查看（查百科会比较慢）",
        )

        plugin.config = {
            "ally_description_enabled": False,
            "ally_wiki_hint_enabled": False,
        }
        self.assertEqual(
            plugin._ally_detail_message(self.entry, "基础文案"), "基础文案"
        )

        plugin.config = {
            "ally_detail_template": "{base}\n资料：{description}\n{wiki_hint}",
            "ally_wiki_hint_text": "引用后发送卡比百科",
        }
        self.assertEqual(
            plugin._ally_detail_message(self.entry, "基础文案"),
            "基础文案\n资料：这是一段简体中文盟友简介。\n引用后发送卡比百科",
        )

    def test_ally_description_limit_includes_suffix_and_zero_is_unlimited(self):
        plugin = make_plugin(self.entry)
        plugin.store.description = "星" * 40
        plugin.config = {"ally_description_max_chars": 16}

        truncated = plugin._ally_description_text(self.entry)

        self.assertEqual(len(truncated), 16)
        self.assertTrue(truncated.endswith("... ..."))

        plugin.config = {"ally_description_max_chars": 0}
        self.assertEqual(plugin._ally_description_text(self.entry), "星" * 40)

    async def test_view_description_works_when_automatic_display_is_disabled(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_enabled": False}
        quoted = Comp.Reply(
            id="reply-view-description",
            message_str="随机查看的盟友是 Papi，图鉴编号 #1202。",
        )

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("查看简介", [quoted])
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("#1202 Papi", results[0])
        self.assertIn("这是一段简体中文盟友简介。", results[0])

    async def test_view_description_can_use_forward_message(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_view_mode": "合并转发"}

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("查看简介 1202")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertIsInstance(results[0][0], Comp.Nodes)
        node = results[0][0].nodes[0]
        self.assertEqual(node.name, "星之卡比图鉴")
        self.assertIn("#1202 Papi", node.content[0].text)

    async def test_view_description_can_render_single_card(self):
        plugin = make_plugin(self.entry)
        plugin.store.description = "卡比简介" * 20
        plugin.config = {
            "ally_description_max_chars": 30,
            "ally_description_view_mode": "简介卡片",
            "ally_description_card_template": "星际档案",
        }
        plugin.html_render = AsyncMock(return_value="C:\\temp\\ally-introduction.png")

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("/查看简介 1202")
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
        self.assertEqual(payload["theme"]["label"], "星际档案")
        self.assertEqual(payload["image_data_uri"], "")
        self.assertIn("图鉴编号", payload_text)
        self.assertIn("#1202", payload_text)
        self.assertIn("Kirby: Meta Knight and the Knight of Yomi", payload_text)
        self.assertEqual(payload["source"], "https://wikirby.com/wiki/Test")

    async def test_view_description_card_failure_falls_back_to_text(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"ally_description_view_mode": "简介卡片"}
        plugin.html_render = AsyncMock(side_effect=RuntimeError("render failed"))

        results = [
            result
            async for result in plugin.view_ally_description(
                FakeEvent("查看简介 1202")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], str)
        self.assertIn("这是一段简体中文盟友简介。", results[0])

    async def test_draw_message_places_description_before_hint(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"draw_cooldown_seconds": 0}
        plugin.store.asset_bytes = lambda _entry, _download=False: b"image-bytes"

        results = [result async for result in plugin.draw_ally(FakeEvent("今日盟友"))]

        text = results[0][0].text
        self.assertLess(text.index("简介："), text.index("详细信息引用本条消息"))
        self.assertIn("这是一段简体中文盟友简介。", text)
        self.assertIsInstance(results[0][-1], Comp.Image)

    async def test_query_ally_uses_query_template_and_description(self):
        plugin = make_plugin(self.entry)
        plugin.store.group = {
            "user-1": {
                "current": {"ally_filename": "Papi.png", "date": get_today()},
                "unlocked": [
                    {"ally_filename": "Papi.png", "unlock_date": "2026-08-01"}
                ],
                "nickname": "爱丽丝",
            }
        }

        results = [result async for result in plugin.query_ally(FakeEvent("查盟友"))]

        text = results[0][0].text
        self.assertIn("爱丽丝 今天的盟友是 Papi，图鉴编号 #1202", text)
        self.assertIn("解锁于 2026-08-01", text)
        self.assertIn("简介：\n这是一段简体中文盟友简介。", text)

    async def test_admin_can_edit_and_restore_description_from_quote(self):
        plugin = make_plugin(self.entry)
        quoted = Comp.Reply(
            id="reply-description",
            message_str="随机查看的盟友是 Papi，图鉴编号 #1202。",
        )

        updated = [
            result
            async for result in plugin.edit_ally_description(
                FakeEvent("星之卡比图鉴简介 新的人工简介。", [quoted])
            )
        ]
        self.assertIn("已保存 #1202 Papi 的人工简介", updated[0])
        self.assertEqual(plugin.store.description, "新的人工简介。")

        viewed = [
            result
            async for result in plugin.edit_ally_description(
                FakeEvent("星之卡比图鉴简介 1202")
            )
        ]
        self.assertIn("管理员人工简介", viewed[0])
        self.assertIn("新的人工简介。", viewed[0])

        restored = [
            result
            async for result in plugin.restore_ally_description(
                FakeEvent("星之卡比图鉴恢复简介", [quoted])
            )
        ]
        self.assertIn("已恢复 #1202 Papi 的内置简介", restored[0])

    async def test_granted_opportunity_extends_effective_draw_limit(self):
        plugin = make_plugin(self.entry)
        plugin.config = {"daily_draw_limit": 1, "draw_cooldown_seconds": 0}
        today = get_today()
        plugin.store.draws[("group-1", "user-1", today)] = 1
        plugin.store.bonuses[("group-1", "user-1", today)] = 1

        results = [result async for result in plugin.draw_ally(FakeEvent("今日盟友"))]

        self.assertEqual(plugin.store.draw_count("group-1", "user-1", today), 2)
        self.assertIn("图鉴编号 #1202", results[0][0].text)
        self.assertIn("今日剩余次数：0", results[0][0].text)

    async def test_admin_can_add_member_opportunities_by_at(self):
        plugin = make_plugin(self.entry)
        plugin.store.group = {"42": {"nickname": "群友"}}
        event = FakeEvent(
            "增加今日抽取次数 2",
            [Comp.At(qq="42"), Comp.Plain("2")],
        )

        results = [result async for result in plugin.add_member_draw_count(event)]

        self.assertEqual(len(results), 1)
        self.assertIn("已为 群友（42）增加 2 次今日抽取机会", results[0])
        self.assertIn("今日可用 5 次", results[0])
        self.assertEqual(sum(plugin.store.bonuses.values()), 2)

    async def test_admin_can_add_member_opportunity_by_user_id(self):
        plugin = make_plugin(self.entry)

        results = [
            result
            async for result in plugin.add_member_draw_count(
                FakeEvent("增加今日抽取次数 2127074778")
            )
        ]

        self.assertIn("增加 1 次今日抽取机会", results[0])
        self.assertEqual(sum(plugin.store.bonuses.values()), 1)

    async def test_admin_rejects_non_positive_opportunity_amount(self):
        plugin = make_plugin(self.entry)
        event = FakeEvent(
            "增加今日抽取次数 -2",
            [Comp.At(qq="42"), Comp.Plain("-2")],
        )

        results = [result async for result in plugin.add_member_draw_count(event)]

        self.assertIn("用法：增加今日抽取次数", results[0])
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
                FakeEvent("重置今日群抽取次数")
            )
        ]

        self.assertIn("共处理 1 位群友", results[0])
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
            "今日盟友",
            "/今日盟友",
            "抽盟友",
            "/抽盟友",
            "抽取盟友",
            "/抽取盟友",
        ):
            with self.subTest(message=message):
                self.assertIsNotNone(command_pattern.fullmatch(message))

        old_handler_name = f"{KirbyCatalogPlugin.draw_ally.__module__}_draw_ally_plain"
        self.assertIsNone(
            star_handlers_registry.get_handler_by_full_name(old_handler_name)
        )


if __name__ == "__main__":
    unittest.main()
