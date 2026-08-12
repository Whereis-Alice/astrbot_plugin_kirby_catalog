import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import astrbot.api.message_components as Comp

from astrbot_plugin_kirby_catalog.kirby_fandom import (
    KirbyFandomClient,
    parse_fandom_image_url,
    parse_fandom_infobox,
    parse_fandom_intro,
    parse_fandom_language_names,
    parse_fandom_rich_sections,
    parse_fandom_sections,
)
from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.wikirby_card import build_card_pages

FANDOM_HTML = """
<div class="mw-parser-output">
  <aside class="portable-infobox">
    <figure class="pi-image"><a href="https://static.wikia.nocookie.net/kirby/images/9/92/Spinni.png"><img alt="Spinni artwork" /></a></figure>
    <div class="pi-data"><h3 class="pi-data-label">Name (JA)</h3><div class="pi-data-value">スピン (Spin)</div></div>
    <div class="pi-data"><h3 class="pi-data-label">Gender</h3><div class="pi-data-value">Male</div></div>
    <div class="pi-data"><h3 class="pi-data-label">Species</h3><div class="pi-data-value">Squeak</div></div>
  </aside>
  <p><b>Spinni</b> is a member of the Squeaks that debuted in Kirby: Squeak Squad.</p>
  <h2><span class="mw-headline">Physical Appearance</span></h2>
  <p>Spinni is a yellow mouse-like creature with a red cape.</p>
  <h2><span class="mw-headline">Games</span></h2>
  <h3><span class="mw-headline">Kirby: Squeak Squad</span></h3>
  <p>Spinni attacks by throwing shurikens.</p>
  <h3><span class="mw-headline">Kirby Star Allies</span></h3>
  <p>Daroach can summon Spinni as part of an attack.</p>
  <h2><span class="mw-headline">Trivia</span></h2>
  <ul><li>Spinni loves his red cape.</li></ul>
  <h2><span class="mw-headline">Gallery</span></h2>
  <p>This media section must not be returned.</p>
</div>
"""

FANDOM_RICH_HTML = """
<div class="mw-parser-output">
  <h2><span class="mw-headline">Related Quotes</span></h2>
  <table class="br-5px"><tr><td>“</td><td><span style="font-style: italic;">First quote.</span>”</td></tr>
    <tr><td>— <span style="font-weight: bold;">Trophy description</span> • <i>Kirby Game</i></td></tr></table>
  <table class="br-5px"><tr><td>“</td><td><span style="font-style: italic;">Second quote.</span>”</td></tr>
    <tr><td>— Official website</td></tr></table>
  <h2><span class="mw-headline">Games</span></h2>
  <h3><span class="mw-headline">Kirby Star Allies</span></h3>
  <h4><span class="mw-headline">Techniques</span></h4>
  <p>Invincibility only applies during boss battles.</p>
  <div class="tabber wds-tabber">
    <ul class="wds-tabs">
      <li class="wds-tabs__tab wds-is-current"><div class="wds-tabs__tab-label">Type A</div></li>
      <li class="wds-tabs__tab"><div class="wds-tabs__tab-label">Type B</div></li>
    </ul>
    <div class="wds-tab__content wds-is-current">
      <table class="wikitable">
        <tr><th rowspan="2">Move</th><th colspan="2">Controls</th><th rowspan="2">Description</th><th rowspan="2">Damage</th></tr>
        <tr><th>Pro Controller</th><th>Joy-Con</th></tr>
        <tr><td>Brush Slash</td><td><span title="B"><img alt="B" /></span></td><td><span title="Down Button"><img alt="Down Button" /></span></td><td>Kirby swings the paintbrush.</td><td>14</td></tr>
        <tr><td>Painter</td><td colspan="2"><span title="Left Stick Down"><img alt="Left Stick Down" /></span> + <span title="B"><img alt="B" /></span></td><td>Kirby paints a helper.</td><td>16</td></tr>
      </table>
    </div>
    <div class="wds-tab__content">
      <table class="wikitable">
        <tr><th>Move</th><th>Controls</th><th>Description</th><th>Damage</th></tr>
        <tr><td>Brush Splash</td><td>Dash + B</td><td>Kirby rushes forward.</td><td>13</td></tr>
      </table>
    </div>
  </div>
  <h4><span class="mw-headline">Cameos</span></h4>
  <p>Unrelated text.</p>
</div>
"""


