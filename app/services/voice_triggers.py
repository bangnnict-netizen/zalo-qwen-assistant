"""Voice-message wake phrases (separate from text bot_tags)."""

from __future__ import annotations

import re

VOICE_TRIGGERS: list[str] = ["@bot", "@byron", "bot ơi", "trợ lý ơi", "boss ơi"]
VOICE_REPLY_PREFIX = "(Em nghe từ tin nhắn thoại) "
VOICE_LOG_PREFIX = "[voice] "


def contains_voice_trigger(text: str, triggers: list[str] | None = None) -> bool:
    """Return True if transcript contains any voice wake phrase (case-insensitive)."""
    haystack = text.casefold()
    for trigger in triggers or VOICE_TRIGGERS:
        if trigger.casefold() in haystack:
            return True
    return False


def strip_voice_triggers(text: str, triggers: list[str] | None = None) -> str:
    """Remove matched voice wake phrases and normalize whitespace."""
    cleaned = text
    for trigger in triggers or VOICE_TRIGGERS:
        pattern = re.compile(re.escape(trigger), re.IGNORECASE)
        cleaned = pattern.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
