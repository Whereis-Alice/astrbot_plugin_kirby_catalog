import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_kirby_catalog.tools.build_catalog_profiles import (
    GoogleTranslator,
    PageDraft,
    TermProtector,
    contains_english_prose,
    draft_translation_looks_incomplete,
    first_quote,
    lead_region,
    normalise_chinese_spacing,
    plain_markup,
    translate_draft_fields,
)


class StubTranslator(GoogleTranslator):
    def request(self, value: str) -> str:
        return value.replace("Kirby", "卡比").replace("hero", "英雄")


class CatalogProfileBuilderTests(unittest.TestCase):
    def test_normalises_spaces_around_chinese_without_changing_english_names(self):
        self.assertEqual(
            normalise_chinese_spacing(
                "帝帝帝大王 住在 Dream Land ，并遇见 Meta Knight 。"
            ),
            "帝帝帝大王住在Dream Land，并遇见Meta Knight。",
        )

    def test_extracts_quote_and_only_page_lead(self):
        wikitext = (
            "{{Quote|Please disappear forever!|Hyness, ''Kirby Star Allies''}}\n"
            "'''Hyness''' is the principal villain in [[Kirby Star Allies]].\n\n"
            "He leads the Three Mage-Sisters.\n"
            "==Game appearances==\n"
            "This section must not enter the introduction."
        )
        protector = TermProtector(
            {
                "kirbystarallies": "星之卡比 新星同盟（Kirby Star Allies）",
                "hyness": "海内司（Hyness）",
            },
            {
                "Kirby Star Allies": "星之卡比 新星同盟（Kirby Star Allies）",
                "Hyness": "海内司（Hyness）",
            },
        )

        quote, attribution = first_quote(wikitext)
        self.assertEqual(quote, "Please disappear forever!")
        self.assertIn("Hyness", attribution)
        lead = protector.restore(plain_markup(lead_region(wikitext), protector))
        self.assertIn("海内司（Hyness）", lead)
        self.assertIn("星之卡比 新星同盟（Kirby Star Allies）", lead)
        self.assertNotIn("This section must not enter", lead)

    def test_long_payload_is_reassembled_and_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "translations.json"
            translator = StubTranslator(
                cache_path,
                engine="bing",
                batch_size=3,
                request_delay=0,
            )
            source = (
                "ZXQ900001QXZ"
                + " ".join("Kirby is a hero." for _ in range(130))
                + "ZXQ900001QXZZXQ900002QXZ"
            )

            translated = translator.translate_batch([source])[0]
            translator.save()

            self.assertIn("卡比 is a 英雄", translated)
            self.assertEqual(translated.count("ZXQ900001QXZ"), 2)
            self.assertEqual(translated.count("ZXQ900002QXZ"), 1)
            self.assertEqual(translator.lookup(source), translated)
            self.assertTrue(cache_path.is_file())

    def test_field_fallback_recovers_a_changed_combined_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            translator = StubTranslator(
                Path(temp) / "translations.json",
                engine="bing",
                batch_size=3,
                request_delay=0,
            )
            draft = PageDraft(
                pageid=1,
                page_title="Kirby",
                source_url="https://wikirby.com/wiki/Kirby",
                source_revision=1,
                source_timestamp="2026-08-05T00:00:00Z",
                summary_en="Kirby is a hero.\n\nKirby protects Dream Land.",
                quote_en="Kirby is here!",
                quote_attribution_en="Kirby",
                protector=TermProtector({}, {}),
            )
            broken = draft.translation_payload().replace(
                "ZXQ900001QXZ", "changed-marker", 1
            )

            with self.assertRaisesRegex(ValueError, "field marker"):
                draft.apply_translation(broken)

            translate_draft_fields(translator, draft)

            self.assertEqual(draft.quote_zh, "卡比is here!")
            self.assertEqual(draft.quote_attribution_zh, "卡比")
            self.assertEqual(
                draft.summary_zh,
                "卡比is a英雄.\n\n卡比protects Dream Land.",
            )
            cached = translator.lookup(draft.translation_payload())
            self.assertTrue(cached)
            replay = PageDraft(**{**draft.__dict__, "quote_zh": "", "summary_zh": ""})
            replay.apply_translation(cached)
            self.assertEqual(replay.summary_zh, draft.summary_zh)

    def test_detects_an_english_dominant_translation(self):
        draft = PageDraft(
            pageid=1,
            page_title="Wizzer",
            source_url="https://wikirby.com/wiki/Wizzer",
            source_revision=1,
            source_timestamp="2026-08-05T00:00:00Z",
            summary_en="",
            quote_en="",
            quote_attribution_en="",
            protector=TermProtector({}, {}),
            summary_zh=(
                "Wizzer是一个敌人。It sits in one place with its shell closed "
                "most of the time, but it opens the shell to attack Kirby "
                "with a long beam when the hero approaches the platform."
            ),
        )

        self.assertTrue(draft_translation_looks_incomplete(draft))
        draft.summary_zh = "Wizzer是一个敌人。它通常待在原地，并会打开外壳攻击卡比。"
        self.assertFalse(draft_translation_looks_incomplete(draft))

    def test_official_english_names_do_not_look_like_untranslated_prose(self):
        description = (
            "Monsieur Goan是一名厨师怪物，出现在A Spice Odyssey中。"
            "他被Night Mare Enterprises派往Cappy Town，并使用Toxic Atomic Curry。"
            "房间使用the Chimney, the Totem Pole, the Outdoor Bath and the Torch。"
            "相关小说为Kirby and the Search for the Dreamy Gears!。"
        )

        self.assertFalse(contains_english_prose(description))


if __name__ == "__main__":
    unittest.main()
