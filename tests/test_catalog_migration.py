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

    def write_supplemental_collection(self, root: Path) -> None:
        records = root / "_收集记录"
        records.mkdir(parents=True)
        files = {
            "能力作品.烈火（Fire）.png": self.make_image((235, 75, 30)),
            "能力作品.烈火EX（Fire EX）.png": self.make_image((175, 35, 30)),
            "能力作品.冰冻（Ice）.png": self.make_image((80, 180, 245)),
        }
        for filename, data in files.items():
            (root / filename).write_bytes(data)
        manifest = [
            {
                "pageid": 3,
                "page_title": "Fire",
                "chinese_name": "烈火",
                "english_name": "Fire",
                "filename": "能力作品.烈火（Fire）.png",
                "character_filename": "烈火（Fire）.png",
                "entry_key": "wikirby-form:3",
                "variant_key": "Fire",
                "catalog_kind": "copy_ability",
                "earliest_work": "Ability Game",
                "work_display_name": "能力作品",
                "infobox_template": "Infobox-Copy Ability",
            },
            {
                "pageid": 3,
                "page_title": "Fire",
                "chinese_name": "烈火EX",
                "english_name": "Fire EX",
                "filename": "能力作品.烈火EX（Fire EX）.png",
                "character_filename": "烈火EX（Fire EX）.png",
                "entry_key": "wikirby-variant:3:ex",
                "variant_key": "Fire EX",
                "catalog_kind": "ex_form",
                "earliest_work": "Ability Game",
                "work_display_name": "能力作品",
                "infobox_template": "Infobox-Copy Ability",
            },
            {
                "pageid": 4,
                "page_title": "Ice",
                "chinese_name": "冰冻",
                "english_name": "Ice",
                "filename": "能力作品.冰冻（Ice）.png",
                "character_filename": "冰冻（Ice）.png",
                "entry_key": "wikirby-form:4",
                "variant_key": "Ice",
                "catalog_kind": "copy_ability",
                "earliest_work": "Ability Game",
                "work_display_name": "能力作品",
                "infobox_template": "Infobox-Copy Ability",
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
        config.joinpath("draw_bonuses.json").write_text(
            json.dumps({"100": {"42": {"2026-01-04": 1}}}), encoding="utf-8"
        )
        config.joinpath("description_overrides.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": {
                        "entry_key:wikirby-page:1": {"description_zh": "管理员简介"}
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_dry_run_and_atomic_apply_preserve_recoverable_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "plugin_data"
            new = root / "new_assets"
            supplemental = root / "supplemental_assets"
            report = root / "report"
            new.mkdir()
            supplemental.mkdir()
            self.write_new_collection(new)
            self.write_supplemental_collection(supplemental)
            self.write_old_data(old)
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992, "date": ""},
                            "Ability Game": {"year": 1993, "date": ""},
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
                        "by_old_filename": {"火焰卡比.png": "Fire"},
                        "by_old_name": {},
                        "ignored": [],
                    }
                ),
                encoding="utf-8",
            )

            plan = create_plan(
                old,
                new,
                report,
                release_order,
                overrides,
                supplemental_assets_roots=[supplemental],
            )
            validate_plan(plan)
            write_reports(plan)

            self.assertEqual(
                [item["id"] for item in plan.catalog_items], [1, 2, 3, 4, 5]
            )
            self.assertEqual(plan.catalog_items[0]["page_title"], "Kirby")
            self.assertEqual(plan.catalog_items[1]["variant_key"], "Fire")
            self.assertEqual(plan.catalog_items[2]["variant_key"], "Fire EX")
            self.assertEqual(plan.catalog_items[3]["variant_key"], "Ice")
            self.assertEqual(plan.catalog_items[4]["page_title"], "Alpha")
            self.assertEqual(
                [
                    item["entry_key"]
                    for item in plan.catalog_items
                    if item["pageid"] == 3
                ],
                ["wikirby-form:3", "wikirby-variant:3:ex"],
            )
            methods = {match.old_filename: match.method for match in plan.matches}
            self.assertEqual(methods["旧Alpha.png"], "sha256")
            self.assertEqual(methods["火焰卡比.png"], "override")
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
            self.assertEqual(
                plan.copied_config_files["draw_bonuses.json"],
                {"100": {"42": {"2026-01-04": 1}}},
            )
            self.assertEqual(
                plan.copied_config_files["description_overrides.json"]["items"][
                    "entry_key:wikirby-page:1"
                ]["description_zh"],
                "管理员简介",
            )

            backup = apply_plan(plan, "REPLACE_OLD_KIRBY_DATA")
            self.assertTrue(backup.joinpath("catalog.json").is_file())
            self.assertEqual(len(list((old / "img" / "allies").iterdir())), 5)
            self.assertTrue((old / "migration_state.json").is_file())

            store = CatalogStore(old, image_base_url="")
            self.assertEqual(len(store.entries()), 5)
            self.assertEqual(store.entries()[0]["pageid"], 1)
            reloaded = store.load_group("100")["42"]
            self.assertEqual(reloaded["total_count"], 88)
            self.assertEqual(reloaded["current"]["ally_filename"], "")
            self.assertEqual(store.draw_count("100", "42", "2026-01-04"), 2)
            self.assertEqual(store.draw_bonus("100", "42", "2026-01-04"), 1)
            self.assertTrue((old / "config" / "description_overrides.json").is_file())
            self.assertEqual(store.user_progress(reloaded)["unlocked"], 2)
            self.assertEqual(store.user_progress(reloaded)["total"], 5)
            self.assertEqual(store.leaderboard("100")[0][2], 2)
            fire_ex = next(
                item for item in store.entries() if item.get("variant_key") == "Fire EX"
            )
            self.assertTrue(store.unlock(reloaded, fire_ex["filename"], "2026-01-05"))
            self.assertEqual(store.user_progress(reloaded)["unlocked"], 3)
            store.save_group("100", {"42": reloaded})
            self.assertEqual(store.leaderboard("100")[0][2], 3)

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

    def test_history_source_restores_forms_and_preserves_active_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "plugin_data"
            new = root / "new_assets"
            supplemental = root / "supplemental_assets"
            first_report = root / "first_report"
            second_report = root / "second_report"
            new.mkdir()
            supplemental.mkdir()
            self.write_new_collection(new)
            self.write_supplemental_collection(supplemental)
            self.write_old_data(active)
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992, "date": ""},
                            "Ability Game": {"year": 1993, "date": ""},
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
                            "火焰卡比.png": "Fire",
                            "旧火焰卡比EX.png": "wikirby-variant:3:ex",
                        },
                        "by_old_name": {},
                        "ignored": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first_plan = create_plan(
                active,
                new,
                first_report,
                release_order,
                overrides,
                supplemental_assets_roots=[supplemental],
            )
            first_backup = apply_plan(first_plan, "REPLACE_OLD_KIRBY_DATA")
            history = root / "plugin_data.before-v3-original"
            first_backup.rename(history)

            history_catalog_path = history / "catalog.json"
            history_catalog = json.loads(
                history_catalog_path.read_text(encoding="utf-8")
            )
            history_catalog["items"].append(
                {
                    "id": 4,
                    "filename": "旧火焰卡比EX.png",
                    "name": "旧火焰卡比EX",
                    "source": "",
                }
            )
            history_catalog_path.write_text(
                json.dumps(history_catalog, ensure_ascii=False), encoding="utf-8"
            )
            fire_ex_asset = supplemental / "能力作品.烈火EX（Fire EX）.png"
            history_fire_ex = history / "img" / "allies" / "旧火焰卡比EX.png"
            history_fire_ex.write_bytes(fire_ex_asset.read_bytes())
            history_group_path = history / "config" / "100.json"
            history_group = json.loads(history_group_path.read_text(encoding="utf-8"))
            history_group["42"]["unlocked"].append(
                {
                    "ally_filename": "旧火焰卡比EX.png",
                    "unlock_date": "2025-12-31",
                }
            )
            history_group["42"]["current"] = {
                "ally_filename": "旧火焰卡比EX.png",
                "date": "2025-12-31",
            }
            history_group_path.write_text(
                json.dumps(history_group, ensure_ascii=False), encoding="utf-8"
            )

            store = CatalogStore(active, image_base_url="")
            active_groups = store.load_group("100")
            active_user = active_groups["42"]
            ice = next(
                item for item in store.entries() if item.get("variant_key") == "Ice"
            )
            self.assertTrue(store.unlock(active_user, ice["filename"], "2026-02-02"))
            active_user["current"] = {
                "ally_filename": ice["filename"],
                "date": "2026-02-02",
            }
            active_user["no_new_count"] = 99
            active_user["total_count"] = 123
            store.save_group("100", active_groups)

            history_hashes = {
                str(path.relative_to(history)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in history.rglob("*")
                if path.is_file()
            }
            second_plan = create_plan(
                active,
                new,
                second_report,
                release_order,
                overrides,
                supplemental_assets_roots=[supplemental],
                history_roots=[history],
            )
            validate_plan(second_plan)
            write_reports(second_plan)

            migrated = second_plan.migrated_groups["100.json"]["42"]
            unlocked = {
                item["ally_filename"]: item["unlock_date"]
                for item in migrated["unlocked"]
            }
            fire_ex = next(
                item
                for item in second_plan.catalog_items
                if item.get("variant_key") == "Fire EX"
            )
            self.assertEqual(len(unlocked), 4)
            self.assertEqual(unlocked[fire_ex["filename"]], "2025-12-31")
            self.assertEqual(migrated["current"]["ally_filename"], ice["filename"])
            self.assertEqual(migrated["no_new_count"], 99)
            self.assertEqual(migrated["total_count"], 123)
            self.assertEqual(second_plan.summary["history_source_count"], 1)
            self.assertEqual(second_plan.summary["history_unlocks_added"], 3)
            self.assertEqual(second_plan.summary["final_unique_unlocks"], 4)
            self.assertEqual(
                second_plan.baseline_reconciliation["status"], "reconstructed"
            )
            self.assertEqual(
                second_plan.baseline_reconciliation["active_baseline_unlocks_removed"],
                2,
            )
            self.assertEqual(
                second_plan.baseline_reconciliation[
                    "active_post_migration_unlocks_preserved"
                ],
                1,
            )
            merge_row = second_plan.history_merge_rows[0]
            self.assertEqual(merge_row["active_unique_unlocks"], 3)
            self.assertEqual(merge_row["active_baseline_unlocks_removed"], 2)
            self.assertEqual(merge_row["active_post_migration_unlocks_preserved"], 1)
            self.assertEqual(merge_row["history_unlocks_added"], 3)
            self.assertEqual(merge_row["final_unique_unlocks"], 4)
            self.assertTrue(second_report.joinpath("历史数据合并.csv").is_file())

            apply_plan(second_plan, "REPLACE_OLD_KIRBY_DATA")
            final_store = CatalogStore(active, image_base_url="")
            final_user = final_store.load_group("100")["42"]
            self.assertEqual(final_store.user_progress(final_user)["unlocked"], 4)
            self.assertEqual(final_store.leaderboard("100")[0][2], 4)
            self.assertEqual(final_user["current"]["ally_filename"], ice["filename"])
            self.assertEqual(
                history_hashes,
                {
                    str(path.relative_to(history)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in history.rglob("*")
                    if path.is_file()
                },
            )

    def test_history_source_must_be_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "plugin_data"
            new = root / "new_assets"
            new.mkdir()
            self.write_new_collection(new)
            self.write_old_data(active)
            release_order = root / "release.json"
            release_order.write_text("{}", encoding="utf-8")
            overrides = root / "overrides.json"
            overrides.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "历史数据源"):
                create_plan(
                    active,
                    new,
                    root / "report",
                    release_order,
                    overrides,
                    history_roots=[active],
                )

    def test_baseline_reconciliation_removes_previous_wrong_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "plugin_data"
            new = root / "new_assets"
            supplemental = root / "supplemental_assets"
            new.mkdir()
            supplemental.mkdir()
            self.write_new_collection(new)
            self.write_supplemental_collection(supplemental)
            self.write_old_data(active)
            group_path = active / "config" / "100.json"
            group = json.loads(group_path.read_text(encoding="utf-8"))
            group["42"]["current"] = {
                "ally_filename": "火焰卡比.png",
                "date": "2026-01-02",
            }
            group_path.write_text(
                json.dumps(group, ensure_ascii=False), encoding="utf-8"
            )
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992},
                            "Ability Game": {"year": 1993},
                            "Late Game": {"year": 2000},
                        }
                    }
                ),
                encoding="utf-8",
            )
            wrong_overrides = root / "wrong_overrides.json"
            wrong_overrides.write_text(
                json.dumps(
                    {
                        "by_old_filename": {"火焰卡比.png": "Kirby"},
                        "by_old_name": {},
                        "ignored": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            corrected_overrides = root / "corrected_overrides.json"
            corrected_overrides.write_text(
                json.dumps(
                    {
                        "by_old_filename": {"火焰卡比.png": "Fire"},
                        "by_old_name": {},
                        "ignored": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first_plan = create_plan(
                active,
                new,
                root / "first_report",
                release_order,
                wrong_overrides,
                supplemental_assets_roots=[supplemental],
            )
            backup = apply_plan(first_plan, "REPLACE_OLD_KIRBY_DATA")
            history = root / "plugin_data.before-v3-original"
            backup.rename(history)

            second_plan = create_plan(
                active,
                new,
                root / "second_report",
                release_order,
                corrected_overrides,
                supplemental_assets_roots=[supplemental],
                history_roots=[history],
            )
            migrated = second_plan.migrated_groups["100.json"]["42"]
            unlocked_names = {item["ally_filename"] for item in migrated["unlocked"]}
            kirby = next(
                item
                for item in second_plan.catalog_items
                if item.get("page_title") == "Kirby"
            )
            fire = next(
                item
                for item in second_plan.catalog_items
                if item.get("variant_key") == "Fire"
            )
            self.assertNotIn(kirby["filename"], unlocked_names)
            self.assertIn(fire["filename"], unlocked_names)
            self.assertEqual(len(unlocked_names), 2)
            self.assertEqual(migrated["current"]["ally_filename"], fire["filename"])
            self.assertEqual(
                second_plan.baseline_reconciliation["active_baseline_unlocks_removed"],
                2,
            )
            self.assertEqual(
                second_plan.baseline_reconciliation["baseline_current_rows_changed"],
                1,
            )
            self.assertEqual(second_plan.summary["final_unique_unlocks"], 2)

    def test_existing_entry_key_survives_asset_and_name_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "plugin_data"
            assets = active / "img" / "allies"
            config = active / "config"
            new = root / "new_assets"
            assets.mkdir(parents=True)
            config.mkdir()
            new.mkdir()
            self.write_new_collection(new)
            legacy_filename = "旧版暗影卡比.png"
            assets.joinpath(legacy_filename).write_bytes(
                self.make_image((120, 20, 180))
            )
            active.joinpath("catalog.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "id": 1,
                                "filename": legacy_filename,
                                "name": "暗影卡比",
                                "source": "旧作品",
                                "entry_key": "wikirby-page:2",
                                "variant_key": "Shadow Kirby",
                                "page_title": "Shadow Kirby",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config.joinpath("100.json").write_text(
                json.dumps(
                    {
                        "42": {
                            "current": {
                                "ally_filename": legacy_filename,
                                "date": "2026-01-01",
                            },
                            "unlocked": [
                                {
                                    "ally_filename": legacy_filename,
                                    "unlock_date": "2026-01-01",
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            release_order = root / "release.json"
            release_order.write_text(
                json.dumps(
                    {
                        "works": {
                            "Early Game": {"year": 1992},
                            "Late Game": {"year": 2000},
                        }
                    }
                ),
                encoding="utf-8",
            )
            overrides = root / "overrides.json"
            overrides.write_text("{}", encoding="utf-8")

            plan = create_plan(
                active,
                new,
                root / "report",
                release_order,
                overrides,
            )
            match = plan.matches[0]
            self.assertEqual(match.method, "entry_key")
            self.assertEqual(match.page_title, "Alpha")
            migrated = plan.migrated_groups["100.json"]["42"]
            self.assertTrue(migrated["current"]["ally_filename"].endswith("Alpha.png"))
            self.assertEqual(len(migrated["unlocked"]), 1)

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
            self.assertEqual(kirby["unlock_date"], "2026-01-04")
            self.assertEqual(alpha["unlock_date"], "2026-01-01")
            self.assertEqual(plan.summary["mapped_unlock_rows"], 3)
            self.assertEqual(plan.summary["generated_unlock_targets"], 4)
            self.assertEqual(plan.summary["expanded_unlock_targets"], 1)
            self.assertEqual(plan.summary["deduplicated_unlock_targets"], 2)
            self.assertEqual(plan.summary["unresolved_unlock_rows"], 1)

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

    def test_packaged_overrides_keep_legacy_forms_independent(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "migration_data"
            / "kirby_legacy_overrides.json"
        )
        overrides = json.loads(path.read_text(encoding="utf-8"))
        filenames = overrides["by_old_filename"]
        expected = {
            "星之卡比Wii 豪华版.电火花球体喽啪EX.png": (
                "wikirby-variant:5718:silver-sphere-doomer-ex"
            ),
            "星之卡比Wii 豪华版.伟大喽啪第三阶段.png": (
                "wikirby-variant:6039:grand-doomer-third-phase"
            ),
            "ally_0385_结晶化原始加鲁鲁飞.png": (
                "wikirby-variant:55239:crystal-primal-awoofy"
            ),
            "经理魔法洛亚.png": "wikirby-variant:5624:manager-magolor",
            "魔法光束瓦豆鲁迪.png": "wikirby-form:9044",
            "盟友料理.png": "wikirby-form:13278",
            "盟友翻滚.png": "wikirby-form:12566",
        }
        for filename, target in expected.items():
            self.assertEqual(filenames[filename], target)

        quartet = filenames["四个卡比.png"]
        self.assertEqual(
            quartet["unlock_targets"],
            [
                "wikirby-form:9039",
                "wikirby-form:9041",
                "wikirby-form:9044",
                "wikirby-form:9046",
            ],
        )
        self.assertEqual(overrides["by_old_name"]["火焰卡比"], "wikirby-form:50")
        self.assertNotIn("盟友料理.png", overrides["ignored"])
        self.assertNotIn("盟友翻滚.png", overrides["ignored"])


if __name__ == "__main__":
    unittest.main()
