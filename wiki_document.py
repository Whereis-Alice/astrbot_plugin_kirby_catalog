from __future__ import annotations

import base64
import hashlib
import html
import re
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .wiki_content import build_content_groups, inline_markup_html, parse_detail_blocks

_THEMES = {
    "梦之泉": {
        "accent": "#d45f90",
        "accent_dark": "#7f3158",
        "accent_soft": "#f9dce9",
        "secondary": "#4f8fb8",
        "secondary_dark": "#315f79",
        "secondary_soft": "#dceef8",
        "surface": "#fffefe",
        "page": "#f5f8fb",
    },
    "卡比粉彩": {
        "accent": "#cc648d",
        "accent_dark": "#7a3654",
        "accent_soft": "#f8dce8",
        "secondary": "#5b9b88",
        "secondary_dark": "#356457",
        "secondary_soft": "#ddf1ea",
        "surface": "#fffefe",
        "page": "#f7f8fb",
    },
    "瓦豆鲁迪": {
        "accent": "#ba6d35",
        "accent_dark": "#70411f",
        "accent_soft": "#f7e3d2",
        "secondary": "#4f8f9c",
        "secondary_dark": "#315f65",
        "secondary_soft": "#dceff1",
        "surface": "#fffefd",
        "page": "#f7f8f7",
    },
    "星际档案": {
        "accent": "#7667a8",
        "accent_dark": "#4b4175",
        "accent_soft": "#e8e3f5",
        "secondary": "#3f8f9e",
        "secondary_dark": "#2e6771",
        "secondary_soft": "#dceff2",
        "surface": "#fdfdff",
        "page": "#f2f5f8",
    },
}


def _safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", str(value or "")).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return value[:80] or "Kirby-Wiki"


