from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore
from astrbot_plugin_kirby_catalog.wiki_index import WikiIndexStore, parse_wiki_number


class WikiIndexStoreTests(unittest.TestCase):
    @staticmethod
    def make_image() -> bytes:
        output = BytesIO()
        Image.new("RGB", (32, 32), (240, 94, 145)).save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def write_shinkaku(path: Path) -> None:
        path.write_text(
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

    def test_parses_supported_number_forms(self):
        for value in ("88", "#88", "编号 88", "序号 88"):
            with self.subTest(value=value):
                self.assertEqual(parse_wiki_number(value), 88)
        self.assertIsNone(parse_wiki_number("编号 Fight"))
        self.assertIsNone(parse_wiki_number("0"))

    def test_catalog_and_shinkaku_defaults_resolve_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "data", image_base_url="")
            entry = store.add_asset("卡比（Kirby）", self.make_image(), "星之卡比")
            store._catalog[entry["filename"]]["page_title"] = "Kirby"
            store._save_catalog()
            shinkaku_path = root / "shinkaku.json"
            self.write_shinkaku(shinkaku_path)

            index = WikiIndexStore(store, shinkaku_path)

            self.assertEqual(index.resolve("wikirby", entry["id"])["target"], "Kirby")
            self.assertEqual(index.resolve("fandom", entry["id"])["target"], "Kirby")
            self.assertEqual(index.resolve("shinkaku", 88)["target"], "ファイター(RBP)")
            self.assertEqual(index.stats()["total"], 3)

    def test_overrides_are_site_scoped_persistent_and_conflict_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CatalogStore(root / "data", image_base_url="")
            first = store.add_asset("卡比（Kirby）", self.make_image(), "星之卡比")
            second = store.add_asset(
                "瓦豆鲁迪（Waddle Dee）", self.make_image(), "星之卡比"
            )
            for entry, page_title in ((first, "Kirby"), (second, "Waddle Dee")):
                store._catalog[entry["filename"]]["page_title"] = page_title
            store._save_catalog()
            shinkaku_path = root / "shinkaku.json"
            self.write_shinkaku(shinkaku_path)
            index = WikiIndexStore(store, shinkaku_path)
            key = f"catalog:{first['entry_key']}"

            saved = index.save(
                {
                    "site": "wikirby",
                    "key": key,
                    "number": 7000,
                    "target": "Kirby (character)",
                    "enabled": True,
                },
                "admin",
            )
            self.assertEqual(saved["number"], 7000)
            self.assertEqual(index.resolve("wikirby", 7000)["target"], "Kirby (character)")
            self.assertEqual(index.resolve("fandom", first["id"])["target"], "Kirby")

            reloaded = WikiIndexStore(store, shinkaku_path)
            self.assertEqual(reloaded.resolve("wikirby", 7000)["target"], "Kirby (character)")
            with self.assertRaisesRegex(ValueError, "已由"):
                reloaded.save(
                    {
                        "site": "wikirby",
                        "key": key,
                        "number": second["id"],
                        "target": "Kirby",
                    }
                )

            restored = reloaded.restore("wikirby", key)
            self.assertEqual(restored["number"], first["id"])
            self.assertFalse(restored["has_override"])


if __name__ == "__main__":
    unittest.main()