def sample_page():
    infobox = parse_fandom_infobox(FANDOM_HTML)
    return {
        "pageid": 1930,
        "title": "Spinni",
        "summary": parse_fandom_intro(FANDOM_HTML),
        "url": "https://kirby.fandom.com/wiki/Spinni",
        "image_url": parse_fandom_image_url(FANDOM_HTML),
        "infobox": infobox,
        "sections": parse_fandom_sections(FANDOM_HTML),
        "rich_sections": [],
        "section_index": [
            {"index": "1", "title": "Physical Appearance", "level": "2"},
            {"index": "2", "title": "Games", "level": "2"},
            {"index": "3", "title": "Kirby: Squeak Squad", "level": "3"},
            {"index": "4", "title": "Kirby Star Allies", "level": "3"},
            {"index": "5", "title": "Trivia", "level": "2"},
        ],
        "categories": ["Characters", "Squeaks"],
        "language_names": parse_fandom_language_names(
            "Spinni",
            infobox,
            [
                {
                    "lang": "zh",
                    "title": "斯品",
                    "url": "https://kirby.fandom.com/zh/wiki/斯品",
                }
            ],
        ),
    }


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return self.body


class FakeFandom:
    def __init__(self):
        self.page = sample_page()
        self.client = KirbyFandomClient(cache_ttl_seconds=0, max_detail_chars=3000)

    async def resolve(self, _query):
        return {"kind": "page", "page": self.page}

    def get_language_names(self, page):
        return self.client.get_language_names(page)

    def get_section_titles(self, page):
        return self.client.get_section_titles(page)

    def get_page_details(self, page, section=""):
        return self.client.get_page_details(page, section)

    async def get_image_bytes(self, _url):
        return None


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1", message=[])
        self.unified_msg_origin = "test:group:group-1"

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


class FakeTranslationContext:
    def __init__(self, completion_text="翻译结果"):
        self.calls = []
        self.completion_text = completion_text

    async def get_current_chat_provider_id(self, umo):
        self.calls.append(("provider", umo))
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return SimpleNamespace(completion_text=self.completion_text)


class BatchTranslationContext:
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, _umo):
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["prompt"].split("JSON：\n", 1)[1])
        for section in payload:
            for quote in section.get("quotes", []) or []:
                quote["text"] = f"译文：{quote['text']}"
        return SimpleNamespace(
            completion_text=json.dumps(payload, ensure_ascii=False)
        )


