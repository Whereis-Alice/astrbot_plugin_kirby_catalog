import os
import tempfile
import time
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from astrbot_plugin_kirby_catalog.wiki_document import (
    build_wiki_document,
    cleanup_wiki_documents,
)


class WikiDocumentTests(unittest.TestCase):
    def test_document_keeps_multi_icons_and_cell_links_without_duplicate_media(self):
        video_url = "https://www.youtube.com/watch?v=team"
        unmatched_url = "https://www.nicovideo.jp/watch/unmatched"
        rich_sections = [
            {
                "kind": "table",
                "title": "Solo with Friends",
                "headers": ["Icon", "Ability"],
                "rows": [
                    [
                        {
                            "text": "",
                            "icons": [
                                {"data_uri": "data:image/png;base64,AAA="},
                                {"data_uri": "data:image/png;base64,BBB="},
                            ],
                            "icon_separator": "×",
                        },
                        {
                            "text": "Susie x Mage-Sisters",
                            "links": [
                                {
                                    "url": video_url,
                                    "label": "Susie x Mage-Sisters",
                                    "is_media": True,
                                    "platform": "YouTube",
                                }
                            ],
                        },
                    ]
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as temporary:
            path = build_wiki_document(
                Path(temporary),
                wiki_name="Kirby Wiki",
                title="Video Gallery",
                source_url="https://example.test/wiki/Video_Gallery",
                summary="Video records.",
                detail_text="",
                rich_sections=rich_sections,
                media_links=[
                    {"url": video_url, "label": "duplicate", "platform": "YouTube"},
                    {"url": unmatched_url, "label": "", "platform": "Niconico"},
                ],
            )
            document = path.read_text(encoding="utf-8")

        soup = BeautifulSoup(document, "html.parser")
        first_cell = soup.select_one("tbody td")
        self.assertEqual(len(first_cell.select("img.table-icon")), 2)
        self.assertEqual(first_cell.get_text(" ", strip=True), "×")
        self.assertNotIn("—", first_cell.get_text())
        self.assertEqual(
            soup.select_one(f'td a[href="{video_url}"]').get_text(" ", strip=True),
            "Susie x Mage-Sisters",
        )
        self.assertEqual(document.count(video_url), 1)
        unmatched_link = soup.select_one(f'.media-section a[href="{unmatched_url}"]')
        self.assertEqual(unmatched_link.get_text(" ", strip=True), "Niconico 1")

    def test_document_keeps_complete_text_table_icon_media_and_source(self):
        tail = "FINAL-CONTENT-MARKER"
        detail = "【Overview】\n" + ("Complete paragraph. " * 800) + tail
        rich_sections = [
            {
                "kind": "table",
                "title": "Boss data",
                "headers": ["Icon", "Boss", "Time"],
                "rows": [
                    [
                        {
                            "text": "",
                            "icon_data_uri": "data:image/png;base64,AA==",
                        },
                        {"text": "Boss A"},
                        {"text": "12.34"},
                    ]
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as temporary:
            path = build_wiki_document(
                Path(temporary),
                wiki_name="Kirby Wiki",
                title="Fighter",
                source_url="https://example.test/wiki/Fighter",
                summary="A complete summary.",
                detail_text=detail,
                rich_sections=rich_sections,
                image_bytes=b"\x89PNG\r\n\x1a\nimage",
                media_urls=["https://www.youtube.com/embed/example"],
                template_name="卡比粉彩",
            )
            document = path.read_text(encoding="utf-8")

        self.assertIn(tail, document)
        self.assertIn("Boss data", document)
        self.assertIn("data:image/png;base64,AA==", document)
        self.assertIn("data:image/png;base64,", document)
        self.assertIn("https://www.youtube.com/embed/example", document)
        self.assertIn("https://example.test/wiki/Fighter", document)
        self.assertNotIn("omitted", document.casefold())
        icon_cell = BeautifulSoup(document, "html.parser").select_one("tbody td")
        self.assertEqual(icon_cell.get_text(strip=True), "")
        self.assertNotIn("—", icon_cell.get_text())

    def test_cleanup_removes_only_expired_html_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            expired = output_dir / "expired.html"
            current = output_dir / "current.html"
            keep = output_dir / "keep.txt"
            expired.write_text("old", encoding="utf-8")
            current.write_text("new", encoding="utf-8")
            keep.write_text("text", encoding="utf-8")
            old_time = time.time() - 7200
            os.utime(expired, (old_time, old_time))

            cleanup_wiki_documents(output_dir, retention_minutes=60)

            self.assertFalse(expired.exists())
            self.assertTrue(current.exists())
            self.assertTrue(keep.exists())

    def test_document_keeps_module_table_order_and_definition_layout(self):
        detail = (
            "【招式列表】\n基础说明。\n"
            "【详细数据】\n"
            "- **火神百裂拳**\n"
            "  - 实际指令为松开 *B*\n"
            "  - 发生 5F\n"
            "- **粉碎拳**\n"
            "  - 指令输入时间 长按8F\n"
            "  - 发生 5F\n"
            "- **扫腿**\n"
            "  - 实际指令为冲刺+松开B\n"
            "  - 发生 1F\n"
            "【特征】\n招式丰富。"
        )
        rich_sections = [
            {
                "kind": "table",
                "title": "详细数据表",
                "source_order": 2,
                "headers": ["招式", "发生"],
                "rows": [[{"text": "火神百裂拳"}, {"text": "5F"}]],
            }
        ]

        with tempfile.TemporaryDirectory() as temporary:
            path = build_wiki_document(
                Path(temporary),
                wiki_name="卡比真格攻略 Wiki",
                title="ファイター(RBP)",
                source_url="https://example.test/Fighter",
                summary="页面概览。",
                detail_text=detail,
                rich_sections=rich_sections,
                image_bytes=None,
                media_urls=[],
                template_name="卡比粉彩",
                preserve_source_order=True,
            )
            document = path.read_text(encoding="utf-8")

        self.assertIn('class="definition-grid"', document)
        self.assertIn("<strong>火神百裂拳</strong>", document)
        self.assertIn("<em>B</em>", document)
        self.assertLess(document.index("详细数据"), document.index("详细数据表"))
        self.assertLess(document.index("详细数据表"), document.index("特征"))

    def test_document_renders_source_emphasis_in_lead_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = build_wiki_document(
                Path(temporary),
                wiki_name="卡比真格攻略 Wiki",
                title="ファイター(RBP)",
                source_url="https://example.test/Fighter",
                summary=(
                    "==身体能力==\n"
                    "**得到提升的能力。\n掌握了超乎寻常的**\n"
                    "**==拳击==与踢技。\n**"
                ),
                detail_text="【技一覧】\n攻略正文。",
                template_name="卡比粉彩",
                preserve_source_order=True,
            )
            document = path.read_text(encoding="utf-8")

        self.assertIn(
            '<span class="source-accent">身体能力</span>',
            document,
        )
        self.assertIn('<span class="source-accent">拳击</span>', document)
        self.assertIn(
            "<strong>得到提升的能力。\n掌握了超乎寻常的</strong>",
            document,
        )
        summary = document.split('<section class="summary">', 1)[1].split(
            "</section>", 1
        )[0]
        self.assertNotIn("**", summary)
        self.assertNotIn("==", summary)
        self.assertLess(document.index("身体能力"), document.index("技一覧"))


    def test_document_scales_up_record_screenshots_but_not_ability_icons(self):
        rich_sections = [
            {
                "kind": "table",
                "title": "記録集",
                "headers": ["能力", "タイム", "記録"],
                "rows": [
                    [
                        {
                            "text": "",
                            "icons": [
                                {
                                    "data_uri": "data:image/png;base64,ICON=",
                                    "kind": "icon",
                                }
                            ],
                        },
                        {"text": "12:45.49"},
                        {
                            "text": "",
                            "icons": [
                                {
                                    "data_uri": "data:image/png;base64,SHOT=",
                                    "kind": "shot",
                                    "link_url": "https://i.imgur.com/6B8owLF.jpg",
                                    "alt": "record",
                                }
                            ],
                        },
                    ]
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as temporary:
            path = build_wiki_document(
                Path(temporary),
                wiki_name="卡比真格攻略 Wiki",
                title="記録集(STA)",
                source_url="https://example.test/Records",
                summary="记录一览。",
                detail_text="",
                rich_sections=rich_sections,
            )
            document = path.read_text(encoding="utf-8")

        soup = BeautifulSoup(document, "html.parser")
        cells = soup.select("tbody td")
        self.assertEqual(len(cells[0].select("img.table-icon")), 1)
        self.assertEqual(len(cells[0].select("img.table-shot")), 0)
        self.assertEqual(
            cells[0].select_one("span.table-icon-set").get("class"),
            ["table-icon-set"],
        )
        shots = cells[2].select("img.table-shot")
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["loading"], "lazy")
        self.assertEqual(shots[0]["alt"], "record")
        self.assertIn(
            "table-shot-set",
            cells[2].select_one("span.table-icon-set").get("class"),
        )
        self.assertEqual(
            cells[2].select_one("a.table-icon-link")["href"],
            "https://i.imgur.com/6B8owLF.jpg",
        )
        self.assertIn(".table-shot", document)


if __name__ == "__main__":
    unittest.main()
