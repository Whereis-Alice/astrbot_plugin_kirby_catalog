import csv
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore
from astrbot_plugin_kirby_catalog.catalog_migration import (
    apply_plan,
    create_plan,
    load_new_assets,
    load_release_order,
    validate_plan,
    write_reports,
)


class CatalogMigrationTests(unittest.TestCase):
    def make_image(self, color):
        output = BytesIO()
        Image.new("RGB", (48, 32), color).save(output, format="PNG")
        return output.getvalue()

    def write_new_collection(self, root: Path) -> None:
        records = root / "_收集记录"
        records.mkdir(parents=True)
        files = {
            "早期作品.卡比（Kirby）.png": self.make_image((255, 120, 170)),
            "后期作品.Alpha.png": self.make_image((20, 140, 220)),
        }
        for filename, data in files.items():
            (root / filename).write_bytes(data)
        manifest = [
            {
                "pageid": 2,
                "page_title": "Alpha",
                "chinese_name": "",
                "english_name": "Alpha",
                "filename": "后期作品.Alpha.png",
                "character_filename": "Alpha.png",
                "earliest_work": "Late Game",
                "earliest_work_raw": "Late Game (2000)",
                "work_display_name": "后期作品",
                "infobox_template": "Infobox-Enemy",
            },
            {
                "pageid": 1,
                "page_title": "Kirby",
                "chinese_name": "卡比",
                "english_name": "Kirby",
                "filename": "早期作品.卡比（Kirby）.png",
                "character_filename": "卡比（Kirby）.png",
                "earliest_work": "Early Game",
                "earliest_work_raw": "Early Game (1992)",
                "work_display_name": "早期作品",
                "infobox_template": "Infobox-Character",
            },
        ]
        (records / "候选清单.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        with (records / "收集清单.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=["filename", "sha256"])
            writer.writeheader()
            for filename, data in files.items():
                writer.writerow(
                    {"filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
                )

    def write_old_data(self, root: Path) -> None:
        assets = root / "img" / "allies"
        config = root / "config"
        assets.mkdir(parents=True)
        config.mkdir()
        alpha = self.make_image((20, 140, 220))
        assets.joinpath("旧Alpha.png").write_bytes(alpha)
        assets.joinpath("火焰卡比.png").write_bytes(self.make_image((230, 80, 30)))
        assets.joinpath("卡比和Alpha.png").write_bytes(self.make_image((40, 210, 90)))
        catalog = {
            "version": 1,
            "items": [
                {"id": 1, "filename": "旧Alpha.png", "name": "旧Alpha", "source": ""},
                {"id": 2, "filename": "火焰卡比.png", "name": "火焰卡比", "source": ""},
                {
                    "id": 3,
                    "filename": "卡比和Alpha.png",
                    "name": "卡比和Alpha",
                    "source": "",
                },
            ],
        }
        root.joinpath("catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
        )
        group = {
            "42": {
                "current": {"ally_filename": "卡比和Alpha.png", "date": "2026-01-04"},
                "unlocked": [
                    {"ally_filename": "旧Alpha.png", "unlock_date": "2026-01-03"},
                    {"ally_filename": "旧Alpha.png", "unlock_date": "2026-01-01"},
                    {"ally_filename": "火焰卡比.png", "unlock_date": "2026-01-02"},
                    {"ally_filename": "卡比和Alpha.png", "unlock_date": "2026-01-04"},
                ],
                "nickname": "Tester",
                "no_new_count": 3,
                "total_count": 88,
            }
        }
        config.joinpath("100.json").write_text(
            json.dumps(group, ensure_ascii=False), encoding="utf-8"
        )
        config.joinpath("draw_limits.json").write_text(
            json.dumps({"100": {"42": {"2026-01-04": 2}}}), encoding="utf-8"
        )

    def test_dry_run_and_atomic_apply_preserve_recoverable_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "plugin_data"
            new = root / "new_assets"
            report = root / "report"
            new.mkdir()
            self.write_new_collection(new)
            self.write_old_data(old)
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992, "date": ""},
                            "Late Game": {"year": 2000, "date": ""},
                        }
                    }
                ),
                encoding="utf-8",
            )
            overrides = root / "overrides.json"
            overrides.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "by_old_filename": {},
                        "by_old_name": {},
                        "ignored": [],
                    }
                ),
                encoding="utf-8",
            )

            plan = create_plan(old, new, report, release_order, overrides)
            validate_plan(plan)
            write_reports(plan)

            self.assertEqual([item["id"] for item in plan.catalog_items], [1, 2])
            self.assertEqual(plan.catalog_items[0]["page_title"], "Kirby")
            self.assertEqual(plan.catalog_items[1]["page_title"], "Alpha")
            methods = {match.old_filename: match.method for match in plan.matches}
            self.assertEqual(methods["旧Alpha.png"], "sha256")
            self.assertEqual(methods["火焰卡比.png"], "canonical_character")
            self.assertEqual(methods["卡比和Alpha.png"], "unresolved")

            migrated = plan.migrated_groups["100.json"]["42"]
            self.assertEqual(migrated["current"]["ally_filename"], "")
            self.assertEqual(migrated["total_count"], 88)
            self.assertEqual(len(migrated["unlocked"]), 2)
            alpha_unlock = next(
                item
                for item in migrated["unlocked"]
                if item["ally_filename"].endswith("Alpha.png")
            )
            self.assertEqual(alpha_unlock["unlock_date"], "2026-01-01")
            self.assertEqual(plan.summary["old_unlock_rows"], 4)
            self.assertEqual(plan.summary["mapped_unlock_rows"], 3)
            self.assertEqual(plan.summary["unresolved_unlock_rows"], 1)
            self.assertEqual(plan.summary["matched_old_entries_referenced_by_users"], 2)
            self.assertEqual(plan.summary["referenced_old_filenames"], 3)

            backup = apply_plan(plan, "REPLACE_OLD_KIRBY_DATA")
            self.assertTrue(backup.joinpath("catalog.json").is_file())
            self.assertEqual(len(list((old / "img" / "allies").iterdir())), 2)
            self.assertTrue((old / "migration_state.json").is_file())

            store = CatalogStore(old, image_base_url="")
            self.assertEqual(len(store.entries()), 2)
            self.assertEqual(store.entries()[0]["pageid"], 1)
            reloaded = store.load_group("100")["42"]
            self.assertEqual(reloaded["total_count"], 88)
            self.assertEqual(reloaded["current"]["ally_filename"], "")
            self.assertEqual(store.draw_count("100", "42", "2026-01-04"), 2)

    def test_report_must_not_live_inside_replaced_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "plugin_data"
            new = root / "new_assets"
            new.mkdir()
            self.write_new_collection(new)
            self.write_old_data(old)
            release_order = root / "release.json"
            release_order.write_text("{}", encoding="utf-8")
            overrides = root / "overrides.json"
            overrides.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "报告目录"):
                create_plan(old, new, old / "report", release_order, overrides)

    def test_expansion_override_unlocks_every_verified_character(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "plugin_data"
            new = root / "new_assets"
            report = root / "report"
            new.mkdir()
            self.write_new_collection(new)
            self.write_old_data(old)
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992, "date": ""},
                            "Late Game": {"year": 2000, "date": ""},
                        }
                    }
                ),
                encoding="utf-8",
            )
            overrides = root / "overrides.json"
            overrides.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "by_old_filename": {
                            "卡比和Alpha.png": {
                                "primary": "Kirby",
                                "unlock_targets": ["Kirby", "Alpha"],
                                "reason": "测试组合素材展开",
                            }
                        },
                        "by_old_name": {},
                        "ignored": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            plan = create_plan(old, new, report, release_order, overrides)
            validate_plan(plan)
            combo = next(
                match for match in plan.matches if match.old_name == "卡比和Alpha"
            )
            self.assertEqual(combo.method, "override_expansion")
            self.assertEqual(len(combo.additional_targets), 1)
            self.assertEqual(combo.additional_targets[0]["page_title"], "Alpha")

            migrated = plan.migrated_groups["100.json"]["42"]
            kirby = next(
                item
                for item in migrated["unlocked"]
                if "Kirby" in item["ally_filename"]
            )
            alpha = next(
                item
                for item in migrated["unlocked"]
                if item["ally_filename"].endswith("Alpha.png")
            )
            self.assertIn("Kirby", migrated["current"]["ally_filename"])
            self.assertEqual(kirby["unlock_date"], "2026-01-02")
            self.assertEqual(alpha["unlock_date"], "2026-01-01")
            self.assertEqual(plan.summary["mapped_unlock_rows"], 4)
            self.assertEqual(plan.summary["generated_unlock_targets"], 5)
            self.assertEqual(plan.summary["expanded_unlock_targets"], 1)
            self.assertEqual(plan.summary["deduplicated_unlock_targets"], 3)
            self.assertEqual(plan.summary["unresolved_unlock_rows"], 0)

    def test_release_order_preserves_declared_sequence_within_a_year(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release.json"
            path.write_text(
                json.dumps(
                    {
                        "works": {
                            "Zeta Game": {"year": 2000, "date": ""},
                            "Alpha Game": {"year": 2000, "date": ""},
                        }
                    }
                ),
                encoding="utf-8",
            )

            order = load_release_order(path)
            self.assertLess(
                order["Zeta Game"]["sequence"], order["Alpha Game"]["sequence"]
            )

    def test_page_rules_exclude_concepts_and_fill_manual_debut(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            new = root / "new_assets"
            new.mkdir()
            self.write_new_collection(new)
            manifest_path = new / "_收集记录" / "候选清单.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            alpha = next(item for item in manifest if item["page_title"] == "Alpha")
            alpha["earliest_work"] = ""
            alpha["earliest_work_raw"] = ""
            manifest.append(
                {
                    "pageid": 3,
                    "page_title": "Concept Page",
                    "filename": "concept.png",
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992, "date": ""},
                            "Manual Debut": {"year": 2005, "date": ""},
                        },
                        "page_overrides": {
                            "Alpha": {
                                "debut_work": "Manual Debut",
                                "year": 2005,
                                "source": "手工首作",
                            }
                        },
                        "excluded_pages": {"Concept Page": "概念页"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            assets, excluded = load_new_assets(new, release_order)
            alpha_asset = next(asset for asset in assets if asset.page_title == "Alpha")
            self.assertEqual(alpha_asset.debut_work, "Manual Debut")
            self.assertEqual(alpha_asset.debut_year, 2005)
            self.assertEqual(alpha_asset.source, "手工首作")
            self.assertEqual(
                excluded,
                [
                    {
                        "page_title": "Concept Page",
                        "filename": "concept.png",
                        "reason": "概念页",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