class FandomNetworkTests(unittest.TestCase):
    def test_api_403_falls_back_to_worker_and_then_prefers_it(self):
        client = KirbyFandomClient(
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
            cache_ttl_seconds=0,
        )
        blocked = HTTPError(
            "https://kirby.fandom.com/api.php",
            403,
            "Forbidden",
            {},
            BytesIO(),
        )
        response = FakeResponse(b'{"query":{"pages":[]}}')

        with patch(
            "astrbot_plugin_kirby_catalog.kirby_fandom.urlopen",
            side_effect=[blocked, response, response],
        ) as open_url:
            first = client._request_sync({"action": "query", "titles": "Kirby"})
            second = client._request_sync(
                {"action": "query", "titles": "Driblee"}
            )

        self.assertEqual(first, {"query": {"pages": []}})
        self.assertEqual(second, {"query": {"pages": []}})
        self.assertEqual(open_url.call_count, 3)
        direct_request = open_url.call_args_list[0].args[0]
        first_proxy_request = open_url.call_args_list[1].args[0]
        preferred_proxy_request = open_url.call_args_list[2].args[0]
        self.assertIn("https://kirby.fandom.com/api.php", direct_request.full_url)
        self.assertIn("site=fandom", first_proxy_request.full_url)
        self.assertIn("path=%2Fapi.php", first_proxy_request.full_url)
        self.assertIn("titles=Kirby", first_proxy_request.full_url)
        self.assertEqual(
            first_proxy_request.headers["Authorization"], "Bearer test-token"
        )
        self.assertIn("site=fandom", preferred_proxy_request.full_url)
        self.assertIn("titles=Driblee", preferred_proxy_request.full_url)

    def test_image_403_falls_back_to_worker_with_fixed_cdn_host(self):
        client = KirbyFandomClient(
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
            cache_ttl_seconds=0,
        )
        image_url = (
            "https://static.wikia.nocookie.net/kirby/images/9/92/Spinni.png?cb=1"
        )
        blocked = HTTPError(image_url, 403, "Forbidden", {}, BytesIO())

        with patch(
            "astrbot_plugin_kirby_catalog.kirby_fandom.urlopen",
            side_effect=[blocked, FakeResponse(b"image-bytes")],
        ) as open_url:
            result = client._image_bytes_sync(image_url)

        self.assertEqual(result, b"image-bytes")
        request = open_url.call_args_list[1].args[0]
        self.assertIn("site=fandom", request.full_url)
        self.assertIn("asset=image", request.full_url)
        self.assertIn("image_host=static.wikia.nocookie.net", request.full_url)
        self.assertIn("path=%2Fkirby%2Fimages%2F9%2F92%2FSpinni.png", request.full_url)
        self.assertIn("cb=1", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")


class FandomParserTests(unittest.TestCase):
    def test_extracts_intro_infobox_image_and_readable_sections(self):
        self.assertIn("member of the Squeaks", parse_fandom_intro(FANDOM_HTML))
        self.assertEqual(parse_fandom_infobox(FANDOM_HTML)[0]["label"], "日文名")
        self.assertIn("static.wikia", parse_fandom_image_url(FANDOM_HTML))
        sections = parse_fandom_sections(FANDOM_HTML)
        self.assertIn("Physical Appearance", [row["title"] for row in sections])
        self.assertIn("趣闻", [row["title"] for row in sections])
        self.assertNotIn("Gallery", [row["title"] for row in sections])

    def test_language_names_include_infobox_and_cross_wiki_titles(self):
        rows = sample_page()["language_names"]

        self.assertEqual(rows[0], {"language": "英语", "name": "Spinni"})
        self.assertEqual(
            rows[1],
            {"language": "日语", "name": "スピン", "romanisation": "Spin"},
        )
        self.assertEqual(rows[2]["name"], "斯品")

    def test_parent_section_collects_child_game_sections(self):
        client = KirbyFandomClient(cache_ttl_seconds=0, max_detail_chars=3000)

        details = client.get_page_details(sample_page(), "Games")

        self.assertEqual(
            [row["title"] for row in details["sections"]],
            ["Kirby: Squeak Squad", "Kirby Star Allies"],
        )

    def test_normalised_page_does_not_keep_full_source_html(self):
        client = KirbyFandomClient(cache_ttl_seconds=3600)
        page = client._normalise_page(
            {
                "parse": {
                    "pageid": 1930,
                    "title": "Spinni",
                    "text": FANDOM_HTML,
                    "sections": [],
                    "categories": [],
                    "langlinks": [],
                }
            },
            "Spinni",
        )

        self.assertIsNotNone(page)
        self.assertNotIn("rendered_html", page)
        self.assertNotIn("wikitext", page)

    def test_related_quotes_keep_text_attribution_and_source(self):
        rich = parse_fandom_rich_sections(FANDOM_RICH_HTML)

        quotes = next(row for row in rich if row["kind"] == "quotes")
        self.assertEqual(len(quotes["quotes"]), 2)
        self.assertEqual(quotes["quotes"][0]["text"], "First quote.")
        self.assertEqual(
            quotes["quotes"][0]["attribution"], "Trophy description"
        )
        self.assertEqual(quotes["quotes"][0]["source"], "Kirby Game")

    def test_techniques_keep_groups_columns_and_button_labels(self):
        rich = parse_fandom_rich_sections(FANDOM_RICH_HTML)

        techniques = next(row for row in rich if row["kind"] == "techniques")
        self.assertEqual(techniques["context"], "Games · Kirby Star Allies")
        self.assertEqual([group["label"] for group in techniques["groups"]], ["Type A", "Type B"])
        first = techniques["groups"][0]["rows"][0]
        self.assertEqual(first["move"], "Brush Slash")
        self.assertIn("Pro 手柄：B", first["controls"])
        self.assertIn("Joy-Con：下方向键", first["controls"])
        self.assertEqual(
            techniques["groups"][0]["rows"][1]["controls"],
            "左摇杆↓ + B",
        )
        self.assertEqual(
            techniques["groups"][1]["rows"][0]["controls"],
            "冲刺 + B",
        )
        self.assertEqual(first["damage"], "14")

    def test_plain_sections_do_not_duplicate_rich_sections(self):
        titles = [row["title"] for row in parse_fandom_sections(FANDOM_RICH_HTML)]

        self.assertNotIn("Related Quotes", titles)
        self.assertNotIn("Techniques", titles)

    def test_rich_sections_keep_their_position_between_narrative_modules(self):
        rendered_html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Overview</span></h2>
          <p>Overview body.</p>
          <h2><span class="mw-headline">Related Quotes</span></h2>
          <table class="br-5px"><tr><td>“</td><td><i>A quote.</i>”</td></tr></table>
          <h2><span class="mw-headline">Games</span></h2>
          <p>Games body.</p>
          <h3><span class="mw-headline">Techniques</span></h3>
          <table class="wikitable">
            <tr><th>Move</th><th>Controls</th><th>Description</th><th>Damage</th></tr>
            <tr><td>Slash</td><td>B</td><td>Attack.</td><td>10</td></tr>
          </table>
          <h2><span class="mw-headline">Trivia</span></h2>
          <p>Trivia body.</p>
        </div>
        """
        narrative = parse_fandom_sections(rendered_html)
        rich = KirbyCatalogPlugin._fandom_source_ordered_rich_sections(
            parse_fandom_rich_sections(rendered_html),
            narrative,
            prefix_heading_count=2,
        )
        detail_text = "\n".join(
            [
                "【页面资料】",
                "• 类型：角色",
                "【页面分类】",
                "• 分类：测试",
                *[
                    line
                    for row in narrative
                    for line in (
                        (
                            f"【{row['title']}】"
                            if str(row.get("level")) == "2"
                            else f"◆ {row['title']}"
                        ),
                        row["text"],
                    )
                ],
            ]
        )

        layout = build_card_pages(
            "Summary",
            detail_text,
            rich,
            page_line_budget=1000,
            preserve_source_order=True,
        )[0]
        titles = [group["title"] for group in layout["content_flow"]]

        self.assertEqual(
            [row["source_position"] for row in narrative],
            [1, 3, 5],
        )
        self.assertEqual(
            [row["source_position"] for row in rich],
            [2, 4],
        )
        self.assertLess(titles.index("Overview"), titles.index("相关语录"))
        self.assertLess(titles.index("相关语录"), titles.index("Games"))
        self.assertLess(titles.index("Games"), titles.index("招式与操作"))
        self.assertLess(titles.index("招式与操作"), titles.index("趣闻"))

    def test_section_query_returns_rich_techniques(self):
        client = KirbyFandomClient(cache_ttl_seconds=0, max_detail_chars=3000)
        page = {
            "sections": parse_fandom_sections(FANDOM_RICH_HTML),
            "rich_sections": parse_fandom_rich_sections(FANDOM_RICH_HTML),
            "section_index": [],
            "infobox": [],
            "categories": [],
        }

        details = client.get_page_details(page, "Techniques")

        self.assertEqual(details["sections"], [])
        self.assertEqual(len(details["rich_sections"]), 1)
        self.assertEqual(details["rich_sections"][0]["kind"], "techniques")

    def test_rich_sections_keep_every_quote_and_technique_without_truncation(self):
        quote_tables = "".join(
            (
                '<table class="br-5px"><tr><td>“</td>'
                f'<td><i>Quote {index}</i>”</td></tr></table>'
            )
            for index in range(25)
        )
        long_description = "Detailed technique description. " * 50
        technique_rows = "".join(
            (
                f"<tr><td>Move {index}</td><td>B</td>"
                f"<td>{long_description if index == 0 else 'Description'}</td>"
                "<td>12</td></tr>"
            )
            for index in range(40)
        )
        rendered_html = f"""
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Related Quotes</span></h2>
          {quote_tables}
          <h2><span class="mw-headline">Games</span></h2>
          <h3><span class="mw-headline">Kirby Game</span></h3>
          <h4><span class="mw-headline">Techniques</span></h4>
          <table class="wikitable">
            <tr><th>Move</th><th>Controls</th><th>Description</th><th>Damage</th></tr>
            {technique_rows}
          </table>
        </div>
        """

        rich = parse_fandom_rich_sections(
            rendered_html,
            max_quotes=1,
            max_technique_rows=1,
        )
        quotes = next(row for row in rich if row["kind"] == "quotes")
        techniques = next(row for row in rich if row["kind"] == "techniques")
        rows = techniques["groups"][0]["rows"]

        self.assertEqual(len(quotes["quotes"]), 25)
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows[0]["description"], long_description.strip())
        self.assertGreater(len(rows[0]["description"]), 1200)
        self.assertNotIn("omitted_count", quotes)
        self.assertNotIn("omitted_count", techniques)

    def test_intro_infobox_sections_and_categories_are_complete(self):
        intro = "Full introduction sentence. " * 100
        infobox = "".join(
            (
                '<div class="pi-data">'
                f'<span class="pi-data-label">Field {index}</span>'
                f'<div class="pi-data-value">Value {index}</div></div>'
            )
            for index in range(24)
        )
        rendered_html = (
            '<div class="mw-parser-output"><aside class="portable-infobox">'
            + infobox
            + f"</aside><p>{intro}</p></div>"
        )
        page = {
            "sections": [
                {"title": f"Section {index}", "text": f"Body {index}"}
                for index in range(75)
            ],
            "rich_sections": [],
            "section_index": [],
            "infobox": parse_fandom_infobox(rendered_html),
            "categories": [f"Category {index}" for index in range(15)],
        }
        client = KirbyFandomClient(
            cache_ttl_seconds=0,
            max_summary_chars=100,
            max_detail_chars=1000,
        )

        details = client.get_page_details(page)

        self.assertEqual(parse_fandom_intro(rendered_html, max_chars=100), intro.strip())
        self.assertEqual(len(page["infobox"]), 24)
        self.assertEqual(len(details["sections"]), 75)
        self.assertIn("Category 14", details["categories"][0]["value"])

    def test_normalised_page_caches_parsed_rich_sections_only(self):
        client = KirbyFandomClient(cache_ttl_seconds=3600)
        page = client._normalise_page(
            {
                "parse": {
                    "pageid": 88,
                    "title": "Artist",
                    "text": FANDOM_RICH_HTML,
                    "sections": [],
                    "categories": [],
                    "langlinks": [],
                }
            },
            "Artist",
        )

        self.assertIsNotNone(page)
        self.assertTrue(page["rich_sections"])
        self.assertNotIn("rendered_html", page)


class FandomCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **config):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"fandom_enabled": True, "fandom_show_image": False, **config}
        plugin.fandom = FakeFandom()
        return plugin

    async def test_full_query_returns_fandom_source_once(self):
        plugin = self.make_plugin()

        results = [
            result
            async for result in plugin.fandom_query_plain(
                FakeEvent("/卡比Fandom Spinni")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("Kirby Fandom：Spinni", results[0][0].text)
        self.assertIn("Physical Appearance", results[0][0].text)
        self.assertIn("来源：https://kirby.fandom.com/wiki/Spinni", results[0][0].text)

    def test_short_commands_parse_page_names_sections_and_detail(self):
        plugin = self.make_plugin()

        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F Spinni")),
            ("Spinni", "page", "", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F名称 Spinni")),
            ("Spinni", "names", "", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("/卡比F章节 Spinni")),
            ("Spinni", "sections", "", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F Spinni | Games")),
            ("Spinni", "page", "Games", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F文本 Spinni")),
            ("Spinni", "page", "", "text"),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F卡片 Spinni")),
            ("Spinni", "page", "", "card"),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F文档 Spinni")),
            ("Spinni", "page", "", "document"),
        )

    async def test_document_command_returns_generated_html_file(self):
        with TemporaryDirectory() as temporary:
            plugin = self.make_plugin(wiki_document_translate_enabled=False)
            plugin.store = SimpleNamespace(root=Path(temporary))
            plugin.fandom.page["rich_sections"] = parse_fandom_rich_sections(
                FANDOM_RICH_HTML
            )

            results = [
                result
                async for result in plugin._fandom_query_impl(
                    FakeEvent("卡比F文档 Spinni")
                )
            ]

            file_component = next(
                component
                for component in results[0]
                if isinstance(component, Comp.File)
            )
            document = Path(file_component.file)
            self.assertTrue(document.exists())
            self.assertIn("First quote.", document.read_text(encoding="utf-8"))

    async def test_section_query_returns_only_requested_parent_section(self):
        plugin = self.make_plugin()

        results = [
            result
            async for result in plugin.fandom_query_plain(
                FakeEvent("卡比F Spinni | Games")
            )
        ]

        text = results[0][0].text
        self.assertIn("Kirby: Squeak Squad", text)
        self.assertIn("Kirby Star Allies", text)
        self.assertNotIn("Physical Appearance", text)

    async def test_names_query_explains_names_are_not_official(self):
        plugin = self.make_plugin()

        result = await plugin.fandom_lookup_names(FakeEvent(""), "Spinni")

        self.assertIn("日语：スピン（Spin）", result)
        self.assertIn("不等同于任天堂官方译名", result)

    async def test_fandom_llm_tools_resolve_wiki_index_numbers(self):
        plugin = self.make_plugin()
        original_resolve = plugin.fandom.resolve
        plugin.fandom.resolve = AsyncMock(side_effect=original_resolve)
        plugin.wiki_index = SimpleNamespace(
            resolve=lambda site, number: (
                {"target": "Spinni"}
                if site == "fandom" and number == 120
                else None
            )
        )

        page_result = await plugin.fandom_lookup_page(FakeEvent(""), "#120")
        names_result = await plugin.fandom_lookup_names(FakeEvent(""), "序号 120")

        self.assertIn("Kirby Fandom：Spinni", page_result)
        self.assertIn("多语言页面名称", names_result)
        self.assertEqual(
            [call.args[0] for call in plugin.fandom.resolve.await_args_list],
            ["Spinni", "Spinni"],
        )

    async def test_fandom_llm_tool_rejects_disabled_wiki_index_number(self):
        plugin = self.make_plugin()
        plugin.fandom.resolve = AsyncMock(side_effect=plugin.fandom.resolve)
        plugin.wiki_index = SimpleNamespace(resolve=lambda _site, _number: None)

        result = await plugin.fandom_lookup_page(FakeEvent(""), "#120")

        self.assertIn("当前没有启用序号 #120", result)
        plugin.fandom.resolve.assert_not_awaited()

    async def test_section_list_keeps_more_than_sixty_entries(self):
        plugin = self.make_plugin()
        plugin.fandom.page["section_index"] = [
            {"index": str(index), "title": f"Section {index}", "level": "2"}
            for index in range(1, 76)
        ]

        result = await plugin._fandom_sections_text("Spinni")

        self.assertIn("75. Section 75", result)
        self.assertNotIn("未显示", result)

    async def test_llm_lookup_does_not_trigger_nested_translation(self):
        plugin = self.make_plugin(fandom_translate_enabled=True)
        plugin.context = FakeTranslationContext()

        result = await plugin.fandom_lookup_page(FakeEvent(""), "Spinni")

        self.assertIn("member of the Squeaks", result)
        self.assertEqual(plugin.context.calls, [])

    async def test_fandom_card_payload_identifies_the_source(self):
        plugin = self.make_plugin(fandom_card_template="卡比粉彩")
        plugin.html_render = AsyncMock(return_value="fandom-card.png")
        rich_sections = parse_fandom_rich_sections(FANDOM_RICH_HTML)

        component = await plugin._fandom_card_component(
            sample_page(), "Summary", "Gender: Male", None, rich_sections
        )

        self.assertIsNotNone(component)
        payload = plugin.html_render.await_args.args[1]
        self.assertEqual(payload["wiki_name"], "Kirby Fandom")
        self.assertEqual(payload["reference_label"], "FANDOM REFERENCE")
        self.assertEqual(payload["rich_sections"][0]["kind"], "quotes")
        self.assertEqual(payload["rich_sections"][1]["kind"], "techniques")
        self.assertIn("technique-table", plugin.html_render.await_args.args[0])
        self.assertEqual(
            plugin.html_render.await_args.kwargs["options"]["viewport_width"], 1600
        )
        template = plugin.html_render.await_args.args[0]
        self.assertNotIn("已显示", template)
        self.assertNotIn("未显示", template)

    async def test_invalid_structured_translation_falls_back_to_original(self):
        plugin = self.make_plugin(fandom_translate_enabled=True)
        plugin.context = FakeTranslationContext("not json")
        rich_sections = parse_fandom_rich_sections(FANDOM_RICH_HTML)

        translated = await plugin._fandom_translate_rich_sections(
            FakeEvent(""), rich_sections
        )

        self.assertEqual(translated, rich_sections)

    async def test_long_structured_translation_is_batched_without_losing_quotes(self):
        plugin = self.make_plugin(
            fandom_translate_enabled=True,
            wiki_translation_chunk_chars=1000,
        )
        plugin.context = BatchTranslationContext()
        rich_sections = [
            {
                "kind": "quotes",
                "title": "Related Quotes",
                "context": "Kirby",
                "quotes": [
                    {
                        "text": f"Quote {index}: " + ("long text " * 30),
                        "attribution": "Narrator",
                        "source": "Kirby Game",
                    }
                    for index in range(12)
                ],
            }
        ]

        translated = await plugin._fandom_translate_rich_sections(
            FakeEvent(""), rich_sections
        )

        self.assertGreater(len(plugin.context.calls), 1)
        self.assertEqual(len(translated[0]["quotes"]), 12)
        self.assertTrue(
            all(
                quote["text"].startswith("译文：")
                for quote in translated[0]["quotes"]
            )
        )

    async def test_structured_translation_preserves_controls_and_damage(self):
        plugin = self.make_plugin(fandom_translate_enabled=True)
        rich_sections = parse_fandom_rich_sections(FANDOM_RICH_HTML)
        translated_payload = json.loads(json.dumps(rich_sections))
        quotes = next(row for row in translated_payload if row["kind"] == "quotes")
        quotes["quotes"][0]["text"] = "第一条语录。"
        techniques = next(
            row for row in translated_payload if row["kind"] == "techniques"
        )
        techniques["groups"][0]["rows"][0]["move"] = "画笔斩"
        techniques["groups"][0]["rows"][0]["description"] = "卡比挥动画笔。"
        techniques["groups"][0]["rows"][0]["controls"] = "错误操作"
        techniques["groups"][0]["rows"][0]["damage"] = "999"
        plugin.context = FakeTranslationContext(
            json.dumps(translated_payload, ensure_ascii=False)
        )

        translated = await plugin._fandom_translate_rich_sections(
            FakeEvent(""), rich_sections
        )

        translated_techniques = next(
            row for row in translated if row["kind"] == "techniques"
        )
        first = translated_techniques["groups"][0]["rows"][0]
        original_first = next(
            row for row in rich_sections if row["kind"] == "techniques"
        )["groups"][0]["rows"][0]
        self.assertEqual(first["move"], "画笔斩")
        self.assertEqual(first["description"], "卡比挥动画笔。")
        self.assertEqual(first["controls"], original_first["controls"])
        self.assertEqual(first["damage"], original_first["damage"])

    async def test_structured_translation_accepts_safe_translated_controls(self):
        plugin = self.make_plugin(fandom_translate_enabled=True)
        rich_sections = parse_fandom_rich_sections(FANDOM_RICH_HTML)
        translated_payload = json.loads(json.dumps(rich_sections))
        techniques = next(
            row for row in translated_payload if row["kind"] == "techniques"
        )
        translated_controls = "Pro 手柄：按 B\nJoy-Con：按下方向键"
        techniques["groups"][0]["rows"][0]["controls"] = translated_controls
        plugin.context = FakeTranslationContext(
            json.dumps(translated_payload, ensure_ascii=False)
        )

        translated = await plugin._fandom_translate_rich_sections(
            FakeEvent(""), rich_sections
        )

        translated_techniques = next(
            row for row in translated if row["kind"] == "techniques"
        )
        self.assertEqual(
            translated_techniques["groups"][0]["rows"][0]["controls"],
            translated_controls,
        )

    def test_control_translation_rejects_missing_or_changed_inputs(self):
        source = "Pro 手柄：左摇杆↓ + B\nJoy-Con：下方向键"

        self.assertFalse(
            KirbyCatalogPlugin._translated_controls_are_safe(
                source, "Pro 手柄：左摇杆↑ + B\nJoy-Con：下方向键"
            )
        )
        self.assertFalse(
            KirbyCatalogPlugin._translated_controls_are_safe(
                source, "Pro 手柄：左摇杆↓ + B\nJoy-Con：方向键"
            )
        )
        self.assertFalse(
            KirbyCatalogPlugin._translated_controls_are_safe(
                source, "Pro 手柄：左摇杆↓ + B；Joy-Con：下方向键"
            )
        )


if __name__ == "__main__":
    unittest.main()
