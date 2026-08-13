"""Unit tests for RealZaloBridge with mocked Zalo client and Supabase."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.repositories.supabase_repo import SupabaseRepo
from app.services.message_pipeline import MessagePipeline
from app.services.zalo_bridge_real import RealZaloBridge, RECONNECT_BACKOFFS


class FakeThreadType:
    USER = MagicMock(name="USER")
    GROUP = MagicMock(name="GROUP")


def _settings() -> Settings:
    return Settings(
        groq_api_key="test",
        bot_tag="@QwenAssist",
        allowed_internal_group_ids=["group_internal_demo"],
        allowed_customer_group_ids=["group_customer_demo"],
    )


def _bridge(**kwargs) -> RealZaloBridge:
    repo = MagicMock(spec=SupabaseRepo)
    repo.load_session.return_value = None
    repo.ensure_tables.return_value = ["zalo_session", "message_logs"]
    pipeline = AsyncMock(spec=MessagePipeline)
    pipeline.handle.return_value = None
    pipeline.is_declared_group.return_value = True
    sleeps: list[float] = []
    return RealZaloBridge(
        pipeline=pipeline,
        repo=repo,
        settings=_settings(),
        sleep=sleeps.append,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_dm_messages_are_ignored() -> None:
    bridge = _bridge()
    client = MagicMock()
    bridge._client = client
    bridge._bind_message_handler()

    with patch("zlapi.models.ThreadType", FakeThreadType):
        handler = client.onMessage
        handler(
            "mid",
            "user1",
            "hello",
            MagicMock(dName="X"),
            "user1",
            FakeThreadType.USER,
        )

    bridge.repo.log_message.assert_not_called()
    bridge.pipeline.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_messages_are_logged() -> None:
    bridge = _bridge()
    client = MagicMock()
    bridge._client = client
    bridge._bind_message_handler()

    with (
        patch("zlapi.models.ThreadType", FakeThreadType),
        patch("app.services.zalo_bridge_real.asyncio.run") as run_mock,
    ):
        handler = client.onMessage
        handler(
            "mid",
            "user1",
            "@QwenAssist giờ nghỉ?",
            MagicMock(dName="Anh Bằng"),
            "group_internal_demo",
            FakeThreadType.GROUP,
        )

    bridge.repo.log_message.assert_called_once()
    run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_undeclared_group_messages_are_silent() -> None:
    bridge = _bridge()
    bridge.pipeline.is_declared_group.return_value = False
    client = MagicMock()
    bridge._client = client
    bridge._bind_message_handler()

    with (
        patch("app.services.zalo_bridge_real.asyncio.run") as run_mock,
    ):
        handler = client.onMessage
        handler(
            "mid",
            "user1",
            "@QwenAssist giờ nghỉ?",
            MagicMock(dName="Anh Bằng"),
            "unknown_group",
            1,
        )

    bridge.repo.log_message.assert_not_called()
    run_mock.assert_not_called()


def test_save_session_after_restore_success() -> None:
    repo = MagicMock(spec=SupabaseRepo)
    repo.load_session.return_value = {
        "cookies": {"a": "1"},
        "imei": "imei-1",
        "user_agent": "ua",
    }

    client = MagicMock()
    client.isLoggedIn.return_value = True
    client.getSession.return_value = {"a": "1"}
    client._imei = "imei-1"

    bridge = RealZaloBridge(
        repo=repo,
        settings=_settings(),
        zalo_client_factory=lambda **kwargs: client,
        sleep=lambda _s: None,
    )

    assert bridge._try_restore_session(repo.load_session.return_value) is True
    assert bridge.persist_session_now() is True
    repo.save_session.assert_called_once()


def test_persist_session_starts_retry_on_failure() -> None:
    repo = MagicMock(spec=SupabaseRepo)
    repo.save_session.side_effect = RuntimeError("db down")
    sleeps: list[float] = []

    bridge = RealZaloBridge(
        repo=repo,
        settings=_settings(),
        sleep=sleeps.append,
    )
    bridge._client = MagicMock()
    bridge._client.getSession.return_value = {"z": "1"}
    bridge._client._imei = "imei-1"
    bridge._status = "connected"

    assert bridge.persist_session_now() is False
    assert bridge._persist_retry_thread is not None
    assert bridge._persist_retry_thread.is_alive()
    bridge._persist_retry_stop.set()
    bridge._persist_retry_thread.join(timeout=2)


def test_persist_session_retry_succeeds() -> None:
    repo = MagicMock(spec=SupabaseRepo)
    repo.save_session.side_effect = [RuntimeError("db down"), None]
    bridge = RealZaloBridge(
        repo=repo,
        settings=_settings(),
        sleep=lambda _s: None,
    )
    bridge._client = MagicMock()
    bridge._client.getSession.return_value = {"z": "1"}
    bridge._client._imei = "imei-1"
    bridge._status = "connected"
    bridge._persist_retry_stop.set()

    assert bridge._persist_session() is False
    assert bridge._persist_session() is True
    assert repo.save_session.call_count == 2


def test_backoff_reconnect_moves_to_awaiting_qr_when_session_dead() -> None:
    repo = MagicMock(spec=SupabaseRepo)
    repo.load_session.return_value = None
    sleeps: list[float] = []

    bridge = RealZaloBridge(
        repo=repo,
        settings=_settings(),
        zalo_client_factory=lambda **kwargs: MagicMock(),
        sleep=sleeps.append,
    )
    bridge._status = "connected"
    bridge._reconnect_attempt = 0

    with patch.object(bridge, "_try_restore_session", return_value=False), patch.object(
        bridge, "_start_qr_login"
    ) as qr_mock:
        bridge._handle_disconnect()

    assert sleeps == [RECONNECT_BACKOFFS[0]]
    assert bridge.status == "awaiting_qr"
    qr_mock.assert_called_once()
