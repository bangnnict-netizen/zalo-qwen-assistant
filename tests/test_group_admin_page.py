"""Tests for /zalo/admin HTML and group binding UI behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services.group_admin_page import render_group_admin_page
from app.services.message_pipeline import MessagePipeline
from app.services.router_service import MessageRouter
from app.services.zalo_bridge_real import RealZaloBridge

HOC_NHOM_ID = "2990310993143568467"
HOC_NHOM_NAME = "Học Nhóm"


def _settings() -> Settings:
    return Settings(
        groq_api_key="test-key",
        admin_token="secret-admin",
        bot_tags=["@Byron", "@bot"],
        allowed_internal_group_ids=[],
        allowed_customer_group_ids=[],
        enable_zalo_real=True,
    )


class InMemoryBindingsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, str]] = {}

    def list_bindings(self) -> list[dict[str, str]]:
        return list(self.rows.values())

    def upsert_binding(self, group_id: str, name: str, group_type: str) -> None:
        group_id = str(group_id)
        self.rows[group_id] = {
            "group_id": group_id,
            "group_type": group_type,
            "name": name,
        }

    def delete_binding(self, group_id: str) -> None:
        self.rows.pop(str(group_id), None)


def test_admin_html_uses_data_group_id_string_not_onclick_number() -> None:
    html = render_group_admin_page(
        groups=[{"group_id": HOC_NHOM_ID, "name": HOC_NHOM_NAME}],
        status_by_group={HOC_NHOM_ID: "Chưa khai báo"},
        admin_token="secret-admin",
        zalo_connected=True,
    )

    assert f'data-group-id="{HOC_NHOM_ID}"' in html
    assert "onclick=" not in html
    assert HOC_NHOM_ID in html
    assert "badge-undeclared" in html


def test_admin_html_unbind_disabled_for_undeclared_group() -> None:
    html = render_group_admin_page(
        groups=[{"group_id": HOC_NHOM_ID, "name": HOC_NHOM_NAME}],
        status_by_group={HOC_NHOM_ID: "Chưa khai báo"},
        admin_token="secret-admin",
        zalo_connected=True,
    )

    assert 'title="Nhóm chưa khai báo"' in html
    assert 'data-action="unbind" disabled' in html


def test_admin_html_has_save_feedback_and_toast() -> None:
    html = render_group_admin_page(
        groups=[{"group_id": HOC_NHOM_ID, "name": HOC_NHOM_NAME}],
        status_by_group={HOC_NHOM_ID: "Chưa khai báo"},
        admin_token="secret-admin",
        zalo_connected=True,
    )

    assert "Đang lưu..." in html
    assert 'id="toast"' in html
    assert "showToast" in html
    assert "window.location.reload()" in html
    assert "String(btn.dataset.groupId" in html


def test_admin_html_status_badges() -> None:
    html = render_group_admin_page(
        groups=[
            {"group_id": "1", "name": "Internal"},
            {"group_id": "2", "name": "Customer"},
            {"group_id": "3", "name": "New"},
        ],
        status_by_group={"1": "Nội bộ", "2": "Khách hàng", "3": "Chưa khai báo"},
        admin_token="t",
        zalo_connected=True,
    )

    assert "badge-internal" in html
    assert "badge-customer" in html
    assert "badge-undeclared" in html


def test_bind_hoc_nhom_internal_preserves_id_and_simulate_replies() -> None:
    repo = InMemoryBindingsRepo()
    settings = _settings()
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(router=router, settings=settings, repo=repo)  # type: ignore[arg-type]
    bridge = MagicMock()
    bridge.send = AsyncMock()

    with (
        patch("app.main.settings", settings),
        patch("app.main.supabase_repo", repo),
        patch("app.main.pipeline", pipeline),
        patch("app.main.zalo_bridge", bridge),
    ):
        client = TestClient(app)
        bind = client.post(
            "/zalo/bindgroup",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "group_id": HOC_NHOM_ID,
                "name": HOC_NHOM_NAME,
                "group_type": "internal",
            },
        )
        assert bind.status_code == 200
        assert bind.json() == {"ok": True}

        stored = repo.list_bindings()
        assert len(stored) == 1
        assert stored[0]["group_id"] == HOC_NHOM_ID
        assert stored[0]["group_type"] == "internal"
        assert pipeline.is_declared_group(HOC_NHOM_ID) is True

        sim = client.post(
            "/simulate",
            json={
                "group_id": HOC_NHOM_ID,
                "sender_gender": "male",
                "text": "@Byron mấy giờ nhà máy nghỉ làm?",
            },
        )

    assert sim.status_code == 200
    body = sim.json()
    assert body["replied"] is True
    bridge.send.assert_awaited_once()


def test_unbind_hoc_nhom_removes_binding() -> None:
    repo = InMemoryBindingsRepo()
    repo.upsert_binding(HOC_NHOM_ID, HOC_NHOM_NAME, "internal")
    settings = _settings()
    pipeline = MessagePipeline(settings=settings, repo=repo)  # type: ignore[arg-type]
    pipeline.reload_bindings()

    with (
        patch("app.main.settings", settings),
        patch("app.main.supabase_repo", repo),
        patch("app.main.pipeline", pipeline),
    ):
        client = TestClient(app)
        response = client.post(
            "/zalo/bindgroup",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "group_id": HOC_NHOM_ID,
                "name": HOC_NHOM_NAME,
                "group_type": None,
            },
        )

    assert response.status_code == 200
    assert repo.list_bindings() == []
    assert pipeline.is_declared_group(HOC_NHOM_ID) is False


def test_admin_page_lists_groups_with_string_ids() -> None:
    bridge = MagicMock(spec=RealZaloBridge)
    bridge.get_status.return_value = {"status": "connected"}
    bridge.list_groups.return_value = [
        {"group_id": HOC_NHOM_ID, "name": HOC_NHOM_NAME},
    ]
    settings = _settings()
    pipeline = MessagePipeline(settings=settings)

    with (
        patch("app.main.settings", settings),
        patch("app.main.zalo_bridge", bridge),
        patch("app.main.pipeline", pipeline),
    ):
        client = TestClient(app)
        response = client.get("/zalo/admin?token=secret-admin")

    assert response.status_code == 200
    assert f'data-group-id="{HOC_NHOM_ID}"' in response.text
    assert 'data-action="unbind" disabled' in response.text
