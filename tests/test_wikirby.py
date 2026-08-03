import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.wikirby import WikirbyClient, parse_language_names


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(group_id="group-1", message=[])

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


class WikirbyCommandTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
