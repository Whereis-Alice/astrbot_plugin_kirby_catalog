import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_kirby_catalog.kirby_fandom import (
    KirbyFandomClient,
    parse_fandom_image_url,
    parse_fandom_infobox,
    parse_fandom_intro,
    parse_fandom_language_names,
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
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, umo):
        self.calls.append(("provider", umo))
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return SimpleNamespace(completion_text="翻译结果")


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

        component = await plugin._fandom_card_component(
            sample_page(), "Summary", "Gender: Male", None
        )

        self.assertIsNotNone(component)
        payload = plugin.html_render.await_args.args[1]
        self.assertEqual(payload["wiki_name"], "Kirby Fandom")
        self.assertEqual(payload["reference_label"], "FANDOM REFERENCE")
        self.assertEqual(
            plugin.html_render.await_args.kwargs["options"]["viewport_width"], 1600
        )


if __name__ == "__main__":
    unittest.main()
