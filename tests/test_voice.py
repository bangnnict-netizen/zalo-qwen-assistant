"""Tests for voice triggers, transcription, listener, and bridge integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.services.message_pipeline import MessagePipeline
from app.services.router_service import MessageRouter
from app.services.transcription_service import (
    GROQ_TRANSCRIPTION_URL,
    TranscriptionService,
    WHISPER_MODEL,
)
from app.services.voice_listener import (
    VoiceListener,
    VoiceProcessResult,
    download_voice_audio,
    extract_voice_download_url,
    is_voice_message,
)
from app.services.voice_triggers import (
    VOICE_LOG_PREFIX,
    VOICE_REPLY_PREFIX,
    contains_voice_trigger,
    strip_voice_triggers,
)
from app.services.zalo_bridge_real import RealZaloBridge


def test_contains_voice_trigger_case_insensitive() -> None:
    assert contains_voice_trigger("Bot ơi mấy giờ nhà máy nghỉ làm?")
    assert contains_voice_trigger("@BYRON giúp em")
    assert contains_voice_trigger("Trợ lý ơi cho hỏi")
    assert not contains_voice_trigger("mấy giờ nhà máy nghỉ làm")


def test_strip_voice_triggers() -> None:
    assert (
        strip_voice_triggers("bot ơi mấy giờ nhà máy nghỉ làm")
        == "mấy giờ nhà máy nghỉ làm"
    )
    assert strip_voice_triggers("@bot   @byron  xin chào") == "xin chào"


def test_is_voice_message_and_extract_url() -> None:
    voice_obj = MagicMock()
    voice_obj.msgType = "chat.voice"
    voice_obj.content = {"href": "https://cdn.zalo.me/voice/test.m4a"}

    assert is_voice_message(voice_obj) is True
    assert extract_voice_download_url(voice_obj) == "https://cdn.zalo.me/voice/test.m4a"

    text_obj = MagicMock()
    text_obj.msgType = "webchat"
    assert is_voice_message(text_obj) is False


@pytest.mark.asyncio
async def test_transcription_service_posts_multipart_to_groq() -> None:
    request = httpx.Request("POST", GROQ_TRANSCRIPTION_URL)
    response = httpx.Response(
        200,
        json={"text": "bot ơi mấy giờ nhà máy nghỉ làm"},
        request=request,
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    service = TranscriptionService(Settings(groq_api_key="test-key"))
    with patch("app.services.transcription_service.httpx.AsyncClient", return_value=cm):
        transcript = await service.transcribe(b"audio-bytes")

    assert transcript == "bot ơi mấy giờ nhà máy nghỉ làm"
    call = client.post.await_args
    assert call.args[0] == GROQ_TRANSCRIPTION_URL
    assert call.kwargs["data"] == {"model": WHISPER_MODEL}
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert "file" in call.kwargs["files"]


@pytest.mark.asyncio
async def test_voice_listener_replies_when_trigger_present() -> None:
    transcription = AsyncMock()
    transcription.transcribe.return_value = "bot ơi mấy giờ nhà máy nghỉ làm"
    listener = VoiceListener(transcription_service=transcription)

    message_object = MagicMock()
    message_object.msgType = "chat.voice"
    message_object.content = {"href": "https://cdn.zalo.me/voice/test.m4a"}

    zalo_client = MagicMock()
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b"audio"
    session.get.return_value.__enter__.return_value = response
    zalo_client._state._session = session

    result = await listener.process(message_object, zalo_client)

    assert result == VoiceProcessResult(
        transcript="bot ơi mấy giờ nhà máy nghỉ làm",
        should_reply=True,
        question="mấy giờ nhà máy nghỉ làm",
    )
    transcription.transcribe.assert_awaited_once_with(b"audio")


@pytest.mark.asyncio
async def test_voice_listener_silent_without_trigger() -> None:
    transcription = AsyncMock()
    transcription.transcribe.return_value = "hôm nay trời đẹp quá"
    listener = VoiceListener(transcription_service=transcription)

    message_object = MagicMock()
    message_object.msgType = "chat.voice"
    message_object.content = {"voiceUrl": "https://cdn.zalo.me/voice/x.m4a"}

    zalo_client = MagicMock()
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b"audio"
    session.get.return_value.__enter__.return_value = response
    zalo_client._state._session = session

    result = await listener.process(message_object, zalo_client)

    assert result is not None
    assert result.should_reply is False
    assert result.question == ""


@pytest.mark.asyncio
async def test_pipeline_handle_voice_routes_question() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    settings = Settings(
        groq_api_key="test",
        allowed_internal_group_ids=["group_internal_demo"],
        allowed_customer_group_ids=[],
    )
    pipeline = MessagePipeline(router=router, settings=settings)

    result = await pipeline.handle_voice(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
        },
        "mấy giờ nhà máy nghỉ làm",
    )

    assert result is not None
    assert result["answer"] == "16h30"
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ nhà máy nghỉ làm",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_bridge_voice_message_logs_and_replies_with_prefix() -> None:
    repo = MagicMock()
    repo.load_session.return_value = None
    pipeline = AsyncMock(spec=MessagePipeline)
    pipeline.is_declared_group.return_value = True
    pipeline.handle_voice.return_value = {"answer": "16h30"}

    voice_listener = AsyncMock()
    voice_listener.process.return_value = VoiceProcessResult(
        transcript="bot ơi mấy giờ nhà máy nghỉ làm",
        should_reply=True,
        question="mấy giờ nhà máy nghỉ làm",
    )

    bridge = RealZaloBridge(
        pipeline=pipeline,
        repo=repo,
        settings=Settings(groq_api_key="test"),
        voice_listener=voice_listener,
        sleep=lambda _s: None,
    )
    bridge._client = MagicMock()
    bridge.send = AsyncMock()

    message_object = MagicMock()
    message_object.msgType = "chat.voice"
    message_object.content = {"href": "https://cdn.zalo.me/voice/test.m4a"}

    await bridge._handle_voice_event(
        message_object=message_object,
        author_id="user1",
        sender_name="Anh Bằng",
        group_id="group_internal_demo",
    )

    repo.log_message.assert_called_once_with(
        group_id="group_internal_demo",
        sender_id="user1",
        sender_name="Anh Bằng",
        gender="unknown",
        text=f"{VOICE_LOG_PREFIX}bot ơi mấy giờ nhà máy nghỉ làm",
    )
    pipeline.handle_voice.assert_awaited_once()
    bridge.send.assert_awaited_once_with(
        "group_internal_demo",
        f"{VOICE_REPLY_PREFIX}16h30",
    )


@pytest.mark.asyncio
async def test_bridge_voice_without_trigger_logs_only() -> None:
    repo = MagicMock()
    pipeline = AsyncMock(spec=MessagePipeline)
    pipeline.is_declared_group.return_value = True

    voice_listener = AsyncMock()
    voice_listener.process.return_value = VoiceProcessResult(
        transcript="hôm nay trời đẹp",
        should_reply=False,
        question="",
    )

    bridge = RealZaloBridge(
        pipeline=pipeline,
        repo=repo,
        settings=Settings(groq_api_key="test"),
        voice_listener=voice_listener,
        sleep=lambda _s: None,
    )
    bridge._client = MagicMock()
    bridge.send = AsyncMock()

    await bridge._handle_voice_event(
        message_object=MagicMock(msgType="chat.voice"),
        author_id="user1",
        sender_name="Anh Bằng",
        group_id="group_internal_demo",
    )

    repo.log_message.assert_called_once()
    pipeline.handle_voice.assert_not_awaited()
    bridge.send.assert_not_awaited()


def test_dm_voice_messages_are_ignored() -> None:
    repo = MagicMock()
    repo.load_session.return_value = None
    pipeline = AsyncMock(spec=MessagePipeline)
    pipeline.is_declared_group.return_value = True
    voice_listener = AsyncMock()

    bridge = RealZaloBridge(
        pipeline=pipeline,
        repo=repo,
        settings=Settings(groq_api_key="test"),
        voice_listener=voice_listener,
        sleep=lambda _s: None,
    )
    client = MagicMock()
    bridge._client = client
    bridge._bind_message_handler()

    class FakeThreadType:
        USER = MagicMock(name="USER")
        GROUP = MagicMock(name="GROUP")

    voice_obj = MagicMock(dName="Anh Bằng")
    voice_obj.msgType = "chat.voice"
    voice_obj.content = {"href": "https://cdn.zalo.me/voice/test.m4a"}

    with patch("zlapi.models.ThreadType", FakeThreadType):
        client.onMessage(
            "mid",
            "user1",
            "",
            voice_obj,
            "user1",
            FakeThreadType.USER,
        )

    repo.log_message.assert_not_called()
    voice_listener.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_voice_audio_uses_client_session() -> None:
    zalo_client = MagicMock()
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b"voice-data"
    session.get.return_value.__enter__.return_value = response
    zalo_client._state._session = session

    data = await download_voice_audio(zalo_client, "https://cdn.zalo.me/voice/x.m4a")

    assert data == b"voice-data"
    session.get.assert_called_once_with("https://cdn.zalo.me/voice/x.m4a")
