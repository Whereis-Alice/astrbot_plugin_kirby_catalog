import asyncio
import time
import unittest
from types import SimpleNamespace

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin


class FakeStore:
    def __init__(self, entry):
        self.entry = entry

    def resolve_entry(self, filename):
        return self.entry if filename == self.entry["filename"] else None


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, message_chain):
        self.sent.append((umo, message_chain))
        return True


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1")

    def get_sender_id(self):
        return "user-1"

    def get_sender_name(self):
        return "测试用户"

    def plain_result(self, text):
        return text


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


if __name__ == "__main__":
    unittest.main()
