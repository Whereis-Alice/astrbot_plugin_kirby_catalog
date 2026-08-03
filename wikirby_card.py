from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

DEFAULT_CARD_TEMPLATE = "梦之泉"
CARD_TEMPLATE_NAMES = ("梦之泉", "卡比粉彩", "瓦豆鲁迪", "星际档案")

CARD_TEMPLATES: dict[str, dict[str, str]] = {
    "梦之泉": {
        "slug": "fountain",
        "label": "梦之泉",
        "eyebrow": "梦之泉档案",
        "canvas": "#f3f8f8",
        "surface": "#ffffff",
        "header": "#dff4f1",
        "image_bg": "#eef8ff",
        "title": "#16313a",
        "text": "#24343a",
        "muted": "#5f7378",
        "accent": "#008b87",
        "accent_alt": "#e34f7b",
        "accent_warm": "#f2b134",
        "border": "#bedbd8",
    },
    "卡比粉彩": {
        "slug": "popstar",
        "label": "卡比粉彩",
        "eyebrow": "波普之星角色档案",
        "canvas": "#fff7fa",
        "surface": "#ffffff",
        "header": "#ffe3ed",
        "image_bg": "#e9f6ff",
        "title": "#3e2942",
        "text": "#3d3340",
        "muted": "#756573",
        "accent": "#d93f73",
        "accent_alt": "#167d9a",
        "accent_warm": "#f1aa2b",
        "border": "#efbfd0",
    },
    "瓦豆鲁迪": {
        "slug": "waddle",
        "label": "瓦豆鲁迪",
        "eyebrow": "瓦豆鲁迪观察笔记",
        "canvas": "#f7f8f5",
        "surface": "#ffffff",
        "header": "#ffe8c6",
        "image_bg": "#fff4df",
        "title": "#3b3028",
        "text": "#403832",
        "muted": "#75685d",
        "accent": "#d9562b",
        "accent_alt": "#0b7d75",
        "accent_warm": "#e4a11b",
        "border": "#e8c99a",
    },
    "星际档案": {
        "slug": "archive",
        "label": "星际档案",
        "eyebrow": "星际参考档案",
        "canvas": "#eef1f3",
        "surface": "#ffffff",
        "header": "#27313b",
        "image_bg": "#e8f0ee",
        "title": "#ffffff",
        "text": "#26313a",
        "muted": "#66747d",
        "accent": "#20a39e",
        "accent_alt": "#e54f6d",
        "accent_warm": "#f0b429",
        "border": "#b9c5ca",
    },
}

