from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any

_BRACKET_HEADING_RE = re.compile(r"^【(.+?)】$")
_SUBHEADING_RE = re.compile(r"^◆\s*(.+)$")
_COLON_HEADING_RE = re.compile(r"^(.{1,72}?)[：:]$")
_LIST_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<number>\d+[.、])|(?P<bullet>[-*•]))\s+(?P<text>.+)$"
)
_URL_RE = re.compile(r"(https?://[^\s<]+)")


def inline_markup_html(value: str, *, linkify: bool = True) -> str:
    """Render the small, parser-owned emphasis dialect as safe HTML."""

    escaped = html.escape(str(value or ""), quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    if linkify:
        escaped = _URL_RE.sub(
            lambda match: (
                f'<a href="{html.escape(match.group(1), quote=True)}" '
                f'target="_blank" rel="noreferrer">{match.group(1)}</a>'
            ),
            escaped,
        )
    return escaped


def _list_depth(indent: str) -> int:
    expanded = indent.replace("\t", "  ")
    return max(0, len(expanded) // 2)


def _parse_list(lines: list[str]) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, list[dict[str, Any]]]] = [(-1, roots)]
    for raw_line in lines:
        match = _LIST_LINE_RE.match(raw_line)
        if match is None:
            continue
        depth = _list_depth(match.group("indent"))
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        target = stack[-1][1]
        item = {
            "text": match.group("text").strip(),
            "ordered": bool(match.group("number")),
            "children": [],
        }
        target.append(item)
        stack.append((depth, item["children"]))
    return roots


def _list_html(items: list[dict[str, Any]], *, ordered: bool = False) -> str:
    if not items:
        return ""
    tag = "ol" if ordered else "ul"
    rows: list[str] = []
    for item in items:
        children = list(item.get("children", []) or [])
        child_html = _list_html(
            children,
            ordered=bool(children and all(child.get("ordered") for child in children)),
        )
        rows.append(
            "<li><span>"
            + inline_markup_html(str(item.get("text") or ""))
            + "</span>"
            + child_html
            + "</li>"
        )
    return f"<{tag}>" + "".join(rows) + f"</{tag}>"


def _definition_grid_html(items: list[dict[str, Any]]) -> str:
    panels: list[str] = []
    for item in items:
        children = list(item.get("children", []) or [])
        child_html = _list_html(
            children,
            ordered=bool(children and all(child.get("ordered") for child in children)),
        )
        panels.append(
            '<article class="definition-item">'
            '<div class="definition-title">'
            + inline_markup_html(str(item.get("text") or ""))
            + "</div>"
            + child_html
            + "</article>"
        )
    return '<div class="definition-grid">' + "".join(panels) + "</div>"


def structured_text_html(
    text: str, *, force_definition_grid: bool = False
) -> tuple[str, bool]:
    """Render paragraphs and nested lists without flattening skill groups."""

    lines = str(text or "").replace("\r\n", "\n").split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    index = 0
    has_definition_grid = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        value = " ".join(part.strip() for part in paragraph if part.strip()).strip()
        paragraph.clear()
        if value:
            output.append(f"<p>{inline_markup_html(value)}</p>")

    while index < len(lines):
        raw_line = lines[index].rstrip()
        if not raw_line.strip():
            flush_paragraph()
            index += 1
            continue
        if _LIST_LINE_RE.match(raw_line):
            flush_paragraph()
            list_lines: list[str] = []
            while index < len(lines) and _LIST_LINE_RE.match(lines[index].rstrip()):
                list_lines.append(lines[index].rstrip())
                index += 1
            items = _parse_list(list_lines)
            nested_count = sum(bool(item.get("children")) for item in items)
            if (
                len(items) >= 3
                and nested_count >= 2
                or force_definition_grid
                and nested_count >= 1
            ):
                output.append(_definition_grid_html(items))
                has_definition_grid = True
            else:
                output.append(
                    _list_html(
                        items,
                        ordered=bool(items and all(item.get("ordered") for item in items)),
                    )
                )
            continue
        paragraph.append(raw_line)
        index += 1
    flush_paragraph()
    return "\n".join(output), has_definition_grid


