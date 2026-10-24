import json
from pathlib import Path

import pytest
from astrbot_plugin_kirby_catalog.terminology import KirbyTerminologyStore

HISTORY_NOTE = (
    "*游戏开发初期的名称叫《春风啵啵啵》，后来改为《闪耀☆啵啵》"
    "（ティンクル☆ポポ/Twinkle Popo）。原定于1992年1月下旬发售，"
    "任天堂介入后，游戏名最终改为了《星之卡比》。"
)


@pytest.fixture()
def bundled_store(tmp_path):
    return KirbyTerminologyStore(
        Path(__file__).resolve().parents[1] / "resources" / "kirby_terminology.json",
        tmp_path / "overrides.json",
    )


def test_bundled_history_note_keeps_its_facts_instead_of_becoming_star_block(
    bundled_store,
):
    result = bundled_store.canonicalize(HISTORY_NOTE)
    protected = bundled_store.protect(HISTORY_NOTE)

    assert "原定于1992年1月下旬发售" in result
    assert "游戏开发初期的名称叫" in result
    assert "星星方块" not in result
    assert all(
        binding.term_id != "special:star-block" for binding in protected.bindings
    )


def test_bundled_misaligned_table_record_is_not_a_term(bundled_store):
    numbers = "121・213・382・630"
    filename = "File:Taiko_Kirby_Warp_Star_Costume.png"
    assert bundled_store.canonicalize(numbers) == numbers
    assert bundled_store.canonicalize(filename) == filename
    entry = bundled_store.entry("work:file-taiko-kirby-warp-star-costume-png")
    assert not entry.enabled


def test_loaded_override_cannot_protect_a_paragraph_as_one_name(tmp_path):
    path = tmp_path / "terms.json"
    path.write_text("[]", encoding="utf-8")
    store = KirbyTerminologyStore(path, tmp_path / "overrides.json")
    store.upsert(
        {
            "term_id": "special:star-block",
            "zh_cn": "星星方块",
            "en": "Star Block",
            "aliases_en": [HISTORY_NOTE],
            "aliases_ja": [HISTORY_NOTE],
        }
    )
    store.reload()

    assert store.canonicalize(HISTORY_NOTE) == HISTORY_NOTE
    assert not store.protect(HISTORY_NOTE).bindings
    assert store.canonicalize("Star Block") == "星星方块（Star Block）"


def test_numeric_separators_and_scores_are_not_names_in_loaded_data(tmp_path):
    values = ["121・213・382・630", "12:45.49", "34.17", "1/2", "１２１・２１３"]
    path = tmp_path / "terms.json"
    path.write_text(
        json.dumps(
            [
                {
                    "term_id": "bad-record",
                    "zh_cn": "错误名称",
                    "ja": values[0],
                    "aliases_ja": values[1:],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = KirbyTerminologyStore(path, tmp_path / "overrides.json")

    for value in values:
        assert store.canonicalize(value) == value
        assert not store.protect(value).bindings


def test_valid_japanese_name_with_digits_remains_matchable(bundled_store):
    name = "中ボス軍団4体"
    entry = bundled_store.entry("character:four-mid-bosses")
    assert bundled_store.canonicalize(name) == entry.canonical_label
    assert bundled_store.protect(name).matched_terms == 1


def test_long_complete_work_qualifier_remains_matchable(bundled_store):
    entry = bundled_store.entry("character:parastella-crystallis-genwel-meteonelfilis")
    alias = max(entry.aliases_en, key=len)
    assert len(alias) >= 100
    assert bundled_store.canonicalize(alias) == entry.canonical_label
    assert bundled_store.protect(alias).matched_terms == 1
