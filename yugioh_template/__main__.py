"""python -m yugioh_template --input cards.json --output cards.png [--black-bold]."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .models import CardData, RenderOptions
from .renderer import render_card_bytes, render_sheet_bytes


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render portable printed cards from local JSON."
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="A card object, a list of cards, or an object with cards/options/layout keys",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="A PNG, JPG or JPEG file"
    )
    parser.add_argument(
        "--black-bold",
        action="store_true",
        help="Print the description in pure black extra-bold text",
    )
    args = parser.parse_args(argv)
    try:
        source = json.loads(args.input.read_text(encoding="utf-8-sig"))
        options, layout = {}, {}
        if isinstance(source, dict) and "cards" in source:
            options, layout = source.get("options", {}), source.get("layout", {})
            source = source["cards"]
        if not isinstance(options, dict) or not isinstance(layout, dict):
            raise TypeError("options and layout must be JSON objects")
        unknown_options = set(options) - {"description_black_bold"}
        unknown_layout = set(layout) - {"tile_width", "gap", "columns"}
        if unknown_options or unknown_layout:
            raise ValueError(
                "Unknown options/layout keys: "
                + ", ".join(sorted(unknown_options | unknown_layout))
            )
        enabled = options.get("description_black_bold", False)
        if type(enabled) is not bool:
            raise TypeError("description_black_bold must be a boolean")
        details = []
        opts = RenderOptions(
            description_black_bold=args.black_bold or enabled, details_out=details
        )
        format = "JPEG" if args.output.suffix.lower() in {".jpg", ".jpeg"} else "PNG"
        if args.output.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("--output must end in .png, .jpg or .jpeg")
        base = args.input.resolve().parent
        if isinstance(source, list):
            cards = [CardData.from_dict(card, base=base) for card in source]
            raw = render_sheet_bytes(cards, opts, format=format, **layout)
        else:
            raw = render_card_bytes(
                CardData.from_dict(source, base=base), opts, format=format
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(raw)
        print(str(args.output.resolve()))
        if details:
            print(
                json.dumps({"overflow": details}, ensure_ascii=False), file=sys.stderr
            )
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Cannot render card: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
