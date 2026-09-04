from __future__ import annotations

import json
import re
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from astrbot_plugin_kirby_catalog.catalog_core import CatalogStore, _atomic_write_json
from astrbot_plugin_kirby_catalog.catalog_transfer import (
    DATASET_SPECS,
    SUMMARY_KEYS,
    CatalogTransferService,
    _safe_member,
)
from astrbot_plugin_kirby_catalog.terminology import (
    KirbyTerminologyStore,
    TerminologyEntry,
    terminology_document,
)
from astrbot_plugin_kirby_catalog.wiki_index import WikiIndexStore

MODULE = "astrbot_plugin_kirby_catalog.catalog_transfer"


def _png(color=(240, 94, 145), size=(32, 32)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class CatalogTransferTests(unittest.TestCase):
    """导入导出服务的端到端行为：导出格式、两阶段导入、以及压缩包的安全边界。"""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = CatalogStore(self.root / "data", image_base_url="")
        self.kirby = self.store.add_asset(
            "卡比（Kirby）",
            _png(),
            "星之卡比",
            description="粉红色的英雄。",
            updated_by="admin",
        )
        self.meta = self.store.add_asset(
            "魅塔骑士（Meta Knight）",
            _png((84, 169, 212)),
            "星之卡比 梦之泉物语",
        )
        for entry, page_title in ((self.kirby, "Kirby"), (self.meta, "Meta Knight")):
            self.store._catalog[entry["filename"]]["page_title"] = page_title
        self.store._save_catalog()
        bundled = self.root / "kirby_terminology.json"
        bundled.write_text(
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
        self.terminology_overrides = (
            self.store.config_dir / "terminology_overrides.json"
        )
        self.terminology = KirbyTerminologyStore(bundled, self.terminology_overrides)
        shinkaku = self.root / "shinkaku.json"
        shinkaku.write_text(
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
        self.wiki_index = WikiIndexStore(self.store, shinkaku)
        self.service = CatalogTransferService(
            self.store, self.terminology, self.wiki_index
        )

    # ------------------------------------------------------------------ 工具

    def collect(self, export) -> bytes:
        return b"".join(export.chunks())

    def stage_bytes(self, dataset: str, filename: str, data: bytes):
        ticket = self.service.begin_upload(dataset, filename)
        record = self.service.stage(ticket["token"], filename, data=data)
        return ticket["token"], record

    def zip_bytes(self, members, compress: bool = True) -> bytes:
        output = BytesIO()
        mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with zipfile.ZipFile(output, "w", mode) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
        return output.getvalue()

    def terminology_payload(self, zh_cn: str = "卡比酱") -> bytes:
        export = self.service.export("terminology", "json")
        payload = json.loads(self.collect(export).decode("utf-8"))
        for item in payload["items"]:
            item["zh_cn"] = zh_cn
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # ------------------------------------------------------------------ 清单

    def test_manifest_describes_every_dataset_with_limits(self) -> None:
        manifest = self.service.manifest()
        datasets = manifest["datasets"]
        self.assertEqual(
            [item["name"] for item in datasets],
            [
                "catalog",
                "terminology",
                "wiki-index",
                "groups",
                "audit",
                "bundle",
                "assets",
            ],
        )
        by_name = {item["name"]: item for item in datasets}
        self.assertEqual(by_name["audit"]["modes"], [])
        self.assertEqual(by_name["catalog"]["count"], 2)
        for item in datasets:
            with self.subTest(dataset=item["name"]):
                self.assertTrue(item["label"])
                self.assertTrue(item["formats"])
                self.assertEqual(len(item["mode_labels"]), len(item["modes"]))
                self.assertEqual(len(item["scope_labels"]), len(item["scopes"]))
        self.assertEqual(
            sorted(manifest["limits"]),
            [
                "archive_bytes",
                "catalog_changes",
                "single_asset_bytes",
                "stage_ttl",
                "text_bytes",
                "volume_bytes",
            ],
        )
        self.assertEqual(
            sorted(manifest["assets"]),
            ["bytes", "count", "volume_bytes", "volumes"],
        )
        self.assertEqual(manifest["assets"]["count"], 2)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["pending"], [])

    # ------------------------------------------------------------------ 导出

    def test_json_export_carries_header_and_csv_export_carries_bom(self) -> None:
        export = self.service.export("catalog", "json")
        self.assertRegex(export.filename, r"^kirby-catalog-\d{8}-\d{6}\.json$")
        payload = json.loads(self.collect(export).decode("utf-8"))
        self.assertEqual(
            sorted(payload),
            [
                "count",
                "dataset",
                "generated_at",
                "items",
                "plugin",
                "schema_version",
                "scope",
            ],
        )
        self.assertEqual(payload["dataset"], "catalog")
        self.assertEqual(payload["scope"], "merged")
        self.assertEqual(payload["count"], len(payload["items"]))
        self.assertEqual(payload["count"], 2)
        csv_export = self.service.export("catalog", "csv")
        raw = self.collect(csv_export)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw[3:].startswith(b"id,"))

    def test_total_bytes_matches_streamed_length(self) -> None:
        for dataset, fmt in (("terminology", "json"), ("bundle", "zip")):
            with self.subTest(dataset=dataset):
                export = self.service.export(dataset, fmt)
                self.assertEqual(export.total_bytes, len(self.collect(export)))

    def test_bundle_export_streams_from_disk_then_removes_temp_file(self) -> None:
        export = self.service.export("bundle", "zip")
        self.assertIsNotNone(export.path)
        self.assertTrue(export.path.is_file())
        payload = self.collect(export)
        self.assertFalse(export.path.exists())
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
        self.assertIn("manifest.json", names)
        self.assertIn("catalog.json", names)

    def test_assets_export_splits_into_volumes(self) -> None:
        collected = []
        with patch(MODULE + ".ASSET_VOLUME_BYTES", 1):
            self.service.invalidate()
            self.assertEqual(self.service.manifest()["assets"]["volumes"], 2)
            for volume in (1, 2):
                export = self.service.export("assets", "zip", volume=volume)
                self.assertIn("part{0}of2".format(volume), export.filename)
                payload = self.collect(export)
                with zipfile.ZipFile(BytesIO(payload)) as archive:
                    names = [n for n in archive.namelist() if n != "manifest.json"]
                self.assertEqual(len(names), 1)
                collected.extend(names)
        self.service.invalidate()
        self.assertEqual(len(collected), 2)
        for name in collected:
            self.assertTrue(name.startswith("assets/"))

    def test_audit_is_export_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "只支持导出"):
            self.service.begin_upload("audit", "kirby-audit.json")
        raw = self.collect(self.service.export("audit", "csv"))
        self.assertTrue(raw[3:].startswith(b"timestamp,"))

    def test_group_export_scopes_to_single_group(self) -> None:
        export = self.service.export("groups", "csv", group_id="12345")
        self.assertIn("12345", export.filename)
        text = self.collect(export).decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("group_id,"))

    # ---------------------------------------------------------------- 上传校验

    def test_dataset_is_recovered_from_export_filename(self) -> None:
        cases = (
            ("kirby-assets-part1of2-20260101-000000.zip", "assets", True),
            ("kirby-wiki-index-20260101-000000.json", "wiki-index", False),
        )
        for filename, expected, archive in cases:
            with self.subTest(filename=filename):
                ticket = self.service.begin_upload("", filename)
                self.assertEqual(ticket["dataset"], expected)
                self.assertIs(ticket["archive"], archive)
                self.service.discard(ticket["token"])

    def test_upload_rejects_mismatched_suffix(self) -> None:
        with self.assertRaisesRegex(ValueError, "只接受 zip 文件"):
            self.service.begin_upload("assets", "x.json")
        with self.assertRaisesRegex(ValueError, "只接受 json、csv 文件"):
            self.service.begin_upload("catalog", "x.zip")

    def test_upload_rejects_backup_of_another_dataset(self) -> None:
        payload = self.terminology_payload()
        with self.assertRaisesRegex(ValueError, "这是「名称库」的备份文件"):
            self.stage_bytes("catalog", "kirby-catalog-20260101-000000.json", payload)

    # ---------------------------------------------------------------- 两阶段导入

    def test_terminology_preview_is_read_only_until_apply(self) -> None:
        export = self.service.export("terminology", "json")
        payload = json.loads(self.collect(export).decode("utf-8"))
        self.assertEqual(payload["count"], 1)
        for item in payload["items"]:
            item["zh_cn"] = "卡比酱"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token, record = self.stage_bytes(
            "terminology", "kirby-terminology-20260101-000000.json", data
        )
        summary = record["summary"]
        self.assertEqual(sorted(summary), sorted(SUMMARY_KEYS))
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["added"], 0)
        self.assertEqual(
            summary["total"],
            summary["added"]
            + summary["updated"]
            + summary["unchanged"]
            + summary["skipped"],
        )
        self.assertGreater(record["expires_in"], 0)
        self.assertEqual(len(self.service.pending()), 1)
        self.assertFalse(self.terminology_overrides.exists())
        result = self.service.apply(token, "merge", "alice")
        self.assertEqual(result["summary"]["updated"], 1)
        self.assertTrue(self.terminology_overrides.is_file())
        self.assertEqual(self.terminology.entry("character:kirby").zh_cn, "卡比酱")
        self.assertEqual(self.service.pending(), [])

    def test_discard_removes_stage_directory(self) -> None:
        token, _record = self.stage_bytes(
            "terminology",
            "kirby-terminology-20260101-000000.json",
            self.terminology_payload(),
        )
        directory = self.service.stage_dir / token
        self.assertTrue(directory.is_dir())
        self.service.discard(token)
        self.assertFalse(directory.exists())
        self.assertEqual(self.service.pending(), [])

    def test_apply_always_writes_an_audit_entry(self) -> None:
        token, _record = self.stage_bytes(
            "terminology",
            "kirby-terminology-20260101-000000.json",
            self.terminology_payload(),
        )
        self.service.apply(token, "merge", "alice")
        latest = self.store.audit_entries(1)[0]
        self.assertEqual(latest["action"], "transfer.import")
        self.assertEqual(latest["username"], "alice")

    def test_stage_rejects_oversized_text(self) -> None:
        ticket = self.service.begin_upload(
            "terminology", "kirby-terminology-20260101-000000.json"
        )
        with patch(MODULE + ".TRANSFER_MAX_TEXT_BYTES", 2 * 1024 * 1024):
            with self.assertRaisesRegex(ValueError, "文件超过 2 MB 上限"):
                self.service.stage(ticket["token"], data=b"x" * (2 * 1024 * 1024 + 1))
        self.service.discard(ticket["token"])

    def test_catalog_import_updates_only_matched_rows(self) -> None:
        export = self.service.export("catalog", "json")
        payload = json.loads(self.collect(export).decode("utf-8"))
        rows = payload["items"]
        self.assertEqual(len(rows), 2)
        rows.sort(key=lambda row: row["id"])
        rows[0]["description"] = "会把一切吸进嘴里的粉红色英雄。"
        rows.append(
            {
                "id": 9999,
                "name": "不存在的盟友",
                "filename": "ally_9999_ghost.png",
            }
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token, record = self.stage_bytes(
            "catalog", "kirby-catalog-20260101-000000.json", data
        )
        self.assertEqual(record["summary"]["total"], 3)
        self.assertEqual(record["summary"]["updated"], 1)
        self.assertEqual(record["summary"]["unchanged"], 1)
        self.assertEqual(record["summary"]["skipped"], 1)
        self.assertTrue(record["warnings"])
        self.service.apply(token, "merge", "alice")
        wanted = rows[0]["id"]
        entry = next(
            item for item in self.store.entries() if int(item.get("id") or 0) == wanted
        )
        profile = self.store.profile_for(entry)
        self.assertEqual(
            profile["description_zh"], "会把一切吸进嘴里的粉红色英雄。"
        )

    def test_wiki_index_numbers_can_be_swapped(self) -> None:
        export = self.service.export("wiki-index", "json")
        payload = json.loads(self.collect(export).decode("utf-8"))
        rows = payload["items"]
        self.assertEqual(len(rows), 5)
        wikirby = sorted(
            [row for row in rows if row["site"] == "wikirby"],
            key=lambda row: row["number"],
        )
        self.assertEqual(len(wikirby), 2)
        low, high = wikirby
        low_key = low["key"]
        low_number = low["number"]
        high_number = high["number"]
        low["number"] = high_number
        high["number"] = low_number
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token, record = self.stage_bytes(
            "wiki-index", "kirby-wiki-index-20260101-000000.json", data
        )
        self.assertEqual(
            record["summary"],
            {
                "total": 5,
                "added": 2,
                "updated": 0,
                "unchanged": 3,
                "removed": 0,
                "skipped": 0,
            },
        )
        self.service.apply(token, "merge", "alice")
        resolved = self.wiki_index.resolve("wikirby", high_number)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["key"], low_key)

    # ------------------------------------------------------------------ 压缩包

    def test_safe_member_blocks_escapes(self) -> None:
        for name in ("../a.png", "/a.png", "C:/a.png", "dir/", "", "..", "a/../../b.png"):
            with self.subTest(name=name):
                self.assertEqual(_safe_member(name), "")
        self.assertEqual(_safe_member("assets/a.png"), "assets/a.png")
        self.assertEqual(_safe_member("assets\\a.png"), "assets/a.png")
        self.assertEqual(_safe_member("./a.png"), "a.png")

    def test_asset_import_ignores_path_traversal_members(self) -> None:
        png = _png((250, 220, 80))
        data = self.zip_bytes(
            [
                ("assets/ally_9001_new.png", png),
                ("../evil.png", png),
                ("/abs.png", png),
            ],
            compress=False,
        )
        token, record = self.stage_bytes(
            "assets", "kirby-assets-20260101-000000.zip", data
        )
        self.assertEqual(record["summary"]["total"], 1)
        self.assertEqual(record["summary"]["added"], 1)
        self.service.apply(token, "merge", "alice")
        self.assertTrue((self.store.assets_dir / "ally_9001_new.png").is_file())
        leaked = sorted(
            item.name
            for item in self.root.rglob("*")
            if item.name in {"evil.png", "abs.png"}
        )
        self.assertEqual(leaked, [])

    def test_archive_guards_member_count_and_empty_package(self) -> None:
        png = _png()
        crowded = self.zip_bytes(
            [("assets/a.png", png), ("assets/b.png", png)], compress=False
        )
        with patch(MODULE + ".MAX_ARCHIVE_MEMBERS", 1):
            with self.assertRaisesRegex(ValueError, "请拆分后再导入"):
                self.stage_bytes(
                    "assets", "kirby-assets-20260101-000000.zip", crowded
                )
        empty = self.zip_bytes([], compress=False)
        self.assertGreater(len(empty), 0)
        with self.assertRaisesRegex(ValueError, "压缩包是空的"):
            self.stage_bytes("assets", "kirby-assets-20260101-000001.zip", empty)

    def test_asset_import_refreshes_catalog_only_once(self) -> None:
        png = _png((120, 200, 160))
        members = [
            ("assets/ally_910{0}_extra.png".format(index), png) for index in (1, 2, 3)
        ]
        data = self.zip_bytes(members, compress=False)
        token, record = self.stage_bytes(
            "assets", "kirby-assets-20260101-000000.zip", data
        )
        self.assertEqual(record["summary"]["added"], 3)
        with patch.object(self.store, "refresh") as refresh:
            self.service.apply(token, "merge", "alice")
        self.assertEqual(refresh.call_count, 1)

    def test_bundle_merge_keeps_local_index_while_replace_overwrites(self) -> None:
        _atomic_write_json(self.store.draw_limits_path, {"1": 5})
        archive = self.collect(self.service.export("bundle", "zip"))
        _atomic_write_json(self.store.draw_limits_path, {"2": 9})
        token, record = self.stage_bytes(
            "bundle", "kirby-bundle-20260101-000000.zip", archive
        )
        summaries = record["summaries"]
        self.assertEqual(sorted(summaries), ["merge", "replace"])
        self.assertLess(summaries["replace"]["skipped"], summaries["merge"]["skipped"])
        with patch.object(self.store, "reload") as reload:
            self.service.apply(token, "merge", "alice")
        self.assertEqual(reload.call_count, 1)
        self.assertEqual(
            json.loads(self.store.draw_limits_path.read_text("utf-8")),
            {"1": 5, "2": 9},
        )
        token, _record = self.stage_bytes(
            "bundle", "kirby-bundle-20260101-000001.zip", archive
        )
        self.service.apply(token, "replace", "alice")
        self.assertEqual(
            json.loads(self.store.draw_limits_path.read_text("utf-8")), {"1": 5}
        )


if __name__ == "__main__":  # pragma: no cover - 手动执行入口
    unittest.main()
