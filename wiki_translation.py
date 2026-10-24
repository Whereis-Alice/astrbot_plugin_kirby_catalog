"""Lossless text units and partial JSON results for Wiki translation.

Only natural-language unit bodies go through the model.  Separators stay local
so a response cannot merge, reorder, or drop the source's paragraph structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TextUnit:
    id: str
    text: str
    prefix: str = ""
    suffix: str = ""


def split_units(
    text: str,
    split_long: Callable[[str], list[str]] | None = None,
) -> list[TextUnit]:
    """Keep each nonblank line's whitespace while optionally splitting its body.

    ``split_long`` must preserve its input exactly when its fragments are
    joined.  It can protect terminology, URLs, or formatting spans before
    choosing sentence boundaries.  Whitespace-only input has no model units.
    """

    units: list[TextUnit] = []
    pending = ""
    for line in str(text or "").splitlines(keepends=True):
        if not line.strip():
            if units:
                units[-1] = replace(units[-1], suffix=units[-1].suffix + line)
            else:
                pending += line
            continue
        body = line.strip()
        leading = line[: len(line) - len(line.lstrip())]
        trailing = line[len(line.rstrip()) :]
        parts = split_long(body) if split_long else [body]
        if not parts or any(not isinstance(part, str) for part in parts):
            raise ValueError(
                "translation splitter must return nonempty string fragments"
            )
        if "".join(parts) != body:
            raise ValueError("translation splitter changed the source text")
        line_start = len(units)
        pending += leading
        for part in parts:
            if not part.strip():
                if len(units) > line_start:
                    units[-1] = replace(units[-1], suffix=units[-1].suffix + part)
                else:
                    pending += part
                continue
            part_leading = part[: len(part) - len(part.lstrip())]
            part_trailing = part[len(part.rstrip()) :]
            prefix = pending + part_leading
            pending = ""
            suffix = part_trailing
            units.append(
                TextUnit(f"u{len(units) + 1:04d}", part.strip(), prefix, suffix)
            )
        if len(units) > line_start:
            units[-1] = replace(units[-1], suffix=units[-1].suffix + trailing)
    return units


def rebuild_units(
    units: Sequence[TextUnit],
    translations: Mapping[str, str],
    *,
    fallback_to_source: bool = True,
) -> str:
    """Restore source order and separators, using source for absent translations."""

    pieces: list[str] = []
    for unit in units:
        if unit.id in translations:
            body = translations[unit.id]
        elif fallback_to_source:
            body = unit.text
        else:
            raise KeyError(unit.id)
        pieces.append(unit.prefix + body + unit.suffix)
    return "".join(pieces)


def unit_payload(units: Iterable[TextUnit]) -> dict[str, str]:
    """Build the ID-to-body JSON object sent to the model."""

    return {unit.id: unit.text for unit in units}


def pack_units(units: Iterable[TextUnit], max_chars: int) -> list[list[TextUnit]]:
    """Batch by serialized payload size without cutting a unit.

    Pass units containing prepared/protected bodies to include placeholder
    expansion in the budget.  An oversized indivisible unit is kept alone.
    """

    limit = max(1, int(max_chars))
    batches: list[list[TextUnit]] = []
    current: list[TextUnit] = []
    for unit in units:
        candidate = [*current, unit]
        size = len(
            json.dumps(
                unit_payload(candidate), ensure_ascii=False, separators=(",", ":")
            )
        )
        if current and size > limit:
            batches.append(current)
            current = [unit]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


class _ObjectPairs(list):
    """Retain duplicate JSON object keys instead of silently overwriting them."""


def parse_translation_response(
    value: str,
    expected_ids: Iterable[str],
) -> tuple[dict[str, str], list[str]]:
    """Accept a JSON object inside optional prose/fences, preserving valid items.

    Duplicate IDs are unusable even when both copies agree.  Unknown IDs and
    invalid values are errors; they never overwrite valid expected items.
    Missing IDs are reported so callers retry only their failed units.
    """

    expected = tuple(dict.fromkeys(str(identifier) for identifier in expected_ids))
    expected_set = set(expected)
    text = str(value or "").strip()
    decoder = json.JSONDecoder(object_pairs_hook=_ObjectPairs)
    parsed = None
    for position, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            candidate, _end = decoder.raw_decode(text, position)
        except (ValueError, json.JSONDecodeError):
            continue
        parsed = candidate
        break
    errors: list[str] = []
    mapping: dict[str, str] = {}
    if not isinstance(parsed, _ObjectPairs):
        errors.append("invalid_json_object")
    else:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for key, translated in parsed:
            if key in seen:
                duplicates.add(key)
                mapping.pop(key, None)
                errors.append(f"duplicate_id:{key}")
                continue
            seen.add(key)
            if key not in expected_set:
                errors.append(f"unknown_id:{key}")
                continue
            if not isinstance(translated, str):
                errors.append(f"non_string:{key}")
                continue
            if not translated.strip():
                errors.append(f"empty_translation:{key}")
                continue
            if key not in duplicates:
                mapping[key] = translated.strip()
    errors.extend(
        f"missing_id:{identifier}"
        for identifier in expected
        if identifier not in mapping
    )
    return mapping, errors