WIKIRBY_CARD_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * {
      box-sizing: border-box;
      letter-spacing: 0;
    }

    html,
    body {
      width: 100%;
      margin: 0;
      padding: 0;
      background: transparent;
    }

    body {
      font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC",
        Arial, sans-serif;
      color: {{ theme.text }};
    }

    #kirby-card {
      width: 100%;
      overflow: hidden;
      background: {{ theme.canvas }};
      border: 1px solid {{ theme.border }};
      border-radius: 6px;
      box-shadow: 0 14px 38px rgba(24, 39, 48, 0.12);
    }

    .color-rail {
      display: grid;
      grid-template-columns: 46% 31% 23%;
      height: 12px;
    }

    .color-rail span:nth-child(1) { background: {{ theme.accent }}; }
    .color-rail span:nth-child(2) { background: {{ theme.accent_alt }}; }
    .color-rail span:nth-child(3) { background: {{ theme.accent_warm }}; }

    .masthead {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 28px;
      align-items: end;
      padding: 34px 44px 30px;
      background: {{ theme.header }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .eyebrow {
      margin: 0 0 10px;
      color: {{ theme.accent }};
      font-size: 17px;
      font-weight: 800;
      line-height: 1.2;
    }

    .title {
      margin: 0;
      color: {{ theme.title }};
      font-size: 42px;
      font-weight: 800;
      line-height: 1.18;
      overflow-wrap: anywhere;
    }

    .subtitle {
      margin-top: 10px;
      color: {{ theme.muted }};
      font-size: 18px;
      line-height: 1.5;
    }

    .page-chip {
      min-width: 112px;
      padding: 12px 15px;
      color: {{ theme.text }};
      background: {{ theme.surface }};
      border: 1px solid {{ theme.border }};
      border-radius: 4px;
      text-align: center;
    }

    .page-chip strong {
      display: block;
      color: {{ theme.accent_alt }};
      font-size: 21px;
      line-height: 1;
    }

    .page-chip span {
      display: block;
      margin-top: 7px;
      color: {{ theme.muted }};
      font-size: 14px;
    }

    .hero {
      display: grid;
      grid-template-columns: 1fr 390px;
      min-height: 330px;
      background: {{ theme.image_bg }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .hero-copy {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 42px 30px 42px 44px;
    }

    .hero-copy .index {
      color: {{ theme.accent_alt }};
      font-size: 16px;
      font-weight: 800;
    }

    .hero-copy .hero-title {
      margin-top: 12px;
      color: {{ theme.text }};
      font-size: 26px;
      font-weight: 800;
      line-height: 1.25;
    }

    .hero-copy .hero-note {
      margin-top: 14px;
      color: {{ theme.muted }};
      font-size: 18px;
      line-height: 1.65;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .hero-art {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px 40px 24px 12px;
    }

    .hero-art img {
      display: block;
      width: 100%;
      height: 270px;
      object-fit: contain;
    }

    .hero--text {
      display: block;
      min-height: 0;
    }

    .hero--text .hero-copy {
      padding-right: 44px;
    }

    .content {
      padding: 0 44px 18px;
      background: {{ theme.surface }};
    }

    .facts-band {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 34px;
      padding: 30px 0 28px;
      border-bottom: 1px solid {{ theme.border }};
    }

    .facts-band .content-block {
      margin: 0;
      padding: 0;
      border-bottom: 0;
    }

    .details-columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 36px;
      min-width: 0;
      padding: 32px 0 8px;
      align-items: start;
    }

    .details-columns--flush {
      padding-top: 30px;
    }

    .details-column {
      min-width: 0;
    }

    .content-block {
      display: block;
      min-width: 0;
      margin-bottom: 28px;
      padding: 0 0 24px;
      border-bottom: 1px solid {{ theme.border }};
    }

    .content-block:last-child {
      border-bottom: 0;
      margin-bottom: 0;
    }

    .block-heading {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 11px;
      color: {{ theme.accent }};
      font-size: 18px;
      font-weight: 800;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .block-mark {
      flex: 0 0 9px;
      width: 9px;
      height: 28px;
      margin-top: 1px;
      background: {{ theme.accent_alt }};
      border-radius: 2px;
    }

    .block-body {
      min-width: 0;
      color: {{ theme.text }};
      font-size: 17px;
      line-height: 1.62;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .fact-list {
      margin: 0;
      border-top: 1px solid {{ theme.border }};
    }

    .fact-row {
      display: grid;
      grid-template-columns: minmax(76px, 0.72fr) minmax(0, 1.45fr);
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid {{ theme.border }};
    }

    .fact-row:last-child {
      border-bottom: 0;
    }

    .fact-label {
      margin: 0;
      color: {{ theme.muted }};
      font-size: 14px;
      font-weight: 800;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .fact-value {
      min-width: 0;
      margin: 0;
      color: {{ theme.text }};
      font-size: 15px;
      line-height: 1.55;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .content-block[data-tone="summary"] .block-heading {
      color: {{ theme.accent_alt }};
    }

    .content-block[data-tone="facts"] .block-mark {
      background: {{ theme.accent_warm }};
    }

    .footer {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      padding: 22px 44px 26px;
      color: {{ theme.muted }};
      background: {{ theme.canvas }};
      border-top: 1px solid {{ theme.border }};
      font-size: 15px;
      line-height: 1.55;
    }

    .footer-label {
      color: {{ theme.accent }};
      font-weight: 800;
    }

    .source {
      min-width: 0;
      overflow-wrap: anywhere;
    }

    #kirby-card.template-popstar .masthead {
      display: block;
      text-align: center;
    }

    #kirby-card.template-popstar .page-chip {
      display: inline-block;
      margin-top: 22px;
    }

    #kirby-card.template-popstar .hero {
      grid-template-columns: 330px 1fr;
    }

    #kirby-card.template-popstar .hero-copy {
      order: 2;
      padding-left: 18px;
      padding-right: 44px;
    }

    #kirby-card.template-popstar .hero-art {
      order: 1;
      padding-left: 44px;
      padding-right: 12px;
    }

    #kirby-card.template-waddle {
      border-top: 12px solid {{ theme.accent }};
    }

    #kirby-card.template-waddle .color-rail {
      display: none;
    }

    #kirby-card.template-waddle .masthead {
      border-left: 18px solid {{ theme.accent_alt }};
    }

    #kirby-card.template-archive {
      border-radius: 0;
      box-shadow: none;
      border-width: 2px;
    }

    #kirby-card.template-archive .eyebrow,
    #kirby-card.template-archive .subtitle {
      color: #d8e2e7;
    }

    #kirby-card.template-archive .page-chip {
      color: #ffffff;
      background: #34414c;
      border-color: #5c6b76;
    }

    #kirby-card.template-archive .page-chip span {
      color: #d8e2e7;
    }

    #kirby-card.template-archive .block-heading,
    #kirby-card.template-archive .footer-label {
      font-family: "Noto Sans Mono CJK SC", "Microsoft YaHei", monospace;
    }

    @media (max-width: 860px) {
      .masthead {
        padding-left: 34px;
        padding-right: 34px;
      }

      .hero {
        grid-template-columns: 1fr 310px;
      }

      .hero-copy {
        padding-left: 34px;
      }

      .hero-art {
        padding-right: 28px;
      }

      .content {
        padding-left: 34px;
        padding-right: 34px;
      }

      .facts-band,
      .details-columns {
        grid-template-columns: 1fr;
      }

      .facts-band {
        gap: 26px;
      }

      .details-columns {
        gap: 28px;
      }

      .footer {
        padding-left: 34px;
        padding-right: 34px;
      }
    }

    @media (max-width: 640px) {
      .masthead {
        grid-template-columns: 1fr;
        gap: 16px;
        align-items: start;
        padding: 28px 28px 24px;
      }

      .title {
        font-size: 34px;
      }

      .subtitle {
        font-size: 16px;
      }

      .page-chip {
        justify-self: start;
        min-width: 0;
      }

      .hero,
      #kirby-card.template-popstar .hero {
        grid-template-columns: 1fr;
      }

      .hero-copy,
      #kirby-card.template-popstar .hero-copy {
        order: 1;
        padding: 30px 28px 20px;
      }

      .hero--text .hero-copy {
        padding-right: 28px;
      }

      .hero-art,
      #kirby-card.template-popstar .hero-art {
        order: 2;
        padding: 0 28px 28px;
      }

      .hero-art img {
        height: 220px;
      }

      .content {
        padding-left: 28px;
        padding-right: 28px;
      }

      .facts-band {
        gap: 24px;
        padding-top: 26px;
      }

      .block-heading {
        font-size: 17px;
      }

      .block-body {
        font-size: 16px;
      }

      .fact-row {
        grid-template-columns: minmax(70px, 0.72fr) minmax(0, 1.45fr);
      }

      .footer {
        grid-template-columns: 1fr;
        gap: 6px;
        padding: 20px 28px 24px;
      }
    }
  </style>
