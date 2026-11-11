"""Identity ownership, cross-adapter routing, merge accounting and crash recovery."""

import asyncio
import base64
import json
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from astrbot_plugin_kirby_catalog import identity_links as module
from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore, get_today
from astrbot_plugin_kirby_catalog.catalog_transfer import CatalogTransferService
from astrbot_plugin_kirby_catalog.identity_links import (
    member_key,
    scope_key,
    unlinked_user,
)
from astrbot_plugin_kirby_catalog.qq_official import identity_key
from PIL import Image

from .test_main import FakeEvent, make_plugin
from .test_qq_official import OfficialEvent


def record(filename="a.png", day="2026-11-10", nickname="旧昵称", pity=1):
    return {
        "nickname": nickname,
        "current": {"ally_filename": filename, "date": day},
        "unlocked": [{"ally_filename": filename, "unlock_date": day}],
        "no_new_count": pity,
    }


class StoreFixture:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        assets = self.root / "img/allies"
        assets.mkdir(parents=True)
        for name in ("a.png", "b.png"):
            Image.new("RGB", (20, 20), "pink").save(assets / name)
        self.store = CatalogStore(self.root, startup_migrate_legacy=False)
        self.links = self.store.identity_links
        self.store.save_group(
            "123", {"456": record(), "789": record("b.png"), "bot_99": record()}
        )

    def code(self, bot="app", scope="official-group", user="openid", old_user="456"):
        return self.links.issue(
            "user", "123", old_user, recipient=member_key(bot, scope, user)[:24]
        )

    def bind_user(
        self, bot="app", scope="official-group", user="openid", old_user="456"
    ):
        return self.links.bind(
            "user", bot, scope, user, self.code(bot, scope, user, old_user)
        )

    def bind_group(self, bot="app", scope="official-group", old_bot=""):
        code = self.links.issue("group", "123", bot_user=old_bot)
        return self.links.bind(
            "group", bot, scope, "admin", code, current_bot="bot_qq_app"
        )


