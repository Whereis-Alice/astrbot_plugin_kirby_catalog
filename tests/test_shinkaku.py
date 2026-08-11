import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from astrbot.api import message_components as Comp
from jinja2 import BaseLoader, Environment
from PIL import Image

from astrbot_plugin_kirby_catalog.kirby_shinkaku import (
    KirbyShinkakuClient,
    _normalise_term,
)
from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin
from astrbot_plugin_kirby_catalog.shinkaku_reference import (
    render_shinkaku_reference_pages,
)
from astrbot_plugin_kirby_catalog.wiki_content import (
    inline_markup_plain,
    parse_detail_blocks,
)
from astrbot_plugin_kirby_catalog.wikirby_card import (
    WIKIRBY_CARD_TEMPLATE,
    build_card_pages,
    resolve_card_template,
)


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


class BatchTranslationContext:
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, _umo):
        return "provider"

    async def llm_generate(self, **kwargs):
        import json

        self.calls.append(kwargs)
        payload = json.loads(kwargs["prompt"].split("JSON：\n", 1)[1])
        for section in payload:
            section["title"] = f"译文：{section['title']}"
            for row in section.get("rows", []) or []:
                if row and row[0]:
                    row[0] = f"译文：{row[0]}"
        return SimpleNamespace(
            completion_text=json.dumps(payload, ensure_ascii=False)
        )


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
    async def test_bundled_page_name_index_covers_every_listed_page(self):
        resource = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "shinkaku_page_names.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
        entries = payload["entries"]

        self.assertEqual(payload["source"]["total_pages"], 301)
        self.assertEqual(len(entries), 301)
        self.assertEqual(
            [row["catalog_index"] for row in entries], list(range(1, 302))
        )
        for field in ("title_zh", "title_en", "title_ja", "url"):
            values = [str(row[field]).strip() for row in entries]
            self.assertTrue(all(values), field)
            self.assertEqual(len(values), len(set(values)), field)

        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        primary_owners = {}
        for row in entries:
            for field in ("title_zh", "title_en", "title_ja"):
                self.assertEqual(client._page_name_match_score(row, row[field]), 400)
            for alias in row["primary_aliases"]:
                if str(alias).startswith("http"):
                    continue
                primary_owners.setdefault(_normalise_term(alias), set()).add(row["id"])
        self.assertFalse(
            {
                key: owners
                for key, owners in primary_owners.items()
                if len(owners) > 1
            }
        )

    async def test_full_chinese_and_english_names_resolve_without_search(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        fighter = next(
            row
            for row in client.page_name_entries
            if row["title_ja"] == "ファイター(RBP)"
        )

        async def get_page(title):
            self.assertEqual(title, "ファイター(RBP)")
            return {
                "title": title,
                "url": fighter["url"],
                "sections": [],
            }

        with (
            patch.object(client, "get_page", side_effect=get_page) as get_page_mock,
            patch.object(
                client,
                "search_pages",
                side_effect=AssertionError("exact local names must not use site search"),
            ),
        ):
            chinese = await client.resolve(fighter["title_zh"])
            english = await client.resolve(fighter["title_en"])

        self.assertEqual(chinese["page"]["title"], "ファイター(RBP)")
        self.assertEqual(english["page"]["title"], "ファイター(RBP)")
        self.assertEqual(get_page_mock.await_count, 2)

    async def test_catalog_number_resolves_to_the_bundled_japanese_page(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        entry = client.get_page_name_by_index(88)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(client.lookup_page_names("#88")[0]["catalog_index"], 88)
        with patch.object(
            client,
            "get_page",
            return_value={"title": entry["title_ja"], "sections": []},
        ) as get_page_mock:
            resolved = await client.resolve("编号 88")

        self.assertEqual(resolved["kind"], "page")
        self.assertEqual(resolved["page"]["title"], entry["title_ja"])
        get_page_mock.assert_awaited_once_with(entry["title_ja"])

    async def test_reference_renderer_caches_grouped_pages(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        with TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            entries = client.page_name_entries[:7]
            first = render_shinkaku_reference_pages(
                output_dir,
                entries,
                entries_per_page=3,
                columns=2,
                single_image=False,
            )
            second = render_shinkaku_reference_pages(
                output_dir,
                entries,
                entries_per_page=3,
                columns=2,
                single_image=False,
            )

            self.assertEqual(first, second)
            self.assertEqual(len(first), 2)
            self.assertTrue(all(path.is_file() for path in first))
            with Image.open(first[0]) as image:
                self.assertEqual(image.width, 1840)
                self.assertGreater(image.height, 400)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["entries"], 7)
            self.assertFalse(manifest["single_image"])

    async def test_compact_reference_renderer_fits_all_pages_in_one_safe_image(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        with TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            first = render_shinkaku_reference_pages(
                output_dir, client.page_name_entries, columns=5
            )
            second = render_shinkaku_reference_pages(
                output_dir, client.page_name_entries, columns=5
            )

            self.assertEqual(first, second)
            self.assertEqual(len(first), 1)
            self.assertTrue(first[0].is_file())
            with Image.open(first[0]) as image:
                self.assertEqual(image.width, 2160)
                self.assertLess(image.height, 8000)
                self.assertLess(image.width * image.height, 18_000_000)
            self.assertLess(first[0].stat().st_size, 8 * 1024 * 1024)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["entries"], 301)
            self.assertEqual(manifest["columns"], 5)
            self.assertTrue(manifest["single_image"])

    async def test_ambiguous_short_name_returns_three_language_candidates(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        resolved = await client.resolve("Fighter")

        self.assertEqual(resolved["kind"], "candidates")
        self.assertGreater(len(resolved["candidates"]), 1)
        for row in resolved["candidates"]:
            self.assertTrue(row["title_zh"])
            self.assertTrue(row["title_en"])
            self.assertTrue(row["title_ja"])

        text = KirbyCatalogPlugin._shinkaku_candidate_text(
            resolved["candidates"], "page"
        )
        self.assertIn("English:", text)
        self.assertIn("日本語：", text)
        self.assertIn("格斗家", text)

    async def test_full_japanese_title_outranks_another_pages_short_alias(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)

        exact = client.lookup_page_names(
            "ウィスピーウッズEX", limit=20, exact_only=True
        )
        short = client.lookup_page_names("Whispy Woods EX", limit=20, exact_only=True)

        self.assertEqual([row["game_code"] for row in exact], ["WII"])
        self.assertGreater(len(short), 1)

    async def test_switch_2_edition_pages_are_distinct(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        rows = [
            row
            for row in client.page_name_entries
            if row["game_code"] == "DIS_S2E"
        ]

        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["url"] for row in rows}), 2)
        self.assertEqual(
            {row["title_ja"] for row in rows},
            {"ジェネル・メフィリス", "動画館(Dis・S2E)"},
        )

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

    async def test_page_parser_keeps_lead_text_emphasis_before_first_section(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        source = """
        <div id="main">
          <div id="page-header"><h2>ファイター(RBP)</h2></div>
          <div id="page-body-inner"><div class="user-area">
            <img src="https://image02.seesaawiki.jp/k/u/kirby_shinkaku/71bb18ea61b24d4a.png">
            <br><br>
            <b><span style="color:#ff0000;">身体てき</span> にせい能が</b><br>
            <b>アップする能力。人なみはずれた</b><br>
            <b>強力な<span style="color:#ff0000;">パンチ</span>や<span style="color:red;">キック</span>を</b><br>
            <b>マスターしている。きたえれば</b><br>
            <b>かのうせいは<span style="color:rgb(255, 0, 0);">むげん大</span>である。</b>
            <div class="wiki-section-1">
              <div class="title-1"><h3>技一覧</h3></div>
              <div class="wiki-section-body-1"><p>最初の攻略本文。</p></div>
            </div>
          </div></div>
        </div>
        """.encode("euc_jp")
        page = client._parse_page(
            source,
            "ファイター(RBP)",
            "https://seesaawiki.jp/kirby_shinkaku/d/Fighter_Test",
        )

        self.assertIsNotNone(page)
        self.assertEqual(
            page["summary"].splitlines(),
            [
                "**==身体てき== にせい能が**",
                "**アップする能力。人なみはずれた**",
                "**強力な==パンチ==や==キック==を**",
                "**マスターしている。きたえれば**",
                "**かのうせいは==むげん大==である。**",
            ],
        )
        self.assertEqual(page["lead"]["text"], page["summary"])
        self.assertEqual([row["title"] for row in page["sections"]], ["技一覧"])
        self.assertNotIn("最初の攻略本文", page["summary"])
        self.assertTrue(page["image_url"].endswith("71bb18ea61b24d4a.png"))

        layout = build_card_pages(
            page["summary"],
            KirbyCatalogPlugin._shinkaku_narrative_text(
                {"sections": page["sections"]}
            ),
            page_line_budget=200,
            preserve_source_order=True,
        )[0]
        rendered = Environment(loader=BaseLoader(), autoescape=True).from_string(
            WIKIRBY_CARD_TEMPLATE
        ).render(
            title=page["title"],
            source=page["url"],
            theme=resolve_card_template("卡比粉彩"),
            wiki_name="卡比真格攻略 Wiki",
            reference_label="SHINKAKU BOSS BATTLE GUIDE",
            image_data_uri="",
            **layout,
        )
        self.assertIn('<span class="source-accent">身体てき</span>', rendered)
        self.assertLess(rendered.index("身体てき"), rendered.index("技一覧"))

    async def test_translated_lead_renders_cross_line_emphasis_without_markers(self):
        translated = (
            "==身体性能==\n"
            "**得到提升的能力。\n将超乎寻常的**\n"
            "强力==拳击==与==踢击==\n"
            "**熟练掌握。\n只要经过锻炼，**\n"
            "**可能性就是==无限大==。\n**"
        )
        layout = build_card_pages(
            translated,
            "【招式列表】\n攻略正文。",
            page_line_budget=200,
            preserve_source_order=True,
        )[0]
        rendered = Environment(loader=BaseLoader(), autoescape=True).from_string(
            WIKIRBY_CARD_TEMPLATE
        ).render(
            title="ファイター(RBP)",
            source="https://seesaawiki.jp/kirby_shinkaku/d/Fighter_Test",
            theme=resolve_card_template("星际档案"),
            wiki_name="卡比真格攻略 Wiki",
            reference_label="SHINKAKU BOSS BATTLE GUIDE",
            image_data_uri="",
            **layout,
        )

        hero = rendered.split('<div class="hero-note">', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("**", hero)
        self.assertNotIn("==", hero)
        self.assertIn("<strong>得到提升的能力。\n将超乎寻常的</strong>", hero)
        self.assertIn('<span class="source-accent">无限大</span>', hero)
        self.assertEqual(
            inline_markup_plain(translated),
            "身体性能\n得到提升的能力。\n将超乎寻常的\n"
            "强力拳击与踢击\n熟练掌握。\n只要经过锻炼，\n"
            "可能性就是无限大。\n",
        )

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

    async def test_parser_keeps_bare_text_toggles_rowspans_hierarchy_and_video(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        source = b"""
        <div id="main">
          <div id="page-header"><h2>Fighter Test</h2></div>
          <h3>Techniques</h3>
          <div class="wiki-section-body-1"><br></div>
          <h5>Quick Shot</h5>
          <div class="wiki-section-body-3">
            First line.<br>Second line with <u>important</u> text.<br>
            <div id="content_block_1">
              <div class="toggle-title">Detailed data</div>
              <div class="toggle-display">Hidden first.<br>Hidden second.</div>
            </div>
            <div id="content_block_2">
              <div class="toggle-title">Boss modifiers</div>
              <div class="toggle-display"><table>
                <tr><th>Boss</th><th>Rating</th><th>Time</th></tr>
                <tr><td rowspan="2">Boss A</td><td>A</td><td>10.0</td></tr>
                <tr><td>B</td><td>12.0</td></tr>
              </table></div>
            </div>
          </div>
          <h3>Video</h3>
          <div class="wiki-section-body-1">
            <img src="https://image02.seesaawiki.jp/k/u/kirby_shinkaku/fighter.png">
            <iframe src="https://www.youtube.com/embed/example"></iframe>
          </div>
        </div>
        """
        page = client._parse_page(
            source,
            "Fighter Test",
            "https://seesaawiki.jp/kirby_shinkaku/d/Fighter_Test",
        )

        self.assertEqual(len(page["sections"]), 3)
        self.assertTrue(page["sections"][0]["group_only"])
        quick = page["sections"][1]
        self.assertEqual(quick["context"], "Techniques")
        self.assertIn("First line.", quick["text_without_tables"])
        self.assertIn("Second line with important text.", quick["text_without_tables"])
        self.assertIn("Hidden first.", quick["text_without_tables"])
        self.assertEqual(len(quick["toggles"]), 2)
        self.assertEqual(quick["tables"][0]["title"], "Boss modifiers")
        self.assertEqual(
            [cell["text"] for cell in quick["tables"][0]["rows"][1]],
            ["Boss A", "B", "12.0"],
        )
        self.assertEqual(
            page["media_urls"], ["https://www.youtube.com/embed/example"]
        )
        self.assertIn("image02.seesaawiki.jp", page["image_url"])

    async def test_fighter_modules_keep_nested_skills_emphasis_and_table_anchors(self):
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        source = """
        <div id="main">
          <div id="page-header"><h2>ファイター(RBP)</h2></div>
          <h3>技一覧</h3>
          <div class="wiki-section-body-1">
            <table>
              <tr><th>技名</th><th>威力</th></tr>
              <tr><td>バルカンジャブ</td><td>50</td></tr>
            </table>
            <div id="content_block_1">
              <div class="toggle-title">詳しいデータ</div>
              <div class="toggle-display"><ul>
                <li><strong>バルカンジャブ</strong><ul>
                  <li>実際のコマンドは<em>Bはなす</em></li>
                  <li>発生 5F</li>
                </ul></li>
                <li><strong>スマッシュパンチ</strong><ul>
                  <li>コマンド入力時間 長押し8F</li>
                  <li>発生 5F</li>
                </ul></li>
                <li><strong>あしばらい</strong><ul>
                  <li>実際のコマンドはダッシュ+Bはなす</li>
                  <li>発生 1F</li>
                </ul></li>
              </ul></div>
            </div>
            <div id="content_block_2">
              <div class="toggle-title">ボスダメージ補正</div>
              <div class="toggle-display"><table>
                <tr><th>技名</th><th>基本威力</th></tr>
                <tr><td>バルカンジャブ</td><td>50</td></tr>
              </table></div>
            </div>
          </div>
          <h3>各ボス戦</h3>
          <div class="wiki-section-body-1">
            <div id="content_block_3">
              <div class="toggle-title">タイム評価について</div>
              <div class="toggle-display">
                <p><strong>SS</strong> ＝ 最速</p>
              </div>
            </div>
            <table>
              <tr><th>Boss</th><th>評価</th></tr>
              <tr><td>Re:ウィスピーボーグ</td><td>B</td></tr>
            </table>
          </div>
        </div>
        """.encode("euc_jp")
        page = client._parse_page(
            source,
            "ファイター(RBP)",
            "https://seesaawiki.jp/kirby_shinkaku/d/Fighter_Test",
        )

        details = {"sections": page["sections"]}
        detail_text = KirbyCatalogPlugin._shinkaku_narrative_text(details)
        rich_sections = KirbyCatalogPlugin._shinkaku_rich_sections(details)
        detail_blocks = parse_detail_blocks(detail_text)

        detailed = next(
            block for block in detail_blocks if block["title"] == "詳しいデータ"
        )
        self.assertTrue(detailed["definition_grid"])
        self.assertIn("<strong>バルカンジャブ</strong>", detailed["body_html"])
        self.assertIn("<em>Bはなす</em>", detailed["body_html"])
        self.assertIn("  - 発生 5F", detailed["body"])

        block_order = {
            block["title"]: block["source_order"] for block in detail_blocks
        }
        self.assertEqual(
            [section["source_order"] for section in rich_sections],
            [
                block_order["技一覧"],
                block_order["ボスダメージ補正"],
                block_order["タイム評価について"],
            ],
        )

        layout = build_card_pages(
            page["summary"],
            detail_text,
            rich_sections,
            page_line_budget=2000,
            preserve_source_order=True,
        )[0]
        groups = {group["title"]: group for group in layout["content_flow"]}
        self.assertEqual(
            [row["display_title"] for row in groups["技一覧"]["rich_sections"]],
            ["技一覧（表 1）"],
        )
        self.assertEqual(
            [
                row["display_title"]
                for row in groups["ボスダメージ補正"]["rich_sections"]
            ],
            ["ボスダメージ補正"],
        )
        self.assertEqual(
            [
                row["display_title"]
                for row in groups["タイム評価について"]["rich_sections"]
            ],
            ["各ボス戦"],
        )

        rendered = Environment(loader=BaseLoader(), autoescape=True).from_string(
            WIKIRBY_CARD_TEMPLATE
        ).render(
            title=page["title"],
            source=page["url"],
            theme=resolve_card_template("卡比粉彩"),
            wiki_name="卡比真格攻略 Wiki",
            reference_label="SHINKAKU BOSS BATTLE GUIDE",
            image_data_uri="",
            **layout,
        )
        self.assertNotIn("资料速览", rendered)
        self.assertIn('class="definition-grid"', rendered)
        article = rendered.split('<section class="article-flow">', 1)[1]
        self.assertLess(article.index("技一覧"), article.index("詳しいデータ"))
        self.assertLess(
            article.index("ボスダメージ補正"), article.index("各ボス戦")
        )

    async def test_paginated_detailed_skills_and_technique_rows_stay_atomic(self):
        skills = []
        for index in range(1, 7):
            skills.extend(
                [
                    f"- 招式 {index}",
                    f"  - 指令 {index}：" + "长按 B 后释放。" * 10,
                    f"  - 发生 {index}F",
                    f"  - 修正时间 {index * 10}F",
                ]
            )
        rich_sections = [
            {
                "kind": "techniques",
                "title": "操作表",
                "source_order": 2,
                "groups": [
                    {
                        "label": "平台操作",
                        "rows": [
                            {
                                "move": f"操作招式 {index}",
                                "controls": "B",
                                "description": "完整说明。" * 30,
                                "damage": str(index * 10),
                            }
                            for index in range(1, 5)
                        ],
                    }
                ],
            }
        ]
        pages = build_card_pages(
            "简介",
            "【详细数据】\n" + "\n".join(skills) + "\n【操作表】\n操作说明。",
            rich_sections,
            page_line_budget=60,
            force_paginate=True,
            preserve_source_order=True,
        )

        bodies = [
            str(group.get("body") or "")
            for page in pages
            for group in page["content_flow"]
        ]
        for index in range(1, 7):
            matching = [body for body in bodies if f"- 招式 {index}" in body]
            self.assertEqual(len(matching), 1)
            self.assertIn(f"  - 指令 {index}：", matching[0])
            self.assertIn(f"  - 发生 {index}F", matching[0])
            owner = next(
                group
                for page in pages
                for group in page["content_flow"]
                if f"- 招式 {index}" in str(group.get("body") or "")
            )
            self.assertTrue(owner["definition_grid"])

        technique_moves = [
            row["move"]
            for page in pages
            for group in page["content_flow"]
            for section in group.get("rich_sections", [])
            if section.get("kind") == "techniques"
            for technique_group in section.get("groups", [])
            for row in technique_group.get("rows", [])
        ]
        self.assertEqual(
            technique_moves,
            [f"操作招式 {index}" for index in range(1, 5)],
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
        self.assertIn("\u3010\u884c\u52d5\u30d1\u30bf\u30fc\u30f3\u3011", results[0][0].text)

    async def test_wiki_case_and_section_alias_are_parsed(self):
        plugin = self.make_plugin()
        query, mode, section, output_override = plugin._shinkaku_query_parts(
            FakeEvent("\u5361\u6bd4\u771f\u683cwiki\u7ae0\u8282 Magolor EX")
        )

        self.assertEqual(query, "Magolor EX")
        self.assertEqual(mode, "sections")
        self.assertEqual(section, "")
        self.assertEqual(output_override, "")

    async def test_explicit_text_card_and_document_modes_are_parsed(self):
        plugin = self.make_plugin()

        text_parts = plugin._shinkaku_query_parts(
            FakeEvent("\u5361\u6bd4\u771f\u683c\u6587\u672c Fighter")
        )
        card_parts = plugin._shinkaku_query_parts(
            FakeEvent("\u5361\u6bd4\u771f\u683c\u5361\u7247 Fighter")
        )
        document_parts = plugin._shinkaku_query_parts(
            FakeEvent("\u5361\u6bd4\u771f\u683c\u6587\u6863 Fighter")
        )

        self.assertEqual(text_parts, ("Fighter", "page", "", "text"))
        self.assertEqual(card_parts, ("Fighter", "page", "", "card"))
        self.assertEqual(document_parts, ("Fighter", "page", "", "document"))

    async def test_document_command_number_uses_shinkaku_catalog_index(self):
        plugin = self.make_plugin()
        plugin.shinkaku = KirbyShinkakuClient(cache_ttl_seconds=0)
        entry = plugin.shinkaku.get_page_name_by_index(88)
        assert entry is not None

        query, mode, section, output_override = plugin._shinkaku_query_parts(
            FakeEvent("卡比真格文档 #88")
        )

        self.assertEqual(query, entry["title_ja"])
        self.assertEqual(mode, "page")
        self.assertEqual(section, "")
        self.assertEqual(output_override, "document")

    async def test_reference_aliases_use_reference_mode_without_a_query(self):
        plugin = self.make_plugin()
        for command in ("卡比真格速查", "卡比真格速查图", "卡比真格名称表"):
            with self.subTest(command=command):
                query, mode, section, output_override = plugin._shinkaku_query_parts(
                    FakeEvent(command)
                )
                self.assertEqual(query, "")
                self.assertEqual(mode, "reference")
                self.assertEqual(section, "")
                self.assertEqual(output_override, "")

    async def test_reference_command_renders_all_indexed_names(self):
        with TemporaryDirectory() as temporary:
            plugin = self.make_plugin()
            plugin.shinkaku = KirbyShinkakuClient(cache_ttl_seconds=0)
            plugin.store = SimpleNamespace(root=Path(temporary))
            title, components = await plugin._shinkaku_reference_components()

            self.assertIn("中文 / 日本語", title)
            self.assertIsInstance(components[0], Comp.Plain)
            self.assertEqual(
                sum(isinstance(component, Comp.Image) for component in components),
                1,
            )

    async def test_candidate_output_mode_supports_plain_and_forward_messages(self):
        plugin = self.make_plugin()
        client = KirbyShinkakuClient(cache_ttl_seconds=0)
        resolved = await client.resolve("Fighter")
        candidates = resolved["candidates"]

        plugin.config["shinkaku_candidate_output_mode"] = "普通消息"
        plain = plugin._shinkaku_candidate_components(candidates, "page")
        self.assertEqual(len(plain), 1)
        self.assertIsInstance(plain[0], Comp.Plain)
        self.assertIn("Fighter (Kirby: Planet Robobot)", plain[0].text)

        plugin.config["shinkaku_candidate_output_mode"] = "合并转发"
        forwarded = plugin._shinkaku_candidate_components(candidates, "page")
        self.assertTrue(forwarded)
        self.assertTrue(
            all(isinstance(component, Comp.Nodes) for component in forwarded)
        )
        nodes = [node for component in forwarded for node in component.nodes]
        self.assertEqual(len(nodes), len(candidates) + 2)
        self.assertIn("找到多个可能", nodes[0].content[0].text)
        self.assertIn("English:", nodes[1].content[0].text)
        self.assertIn("例如：卡比真格", nodes[-1].content[0].text)

    async def test_ambiguous_command_uses_configured_forward_output(self):
        plugin = self.make_plugin()
        plugin.config["shinkaku_candidate_output_mode"] = "合并转发"
        plugin.shinkaku = KirbyShinkakuClient(cache_ttl_seconds=0)

        results = [
            result
            async for result in plugin._shinkaku_query_impl(
                FakeEvent("卡比真格 Fighter")
            )
        ]

        self.assertEqual(len(results), 1)
        self.assertTrue(
            all(isinstance(component, Comp.Nodes) for component in results[0])
        )

    async def test_document_command_returns_generated_html_file(self):
        with TemporaryDirectory() as temporary:
            plugin = self.make_plugin()
            plugin.config["wiki_document_translate_enabled"] = False
            plugin.store = SimpleNamespace(root=Path(temporary))

            results = [
                result
                async for result in plugin._shinkaku_query_impl(
                    FakeEvent("卡比真格文档 Magolor EX")
                )
            ]

            file_component = next(
                component
                for component in results[0]
                if isinstance(component, Comp.File)
            )
            document = Path(file_component.file)
            self.assertTrue(document.exists())
            self.assertIn("\u884c\u52d5\u30d1\u30bf\u30fc\u30f3", document.read_text(encoding="utf-8"))

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

    async def test_page_name_command_uses_static_index_without_network(self):
        plugin = self.make_plugin()
        plugin.shinkaku = KirbyShinkakuClient(cache_ttl_seconds=0)

        with patch.object(
            plugin.shinkaku,
            "lookup_terms",
            side_effect=AssertionError("a full page name must stay local"),
        ):
            result = await plugin._shinkaku_terms_text(
                "Fighter (Kirby: Planet Robobot)"
            )

        self.assertIn("格斗家（星之卡比 机器人星球）", result)
        self.assertIn("Fighter (Kirby: Planet Robobot)", result)
        self.assertIn("ファイター(RBP)", result)
        self.assertIn("全部 301 页", result)

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

    async def test_long_table_translation_is_batched_without_losing_rows(self):
        plugin = self.make_plugin()
        plugin.config.update(
            {
                "shinkaku_translate_enabled": True,
                "wiki_translation_chunk_chars": 1000,
            }
        )
        plugin.context = BatchTranslationContext()
        source = [
            {
                "kind": "table",
                "title": "技一覧",
                "headers": ["技名", "説明", "評価"],
                "rows": [
                    [
                        f"技 {index}",
                        "長い攻略説明 " * 35,
                        "SS",
                    ]
                    for index in range(14)
                ],
            }
        ]

        translated = await plugin._shinkaku_translate_rich_sections(
            FakeEvent(""), source
        )

        self.assertGreater(len(plugin.context.calls), 1)
        self.assertEqual(len(translated[0]["rows"]), 14)
        self.assertTrue(
            all(row[0].startswith("译文：") for row in translated[0]["rows"])
        )
        self.assertTrue(all(row[2] == "SS" for row in translated[0]["rows"]))

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
