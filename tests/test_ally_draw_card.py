"""Daily-card regressions for real image formats, bounded output and draw data."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_kirby_catalog import ally_draw_card as card_module
from astrbot_plugin_kirby_catalog.ally_draw_card import (
    ALLY_DRAW_CARD_RANDOM_THEME,
    ALLY_DRAW_CARD_THEMES,
    CARD_HEIGHT,
    CARD_WIDTH,
    MAX_CARD_HEIGHT,
    build_ally_draw_card_data,
    render_ally_draw_card,
    select_ally_draw_card_theme,
)
from PIL import Image, ImageDraw


def encoded_image(image: Image.Image, format_name: str = "PNG") -> bytes:
    stream = BytesIO()
    image.save(stream, format=format_name)
    return stream.getvalue()


class AllyCardThemeSelectionTests(unittest.TestCase):
    def test_fixed_theme_ignores_random_pool(self):
        with patch.object(card_module.random.SystemRandom, "choice") as choice:
            for theme in ALLY_DRAW_CARD_THEMES:
                self.assertEqual(
                    select_ally_draw_card_theme(theme, ["毛线物语"]), theme
                )
            for invalid in (None, "", "未知主题"):
                self.assertEqual(select_ally_draw_card_theme(invalid), "星愿收藏")
        choice.assert_not_called()

    def test_random_pool_filters_invalid_values_and_duplicate_weights(self):
        candidates = [" 梦泉琉璃 ", "毛线物语", "梦泉琉璃", "未知", None, [], 7]
        with patch.object(
            card_module.random.SystemRandom, "choice", return_value="毛线物语"
        ) as choice:
            result = select_ally_draw_card_theme(
                ALLY_DRAW_CARD_RANDOM_THEME, candidates
            )
        self.assertEqual(result, "毛线物语")
        choice.assert_called_once_with(["梦泉琉璃", "毛线物语"])
        self.assertEqual(candidates[0], " 梦泉琉璃 ")

    def test_empty_or_invalid_pool_uses_all_themes(self):
        for candidates in (None, [], ["未知", None], "毛线物语", {"毛线物语": True}):
            with (
                self.subTest(candidates=candidates),
                patch.object(
                    card_module.random.SystemRandom, "choice", return_value="星海航记"
                ) as choice,
            ):
                self.assertEqual(
                    select_ally_draw_card_theme(
                        ALLY_DRAW_CARD_RANDOM_THEME, candidates
                    ),
                    "星海航记",
                )
                choice.assert_called_once_with(list(ALLY_DRAW_CARD_THEMES))

    def test_one_selected_theme_and_auto_aliases(self):
        with patch.object(card_module.random.SystemRandom, "choice") as choice:
            for mode in (ALLY_DRAW_CARD_RANDOM_THEME, "随机", "自动"):
                self.assertEqual(
                    select_ally_draw_card_theme(mode, ("毛线物语", "毛线物语")),
                    "毛线物语",
                )
        choice.assert_not_called()

    def test_auto_only_returns_selected_styles_without_changing_draw_random_state(self):
        state = random.getstate()
        selected = ["梦泉琉璃", "波普漫画"]
        try:
            for _ in range(20):
                self.assertIn(
                    select_ally_draw_card_theme(ALLY_DRAW_CARD_RANDOM_THEME, selected),
                    selected,
                )
            self.assertEqual(random.getstate(), state)
        finally:
            random.setstate(state)

    def test_dashboard_schema_exposes_the_same_selectable_themes(self):
        schema = json.loads(
            (Path(__file__).parents[1] / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )

        def find(key, value):
            if isinstance(value, dict):
                if key in value:
                    return value[key]
                for child in value.values():
                    found = find(key, child)
                    if found is not None:
                        return found
            return None

        theme = find("ally_draw_card_theme", schema)
        pool = find("ally_draw_card_random_themes", schema)
        self.assertEqual(
            theme["options"], [*ALLY_DRAW_CARD_THEMES, ALLY_DRAW_CARD_RANDOM_THEME]
        )
        self.assertEqual(theme["default"], "星愿收藏")
        self.assertEqual(pool["type"], "list")
        self.assertEqual(pool["render_type"], "checkbox")
        self.assertEqual(pool["options"], list(ALLY_DRAW_CARD_THEMES))
        self.assertEqual(pool["default"], list(ALLY_DRAW_CARD_THEMES))


class AllyDrawCardTests(unittest.TestCase):
    @staticmethod
    def card_data(**overrides):
        return build_ally_draw_card_data(
            {"id": 17, "name": "卡比（Kirby）", "source": "星之卡比"},
            nickname="旅行者",
            date="2026-10-28",
            remaining=2,
            **overrides,
        )

    def test_transparent_png_trims_margin_without_cutting_subject(self):
        source = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.rectangle((25, 20, 94, 79), fill=(241, 128, 179, 255))
        draw.rectangle((25, 42, 28, 46), fill=(31, 30, 58, 255))
        draw.rectangle((91, 42, 94, 46), fill=(31, 30, 58, 255))
        data = self.card_data(image_bytes=encoded_image(source))
        with Image.open(BytesIO(data["artwork"])) as cropped:
            self.assertEqual(cropped.size, (70, 60))
            self.assertEqual(cropped.getpixel((0, 23)), (31, 30, 58, 255))
            self.assertEqual(cropped.getpixel((69, 23)), (31, 30, 58, 255))
            self.assertEqual(cropped.getchannel("A").getbbox(), (0, 0, 70, 60))

    def test_white_backed_art_keeps_white_eyes_inside_colored_outline(self):
        source = Image.new("RGB", (96, 96), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((16, 16, 79, 79), fill=(45, 62, 95))
        draw.rectangle((21, 21, 74, 74), fill=(246, 162, 191))
        draw.rectangle((28, 29, 40, 49), fill="white", outline=(45, 62, 95), width=2)
        draw.rectangle((55, 29, 67, 49), fill="white", outline=(45, 62, 95), width=2)
        data = self.card_data(image_bytes=encoded_image(source))
        with Image.open(BytesIO(data["artwork"])) as cropped:
            self.assertEqual(cropped.size, (64, 64))
            self.assertEqual(cropped.getpixel((18, 22)), (255, 255, 255, 255))
            self.assertEqual(cropped.getpixel((45, 22)), (255, 255, 255, 255))
            self.assertEqual(cropped.getpixel((0, 0)), (45, 62, 95, 255))

    def test_animated_gif_uses_a_complete_first_frame(self):
        first = Image.new("RGB", (80, 60), (236, 114, 170))
        second = Image.new("RGB", (80, 60), (78, 184, 96))
        stream = BytesIO()
        first.save(
            stream,
            format="GIF",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        data = self.card_data(image_bytes=stream.getvalue())
        with Image.open(BytesIO(data["artwork"])) as frozen:
            self.assertEqual(frozen.format, "PNG")
            self.assertEqual(frozen.size, (80, 60))
            self.assertEqual(frozen.getpixel((40, 30)), (236, 114, 170, 255))
            self.assertFalse(getattr(frozen, "is_animated", False))

    def test_each_theme_renders_long_fields_into_bounded_readable_image(self):
        source = encoded_image(Image.new("RGBA", (800, 250), (240, 146, 187, 255)))
        entry = {
            "id": 1353,
            "name": "银河漫游者的特别纪念形态" * 30
            + "（Very Long Ally Name " * 12
            + ")",
            "name_en": "A Very Long English Ally Name " * 40,
            "source": "星之卡比特别纪念作品与远方银河的故事" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            for theme in ALLY_DRAW_CARD_THEMES:
                with self.subTest(theme=theme):
                    data = build_ally_draw_card_data(
                        entry,
                        nickname="名字非常长的旅行者" * 50,
                        date="2026-10-28",
                        remaining=999,
                        description="一段很长的简介与收藏说明。" * 150,
                        image_bytes=source,
                        theme=theme,
                        wiki_hint="引用这条消息查看百科与简介。" * 30,
                    )
                    output = Path(directory) / (theme + ".png")
                    result = render_ally_draw_card(data, output)
                    self.assertEqual(result, output)
                    with Image.open(output) as rendered:
                        rendered.load()
                        self.assertEqual(rendered.format, "PNG")
                        self.assertEqual(rendered.width, CARD_WIDTH)
                        self.assertGreater(rendered.height, CARD_HEIGHT)
                        self.assertLessEqual(rendered.height, MAX_CARD_HEIGHT)
                        self.assertEqual(rendered.mode, "RGB")
                    self.assertLess(output.stat().st_size, 4 * 1024 * 1024)
            self.assertEqual(list(Path(directory).glob("*-tmp-*.png")), [])

    def test_metadata_uses_real_catalog_number_remaining_and_draw_state(self):
        states = (
            ({}, "初次相遇"),
            ({"repeated": True}, "再度同行"),
            ({"pity": True}, "保底邂逅"),
            ({"all_unlocked": True, "repeated": True}, "图鉴已集齐"),
            ({"existing_today": True}, "今日已相遇"),
        )
        for flags, expected in states:
            with self.subTest(flags=flags):
                data = self.card_data(**flags)
                self.assertEqual(data["name"], "卡比")
                self.assertEqual(data["english_name"], "Kirby")
                self.assertEqual(data["catalog_id"], 17)
                self.assertEqual(data["remaining"], 2)
                self.assertEqual(data["date"], "2026.10.28")
                self.assertEqual(data["status"], expected)

    def test_missing_or_unreadable_artwork_still_renders_a_complete_card(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, raw in enumerate((None, b"not an image")):
                with self.subTest(raw=raw):
                    data = self.card_data(image_bytes=raw, theme="invalid-theme")
                    self.assertIsNone(data["artwork"])
                    self.assertEqual(data["theme"], ALLY_DRAW_CARD_THEMES[0])
                    output = Path(directory) / "nested" / f"missing-{index}.png"
                    render_ally_draw_card(data, output)
                    with Image.open(output) as rendered:
                        rendered.load()
                        self.assertEqual(rendered.size, (CARD_WIDTH, CARD_HEIGHT))
                    self.assertGreater(output.stat().st_size, 1000)

    def test_card_intro_skips_leading_quote_and_preserves_real_description(self):
        data = self.card_data(
            description="“Hiii！”\n— 卡比（Kirby）\n卡比（Kirby）是星之卡比系列（Kirby series）的主角。"
        )
        self.assertEqual(data["description"], "卡比是星之卡比系列的主角。")
        quoted = self.card_data(description="“今天也要开心！”")
        self.assertEqual(quoted["description"], "“今天也要开心！”")

    def test_missing_cjk_fonts_reports_failure_instead_of_rendering_square_glyphs(self):
        card_module._font.cache_clear()
        try:
            with (
                patch.object(Path, "is_file", return_value=False),
                self.assertRaisesRegex(OSError, "中文字体"),
            ):
                card_module._font(24)
        finally:
            card_module._font.cache_clear()

    def test_archive_wrap_retains_text_after_500_chars_and_latin_words(self):
        description = (
            "这是已有档案中的一段资料。" * 45
        ) + "\n\nParasol Waddle Doo可以撑着阳伞漂浮。"
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        font = card_module._font(26)
        lines = card_module._lines(draw, description, font, 832, 40)
        self.assertIn("Parasol Waddle Doo", "\n".join(lines))
        self.assertIn("", lines)
        self.assertEqual(
            "".join(lines).replace(" ", ""),
            description.replace("\n", "").replace(" ", ""),
        )
        for line in lines:
            self.assertLessEqual(draw.textlength(line, font=font), 832)

    def test_card_grows_with_archive_and_marks_safety_truncation(self):
        short = self.card_data(description="卡比是星之卡比系列的主角。")
        long = self.card_data(description="卡比是星之卡比系列的主角。" * 50)
        bounded = self.card_data(
            description="卡比是星之卡比系列的主角。" * 1000, description_max_chars=0
        )
        self.assertEqual(long["description"].count("系列的主角。"), 50)
        self.assertTrue(bounded["description"].endswith("…"))
        with tempfile.TemporaryDirectory() as directory:
            sizes = []
            for index, data in enumerate((short, long, bounded)):
                result = render_ally_draw_card(
                    data, Path(directory) / f"archive-{index}.png"
                )
                with Image.open(result) as image:
                    sizes.append(image.height)
                    self.assertLessEqual(image.height, MAX_CARD_HEIGHT)
            self.assertEqual(sizes[0], CARD_HEIGHT)
            self.assertGreater(sizes[1], sizes[0])
            self.assertGreaterEqual(sizes[2], sizes[1])

    def test_pure_card_pixels_are_independent_of_collector_date_and_draw_state(self):
        first = self.card_data(description="卡比是星之卡比系列的主角。")
        second = dict(
            first,
            nickname="不同收藏者",
            date="2099.12.31",
            remaining=99,
            collected=999,
            total=2000,
            status="保底邂逅",
            status_note="不得出现的抽取状态",
            wiki_hint="不得出现的操作提示",
        )
        with tempfile.TemporaryDirectory() as directory:
            for theme in ALLY_DRAW_CARD_THEMES:
                with self.subTest(theme=theme):
                    one = render_ally_draw_card(
                        dict(first, theme=theme), Path(directory) / "one.png"
                    )
                    two = render_ally_draw_card(
                        dict(second, theme=theme), Path(directory) / "two.png"
                    )
                    self.assertEqual(one.read_bytes(), two.read_bytes())

    def test_small_artwork_is_enlarged_without_cropping_or_changing_aspect_ratio(self):
        source = Image.new("RGBA", (121, 120), (240, 80, 120, 255))
        fitted = card_module._fit_artwork(source, (816, 720))
        self.assertEqual(fitted.size, (726, 720))
        self.assertEqual(fitted.getpixel((0, 0)), source.getpixel((0, 0)))
        for dimensions in ((90, 240), (240, 90)):
            with self.subTest(dimensions=dimensions):
                art = Image.new("RGBA", dimensions, (50, 100, 200, 255))
                fitted = card_module._fit_artwork(art, (816, 720))
                self.assertLessEqual(fitted.width, 816)
                self.assertLessEqual(fitted.height, 720)
                self.assertAlmostEqual(
                    fitted.width / fitted.height,
                    dimensions[0] / dimensions[1],
                    delta=0.01,
                )

    def test_card_text_contains_only_character_information(self):
        data = self.card_data(
            description="卡比是星之卡比系列的主角。", wiki_hint="引用回复素材"
        )
        seen = []
        original = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            seen.append(str(text))
            return original(draw, xy, text, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(ImageDraw.ImageDraw, "text", capture),
        ):
            render_ally_draw_card(data, Path(directory) / "character.png")
        visible = "\n".join(seen)
        self.assertIn("简介", visible)
        self.assertIn("卡比", visible)
        for text in (
            "今日盟友",
            "KIRBY",
            "DAILY ALLY",
            "ALLY PROFILE",
            "收藏者",
            "旅行者",
            "2026",
            "今日剩余",
            "收藏进度",
            "引用",
            "伙伴小档案",
            "初次相遇",
        ):
            self.assertNotIn(text, visible)