</head>
<body>
  <main id="kirby-card" class="template-{{ theme.slug }}">
    <div class="color-rail"><span></span><span></span><span></span></div>
    <header class="masthead">
      <div>
        <div class="eyebrow">{{ theme.eyebrow | e }}</div>
        <h1 class="title">{{ title | e }}</h1>
        <div class="subtitle">WiKirby 百科阅读卡片 · {{ theme.label | e }}</div>
      </div>
      <div class="page-chip">
        <strong>角色档案</strong>
        <span>WIKIRBY REFERENCE</span>
      </div>
    </header>

    <section class="hero{% if not image_data_uri %} hero--text{% endif %}">
      <div class="hero-copy">
        <div class="index">页面简介</div>
        <div class="hero-title">简介</div>
        <div class="hero-note">{{ summary | e }}</div>
      </div>
      {% if image_data_uri %}
      <div class="hero-art">
        <img src="{{ image_data_uri }}" alt="{{ title | e }}" />
      </div>
      {% endif %}
    </section>

    <div class="content">
      {% if left_blocks %}
      <section class="facts-band">
        {% for block in left_blocks %}
        <section class="content-block" data-tone="{{ block.tone }}">
          <div class="block-heading">
            <span class="block-mark"></span>
            <span>{{ block.title | e }}</span>
          </div>
          {% if block.fact_items %}
          <dl class="fact-list">
            {% for item in block.fact_items %}
            <div class="fact-row">
              <dt class="fact-label">{{ item.label | e }}</dt>
              <dd class="fact-value">{{ item.value | e }}</dd>
            </div>
            {% endfor %}
          </dl>
          {% else %}
          <div class="block-body">{{ block.body | e }}</div>
          {% endif %}
        </section>
        {% endfor %}
      </section>
      {% endif %}
      {% if right_block_count %}
      <section class="details-columns{% if not left_blocks %} details-columns--flush{% endif %}">
        {% for column in right_columns %}
        <div class="details-column">
          {% for block in column %}
          <section class="content-block" data-tone="{{ block.tone }}">
            <div class="block-heading">
              <span class="block-mark"></span>
              <span>{{ block.title | e }}</span>
            </div>
            <div class="block-body">{{ block.body | e }}</div>
          </section>
          {% endfor %}
        </div>
        {% endfor %}
      </section>
      {% endif %}
    </div>

    <footer class="footer">
      <div class="footer-label">来源</div>
      <div class="source">{{ source | e }}</div>
    </footer>
  </main>
