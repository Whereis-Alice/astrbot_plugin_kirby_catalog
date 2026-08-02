import asyncio
import time
import unittest
from types import SimpleNamespace

from astrbot.api import message_components as Comp

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin


class FakeStore:
    def __init__(self, entry):
        self.entry = entry
        self.group = {}

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

    def unlock(self, user, filename):
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

    def asset_bytes(self, entry, download=False):
        return None


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, message_chain):
        self.sent.append((umo, message_chain))
        return True


class FakeEvent:
    def __init__(self, message_str, message=None):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1", message=message or [])

    def get_sender_id(self):
        return "user-1"

    def get_sender_name(self):
        return "测试用户"

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
            ["答对啦！答案是 #12 星之卡比，并已收入你的图鉴。"],
        )
        self.assertNotIn("group-1", plugin._guess_sessions)

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

        self.assertTrue(results[0][0].text.startswith("随机盟友：#12 星之卡比"))
        self.assertFalse(plugin.store.group)


if __name__ == "__main__":
    unittest.main()
