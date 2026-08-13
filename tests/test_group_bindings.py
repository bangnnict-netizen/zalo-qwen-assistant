"""Tests for Supabase-backed group bindings and admin self-service flow."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.repositories.supabase_repo import SupabaseRepo
from app.services.message_pipeline import MessagePipeline
from app.services.router_service import MessageRouter
from app.services.zalo_bridge_real import RealZaloBridge


def _settings() -> Settings:
    return Settings(
        groq_api_key="test-key",
        admin_token="secret-admin",
        bot_tags=["@Byron", "@bot"],
        allowed_internal_group_ids=[],
        allowed_customer_group_ids=[],
    )


class InMemoryBindingsRepo:
    """Minimal repo stub backing GroupBindingRegistry in tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, str]] = {}

    def list_bindings(self) -> list[dict[str, str]]:
        return list(self.rows.values())

    def upsert_binding(self, group_id: str, name: str, group_type: str) -> None:
        self.rows[group_id] = {
            "group_id": group_id,
            "group_type": group_type,
            "name": name,
        }

    def delete_binding(self, group_id: str) -> None:
        self.rows.pop(group_id, None)


@pytest.fixture
def binding_repo() -> InMemoryBindingsRepo:
    return InMemoryBindingsRepo()


@pytest.fixture
def pipeline(binding_repo: InMemoryBindingsRepo) -> MessagePipeline:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    pipe = MessagePipeline(
        router=router,
        settings=_settings(),
        repo=binding_repo,  # type: ignore[arg-type]
    )
    pipe.reload_bindings()
    return pipe


@pytest.mark.asyncio
async def test_bind_internal_simulate_uses_internal_persona(
    pipeline: MessagePipeline,
    binding_repo: InMemoryBindingsRepo,
) -> None:
    binding_repo.upsert_binding("7417141469033973442", "AI_Group", "internal")
    pipeline.reload_bindings()

    result = await pipeline.handle(
        {
            "group_id": "7417141469033973442",
            "sender_gender": "male",
            "text": "@Byron mấy giờ nhà máy nghỉ làm?",
        }
    )

    assert result is not None
    pipeline.router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ nhà máy nghỉ làm?",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_unbind_group_pipeline_returns_none(
    pipeline: MessagePipeline,
    binding_repo: InMemoryBindingsRepo,
) -> None:
    binding_repo.upsert_binding("7417141469033973442", "AI_Group", "internal")
    pipeline.reload_bindings()
    binding_repo.delete_binding("7417141469033973442")
    pipeline.reload_bindings()

    result = await pipeline.handle(
        {
            "group_id": "7417141469033973442",
            "sender_gender": "male",
            "text": "@Byron mấy giờ nhà máy nghỉ làm?",
        }
    )

    assert result is None
    pipeline.router.route.assert_not_awaited()


def test_simulate_endpoint_replied_false_after_unbind(
    binding_repo: InMemoryBindingsRepo,
) -> None:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(
        router=router,
        settings=_settings(),
        repo=binding_repo,  # type: ignore[arg-type]
    )
    pipeline.reload_bindings()
    bridge = MagicMock()
    bridge.send = AsyncMock()

    with patch("app.main.pipeline", pipeline), patch("app.main.zalo_bridge", bridge):
        client = TestClient(app)
        response = client.post(
            "/simulate",
            json={
                "group_id": "7417141469033973442",
                "sender_gender": "male",
                "text": "@Byron mấy giờ nhà máy nghỉ làm?",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"replied": False}
    bridge.send.assert_not_awaited()


def test_simulate_endpoint_replied_true_after_bind_internal(
    binding_repo: InMemoryBindingsRepo,
) -> None:
    binding_repo.upsert_binding("7417141469033973442", "AI_Group", "internal")
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(
        router=router,
        settings=_settings(),
        repo=binding_repo,  # type: ignore[arg-type]
    )
    pipeline.reload_bindings()
    bridge = MagicMock()
    bridge.send = AsyncMock()

    async def _run_simulate() -> None:
        pass

    with patch("app.main.pipeline", pipeline), patch("app.main.zalo_bridge", bridge):
        client = TestClient(app)
        response = client.post(
            "/simulate",
            json={
                "group_id": "7417141469033973442",
                "sender_gender": "male",
                "text": "@Byron mấy giờ nhà máy nghỉ làm?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["replied"] is True
    assert body["model_used"] == "test-model"
    bridge.send.assert_awaited_once()


def test_undeclared_group_is_not_logged() -> None:
    repo = MagicMock(spec=SupabaseRepo)
    repo.load_session.return_value = None
    pipeline = MagicMock(spec=MessagePipeline)
    pipeline.is_declared_group.return_value = False
    pipeline.handle.return_value = None

    bridge = RealZaloBridge(
        pipeline=pipeline,
        repo=repo,
        settings=_settings(),
        sleep=lambda _s: None,
    )
    client = MagicMock()
    bridge._client = client
    bridge._bind_message_handler()

    class FakeThreadType:
        GROUP = 1

    with patch("app.services.zalo_bridge_real.asyncio.run"):
        handler = client.onMessage
        handler(
            "mid",
            "user1",
            "@Byron hello",
            MagicMock(dName="Anh Bằng"),
            "9999999999999999999",
            FakeThreadType.GROUP,
        )

    repo.log_message.assert_not_called()
    pipeline.handle.assert_not_called()


def test_bindgroup_endpoint_reloads_pipeline(binding_repo: InMemoryBindingsRepo) -> None:
    settings = _settings()
    pipeline = MessagePipeline(settings=settings, repo=binding_repo)  # type: ignore[arg-type]

    with (
        patch("app.main.settings", settings),
        patch("app.main.supabase_repo", binding_repo),
        patch("app.main.pipeline", pipeline),
    ):
        client = TestClient(app)
        response = client.post(
            "/zalo/bindgroup",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "group_id": "7417141469033973442",
                "name": "AI_Group",
                "group_type": "internal",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert pipeline.is_declared_group("7417141469033973442") is True
    assert binding_repo.list_bindings()[0]["group_type"] == "internal"
