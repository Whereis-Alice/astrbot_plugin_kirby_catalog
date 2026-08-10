import os
import tempfile
import time
import unittest
from pathlib import Path

from astrbot_plugin_kirby_catalog.wiki_document import (
    build_wiki_document,
    cleanup_wiki_documents,
)


class WikiDocumentTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
