"""Inbound Zalo voice message listener: download, transcribe, trigger gate."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.transcription_service import TranscriptionService
from app.services.voice_triggers import (
    contains_voice_trigger,
    strip_voice_triggers,
)

logger = logging.getLogger(__name__)

VOICE_MSG_TYPE = "chat.voice"
_URL_KEYS = ("href", "voiceUrl", "m4aUrl", "fileUrl", "url")


@dataclass(frozen=True)
class VoiceProcessResult:
    transcript: str
    should_reply: bool
    question: str


def is_voice_message(message_object: Any) -> bool:
    return getattr(message_object, "msgType", None) == VOICE_MSG_TYPE


def extract_voice_download_url(message_object: Any) -> str | None:
    """Extract a downloadable audio URL from a Zalo voice message object."""
    content = getattr(message_object, "content", None)
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("http"):
            return stripped
        try:
            content = json.loads(stripped)
        except json.JSONDecodeError:
            return None

    if content is None:
        return None

    if isinstance(content, dict):
        items = content.items()
    else:
        items = ((key, getattr(content, key, None)) for key in _URL_KEYS)

    for _key, value in items:
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


async def download_voice_audio(client: Any, url: str) -> bytes | None:
    """Download voice bytes using the authenticated Zalo client session."""
    session = getattr(getattr(client, "_state", None), "_session", None)
    if session is None:
        return None

    def _fetch() -> bytes | None:
        try:
            with session.get(url) as response:
                if response.status_code == 200:
                    return response.content
        except Exception:
            logger.warning("Failed to download Zalo voice from %s", url, exc_info=True)
            return None
        return None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


class VoiceListener:
    """Process declared-group voice messages into transcripts and gated questions."""

    def __init__(self, transcription_service: TranscriptionService | None = None) -> None:
        self.transcription = transcription_service or TranscriptionService()

    async def process(
        self,
        message_object: Any,
        zalo_client: Any,
    ) -> VoiceProcessResult | None:
        if not is_voice_message(message_object):
            return None

        url = extract_voice_download_url(message_object)
        if not url:
            logger.warning("Voice message missing download URL")
            return None

        audio_bytes = await download_voice_audio(zalo_client, url)
        if not audio_bytes:
            return None

        transcript = await self.transcription.transcribe(audio_bytes)
        if not transcript:
            return None

        should_reply = contains_voice_trigger(transcript)
        question = strip_voice_triggers(transcript) if should_reply else ""
        return VoiceProcessResult(
            transcript=transcript,
            should_reply=should_reply,
            question=question,
        )
