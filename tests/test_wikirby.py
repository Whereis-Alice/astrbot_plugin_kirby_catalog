import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import astrbot.api.message_components as Comp
from jinja2 import BaseLoader, Environment

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.terminology import (
    KirbyTerminologyStore,
    TerminologyEntry,
    terminology_document,
)
from astrbot_plugin_kirby_catalog.wikirby import (
    WikirbyClient,
    WikirbyError,
    parse_language_names,
    parse_locations_html,
    parse_page_details,
    parse_rendered_language_names,
    parse_rendered_sections,
)
from astrbot_plugin_kirby_catalog.wikirby_card import (
    CARD_TEMPLATE_NAMES,
    WIKIRBY_CARD_TEMPLATE,
    build_card_layout,
    build_card_pages,
    estimate_text_lines,
    resolve_card_template,
)


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1", message=[])
        self.unified_msg_origin = "test:group:group-1"
        self.is_at_or_wake_command = False

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


class FakeWikirby:
    async def resolve(self, query):
        return {
            "kind": "page",
            "page": {
                "pageid": 1,
                "lastrevid": 2,
                "title": query,
                "summary": "A short Kirby character summary.",
                "url": "https://wikirby.com/wiki/Driblee",
                "image_url": "",
            },
        }

    async def get_language_names(self, page):
        return [
            {"language": "简体中文", "name": "噗噜鳗", "romanisation": "pū lū màn"},
            {"language": "英语", "name": "Driblee"},
        ]

    async def get_page_details(self, page):
        return {"infobox": [], "sections": []}


class FakeTranslationContext:
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, umo):
        self.calls.append(("provider", umo))
        return "native-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return SimpleNamespace(completion_text="这是一段中文简介。")


class TerminologyTranslationContext:
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, _umo):
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        source = kwargs["prompt"].split("原文：\n", 1)[1]
        return SimpleNamespace(completion_text=source.replace(" met ", " 遇见 "))


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return self.body


