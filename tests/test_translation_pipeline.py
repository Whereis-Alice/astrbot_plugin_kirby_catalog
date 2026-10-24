"""Translation reliability regressions using the actual provider orchestration."""

import asyncio
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_kirby_catalog.main import KirbyCatalogPlugin, WikiTranslationIncompleteError
from astrbot_plugin_kirby_catalog.terminology import (
    KirbyTerminologyStore,
    TerminologyEntry,
    terminology_document,
)


class ScriptedContext:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.payloads = []

    async def get_current_chat_provider_id(self, _umo):
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["prompt"].split("JSON：\n", 1)[1])
        self.payloads.append(payload)
        result = self.responder(payload, len(self.calls))
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, dict):
            return SimpleNamespace(completion_text=json.dumps(result, ensure_ascii=False), finish_reason="stop")
        return result


class TranslationPipelineTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, responder, **config):
        plugin = KirbyCatalogPlugin.__new__(KirbyCatalogPlugin)
        plugin.config = {
            "wikirby_translate_provider_id": "provider",
            "shinkaku_translate_provider_id": "provider",
            "wikirby_cache_ttl_seconds": 3600,
            "shinkaku_cache_ttl_seconds": 3600,
            "terminology_enabled": False,
            "wiki_translation_retry_depth": 1,
            **config,
        }
        plugin.context = ScriptedContext(responder)
        return plugin

    async def translate(self, plugin, source, *, japanese=False):
        return await plugin._wiki_translate_text(
            SimpleNamespace(unified_msg_origin="test:group:translation"),
            source,
            enabled=True,
            provider_key="shinkaku_translate_provider_id" if japanese else "wikirby_translate_provider_id",
            source_name="卡比真格攻略 Wiki" if japanese else "WiKirby",
            require_complete=True,
            context_label="regression",
            stage="details",
        )

    async def test_canonical_term_with_chinese_digit_does_not_fail_numeric_validation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundled = root / "terms.json"
            bundled.write_text(json.dumps(terminology_document([
                TerminologyEntry.from_mapping({
                    "term_id": "special:guardian-angel-landia",
                    "category": "special",
                    "zh_cn": "四首守护神：龙帝亚",
                    "en": "Guardian Angel: Landia",
                    "ja": "4つ首の守り神：ランディア",
                    "zh_status": "official",
                })
            ]), ensure_ascii=False), encoding="utf-8")
            plugin = self.make_plugin(
                lambda payload, _call: {
                    identifier: body.replace("が登場します。", "登场。")
                    for identifier, body in payload.items()
                },
                terminology_enabled=True,
            )
            plugin.terminology = KirbyTerminologyStore(bundled, root / "overrides.json")
            result = await self.translate(plugin, "4つ首の守り神：ランディアが登場します。", japanese=True)
            self.assertIn("四首守护神：龙帝亚", result)
            self.assertIn("登场", result)
            self.assertEqual(len(plugin.context.calls), 1)
            payload = next(iter(plugin.context.payloads[0].values()))
            self.assertIn("KTERM", payload)
            self.assertNotIn("KNUM", payload)

    async def test_numeric_placeholder_next_to_latin_word_is_still_preserved(self):
        def responder(payload, _call):
            return {
                identifier: "攻击hit" + re.search(r"⟦KNUM-[0-9]{4}⟧", body).group(0) + "。"
                for identifier, body in payload.items()
            }

        plugin = self.make_plugin(responder)
        result = await self.translate(plugin, "攻撃は30Fです。", japanese=True)
        self.assertEqual(result, "攻击hit30F。")
        self.assertEqual(len(plugin.context.calls), 1)

    async def test_retry_only_requests_missing_unit_and_retains_original_separators(self):
        def responder(payload, call):
            if call == 1:
                return {next(iter(payload)): "第一段。"}
            return {identifier: "第二段。" for identifier in payload}

        plugin = self.make_plugin(responder)
        result = await self.translate(plugin, " First paragraph.\r\n\r\n  Second paragraph.\n")
        self.assertEqual(result, " 第一段。\r\n\r\n  第二段。\n")
        self.assertEqual(len(plugin.context.payloads[0]), 2)
        self.assertEqual(len(plugin.context.payloads[1]), 1)
        self.assertEqual(set(plugin.context.payloads[1]), {"u0002"})
        self.assertNotIn("First paragraph.", plugin.context.payloads[1].values())

    async def test_successful_unit_survives_failed_strict_query_for_next_request(self):
        def responder(payload, call):
            return {next(iter(payload)): "第一段。" if call == 1 else "第二段。"}

        plugin = self.make_plugin(responder, wiki_translation_retry_depth=0)
        source = "First paragraph.\n\nSecond paragraph."
        with self.assertRaises(WikiTranslationIncompleteError):
            await self.translate(plugin, source)
        self.assertFalse(plugin._wiki_translation_cache)
        self.assertEqual(len(plugin._wiki_translation_unit_cache), 1)
        result = await self.translate(plugin, source)
        self.assertEqual(result, "第一段。\n\n第二段。")
        self.assertEqual(plugin.context.payloads[1], {"u0002": "Second paragraph."})

    async def test_concurrent_identical_queries_share_one_provider_call(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def responder(payload, _call):
            started.set()
            await release.wait()
            return {identifier: "共享译文。" for identifier in payload}

        plugin = self.make_plugin(responder)
        first = asyncio.create_task(self.translate(plugin, "A short paragraph."))
        await started.wait()
        second = asyncio.create_task(self.translate(plugin, "A short paragraph."))
        await asyncio.sleep(0)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), ["共享译文。", "共享译文。"])
        self.assertEqual(len(plugin.context.calls), 1)
        await asyncio.sleep(0)
        self.assertFalse(plugin._wiki_translation_tasks)

    async def test_single_call_timeout_cancels_provider_and_fails_boundedly(self):
        cancelled = asyncio.Event()

        async def responder(_payload, _call):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        plugin = self.make_plugin(responder, wiki_translation_retry_depth=0)
        with patch.object(plugin, "_wiki_translation_timeout_seconds", return_value=0.01):
            with self.assertRaises(WikiTranslationIncompleteError) as raised:
                await asyncio.wait_for(self.translate(plugin, "A short paragraph."), timeout=1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(plugin.context.calls), 1)
        self.assertIn("TimeoutError", str(raised.exception))

    async def test_raw_completion_length_finish_reason_cannot_cache_truncated_result(self):
        def responder(payload, _call):
            return SimpleNamespace(
                completion_text=json.dumps({identifier: "表面完整的译文。" for identifier in payload}, ensure_ascii=False),
                raw_completion={"choices": [{"finish_reason": "length"}]},
            )

        plugin = self.make_plugin(responder, wiki_translation_retry_depth=0)
        with self.assertRaises(WikiTranslationIncompleteError) as raised:
            await self.translate(plugin, "A short paragraph.")
        self.assertIn("provider_incomplete:length", str(raised.exception))
        self.assertFalse(plugin._wiki_translation_cache)
        self.assertFalse(plugin._wiki_translation_unit_cache)

    def test_finish_reason_supports_sdk_response_object_and_direct_reason(self):
        object_response = SimpleNamespace(raw_completion=SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="max_tokens")]
        ))
        self.assertEqual(KirbyCatalogPlugin._wiki_response_finish_reason(object_response), "max_tokens")
        direct_response = SimpleNamespace(finish_reason="stop", raw_completion={"choices": [{"finish_reason": "length"}]})
        self.assertEqual(KirbyCatalogPlugin._wiki_response_finish_reason(direct_response), "stop")

    def test_split_does_not_cut_url_or_decimal_frame_fact(self):
        plugin = self.make_plugin(lambda payload, _call: payload)
        url = "https://example.test/video/2026?id=123456789"
        source = "前文" * 20 + url + "\n" + "说明" * 20 + "123456.75F" + "之后" * 20
        chunks = plugin._wiki_translation_chunks(source, 50, source_name="卡比真格攻略 Wiki")
        self.assertEqual("".join(chunks), source)
        self.assertTrue(any(url in chunk for chunk in chunks))
        self.assertTrue(any("123456.75F" in chunk for chunk in chunks))

    async def test_legacy_chunk_path_still_rejects_missing_completion_marker(self):
        plugin = self.make_plugin(lambda payload, _call: payload)

        async def legacy_generate(**_kwargs):
            return SimpleNamespace(completion_text="只有译文，没有完整性标记。", finish_reason="stop")

        plugin.context.llm_generate = legacy_generate
        translated, error, _metrics = await plugin._wiki_translate_chunk_once(
            provider_id="provider", source_name="WiKirby", chunk="A short paragraph.",
            chunk_label="1/1", require_complete=True,
        )
        self.assertFalse(translated)
        self.assertIn("completion_marker_missing", error)

    async def test_legacy_retry_path_can_repair_missing_end_marker(self):
        plugin = self.make_plugin(lambda payload, _call: payload)
        prompts = []

        async def legacy_generate(**kwargs):
            prompts.append(kwargs["prompt"])
            source = kwargs["prompt"].split("原文：\n", 1)[1]
            begin, end = source.splitlines()[0], source.splitlines()[-1]
            translated = f"{begin}\n【概览】\n已经完整翻译的正文。"
            if len(prompts) > 1:
                translated += f"\n{end}"
            return SimpleNamespace(completion_text=translated, finish_reason="stop")

        plugin.context.llm_generate = legacy_generate
        translated, complete, calls, failures = await plugin._wiki_translate_chunk_with_retry(
            provider_id="provider", source_name="WiKirby",
            chunk="【Overview】\nA short paragraph.", chunk_label="1/1",
            require_complete=True, depth=0, max_depth=1, min_chunk_chars=800,
        )
        self.assertTrue(complete)
        self.assertEqual(calls, 2)
        self.assertEqual(failures, [])
        self.assertIn("已经完整翻译", translated)
        self.assertIn("这是一次纠错重译", prompts[1])


if __name__ == "__main__":
    unittest.main()