</body>
</html>
"""

_HEADING_RE = re.compile(r"^(.{1,48}?)[：:]$")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])\s*")
_LINE_UNITS = 54


def resolve_card_template(value: Any) -> dict[str, str]:
    name = str(value or DEFAULT_CARD_TEMPLATE).strip()
    aliases = {
        "fountain": "梦之泉",
        "dream": "梦之泉",
        "popstar": "卡比粉彩",
        "pink": "卡比粉彩",
        "waddle": "瓦豆鲁迪",
        "archive": "星际档案",
    }
    name = aliases.get(name.casefold(), name)
    return dict(CARD_TEMPLATES.get(name, CARD_TEMPLATES[DEFAULT_CARD_TEMPLATE]))


def estimate_text_lines(text: str) -> int:
    lines = 0
    for raw_line in (text or "").splitlines() or [""]:
        units = sum(_display_units(char) for char in raw_line)
        lines += max(1, math.ceil(units / _LINE_UNITS))
    return lines


def build_card_layout(
    summary: str,
    detail_text: str,
) -> dict[str, Any]:
    summary = summary.strip() or "该页面暂时没有可显示的正文摘要。"
    detail_blocks = _detail_blocks(detail_text)
    left_blocks: list[dict[str, Any]] = []
    right_blocks: list[dict[str, Any]] = []

    for block in detail_blocks:
        if block["tone"] == "facts" or block["title"] == "其他语言名称":
            left_blocks.extend(
                _with_fact_items(part) for part in _split_for_column(block)
            )
        else:
            right_blocks.extend(_split_for_column(block, max_lines=92))

    if not left_blocks and not right_blocks:
        left_blocks.append(
            _with_fact_items(
                {
                    "title": "页面资料",
                    "body": "暂无额外资料。",
                    "tone": "facts",
                }
            )
        )

    columns: list[list[dict[str, str]]] = [[], []]
    column_lines = [0, 0]
    for block in right_blocks:
        target = 0 if column_lines[0] <= column_lines[1] else 1
        columns[target].append(block)
        column_lines[target] += estimate_text_lines(block["body"]) + 4

    return {
        "summary": summary,
        "left_blocks": left_blocks,
        "right_columns": columns,
        "right_block_count": sum(len(column) for column in columns),
    }


def _split_for_column(
    block: dict[str, str], max_lines: int = 76
) -> list[dict[str, str]]:
    chunks = _chunk_body(block["body"], max_lines)
    result: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks):
        title = block["title"] if index == 0 else f"{block['title']}（续）"
        result.append({**block, "title": title, "body": chunk})
    return result


def _with_fact_items(block: dict[str, str]) -> dict[str, Any]:
    return {**block, "fact_items": _key_value_items(block["body"])}


def _key_value_items(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    source_lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in source_lines:
        line = line.lstrip("•*- ").strip()
        match = re.fullmatch(r"([^：:]{1,24})[：:]\s*(.+)", line)
        if not match:
            return []
        items.append(
            {"label": match.group(1).strip(), "value": match.group(2).strip()}
        )
    return items if len(items) >= 2 else []


def _detail_blocks(detail_text: str) -> list[dict[str, str]]:
    if not detail_text.strip():
        return []
    blocks: list[dict[str, str]] = []
    title = "资料速览"
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        body = "\n".join(line for line in lines if line).strip()
        if body:
            tone = "facts" if title == "资料速览" else "section"
            blocks.append({"title": title, "body": body, "tone": tone})
        lines = []

    for raw_line in detail_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING_RE.fullmatch(line)
        if heading:
            flush()
            title = heading.group(1).strip()
            continue
        lines.append(line)
    flush()
    return blocks


def _chunk_body(text: str, max_lines: int) -> list[str]:
    segments = _text_segments(text)
    chunks: list[str] = []
    current: list[str] = []

    for segment in segments:
        for part in _split_oversized_segment(segment, max_lines):
            candidate = "\n".join([*current, part])
            if current and estimate_text_lines(candidate) > max_lines:
                chunks.append("\n".join(current).strip())
                current = [part]
            else:
                current.append(part)
    if current:
        chunks.append("\n".join(current).strip())
    return chunks or [""]


def _text_segments(text: str) -> list[str]:
    segments: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("•", "-", "*")):
            segments.append(line)
            continue
        sentences = [part.strip() for part in _SENTENCE_BREAK_RE.split(line)]
        segments.extend(part for part in sentences if part)
    return segments


def _split_oversized_segment(segment: str, max_lines: int) -> list[str]:
    max_units = max_lines * _LINE_UNITS
    if _text_units(segment) <= max_units:
        return [segment]

    parts: list[str] = []
    remaining = segment
    while remaining:
        units = 0
        cut = 0
        for index, char in enumerate(remaining, start=1):
            next_units = units + _display_units(char)
            if next_units > max_units:
                break
            units = next_units
            cut = index
        if cut >= len(remaining):
            parts.append(remaining.strip())
            break
        preferred = max(
            remaining.rfind(" ", int(cut * 0.6), cut),
            remaining.rfind("，", int(cut * 0.6), cut),
            remaining.rfind(",", int(cut * 0.6), cut),
        )
        if preferred > 0:
            cut = preferred + 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [part for part in parts if part]


def _text_units(text: str) -> int:
    return sum(_display_units(char) for char in text)


def _display_units(char: str) -> int:
    if char == "\t":
        return 4
    return 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
