"""Unit tests for LLMService fallback logic. httpx is mocked — no live API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.services.llm_service import BUSY_REPLY, GROQ_CHAT_URL, LLMService

PRIMARY = "primary-model"
FALLBACK = "fallback-model"


def _settings() -> Settings:
    return Settings(
        groq_api_key="test-key-not-real",
        llm_primary_model=PRIMARY,
        llm_fallback_model=FALLBACK,
    )


def _ok_response(content: str) -> httpx.Response:
    request = httpx.Request("POST", GROQ_CHAT_URL)
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


def _status_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", GROQ_CHAT_URL)
    return httpx.Response(
        status_code,
        json={"error": {"message": "failed"}},
        request=request,
    )


def _patch_client(side_effect: list[object]) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.post = AsyncMock(side_effect=side_effect)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


def _run(coro):
    return asyncio.run(coro)


def _posted_models(client: MagicMock) -> list[str]:
    return [call.kwargs["json"]["model"] for call in client.post.await_args_list]


def test_primary_success_does_not_call_fallback() -> None:
    cm, client = _patch_client([_ok_response("xin chào")])
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        result = _run(service.chat("Xin chào"))

    assert result == {"answer": "xin chào", "model_used": PRIMARY}
    assert _posted_models(client) == [PRIMARY]


@pytest.mark.parametrize(
    "primary_error",
    [
        httpx.TimeoutException("timed out"),
        _status_response(429),
        _status_response(404),
        _status_response(500),
        _status_response(502),
    ],
)
def test_primary_error_falls_back_to_secondary(primary_error: object) -> None:
    cm, client = _patch_client(
        [primary_error, _ok_response("trả lời từ fallback")],
    )
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        result = _run(service.chat("Câu hỏi?"))

    assert result == {
        "answer": "trả lời từ fallback",
        "model_used": FALLBACK,
    }
    assert _posted_models(client) == [PRIMARY, FALLBACK]


def test_both_models_fail_returns_busy_message() -> None:
    cm, client = _patch_client(
        [_status_response(429), httpx.TimeoutException("timed out")],
    )
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        result = _run(service.chat("Câu hỏi?"))

    assert result == {"answer": BUSY_REPLY, "model_used": "none"}
    assert _posted_models(client) == [PRIMARY, FALLBACK]


def test_system_prompt_included_when_provided() -> None:
    cm, client = _patch_client([_ok_response("ok")])
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        _run(service.chat("Câu hỏi?", system="Bạn là trợ lý."))

    body = client.post.await_args.kwargs["json"]
    assert body["messages"] == [
        {"role": "system", "content": "Bạn là trợ lý."},
        {"role": "user", "content": "Câu hỏi?"},
    ]
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 1024


def test_does_not_log_api_key(caplog: pytest.LogCaptureFixture) -> None:
    cm, _client = _patch_client([_status_response(500), _status_response(500)])
    service = LLMService(_settings())

    with (
        caplog.at_level("WARNING"),
        patch("app.services.llm_service.httpx.AsyncClient", return_value=cm),
    ):
        _run(service.chat("Câu hỏi?"))

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "test-key-not-real" not in combined
    assert "Bearer" not in combined


def test_strips_think_blocks_from_answer() -> None:
    raw = (
        "<think>\nThinking Process:\nI should introduce myself.\n</think>\n\n"
        "Xin chào! Em là trợ lý AI."
    )
    cm, _client = _patch_client([_ok_response(raw)])
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        result = _run(service.chat("Xin chào, bạn là ai?"))

    assert "<think>" not in result["answer"]
    assert "</think>" not in result["answer"]
    assert "Thinking Process" not in result["answer"]
    assert result["answer"] == "Xin chào! Em là trợ lý AI."
    assert result["model_used"] == PRIMARY


def test_strips_unclosed_think_blocks_from_answer() -> None:
    raw = "<think>\nStill thinking without close tag...\nVisible answer should remain."
    # After strip of unclosed <think>..., nothing usable remains → try fallback.
    cm, client = _patch_client(
        [
            _ok_response(raw),
            _ok_response("Em xin phép trả lời ngắn gọn."),
        ]
    )
    service = LLMService(_settings())

    with patch("app.services.llm_service.httpx.AsyncClient", return_value=cm):
        result = _run(service.chat("Câu hỏi?"))

    assert "<think>" not in result["answer"]
    assert result["answer"] == "Em xin phép trả lời ngắn gọn."
    assert result["model_used"] == FALLBACK
    assert _posted_models(client) == [PRIMARY, FALLBACK]
