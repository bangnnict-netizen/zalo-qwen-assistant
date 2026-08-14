"""Groq Whisper transcription via OpenAI-compatible /audio/transcriptions."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3"
REQUEST_TIMEOUT = 60.0


class TranscriptionService:
    """Transcribe audio bytes with Groq Whisper."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._api_key = self._settings.groq_api_key

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "voice.m4a",
        content_type: str = "audio/mp4",
    ) -> str | None:
        """Return transcript text or None on failure."""
        if not audio_bytes:
            return None
        headers = {"Authorization": f"Bearer {self._api_key}"}
        files = {"file": (filename, audio_bytes, content_type)}
        data = {"model": WHISPER_MODEL}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    GROQ_TRANSCRIPTION_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )
        except httpx.TimeoutException:
            logger.warning("Groq transcription timed out")
            return None
        except httpx.RequestError:
            logger.warning("Groq transcription request failed")
            return None

        if response.status_code != 200:
            logger.warning("Groq transcription HTTP %s", response.status_code)
            return None

        try:
            payload = response.json()
            text = payload.get("text", "")
        except ValueError:
            logger.warning("Unexpected Groq transcription response")
            return None

        if not isinstance(text, str):
            return None
        cleaned = text.strip()
        return cleaned or None
