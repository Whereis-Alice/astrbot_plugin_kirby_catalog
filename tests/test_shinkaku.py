import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from astrbot.api import message_components as Comp

from astrbot_plugin_kirby_catalog.kirby_shinkaku import KirbyShinkakuClient
from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.wikirby_card import build_card_pages


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return self.body


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1", message=[])
        self.unified_msg_origin = "test:group:group-1"

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


def sample_page():
    return {
        "title": "\u30de\u30db\u30ed\u30a2EX",
        "summary": "\u771f\u683c\u653b\u7565 Wiki \u306e\u8cc7\u6599\u30da\u30fc\u30b8",
        "url": "https://seesaawiki.jp/kirby_shinkaku/d/%a5%de%a5%db%a5%ed%a5%a2EX",
        "image_url": "",
        "sections": [
            {
                "title": "\u884c\u52d5\u30d1\u30bf\u30fc\u30f3",
                "text": "\u653b\u6483\u306e\u30e1\u30e2\u3067\u3059\u3002",
                "level": "1",
            },
            {
                "title": "\u653b\u7565\u6cd5",
                "text": "\u56de\u907f\u306e\u30e1\u30e2\u3067\u3059\u3002",
                "level": "1",
            },
        ],
        "section_index": [
            {"index": "1", "title": "\u884c\u52d5\u30d1\u30bf\u30fc\u30f3", "level": "1"},
            {"index": "2", "title": "\u653b\u7565\u6cd5", "level": "1"},
        ],
    }


class FakeShinkaku:
    def __init__(self):
        self.page = sample_page()

    async def resolve(self, _query, aliases=None):
        return {"kind": "page", "page": self.page}

    async def lookup_terms(self, _query):
        return [{"japanese": "\u30e1\u30bf\u30ca\u30a4\u30c8", "english": "Meta Knight"}]

    def get_section_titles(self, page):
        return page["section_index"]

    def get_page_details(self, page, section=""):
        if not section:
            return {"sections": page["sections"]}
        return {
            "sections": [
                row for row in page["sections"] if row["title"] == section
            ]
        }

    async def get_image_bytes(self, _image_url):
        return None


class ShinkakuClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_and_search_urls_use_euc_jp(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        title = "\u30de\u30db\u30ed\u30a2EX"

        self.assertIn("%A5%DE%A5%DB%A5%ED%A5%A2EX", client._page_url(title))
        self.assertIn("keywords=%A5%DE%A5%DB%A5%ED%A5%A2", client._search_url(title))
        self.assertIn("search_target=page_name", client._search_url(title))

    async def test_html_decoder_falls_back_to_cp932_when_needed(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        text = "\u30e1\u30bf\u30ca\u30a4\u30c8(Wii)"

        self.assertEqual(client._decode_html(text.encode("cp932")), text)

    async def test_worker_fallback_preserves_euc_jp_search_query(self):
        client = KirbyShinkakuClient(
            cache_ttl_seconds=0,
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
        )
        target = client._search_url("\u30de\u30db\u30ed\u30a2")
        blocked = HTTPError(target, 403, "Forbidden", {}, BytesIO())

        with patch(
            "astrbot_plugin_kirby_catalog.kirby_shinkaku.urlopen",
            side_effect=[blocked, FakeResponse(b"ok")],
        ) as open_url:
            self.assertEqual(
                client._read_target_with_proxy_fallback_sync(target, image=False),
                b"ok",
            )

        proxy_request = open_url.call_args_list[1].args[0]
        self.assertIn("site=shinkaku", proxy_request.full_url)
        self.assertIn("path=%2Fkirby_shinkaku%2Fsearch", proxy_request.full_url)
        self.assertIn("raw_query=keywords%3D%25A5%25DE", proxy_request.full_url)
        self.assertNotIn("%EF%BF%BD", proxy_request.full_url)
        self.assertEqual(proxy_request.headers["Authorization"], "Bearer test-token")

    async def test_preferred_worker_404_stays_a_normal_missing_page(self):
        client = KirbyShinkakuClient(
            cache_ttl_seconds=0,
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
        )
        client._proxy_preferred_until = 10**12
        target = client._page_url("Magolor EX")
        proxy_url = client._proxy_url_for(target, image=False)
        missing = HTTPError(proxy_url, 404, "Not Found", {}, BytesIO())

        with patch(
            "astrbot_plugin_kirby_catalog.kirby_shinkaku.urlopen",
            side_effect=[missing],
        ) as open_url:
            page = client._get_page_sync("Magolor EX")

        self.assertIsNone(page)
        self.assertEqual(open_url.call_count, 1)

    async def test_page_parser_extracts_sections_tables_and_first_image(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        html = """
        <div id="main">
          <div id="page-header"><h2>\u30de\u30db\u30ed\u30a2EX</h2></div>
          <img src="https://image01.seesaawiki.jp/k/u/kirby_shinkaku/magolor.jpg">
          <h3>\u884c\u52d5\u30d1\u30bf\u30fc\u30f3</h3>
          <div class="wiki-section-body-1"><p>\u5149\u5f3e\u3092\u653e\u3064\u3002</p><table>
            <tr><th>\u884c\u52d5</th><th>\u5bfe\u7b56</th></tr><tr><td>\u7a81\u9032</td><td>\u56de\u907f</td></tr>
          </table></div>
          <h3>\u653b\u7565\u6cd5</h3>
          <div class="wiki-section-body-1"><p>\u7126\u3089\u305a\u89b3\u5bdf\u3059\u308b\u3002</p></div>
        </div>
        """.encode("euc_jp")
        page = client._parse_page(
            html,
            "\u30de\u30db\u30ed\u30a2EX",
            "https://seesaawiki.jp/kirby_shinkaku/d/%a5%de%a5%db%a5%ed%a5%a2EX",
        )

        self.assertIsNotNone(page)
        self.assertEqual(page["title"], "\u30de\u30db\u30ed\u30a2EX")
        self.assertEqual(
            [row["title"] for row in page["sections"]],
            ["\u884c\u52d5\u30d1\u30bf\u30fc\u30f3", "\u653b\u7565\u6cd5"],
        )
        self.assertIn("\u8868\u683c\uff1a\u884c\u52d5 | \u5bfe\u7b56", page["sections"][0]["text"])
        self.assertIn("image01.seesaawiki.jp", page["image_url"])

    async def test_search_parser_keeps_page_title_links_not_navigation(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        source = """
        <div id="main" class="page-result">
          <div class="result-box"><div class="paging-top">
            <a href="/kirby_shinkaku/search?p=2">2</a>
          </div></div>
          <div class="result-box"><div class="body">
            <h3 class="keyword"><a href="/kirby_shinkaku/d/%a5%e1%a5%bf">\u30e1\u30bf\u30ca\u30a4\u30c8(STA)</a></h3>
            <p class="url"><a href="/kirby_shinkaku/d/%a5%e1%a5%bf">duplicate URL</a></p>
          </div></div>
        </div>
        """.encode("euc_jp")
        with patch.object(
            client,
            "_read_target_with_proxy_fallback_sync",
            return_value=source,
        ):
            pages = await client.search_pages("\u30e1\u30bf\u30ca\u30a4\u30c8")

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["title"], "\u30e1\u30bf\u30ca\u30a4\u30c8(STA)")

    async def test_english_corner_terms_and_exact_guard(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        source = """
        <div id="main"><table>
          <tr><th>\u65e5\u672c\u540d</th><th>\u82f1\u540d</th></tr>
          <tr><td>\u30e1\u30bf\u30ca\u30a4\u30c8</td><td>Meta Knight</td></tr>
          <tr><td>\u30c0\u30fc\u30af\u30e1\u30bf\u30ca\u30a4\u30c8</td><td>Dark Meta Knight's Revenge</td></tr>
        </table></div>
        """.encode("euc_jp")
        with patch.object(
            client,
            "_read_target_with_proxy_fallback_sync",
            return_value=source,
        ):
            rows = await client.lookup_terms("Meta Knight")

        self.assertEqual(rows[0]["japanese"], "\u30e1\u30bf\u30ca\u30a4\u30c8")
        self.assertFalse(
            client._term_row_matches(
                {
                    "japanese": "\u30c0\u30fc\u30af\u30e1\u30bf\u30ca\u30a4\u30c8",
                    "english": "Dark Meta Knight's Revenge",
                },
                "Meta Knight",
            )
        )
        self.assertEqual(
            client._english_base_and_suffix("Magolor EX"), ("Magolor", "EX")
        )


    async def test_page_parser_keeps_original_table_icon_url(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        html = b"""
        <div id=\"main\">
          <div id=\"page-header\"><h2>Test</h2></div>
          <h3>Times</h3>
          <div class=\"wiki-section-body-1\"><table>
            <tr><th>Icon</th><th>Ability</th><th>Rating</th></tr>
            <tr><td><img src=\"https://image01.seesaawiki.jp/k/u/kirby_shinkaku/ice.png\"></td><td>Ice</td><td>SS</td></tr>
          </table></div>
        </div>
        """
        page = client._parse_page(
            html,
            "Test",
            "https://seesaawiki.jp/kirby_shinkaku/d/Test",
        )

        cell = page["sections"][0]["tables"][0]["rows"][0][0]
        self.assertEqual(cell["text"], "")
        self.assertEqual(
            cell["icon_url"],
            "https://image01.seesaawiki.jp/k/u/kirby_shinkaku/ice.png",
        )


class ShinkakuCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "shinkaku_enabled": True,
            "shinkaku_show_image": False,
            "shinkaku_output_mode": "普通消息",
        }
        plugin.shinkaku = FakeShinkaku()
        plugin.store = None
        return plugin

    async def test_page_command_uses_normal_message_and_page_content(self):
        plugin = self.make_plugin()

        results = [
            result
            async for result in plugin._shinkaku_query_impl(
                FakeEvent("/\u5361\u6bd4\u771f\u683c Magolor EX")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0][0], Comp.Plain)
        self.assertIn("\u30de\u30db\u30ed\u30a2EX", results[0][0].text)
        self.assertIn("\u653b\u7565\u8d44\u6599", results[0][0].text)

    async def test_wiki_case_and_section_alias_are_parsed(self):
        plugin = self.make_plugin()
        query, mode, section = plugin._shinkaku_query_parts(
            FakeEvent("\u5361\u6bd4\u771f\u683cwiki\u7ae0\u8282 Magolor EX")
        )

        self.assertEqual(query, "Magolor EX")
        self.assertEqual(mode, "sections")
        self.assertEqual(section, "")

    async def test_terms_and_llm_tools_are_available(self):
        plugin = self.make_plugin()

        term_results = [
            result
            async for result in plugin._shinkaku_query_impl(
                FakeEvent("\u5361\u6bd4\u771f\u683c\u540d\u79f0 Meta Knight")
            )
        ]
        page_result = await plugin.shinkaku_lookup_page(
            FakeEvent(""), "Magolor EX"
        )
        term_result = await plugin.shinkaku_lookup_terms(FakeEvent(""), "Meta Knight")

        self.assertIn("Meta Knight", term_results[0])
        self.assertIn("\u30de\u30db\u30ed\u30a2EX", page_result)
        self.assertIn("\u30e1\u30bf\u30ca\u30a4\u30c8", term_result)

    async def test_nested_shinkaku_settings_are_read(self):
        plugin = self.make_plugin()
        plugin.config = {
            "shinkaku_settings": {
                "shinkaku_translate_enabled": True,
                "shinkaku_translate_provider_id": "translation-model",
                "shinkaku_output_mode": "文字+卡片合并转发",
            }
        }

        self.assertTrue(plugin._shinkaku_translate_enabled())
        self.assertEqual(
            plugin._config_value("shinkaku_translate_provider_id", ""),
            "translation-model",
        )
        self.assertEqual(plugin._shinkaku_output_mode(), "card_forward")

    async def test_table_translation_keeps_icons_and_invariant_scores(self):
        plugin = self.make_plugin()
        source = [
            {
                "kind": "table",
                "title": "Times",
                "headers": ["Icon", "Ability", "Rating", "Record"],
                "rows": [
                    [
                        {"text": "", "icon_url": "https://example.test/ice.png"},
                        {"text": "Ice", "icon_url": ""},
                        {"text": "SS", "icon_url": ""},
                        {"text": "34.17", "icon_url": ""},
                    ]
                ],
            }
        ]
        translated = [
            {
                "kind": "table",
                "title": "用时",
                "headers": ["图标", "能力", "评级", "实机记录"],
                "rows": [["图标", "冰", "超级", "三十四点一七"]],
            }
        ]

        result = plugin._translated_rich_sections(source, translated)

        self.assertEqual(result[0]["title"], "用时")
        self.assertEqual(result[0]["rows"][0][0]["text"], "")
        self.assertEqual(
            result[0]["rows"][0][0]["icon_url"],
            "https://example.test/ice.png",
        )
        self.assertEqual(result[0]["rows"][0][1]["text"], "冰")
        self.assertEqual(result[0]["rows"][0][2]["text"], "SS")
        self.assertEqual(result[0]["rows"][0][3]["text"], "34.17")

    async def test_card_layout_keeps_table_rows_structured(self):
        layouts = build_card_pages(
            "Test summary",
            "",
            [
                {
                    "kind": "table",
                    "title": "Times",
                    "headers": ["Icon", "Ability", "Rating", "Record"],
                    "rows": [
                        [
                            {"text": "", "icon_data_uri": "data:image/png;base64,AA=="},
                            {"text": "Ice"},
                            {"text": "SS"},
                            {"text": "34.17"},
                        ]
                    ],
                }
            ],
            page_line_budget=60,
            force_paginate=True,
        )

        table = layouts[0]["rich_sections"][0]
        self.assertEqual(table["kind"], "table")
        self.assertEqual(
            table["rows"][0][0]["icon_data_uri"],
            "data:image/png;base64,AA==",
        )


if __name__ == "__main__":
    unittest.main()
