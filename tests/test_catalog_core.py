import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore, get_today


class CatalogStoreTests(unittest.TestCase):
    def make_image(self, color=(255, 0, 0)):
        output = BytesIO()
        Image.new("RGB", (32, 32), color).save(output, format="PNG")
        return output.getvalue()

    def test_migrates_old_records_and_draw_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            old_config = legacy / "config"
            old_images = legacy / "img" / "wife"
            old_config.mkdir(parents=True)
            old_images.mkdir(parents=True)
            (old_images / "Kirby.Kirby.png").write_bytes(self.make_image())
            (legacy / "wife_index.json").write_text(
                json.dumps({"Kirby.Kirby.png": 7}, ensure_ascii=False),
                encoding="utf-8",
            )
            (old_config / "123.json").write_text(
                json.dumps(
                    {
                        "42": ["Kirby.Kirby.png", "2025-01-02", "小明"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (old_config / "wife_draw_limit.json").write_text(
                json.dumps({"123": {"42": {get_today(): 2}}}),
                encoding="utf-8",
            )
            store = CatalogStore(root / "new", [legacy], image_base_url="")
            self.assertEqual(store.entries()[0]["id"], 7)
            self.assertTrue(
                (root / "new" / "img" / "allies" / "Kirby.Kirby.png").is_file()
            )
            self.assertEqual(
                store.asset_path(store.entries()[0]),
                root / "new" / "img" / "allies" / "Kirby.Kirby.png",
            )
            group = store.load_group("123")
            self.assertEqual(group["42"]["nickname"], "小明")
            self.assertEqual(
                group["42"]["unlocked"][0]["ally_filename"], "Kirby.Kirby.png"
            )
            self.assertEqual(store.draw_count("123", "42"), 2)

    def test_draw_bonuses_and_resets_are_group_and_date_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new"
            store = CatalogStore(root, image_base_url="")
            today = "2026-08-05"
            previous_day = "2026-08-04"

            store.increment_draw("group-1", "42", today)
            store.increment_draw("group-1", "42", today)
            store.increment_draw("group-2", "42", today)
            store.increment_draw("group-1", "42", previous_day)
            self.assertEqual(store.add_draw_bonus("group-1", "42", 2, today), 2)
            self.assertEqual(store.add_draw_bonus("group-1", "42", 1, previous_day), 1)

            result = store.reset_group_draws("group-1", today)

            self.assertEqual(
                result, {"users": 1, "draw_records": 1, "bonus_records": 1}
            )
            self.assertEqual(store.draw_count("group-1", "42", today), 0)
            self.assertEqual(store.draw_bonus("group-1", "42", today), 0)
            self.assertEqual(store.draw_count("group-2", "42", today), 1)
            self.assertEqual(store.draw_count("group-1", "42", previous_day), 1)
            self.assertEqual(store.draw_bonus("group-1", "42", previous_day), 1)

            reloaded = CatalogStore(root, image_base_url="")
            self.assertEqual(reloaded.draw_count("group-1", "42", today), 0)
            self.assertEqual(reloaded.draw_bonus("group-1", "42", today), 0)
            self.assertEqual(reloaded.draw_count("group-2", "42", today), 1)
            self.assertEqual(reloaded.draw_bonus("group-1", "42", previous_day), 1)

    def test_draw_pool_is_cached_until_catalog_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            first = store.add_asset("测试盟友一", self.make_image(), "星之卡比")
            second = store.add_asset("测试盟友二", self.make_image(), "星之卡比")
            original_asset_path = store.asset_path
            calls = []

            def counting_asset_path(entry):
                calls.append(entry["filename"])
                return original_asset_path(entry)

            store.asset_path = counting_asset_path  # type: ignore[method-assign]
            self.assertEqual(
                {item["filename"] for item in store.get_draw_pool()},
                {first["filename"], second["filename"]},
            )
            store.get_draw_pool()
            self.assertEqual(len(calls), 2)

            third = store.add_asset("测试盟友三", self.make_image(), "星之卡比")
            self.assertEqual(
                {item["filename"] for item in store.get_draw_pool()},
                {first["filename"], second["filename"], third["filename"]},
            )
            self.assertEqual(len(calls), 5)

    def test_fast_start_trusts_existing_catalog_without_legacy_or_asset_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stable"
            assets = root / "img" / "allies"
            assets.mkdir(parents=True)
            filename = "星之卡比.卡比.png"
            assets.joinpath(filename).write_bytes(self.make_image())
            (root / "catalog.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "id": 1,
                                "filename": filename,
                                "name": "卡比",
                                "source": "星之卡比",
                                "aliases": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            legacy = Path(temp) / "legacy"
            legacy_assets = legacy / "img" / "wife"
            legacy_assets.mkdir(parents=True)
            legacy_assets.joinpath("旧素材.png").write_bytes(self.make_image())

            with patch.object(
                CatalogStore,
                "_refresh_catalog",
                side_effect=AssertionError("fast start must not scan assets"),
            ), patch.object(
                CatalogStore,
                "_migrate_legacy_data",
                side_effect=AssertionError("fast start must not migrate legacy data"),
            ):
                store = CatalogStore(
                    root,
                    [legacy],
                    image_base_url="",
                    startup_migrate_legacy=False,
                    startup_full_scan=False,
                    lazy_profiles=True,
                )

            self.assertEqual(store.catalog_size, 1)
            self.assertEqual(store.resolve_entry("1")["name"], "卡比")

    def test_fast_start_recovers_empty_catalog_from_current_assets_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "empty"
            assets = root / "img" / "allies"
            assets.mkdir(parents=True)
            filename = "星之卡比.卡比.png"
            assets.joinpath(filename).write_bytes(self.make_image())
            legacy = Path(temp) / "legacy"
            legacy_assets = legacy / "img" / "wife"
            legacy_assets.mkdir(parents=True)
            legacy_assets.joinpath("旧素材.png").write_bytes(self.make_image())

            store = CatalogStore(
                root,
                [legacy],
                image_base_url="",
                startup_migrate_legacy=False,
                startup_full_scan=False,
                lazy_profiles=True,
            )

            self.assertEqual(store.catalog_size, 1)
            self.assertIsNotNone(store.resolve_entry(filename))
            self.assertIsNone(store.resolve_entry("旧素材.png"))

    def test_reset_group_draws_rejects_unsafe_group_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")

            with self.assertRaisesRegex(ValueError, "群号格式无效"):
                store.reset_group_draws("../outside")

    def test_draw_bonus_file_is_not_rewritten_as_group_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new"
            store = CatalogStore(root, image_base_url="")
            store.add_draw_bonus("group-1", "42", 3, "2026-08-05")
            expected = json.loads(store.draw_bonuses_path.read_text(encoding="utf-8"))
            entry = store.add_asset("测试盟友", self.make_image(), "星之卡比")

            store.rename_entry(entry, "改名后的测试盟友")
            store.refresh()

            actual = json.loads(store.draw_bonuses_path.read_text(encoding="utf-8"))
            self.assertEqual(actual, expected)

    def test_bundled_description_override_survives_rename_and_can_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            initial = CatalogStore(root, image_base_url="")
            entry = initial.add_asset(
                "海内司（Hyness）", self.make_image(), "星之卡比 新星同盟"
            )
            profiles_path = Path(temp) / "catalog_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "items": {
                            entry["entry_key"]: {
                                "name_zh": "海内司",
                                "name_en": "Hyness",
                                "display_name": "海内司（Hyness）",
                                "description_zh": "这是内置简介。",
                                "source_url": "https://wikirby.com/wiki/Hyness",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = CatalogStore(root, image_base_url="", profiles_path=profiles_path)
            entry = store.resolve_entry(str(entry["id"]))
            self.assertIsNotNone(entry)
            self.assertEqual(store.description_for(entry), "这是内置简介。")
            self.assertEqual(store.find_entries("Hyness")[0]["id"], entry["id"])

            store.set_description(entry, "这是管理员修订后的简介。", "admin")
            renamed = store.rename_entry(entry, "魔神官海内司（Hyness）")
            reloaded = CatalogStore(
                root, image_base_url="", profiles_path=profiles_path
            )
            self.assertEqual(
                reloaded.description_for(renamed), "这是管理员修订后的简介。"
            )
            self.assertTrue((root / "config" / "description_overrides.json").is_file())

            removed, profile = reloaded.restore_description(renamed)
            self.assertTrue(removed)
            self.assertEqual(profile["description_zh"], "这是内置简介。")

    def test_rename_updates_all_user_references_and_keeps_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            image = self.make_image()
            entry = store.add_asset("旧名字", image, "星之卡比")
            config = {
                "1": {
                    "current": {
                        "ally_filename": entry["filename"],
                        "date": get_today(),
                    },
                    "unlocked": [
                        {"ally_filename": entry["filename"], "unlock_date": get_today()}
                    ],
                    "nickname": "用户",
                },
                "2": {
                    "current": {"ally_filename": "", "date": ""},
                    "unlocked": [
                        {"ally_filename": entry["filename"], "unlock_date": get_today()}
                    ],
                    "nickname": "另一位用户",
                },
            }
            store.save_group("100", config)
            renamed = store.rename_entry(entry, "新名字")
            self.assertEqual(renamed["id"], entry["id"])
            store.refresh()
            self.assertEqual(
                store.find_entries(entry["filename"])[0]["id"], entry["id"]
            )
            migrated = store.load_group("100")
            for user in migrated.values():
                self.assertNotIn(entry["filename"], store.unlocked_filenames(user))
            self.assertEqual(
                migrated["1"]["current"]["ally_filename"], renamed["filename"]
            )

    def test_add_and_replace_asset_and_render_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            entry = store.add_asset("测试盟友", self.make_image((0, 255, 0)))
            self.assertTrue(store.asset_path(entry).is_file())
            store.replace_asset(entry, self.make_image((0, 0, 255)))
            output = root / "gallery" / "test.png"
            store.render_gallery(output, {entry["filename"]}, "测试图鉴", columns=4)
            self.assertTrue(output.is_file())
            with Image.open(output) as image:
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)

    def test_gallery_uses_two_compact_name_lines_and_new_cache_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            entry = store.add_asset(
                "非常非常长的能力卡比名称（Extremely Long Kirby Form Name）",
                self.make_image((240, 94, 145)),
            )
            output = root / "gallery" / "long-name.png"

            with patch.object(
                store,
                "_wrap_text_lines",
                wraps=store._wrap_text_lines,
            ) as wrap:
                store.render_gallery(
                    output,
                    {entry["filename"]},
                    "长名称图鉴",
                    columns=4,
                )

            self.assertTrue(
                any(call.kwargs.get("max_lines") == 2 for call in wrap.call_args_list)
            )
            manifest = json.loads(
                output.with_suffix(".png.cache.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["signature"])
            with Image.open(output) as image:
                self.assertEqual(image.height, 208)

    def test_find_entries_supports_page_variant_and_stable_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            entry = store.add_asset("山石EX（Moundo EX）", self.make_image())
            stored = store._catalog[entry["filename"]]
            stored["page_title"] = "Moundo"
            stored["variant_key"] = "Moundo EX"
            store._save_catalog()

            self.assertEqual(store.find_entries("Moundo EX")[0]["id"], entry["id"])
            self.assertEqual(
                store.find_entries(entry["entry_key"])[0]["id"], entry["id"]
            )

    def test_gallery_only_paginates_when_height_limit_is_exceeded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            unlocked = set()
            for index in range(20):
                entry = store.add_asset(
                    f"测试盟友 {index}", self.make_image((index, 80, 160))
                )
                unlocked.add(entry["filename"])

            single = store.render_gallery_pages(
                root / "gallery" / "single.png",
                unlocked,
                "测试图鉴",
                columns=4,
                max_height_px=0,
            )
            paged = store.render_gallery_pages(
                root / "gallery" / "paged.png",
                unlocked,
                "测试图鉴",
                columns=4,
                max_height_px=400,
            )

            self.assertEqual(len(single), 1)
            self.assertGreater(len(paged), 1)
            for path in paged:
                with Image.open(path) as image:
                    self.assertLessEqual(image.height, 400)
            with patch.object(
                store,
                "_render_gallery_page",
                wraps=store._render_gallery_page,
            ) as render_page:
                cached = store.render_gallery_pages(
                    root / "gallery" / "paged.png",
                    unlocked,
                    "测试图鉴",
                    columns=4,
                    max_height_px=400,
                )
            self.assertEqual(cached, paged)
            render_page.assert_not_called()

    def test_repairs_duplicate_legacy_ids_without_losing_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            new = root / "new"
            assets = new / "img" / "allies"
            assets.mkdir(parents=True)
            assets.joinpath("first.png").write_bytes(self.make_image())
            assets.joinpath("second.png").write_bytes(self.make_image((0, 255, 0)))
            (new / "catalog.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {"filename": "first.png", "id": 227, "name": "第一条"},
                            {"filename": "second.png", "id": 227, "name": "第二条"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = CatalogStore(new, image_base_url="")

            entries = store.entries()
            self.assertEqual(len(entries), 2)
            self.assertEqual(len({entry["id"] for entry in entries}), 2)
            self.assertEqual(store.find_entries("227")[0]["filename"], "first.png")
            self.assertEqual(len(store.find_entries("second.png")), 1)

    def test_rename_preserves_source_prefix_in_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            filename = "星之卡比 探索发现.水晶针卡比.png"
            path = store.assets_dir / filename
            path.write_bytes(self.make_image())
            store.refresh()
            entry = store.resolve_entry("水晶针卡比")

            self.assertIsNotNone(entry)
            renamed = store.rename_entry(entry or {}, "结晶化针卡比")

            self.assertEqual(renamed["filename"], "星之卡比 探索发现.结晶化针卡比.png")
            self.assertFalse(path.exists())
            self.assertTrue(store.asset_path(renamed).is_file())

    def test_rename_source_updates_filename_and_all_user_references(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            entry = store.add_asset(
                "卡比（Kirby）", self.make_image(), "Kirby's Dream Land"
            )
            store.save_group(
                "100",
                {
                    "42": {
                        "current": {
                            "ally_filename": entry["filename"],
                            "date": get_today(),
                        },
                        "unlocked": [
                            {
                                "ally_filename": entry["filename"],
                                "unlock_date": get_today(),
                            }
                        ],
                        "nickname": "测试用户",
                    }
                },
            )

            renamed = store.rename_entry(entry, "卡比（Kirby）", "星之卡比 初代")

            self.assertEqual(renamed["filename"], "星之卡比 初代.卡比_Kirby.png")
            user = store.load_group("100")["42"]
            self.assertEqual(user["current"]["ally_filename"], renamed["filename"])
            self.assertEqual(user["unlocked"][0]["ally_filename"], renamed["filename"])

    def test_source_filename_keeps_periods_inside_character_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CatalogStore(Path(temp_dir), "")
            filename = "Kirby Air Riders.J.J.png"
            path = store.assets_dir / filename
            path.write_bytes(self.make_image())
            store.refresh()

            entry = store.resolve_entry(filename)

            self.assertIsNotNone(entry)
            entry = entry or {}
            self.assertEqual(entry["source"], "Kirby Air Riders")
            self.assertEqual(entry["name"], "J.J")
            renamed = store.rename_entry(entry, "J.J.二号")
            self.assertEqual(renamed["filename"], "Kirby Air Riders.J_J_二号.png")
            self.assertFalse(path.exists())
            self.assertTrue(store.asset_path(renamed).is_file())

    def test_renamed_legacy_asset_is_not_reintroduced_on_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            legacy_assets = legacy / "img" / "wife"
            legacy_assets.mkdir(parents=True)
            filename = "星之卡比 探索发现.水晶针卡比.png"
            (legacy_assets / filename).write_bytes(self.make_image())
            (legacy / "wife_index.json").write_text(
                json.dumps({filename: 227}, ensure_ascii=False),
                encoding="utf-8",
            )
            store = CatalogStore(root / "new", [legacy], image_base_url="")
            entry = store.resolve_entry("227")

            renamed = store.rename_entry(entry or {}, "结晶化针卡比")
            store.refresh()

            filenames = {item["filename"] for item in store.entries()}
            self.assertIn(renamed["filename"], filenames)
            self.assertNotIn(filename, filenames)

    def test_keeps_duplicate_local_files_for_explicit_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            new = root / "new"
            assets = new / "img" / "allies"
            assets.mkdir(parents=True)
            image = self.make_image()
            old_filename = "星之卡比 探索发现.水晶针卡比.png"
            new_filename = "星之卡比 探索发现.结晶化针卡比.png"
            assets.joinpath(old_filename).write_bytes(image)
            assets.joinpath(new_filename).write_bytes(image)
            (new / "catalog.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "filename": old_filename,
                                "id": 417,
                                "name": "水晶针卡比",
                                "source": "星之卡比 探索发现",
                            },
                            {
                                "filename": new_filename,
                                "id": 227,
                                "name": "结晶化针卡比",
                                "source": "星之卡比 探索发现",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = CatalogStore(new, image_base_url="")

            entries = store.entries()
            self.assertEqual([entry["id"] for entry in entries], [227, 417])
            self.assertEqual(entries[0]["name"], "结晶化针卡比")
            self.assertEqual(entries[1]["name"], "水晶针卡比")

    def test_restores_local_file_hidden_by_old_same_name_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            new = root / "new"
            assets = new / "img" / "allies"
            assets.mkdir(parents=True)
            visible_filename = "星之卡比 探索发现.同名盟友.png"
            hidden_filename = "ally_0002_同名盟友.png"
            image = self.make_image()
            assets.joinpath(visible_filename).write_bytes(image)
            assets.joinpath(hidden_filename).write_bytes(image)
            (new / "catalog.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "filename": visible_filename,
                                "id": 3,
                                "name": "同名盟友",
                                "source": "星之卡比 探索发现",
                                "aliases": [hidden_filename],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = CatalogStore(new, image_base_url="")

            entries = store.entries()
            self.assertEqual(len(entries), 2)
            restored = store.resolve_entry(hidden_filename)
            self.assertIsNotNone(restored)
            self.assertNotIn(hidden_filename, entries[0]["aliases"])

    def test_cleanup_renamed_prefix_preserves_users_and_one_kept_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            old = store.add_asset("水晶天鹅罗利那", self.make_image(), "星之卡比")
            renamed = store.add_asset(
                "结晶化天鹅罗利那", self.make_image((0, 255, 0)), "星之卡比"
            )
            store.add_asset("水晶针卡比", self.make_image((0, 0, 255)), "星之卡比")
            duplicate_needle = store.add_asset(
                "水晶针卡比", self.make_image((255, 255, 0)), "星之卡比"
            )
            store.save_group(
                "100",
                {
                    "1": {
                        "current": {
                            "ally_filename": old["filename"],
                            "date": get_today(),
                        },
                        "unlocked": [
                            {
                                "ally_filename": old["filename"],
                                "unlock_date": get_today(),
                            },
                            {
                                "ally_filename": duplicate_needle["filename"],
                                "unlock_date": get_today(),
                            },
                        ],
                        "nickname": "用户",
                    }
                },
            )

            result = store.cleanup_renamed_prefix("水晶", "结晶化", ["水晶针卡比"])

            self.assertEqual(result["unresolved"], [])
            names = [entry["name"] for entry in store.entries()]
            self.assertEqual(names.count("水晶针卡比"), 1)
            self.assertIn("结晶化天鹅罗利那", names)
            user = store.load_group("100")["1"]
            self.assertNotIn(old["filename"], store.unlocked_filenames(user))
            self.assertEqual(
                store.load_group("100")["1"]["current"]["ally_filename"],
                renamed["filename"],
            )

    def test_merge_duplicate_entries_uses_explicit_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "new", image_base_url="")
            duplicate = store.add_asset("皮鞭卡比", self.make_image(), "星之卡比")
            target = store.add_asset(
                "鞭子卡比", self.make_image((0, 255, 0)), "星之卡比"
            )
            store.save_group(
                "100",
                {
                    "1": {
                        "current": {
                            "ally_filename": duplicate["filename"],
                            "date": get_today(),
                        },
                        "unlocked": [
                            {
                                "ally_filename": duplicate["filename"],
                                "unlock_date": get_today(),
                            }
                        ],
                        "nickname": "用户",
                    }
                },
            )

            result = store.merge_duplicate_entries([(duplicate["id"], target["id"])])

            self.assertEqual(len(result["removed"]), 1)
            self.assertIsNotNone(store.resolve_entry(str(target["id"])))
            user = store.load_group("100")["1"]
            self.assertEqual(user["current"]["ally_filename"], target["filename"])
            self.assertEqual(user["unlocked"][0]["ally_filename"], target["filename"])

    def test_user_progress_counts_canonical_aliases_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            first = store.add_asset("斯品", self.make_image(), "星之卡比")
            store.add_asset("多洛奇", self.make_image((0, 255, 0)), "星之卡比")
            store._catalog[first["filename"]]["aliases"] = ["旧名斯品.png"]

            progress = store.user_progress(
                {
                    "unlocked": [
                        {"ally_filename": first["filename"]},
                        {"ally_filename": "旧名斯品.png"},
                    ]
                }
            )

            self.assertEqual(progress["unlocked"], 1)
            self.assertEqual(progress["total"], 2)
            self.assertEqual(len(progress["missing"]), 1)
            self.assertEqual(progress["missing"][0]["name"], "多洛奇")

    def test_leaderboard_counts_canonical_aliases_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            first = store.add_asset("Kirby", self.make_image(), "Kirby's Dream Land")
            store.add_asset(
                "Waddle Dee", self.make_image((0, 255, 0)), "Kirby's Dream Land"
            )
            store._catalog[first["filename"]]["aliases"] = ["old-kirby.png"]
            store._save_catalog()
            store.save_group(
                "100",
                {
                    "1": {
                        "current": {
                            "ally_filename": first["filename"],
                            "date": get_today(),
                        },
                        "unlocked": [
                            {
                                "ally_filename": first["filename"],
                                "unlock_date": get_today(),
                            },
                            {
                                "ally_filename": "old-kirby.png",
                                "unlock_date": get_today(),
                            },
                        ],
                        "nickname": "Tester",
                    }
                },
            )

            self.assertEqual(store.leaderboard("100")[0][2], 1)

    def test_delete_and_restore_preserve_id_description_and_group_references(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            entry = store.add_asset(
                "测试盟友",
                self.make_image(),
                "测试作品",
                description="管理员简介",
                updated_by="admin",
            )
            alias = "旧测试盟友.png"
            store._catalog[entry["filename"]]["aliases"] = [alias]
            store._save_catalog()
            (store.assets_dir / alias).write_bytes(self.make_image((0, 255, 0)))
            store.save_group(
                "100",
                {
                    "1": {
                        "current": {
                            "ally_filename": entry["filename"],
                            "date": "2026-08-01",
                        },
                        "unlocked": [
                            {
                                "ally_filename": alias,
                                "unlock_date": "2026-07-01",
                            }
                        ],
                        "nickname": "甲",
                    }
                },
            )
            store.save_group(
                "200",
                {
                    "2": {
                        "current": {"ally_filename": "", "date": ""},
                        "unlocked": [
                            {
                                "ally_filename": entry["filename"],
                                "unlock_date": "2026-07-02",
                            }
                        ],
                        "nickname": "乙",
                    }
                },
            )

            tombstone = store.delete_entry(entry, deleted_by="admin")

            self.assertIsNone(store.resolve_entry(str(entry["id"])))
            self.assertIn(alias, tombstone["reference_names"])
            self.assertEqual(store.load_group("100")["1"]["unlocked"], [])
            store.refresh()
            self.assertIsNone(store.resolve_entry(alias))

            later = store.add_asset("后来新增", self.make_image((0, 0, 255)))
            self.assertGreater(later["id"], entry["id"])
            restored = store.restore_deleted_entry(tombstone["token"], "admin")

            self.assertEqual(restored["id"], entry["id"])
            self.assertEqual(store.description_for(restored), "管理员简介")
            self.assertEqual(
                store.load_group("100")["1"]["current"]["ally_filename"],
                entry["filename"],
            )
            self.assertEqual(
                store.load_group("100")["1"]["unlocked"][0]["ally_filename"],
                alias,
            )
            self.assertEqual(
                store.load_group("200")["2"]["unlocked"][0]["ally_filename"],
                entry["filename"],
            )
            self.assertEqual(store.deleted_entries(), [])

    def test_restore_rolls_back_every_file_when_second_group_save_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            entry = store.add_asset(
                "事务测试",
                self.make_image(),
                "测试作品",
                description="事务简介",
                updated_by="admin",
            )
            for group_id, user_id in (("100", "1"), ("200", "2")):
                store.save_group(
                    group_id,
                    {
                        user_id: {
                            "current": {
                                "ally_filename": entry["filename"],
                                "date": "2026-08-01",
                            },
                            "unlocked": [
                                {
                                    "ally_filename": entry["filename"],
                                    "unlock_date": "2026-07-01",
                                }
                            ],
                            "nickname": user_id,
                        }
                    },
                )
            tombstone = store.delete_entry(entry, deleted_by="admin")
            record_path = store.trash_dir / tombstone["token"] / "record.json"
            tracked_paths = [
                store.catalog_path,
                store.description_overrides_path,
                store.tombstones_path,
                store.config_dir / "100.json",
                store.config_dir / "200.json",
                record_path,
            ]
            before = {path: path.read_bytes() for path in tracked_paths}
            original_save_group = store.save_group

            def fail_second_group(group_id, config):
                if group_id == "200":
                    raise OSError("simulated group write failure")
                return original_save_group(group_id, config)

            with patch.object(store, "save_group", side_effect=fail_second_group):
                with self.assertRaisesRegex(OSError, "simulated group write failure"):
                    store.restore_deleted_entry(tombstone["token"], "admin")

            self.assertIsNone(store.resolve_entry(str(entry["id"])))
            self.assertFalse((store.assets_dir / entry["filename"]).exists())
            self.assertEqual(
                {path: path.read_bytes() for path in tracked_paths}, before
            )
            self.assertEqual(store.deleted_entries()[0]["token"], tombstone["token"])

    def test_add_asset_rolls_back_when_description_save_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CatalogStore(Path(temp) / "new", image_base_url="")
            original_save = store._save_description_overrides
            failed = False

            def fail_once():
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("simulated description write failure")
                return original_save()

            with patch.object(
                store, "_save_description_overrides", side_effect=fail_once
            ):
                with self.assertRaisesRegex(
                    OSError, "simulated description write failure"
                ):
                    store.add_asset(
                        "不能留下",
                        self.make_image(),
                        description="这条简介不应留下",
                    )

            self.assertEqual(store.entries(), [])
            self.assertEqual(list(store.assets_dir.iterdir()), [])
            self.assertEqual(store._description_overrides, {})
            self.assertEqual(
                json.loads(store.catalog_path.read_text(encoding="utf-8"))["items"],
                [],
            )


if __name__ == "__main__":
    unittest.main()
