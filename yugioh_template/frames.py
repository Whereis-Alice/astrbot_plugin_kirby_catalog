"""Reference frame fragments, separated from all text and illustrations."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


_ROOT = Path(__file__).parent / "assets/yugioh/frames"


@lru_cache(maxsize=24)
def _frame(name, width, height, kind):
    with Image.open(_ROOT / (name + ".png")) as opened:
        image = opened.convert("RGBA")
    alpha = image.getchannel("A")
    if name == "title" and kind == "SHINY":
        # The supplied white card has an unboxed name and a narrow lower lip.
        target = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(target)
        for offset, colour in (
            (7, (120, 126, 126, 80)),
            (6, (72, 81, 83, 165)),
            (5, (143, 149, 148, 210)),
            (4, (232, 235, 231, 190)),
        ):
            draw.line(
                (4, height - offset, width - 4, height - offset), fill=colour, width=1
            )
        return target
    if name == "outer" and kind != "C":
        red, green, blue, _ = image.split()
        warm = ImageChops.multiply(
            ImageChops.subtract(red, green).point(lambda v: 255 if v > 9 else 0),
            ImageChops.subtract(red, blue).point(lambda v: 255 if v > 12 else 0),
        )
        tones = {
            "R": ((56, 13, 49), (235, 152, 210)),
            "SR": ((45, 28, 65), (201, 173, 220)),
            "SSR": ((5, 10, 15), (150, 158, 150)),
            "SHINY": ((90, 97, 99), (251, 252, 247)),
        }
        tint = ImageOps.colorize(ImageOps.grayscale(image), *tones[kind]).convert(
            "RGBA"
        )
        image = Image.composite(tint, image, warm)
        image.putalpha(alpha)
    if name in {"title", "description"} and kind != "C":
        tones = {
            "R": ((57, 15, 51), (240, 182, 220)),
            "SR": ((44, 27, 62), (209, 182, 226)),
            "SSR": ((3, 8, 11), (178, 176, 160)),
            "SHINY": ((71, 80, 85), (252, 250, 242)),
        }
        image = ImageOps.colorize(ImageOps.grayscale(image), *tones[kind]).convert(
            "RGBA"
        )
        image.putalpha(alpha)
    if name in {"outer", "description"}:
        source_border, target_border = (48, 56) if name == "outer" else (40, 26)
        target = Image.new("RGBA", (width, height))
        sx, sy = (
            (0, source_border, image.width - source_border, image.width),
            (0, source_border, image.height - source_border, image.height),
        )
        dx, dy = (
            (0, target_border, width - target_border, width),
            (0, target_border, height - target_border, height),
        )
        for row in range(3):
            for column in range(3):
                if row == 1 and column == 1:
                    continue
                tile = image.crop((sx[column], sy[row], sx[column + 1], sy[row + 1]))
                tile = tile.resize(
                    (dx[column + 1] - dx[column], dy[row + 1] - dy[row]),
                    Image.Resampling.LANCZOS,
                )
                target.alpha_composite(tile, (dx[column], dy[row]))
        return target
    return image.resize((width, height), Image.Resampling.LANCZOS)


def paint_frame(base, name, box, kind):
    left, top, right, bottom = map(round, box)
    width, height = right - left, bottom - top
    # The original picture mould stands above the stock: its broad ambient
    # shadow is visible on all four sides, with a darker contact edge below.
    # Keep this outside the mould so neither paper nor portrait is dimmed.
    if name == "picture":
        margin = 64
        for blur, spread, opacity, offset in (
            (15, 5, 185, (0, 1)),
            (5, 1, 150, (2, 4)),
        ):
            mask = Image.new("L", (width + margin * 2, height + margin * 2))
            ImageDraw.Draw(mask).rectangle(
                (
                    margin - spread,
                    margin - spread,
                    margin + width + spread,
                    margin + height + spread,
                ),
                fill=opacity,
            )
            shade = Image.new("RGBA", mask.size, (14, 17, 20, 0))
            shade.putalpha(mask.filter(ImageFilter.GaussianBlur(blur)))
            base.alpha_composite(
                shade, (left - margin + offset[0], top - margin + offset[1])
            )
    elif name in {"title", "description"} and not (name == "title" and kind == "SHINY"):
        margin = 20
        mask = Image.new("L", (width + margin * 2, height + margin * 2))
        ImageDraw.Draw(mask).rectangle(
            (margin, margin, width + margin, height + margin), fill=160
        )
        shade = Image.new("RGBA", mask.size, (12, 13, 20, 0))
        shade.putalpha(mask.filter(ImageFilter.GaussianBlur(4)))
        base.alpha_composite(shade, (left - margin + 3, top - margin + 5))
    base.alpha_composite(_frame(name, width, height, kind), (left, top))
