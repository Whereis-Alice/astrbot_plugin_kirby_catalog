from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore
from astrbot_plugin_kirby_catalog.webui import (
    MAX_UPLOAD_BYTES,
    MODERN_WEB_API,
    PLUGIN_ID,
    THUMBNAIL_CACHE_LIMIT,
    CatalogAdminService,
    KirbyCatalogWebUI,
    _bounded_int,
    _decode_terminology_id,
    _normalize_theme,
)
from astrbot_plugin_kirby_catalog.wiki_index import WikiIndexStore
from astrbot_plugin_kirby_catalog.terminology import (
    KirbyTerminologyStore,
    TerminologyEntry,
    terminology_document,
)


class CatalogAdminServiceTests(unittest.TestCase):
    def make_image(self, color=(240, 94, 145), size=(48, 48)) -> bytes:
        output = BytesIO()
        Image.new("RGBA", size, color).save(output, format="PNG")
        return output.getvalue()

    def make_service(self, root: Path) -> tuple[CatalogStore, CatalogAdminService]:
        store = CatalogStore(root / "data", image_base_url="")
        return store, CatalogAdminService(store)

    def test_lists_filters_and_thumbnails_catalog_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            first = store.add_asset(
                "卡比（Kirby）",
                self.make_image(),
                "星之卡比",
                description="粉红色的英雄。",
                updated_by="admin",
            )
            second = store.add_asset(
                "利剑卡比（Sword Kirby）",
                self.make_image((84, 169, 212)),
                "星之卡比 梦之泉物语",
            )
            store._catalog[second["filename"]]["catalog_kind"] = "ability"
            store._save_catalog()

            result = service.list_entries(
                {
                    "query": "Sword",
                    "kind": "ability",
                    "status": "missing_description",
                    "page": 1,
                    "page_size": 30,
                }
            )

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["id"], second["id"])
            self.assertTrue(
                result["items"][0]["thumbnail"].startswith("data:image/webp;base64,")
            )
            detail = service.entry_detail(first["id"])
            self.assertEqual(detail["description"], "粉红色的英雄。")
            self.assertEqual(detail["description_origin"], "override")

    def test_stages_and_adds_asset_with_description_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            staged = service.stage_upload(self.make_image(size=(96, 72)))

            added = service.add_entry(
                {
                    "upload_token": staged["token"],
                    "name": "新盟友",
                    "source": "测试作品",
                    "description": "这是通过管理台新增的简介。",
                },
                "alice",
            )

            self.assertEqual(added["name"], "新盟友")
            self.assertEqual(added["source"], "测试作品")
            self.assertEqual(added["description"], "这是通过管理台新增的简介。")
            self.assertFalse(list(service.upload_dir.glob(f"{staged['token']}.*")))
            self.assertTrue(
                store.asset_path(store.resolve_entry(str(added["id"]))).is_file()
            )
            self.assertEqual(store.audit_entries(1)[0]["action"], "entry.add")

    def test_rejects_invalid_and_oversized_uploads(self):
        with tempfile.TemporaryDirectory() as temp:
            _store, service = self.make_service(Path(temp))

            with self.assertRaisesRegex(ValueError, "图片格式无法识别"):
                service.stage_upload(b"not-an-image")
            with self.assertRaisesRegex(ValueError, "16 MB"):
                service.stage_upload(b"x" * (MAX_UPLOAD_BYTES + 1))

    def test_entry_update_validates_description_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            entry = store.add_asset("旧名字", self.make_image(), "旧作品")

            with patch.object(store, "update_entry_details") as update:
                with self.assertRaisesRegex(ValueError, "30000"):
                    service.save_entry(
                        {
                            "id": entry["id"],
                            "name": "新名字",
                            "source": "新作品",
                            "description_action": "set",
                            "description": "长" * 30001,
                        },
                        "admin",
                    )

            update.assert_not_called()
            unchanged = store.resolve_entry(str(entry["id"]))
            self.assertEqual(unchanged["name"], "旧名字")
            self.assertEqual(unchanged["source"], "旧作品")

    def test_entry_update_rolls_back_rename_and_references_on_description_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            entry = store.add_asset("旧名字", self.make_image(), "旧作品")
            store.save_group(
                "100",
                {
                    "42": {
                        "current": {
                            "ally_filename": entry["filename"],
                            "date": "2026-08-05",
                        },
                        "unlocked": [
                            {
                                "ally_filename": entry["filename"],
                                "unlock_date": "2026-08-01",
                            }
                        ],
                        "nickname": "爱丽丝",
                    }
                },
            )
            original_save = store._save_description_overrides
            failed = False

            def fail_once():
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("simulated description failure")
                return original_save()

            with patch.object(
                store, "_save_description_overrides", side_effect=fail_once
            ):
                with self.assertRaisesRegex(OSError, "simulated description failure"):
                    service.save_entry(
                        {
                            "id": entry["id"],
                            "name": "新名字",
                            "source": "新作品",
                            "description_action": "set",
                            "description": "不应留下的简介",
                        },
                        "admin",
                    )

            restored = store.resolve_entry(str(entry["id"]))
            self.assertEqual(restored["name"], "旧名字")
            self.assertEqual(restored["source"], "旧作品")
            self.assertEqual(restored["filename"], entry["filename"])
            self.assertTrue((store.assets_dir / entry["filename"]).is_file())
            self.assertFalse((store.assets_dir / "新作品.新名字.png").exists())
            user = store.load_group("100")["42"]
            self.assertEqual(user["current"]["ally_filename"], entry["filename"])
            self.assertEqual(user["unlocked"][0]["ally_filename"], entry["filename"])
            self.assertEqual(store.description_for(restored), "")

    def test_manages_group_member_progress_and_daily_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            kirby = store.add_asset("卡比", self.make_image(), "星之卡比")
            dee = store.add_asset(
                "瓦豆鲁迪", self.make_image((240, 174, 70)), "星之卡比"
            )
            store.save_group(
                "100",
                {
                    "42": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": [],
                        "nickname": "旧昵称",
                    }
                },
            )

            saved = service.save_user(
                {
                    "group_id": "100",
                    "user_id": "42",
                    "nickname": "爱丽丝",
                    "no_new_count": 3,
                    "current_id": kirby["id"],
                    "current_date": "2026-08-05",
                    "draw_count": 2,
                    "draw_bonus": 1,
                },
                "admin",
            )
            self.assertEqual(saved["nickname"], "爱丽丝")
            self.assertEqual(saved["current"]["id"], kirby["id"])
            self.assertEqual(saved["draw_count"], 2)
            self.assertEqual(saved["draw_bonus"], 1)

            added = service.change_unlock(
                {
                    "group_id": "100",
                    "user_id": "42",
                    "entry_id": dee["id"],
                    "action": "add",
                    "unlock_date": "2026-08-01",
                },
                "admin",
            )
            self.assertEqual(added["unlocked"], 1)
            self.assertEqual(added["unlocks"][0]["id"], dee["id"])

            groups = service.list_groups({"page": 1, "page_size": 30})
            self.assertEqual(groups["items"][0]["group_id"], "100")
            self.assertEqual(groups["items"][0]["unique_unlocks"], 1)
            users = service.list_users({"group_id": "100", "page": 1, "page_size": 30})
            self.assertEqual(users["items"][0]["completion"], 50.0)

            reset = service.reset_group_draws({"group_id": "100"}, "admin")
            self.assertEqual(reset["users"], 1)
            self.assertEqual(store.draw_count("100", "42"), 0)
            self.assertEqual(store.draw_bonus("100", "42"), 0)

            removed = service.change_unlock(
                {
                    "group_id": "100",
                    "user_id": "42",
                    "entry_id": dee["id"],
                    "action": "remove",
                },
                "admin",
            )
            self.assertEqual(removed["unlocked"], 0)

    def test_member_update_rolls_back_record_and_counts_on_second_counter_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            entry = store.add_asset("卡比", self.make_image(), "星之卡比")
            store.save_group(
                "100",
                {
                    "42": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": [],
                        "nickname": "旧昵称",
                        "no_new_count": 1,
                    }
                },
            )
            store.set_draw_count("100", "42", 1)
            store.set_draw_bonus("100", "42", 2)
            original_save = store._save_draw_bonuses
            failed = False

            def fail_once():
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("simulated bonus failure")
                return original_save()

            with patch.object(store, "_save_draw_bonuses", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "simulated bonus failure"):
                    service.save_user(
                        {
                            "group_id": "100",
                            "user_id": "42",
                            "nickname": "新昵称",
                            "no_new_count": 9,
                            "current_id": entry["id"],
                            "current_date": "2026-08-05",
                            "draw_count": 7,
                            "draw_bonus": 8,
                        },
                        "admin",
                    )

            user = store.load_group("100")["42"]
            self.assertEqual(user["nickname"], "旧昵称")
            self.assertEqual(user["no_new_count"], 1)
            self.assertEqual(user["current"]["ally_filename"], "")
            self.assertEqual(store.draw_count("100", "42"), 1)
            self.assertEqual(store.draw_bonus("100", "42"), 2)

    def test_preferences_and_audit_history_are_persistent_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store, service = self.make_service(root)
            self.assertEqual(
                service.save_preferences({"theme": "dark"}, "alice"),
                {"theme": "starlight"},
            )
            self.assertEqual(service.preferences("alice"), {"theme": "starlight"})

            with patch.object(store, "_save_audit_entries"):
                for index in range(1005):
                    store.append_audit("test", str(index), "record", "alice")
            store._save_audit_entries()
            self.assertEqual(len(store.audit_entries(500)), 500)
            self.assertEqual(len(store._audit_entries), 1000)
            self.assertEqual(store.audit_entries(1)[0]["target"], "1004")

            reloaded = CatalogStore(root / "data", image_base_url="")
            self.assertEqual(len(reloaded._audit_entries), 1000)
            reloaded_service = CatalogAdminService(reloaded)
            self.assertEqual(
                reloaded_service.preferences("alice"), {"theme": "starlight"}
            )

    def test_manages_terminology_overrides_and_does_not_create_group_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "data", image_base_url="")
            bundled_path = root / "bundled-terminology.json"
            bundled_path.write_text(
                json.dumps(
                    terminology_document(
                        [
                            TerminologyEntry.from_mapping(
                                {
                                "term_id": "character:kirby",
                                "category": "character",
                                "zh_cn": "卡比",
                                "en": "Kirby",
                                "ja": "カービィ",
                                "zh_status": "official",
                                }
                            )
                        ]
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            terminology = KirbyTerminologyStore(
                bundled_path,
                store.config_dir / "terminology_overrides.json",
            )
            service = CatalogAdminService(store, terminology)

            listed = service.list_terminology({"query": "Kirby", "page": 1})
            self.assertEqual(listed["total"], 1)
            self.assertEqual(store.group_ids(), [])

            saved = service.save_terminology(
                {
                    "term_id": "character:kirby",
                    "category": "character",
                    "zh_cn": "星之卡比",
                    "en": "Kirby",
                    "ja": "カービィ",
                    "zh_status": "official",
                    "priority": 200,
                    "enabled": True,
                },
                "admin",
            )
            self.assertEqual(saved["canonical_label"], "星之卡比（Kirby）")
            self.assertEqual(saved["origin"], "override")
            self.assertEqual(terminology.canonicalize("Kirby"), "星之卡比（Kirby）")

            exported = service.export_terminology("json")
            self.assertIn("星之卡比", base64.b64decode(exported["content_base64"]).decode("utf-8"))

            service.restore_terminology("character:kirby", "admin")
            self.assertEqual(terminology.canonicalize("Kirby"), "卡比（Kirby）")
            self.assertEqual(service.terminology_detail("character:kirby")["origin"], "bundled")

            custom = service.save_terminology(
                {
                    "category": "special",
                    "zh_cn": "自定义术语",
                    "en": "Custom Term",
                    "ja": "カスタム用語",
                },
                "admin",
            )
            deleted = service.restore_terminology(custom["term_id"], "admin")
            self.assertTrue(deleted["deleted"])
            self.assertIsNone(terminology.entry(custom["term_id"]))

    def make_terminology(self, root: Path):
        store = CatalogStore(root / "data", image_base_url="")
        bundled_path = root / "bundled-terminology.json"
        bundled_path.write_text(
            json.dumps(
                terminology_document(
                    [
                        TerminologyEntry.from_mapping(
                            {
                                "term_id": "character:kirby",
                                "category": "character",
                                "zh_cn": "卡比",
                                "en": "Kirby",
                                "ja": "カービィ",
                                "zh_status": "official",
                            }
                        )
                    ]
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        terminology = KirbyTerminologyStore(
            bundled_path,
            store.config_dir / "terminology_overrides.json",
        )
        return store, terminology, CatalogAdminService(store, terminology)

    def test_theme_whitelist_migrates_legacy_values_and_rejects_unknown(self):
        self.assertEqual(_normalize_theme("  Dreamland "), "dreamland")
        self.assertEqual(_normalize_theme("KIRBY"), "dreamland")
        self.assertEqual(_normalize_theme("light"), "dreamland")
        self.assertEqual(_normalize_theme("dark"), "starlight")
        self.assertEqual(_normalize_theme("meta"), "metaknight")
        self.assertEqual(_normalize_theme("rainbow"), "")
        self.assertEqual(_normalize_theme(None), "")
        self.assertEqual(_normalize_theme(["dreamland"]), "")

        with tempfile.TemporaryDirectory() as temp:
            _store, service = self.make_service(Path(temp))
            for theme in ("auto", "dreamland", "starlight", "metaknight"):
                self.assertEqual(
                    service.save_preferences({"theme": theme}, "alice"),
                    {"theme": theme},
                )
                self.assertEqual(service.preferences("alice"), {"theme": theme})

            self.assertEqual(
                service.save_preferences({"theme": "kirby"}, "bob"),
                {"theme": "dreamland"},
            )
            self.assertEqual(service.preferences("bob"), {"theme": "dreamland"})
            self.assertEqual(
                service.save_preferences({"theme": " DARK "}, "bob"),
                {"theme": "starlight"},
            )
            self.assertEqual(service.preferences("bob"), {"theme": "starlight"})
            self.assertEqual(
                service.save_preferences({"theme": "meta"}, "bob"),
                {"theme": "metaknight"},
            )
            self.assertEqual(service.preferences("bob"), {"theme": "metaknight"})

            with self.assertRaisesRegex(ValueError, "主题选项无效"):
                service.save_preferences({"theme": "rainbow"}, "bob")
            self.assertEqual(service.preferences("bob"), {"theme": "metaknight"})

            service.preferences_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "users": {
                            "carol": {"theme": "kirby"},
                            "dave": {"theme": "neon"},
                            "erin": {"theme": ["broken"]},
                            "frank": "not-a-mapping",
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(service.preferences("carol"), {"theme": "dreamland"})
            self.assertEqual(service.preferences("dave"), {"theme": "auto"})
            self.assertEqual(service.preferences("erin"), {"theme": "auto"})
            self.assertEqual(service.preferences("frank"), {"theme": "auto"})
            self.assertEqual(service.preferences("nobody"), {"theme": "auto"})

    def test_bounded_int_clamps_into_range(self):
        self.assertEqual(_bounded_int(250, 100, 1, 500), 250)
        self.assertEqual(_bounded_int(1, 100, 1, 500), 1)
        self.assertEqual(_bounded_int(500, 100, 1, 500), 500)
        self.assertEqual(_bounded_int(0, 100, 1, 500), 1)
        self.assertEqual(_bounded_int(-9, 100, 1, 500), 1)
        self.assertEqual(_bounded_int(10**9, 100, 1, 500), 500)
        self.assertEqual(_bounded_int("42", 100, 1, 500), 42)
        self.assertEqual(_bounded_int(None, 100, 1, 500), 100)
        self.assertEqual(_bounded_int("abc", 100, 1, 500), 100)
        self.assertEqual(_bounded_int(THUMBNAIL_CACHE_LIMIT, 100, 1, 500), 500)

    @staticmethod
    def stable_export(raw: bytes, format_name: str):
        """Drop the export timestamp so two exports can be compared."""
        if format_name.casefold() != "json":
            return raw
        document = json.loads(raw.decode("utf-8"))
        document.pop("generated_at", None)
        return document

    def test_export_terminology_bytes_matches_base64_export(self):
        with tempfile.TemporaryDirectory() as temp:
            _store, _terminology, service = self.make_terminology(Path(temp))

            cases = (
                ("json", "merged", "application/json; charset=utf-8"),
                ("JSON", "override", "application/json; charset=utf-8"),
                ("csv", "overrides", "text/csv; charset=utf-8"),
                ("csv", "merged", "text/csv; charset=utf-8"),
            )
            for format_name, scope, mime in cases:
                with self.subTest(format=format_name, scope=scope):
                    data, filename, mime_type, revision = (
                        service.export_terminology_bytes(format_name, scope)
                    )
                    legacy = service.export_terminology(format_name, scope)
                    label = (
                        "overrides"
                        if scope in {"override", "overrides"}
                        else "merged"
                    )
                    expected_name = (
                        f"kirby_terminology_{label}.{format_name.casefold()}"
                    )
                    self.assertIsInstance(data, bytes)
                    self.assertEqual(
                        self.stable_export(data, format_name),
                        self.stable_export(
                            base64.b64decode(legacy["content_base64"]), format_name
                        ),
                    )
                    self.assertEqual(filename, expected_name)
                    self.assertEqual(filename, legacy["filename"])
                    self.assertEqual(mime_type, mime)
                    self.assertEqual(mime_type, legacy["mime_type"])
                    self.assertEqual(revision, legacy["revision"])

            merged = service.export_terminology_bytes("json")[0].decode("utf-8")
            self.assertIn("卡比", merged)
            with self.assertRaisesRegex(ValueError, "JSON 或 CSV"):
                service.export_terminology_bytes("xml")

    def test_list_users_sorts_by_unlocks_then_nickname(self):
        with tempfile.TemporaryDirectory() as temp:
            store, service = self.make_service(Path(temp))
            entry = store.add_asset("卡比", self.make_image(), "星之卡比")
            unlocked = [
                {"ally_filename": entry["filename"], "unlock_date": "2026-08-01"}
            ]
            store.save_group(
                "100",
                {
                    "1": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": list(unlocked),
                        "nickname": "Z",
                    },
                    "2": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": list(unlocked),
                        "nickname": "A",
                    },
                    "3": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": [],
                        "nickname": "B",
                    },
                },
            )

            result = service.list_users(
                {"group_id": "100", "page": 1, "page_size": 30}
            )

            self.assertEqual(
                [row["nickname"] for row in result["items"]], ["A", "Z", "B"]
            )
            self.assertEqual([row["unlocked"] for row in result["items"]], [1, 1, 0])

    def test_manages_all_wiki_indexes_without_creating_group_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "data", image_base_url="")
            kirby = store.add_asset("卡比（Kirby）", self.make_image(), "星之卡比")
            dee = store.add_asset("瓦豆鲁迪（Waddle Dee）", self.make_image(), "星之卡比")
            store._catalog[kirby["filename"]]["page_title"] = "Kirby"
            store._catalog[dee["filename"]]["page_title"] = "Waddle Dee"
            store._save_catalog()
            shinkaku_path = root / "shinkaku.json"
            shinkaku_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "id": "shinkaku:fighter-rbp",
                                "catalog_index": 88,
                                "title_zh": "格斗家（星之卡比 机器人星球）",
                                "title_en": "Fighter (Kirby: Planet Robobot)",
                                "title_ja": "ファイター(RBP)",
                                "game_zh": "星之卡比 机器人星球",
                                "section_zh": "能力",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            wiki_index = WikiIndexStore(store, shinkaku_path)
            service = CatalogAdminService(store, wiki_index=wiki_index)

            self.assertEqual(
                service.list_wiki_index({"site": "wikirby", "page": 1})["total"],
                2,
            )
            self.assertEqual(
                service.list_wiki_index({"site": "fandom", "page": 1})["total"],
                2,
            )
            shinkaku = service.list_wiki_index(
                {"site": "shinkaku", "query": "机器人星球", "page": 1}
            )
            self.assertEqual(shinkaku["items"][0]["number"], 88)

            detail = service.wiki_index_detail("wikirby", f"catalog:{kirby['entry_key']}")
            saved = service.save_wiki_index(
                {
                    "site": "wikirby",
                    "key": detail["key"],
                    "number": 9001,
                    "target": "Kirby (series character)",
                    "enabled": False,
                },
                "admin",
            )
            self.assertTrue(saved["has_override"])
            self.assertFalse(saved["enabled"])
            self.assertIsNone(wiki_index.resolve("wikirby", 9001))
            with self.assertRaisesRegex(ValueError, "已由"):
                service.save_wiki_index(
                    {
                        "site": "fandom",
                        "key": f"catalog:{kirby['entry_key']}",
                        "number": dee["id"],
                        "target": "Kirby",
                    },
                    "admin",
                )

            restored = service.restore_wiki_index(
                "wikirby", detail["key"], "admin"
            )
            self.assertFalse(restored["has_override"])
            self.assertEqual(restored["number"], kirby["id"])
            self.assertEqual(store.group_ids(), [])


class KirbyCatalogWebUiRegistrationTests(unittest.TestCase):
    def test_decodes_legacy_terminology_path_ids(self):
        self.assertEqual(_decode_terminology_id("ability:hydra"), "ability:hydra")
        self.assertEqual(_decode_terminology_id("ability%3Ahydra"), "ability:hydra")
        self.assertEqual(_decode_terminology_id("ability%253Ahydra"), "ability:hydra")

    def test_registers_all_routes_with_plugin_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "data", image_base_url="")
            routes = []
            context = SimpleNamespace(
                register_web_api=lambda *args: routes.append(args)
            )
            webui = KirbyCatalogWebUI(context, store, asyncio.Lock())

            webui.register()

            self.assertEqual(len(routes), 38)
            self.assertTrue(
                all(route[0].startswith(f"/{PLUGIN_ID}/admin/") for route in routes)
            )
            paths = {route[0] for route in routes}
            self.assertIn(f"/{PLUGIN_ID}/admin/entries/<entry_id>/image", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/groups/user/save", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/trash/restore", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/terminology-entry", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/terminology/entry", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/terminology/<term_id>", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/terminology/download", paths)
            ordered = [route[0] for route in routes]
            self.assertLess(
                ordered.index(f"/{PLUGIN_ID}/admin/terminology/download"),
                ordered.index(f"/{PLUGIN_ID}/admin/terminology/<term_id>"),
            )
            self.assertIn(f"/{PLUGIN_ID}/admin/terminology/save", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/wiki-index", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/wiki-index-entry", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/wiki-index/save", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/wiki-index/restore", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/manifest", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/download", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/stage/<dataset>", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/stage", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/apply", paths)
            self.assertIn(f"/{PLUGIN_ID}/admin/transfer/discard", paths)

    def test_register_replaces_stale_legacy_terminology_route(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "data", image_base_url="")
            legacy_path = f"/{PLUGIN_ID}/admin/terminology/<term_id>"

            async def stale_handler(term_id):
                return term_id

            routes = [(legacy_path, stale_handler, ["GET"], "stale")]

            def register(*route):
                for index, current in enumerate(routes):
                    if current[0] == route[0] and current[2] == route[2]:
                        routes[index] = route
                        return
                routes.append(route)

            webui = KirbyCatalogWebUI(
                SimpleNamespace(register_web_api=register),
                store,
                asyncio.Lock(),
            )
            webui.register()

            registered = next(route for route in routes if route[0] == legacy_path)
            self.assertIs(registered[1].__self__, webui)
            self.assertIs(
                registered[1].__func__,
                webui.terminology_entry_path.__func__,
            )

    @unittest.skipIf(
        MODERN_WEB_API, "quart fallback path is exercised on AstrBot < 4.26"
    )
    def test_download_terminology_streams_raw_bytes_with_attachment_headers(self):
        import quart

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "data", image_base_url="")
            bundled_path = root / "bundled-terminology.json"
            bundled_path.write_text(
                json.dumps(
                    terminology_document(
                        [
                            TerminologyEntry.from_mapping(
                                {
                                    "term_id": "character:kirby",
                                    "category": "character",
                                    "zh_cn": "卡比",
                                    "en": "Kirby",
                                    "ja": "カービィ",
                                    "zh_status": "official",
                                }
                            )
                        ]
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            terminology = KirbyTerminologyStore(
                bundled_path,
                store.config_dir / "terminology_overrides.json",
            )
            webui = KirbyCatalogWebUI(
                SimpleNamespace(register_web_api=lambda *args: None),
                store,
                asyncio.Lock(),
                terminology,
            )
            app = quart.Quart(__name__)
            expected = json.loads(
                webui.service.export_terminology_bytes("json", "merged")[0]
            )
            expected.pop("generated_at", None)

            async def download(query):
                async with app.test_request_context(
                    "/download", method="GET", query_string=query
                ):
                    response = await webui.download_terminology()
                    return (
                        response.status_code,
                        dict(response.headers),
                        await response.get_data(),
                    )

            status, headers, body = asyncio.run(
                download({"format": "json", "scope": "merged"})
            )

            self.assertEqual(status, 200)
            document = json.loads(body)
            document.pop("generated_at", None)
            self.assertEqual(document, expected)
            self.assertIn("卡比", body.decode("utf-8"))
            self.assertEqual(
                headers["Content-Disposition"],
                'attachment; filename="kirby_terminology_merged.json"; '
                "filename*=UTF-8''kirby_terminology_merged.json",
            )
            self.assertEqual(headers["Content-Length"], str(len(body)))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("application/json", headers["Content-Type"])

            status, headers, body = asyncio.run(
                download({"format": "csv", "scope": "override"})
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                headers["Content-Disposition"],
                'attachment; filename="kirby_terminology_overrides.csv"; '
                "filename*=UTF-8''kirby_terminology_overrides.csv",
            )
            self.assertIn("text/csv", headers["Content-Type"])

            status, _headers, body = asyncio.run(download({"format": "xml"}))
            self.assertEqual(status, 400)
            self.assertIn("JSON", json.loads(body)["message"])

    def test_page_bundle_is_modular_bridge_only_and_self_contained(self):
        plugin_root = Path(__file__).parents[1]
        page_root = plugin_root / "pages" / "catalog-admin"
        index = (page_root / "index.html").read_text(encoding="utf-8")
        boot = (page_root / "app.js").read_text(encoding="utf-8")
        script_files = sorted(page_root.glob("js/**/*.js"))
        style_files = sorted(page_root.glob("styles/*.css"))
        script = boot + "\n".join(
            path.read_text(encoding="utf-8") for path in script_files
        )
        styles = "\n".join(
            path.read_text(encoding="utf-8") for path in style_files
        )
        # Calls are formatted across lines by prettier, so match on a collapsed copy.
        calls = re.sub(r"\s+", "", script)

        # The legacy monolith and the vendored icon bundle are gone for good.
        self.assertFalse((page_root / "style.css").exists())
        self.assertFalse((page_root / "vendor").exists())
        self.assertGreaterEqual(len(script_files), 20)
        self.assertEqual(
            [path.name for path in style_files],
            ["base.css", "components.css", "tokens.css", "views.css"],
        )

        # No external network dependency of any kind.
        for blob in (index, script, styles):
            lowered = blob.lower()
            self.assertNotIn("cdn.", lowered)
            self.assertNotIn("https://unpkg", lowered)
            self.assertNotIn("@import", lowered)

        # Styles are linked in cascade order; the entry point is an ES module.
        for name in ("tokens", "base", "components", "views"):
            self.assertIn(f'href="./styles/{name}.css"', index)
        self.assertIn('<script type="module" src="./app.js"', index)
        self.assertLess(
            index.index('href="./styles/tokens.css"'),
            index.index('href="./styles/views.css"'),
        )

        # Every relative asset reference resolves to a file that ships with the
        # plugin, because the host rewrites static literals only.
        references = set(
            re.findall(r'(?:href|src)="(\./[^"]+)"', index)
        )
        self.assertTrue(references)
        for reference in references:
            self.assertTrue(
                (page_root / reference[2:]).is_file(),
                f"index.html references missing asset {reference}",
            )
        for path in [page_root / "app.js", *script_files]:
            body = path.read_text(encoding="utf-8")
            for specifier in re.findall(
                r'^\s*(?:import|export)[^\n]*?from\s+"(\.[^"]+)"',
                body,
                re.MULTILINE,
            ):
                target = (path.parent / specifier).resolve()
                self.assertTrue(
                    target.is_file(),
                    f"{path.name} imports missing module {specifier}",
                )
            self.assertNotIn("await import(", body)

        # Icons are inlined SVG, not a runtime icon library lookup.
        glyphs = (page_root / "js" / "core" / "icon-glyphs.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('"<path d=', glyphs)
        self.assertGreaterEqual(glyphs.count('": "'), 80)
        renderer = (page_root / "js" / "core" / "icons.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("stroke-linecap", renderer)
        self.assertIn("ICON_GLYPHS", renderer)
        self.assertNotIn("lucide", index.lower())
        self.assertTrue(
            "window.lucide" not in script,
            "icons must not depend on a runtime icon library",
        )

        # The page talks to the host through the injected bridge only.
        bridge = (page_root / "js" / "core" / "bridge.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.AstrBotPluginPage", bridge)
        self.assertIn("bridge.onContext ===", boot)
        self.assertIn("bridge.onContextChange ===", boot)
        self.assertTrue("fetch(" not in script, "page must not bypass the bridge")
        self.assertTrue(
            "XMLHttpRequest" not in script, "page must not bypass the bridge"
        )

        # Each backend endpoint stays wired to the rebuilt front end.
        for endpoint in (
            'apiGet("admin/summary"',
            'apiPost("admin/preferences"',
            'apiGet("admin/entries"',
            'apiPost("admin/entries/save"',
            'apiPost("admin/entries/add"',
            'apiPost("admin/entries/delete"',
            'apiUpload("admin/uploads/image"',
            'apiGet("admin/trash"',
            'apiPost("admin/trash/restore"',
            'apiGet("admin/groups"',
            'apiGet("admin/groups/users"',
            'apiGet("admin/groups/user"',
            'apiPost("admin/groups/user/save"',
            'apiPost("admin/groups/user/unlock"',
            'apiPost("admin/groups/user/delete"',
            'apiPost("admin/groups/reset-draws"',
            'apiGet("admin/audit"',
            'apiGet("admin/terminology"',
            'apiGet("admin/terminology-entry"',
            'apiPost("admin/terminology/save"',
            'apiPost("admin/terminology/restore"',
            'apiDownload("admin/terminology/download"',
            'apiUpload("admin/terminology/import"',
            'apiGet("admin/wiki-index"',
            'apiGet("admin/wiki-index-entry"',
            'apiPost("admin/wiki-index/save"',
            'apiPost("admin/wiki-index/restore"',
            'apiGet("admin/transfer/manifest"',
            'apiDownload("admin/transfer/download"',
            'apiPost("admin/transfer/apply"',
            'apiPost("admin/transfer/discard"',
        ):
            self.assertTrue(
                re.sub(r"\s+", "", endpoint) in calls,
                f"front end no longer calls {endpoint}",
            )
        self.assertTrue("confirmAction({" in script, "destructive actions need confirm")

        # Eight views exist as nav entries and panels.
        for view in (
            "overview",
            "catalog",
            "terminology",
            "wiki-index",
            "groups",
            "trash",
            "audit",
            "transfer",
        ):
            self.assertIn(f'data-view="{view}"', index)
            self.assertIn(f'data-view-panel="{view}"', index)

        # Skins live on data-kirby-skin because the host rewrites data-theme.
        self.assertIn('data-kirby-theme="auto"', index)
        self.assertIn('data-kirby-skin="dreamland"', index)
        for skin in ("dreamland", "starlight", "metaknight"):
            self.assertTrue(
                f'[data-kirby-skin="{skin}"]' in styles,
                f"skin {skin} has no tokens",
            )
            self.assertIn(f'data-theme-option="{skin}"', index)
        self.assertIn('data-theme-option="auto"', index)
        self.assertTrue(
            "data-effective-theme" not in styles,
            "the legacy data-effective-theme hook must be gone",
        )
        for needle in (
            "prefers-reduced-motion",
            "@media print",
            ".wiki-index-table",
        ):
            self.assertTrue(needle in styles, f"stylesheet lost {needle}")
        # Two breakpoints per layer: tablet collapse and phone single column.
        breakpoints = sorted(
            {int(value) for value in re.findall(r"max-width:\s*(\d+)px", styles)}
        )
        self.assertTrue(
            any(value >= 1000 for value in breakpoints)
            and any(value <= 700 for value in breakpoints),
            f"responsive breakpoints missing: {breakpoints}",
        )
        self.assertTrue(
            (plugin_root / ".astrbot-plugin" / "i18n" / "zh-CN.json").is_file()
        )

    def test_plugin_initialization_registers_management_page_routes(self):
        from astrbot_plugin_kirby_catalog import main as plugin_main

        with tempfile.TemporaryDirectory() as temp:
            routes = []
            context = SimpleNamespace(
                register_web_api=lambda *args: routes.append(args)
            )

            def data_dir(plugin_id):
                return str(Path(temp) / str(plugin_id))

            with patch.object(
                plugin_main.StarTools, "get_data_dir", side_effect=data_dir
            ):
                plugin = plugin_main.KirbyCatalogPlugin(context, {})

            self.assertIsNotNone(plugin.webui)
            self.assertEqual(len(routes), 38)
            self.assertIs(plugin.webui.write_lock, plugin._draw_lock)
            self.assertEqual(plugin.store.legacy_dirs, [])
            self.assertFalse(plugin.store._profiles_loaded)
            self.assertFalse(plugin.terminology.loaded)


if __name__ == "__main__":
    unittest.main()
