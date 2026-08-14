"""Groq Whisper transcription via OpenAI-compatible /audio/transcriptions."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings
from app.core.debug_events import record_event

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
        import subprocess
        import tempfile
        import os
        headers = {"Authorization": f"Bearer {self._api_key}"}
        # If input appears to be AAC ADTS (starts with 0xFF 0xF1/0xF9) or filename ext is .aac,
        # attempt local conversion to m4a via ffmpeg for Groq compatibility.
        tmp_input = None
        tmp_output = None
        try:
            name, ext = os.path.splitext(filename or "")
            is_aac_bytes = len(audio_bytes) >= 2 and audio_bytes[0:2] in (b"\xff\xf1", b"\xff\xf9")
            if is_aac_bytes or ext.lower() in (".aac",):
                tmp_input = tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".aac")
                tmp_input.write(audio_bytes)
                tmp_input.flush()
                tmp_input.close()
                tmp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a")
                tmp_output.close()
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    tmp_input.name,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    tmp_output.name,
                ]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    with open(tmp_output.name, "rb") as fh:
                        audio_bytes = fh.read()
                    filename = os.path.basename(tmp_output.name)
                    content_type = "audio/mp4"
                    record_event({"debug": "ffmpeg_converted", "from_ext": ext, "to": filename})
                except Exception as exc:
                    record_event({"debug": "ffmpeg_failed", "error": repr(exc)})
        finally:
            try:
                if tmp_input:
                    os.unlink(tmp_input.name)
            except Exception:
                pass
            try:
                if tmp_output:
                    os.unlink(tmp_output.name)
            except Exception:
                pass

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
                # record request metadata
                record_event(
                    {
                        "debug": "groq_request",
                        "url": GROQ_TRANSCRIPTION_URL,
                        "model": WHISPER_MODEL,
                        "file_name": filename,
                        "content_type": content_type,
                    }
                )
        except httpx.TimeoutException:
            logger.exception("Groq transcription timed out")
            record_event({"debug": "groq_timeout", "url": GROQ_TRANSCRIPTION_URL})
            return None
        except httpx.RequestError:
            logger.exception("Groq transcription request failed")
            record_event({"debug": "groq_request_error", "url": GROQ_TRANSCRIPTION_URL})
            return None

        if response.status_code != 200:
            # record full response body for diagnostics
            try:
                body = response.text
            except Exception:
                body = None
            record_event(
                {
                    "debug": "groq_non_200",
                    "status": response.status_code,
                    "body": body,
                    "url": GROQ_TRANSCRIPTION_URL,
                }
            )
            logger.warning("Groq transcription HTTP %s: %s", response.status_code, response.text)
            return None

        try:
            payload = response.json()
            text = payload.get("text", "")
        except ValueError:
            logger.exception("Unexpected Groq transcription response")
            return None

        if not isinstance(text, str):
            return None
        cleaned = text.strip()
        return cleaned or None
