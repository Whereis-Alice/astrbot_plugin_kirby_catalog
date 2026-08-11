from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

DEFAULT_ENTRIES_PER_PAGE = 50
DEFAULT_COLUMNS = 2
DEFAULT_COMPACT_COLUMNS = 5
REFERENCE_RENDER_VERSION = 3

_GROUP_PALETTE = (
    ((255, 228, 236), (154, 54, 91), (255, 241, 246)),
    ((218, 239, 249), (38, 104, 137), (239, 249, 253)),
    ((255, 239, 202), (137, 91, 22), (255, 249, 231)),
    ((222, 244, 232), (47, 111, 82), (241, 252, 246)),
    ((232, 226, 250), (91, 69, 135), (247, 244, 253)),
)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path(
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
        ),
        Path(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
        Path(
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
        ),
        Path(
            "/usr/share/fonts/opentype/noto/NotoSansJP-Bold.otf"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    value: Any,
    font: ImageFont.ImageFont,
    width: int,
) -> list[str]:
    text = str(value or "").strip() or "未命名页面"
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current or " ")
    return lines


def _group_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("game_zh") or "综合").strip() or "综合",
        str(entry.get("game_ja") or "").strip(),
        str(entry.get("section_zh") or "综合资料").strip() or "综合资料",
        str(entry.get("section_ja") or "").strip(),
    )


def _normalise_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output = [dict(entry) for entry in entries if isinstance(entry, dict)]
    output.sort(
        key=lambda entry: (
            int(entry.get("catalog_index") or entry.get("source_index") or 0),
            str(entry.get("title_ja") or ""),
        )
    )
    return output


def _grouped_entries(entries: Sequence[dict[str, Any]]) -> list[tuple[tuple[str, str, str, str], list[dict[str, Any]]]]:
    groups: list[tuple[tuple[str, str, str, str], list[dict[str, Any]]]] = []
    for entry in entries:
        key = _group_key(entry)
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append(entry)
    return groups


def _paginate_rows(
    entries: Sequence[dict[str, Any]],
    entries_per_page: int,
    columns: int,
) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    rows: list[dict[str, Any]] = []
    count = 0
    page_group: tuple[str, str, str, str] | None = None

    def flush() -> None:
        nonlocal rows, count, page_group
        if rows:
            pages.append(rows)
        rows = []
        count = 0
        page_group = None

    for group_key, group_entries in _grouped_entries(entries):
        offset = 0
        while offset < len(group_entries):
            if rows and count >= entries_per_page:
                flush()
            if page_group != group_key:
                rows.append({"kind": "group", "group": group_key})
                page_group = group_key
            available = max(1, entries_per_page - count)
            take = min(available, len(group_entries) - offset)
            selected = group_entries[offset : offset + take]
            for row_start in range(0, len(selected), columns):
                rows.append(
                    {
                        "kind": "entries",
                        "entries": selected[row_start : row_start + columns],
                    }
                )
            offset += take
            count += take
            if offset < len(group_entries) and count >= entries_per_page:
                flush()
    flush()

    if len(pages) >= 2:
        previous_count = sum(
            len(row.get("entries", []) or [])
            for row in pages[-2]
            if row.get("kind") == "entries"
        )
        trailing_count = sum(
            len(row.get("entries", []) or [])
            for row in pages[-1]
            if row.get("kind") == "entries"
        )
        if (
            0 < trailing_count <= columns
            and previous_count + trailing_count <= entries_per_page + columns
        ):
            pages[-2].extend(pages.pop())
    return pages


