from __future__ import annotations

import json

import pytest

from astrbot_plugin_kirby_catalog.terminology import (
    KirbyTerminologyStore,
    TerminologyEntry,
    TerminologyPlaceholderError,
    terminology_document,
)


def _entry(
    term_id: str,
    zh: str,
    en: str,
    ja: str,
    *,
    category: str = "character",
    aliases_en: tuple[str, ...] = (),
    priority: int = 100,
    match_case: bool = False,
) -> TerminologyEntry:
    return TerminologyEntry.from_mapping(
        {
            "term_id": term_id,
            "category": category,
            "zh_cn": zh,
            "en": en,
            "ja": ja,
            "aliases_en": aliases_en,
            "zh_status": "official",
            "sources": ["test"],
            "priority": priority,
            "enabled": True,
            "match_case": match_case,
        }
    )


@pytest.fixture()
def terminology_store(tmp_path):
    entries = [
        _entry("character:kirby", "卡比", "Kirby", "カービィ"),
        _entry(
            "character:meta-knight",
            "魅塔骑士",
            "Meta Knight",
            "メタナイト",
        ),
        _entry(
            "ability:water",
            "水能力",
            "Water",
            "ウォーター",
            category="ability",
            match_case=True,
        ),
    ]
    bundled = tmp_path / "kirby_terminology.json"
    overrides = tmp_path / "terminology_overrides.json"
    bundled.write_text(
        json.dumps(terminology_document(entries), ensure_ascii=False),
        encoding="utf-8",
    )
    return KirbyTerminologyStore(bundled, overrides)


def test_protect_restore_uses_canonical_bilingual_names(terminology_store):
    protected = terminology_store.protect(
        "Kirby fought Meta Knight. Kirby won. カービィ returned."
    )

    assert protected.matched_terms == 2
    assert protected.matched_occurrences == 4
    assert "Kirby" not in protected.protected_text
    assert protected.canonical_source() == (
        "卡比（Kirby） fought 魅塔骑士（Meta Knight）. "
        "卡比（Kirby） won. 卡比（Kirby） returned."
    )


def test_longest_match_and_existing_bilingual_text_do_not_duplicate(
    terminology_store,
):
    text = "魅塔骑士（Meta Knight） met Meta Knight and Knight."

    assert terminology_store.canonicalize(text) == (
        "魅塔骑士（Meta Knight） met 魅塔骑士（Meta Knight） and Knight."
    )


def test_urls_and_lowercase_ambiguous_words_are_not_replaced(terminology_store):
    text = (
        "https://wikirby.com/wiki/Kirby says water is useful, "
        "but Water grants an ability to Kirby."
    )

    assert terminology_store.canonicalize(text) == (
        "https://wikirby.com/wiki/Kirby says water is useful, "
        "but 水能力（Water） grants an ability to 卡比（Kirby）."
    )


def test_generic_english_terms_only_match_title_case(tmp_path):
    entries = [
        _entry("special:gear", "齿轮", "gear", "ギア", category="special"),
        _entry("work:arena", "斗技场", "Arena", "闘技場", category="work"),
    ]
    bundled = tmp_path / "terms.json"
    bundled.write_text(
        json.dumps(terminology_document(entries), ensure_ascii=False),
        encoding="utf-8",
    )
    store = KirbyTerminologyStore(bundled, tmp_path / "overrides.json")

    assert store.canonicalize("gear in an arena; Gear in Arena") == (
        "gear in an arena; 齿轮（gear） in 斗技场（Arena）"
    )


def test_ascii_word_boundaries_prevent_partial_name_matches(terminology_store):
    assert terminology_store.canonicalize("Kirbyville Kirby Kirby's") == (
        "Kirbyville 卡比（Kirby） 卡比（Kirby）'s"
    )


def test_placeholder_validation_rejects_lost_tokens(terminology_store):
    protected = terminology_store.protect("Kirby and Kirby")
    assert protected.matched_terms == 1
    translated = protected.protected_text.replace(protected.bindings[0].token, "", 1)

    with pytest.raises(TerminologyPlaceholderError):
        protected.restore(translated)


def test_placeholder_restore_repairs_markdown_and_escaped_legacy_shape(
    terminology_store,
):
    protected = terminology_store.protect("Kirby met Meta Knight.")
    kirby, meta = protected.bindings
    kirby_parts = protected._token_parts(kirby.token)
    meta_parts = protected._token_parts(meta.token)
    assert kirby_parts is not None
    assert meta_parts is not None
    translated = (
        f"**KTERM\\_{kirby_parts[0]}\\_{kirby_parts[1]}** 遇见了 "
        f"__KTERM_{meta_parts[0]}_{meta_parts[1]}__。"
    )

    restored = protected.restore(translated)

    assert restored == "卡比（Kirby） 遇见了 魅塔骑士（Meta Knight）。"


