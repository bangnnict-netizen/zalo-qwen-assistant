"""API tests for Zalo admin endpoints."""

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


def test_persist_session_endpoint_requires_token(client: TestClient) -> None:
    response = client.post("/zalo/persist-session")
    assert response.status_code == 401


def test_persist_session_endpoint_returns_saved_flag(client: TestClient) -> None:
    bridge = MagicMock(spec=RealZaloBridge)
    bridge.persist_session_now.return_value = True
    settings = Settings(
        groq_api_key="test",
        admin_token="secret-admin",
        enable_zalo_real=True,
    )

    with (
        patch("app.main.settings", settings),
        patch("app.main.zalo_bridge", bridge),
    ):
        response = client.post(
            "/zalo/persist-session",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    assert response.json() == {"saved": True}
    bridge.persist_session_now.assert_called_once()
