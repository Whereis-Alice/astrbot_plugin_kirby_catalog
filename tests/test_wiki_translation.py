"""Regression tests for lossless units and independently recoverable translations."""

import json
import unittest

from astrbot_plugin_kirby_catalog.wiki_translation import (
    TextUnit,
    pack_units,
    parse_translation_response,
    rebuild_units,
    split_units,
    unit_payload,
)


class TranslationUnitTests(unittest.TestCase):
    def test_source_order_blank_lines_and_indentation_stay_local(self):
        source = "\n\t【見出し】  \r\n\r\n  本文。\t\n\n最後。  "
        units = split_units(source)
        self.assertEqual([unit.text for unit in units], ["【見出し】", "本文。", "最後。"])
        self.assertEqual(rebuild_units(units, {}), source)
        translated = {"u0003": "结尾。", "u0001": "【标题】", "u0002": "正文。"}
        self.assertEqual(
            rebuild_units(units, translated),
            "\n\t【标题】  \r\n\r\n  正文。\t\n\n结尾。  ",
        )

    def test_long_line_split_keeps_spaces_between_fragments(self):
        source = "  First sentence.  Second sentence.\r\n\nThird.\n"

        def splitter(body):
            if body.startswith("First"):
                return ["First sentence.", "  ", "Second sentence."]
            return [body]

        units = split_units(source, splitter)
        self.assertEqual([unit.text for unit in units], ["First sentence.", "Second sentence.", "Third."])
        self.assertEqual(rebuild_units(units, {}), source)
        self.assertEqual(
            rebuild_units(units, {"u0001": "第一句。", "u0002": "第二句。", "u0003": "第三句。"}),
            "  第一句。  第二句。\r\n\n第三句。\n",
        )

    def test_splitter_that_loses_source_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed the source"):
            split_units("source text", lambda _body: ["source", "text"])
        with self.assertRaises(ValueError):
            split_units("source text", lambda _body: [])

    def test_missing_translation_has_explicit_source_fallback(self):
        units = split_units("First.\nSecond.")
        self.assertEqual(rebuild_units(units, {"u0001": "第一句。"}), "第一句。\nSecond.")
        with self.assertRaises(KeyError):
            rebuild_units(units, {"u0001": "第一句。"}, fallback_to_source=False)

    def test_whitespace_only_text_needs_no_model_units(self):
        self.assertEqual(split_units("\n \t\r\n"), [])
        self.assertEqual(split_units(""), [])

    def test_pack_counts_real_json_escaping_and_placeholder_expansion(self):
        units = [
            TextUnit("u0001", '"quoted"\t⟦KNUM-0001⟧'),
            TextUnit("u0002", "⟦KTERM-ABCD1234-0001⟧ 中文"),
            TextUnit("u0003", "third"),
        ]
        first_size = len(json.dumps(unit_payload(units[:1]), ensure_ascii=False, separators=(",", ":")))
        second_size = len(json.dumps(unit_payload(units[1:2]), ensure_ascii=False, separators=(",", ":")))
        limit = max(first_size, second_size)
        batches = pack_units(units, limit)
        self.assertEqual([unit for batch in batches for unit in batch], units)
        self.assertEqual(batches[0], [units[0]])
        for batch in batches:
            self.assertLessEqual(
                len(json.dumps(unit_payload(batch), ensure_ascii=False, separators=(",", ":"))),
                limit,
            )

    def test_oversized_unit_is_kept_whole_and_alone(self):
        units = [TextUnit("u0001", "x" * 100), TextUnit("u0002", "y")]
        self.assertEqual(pack_units(units, 30), [[units[0]], [units[1]]])
        self.assertEqual(pack_units([], 30), [])


class TranslationResponseTests(unittest.TestCase):
    def test_fenced_json_with_explanation_and_reordered_ids(self):
        mapping, errors = parse_translation_response(
            '以下是翻译：\n```json\n{"u0002":" 第二段。 ","u0001":"第一段。"}\n```\n翻译结束。',
            ["u0001", "u0002"],
        )
        self.assertEqual(mapping, {"u0002": "第二段。", "u0001": "第一段。"})
        self.assertEqual(errors, [])

    def test_missing_id_preserves_successful_unit_for_targeted_retry(self):
        mapping, errors = parse_translation_response('{"u0001":"已译。"}', ["u0001", "u0002"])
        self.assertEqual(mapping, {"u0001": "已译。"})
        self.assertEqual(errors, ["missing_id:u0002"])

    def test_duplicate_id_is_rejected_without_losing_other_units(self):
        mapping, errors = parse_translation_response(
            '{"u0001":"初次。","u0002":"有效。","u0001":"覆盖。","u0001":"再覆盖。"}',
            ["u0001", "u0002"],
        )
        self.assertEqual(mapping, {"u0002": "有效。"})
        self.assertIn("duplicate_id:u0001", errors)
        self.assertIn("missing_id:u0001", errors)

    def test_unknown_id_never_enters_reconstructed_content(self):
        mapping, errors = parse_translation_response(
            '{"u0001":"有效。","u9999":"新增正文。"}', ["u0001"]
        )
        self.assertEqual(mapping, {"u0001": "有效。"})
        self.assertEqual(errors, ["unknown_id:u9999"])

    def test_empty_and_nonstring_values_are_failed_units(self):
        mapping, errors = parse_translation_response(
            '{"u0001":" ","u0002":17,"u0003":false,"u0004":{"text":"错误格式"},"u0005":"有效。"}',
            ["u0001", "u0002", "u0003", "u0004", "u0005"],
        )
        self.assertEqual(mapping, {"u0005": "有效。"})
        self.assertIn("empty_translation:u0001", errors)
        for identifier in ("u0002", "u0003", "u0004"):
            self.assertIn(f"non_string:{identifier}", errors)
        for identifier in ("u0001", "u0002", "u0003", "u0004"):
            self.assertIn(f"missing_id:{identifier}", errors)

    def test_arrays_and_invalid_json_cannot_supply_a_nested_object(self):
        for value in ('[{"u0001":"nested"}]', '```json\n[{"u0001":"nested"}]\n```', 'plain text', '{"u0001":'):
            with self.subTest(value=value):
                mapping, errors = parse_translation_response(value, ["u0001"])
                self.assertEqual(mapping, {})
                self.assertIn("invalid_json_object", errors)
                self.assertIn("missing_id:u0001", errors)


if __name__ == "__main__":
    unittest.main()
