"""Require AstrBot's configured command wake before matching plugin regexes."""

from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import CustomFilter


class AstrBotCommandFilter(CustomFilter):
    """Use the wake decision made with the current session's AstrBot config.

    AstrBot removes a matched wake prefix before evaluating handler filters.
    Mentions, quoted Bot replies and permitted private commands use the same
    wake flag as standard commands. Regexes must not strip another prefix.
    """

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        return bool(getattr(event, "is_at_or_wake_command", False))