def _compact_rows(
    entries: Sequence[dict[str, Any]], columns: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_key, group_entries in _grouped_entries(entries):
        rows.append({"kind": "group", "group": group_key})
        for row_start in range(0, len(group_entries), columns):
            rows.append(
                {
                    "kind": "entries",
                    "entries": group_entries[row_start : row_start + columns],
                }
            )
    return rows


def _entry_height(
    draw: ImageDraw.ImageDraw,
    entry: dict[str, Any],
    name_font: ImageFont.ImageFont,
    japanese_font: ImageFont.ImageFont,
    name_width: int,
) -> int:
    zh_lines = _wrap_text(draw, entry.get("title_zh"), name_font, name_width)
    ja_lines = _wrap_text(draw, entry.get("title_ja"), japanese_font, name_width)
    return max(112, 64 + len(zh_lines) * 28 + len(ja_lines) * 25)


def _compact_entry_names(entry: dict[str, Any]) -> tuple[str, str]:
    chinese = str(entry.get("base_zh") or entry.get("title_zh") or "未命名页面")
    japanese = str(entry.get("base_ja") or entry.get("title_ja") or "未命名页面")
    return chinese.strip(), japanese.strip()


def _compact_entry_height(
    draw: ImageDraw.ImageDraw,
    entry: dict[str, Any],
    name_font: ImageFont.ImageFont,
    japanese_font: ImageFont.ImageFont,
    name_width: int,
) -> int:
    chinese, japanese = _compact_entry_names(entry)
    zh_lines = _wrap_text(draw, chinese, name_font, name_width)
    ja_lines = _wrap_text(draw, japanese, japanese_font, name_width)
    return max(56, 16 + len(zh_lines) * 21 + len(ja_lines) * 17)


def _safe_stem(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("._") or "reference"


def _signature(
    entries: Sequence[dict[str, Any]],
    entries_per_page: int,
    columns: int,
    single_image: bool,
) -> str:
    payload = {
        "version": REFERENCE_RENDER_VERSION,
        "entries_per_page": entries_per_page,
        "columns": columns,
        "single_image": single_image,
        "entries": [
            {
                "catalog_index": entry.get("catalog_index"),
                "title_zh": entry.get("title_zh"),
                "title_ja": entry.get("title_ja"),
                "base_zh": entry.get("base_zh"),
                "base_ja": entry.get("base_ja"),
                "game_zh": entry.get("game_zh"),
                "game_ja": entry.get("game_ja"),
                "section_zh": entry.get("section_zh"),
                "section_ja": entry.get("section_ja"),
            }
            for entry in entries
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _draw_reference_page(
    output_path: Path,
    rows: Sequence[dict[str, Any]],
    *,
    page_number: int,
    page_total: int,
    total_entries: int,
    columns: int,
) -> None:
    canvas_width = 1840
    margin_x = 48
    top_height = 154
    footer_height = 42
    row_gap = 14
    column_gap = 22
    cell_width = (canvas_width - margin_x * 2 - column_gap * (columns - 1)) // columns
    title_font = _font(40, True)
    subtitle_font = _font(20)
    group_font = _font(24, True)
    group_subtitle_font = _font(16)
    index_font = _font(20, True)
    name_font = _font(23, True)
    japanese_font = _font(19)
    draw_probe = Image.new("RGB", (1, 1), "white")
    probe = ImageDraw.Draw(draw_probe)

    prepared_rows: list[tuple[dict[str, Any], int, int]] = []
    for row in rows:
        if row.get("kind") == "group":
            prepared_rows.append((row, 68, 0))
            continue
        row_entries = list(row.get("entries", []) or [])
        row_height = max(
            _entry_height(
                probe,
                entry,
                name_font,
                japanese_font,
                cell_width - 148,
            )
            for entry in row_entries
        )
        prepared_rows.append((row, row_height, len(row_entries)))

    canvas_height = top_height + footer_height
    if prepared_rows:
        canvas_height += sum(height for _, height, _ in prepared_rows)
        canvas_height += row_gap * (len(prepared_rows) - 1)
    canvas = Image.new("RGB", (canvas_width, canvas_height), (245, 248, 252))
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle(
        (24, 20, canvas_width - 24, top_height - 18),
        radius=18,
        fill=(255, 255, 255),
        outline=(224, 230, 236),
        width=2,
    )
    draw.rounded_rectangle(
        (24, 20, 36, top_height - 18),
        radius=6,
        fill=(232, 103, 145),
    )
    draw.text((58, 42), "真格攻略 Wiki 名称速查", fill=(48, 53, 61), font=title_font)
    draw.text(
        (60, 98),
        f"中文名称 / 日本語名称    共 {total_entries} 个页面    编号按作品与原站栏目排列",
        fill=(101, 111, 123),
        font=subtitle_font,
    )
    page_label = f"第 {page_number}/{page_total} 页"
    page_box = draw.textbbox((0, 0), page_label, font=subtitle_font)
    draw.text(
        (canvas_width - 64 - (page_box[2] - page_box[0]), 54),
        page_label,
        fill=(38, 104, 137),
        font=subtitle_font,
    )

    y = top_height
    palette_index = 0
    for row, row_height, _ in prepared_rows:
        if row.get("kind") == "group":
            game_zh, game_ja, section_zh, section_ja = row["group"]
            soft, dark, _ = _GROUP_PALETTE[palette_index % len(_GROUP_PALETTE)]
            palette_index += 1
            draw.rounded_rectangle(
                (margin_x, y, canvas_width - margin_x, y + row_height),
                radius=12,
                fill=soft,
                outline=soft,
            )
            title = f"{game_zh}  ·  {section_zh}"
            draw.text((margin_x + 22, y + 12), title, fill=dark, font=group_font)
            subtitle = " / ".join(value for value in (game_ja, section_ja) if value)
            if subtitle:
                draw.text(
                    (margin_x + 24, y + 43),
                    subtitle,
                    fill=dark,
                    font=group_subtitle_font,
                )
        else:
            row_entries = list(row.get("entries", []) or [])
            for column, entry in enumerate(row_entries):
                x = margin_x + column * (cell_width + column_gap)
                draw.rounded_rectangle(
                    (x, y, x + cell_width, y + row_height),
                    radius=12,
                    fill=(255, 255, 255),
                    outline=(222, 229, 236),
                    width=2,
                )
                badge = f"#{int(entry.get('catalog_index') or entry.get('source_index') or 0)}"
                badge_width = 104
                draw.rounded_rectangle(
                    (x + 16, y + 16, x + 16 + badge_width, y + 49),
                    radius=9,
                    fill=(238, 244, 248),
                )
                draw.text((x + 28, y + 20), badge, fill=(38, 104, 137), font=index_font)
                text_x = x + 136
                text_width = cell_width - 152
                zh_lines = _wrap_text(draw, entry.get("title_zh"), name_font, text_width)
                ja_lines = _wrap_text(
                    draw, entry.get("title_ja"), japanese_font, text_width
                )
                name_y = y + 12
                draw.text((text_x, name_y), "中文", fill=(154, 54, 91), font=group_subtitle_font)
                name_y += 20
                for line in zh_lines:
                    draw.text((text_x, name_y), line, fill=(45, 51, 59), font=name_font)
                    name_y += 28
                name_y += 4
                draw.text((text_x, name_y), "日文", fill=(38, 104, 137), font=group_subtitle_font)
                name_y += 19
                for line in ja_lines:
                    draw.text((text_x, name_y), line, fill=(84, 94, 104), font=japanese_font)
                    name_y += 25
        y += row_height + row_gap

    footer = "完整页面名称与来源 URL 见插件 resources/shinkaku_page_names.json / .csv / .md"
    draw.text((margin_x, canvas_height - footer_height + 9), footer, fill=(119, 128, 138), font=group_subtitle_font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def _draw_compact_reference(
    output_path: Path,
    entries: Sequence[dict[str, Any]],
    *,
    columns: int,
) -> None:
    canvas_width = 2160
    margin_x = 24
    top_height = 118
    footer_height = 32
    row_gap = 2
    column_gap = 6
    group_height = 46
    cell_width = (
        canvas_width - margin_x * 2 - column_gap * (columns - 1)
    ) // columns
    title_font = _font(34, True)
    subtitle_font = _font(17)
    group_font = _font(18, True)
    group_subtitle_font = _font(12)
    index_font = _font(14, True)
    name_font = _font(16, True)
    japanese_font = _font(13)
    draw_probe = Image.new("RGB", (1, 1), "white")
    probe = ImageDraw.Draw(draw_probe)
    rows = _compact_rows(entries, columns)

    prepared_rows: list[tuple[dict[str, Any], int]] = []
    for row in rows:
        if row.get("kind") == "group":
            prepared_rows.append((row, group_height))
            continue
        row_entries = list(row.get("entries", []) or [])
        row_height = max(
            _compact_entry_height(
                probe,
                entry,
                name_font,
                japanese_font,
                cell_width - 70,
            )
            for entry in row_entries
        )
        prepared_rows.append((row, row_height))

    canvas_height = top_height + footer_height
    if prepared_rows:
        canvas_height += sum(height for _, height in prepared_rows)
        canvas_height += row_gap * (len(prepared_rows) - 1)
    canvas = Image.new("RGB", (canvas_width, canvas_height), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle(
        (0, 0, canvas_width, top_height - 8),
        fill=(255, 255, 255),
        outline=(225, 230, 236),
        width=1,
    )
    draw.rectangle((0, 0, 12, top_height - 8), fill=(232, 103, 145))
    draw.text(
        (34, 24),
        "真格攻略 Wiki 名称速查",
        fill=(45, 50, 58),
        font=title_font,
    )
    draw.text(
        (36, 72),
        "中文名称 / 日本語名称  ·  按作品、资料类型与原站顺序编号",
        fill=(99, 108, 119),
        font=subtitle_font,
    )
    range_label = f"共 {len(entries)} 个页面  ·  #1-#{len(entries)}"
    range_box = draw.textbbox((0, 0), range_label, font=subtitle_font)
    draw.text(
        (canvas_width - 34 - (range_box[2] - range_box[0]), 34),
        range_label,
        fill=(38, 104, 137),
        font=subtitle_font,
    )

    y = top_height
    palette_index = 0
    entry_row_index = 0
    for row, row_height in prepared_rows:
        if row.get("kind") == "group":
            game_zh, game_ja, section_zh, section_ja = row["group"]
            soft, dark, _ = _GROUP_PALETTE[palette_index % len(_GROUP_PALETTE)]
            palette_index += 1
            draw.rectangle(
                (margin_x, y, canvas_width - margin_x, y + row_height),
                fill=soft,
            )
            draw.text(
                (margin_x + 12, y + 5),
                f"{game_zh}  ·  {section_zh}",
                fill=dark,
                font=group_font,
            )
            subtitle = " / ".join(
                value for value in (game_ja, section_ja) if value
            )
            if subtitle:
                draw.text(
                    (margin_x + 13, y + 28),
                    subtitle,
                    fill=dark,
                    font=group_subtitle_font,
                )
        else:
            row_fill = (
                (255, 255, 255)
                if entry_row_index % 2 == 0
                else (239, 242, 246)
            )
            entry_row_index += 1
            row_entries = list(row.get("entries", []) or [])
            for column in range(columns):
                x = margin_x + column * (cell_width + column_gap)
                draw.rectangle(
                    (x, y, x + cell_width, y + row_height),
                    fill=row_fill,
                    outline=(226, 231, 237),
                    width=1,
                )
                if column >= len(row_entries):
                    continue
                entry = row_entries[column]
                badge = f"#{int(entry.get('catalog_index') or entry.get('source_index') or 0)}"
                draw.text(
                    (x + 8, y + 8),
                    badge,
                    fill=(38, 104, 137),
                    font=index_font,
                )
                chinese, japanese = _compact_entry_names(entry)
                text_x = x + 62
                text_width = cell_width - 70
                zh_lines = _wrap_text(draw, chinese, name_font, text_width)
                ja_lines = _wrap_text(draw, japanese, japanese_font, text_width)
                text_y = y + 5
                for line in zh_lines:
                    draw.text(
                        (text_x, text_y),
                        line,
                        fill=(48, 53, 61),
                        font=name_font,
                    )
                    text_y += 21
                for line in ja_lines:
                    draw.text(
                        (text_x, text_y),
                        line,
                        fill=(102, 111, 121),
                        font=japanese_font,
                    )
                    text_y += 17
        y += row_height + row_gap

    footer = "完整名称、译名来源与页面 URL：resources/shinkaku_page_names.json / .csv / .md"
    draw.text(
        (margin_x, canvas_height - footer_height + 8),
        footer,
        fill=(119, 128, 138),
        font=group_subtitle_font,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def render_shinkaku_reference_pages(
    output_dir: Path,
    entries: Iterable[dict[str, Any]],
    *,
    entries_per_page: int = DEFAULT_ENTRIES_PER_PAGE,
    columns: int = DEFAULT_COLUMNS,
    single_image: bool = True,
) -> list[Path]:
    """Render the bundled Chinese/Japanese page-name index into cached PNGs."""

    normalised = _normalise_entries(entries)
    if not normalised:
        raise ValueError("真格攻略 Wiki 名称索引为空")
    entries_per_page = max(1, int(entries_per_page))
    single_image = bool(single_image)
    columns = max(1, min(7 if single_image else 3, int(columns)))
    pages = [] if single_image else _paginate_rows(
        normalised, entries_per_page, columns
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    signature = _signature(
        normalised, entries_per_page, columns, single_image
    )
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        manifest = {}
    if isinstance(manifest, dict) and manifest.get("signature") == signature:
        cached = [
            output_dir / Path(str(name)).name
            for name in manifest.get("outputs", [])
            if str(name).strip()
        ]
        if cached and all(path.is_file() for path in cached):
            return cached

    stem = _safe_stem("shinkaku_reference")
    outputs: list[Path] = []
    if single_image:
        output = output_dir / f"{stem}_compact.png"
        _draw_compact_reference(output, normalised, columns=columns)
        outputs.append(output)
    else:
        page_total = len(pages)
        for page_number, rows in enumerate(pages, start=1):
            output = output_dir / (
                f"{stem}_p{page_number:02d}-of-{page_total:02d}.png"
            )
            _draw_reference_page(
                output,
                rows,
                page_number=page_number,
                page_total=page_total,
                total_entries=len(normalised),
                columns=columns,
            )
            outputs.append(output)

    for old_name in manifest.get("outputs", []) if isinstance(manifest, dict) else []:
        old_path = output_dir / Path(str(old_name)).name
        if old_path not in outputs and old_path.suffix.lower() == ".png":
            try:
                old_path.unlink()
            except OSError:
                pass
    manifest_path.write_text(
        json.dumps(
            {
                "signature": signature,
                "entries": len(normalised),
                "entries_per_page": entries_per_page,
                "columns": columns,
                "single_image": single_image,
                "outputs": [path.name for path in outputs],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return outputs


def shinkaku_reference_text(entries: Iterable[dict[str, Any]]) -> str:
    """Build a complete plain-text fallback with the same grouping and numbers."""

    normalised = _normalise_entries(entries)
    lines = [
        f"真格攻略 Wiki 名称速查：共 {len(normalised)} 个页面",
        "编号按作品与原站栏目排列；每项依次为中文名称 / 日本語名称。",
    ]
    for group_key, group_entries in _grouped_entries(normalised):
        game_zh, game_ja, section_zh, section_ja = group_key
        group_title = f"{game_zh} · {section_zh}"
        if game_ja or section_ja:
            group_title += "（" + " / ".join(value for value in (game_ja, section_ja) if value) + "）"
        lines.append("")
        lines.append(group_title)
        for entry in group_entries:
            index = int(entry.get("catalog_index") or entry.get("source_index") or 0)
            lines.append(f"#{index} {entry.get('title_zh') or '未命名页面'} / {entry.get('title_ja') or '未命名页面'}")
    return "\n".join(lines)
