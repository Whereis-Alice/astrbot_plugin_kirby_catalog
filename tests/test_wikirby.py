import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import astrbot.api.message_components as Comp

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.wikirby import (
    WikirbyClient,
    WikirbyError,
    parse_language_names,
    parse_locations_html,
    parse_page_details,
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

    async def test_forward_mode_wraps_text_in_one_forward_node(self):
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

        self.assertIn("简介：\n这是一段中文简介。", results[0][0].text)
        self.assertEqual(plugin.context.calls[0], ("provider", "test:group:group-1"))
        self.assertEqual(plugin.context.calls[1][0], "generate")


if __name__ == "__main__":
    unittest.main()
