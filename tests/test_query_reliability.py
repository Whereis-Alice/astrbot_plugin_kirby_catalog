import unittest
from unittest.mock import AsyncMock, patch

from astrbot_plugin_kirby_catalog.kirby_shinkaku import KirbyShinkakuClient
from astrbot_plugin_kirby_catalog.wiki_content import inline_markup_html
from bs4 import BeautifulSoup


class ShinkakuQueryReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_english_game_suffix_does_not_resolve_the_base_game_page(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        base_title = "メタナイト"
        qualified_title = "メタナイト(NewGame)"

        async def get_page(title):
            if title in {base_title, qualified_title}:
                return {"title": title}
            return None

        with (
            patch.object(client, "lookup_page_names", return_value=[]),
            patch.object(
                client,
                "english_terms",
                AsyncMock(return_value=[{"japanese": base_title, "english": "Meta Knight"}]),
            ),
            patch.object(client, "get_page", AsyncMock(side_effect=get_page)) as fetch,
            patch.object(client, "search_pages", AsyncMock()) as search,
        ):
            result = await client.resolve("Meta Knight (NewGame)")

        self.assertEqual(result["page"]["title"], qualified_title)
        self.assertNotIn(base_title, [call.args[0] for call in fetch.call_args_list])
        search.assert_not_awaited()

    async def test_english_ex_suffix_does_not_fall_back_to_normal_boss(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)

        async def get_page(title):
            return {"title": title} if title in {"マホロア", "マホロアEX"} else None

        with (
            patch.object(client, "lookup_page_names", return_value=[]),
            patch.object(
                client,
                "english_terms",
                AsyncMock(return_value=[{"japanese": "マホロア", "english": "Magolor"}]),
            ),
            patch.object(client, "get_page", AsyncMock(side_effect=get_page)) as fetch,
        ):
            result = await client.resolve("Magolor EX")

        self.assertEqual(result["page"]["title"], "マホロアEX")
        self.assertNotIn("マホロア", [call.args[0] for call in fetch.call_args_list])


class WikiEncodingAndLinkTests(unittest.TestCase):
    def test_decoder_honours_utf8_meta_instead_of_accepting_shift_jis_mojibake(self):
        for declaration in (
            '<meta charset="utf-8">',
            '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">',
        ):
            text = declaration + "<p>攻略</p>"
            with self.subTest(declaration=declaration):
                self.assertEqual(KirbyShinkakuClient._decode_html(text.encode("utf-8")), text)

    def test_decoder_honours_utf8_bom(self):
        text = "<p>攻略</p>"
        self.assertEqual(KirbyShinkakuClient._decode_html(text.encode("utf-8-sig")), text)

    def test_decoder_keeps_euc_jp_and_handles_invalid_charset(self):
        text = '<meta charset="unknown-encoding"><p>トルネイド(Wii)</p>'
        self.assertEqual(KirbyShinkakuClient._decode_html(text.encode("euc_jp")), text)

    def test_inline_video_link_keeps_query_parameters(self):
        url = "https://www.youtube.com/watch?v=abc&t=45&list=example"
        soup = BeautifulSoup(inline_markup_html("视频：" + url), "html.parser")
        self.assertEqual(soup.a["href"], url)
        self.assertEqual(soup.a.get_text(), url)

    def test_inline_link_still_escapes_attribute_quotes(self):
        url = 'https://example.test/?q="quoted"&value=1'
        soup = BeautifulSoup(inline_markup_html(url), "html.parser")
        self.assertEqual(soup.a["href"], url)
        self.assertEqual(set(soup.a.attrs), {"href", "target", "rel"})


if __name__ == "__main__":
    unittest.main()
