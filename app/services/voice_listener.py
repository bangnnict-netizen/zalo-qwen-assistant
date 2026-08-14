"""Inbound Zalo voice message listener: download, transcribe, trigger gate."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.transcription_service import TranscriptionService
from app.core.debug_events import record_event
from urllib.parse import urlparse
import os
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
        logger.warning("Zalo client session missing, cannot download voice from %s", url)
        return None

    def _fetch() -> bytes | None:
        try:
            with session.get(url) as response:
                try:
                    content = response.content
                    status = response.status_code
                    ctype = response.headers.get("Content-Type")
                    path = urlparse(url).path or ""
                    ext = os.path.splitext(path)[1].lower()
                    record_event(
                        {
                            "debug": "voice_download",
                            "url": url,
                            "status": status,
                            "content_type": ctype,
                            "bytes": len(content) if content is not None else 0,
                            "ext": ext,
                        }
                    )
                except Exception:
                    # best-effort record
                    record_event({"debug": "voice_download_meta_failed", "url": url})
                if response.status_code == 200:
                    return content
        except Exception:
            logger.warning("Failed to download Zalo voice from %s", url, exc_info=True)
            record_event({"debug": "voice_download_exception", "url": url, "error": repr(Exception)})
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
            logger.warning("No audio bytes downloaded for voice URL %s", url)
            return None

        try:
            transcript = await self.transcription.transcribe(audio_bytes)
        except Exception:
            logger.exception("Unexpected error during transcription")
            return None

        if not transcript:
            logger.info("Transcription produced no text for voice URL %s", url)
            return None

        should_reply = contains_voice_trigger(transcript)
        question = strip_voice_triggers(transcript) if should_reply else ""
        return VoiceProcessResult(
            transcript=transcript,
            should_reply=should_reply,
            question=question,
        )
