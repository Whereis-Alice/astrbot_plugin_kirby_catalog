import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
