"""Groq LLM client via the OpenAI-compatible HTTP API (httpx, no SDK)."""

from __future__ import annotations

import logging
import re

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
BUSY_REPLY = (
    "Dạ em đang bận chút, anh/chị vui lòng thử lại sau 1 phút nhé!"
)
REQUEST_TIMEOUT = 30.0
TEMPERATURE = 0.7
MAX_TOKENS = 1024
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


class LLMService:
    """Call Groq chat completions with automatic primary → fallback retry."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._api_key = self._settings.groq_api_key
        self._primary_model = self._settings.llm_primary_model
        self._fallback_model = self._settings.llm_fallback_model

    async def chat(self, question: str, system: str = "") -> dict[str, str]:
        """Return `answer` and `model_used`. Never raises on Groq failures."""
        messages = _build_messages(question, system)
        for model in (self._primary_model, self._fallback_model):
            result = await self._complete(model, messages)
            if result is not None:
                return result
        return {"answer": BUSY_REPLY, "model_used": "none"}

    async def _complete(
        self,
        model: str,
        messages: list[dict[str, str]],
    ) -> dict[str, str] | None:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }
        # Qwen reasoning models otherwise dump long <think> traces that hit max_tokens.
        if "qwen" in model.lower():
            body["reasoning_effort"] = "none"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    GROQ_CHAT_URL,
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException:
            logger.warning("Groq timed out for model %s", model)
            return None
        except httpx.RequestError:
            logger.warning("Groq request error for model %s", model)
            return None

        if _is_retryable_status(response.status_code):
            logger.warning(
                "Groq returned HTTP %s for model %s",
                response.status_code,
                model,
            )
            return None

        try:
            payload = response.json()
            answer = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            logger.warning("Unexpected Groq response for model %s", model)
            return None

        if not isinstance(answer, str):
            logger.warning("Empty Groq content for model %s", model)
            return None

        cleaned = strip_thinking(answer)
        if not cleaned:
            logger.warning("Answer empty after stripping think blocks for %s", model)
            return None

        return {"answer": cleaned, "model_used": model}


def strip_thinking(text: str) -> str:
    """Remove leaked model thinking blocks (closed or truncated) before return."""
    cleaned = THINK_BLOCK_RE.sub("", text)
    cleaned = THINK_UNCLOSED_RE.sub("", cleaned)
    return cleaned.strip()


def _build_messages(question: str, system: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": question})
    return messages


def _is_retryable_status(status_code: int) -> bool:
    """Retry on 429, 404 (missing model), 5xx, and other non-success HTTP codes."""
    return status_code != 200