class WikirbyParserTests(unittest.TestCase):
    def test_api_urls_include_alternate_wikirby_hostname(self):
        client = WikirbyClient()

        self.assertEqual(
            client._api_urls(),
            (
                "https://wikirby.com/w/api.php",
                "https://www.wikirby.com/w/api.php",
            ),
        )
        self.assertEqual(
            client._rest_urls(),
            (
                "https://wikirby.com/w/rest.php",
                "https://www.wikirby.com/w/rest.php",
            ),
        )

    def test_api_request_retries_403_then_uses_alternate_hostname(self):
        error = HTTPError(
            "https://wikirby.com/w/api.php",
            403,
            "Forbidden",
            {"Retry-After": "0"},
            BytesIO(),
        )
        response = FakeResponse(b'{"query":{"pages":[]}}')
        client = WikirbyClient(cache_ttl_seconds=0)

        with patch(
            "astrbot_plugin_kirby_catalog.wikirby.urlopen",
            side_effect=[error, error, response],
        ) as open_url, patch("astrbot_plugin_kirby_catalog.wikirby.time.sleep"):
            data = client._request_sync({"action": "query"})

        self.assertEqual(data, {"query": {"pages": []}})
        self.assertEqual(open_url.call_count, 3)
        self.assertIn("www.wikirby.com", open_url.call_args_list[-1].args[0].full_url)

    def test_proxy_rewrites_target_path_and_sends_bearer_token(self):
        client = WikirbyClient(
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
            cache_ttl_seconds=0,
        )
        response = FakeResponse(b'{"query":{"pages":[]}}')

        with patch(
            "astrbot_plugin_kirby_catalog.wikirby.urlopen",
            return_value=response,
        ) as open_url:
            client._request_sync({"action": "query", "titles": "Driblee"})

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url.split("?", 1)[0], "https://kirby-proxy.example.workers.dev")
        self.assertIn("path=%2Fw%2Fapi.php", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_proxy_downloads_cdn_image_with_asset_marker(self):
        client = WikirbyClient(
            proxy_url="https://kirby-proxy.example.workers.dev",
            proxy_token="test-token",
            cache_ttl_seconds=0,
        )
        response = FakeResponse(b"image-bytes")

        with patch(
            "astrbot_plugin_kirby_catalog.wikirby.urlopen",
            return_value=response,
        ) as open_url:
            result = client._image_bytes_sync(
                "https://cdn.wikirby.com/3/33/KSA_Driblee_Artwork.png"
            )

        self.assertEqual(result, b"image-bytes")
        request = open_url.call_args.args[0]
        self.assertIn("path=%2F3%2F33%2FKSA_Driblee_Artwork.png", request.full_url)
        self.assertIn("asset=image", request.full_url)

    def test_extracts_language_names_without_meanings(self):
        wikitext = """
==Names in other languages==
{{Names
|ja=プルアンナ
|jaR=Puruanna
|en=Driblee<ref name=KSA>source</ref>
|zhTrad=噗嚕鰻
|zhTradR=pū lū màn
|zhSimp=噗噜鳗
|zhSimpR=pū lū màn
|zhM=Plu Eel
|ko=탱장어
}}
"""

        rows = parse_language_names(wikitext)

        self.assertEqual(rows[0], {"language": "日语", "name": "プルアンナ", "romanisation": "Puruanna"})
        self.assertEqual(rows[1], {"language": "英语", "name": "Driblee"})
        self.assertEqual(rows[2]["language"], "繁体中文")
        self.assertEqual(rows[3]["name"], "噗噜鳗")
        self.assertNotIn("Plu Eel", " ".join(row["name"] for row in rows))

    def test_extracts_hand_authored_language_tables_with_subheadings(self):
        rendered_html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Names in other languages</span></h2>
          <h3><span class="mw-headline">Meta Knight</span></h3>
          <table class="roundtable">
            <tr><th>Language</th><th>Name</th><th>Meaning</th></tr>
            <tr><td><b>Simplified Chinese</b></td><td>魅塔骑士<br><i>Mèitǎ Qíshì</i></td><td>Meta Knight</td></tr>
            <tr><td><b>Japanese</b></td><td><ruby>メタ<rt>めた</rt></ruby>ナイト<br><i>Meta Naito</i></td><td>Meta Knight</td></tr>
          </table>
          <h3><span class="mw-headline">The Lone Swordsman, Meta Knight</span></h3>
          <table class="roundtable">
            <tr><th>Language</th><th>Name</th><th>Meaning</th></tr>
            <tr><td><b>Simplified Chinese</b></td><td>孤高的骑士 魅塔骑士<br><i>Gūgāo de Qíshì Mèitǎ Qíshì</i></td><td>The Lone Knight, Meta Knight</td></tr>
          </table>
        </div>
        """

        rows = parse_rendered_language_names(rendered_html)

        self.assertEqual(
            rows[0],
            {
                "language": "简体中文",
                "name": "魅塔骑士",
                "romanisation": "Mèitǎ Qíshì",
                "section": "Meta Knight",
            },
        )
        self.assertEqual(rows[1]["language"], "日语")
        self.assertEqual(rows[1]["name"], "メタナイト")
        self.assertNotIn("めた", rows[1]["name"])
        self.assertEqual(rows[2]["section"], "The Lone Swordsman, Meta Knight")

        details = parse_page_details("", rendered_html)
        language_section = details["sections"][-1]
        self.assertEqual(language_section["title"], "其他语言名称")
        self.assertIn("【Meta Knight】", language_section["text"])
        self.assertIn("简体中文：魅塔骑士", language_section["text"])

    def test_page_url_is_converted_to_title(self):
        self.assertEqual(
            WikirbyClient._title_from_url("https://wikirby.com/wiki/Driblee"),
            "Driblee",
        )

    def test_rest_image_url_uses_cdn_file_hash(self):
        self.assertEqual(
            WikirbyClient._image_url_from_wikitext(
                "{{Infobox|image=[[File:KSA Driblee Artwork.png|200px]]}}"
            ),
            "https://cdn.wikirby.com/3/33/KSA_Driblee_Artwork.png",
        )

    def test_raw_page_builds_page_data_from_wikitext(self):
        client = WikirbyClient(cache_ttl_seconds=0)
        raw = b"{{Infobox|image=[[File:KSA Driblee Artwork.png]]}}\nDriblee summary."

        with patch.object(client, "_read_urls_sync", return_value=raw):
            page = client._raw_page_sync("Driblee")

        self.assertEqual(page["title"], "Driblee")
        self.assertIn("Driblee summary", page["summary"])
        self.assertIn("cdn.wikirby.com/3/33", page["image_url"])

    def test_general_page_beats_work_specific_page_for_localised_name(self):
        query = "瓦豆鲁迪"
        base = {"title": "Waddle Dee", "snippet": query, "wordcount": 12809}
        work_specific = {
            "title": "Waddle Dee (Kirby 64: The Crystal Shards)",
            "snippet": query,
            "wordcount": 1722,
        }

        self.assertGreater(
            WikirbyClient._score_page(query, base),
            WikirbyClient._score_page(query, work_specific),
        )

    def test_extracts_selected_page_details_without_gallery_or_names(self):
        wikitext = """
{{Infobox-Enemy
|game1=''[[Kirby Star Allies]]'' (2018)
|copy ability=[[Water]]
|similar=[[Water Galbo]], [[Colossal Driblee]]
}}
==Locations==
Driblee can be found in the following stages:
{{Appearances-KSA|DoD=y}}
==Trivia==
*It appears in a trailer.
==Gallery==
{{center|<gallery>file.png</gallery>}}
==Names in other languages==
{{Names
|en=Driblee
}}
"""

        details = parse_page_details(wikitext)

        self.assertEqual(
            details["infobox"],
            [
                {"label": "出现作品", "value": "Kirby Star Allies (2018)"},
                {"label": "提供能力", "value": "Water"},
                {"label": "相似角色", "value": "Water Galbo, Colossal Driblee"},
            ],
        )
        self.assertEqual(details["sections"][0]["title"], "出现地点")
        self.assertIn("following stages", details["sections"][0]["text"])
        self.assertEqual(details["sections"][1]["title"], "趣闻")
        self.assertNotIn("Gallery", " ".join(row["title"] for row in details["sections"]))
        self.assertNotIn("Names", " ".join(row["title"] for row in details["sections"]))
        language_section = details["sections"][2]
        self.assertEqual(language_section["title"], "其他语言名称")
        self.assertIn("英语：Driblee", language_section["text"])

    def test_extracts_yes_locations_from_rendered_wikitable(self):
        rendered_html = """
        <table class="wikitable mw-collapsible">
          <tr><th>Stage</th><th>Appearance?</th></tr>
          <tr>
            <td><a href="/wiki/Falluna_Moon">Falluna Moon</a></td>
            <td><img alt="Yes" src="yes.png"></td>
          </tr>
          <tr>
            <td><a href="/wiki/Planet_Earthfall">Planet Earthfall</a></td>
            <td><img alt="No" src="no.png"></td>
          </tr>
          <tr>
            <td><a href="/wiki/Donut_Dome">Donut Dome</a></td>
            <td><img alt="Yes" src="yes.png"></td>
          </tr>
        </table>
        <table class="navbox"><tr><td>Do not include</td></tr></table>
        """

        self.assertEqual(
            parse_locations_html(rendered_html),
            ["Falluna Moon", "Donut Dome"],
        )

    def test_merges_rendered_locations_into_page_details(self):
        details = parse_page_details(
            """\n==Locations==
Driblee can be found in the following stages:
{{Appearances-KSA|DoD=y}}
""",
            """
            <table class="wikitable">
              <tr><th>Stage</th><th>Appearance?</th></tr>
              <tr><td><a>Donut Dome</a></td><td><img alt="Yes"></td></tr>
            </table>
            """,
        )

        self.assertIn("• Donut Dome", details["sections"][0]["text"])

    def test_rendered_html_keeps_headings_and_compacts_tables(self):
        rendered_html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Game appearances</span></h2>
          <table class="wikitable">
            <tr><th colspan="2">Waddle Doo's video game appearances</th></tr>
            <tr><th>Game</th><th>Role</th></tr>
            <tr><td>Kirby's Dream Land</td><td>Enemy</td></tr>
          </table>
          <h3><span class="mw-headline">Kirby's Dream Land</span></h3>
          <figure>Artwork that should not be included</figure>
          <p>Waddle Doo fires a beam at Kirby.</p>
          <h2><span class="mw-headline">Gallery</span></h2>
          <p>Gallery content should not be included.</p>
        </div>
        """

        sections = parse_rendered_sections(rendered_html)

        self.assertEqual(sections[0]["title"], "Game appearances")
        self.assertIn("• Kirby's Dream Land — Enemy", sections[0]["text"])
        self.assertEqual(sections[1]["title"], "Kirby's Dream Land")
        self.assertIn("fires a beam", sections[1]["text"])
        self.assertNotIn("Artwork", " ".join(item["text"] for item in sections))
        self.assertNotIn("Gallery", " ".join(item["title"] for item in sections))

    def test_rendered_details_keep_all_table_rows_and_long_blocks(self):
        long_cell = "Complete table cell " * 60
        table_rows = "".join(
            f"<tr><td>Row {index}</td><td>{long_cell if index == 54 else 'Value'}</td></tr>"
            for index in range(55)
        )
        long_definition = "Definition text " * 50
        long_flex = "Flexible detail " * 70
        rendered_html = f"""
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Game appearances</span></h2>
          <table class="wikitable">
            <tr><th>Entry</th><th>Description</th></tr>
            {table_rows}
          </table>
          <dl><dt>Notes</dt><dd>{long_definition}</dd></dl>
          <div style="display:flex">{long_flex}</div>
        </div>
        """

        sections = parse_rendered_sections(rendered_html)
        body = sections[0]["text"]

        self.assertIn(f"• Row 54 — {long_cell.strip()}", body)
        self.assertIn(long_definition.strip(), body)
        self.assertIn(long_flex.strip(), body)
        self.assertNotIn("...", body)

    def test_wikitext_fallback_keeps_more_than_forty_table_rows(self):
        table_rows = "\n".join(
            f"|-\n| Row {index} || Value {index}" for index in range(55)
        )
        details = parse_page_details(
            "==Game appearances==\n{| class=\"wikitable\"\n"
            "! Entry !! Description\n"
            f"{table_rows}\n|}}"
        )

        self.assertIn("• Row 54 — Value 54", details["sections"][0]["text"])

    def test_wikitext_fallback_removes_table_and_media_syntax(self):
        details = parse_page_details(
            dedent(
                """
            ==Game appearances==
            {| class="wikitable"
            |-
            ! Game !! Role
            |-
            | [[Kirby's Dream Land]] || Enemy
            |}
            ===Kirby's Adventure===
            File:KA Waddle Doo sprite.png
            Waddle Doo is a common enemy.
            """
            )
        )

        text = details["sections"][0]["text"]
        self.assertIn("• Kirby's Dream Land — Enemy", text)
        self.assertIn("Kirby's Adventure：", text)
        self.assertNotIn("{|", text)
        self.assertNotIn("File:", text)


class WikirbyCardTests(unittest.TestCase):
    def test_card_facts_band_turns_fact_rows_into_key_value_items(self):
        layout = build_card_layout(
            "简介。",
            "种类：敌人\n提供能力：光束\n其他语言名称：\n• 日语：ワドルドゥ\n• 简体中文：瓦豆鲁笃",
        )

        self.assertEqual(
            layout["left_blocks"][0]["fact_items"][0],
            {"label": "种类", "value": "敌人"},
        )
        self.assertEqual(
            layout["left_blocks"][1]["fact_items"][1],
            {"label": "简体中文", "value": "瓦豆鲁笃"},
        )

    def test_card_template_renders_fact_items(self):
        layout = build_card_layout("简介。", "种类：敌人\n提供能力：光束")
        html = Environment(loader=BaseLoader(), autoescape=True).from_string(
            WIKIRBY_CARD_TEMPLATE
        ).render(
            title="Waddle Doo",
            source="https://wikirby.com/wiki/Waddle_Doo",
            theme=resolve_card_template("梦之泉"),
            **layout,
            image_data_uri="",
        )

        self.assertIn('class="fact-list"', html)
        self.assertIn('class="facts-band"', html)
        self.assertNotIn('class="sidebar"', html)
        self.assertIn("提供能力", html)
        self.assertNotIn("已显示", html)
        self.assertNotIn("未显示", html)

    def test_card_layout_uses_facts_band_and_two_detail_columns(self):
        detail = "游戏登场：\n" + "\n".join(
            f"第 {index} 段：" + "瓦豆鲁笃在关卡中移动并攻击卡比。" * 8
            for index in range(1, 24)
        )
        detail += "\n其他语言名称：\n英语：Waddle Doo\n日语：ワドルドゥ"

        layout = build_card_layout(
            "这是一段简介。" * 30,
            detail,
        )

        self.assertIn("简介", layout["summary"])
        self.assertTrue(layout["left_blocks"])
        self.assertEqual(len(layout["right_columns"]), 2)
        self.assertGreater(
            sum(
                estimate_text_lines(block["body"])
                for column in layout["right_columns"]
                for block in column
            ),
            100,
        )

    def test_short_card_stays_on_one_page(self):
        pages = build_card_pages(
            "简短简介。",
            "种类：敌人\n提供能力：水",
            page_line_budget=110,
            has_image=True,
        )

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page_number"], 1)
        self.assertEqual(pages[0]["page_total"], 1)
        self.assertTrue(pages[0]["show_summary"])

    def test_oversized_card_paginates_without_losing_technique_rows(self):
        rows = [
            {
                "move": f"招式 {index}",
                "controls": "Pro 手柄：B",
                "description": f"第 {index} 行说明。" * 12,
                "damage": str(index),
            }
            for index in range(1, 31)
        ]
        pages = build_card_pages(
            "简介。" * 20,
            "游戏登场：\n" + "完整正文。" * 500,
            [
                {
                    "kind": "techniques",
                    "context": "Kirby Fighters 2",
                    "intro": "完整招式表。",
                    "groups": [{"label": "Type A", "rows": rows}],
                }
            ],
            page_line_budget=90,
            has_image=True,
        )

        self.assertGreater(len(pages), 1)
        self.assertTrue(pages[0]["show_summary"])
        self.assertTrue(all(not page["show_summary"] for page in pages[1:]))
        self.assertTrue(
            all(page["page_total"] == len(pages) for page in pages)
        )
        rendered_moves = [
            row["move"].removesuffix("（续）")
            for page in pages
            for section in page["rich_sections"]
            for group in section.get("groups", [])
            for row in group.get("rows", [])
        ]
        self.assertEqual(set(rendered_moves), {f"招式 {index}" for index in range(1, 31)})

    def test_all_card_templates_resolve_to_distinct_variants(self):
        themes = [resolve_card_template(name) for name in CARD_TEMPLATE_NAMES]

        self.assertEqual(len({theme["slug"] for theme in themes}), 4)
        self.assertEqual(len({theme["surface"] for theme in themes}), 4)
        self.assertTrue(all(theme["surface"].casefold() != "#ffffff" for theme in themes))
        self.assertTrue(all(int(theme["surface"][1:3], 16) >= 238 for theme in themes))
        self.assertTrue(all(theme.get("panel_a") for theme in themes))
        self.assertEqual(resolve_card_template("unknown")["slug"], "fountain")


class WikirbyClientDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_page_request_and_fallback_summary_are_not_truncated(self):
        client = WikirbyClient(cache_ttl_seconds=0, max_summary_chars=100)
        summary = "Complete introduction. " * 150
        client._request = AsyncMock(
            return_value={
                "query": {
                    "pages": [
                        {
                            "pageid": 7,
                            "title": "Waddle Doo",
                            "extract": summary,
                            "fullurl": "https://wikirby.com/wiki/Waddle_Doo",
                            "lastrevid": 11,
                        }
                    ]
                }
            }
        )

        page = await client.get_page("Waddle Doo")
        request_params = client._request.await_args.args[0]

        self.assertNotIn("exchars", request_params)
        self.assertEqual(page["summary"], summary.strip())
        self.assertEqual(
            client._summary_from_wikitext(summary, max_chars=100),
            summary.strip(),
        )

    async def test_search_resolution_refetches_selected_page_in_full(self):
        client = WikirbyClient(cache_ttl_seconds=0)
        full_page = {
            "pageid": 12,
            "title": "Waddle Doo",
            "summary": "Full selected-page introduction.",
            "url": "https://wikirby.com/wiki/Waddle_Doo",
        }
        client.get_page = AsyncMock(side_effect=[None, full_page])
        client.search_pages = AsyncMock(
            return_value=[
                {
                    "pageid": 12,
                    "title": "Waddle Doo",
                    "snippet": "瓦豆鲁笃",
                    "wordcount": 1000,
                }
            ]
        )

        result = await client.resolve("瓦豆鲁笃")

        self.assertEqual(result, {"kind": "page", "page": full_page})
        self.assertEqual(
            [call.args[0] for call in client.get_page.await_args_list],
            ["瓦豆鲁笃", "Waddle Doo"],
        )

    async def test_details_always_request_rendered_html(self):
        client = WikirbyClient(cache_ttl_seconds=0)
        page = {
            "pageid": 7,
            "lastrevid": 11,
            "title": "Waddle Doo",
            "wikitext": "{{Infobox-Enemy|copy ability=[[Beam]]}}",
        }
        rendered_html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Game appearances</span></h2>
          <p>Waddle Doo appears in Kirby's Dream Land.</p>
        </div>
        """

        with patch.object(
            client,
            "_get_rendered_page_html",
            new=AsyncMock(return_value=rendered_html),
        ) as get_rendered:
            details = await client.get_page_details(page)

        get_rendered.assert_awaited_once_with("Waddle Doo")
        self.assertEqual(details["sections"][0]["title"], "Game appearances")

    async def test_names_fall_back_to_rendered_language_table(self):
        client = WikirbyClient(cache_ttl_seconds=0)
        page = {
            "pageid": 24,
            "lastrevid": 25,
            "title": "Meta Knight",
            "wikitext": "No Names template here.",
        }
        rendered_html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline">Names in other languages</span></h2>
          <h3><span class="mw-headline">Meta Knight</span></h3>
          <table class="roundtable">
            <tr><th>Language</th><th>Name</th></tr>
            <tr><td>Simplified Chinese</td><td>魅塔骑士<br><i>Mèitǎ Qíshì</i></td></tr>
          </table>
        </div>
        """

        with patch.object(
            client,
            "_get_rendered_page_html",
            new=AsyncMock(return_value=rendered_html),
        ) as get_rendered:
            names = await client.get_language_names(page)

        get_rendered.assert_awaited_once_with("Meta Knight")
        self.assertEqual(names[0]["name"], "魅塔骑士")
        self.assertEqual(names[0]["romanisation"], "Mèitǎ Qíshì")


class WikirbyCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_falls_back_to_rest_when_mediawiki_api_is_blocked(self):
        plugin_client = WikirbyClient(cache_ttl_seconds=0)
        rest_page = {
            "pageid": 1,
            "lastrevid": 2,
            "title": "Driblee",
            "summary": "summary",
            "url": "https://wikirby.com/wiki/Driblee",
            "image_url": "",
            "wikitext": "==Names in other languages==",
        }

        with patch.object(
            plugin_client,
            "_request",
            side_effect=WikirbyError("HTTP 403"),
        ), patch.object(
            plugin_client, "_rest_page_sync", return_value=rest_page
        ):
            result = await plugin_client.get_page("Driblee")

        self.assertEqual(result, rest_page)

    async def test_name_command_only_returns_language_names(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True, "wikirby_show_image": False}
        plugin.wikirby = FakeWikirby()

        results = [
            result
            async for result in plugin._wikirby_query_impl(
                FakeEvent("卡比百科名称 Driblee")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("简体中文：噗噜鳗（pū lū màn）", results[0])
        self.assertIn("英语：Driblee", results[0])
        self.assertNotIn("简介：", results[0])

    async def test_summary_command_returns_source(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True, "wikirby_show_image": False}
        plugin.wikirby = FakeWikirby()

        results = [
            result
            async for result in plugin._wikirby_query_impl(FakeEvent("卡比百科 Driblee"))
        ]

        self.assertEqual(len(results), 1)
        self.assertIn("WiKirby：Driblee", results[0][0].text)
        self.assertIn("来源：https://wikirby.com/wiki/Driblee", results[0][0].text)
        self.assertNotIn("剧透", results[0][0].text)

    async def test_official_names_are_available_as_llm_tool(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True}
        plugin.wikirby = FakeWikirby()

        result = await plugin.wikirby_lookup_official_names(
            FakeEvent(""), "Driblee"
        )

        self.assertIn("简体中文：噗噜鳗（pū lū màn）", result)
        self.assertIn("来源：https://wikirby.com/wiki/Driblee", result)

    async def test_full_wikirby_page_is_available_as_llm_tool(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True}
        plugin.wikirby = FakeWikirby()

        result = await plugin.wikirby_lookup_page(FakeEvent(""), "Driblee")

        self.assertIn("WiKirby：Driblee", result)
        self.assertIn("【简介】", result)
        self.assertIn("来源：https://wikirby.com/wiki/Driblee", result)

    async def test_wikirby_llm_tools_resolve_wiki_index_numbers(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True}
        plugin.wikirby = FakeWikirby()
        original_resolve = plugin.wikirby.resolve
        plugin.wikirby.resolve = AsyncMock(side_effect=original_resolve)
        plugin.wiki_index = SimpleNamespace(
            resolve=lambda site, number: (
                {"target": "Meta Knight"}
                if site == "wikirby" and number == 88
                else None
            )
        )

        page_result = await plugin.wikirby_lookup_page(FakeEvent(""), "#88")
        names_result = await plugin.wikirby_lookup_official_names(
            FakeEvent(""), "编号 88"
        )

        self.assertIn("WiKirby：Meta Knight", page_result)
        self.assertIn("官方名称", names_result)
        self.assertEqual(
            [call.args[0] for call in plugin.wikirby.resolve.await_args_list],
            ["Meta Knight", "Meta Knight"],
        )

    async def test_wikirby_llm_tool_rejects_disabled_wiki_index_number(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True}
        plugin.wikirby = FakeWikirby()
        plugin.wikirby.resolve = AsyncMock(side_effect=plugin.wikirby.resolve)
        plugin.wiki_index = SimpleNamespace(resolve=lambda _site, _number: None)

        result = await plugin.wikirby_lookup_page(FakeEvent(""), "#88")

        self.assertIn("当前没有启用序号 #88", result)
        plugin.wikirby.resolve.assert_not_awaited()

    async def test_full_wikirby_llm_tool_does_not_run_nested_translation(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_enabled": True,
            "wikirby_translate_enabled": True,
        }
        plugin.wikirby = FakeWikirby()
        plugin.context = FakeTranslationContext()

        result = await plugin.wikirby_lookup_page(FakeEvent(""), "Driblee")

        self.assertIn("A short Kirby character summary.", result)
        self.assertEqual(plugin.context.calls, [])

    async def test_unified_handler_handles_slash_command_once(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_enabled": True, "wikirby_show_image": False}
        plugin.wikirby = FakeWikirby()
        event = FakeEvent("/卡比百科 Driblee")

        results = [result async for result in plugin.wikirby_query_plain(event)]

        self.assertEqual(len(results), 1)
        self.assertIn("WiKirby：Driblee", results[0][0].text)

    async def test_output_mode_accepts_forward_and_card_choices(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"wikirby_output_mode": "文字+卡片合并转发"}

        self.assertEqual(plugin._wikirby_output_mode(), "card_forward")

    async def test_short_forward_text_uses_one_node(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_enabled": True,
            "wikirby_show_image": False,
            "wikirby_output_mode": "合并转发",
        }
        plugin.wikirby = FakeWikirby()

        results = [
            result
            async for result in plugin._wikirby_query_impl(
                FakeEvent("卡比百科 Driblee")
            )
        ]

        self.assertIsInstance(results[0][0], Comp.Nodes)
        self.assertEqual(len(results[0][0].nodes), 1)

    def test_forward_mode_splits_long_text_and_separates_image_node(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"forward_node_max_chars": 1000}
        text = "".join(
            f"第{index}段：" + ("卡比百科正文。" * 90) + "\n\n"
            for index in range(12)
        )

        components = plugin._wiki_response_components(
            text, b"image-bytes", "forward", None
        )

        self.assertEqual(len(components), 2)
        self.assertTrue(all(isinstance(component, Comp.Nodes) for component in components))
        text_nodes = components[0].nodes
        image_nodes = components[1].nodes
        self.assertGreater(len(text_nodes), 2)
        self.assertEqual(len(image_nodes), 1)
        self.assertIsInstance(image_nodes[0].content[0], Comp.Image)
        self.assertTrue(
            all(isinstance(node.content[0], Comp.Plain) for node in text_nodes)
        )
        self.assertTrue(all(len(node.content[0].text) <= 1000 for node in text_nodes))
        self.assertEqual("".join(node.content[0].text for node in text_nodes), text)

    def test_short_forward_keeps_text_and_one_image_in_one_message(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {"forward_max_images_per_message": 2}

        components = plugin._wiki_response_components(
            "简短百科内容", b"image-bytes", "forward", None
        )

        self.assertEqual(len(components), 1)
        self.assertEqual(len(components[0].nodes), 2)
        self.assertIsInstance(components[0].nodes[0].content[0], Comp.Plain)
        self.assertIsInstance(components[0].nodes[1].content[0], Comp.Image)

    def test_card_forward_limits_images_and_does_not_mix_long_text(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "forward_node_max_chars": 500,
            "forward_max_images_per_message": 2,
        }
        text = "完整百科正文。" * 600
        cards = [Comp.Image.fromBytes(f"card-{index}".encode()) for index in range(5)]

        components = plugin._wiki_response_components(
            text, None, "card_forward", cards
        )

        text_batches = [
            component
            for component in components
            if all(isinstance(node.content[0], Comp.Plain) for node in component.nodes)
        ]
        image_batches = [
            component
            for component in components
            if all(isinstance(node.content[0], Comp.Image) for node in component.nodes)
        ]
        self.assertTrue(text_batches)
        self.assertEqual(len(image_batches), 3)
        self.assertTrue(all(len(component.nodes) <= 2 for component in image_batches))
        self.assertEqual(
            "".join(
                node.content[0].text
                for component in text_batches
                for node in component.nodes
            ),
            text,
        )

    def test_forward_mode_batches_excess_nodes_without_losing_content(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "forward_node_max_chars": 500,
            "forward_max_nodes_per_message": 5,
        }
        text = "\n\n".join(
            f"第 {index} 段：" + ("完整百科正文。" * 80) for index in range(18)
        )

        components = plugin._wiki_response_components(
            text, b"image-bytes", "forward", None
        )

        self.assertGreater(len(components), 1)
        self.assertTrue(all(isinstance(component, Comp.Nodes) for component in components))
        self.assertTrue(all(len(component.nodes) <= 5 for component in components))
        self.assertTrue(
            all(
                not (
                    any(isinstance(node.content[0], Comp.Plain) for node in component.nodes)
                    and any(isinstance(node.content[0], Comp.Image) for node in component.nodes)
                )
                for component in components
            )
        )
        nodes = [node for component in components for node in component.nodes]
        self.assertIsInstance(nodes[-1].content[0], Comp.Image)
        text_nodes = nodes[:-1]
        self.assertEqual("".join(node.content[0].text for node in text_nodes), text)

    async def test_translation_cache_avoids_repeating_the_same_llm_request(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_translate_provider_id": "native-provider",
            "wikirby_cache_ttl_seconds": 3600,
        }
        plugin.context = FakeTranslationContext()
        event = FakeEvent("卡比百科 Driblee")

        first = await plugin._wiki_translate_text(
            event,
            "The same source text.",
            enabled=True,
            provider_key="wikirby_translate_provider_id",
            source_name="WiKirby",
        )
        second = await plugin._wiki_translate_text(
            event,
            "The same source text.",
            enabled=True,
            provider_key="wikirby_translate_provider_id",
            source_name="WiKirby",
        )

        self.assertEqual(first, "这是一段中文简介。")
        self.assertEqual(second, first)
        generate_calls = [call for call in plugin.context.calls if call[0] == "generate"]
        self.assertEqual(len(generate_calls), 1)

    async def test_translation_protects_and_restores_terminology(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundled_path = root / "terms.json"
            bundled_path.write_text(
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
                            ),
                            TerminologyEntry.from_mapping(
                                {
                                    "term_id": "character:meta-knight",
                                    "category": "character",
                                    "zh_cn": "魅塔骑士",
                                    "en": "Meta Knight",
                                    "ja": "メタナイト",
                                    "zh_status": "official",
                                }
                            ),
                        ]
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
            plugin.config = {
                "wikirby_translate_provider_id": "provider",
                "wikirby_cache_ttl_seconds": 3600,
                "terminology_enabled": True,
                "terminology_strict_placeholders": True,
            }
            plugin.terminology = KirbyTerminologyStore(
                bundled_path, root / "overrides.json"
            )
            plugin.context = TerminologyTranslationContext()

            result = await plugin._wiki_translate_text(
                FakeEvent("卡比百科 Kirby"),
                "Kirby met Meta Knight.",
                enabled=True,
                provider_key="wikirby_translate_provider_id",
                source_name="WiKirby",
            )

            self.assertIn("卡比（Kirby）", result)
            self.assertIn("魅塔骑士（Meta Knight）", result)
            self.assertIn("遇见", result)
            self.assertIn("__KTERM_", plugin.context.calls[0]["prompt"])

    async def test_card_mode_uses_astrbot_html_renderer(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_enabled": True,
            "wikirby_show_image": False,
            "wikirby_output_mode": "仅百科卡片",
        }
        plugin.wikirby = FakeWikirby()
        plugin.html_render = AsyncMock(return_value="card.png")

        results = [
            result
            async for result in plugin._wikirby_query_impl(
                FakeEvent("卡比百科 Driblee")
            )
        ]

        self.assertIsInstance(results[0][0], Comp.Image)
        plugin.html_render.assert_awaited_once()
        render_call = plugin.html_render.await_args
        self.assertEqual(render_call.kwargs["options"]["selector"], "#kirby-card")
        self.assertTrue(render_call.kwargs["options"]["full_page"])
        self.assertEqual(render_call.kwargs["options"]["viewport_width"], 1600)
        self.assertEqual(render_call.kwargs["options"]["viewport_height"], 600)
        self.assertEqual(render_call.kwargs["options"]["scale"], "device")
        self.assertEqual(
            render_call.kwargs["options"]["device_scale_factor_level"], "high"
        )
        self.assertEqual(render_call.kwargs["options"]["type"], "jpeg")
        self.assertEqual(render_call.kwargs["options"]["quality"], 92)
        self.assertNotIn("viewport", render_call.kwargs["options"])

    async def test_card_renders_one_image_with_selected_template(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_card_template": "瓦豆鲁迪",
        }
        plugin.html_render = AsyncMock(return_value="card.png")
        page = {
            "title": "Waddle Doo",
            "url": "https://wikirby.com/wiki/Waddle_Doo",
        }

        component = await plugin._wikirby_card_component(
            page,
            "简介。" * 300,
            "游戏登场：\n" + ("瓦豆鲁笃会发射光束。" * 1200),
            None,
        )

        self.assertIsNotNone(component)
        self.assertEqual(plugin.html_render.await_count, 1)
        first_payload = plugin.html_render.await_args_list[0].args[1]
        self.assertEqual(first_payload["theme"]["slug"], "waddle")
        self.assertEqual(len(first_payload["right_columns"]), 2)

    async def test_oversized_wiki_card_renders_multiple_numbered_pages(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wiki_card_auto_paginate": True,
            "wiki_card_page_line_budget": 80,
        }
        plugin.html_render = AsyncMock(return_value="card.png")
        page = {
            "title": "Kirby",
            "url": "https://wikirby.com/wiki/Kirby",
        }

        components = await plugin._wikirby_card_components(
            page,
            "卡比简介。" * 30,
            "游戏登场：\n"
            + "\n".join(
                f"第 {index} 部作品：" + "完整经历。" * 30
                for index in range(1, 25)
            ),
            None,
        )

        self.assertGreater(len(components), 1)
        self.assertEqual(plugin.html_render.await_count, len(components))
        payloads = [call.args[1] for call in plugin.html_render.await_args_list]
        self.assertEqual(
            [payload["page_number"] for payload in payloads],
            list(range(1, len(payloads) + 1)),
        )
        self.assertTrue(all(payload["page_total"] == len(payloads) for payload in payloads))

    async def test_wiki_card_line_budget_accepts_values_above_300(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wiki_card_auto_paginate": True,
            "wiki_card_page_line_budget": 1200,
        }
        plugin.html_render = AsyncMock(return_value="card.png")
        page = {
            "title": "Kirby",
            "url": "https://wikirby.com/wiki/Kirby",
        }

        with patch(
            "astrbot_plugin_kirby_catalog.main.build_card_pages",
            wraps=build_card_pages,
        ) as page_builder:
            components = await plugin._wikirby_card_components(
                page,
                "卡比简介。",
                "游戏登场：\n星之卡比",
                None,
            )

        self.assertEqual(len(components), 1)
        self.assertEqual(page_builder.call_args.kwargs["page_line_budget"], 1200)

    async def test_summary_can_use_native_provider_for_translation(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_enabled": True,
            "wikirby_show_image": False,
            "wikirby_translate_enabled": True,
        }
        plugin.wikirby = FakeWikirby()
        plugin.context = FakeTranslationContext()

        results = [
            result
            async for result in plugin._wikirby_query_impl(
                FakeEvent("卡比百科 Driblee")
            )
        ]

        self.assertIn("【简介】\n这是一段中文简介。", results[0][0].text)
        self.assertEqual(plugin.context.calls[0], ("provider", "test:group:group-1"))
        self.assertEqual(plugin.context.calls[1][0], "generate")

    async def test_explicit_output_mode_suffixes_are_parsed(self):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {}

        self.assertEqual(
            plugin._wikirby_query_parts(FakeEvent("卡比百科文本 Driblee")),
            ("Driblee", False, "text"),
        )
        self.assertEqual(
            plugin._wikirby_query_parts(FakeEvent("卡比百科卡片 Driblee")),
            ("Driblee", False, "card"),
        )
        self.assertEqual(
            plugin._wikirby_query_parts(FakeEvent("卡比百科文档 Driblee")),
            ("Driblee", False, "document"),
        )

        plugin.config = {"wiki_text_command_use_forward": True}
        self.assertEqual(
            plugin._resolved_wiki_output_mode("card", "text"), "forward"
        )
        plugin.config = {"wiki_text_command_use_forward": False}
        self.assertEqual(plugin._resolved_wiki_output_mode("card", "text"), "text")

    async def test_document_command_returns_generated_html_file(self):
        with TemporaryDirectory() as temporary:
            plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
            plugin.config = {
                "wikirby_enabled": True,
                "wikirby_show_image": False,
                "wiki_document_translate_enabled": False,
            }
            plugin.wikirby = FakeWikirby()
            plugin.store = SimpleNamespace(root=Path(temporary))

            results = [
                result
                async for result in plugin._wikirby_query_impl(
                    FakeEvent("卡比百科文档 Driblee")
                )
            ]

            file_component = next(
                component
                for component in results[0]
                if isinstance(component, Comp.File)
            )
            self.assertTrue(Path(file_component.file).exists())
            self.assertTrue(file_component.name.endswith(".html"))


if __name__ == "__main__":
    unittest.main()
