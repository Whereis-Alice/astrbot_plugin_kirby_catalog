"""Daily-card regressions for real image formats, bounded output and draw data."""

from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_kirby_catalog import ally_draw_card as card_module
from astrbot_plugin_kirby_catalog.ally_draw_card import (
    ALLY_DRAW_CARD_THEMES,
    CARD_HEIGHT,
    CARD_WIDTH,
    build_ally_draw_card_data,
    render_ally_draw_card,
)
from PIL import Image, ImageDraw


def encoded_image(image: Image.Image, format_name: str = "PNG") -> bytes:
    stream = BytesIO()
    image.save(stream, format=format_name)
    return stream.getvalue()


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
                        self.assertEqual(rendered.size, (CARD_WIDTH, CARD_HEIGHT))
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