class IdentityStoreTests(StoreFixture, unittest.TestCase):
    def test_unbound_context_preserves_existing_official_data(self):
        self.assertEqual(
            self.links.resolve("app", "official-group", "openid"),
            ("official-group", "openid"),
        )

    def test_personal_binding_does_not_grant_group_visibility(self):
        self.bind_user()
        self.assertEqual(
            self.links.resolve("app", "official-group", "openid"), ("123", "456")
        )
        self.assertEqual(self.links.group("app", "official-group"), "official-group")

    def test_group_binding_preserves_separate_unclaimed_members(self):
        self.store.save_group("official-group", {"openid": record("b.png")})
        self.store.set_draw_count("official-group", "openid", 2)
        self.bind_group()
        context = self.links.resolve("app", "official-group", "openid")
        self.assertEqual(context, ("123", unlinked_user("app", "openid")))
        self.assertEqual(self.store.draw_count(*context), 2)
        self.assertIn("456", self.store.load_group("123"))
        self.assertNotIn("openid", self.store.load_group("official-group"))

    def test_merge_deduplicates_first_dates_and_keeps_latest_current(self):
        old = record(day="2026-10-01", pity=2)
        self.store.save_group("123", {"456": old})
        new = record("b.png", "2026-11-11", "新昵称", 3)
        new["unlocked"].append({"ally_filename": "a.png", "unlock_date": "2026-10-20"})
        self.store.save_group("official-group", {"openid": new})
        self.bind_user()
        user = self.store.load_group("123")["456"]
        self.assertEqual(user["current"], new["current"])
        self.assertEqual(user["nickname"], "旧昵称")
        self.assertEqual(user["no_new_count"], 3)
        self.assertEqual(
            {i["ally_filename"]: i["unlock_date"] for i in user["unlocked"]},
            {"a.png": "2026-10-01", "b.png": "2026-11-11"},
        )

    def test_used_counts_add_and_bonus_takes_max_for_each_day(self):
        for day in (get_today(), "2026-11-01"):
            self.store.set_draw_count("123", "456", 3, day)
            self.store.set_draw_count("official-group", "openid", 2, day)
            self.store.set_draw_bonus("123", "456", 2, day)
            self.store.set_draw_bonus("official-group", "openid", 1, day)
        self.bind_user()
        for day in (get_today(), "2026-11-01"):
            self.assertEqual(self.store.draw_count("123", "456", day), 5)
            self.assertEqual(self.store.draw_bonus("123", "456", day), 2)
            self.assertEqual(self.store.draw_count("official-group", "openid", day), 0)

    def test_repeat_consumption_is_idempotent_for_same_recipient(self):
        self.store.set_draw_count("official-group", "openid", 2)
        code = self.code()
        self.links.bind("user", "app", "official-group", "openid", code)
        self.links.bind("user", "app", "official-group", "openid", code)
        self.assertEqual(self.store.draw_count("123", "456"), 2)
        with self.assertRaisesRegex(ValueError, "无效"):
            self.links.bind("user", "app", "other-group", "openid", code)

    def test_stolen_code_does_not_bind_or_consume_it(self):
        code = self.code()
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.links.bind("user", "app", "official-group", "thief", code)
        self.links.bind("user", "app", "official-group", "openid", code)

    def test_expired_code_and_reissued_code_are_rejected(self):
        with patch.object(module.time, "time", return_value=100):
            expired = self.code()
        with patch.object(module.time, "time", return_value=1301):
            with self.assertRaisesRegex(ValueError, "过期"):
                self.links.bind("user", "app", "official-group", "openid", expired)
        first, second = self.code(), self.code()
        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            self.links.bind("user", "app", "official-group", "openid", first)

    def test_plaintext_claim_code_is_never_persisted(self):
        code = self.code()
        self.assertNotIn(code, self.links.path.read_text(encoding="utf-8"))

    def test_cannot_overwrite_existing_link(self):
        self.bind_user()
        code = self.code(old_user="789")
        with self.assertRaisesRegex(ValueError, "已经绑定"):
            self.links.bind("user", "app", "official-group", "openid", code)

    def test_bot_group_private_scopes_are_isolated(self):
        self.bind_user()
        for bot, scope in (
            ("app2", "official-group"),
            ("app", "other-group"),
            ("app", "qqdm_abcdef"),
        ):
            self.assertEqual(
                self.links.resolve(bot, scope, "openid"), (scope, "openid")
            )

    def test_two_official_scopes_can_share_one_old_record(self):
        self.bind_user()
        self.store.set_draw_count("123", "456", 3)
        self.bind_user(scope="qqdm_abcdef", user="dm-openid")
        self.assertEqual(
            self.links.resolve("app", "qqdm_abcdef", "dm-openid"), ("123", "456")
        )
        self.assertEqual(self.store.draw_count("123", "456"), 3)

    def test_personal_and_group_link_orders_both_preserve_records(self):
        self.store.save_group("official-group", {"openid": record("b.png")})
        self.bind_group()
        self.bind_user()
        self.assertEqual(len(self.store.load_group("123")["456"]["unlocked"]), 2)
        self.assertNotIn(unlinked_user("app", "openid"), self.store.load_group("123"))
        self.bind_user(scope="official-group2")
        self.bind_group(scope="official-group2")
        self.assertEqual(
            self.links.resolve("app", "official-group2", "openid"), ("123", "456")
        )

    def test_group_and_personal_links_cannot_disagree(self):
        self.bind_user()
        self.store.save_group("999", {"456": record()})
        code = self.links.issue("group", "999")
        with self.assertRaisesRegex(ValueError, "其他旧群"):
            self.links.bind("group", "app", "official-group", "admin", code)

    def test_bot_collection_and_counts_merge_into_confirmed_old_bot(self):
        self.store.save_group("official-group", {"bot_qq_app": record("b.png")})
        self.store.set_draw_count("official-group", "bot_qq_app", 2)
        self.store.set_draw_count("123", "bot_99", 1)
        self.bind_group(old_bot="bot_99")
        self.assertEqual(
            self.links.bot_user("app", "official-group", "bot_qq_app"), "bot_99"
        )
        self.assertEqual(self.store.draw_count("123", "bot_99"), 3)
        self.assertEqual(len(self.store.load_group("123")["bot_99"]["unlocked"]), 2)

    def test_multi_file_write_failure_rolls_back_maps_records_and_counters(self):
        self.store.save_group("official-group", {"openid": record("b.png")})
        self.store.set_draw_count("official-group", "openid", 2)
        code = self.code()
        before = {p: p.read_bytes() for p in self.store.config_dir.glob("*.json")}
        original = module._atomic_write_json

        def failing(path, data):
            if path == self.store.draw_bonuses_path:
                raise OSError("disk full")
            original(path, data)

        with patch.object(module, "_atomic_write_json", side_effect=failing):
            with self.assertRaises(OSError):
                self.links.bind("user", "app", "official-group", "openid", code)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.store.draw_count("official-group", "openid"), 2)
        self.assertFalse(self.links.journal.exists())
        self.links.bind("user", "app", "official-group", "openid", code)

    def test_startup_recovers_interrupted_transaction_before_loading_counts(self):
        self.store.set_draw_count("123", "456", 3)
        original = self.store.draw_limits_path.read_bytes()
        self.links.journal.write_text(
            json.dumps(
                {
                    "version": 1,
                    "before": {
                        "config/draw_limits.json": base64.b64encode(original).decode(),
                        "config/identity_links.json": None,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.store.draw_limits_path.write_text("{}", encoding="utf-8")
        self.links.path.write_text("{}", encoding="utf-8")
        restored = CatalogStore(self.root, startup_migrate_legacy=False)
        self.assertEqual(restored.draw_count("123", "456"), 3)
        self.assertFalse(self.links.journal.exists())
        self.assertFalse(self.links.path.exists())

    def test_journal_rejects_paths_outside_config_before_any_restore(self):
        outside = self.root / "outside.json"
        outside.write_text("safe", encoding="utf-8")
        self.links.journal.write_text(
            json.dumps(
                {
                    "version": 1,
                    "before": {
                        "config/123.json": None,
                        "../outside.json": None,
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.links.recover()
        self.assertEqual(outside.read_text(encoding="utf-8"), "safe")
        self.assertTrue((self.store.config_dir / "123.json").exists())

    def test_identity_file_does_not_appear_as_group_and_backup_drops_codes(self):
        self.bind_user()
        self.code(user="other")
        self.assertNotIn("identity_links", self.store.group_ids())
        transfer = CatalogTransferService(self.store)
        exported = transfer.export("bundle")
        with zipfile.ZipFile(exported.path) as archive:
            state = json.loads(archive.read("config/identity_links.json"))
            self.assertTrue(state["users"])
            self.assertFalse(state["codes"])
        second_root = self.root / "restored"
        restored = CatalogStore(second_root, startup_migrate_legacy=False)
        service = CatalogTransferService(restored)
        service._apply_bundle(exported.path, "replace")
        self.assertEqual(
            restored.identity_links.resolve("app", "official-group", "openid"),
            ("123", "456"),
        )

    def test_backup_merge_keeps_other_links_and_rejects_conflicts(self):
        self.bind_user()
        first = self.links._state()
        self.bind_user(scope="other-group", old_user="789")
        second = self.links._state()
        self.assertEqual(len(module.merge_backup(first, second)["users"]), 2)
        conflicting = deepcopy(first)
        next(iter(conflicting["users"].values()))["user"] = "789"
        with self.assertRaisesRegex(ValueError, "冲突"):
            module.merge_backup(first, conflicting)

    def test_invalid_backup_cannot_point_to_a_config_file(self):
        state = module.empty_state()
        state["groups"][scope_key("app", "group")] = {"group": "draw_limits"}
        with self.assertRaises(ValueError):
            module.validate_state(state)

    def test_invalid_newer_current_does_not_replace_valid_old_ally(self):
        self.store.save_group(
            "official-group", {"openid": record("missing.png", "2026-11-11")}
        )
        self.bind_user()
        self.assertEqual(
            self.store.load_group("123")["456"]["current"]["ally_filename"], "a.png"
        )

    def test_identity_conflict_aborts_backup_before_group_overwrite(self):
        self.bind_user()
        state = self.links._state()
        next(iter(state["users"].values()))["user"] = "789"
        archive = self.root / "conflict.zip"
        with zipfile.ZipFile(archive, "w") as target:
            target.writestr("config/123.json", "{}")
            target.writestr("config/identity_links.json", json.dumps(state))
        original = self.store.load_group("123")
        with self.assertRaisesRegex(ValueError, "冲突"):
            CatalogTransferService(self.store)._apply_bundle(archive, "merge")
        self.assertEqual(self.store.load_group("123"), original)

    def test_multiple_users_keep_independent_merge_accounting(self):
        self.store.set_draw_count("official-group", "openid", 2)
        self.store.set_draw_count("official-group", "openid2", 1)
        self.bind_user()
        self.bind_user(user="openid2", old_user="789")
        self.assertEqual(self.store.draw_count("123", "456"), 2)
        self.assertEqual(self.store.draw_count("123", "789"), 1)

    def test_same_data_scope_cannot_rename_legacy_members(self):
        code = self.links.issue("group", "123")
        with self.assertRaisesRegex(ValueError, "相同"):
            self.links.bind("group", "app", "123", "admin", code)
        self.assertIn("456", self.store.load_group("123"))

    def test_auto_match_requires_admin_group_link_and_exact_profile(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称", "group_card": "群名片"}
        self.store.save_group("123", old)
        self.assertFalse(
            self.links.auto_bind_by_name("app", "official-group", "openid", "资料昵称")
        )
        self.bind_group()
        self.assertFalse(
            self.links.auto_bind_by_name("app", "official-group", "openid", "群名片")
        )
        self.assertTrue(
            self.links.auto_bind_by_name("app", "official-group", "openid", "资料昵称")
        )
        self.assertEqual(
            self.links.resolve("app", "official-group", "openid"), ("123", "456")
        )
        self.assertTrue(list((self.root / "identity-backups").glob("*.json")))

    def test_auto_match_skips_ambiguous_names_and_other_claimed_account(self):
        old = self.store.load_group("123")
        for uid in ("456", "789"):
            old[uid]["identity_names"] = {"profile": "同名"}
        self.store.save_group("123", old)
        self.bind_group()
        self.assertFalse(
            self.links.auto_bind_by_name("app", "official-group", "openid", "同名")
        )
        old["789"]["identity_names"]["profile"] = "其他"
        self.store.save_group("123", old)
        self.bind_user(user="actual-owner")
        self.assertFalse(
            self.links.auto_bind_by_name("app", "official-group", "openid", "同名")
        )

    def test_auto_match_does_not_replay_a_completed_merge(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称"}
        self.store.save_group("123", old)
        self.store.set_draw_count("official-group", "openid", 2)
        self.bind_group()
        self.assertTrue(
            self.links.auto_bind_by_name("app", "official-group", "openid", "资料昵称")
        )
        self.assertFalse(
            self.links.auto_bind_by_name("app", "official-group", "openid", "资料昵称")
        )
        self.assertEqual(self.store.draw_count("123", "456"), 2)

    def test_auto_binding_failure_leaves_no_pending_claim_code(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称"}
        self.store.save_group("123", old)
        self.bind_group()
        original = self.links.path.read_bytes()
        atomic = module._atomic_write_json

        def fail(path, data):
            if path == self.store.draw_limits_path:
                raise OSError("full")
            atomic(path, data)

        with patch.object(module, "_atomic_write_json", side_effect=fail):
            with self.assertRaises(OSError):
                self.links.auto_bind_by_name(
                    "app", "official-group", "openid", "资料昵称"
                )
        self.assertEqual(self.links.path.read_bytes(), original)


class IdentityRoutingTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.event = OfficialEvent()
        self.event.is_admin = lambda: False
        self.event.is_private_chat = lambda: not self.event.message_obj.group_id
        self.plugin = make_plugin(self.store.resolve_entry("a.png"))
        self.plugin.store = self.store
        self.plugin.config = {"daily_draw_limit": 5, "draw_cooldown_seconds": 0}
        self.plugin._daily_ally_chain = AsyncMock(return_value=[])
        self.plugin._chain_result_with_media = AsyncMock(return_value="sent")
        self.bot = identity_key(self.event)

    def link(self):
        self.bind_user(self.bot, "group-openid", "user-openid")

    async def test_old_and_official_draws_share_daily_limit(self):
        self.link()
        self.store.set_draw_count("123", "456", 4)
        results = [r async for r in self.plugin._draw_ally_impl(self.event)]
        self.assertEqual(results, ["sent"])
        old = FakeEvent("今日盟友", group_id="123", sender_id="456")
        results = [r async for r in self.plugin._draw_ally_impl(old)]
        self.assertIn("已经抽了 5 次", results[0])
        self.assertEqual(self.store.draw_count("123", "456"), 5)

    async def test_queued_draw_resolves_identity_after_binding(self):
        await self.plugin._draw_lock.acquire()
        task = asyncio.create_task(anext(self.plugin._draw_ally_impl(self.event)))
        await asyncio.sleep(0)
        self.link()
        self.plugin._draw_lock.release()
        await task
        self.assertEqual(self.store.draw_count("123", "456"), 1)
        self.assertEqual(self.store.draw_count("group-openid", "user-openid"), 0)

    async def test_personal_progress_uses_legacy_collection(self):
        self.link()
        result = [r async for r in self.plugin.personal_progress(self.event)]
        self.assertEqual(result, ["sent"])
        chain = self.plugin._chain_result_with_media.call_args.args[1]
        self.assertIn("已解锁：1/2", chain[0].text)

    async def test_no_argument_query_uses_own_record_without_exposing_others(self):
        self.link()
        user = record(day=get_today())
        self.store.save_group("123", {"456": user, "789": record("b.png", get_today())})
        self.event.message_str = "查盟友"
        self.plugin._ally_chain = AsyncMock(return_value=[])
        result = [r async for r in self.plugin.query_ally(self.event)]
        self.assertEqual(result, ["sent"])
        self.event.message_str = "查盟友 789"
        result = [r async for r in self.plugin.query_ally(self.event)]
        self.assertIn("没有找到盟友", result[0])
        self.assertNotIn("旧昵称", result[0])

    async def test_official_bonus_targets_linked_openid(self):
        self.link()
        self.event.message_str = "增加今日抽取次数 user-openid 2"
        results = [r async for r in self.plugin.add_member_draw_count(self.event)]
        self.assertIn("增加 2 次", results[0])
        self.assertEqual(self.store.draw_bonus("123", "456"), 2)

    async def test_issue_code_requires_private_old_onebot_and_own_account(self):
        identity = member_key(self.bot, "group-openid", "user-openid")[:24]
        old = FakeEvent(f"盟友绑定码 123 {identity}", group_id="", sender_id="456")
        old.get_platform_name = lambda: "aiocqhttp"
        old.is_private_chat = lambda: True
        results = [r async for r in self.plugin.issue_ally_link(old)]
        self.assertIn("你的绑定码", results[0])
        old.sender_id = "777"
        results = [r async for r in self.plugin.issue_ally_link(old)]
        self.assertIn("没有找到", results[0])
        old.message_obj.group_id = "123"
        results = [r async for r in self.plugin.issue_ally_link(old)]
        self.assertIn("只在私聊", results[0])

    async def test_admin_codes_and_group_consumption_check_permissions(self):
        self.event.message_str = "盟友群绑定码 123"
        results = [r async for r in self.plugin.admin_issue_group_link(self.event)]
        self.assertIn("仅限", results[0])
        self.event.is_admin = lambda: True
        results = [r async for r in self.plugin.admin_issue_group_link(self.event)]
        self.assertIn("私聊", results[0])
        code = self.links.issue("group", "123")
        self.event.message_str = "绑定盟友群 " + code
        self.event.is_admin = lambda: False
        results = [r async for r in self.plugin.bind_ally_group(self.event)]
        self.assertIn("仅限", results[0])
        self.event.is_admin = lambda: True
        results = [r async for r in self.plugin.bind_ally_group(self.event)]
        self.assertIn("已关联旧群", results[0])

    async def test_bound_user_display_name_falls_back_to_legacy_nickname(self):
        self.link()
        self.event.sender_name = "user-openid"
        self.assertEqual(self.plugin._data_name(self.event), "旧昵称")

    async def test_group_namespace_does_not_claim_personal_identity_in_status(self):
        self.bind_group(self.bot, "group-openid")
        results = [r async for r in self.plugin.ally_identity_status(self.event)]
        self.assertIn("尚未绑定旧账号", results[0])
        self.assertIn("目标身份码", results[0])
        self.assertNotIn("user-openid", results[0])

    async def test_group_bot_binding_keeps_manual_identity_override(self):
        current = self.plugin._bot_identity(self.event)
        self.store.save_group("group-openid", {current: record("b.png")})
        code = self.links.issue("group", "123", bot_user="bot_99")
        self.links.bind(
            "group", self.bot, "group-openid", "admin", code, current_bot=current
        )
        self.assertEqual(self.plugin._bot_identity(self.event), "bot_99")
        self.plugin.config["bot_draw_identity"] = "222"
        self.assertEqual(self.plugin._bot_identity(self.event), "bot_222")

    async def test_group_links_survive_new_server_adapter_id(self):
        self.link()
        self.bind_group(self.bot, "group-openid")
        self.event.platform_meta.id = "new-server-adapter"
        self.assertEqual(self.plugin._data_context(self.event), ("123", "456"))
        self.assertEqual(self.plugin._collection_group_id(self.event), "123")
        other = OfficialEvent(app="different-app")
        self.assertEqual(
            self.plugin._data_context(other), ("group-openid", "user-openid")
        )

    async def test_private_link_merges_existing_data_and_survives_new_server(self):
        event = OfficialEvent(group="", user="private-openid")
        raw_scope = self.plugin._group_id(event)
        identity_scope = self.plugin._identity_scope(event)
        self.store.save_group(raw_scope, {"private-openid": record("b.png")})
        code = self.code(self.bot, identity_scope, "private-openid")
        event.message_str = "绑定盟友 " + code
        results = [r async for r in self.plugin.bind_ally_identity(event)]
        self.assertIn("已关联旧群", results[0])
        self.assertEqual(len(self.store.load_group("123")["456"]["unlocked"]), 2)
        event.platform_meta.id = "new-server"
        self.assertEqual(self.plugin._data_context(event), ("123", "456"))
        self.assertFalse(self.store.load_group(raw_scope))

    async def test_personal_gallery_uses_old_user_unlocked_set(self):
        self.link()
        with patch.object(
            self.store, "render_gallery_pages", return_value=[]
        ) as render:
            results = [r async for r in self.plugin.personal_gallery(self.event)]
        self.assertEqual(results, ["sent"])
        self.assertEqual(render.call_args.args[1], {"a.png"})

    async def test_group_leaderboard_and_reset_use_admin_link(self):
        self.link()
        self.bind_group(self.bot, "group-openid")
        self.store.set_draw_count("123", "456", 3)
        results = [r async for r in self.plugin.leaderboard(self.event)]
        self.assertIn("旧昵称", results[0])
        results = [r async for r in self.plugin.reset_group_draw_counts(self.event)]
        self.assertIn("已重置", results[0])
        self.assertEqual(self.store.draw_count("123", "456"), 0)

    async def test_old_draw_records_profile_nickname_and_group_card_separately(self):
        old = FakeEvent(
            "今日盟友", group_id="123", sender_id="456", sender_name="群名片"
        )
        old.get_platform_name = lambda: "aiocqhttp"
        old.message_obj.raw_message = {
            "sender": {"nickname": "资料昵称", "card": "群名片"}
        }
        await anext(self.plugin._draw_ally_impl(old))
        user = self.store.load_group("123")["456"]
        self.assertEqual(user["identity_names"]["profile"], "资料昵称")
        self.assertEqual(user["identity_names"]["group_card"], "群名片")
        rows = CatalogTransferService(self.store)._group_rows("123")
        self.assertEqual(
            next(row for row in rows if row["user_id"] == "456")["profile_nickname"],
            "资料昵称",
        )

    async def test_official_draw_auto_migrates_only_when_configured(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称"}
        self.store.save_group("123", old)
        self.bind_group(self.bot, "group-openid")
        self.event.message_obj.raw_message.author.username = "资料昵称"
        self.plugin.config["qq_official_auto_migrate_by_name"] = True
        results = [r async for r in self.plugin._draw_ally_impl(self.event)]
        self.assertEqual(results, ["sent"])
        self.assertEqual(self.plugin._data_context(self.event), ("123", "456"))
        self.assertEqual(self.store.draw_count("123", "456"), 1)
        self.assertFalse(self.links._state()["codes"])

    async def test_no_actual_official_username_never_uses_display_name_to_claim(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "狐狸"}
        self.store.save_group("123", old)
        self.bind_group(self.bot, "group-openid")
        self.plugin.config["qq_official_auto_migrate_by_name"] = True
        await anext(self.plugin._draw_ally_impl(self.event))
        self.assertNotEqual(self.plugin._data_context(self.event), ("123", "456"))

    async def test_name_matching_is_disabled_by_default(self):
        old = self.store.load_group("123")
        old["456"]["identity_names"] = {"profile": "资料昵称"}
        self.store.save_group("123", old)
        self.bind_group(self.bot, "group-openid")
        self.event.message_obj.raw_message.author.username = "资料昵称"
        await anext(self.plugin._draw_ally_impl(self.event))
        self.assertNotEqual(self.plugin._data_context(self.event), ("123", "456"))
