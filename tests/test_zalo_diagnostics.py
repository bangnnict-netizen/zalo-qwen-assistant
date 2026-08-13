"""Tests for Zalo diagnostic endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services.zalo_bridge_real import RealZaloBridge


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_recent_logs_endpoint(client: TestClient) -> None:
    settings = Settings(groq_api_key="test", admin_token="secret-admin")
    repo = MagicMock()
    repo.recent_logs.return_value = [
        {
            "group_id": "111",
            "sender_name": "Anh Bằng",
            "text": "hello",
            "created_at": "2026-08-13T00:00:00+00:00",
        }
    ]

    with patch("app.main.settings", settings), patch("app.main.supabase_repo", repo):
        response = client.get(
            "/zalo/recent-logs?limit=5",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    assert response.json()[0]["group_id"] == "111"
    repo.recent_logs.assert_called_once_with(limit=5)


def test_groups_endpoint(client: TestClient) -> None:
    settings = Settings(groq_api_key="test", admin_token="secret-admin", enable_zalo_real=True)
    bridge = MagicMock(spec=RealZaloBridge)
    bridge.list_groups.return_value = [{"group_id": "123", "name": "Nhóm test"}]

    with patch("app.main.settings", settings), patch("app.main.zalo_bridge", bridge):
        response = client.get(
            "/zalo/groups",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    assert response.json() == [{"group_id": "123", "name": "Nhóm test"}]
