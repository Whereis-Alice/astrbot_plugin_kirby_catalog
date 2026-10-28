"""Reference regressions for persisted message IDs and OneBot image hashes."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_kirby_catalog.ally_card_refs import AllyCardReferences


class AllyCardReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "references.json"
        self.image = self.root / ("kirby-ally-card-" + "a" * 32 + ".png")
        self.image.write_bytes(b"unique card contents")
        self.entry = {
            "id": 539,
            "entry_key": "wikirby-page:2705",
            "filename": "Roly-Poly.png",
        }

    def test_message_binding_is_scoped_and_survives_reload(self):
        refs = AllyCardReferences(self.path)
        key = refs.register_image(self.image, self.entry)
        refs.bind_message(key, "onebot:bot1:group1", 66)
        restored = AllyCardReferences(self.path)
        self.assertEqual(
            restored.quoted_target("onebot:bot1:group1", ["66"], []),
            "wikirby-page:2705",
        )
        self.assertEqual(restored.quoted_target("onebot:bot2:group1", ["66"], []), "")
        self.assertEqual(restored.quoted_target("onebot:bot1:group2", ["66"], []), "")

    def test_uploaded_image_md5_resolves_without_caption_or_receipt(self):
        refs = AllyCardReferences(self.path)
        refs.register_image(self.image, self.entry)
        digest = (
            hashlib.md5(self.image.read_bytes(), usedforsecurity=False)
            .hexdigest()
            .upper()
        )
        url = f"https://gchat.qpic.cn/gchatpic_new/0/0-0-{digest}/0"
        self.assertEqual(
            refs.quoted_target("another-group", [], [url]), "wikirby-page:2705"
        )

    def test_prepared_image_hash_keeps_the_same_stable_target(self):
        refs = AllyCardReferences(self.path)
        key = refs.register_image(self.image, self.entry)
        prepared = self.root / "kirby-delivery.jpg"
        prepared.write_bytes(b"prepared card contents")
        refs.register_image(prepared, {}, key)
        digest = hashlib.md5(prepared.read_bytes(), usedforsecurity=False).hexdigest()
        self.assertEqual(
            AllyCardReferences(self.path).quoted_target("scope", [], [digest + ".jpg"]),
            "wikirby-page:2705",
        )

    def test_corrupt_cache_does_not_prevent_new_references(self):
        self.path.write_text("broken JSON", encoding="utf-8")
        refs = AllyCardReferences(self.path)
        refs.register_image(self.image, self.entry)
        self.assertEqual(
            refs.quoted_target("scope", [], [str(self.image)]), "wikirby-page:2705"
        )

    def test_expired_records_are_removed_and_live_references_remain(self):
        self.path.write_text(
            json.dumps(
                {
                    "cards": {"expired": {"target": "wrong", "created": 0}},
                    "messages": {"scope\u00001": "expired"},
                }
            ),
            encoding="utf-8",
        )
        refs = AllyCardReferences(self.path)
        self.assertEqual(refs.quoted_target("scope", ["1"], []), "")
        refs.register_image(self.image, self.entry)
        self.assertEqual(len(refs.cards), 1)

    def test_reference_cache_has_a_bound_and_discards_removed_message_bindings(self):
        refs = AllyCardReferences(self.path)
        refs.cards = {
            str(i): {"created": time.time() + i, "target": str(i)} for i in range(5)
        }
        refs.messages = {"scope\u0000" + str(i): str(i) for i in range(5)}
        with patch("astrbot_plugin_kirby_catalog.ally_card_refs.MAX_CARDS", 2):
            refs._prune()
        self.assertEqual(set(refs.cards), {"3", "4"})
        self.assertEqual(set(refs.messages.values()), {"3", "4"})

    def test_unrelated_image_and_missing_message_do_not_guess_an_entry(self):
        refs = AllyCardReferences(self.path)
        refs.register_image(self.image, self.entry)
        self.assertEqual(
            refs.quoted_target("scope", ["unknown"], ["b" * 32 + ".png"]), ""
        )