def test_placeholder_restore_accepts_known_surplus_without_losing_translation(
    terminology_store,
):
    protected = terminology_store.protect("Kirby met Kirby. Kirby waved. Kirby won. Kirby left.")
    token = protected.bindings[0].token
    assert protected.bindings[0].count == 5
    translated = protected.protected_text + f" 附注：{token}。"

    restored = protected.restore(translated)

    assert "附注：卡比（Kirby）。" in restored
    assert restored.count("卡比（Kirby）") == 6


def test_placeholder_validation_rejects_unknown_tokens(terminology_store):
    protected = terminology_store.protect("Kirby")
    translated = protected.protected_text + " ⟦KTERM-DEADBEEF-9999⟧"

    with pytest.raises(TerminologyPlaceholderError, match="unknown"):
        protected.restore(translated)


def test_structured_restore_validates_across_nested_values(terminology_store):
    protected = terminology_store.protect(
        json.dumps(
            [{"title": "Kirby", "rows": [["Meta Knight", "Kirby"]]}],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    payload = json.loads(protected.protected_text)

    restored = protected.restore_object(payload)

    assert restored == [
        {
            "title": "卡比（Kirby）",
            "rows": [["魅塔骑士（Meta Knight）", "卡比（Kirby）"]],
        }
    ]


def test_override_reload_changes_revision_and_restore_returns_to_base(
    terminology_store,
):
    original_revision = terminology_store.revision
    original = terminology_store.entry("character:kirby")
    assert original is not None
    changed = original.to_mapping()
    changed["zh_cn"] = "粉色恶魔"

    terminology_store.upsert(changed)

    assert terminology_store.revision != original_revision
    assert terminology_store.origin("character:kirby") == "override"
    assert terminology_store.canonicalize("Kirby") == "粉色恶魔（Kirby）"

    terminology_store.restore("character:kirby")
    assert terminology_store.origin("character:kirby") == "bundled"
    assert terminology_store.canonicalize("Kirby") == "卡比（Kirby）"


def test_lazy_store_loads_on_first_use_and_can_reload_after_release(tmp_path):
    bundled = tmp_path / "terms.json"
    bundled.write_text(
        json.dumps(
            terminology_document(
                [_entry("character:kirby", "卡比", "Kirby", "カービィ")]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = KirbyTerminologyStore(
        bundled,
        tmp_path / "overrides.json",
        lazy=True,
    )

    assert store.loaded is False
    assert store.canonicalize("Kirby") == "卡比（Kirby）"
    assert store.loaded is True

    store.release()

    assert store.loaded is False
    assert store.canonicalize("Kirby") == "卡比（Kirby）"
    assert store.loaded is True


def test_conflicts_are_reported_and_priority_selects_winner(tmp_path):
    entries = [
        _entry("character:a", "甲", "Shared", "エー", priority=10),
        _entry("character:b", "乙", "Other", "ビー", aliases_en=("Shared",), priority=20),
    ]
    bundled = tmp_path / "terms.json"
    bundled.write_text(
        json.dumps(terminology_document(entries), ensure_ascii=False),
        encoding="utf-8",
    )
    store = KirbyTerminologyStore(bundled, tmp_path / "overrides.json")

    assert store.canonicalize("Shared") == "乙（Other）"
    conflicts = store.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["alias"] == "Shared"
    assert {row["term_id"] for row in conflicts[0]["entries"]} == {
        "character:a",
        "character:b",
    }


def test_json_and_csv_exports_round_trip(terminology_store, tmp_path):
    json_bytes = terminology_store.export_json()
    csv_bytes = terminology_store.export_csv()

    imported_json = KirbyTerminologyStore(
        tmp_path / "missing.json",
        tmp_path / "json-overrides.json",
    )
    imported_json.import_json(json_bytes)
    assert imported_json.stats()["entries"] == 3

    imported_csv = KirbyTerminologyStore(
        tmp_path / "also-missing.json",
        tmp_path / "csv-overrides.json",
    )
    imported_csv.import_csv(csv_bytes)
    assert imported_csv.stats()["entries"] == 3
    assert imported_csv.entry("character:kirby").zh_cn == "卡比"