def _image_data_uri(data: bytes | None) -> str:
    if not data:
        return ""
    if data.startswith(b"\x89PNG"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _linkify(value: str) -> str:
    return inline_markup_html(value)


def _plain_text_html(text: str) -> str:
    output: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if not list_items:
            return
        output.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
        list_items.clear()

    for raw_line in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush_list()
            continue
        heading = re.fullmatch(r"【(.+?)】", line)
        subheading = re.fullmatch(r"◆\s*(.+)", line)
        bullet = re.match(r"^(?:[-•·]|\d+[.、])\s*(.+)$", line)
        if heading:
            flush_list()
            output.append(f"<h2>{html.escape(heading.group(1))}</h2>")
        elif subheading:
            flush_list()
            output.append(f"<h3>{html.escape(subheading.group(1))}</h3>")
        elif bullet:
            list_items.append(_linkify(bullet.group(1)))
        elif line.endswith(("：", ":")) and len(line) <= 72:
            flush_list()
            output.append(f"<h3>{html.escape(line.rstrip('：:'))}</h3>")
        else:
            flush_list()
            output.append(f"<p>{_linkify(line)}</p>")
    flush_list()
    return "\n".join(output)


def _cell_value(cell: Any) -> tuple[str, str]:
    if not isinstance(cell, dict):
        return str(cell or ""), ""
    image = str(cell.get("icon_data_uri") or cell.get("icon_url") or "").strip()
    return str(cell.get("text") or "").strip(), image


def _rich_section_html(
    section: dict[str, Any], *, nested: bool = False, show_title: bool = True
) -> str:
    if not isinstance(section, dict):
        return ""
    kind = str(section.get("kind") or "").strip()
    title = str(section.get("title") or section.get("display_title") or "资料").strip()
    context = str(section.get("context") or "").strip()
    heading_tag = "h3" if nested else "h2"
    heading = f"<{heading_tag}>{html.escape(title)}</{heading_tag}>" if show_title else ""
    if context and show_title:
        heading += f'<div class="section-context">{html.escape(context)}</div>'

    body = ""
    section_class = ""
    if kind == "table":
        headers = [str(value or "") for value in section.get("headers", []) or []]
        rows = section.get("rows", []) or []
        if not headers or not rows:
            return ""
        table_rows: list[str] = []
        for row in rows:
            cells: list[str] = []
            for raw_cell in row if isinstance(row, list) else []:
                text, image = _cell_value(raw_cell)
                media = (
                    f'<img class="table-icon" src="{html.escape(image, quote=True)}" alt="" />'
                    if image
                    else ""
                )
                cells.append(
                    f"<td>{media}<span>{_linkify(text) if text else '—'}</span></td>"
                )
            table_rows.append("<tr>" + "".join(cells) + "</tr>")
        body = (
            '<div class="table-wrap"><table><thead><tr>'
            + "".join(f"<th>{html.escape(value)}</th>" for value in headers)
            + "</tr></thead><tbody>"
            + "".join(table_rows)
            + "</tbody></table></div>"
        )
        section_class = "table-section"
    elif kind == "quotes":
        quotes: list[str] = []
        for quote in section.get("quotes", []) or []:
            if not isinstance(quote, dict) or not str(quote.get("text") or "").strip():
                continue
            credit = " · ".join(
                value
                for value in (
                    str(quote.get("attribution") or "").strip(),
                    str(quote.get("source") or "").strip(),
                )
                if value
            )
            quotes.append(
                '<blockquote><div class="quote-text">'
                + _linkify(str(quote.get("text") or ""))
                + "</div>"
                + (f"<cite>— {html.escape(credit)}</cite>" if credit else "")
                + "</blockquote>"
            )
        if not quotes:
            return ""
        body = "".join(quotes)
        section_class = "quote-section"
    elif kind == "techniques":
        groups: list[str] = []
        intro = str(section.get("intro") or "").strip()
        if intro:
            groups.append(f'<div class="tech-intro">{_plain_text_html(intro)}</div>')
        for group in section.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            rows = group.get("rows", []) or []
            if not rows:
                continue
            group_title = str(group.get("label") or "").strip()
            table_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                table_rows.append(
                    "<tr>"
                    f"<td><strong>{html.escape(str(row.get('move') or '未命名招式'))}</strong></td>"
                    f"<td>{html.escape(str(row.get('controls') or '—'))}</td>"
                    f"<td>{_linkify(str(row.get('description') or ''))}</td>"
                    f"<td>{html.escape(str(row.get('damage') or '—'))}</td>"
                    "</tr>"
                )
            groups.append(
                (f"<h4>{html.escape(group_title)}</h4>" if group_title else "")
                + '<div class="table-wrap"><table class="techniques"><thead><tr>'
                "<th>招式</th><th>操作</th><th>说明</th><th>伤害</th>"
                "</tr></thead><tbody>"
                + "".join(table_rows)
                + "</tbody></table></div>"
            )
        if not groups:
            return ""
        body = "".join(groups)
        section_class = "technique-section"
    else:
        return ""

    wrapper = "div" if nested else "section"
    classes = f"module-rich {section_class}" if nested else f"content-section {section_class}"
    return f'<{wrapper} class="{classes}">' + heading + body + f"</{wrapper}>"


def _rich_sections_html(rich_sections: Iterable[dict[str, Any]]) -> str:
    return "\n".join(
        value
        for value in (
            _rich_section_html(section)
            for section in rich_sections
            if isinstance(section, dict)
        )
        if value
    )


def _ordered_content_html(
    detail_text: str,
    rich_sections: Iterable[dict[str, Any]],
    *,
    preserve_source_order: bool,
) -> str:
    groups = build_content_groups(
        parse_detail_blocks(detail_text),
        [dict(section) for section in rich_sections if isinstance(section, dict)],
        preserve_source_order=preserve_source_order,
    )
    output: list[str] = []
    for group in groups:
        title = str(group.get("title") or "").strip()
        tone = int(group.get("tone_index", 0) or 0)
        if group.get("group_only"):
            output.append(
                f'<section class="module-divider tone-{tone}"><h2>{html.escape(title)}</h2></section>'
            )
            continue
        classes = ["content-section", "article-module", f"tone-{tone}"]
        if int(group.get("level", 1) or 1) > 1:
            classes.append("article-module-child")
        body = [f'<section class="{" ".join(classes)}">']
        if title:
            body.append(f"<h2>{html.escape(title)}</h2>")
        if group.get("body_html"):
            body.append(f'<div class="module-body">{group["body_html"]}</div>')
        attachments = list(group.get("rich_sections", []) or [])
        for section in attachments:
            section_title = str(
                section.get("title") or section.get("display_title") or ""
            ).strip()
            show_title = bool(
                section_title
                and (
                    section_title.casefold() != title.casefold()
                    or len(attachments) > 1
                )
            )
            rendered = _rich_section_html(
                section, nested=True, show_title=show_title
            )
            if rendered:
                body.append(rendered)
        body.append("</section>")
        output.append("".join(body))
    return "\n".join(output)


def cleanup_wiki_documents(output_dir: Path, retention_minutes: float) -> None:
    if retention_minutes <= 0 or not output_dir.exists():
        return
    cutoff = time.time() - retention_minutes * 60
    for path in output_dir.glob("*.html"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def build_wiki_document(
    output_dir: Path,
    *,
    wiki_name: str,
    title: str,
    source_url: str,
    summary: str,
    detail_text: str,
    rich_sections: Iterable[dict[str, Any]] = (),
    image_bytes: bytes | None = None,
    media_urls: Iterable[str] = (),
    template_name: str = "卡比粉彩",
    preserve_source_order: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    theme = _THEMES.get(template_name, _THEMES["卡比粉彩"])
    image_uri = _image_data_uri(image_bytes)
    source_url = str(source_url or "").strip()
    media = list(dict.fromkeys(str(value or "").strip() for value in media_urls if value))
    media_html = ""
    if media:
        media_html = (
            '<section class="content-section media-section"><h2>相关媒体</h2><ul>'
            + "".join(
                f'<li><a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noreferrer">{html.escape(url)}</a></li>'
                for url in media
            )
            + "</ul></section>"
        )
    hero_image = (
        f'<figure class="hero-image"><img src="{image_uri}" alt="{html.escape(title)}" /></figure>'
        if image_uri
        else ""
    )
    summary_html = (
        '<section class="summary"><div class="eyebrow">页面概览</div>'
        + _plain_text_html(summary)
        + "</section>"
        if summary
        else ""
    )
    content_html = _ordered_content_html(
        detail_text,
        rich_sections,
        preserve_source_order=preserve_source_order,
    )
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)} - {html.escape(wiki_name)}</title>
  <style>
    :root {{
      --accent: {theme['accent']};
      --accent-dark: {theme['accent_dark']};
      --accent-soft: {theme['accent_soft']};
      --secondary: {theme['secondary']};
      --secondary-dark: {theme['secondary_dark']};
      --secondary-soft: {theme['secondary_soft']};
      --surface: {theme['surface']};
      --page: {theme['page']};
      --ink: #25303a;
      --muted: #66717c;
      --line: #dbe2e8;
      --warm: #fff2c9;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--page); color: var(--ink); font-family: "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif; line-height: 1.75; }}
    a {{ color: var(--secondary); text-decoration-thickness: 1px; text-underline-offset: 3px; overflow-wrap: anywhere; }}
    .page {{ width: min(1180px, calc(100% - 28px)); margin: 22px auto 48px; }}
    header {{ padding: 30px 34px; background: var(--surface); border: 1px solid var(--line); border-top: 6px solid var(--accent); border-radius: 8px; }}
    .wiki-name {{ color: var(--accent); font-size: 14px; font-weight: 800; }}
    h1 {{ margin: 6px 0 8px; font-size: 48px; line-height: 1.2; letter-spacing: 0; overflow-wrap: anywhere; }}
    .source {{ color: var(--muted); font-size: 14px; overflow-wrap: anywhere; }}
    .hero-image {{ margin: 18px 0 0; padding: 18px; text-align: center; background: var(--secondary-soft); border-radius: 8px; }}
    .hero-image img {{ max-width: 100%; max-height: 680px; object-fit: contain; }}
    .summary, .content-section {{ margin-top: 18px; padding: 24px 28px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
    .summary {{ border-left: 6px solid var(--secondary); background: var(--secondary-soft); }}
    .eyebrow, h2 {{ color: var(--accent-dark); font-weight: 900; }}
    .eyebrow {{ font-size: 14px; margin-bottom: 7px; }}
    h2 {{ margin: 0 0 14px; font-size: 24px; line-height: 1.35; }}
    h3 {{ margin: 20px 0 10px; color: var(--secondary-dark); font-size: 19px; font-weight: 900; line-height: 1.4; }}
    h4 {{ margin: 18px 0 9px; color: #694d1f; font-size: 17px; font-weight: 900; line-height: 1.4; }}
    p {{ margin: 0 0 13px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    ul {{ margin: 8px 0 14px; padding-left: 24px; }}
    li {{ margin: 5px 0; }}
    strong {{ color: var(--accent-dark); font-weight: 900; }}
    em {{ color: var(--secondary-dark); font-style: normal; font-weight: 750; }}
    .source-accent {{ color: #c9344b; font-weight: 900; }}
    .article-module {{ border-left: 7px solid var(--accent); }}
    .article-module.tone-1 {{ border-left-color: var(--secondary); background: #f4f9fc; }}
    .article-module.tone-2 {{ border-left-color: #c28c2e; background: #fffaf0; }}
    .article-module.tone-3 {{ border-left-color: #598f7d; background: #f3faf7; }}
    .article-module-child > h2 {{ color: var(--secondary-dark); font-size: 22px; }}
    .module-divider {{ margin-top: 22px; padding: 15px 22px; background: var(--accent-soft); border-top: 5px solid var(--accent); border-bottom: 1px solid var(--line); }}
    .module-divider h2 {{ margin: 0; color: var(--accent-dark); }}
    .module-divider.tone-1 {{ background: var(--secondary-soft); border-top-color: var(--secondary); }}
    .module-divider.tone-2 {{ background: var(--warm); border-top-color: #c28c2e; }}
    .module-body > :last-child {{ margin-bottom: 0; }}
    .module-body li > ul, .module-body li > ol {{ margin-top: 4px; margin-bottom: 8px; }}
    .definition-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; margin-top: 4px; }}
    .definition-item {{ min-width: 0; padding: 14px 16px 12px; background: rgba(255,255,255,.58); border-left: 4px solid var(--secondary); break-inside: avoid; page-break-inside: avoid; }}
    .definition-item:nth-child(3n + 2) {{ border-left-color: var(--accent); }}
    .definition-item:nth-child(3n) {{ border-left-color: #c28c2e; }}
    .definition-title {{ margin-bottom: 8px; color: var(--accent-dark); font-size: 17px; font-weight: 900; line-height: 1.4; }}
    .definition-item ul, .definition-item ol {{ margin: 0; padding-left: 21px; }}
    .definition-item li {{ margin: 4px 0; font-size: 14px; line-height: 1.58; }}
    .module-rich {{ margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--line); }}
    .module-rich:first-child {{ margin-top: 0; padding-top: 0; border-top: 0; }}
    .section-context {{ margin: -8px 0 14px; color: var(--muted); font-size: 13px; }}
    blockquote {{ margin: 14px 0; padding: 18px 20px; background: var(--accent-soft); border-left: 5px solid var(--accent); border-radius: 6px; }}
    .quote-text {{ font-size: 17px; white-space: pre-wrap; }}
    cite {{ display: block; margin-top: 10px; color: var(--muted); font-style: normal; }}
    .table-wrap {{ width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 14px; }}
    th, td {{ min-width: 96px; padding: 10px 11px; border: 1px solid var(--line); text-align: left; vertical-align: top; white-space: pre-wrap; }}
    th {{ position: sticky; top: 0; z-index: 1; background: var(--secondary-soft); color: #294550; }}
    tbody tr:nth-child(even) {{ background: #fafbfc; }}
    .table-icon {{ display: inline-block; width: 42px; height: 42px; margin: 0 8px 4px 0; object-fit: contain; vertical-align: middle; }}
    .tech-intro {{ margin-bottom: 14px; padding: 14px 16px; background: var(--warm); border-radius: 6px; }}
    footer {{ margin-top: 18px; padding: 18px 4px; color: var(--muted); font-size: 13px; }}
    @media (max-width: 680px) {{
      .page {{ width: min(100% - 16px, 1180px); margin-top: 8px; }}
      header, .summary, .content-section {{ padding: 20px 18px; }}
      h1 {{ font-size: 30px; }}
      h2 {{ font-size: 21px; }}
      .definition-grid {{ grid-template-columns: 1fr; }}
      th, td {{ min-width: 86px; padding: 8px; font-size: 13px; }}
    }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ width: 100%; margin: 0; }}
      header, .summary, .content-section {{ break-inside: avoid-page; box-shadow: none; }}
      a {{ color: inherit; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div class="wiki-name">{html.escape(wiki_name)}</div>
      <h1>{html.escape(title)}</h1>
      <div class="source">来源：<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noreferrer">{html.escape(source_url)}</a></div>
      {hero_image}
    </header>
    {summary_html}
    {content_html}
    {media_html}
    <footer>生成时间：{html.escape(generated_at)} · 本文档保留查询时取得的完整可解析内容。</footer>
  </main>
</body>
</html>
"""
    digest = hashlib.sha256(
        f"{wiki_name}\0{title}\0{source_url}\0{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:10]
    path = output_dir / f"{_safe_filename(wiki_name)}-{_safe_filename(title)}-{digest}.html"
    path.write_text(document, encoding="utf-8")
    return path