def parse_detail_blocks(detail_text: str) -> list[dict[str, Any]]:
    """Split translated wiki text into ordered main and child modules."""

    if not str(detail_text or "").strip():
        return []

    blocks: list[dict[str, Any]] = []
    title = ""
    level = 1
    source_order = 0
    explicit_heading = False
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        body = "\n".join(line.rstrip() for line in lines).strip()
        lines = []
        if not body and not explicit_heading:
            return
        body_html, definition_grid = structured_text_html(body)
        line_count = len([line for line in body.splitlines() if line.strip()])
        blocks.append(
            {
                "title": title.strip(),
                "body": body,
                "body_html": body_html,
                "level": level,
                "source_order": source_order,
                "tone_index": max(0, source_order - 1) % 4,
                "group_only": bool(explicit_heading and not body),
                "definition_grid": definition_grid,
                "compact": bool(
                    body
                    and not definition_grid
                    and level > 1
                    and len(body) <= 320
                    and line_count <= 8
                ),
            }
        )

    for raw_line in str(detail_text or "").replace("\r\n", "\n").split("\n"):
        stripped = raw_line.strip()
        heading = _BRACKET_HEADING_RE.fullmatch(stripped)
        subheading = _SUBHEADING_RE.fullmatch(stripped)
        colon_heading = (
            _COLON_HEADING_RE.fullmatch(stripped)
            if stripped and not _LIST_LINE_RE.match(raw_line)
            else None
        )
        if heading or subheading or colon_heading:
            flush()
            source_order += 1
            explicit_heading = True
            if heading:
                title = heading.group(1).strip()
                level = 1
            elif subheading:
                title = subheading.group(1).strip()
                level = 2
            else:
                title = colon_heading.group(1).strip()
                level = 1
            continue
        lines.append(raw_line)
    flush()
    return blocks


def build_content_groups(
    detail_blocks: list[dict[str, Any]],
    rich_sections: list[dict[str, Any]],
    *,
    preserve_source_order: bool = False,
) -> list[dict[str, Any]]:
    """Keep narrative and structured data in one source-ordered module stream."""

    groups: list[dict[str, Any]] = []
    def source_order(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    by_order: dict[float, dict[str, Any]] = {}
    for block in detail_blocks:
        group = deepcopy(block)
        group["rich_sections"] = []
        groups.append(group)
        order = source_order(group.get("source_order", 0))
        if order > 0:
            by_order[order] = group

    deferred: list[dict[str, Any]] = []
    for index, raw_section in enumerate(rich_sections, start=1):
        section = deepcopy(raw_section)
        order = source_order(section.get("source_order", 0))
        if preserve_source_order and order > 0 and order in by_order:
            by_order[order]["rich_sections"].append(section)
            continue
        deferred.append(
            {
                "title": str(
                    section.get("display_title") or section.get("title") or "资料"
                ).strip(),
                "body": "",
                "body_html": "",
                "level": 1,
                "source_order": order if preserve_source_order and order > 0 else 0,
                "tone_index": (
                    max(0, int(order) - 1) % 4
                    if preserve_source_order and order > 0
                    else (len(groups) + index - 1) % 4
                ),
                "group_only": False,
                "definition_grid": False,
                "compact": False,
                "rich_sections": [section],
            }
        )

    groups.extend(deferred)
    if preserve_source_order:
        groups.sort(
            key=lambda group: (
                source_order(group.get("source_order", 0)) <= 0,
                source_order(group.get("source_order", 0)),
            )
        )

    output: list[dict[str, Any]] = []
    for group in groups:
        attachments = list(group.get("rich_sections", []) or [])
        attachments.sort(
            key=lambda section: (
                int(section.get("table_order", 0) or 0),
                str(section.get("title") or section.get("display_title") or ""),
            )
        )
        group["rich_sections"] = attachments
        if group.get("group_only") and attachments:
            group["group_only"] = False
        if group.get("body") or attachments or group.get("group_only"):
            output.append(group)
    return output
