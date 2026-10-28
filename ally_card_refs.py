"""Bounded, persistent references for cards with a short human caption."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

MAX_CARDS = 8192
MAX_AGE_SECONDS = 90 * 24 * 3600
_IMAGE_KEY = re.compile(r"(?<![a-fA-F0-9])[a-fA-F0-9]{32}(?![a-fA-F0-9])")


class AllyCardReferences:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.cards: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, str] = {}
        try:
            if self.path.stat().st_size <= 8 * 1024 * 1024:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.cards = {
                        str(k): v
                        for k, v in data.get("cards", {}).items()
                        if isinstance(v, dict) and v.get("target")
                    }
                    self.messages = {
                        str(k): str(v)
                        for k, v in data.get("messages", {}).items()
                        if str(v) in self.cards
                    }
        except (OSError, ValueError, AttributeError):
            self.cards, self.messages = {}, {}
        self._prune()

    def _prune(self) -> None:
        cutoff = time.time() - MAX_AGE_SECONDS
        self.cards = {
            k: v
            for k, v in self.cards.items()
            if isinstance(v.get("created"), (int, float)) and v["created"] >= cutoff
        }
        if len(self.cards) > MAX_CARDS:
            newest = sorted(
                self.cards, key=lambda k: self.cards[k]["created"], reverse=True
            )
            self.cards = {key: self.cards[key] for key in newest[:MAX_CARDS]}
        self.messages = {k: v for k, v in self.messages.items() if v in self.cards}
        if len(self.messages) > MAX_CARDS * 2:
            self.messages = dict(list(self.messages.items())[-MAX_CARDS * 2:])

    def _save(self) -> None:
        self._prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.stem}-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {"version": 1, "cards": self.cards, "messages": self.messages},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _keys(values: list[str]) -> set[str]:
        return {
            m.group().casefold()
            for value in values
            for m in _IMAGE_KEY.finditer(str(value))
        }

    def register_image(
        self, path: Path | str, entry: dict[str, Any], card_key: str = ""
    ) -> str:
        path = Path(path)
        raw = path.read_bytes()
        digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        with self._lock:
            key = card_key or uuid.uuid4().hex
            target = str(
                entry.get("entry_key") or entry.get("filename") or entry.get("id") or ""
            )
            if not target and key not in self.cards:
                return ""
            record = self.cards.setdefault(
                key, {"target": target, "created": time.time(), "images": []}
            )
            images = sorted(
                set(record.get("images", [])) | self._keys([str(path)]) | {digest}
            )
            if images == record["images"]:
                return key
            record["images"] = images
            self._save()
            return key

    def image_card(self, values: list[str]) -> str:
        keys = self._keys(values)
        if not keys:
            return ""
        with self._lock:
            for key, record in reversed(list(self.cards.items())):
                if keys.intersection(record.get("images", [])):
                    return key
        return ""

    def bind_message(self, card_key: str, scope: str, message_id: Any) -> None:
        message_id = str(message_id or "")
        if not scope or not message_id:
            return
        with self._lock:
            if card_key not in self.cards:
                return
            self.messages[f"{scope}\0{message_id}"] = card_key
            self._save()

    def quoted_target(
        self, scope: str, message_ids: list[str], image_values: list[str]
    ) -> str:
        with self._lock:
            key = next(
                (
                    self.messages.get(f"{scope}\0{mid}", "")
                    for mid in message_ids
                    if self.messages.get(f"{scope}\0{mid}")
                ),
                "",
            )
            key = key or self.image_card(image_values)
            return str(self.cards.get(key, {}).get("target") or "")
