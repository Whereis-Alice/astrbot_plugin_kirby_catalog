import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        self.assertIn("Pro Controller：B", first["controls"])
        self.assertIn("Joy-Con：Down Button", first["controls"])
        self.assertEqual(
            techniques["groups"][0]["rows"][1]["controls"],
            "Left Stick Down + B",
        )
        self.assertEqual(first["damage"], "14")

    def test_plain_sections_do_not_duplicate_rich_sections(self):
        titles = [row["title"] for row in parse_fandom_sections(FANDOM_RICH_HTML)]

        self.assertNotIn("Related Quotes", titles)
        self.assertNotIn("Techniques", titles)

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
            ("Spinni", "page", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F名称 Spinni")),
            ("Spinni", "names", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("/卡比F章节 Spinni")),
            ("Spinni", "sections", ""),
        )
        self.assertEqual(
            plugin._fandom_query_parts(FakeEvent("卡比F Spinni | Games")),
            ("Spinni", "page", "Games"),
        )

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

    async def test_invalid_structured_translation_falls_back_to_original(self):
        plugin = self.make_plugin(fandom_translate_enabled=True)
        plugin.context = FakeTranslationContext("not json")
        rich_sections = parse_fandom_rich_sections(FANDOM_RICH_HTML)

        translated = await plugin._fandom_translate_rich_sections(
            FakeEvent(""), rich_sections
        )

        self.assertEqual(translated, rich_sections)

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


if __name__ == "__main__":
    unittest.main()
