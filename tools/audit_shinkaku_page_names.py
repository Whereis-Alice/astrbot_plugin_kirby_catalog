"""Audit the bundled three-language index for every 真格 Wiki page."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESOURCE = ROOT / "resources" / "shinkaku_page_names.json"
REQUIRED_FIELDS = (
    "id",
    "catalog_index",
    "source_index",
    "title_zh",
    "title_en",
    "title_ja",
    "url",
    "game_code",
    "section_zh",
    "section_en",
    "category",
    "zh_status",
)
ALLOWED_ZH_STATUSES = {"official", "official_reused", "translated", "unchanged"}


def _non_empty(value: Any) -> bool:
    return bool(str(value or "").strip())


def _normalise_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", text)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_payload(
    payload: dict[str, Any],
    *,
    expected_pages: int | None = None,
    source_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ["entries must be a list"]

    declared_total = payload.get("source", {}).get("total_pages")
    expected = expected_pages or declared_total
    if not isinstance(expected, int) or expected <= 0:
        errors.append("source.total_pages must be a positive integer")
        expected = len(entries)
    if len(entries) != expected:
        errors.append(f"entry count {len(entries)} != expected page count {expected}")

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} is not an object")
            continue
        missing = [field for field in REQUIRED_FIELDS if not _non_empty(entry.get(field))]
        if missing:
            errors.append(f"entry {index} missing fields: {', '.join(missing)}")
        if entry.get("zh_status") not in ALLOWED_ZH_STATUSES:
            errors.append(f"entry {index} has invalid zh_status")
        if not str(entry.get("url", "")).startswith(
            "https://seesaawiki.jp/kirby_shinkaku/d/"
        ):
            errors.append(f"entry {index} has an unexpected page URL")
        primary = {
            _normalise_name(value) for value in entry.get("primary_aliases", [])
        }
        for field in ("title_zh", "title_en", "title_ja"):
            if _normalise_name(entry.get(field)) not in primary:
                errors.append(f"entry {index} primary_aliases omits {field}")
        if str(entry.get("url", "")).strip() not in entry.get("primary_aliases", []):
            errors.append(f"entry {index} primary_aliases omits url")

    if isinstance(expected, int):
        for field in ("catalog_index", "source_index"):
            values = [entry.get(field) for entry in entries if isinstance(entry, dict)]
            if sorted(values) != list(range(1, expected + 1)):
                errors.append(f"{field} must be a complete 1..{expected} sequence")

    for field in ("id", "title_zh", "title_en", "title_ja", "url"):
        values = [entry.get(field) for entry in entries if isinstance(entry, dict)]
        if len(values) != len(set(values)):
            errors.append(f"{field} contains duplicates")
        if field.startswith("title_"):
            normalised = [_normalise_name(value) for value in values]
            if len(normalised) != len(set(normalised)):
                errors.append(f"{field} collides after query normalization")

    primary_owners: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id", ""))
        for alias in entry.get("primary_aliases", []):
            if str(alias).startswith("http"):
                continue
            primary_owners.setdefault(_normalise_name(alias), set()).add(entry_id)
    if any(len(owners) > 1 for owners in primary_owners.values()):
        errors.append("primary page names collide across entries after normalization")

    if source_snapshot is not None:
        snapshot_pages = source_snapshot.get("pages", [])
        expected_by_url = {
            str(page.get("href", "")).strip(): str(page.get("text", "")).strip()
            for page in snapshot_pages
            if isinstance(page, dict)
        }
        actual_by_url = {
            str(entry.get("url", "")).strip(): str(entry.get("title_ja", "")).strip()
            for entry in entries
            if isinstance(entry, dict)
        }
        if len(snapshot_pages) != expected:
            errors.append(
                f"source snapshot count {len(snapshot_pages)} != expected {expected}"
            )
        if len(expected_by_url) != len(snapshot_pages):
            errors.append("source snapshot contains duplicate or empty URLs")
        missing = sorted(set(expected_by_url) - set(actual_by_url))
        extra = sorted(set(actual_by_url) - set(expected_by_url))
        mismatched = sorted(
            url
            for url in set(expected_by_url) & set(actual_by_url)
            if expected_by_url[url] != actual_by_url[url]
        )
        if missing:
            errors.append(f"missing source pages: {len(missing)}")
        if extra:
            errors.append(f"extra pages not in source snapshot: {len(extra)}")
        if mismatched:
            errors.append(f"source title mismatches: {len(mismatched)}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource",
        type=Path,
        default=DEFAULT_RESOURCE,
        help="bundled shinkaku_page_names.json path",
    )
    parser.add_argument(
        "--source-snapshot",
        type=Path,
        help="optional page-list snapshot with a top-level pages array",
    )
    parser.add_argument(
        "--expected-pages",
        type=int,
        default=None,
        help="override the page count declared by the resource",
    )
    args = parser.parse_args()

    try:
        payload = _load_json(args.resource)
        snapshot = _load_json(args.source_snapshot) if args.source_snapshot else None
        errors = audit_payload(
            payload,
            expected_pages=args.expected_pages,
            source_snapshot=snapshot,
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("真格 Wiki 三语名称表审计失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    entries = payload["entries"]
    statuses: dict[str, int] = {}
    for entry in entries:
        status = str(entry["zh_status"])
        statuses[status] = statuses.get(status, 0) + 1
    print(
        json.dumps(
            {
                "status": "ok",
                "entries": len(entries),
                "catalog_index": [entries[0]["catalog_index"], entries[-1]["catalog_index"]],
                "zh_status": statuses,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
