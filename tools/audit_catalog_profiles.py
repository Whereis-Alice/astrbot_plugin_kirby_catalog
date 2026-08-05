from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"ZXQ\d{6}QXZ")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
ENGLISH_PROSE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "but",
        "by",
        "can",
        "for",
        "from",
        "has",
        "have",
        "he",
        "her",
        "his",
        "in",
        "is",
        "it",
        "of",
        "on",
        "she",
        "that",
        "the",
        "their",
        "they",
        "this",
        "to",
        "was",
        "were",
        "when",
        "while",
        "will",
        "with",
    }
)
ENGLISH_PROSE_ANCHORS = frozenset(
    {
        "are",
        "can",
        "has",
        "have",
        "he",
        "her",
        "his",
        "is",
        "it",
        "she",
        "that",
        "their",
        "they",
        "this",
        "was",
        "we",
        "were",
        "will",
        "you",
    }
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_items(path: Path) -> list[dict[str, Any]]:
    raw = read_json(path)
    items = raw.get("items", []) if isinstance(raw, dict) else []
    return [item for item in items if isinstance(item, dict)]


def contains_english_prose(value: str) -> bool:
    without_parenthetical_names = re.sub(
        r"[（(][^（）()\n]*[A-Za-z][^（）()\n]*[）)]", "", str(value or "")
    )
    for span in re.split(r"[\u3400-\u9fff]", without_parenthetical_names):
        words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", span)
        if len(words) < 8:
            continue
        folded = [word.casefold() for word in words]
        common = sum(word in ENGLISH_PROSE_WORDS for word in folded)
        anchors = sum(word in ENGLISH_PROSE_ANCHORS for word in folded)
        if common >= 3 and anchors:
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit the generated Kirby catalog profile database."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=1353)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = catalog_items(args.catalog)
    raw_profiles = read_json(args.profiles)
    profiles = raw_profiles.get("items", {}) if isinstance(raw_profiles, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}

    fatal: list[dict[str, Any]] = []
    suspicious_english: list[dict[str, Any]] = []
    catalog_keys: list[str] = []
    catalog_ids: list[int] = []
    for entry in catalog:
        entry_key = str(entry.get("entry_key") or "").strip()
        catalog_id = int(entry.get("id", 0) or 0)
        catalog_keys.append(entry_key)
        catalog_ids.append(catalog_id)
        profile = profiles.get(entry_key)
        if not isinstance(profile, dict):
            fatal.append(
                {"catalog_id": catalog_id, "entry_key": entry_key, "reason": "资料缺失"}
            )
            continue
        description = str(profile.get("description_zh") or "").strip()
        reasons: list[str] = []
        if int(profile.get("catalog_id", 0) or 0) != catalog_id:
            reasons.append("资料编号与 catalog 不一致")
        if str(profile.get("entry_key") or "") != entry_key:
            reasons.append("资料 entry_key 不一致")
        if not description:
            reasons.append("简体中文简介为空")
        if description and not CJK_RE.search(description):
            reasons.append("简介不含中文")
        if TOKEN_RE.search(description):
            reasons.append("简介残留术语占位符")
        if "\ufffd" in description:
            reasons.append("简介含 Unicode 替换字符")
        if str(profile.get("status") or "") != "matched":
            reasons.append("资料状态不是 matched")
        if not str(profile.get("source_url") or "").startswith(
            "https://wikirby.com/wiki/"
        ):
            reasons.append("WiKirby 来源链接缺失")
        if int(profile.get("source_revision", 0) or 0) <= 0:
            reasons.append("WiKirby 来源修订号缺失")
        if reasons:
            fatal.append(
                {
                    "catalog_id": catalog_id,
                    "entry_key": entry_key,
                    "name": entry.get("name", ""),
                    "reason": "；".join(reasons),
                }
            )

        latin = len(LATIN_RE.findall(description))
        cjk = len(CJK_RE.findall(description))
        if contains_english_prose(description):
            suspicious_english.append(
                {
                    "catalog_id": catalog_id,
                    "entry_key": entry_key,
                    "name": entry.get("name", ""),
                    "latin_chars": latin,
                    "cjk_chars": cjk,
                }
            )

    profile_keys = {str(key) for key in profiles}
    catalog_key_set = set(catalog_keys)
    extra_profiles = sorted(profile_keys - catalog_key_set)
    if len(catalog) != args.expected_count:
        fatal.append(
            {"reason": (f"catalog 条目数为 {len(catalog)}，预期 {args.expected_count}")}
        )
    if len(profiles) != args.expected_count:
        fatal.append(
            {"reason": (f"资料条目数为 {len(profiles)}，预期 {args.expected_count}")}
        )
    if len(catalog_keys) != len(catalog_key_set) or any(
        not key for key in catalog_keys
    ):
        fatal.append({"reason": "catalog 含空白或重复 entry_key"})
    if len(catalog_ids) != len(set(catalog_ids)):
        fatal.append({"reason": "catalog 含重复编号"})
    if extra_profiles:
        fatal.append({"reason": "资料含 catalog 外条目", "entry_keys": extra_profiles})

    report = {
        "catalog_entries": len(catalog),
        "profile_entries": len(profiles),
        "unique_source_pages": len(
            {
                int(profile.get("pageid", 0) or 0)
                for profile in profiles.values()
                if isinstance(profile, dict)
            }
        ),
        "fatal_count": len(fatal),
        "suspicious_english_count": len(suspicious_english),
        "fatal": fatal,
        "suspicious_english": suspicious_english,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
