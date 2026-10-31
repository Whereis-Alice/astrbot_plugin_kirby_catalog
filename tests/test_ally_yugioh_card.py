"""Yu-Gi-Oh family routing, independent stars, actual facts and print limits."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_kirby_catalog import ally_draw_card as cards
from astrbot_plugin_kirby_catalog.ally_yugioh_card import yugioh_ally_card_data
from astrbot_plugin_kirby_catalog.yugioh_template import (
    RenderOptions,
    render_card,
    renderer,
)
from astrbot_plugin_kirby_catalog.yugioh_template.fonts import (
    card_name_artwork_metrics,
    load_card_font,
)
from PIL import Image, ImageDraw


class YugiohAllyCardTests(unittest.TestCase):
    @staticmethod
    def payload(**overrides):
        entry = {
            "id": 854,
            "name": "魔法洛亚",
            "name_en": "Magolor",
            "source": "星之卡比 Wii",
        }
        return cards.build_ally_draw_card_data(
            entry,
            nickname="狐狸",
            date="2026-10-31",
            remaining=4,
            description="魔法洛亚是一只猫咪般的外星魔法师。",
            theme=cards.YUGIOH_DRAW_CARD_THEME,
            **overrides,
        )

    def test_families_do_not_mix_and_legacy_kirby_auto_still_works(self):
        with patch.object(
            cards.random.SystemRandom, "choice", return_value="毛线物语"
        ) as choice:
            self.assertEqual(
                cards.select_ally_draw_card_theme(
                    cards.YUGIOH_DRAW_CARD_THEME, ["毛线物语"]
                ),
                cards.YUGIOH_DRAW_CARD_THEME,
            )
            choice.assert_not_called()
            self.assertEqual(
                cards.select_ally_draw_card_theme(
                    cards.ALLY_DRAW_CARD_RANDOM_THEME,
                    [cards.YUGIOH_DRAW_CARD_THEME, "毛线物语", "星海航记"],
                ),
                "毛线物语",
            )
            choice.assert_called_once_with(["毛线物语", "星海航记"])
            self.assertEqual(
                cards.select_ally_draw_card_theme("随机主题（自动）", ["星海航记"]),
                "星海航记",
            )

    def test_print_stock_and_stars_are_chosen_separately(self):
        with patch.object(cards.random, "SystemRandom") as random_class:
            random_class.return_value.choice.return_value = "C"
            random_class.return_value.randint.return_value = 12
            payload = self.payload()
        random_class.return_value.choice.assert_called_once_with(
            cards.YUGIOH_DRAW_CARD_EDITIONS
        )
        random_class.return_value.randint.assert_called_once_with(1, 12)
        self.assertEqual(
            (payload["yugioh_edition"], payload["yugioh_stars"]), ("C", 12)
        )
        self.assertEqual((payload["catalog_id"], payload["remaining"]), (854, 4))

    def test_variant_suffix_is_preserved_as_part_of_the_printed_name(self):
        data = cards.build_ally_draw_card_data(
            {"id": 854, "name": "魔法洛亚（EX）", "name_en": "Magolor EX"},
            nickname="狐狸",
            date="2026-10-31",
            remaining=4,
            theme=cards.YUGIOH_DRAW_CARD_THEME,
        )
        self.assertEqual(data["name"], "魔法洛亚（EX）")
        self.assertEqual(data["english_name"], "Magolor EX")

    def test_yugioh_visual_randomness_leaves_character_random_state_unchanged(self):
        state = random.getstate()
        for _ in range(12):
            payload = self.payload()
            self.assertIn(payload["yugioh_edition"], cards.YUGIOH_DRAW_CARD_EDITIONS)
            self.assertTrue(1 <= payload["yugioh_stars"] <= 12)
        self.assertEqual(random.getstate(), state)

    def test_card_maps_existing_facts_without_invented_combat_values(self):
        payload = self.payload()
        model = yugioh_ally_card_data(payload)
        self.assertEqual(
            (model.title, model.rail_left, model.footer_right),
            ("魔法洛亚", "Magolor", "#0854"),
        )
        self.assertEqual(model.rail_right, "")
        self.assertIn("星之卡比 Wii", model.footer_left)
        self.assertEqual(model.description, payload["description"])
        self.assertEqual(model.descriptor_parts, ("简介",))
        self.assertEqual((model.attack, model.defense, model.total), ("", "", ""))
        self.assertEqual((model.small_stats, model.relations), ((), ()))

    def test_five_stocks_render_fixed_size_and_explicit_star_counts(self):
        source = BytesIO()
        Image.new("RGBA", (45, 110), (245, 80, 150, 255)).save(source, format="PNG")
        payload = self.payload(image_bytes=source.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            for edition, stars in zip(
                cards.YUGIOH_DRAW_CARD_EDITIONS, (12, 1, 8, 2, 7)
            ):
                with (
                    self.subTest(edition=edition),
                    patch.object(
                        renderer, "render_level_star", wraps=renderer.render_level_star
                    ) as star,
                ):
                    data = dict(payload, yugioh_edition=edition, yugioh_stars=stars)
                    path = cards.render_ally_draw_card(
                        data, Path(directory) / f"{edition}.png"
                    )
                    with Image.open(path) as image:
                        self.assertEqual(image.size, (1080, 1680))
                        self.assertEqual(image.mode, "RGB")
                    self.assertEqual(star.call_count, stars)
                    self.assertLess(path.stat().st_size, 4 * 1024 * 1024)
            self.assertFalse(list(Path(directory).glob("*-tmp-*.png")))
        self.assertEqual(payload["artwork"], source.getvalue())

    def test_long_profile_is_marked_as_excerpt_instead_of_overlapping_footer(self):
        payload = self.payload()
        payload["description"] = "魔法洛亚是一只猫咪般的外星魔法师。" * 100
        image = render_card(
            yugioh_ally_card_data(payload), RenderOptions(description_max_lines=10)
        )
        self.assertEqual(image.size, (1080, 1680))
        self.assertTrue(
            any(item.startswith("description:") for item in image.info["overflow"])
        )
        with patch.object(renderer, "_text", wraps=renderer._text) as text:
            render_card(
                yugioh_ally_card_data(payload), RenderOptions(description_max_lines=10)
            )
        paragraphs = [
            call
            for call in text.call_args_list
            if call.kwargs.get("font") is not None
            and call.args[2] >= 1180
            and call.args[4] == 28
        ]
        self.assertLessEqual(len(paragraphs), 10)
        self.assertTrue(any(str(call.args[3]).endswith("…") for call in paragraphs))
        self.assertTrue(all(call.args[2] < 1583 for call in paragraphs))

    def test_empty_profile_hides_description_and_bad_art_is_bounded(self):
        payload = self.payload(image_bytes=b"not an image")
        payload["description"] = ""
        model = yugioh_ally_card_data(payload)
        self.assertEqual(model.descriptor_parts, ())
        self.assertEqual(render_card(model).size, (1080, 1680))

    def test_prepared_titles_cover_the_existing_catalogue_as_complete_names(self):
        root = Path(__file__).parents[1]
        profiles = json.loads(
            (root / "resources/catalog_profiles.json").read_text(encoding="utf-8")
        )["items"]
        for profile in profiles.values():
            name = profile.get("name_zh") or profile["name_en"]
            self.assertIsNotNone(card_name_artwork_metrics(name, 66), name)
        self.assertIsNone(card_name_artwork_metrics("管理员新增的原创盟友", 66))

    def test_printed_profile_keeps_latin_words_and_marks_the_excerpt(self):
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        face = load_card_font(28)
        text = "星之卡比 Wii Kirby's Return to Dream Land 的故事。"
        lines, clipped = renderer._wrap(draw, text, face, 200, 10)
        self.assertFalse(clipped)
        self.assertIn("Kirby's", "\n".join(lines))
        self.assertTrue(all(draw.textlength(line, font=face) <= 200 for line in lines))
        lines, clipped = renderer._wrap(draw, text, face, 200, 2)
        self.assertTrue(clipped)
        self.assertTrue(lines[-1].endswith("…"))

    def test_longest_builtin_name_fits_all_stocks_as_a_complete_title(self):
        root = Path(__file__).parents[1]
        profiles = json.loads(
            (root / "resources/catalog_profiles.json").read_text(encoding="utf-8")
        )["items"]
        names = [
            profile.get("name_zh") or profile["name_en"]
            for profile in profiles.values()
        ]
        name = max(names, key=lambda value: card_name_artwork_metrics(value, 66)[0])
        self.assertGreater(card_name_artwork_metrics(name, 26)[0], 748)
        model = yugioh_ally_card_data(dict(self.payload(), name=name))
        for edition in cards.YUGIOH_DRAW_CARD_EDITIONS:
            with self.subTest(edition=edition):
                image = render_card(replace(model, edition=edition))
                self.assertEqual(image.size, (1080, 1680))
                self.assertFalse(
                    any("title" in item for item in image.info["overflow"])
                )
