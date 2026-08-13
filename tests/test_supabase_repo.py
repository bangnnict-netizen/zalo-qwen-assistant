"""Unit tests for SupabaseRepo session and message logging."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.repositories.supabase_repo import SupabaseRepo, TABLES


def _settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="test-key",
        supabase_db_url="postgresql://postgres:pass@localhost:5432/postgres",
        ttl_days=3,
    )


def test_save_and_load_session() -> None:
    repo = SupabaseRepo(_settings())
    table = MagicMock()
    repo._client = MagicMock()
    repo._client.table.return_value = table
    table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "default"}])
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[{"payload": {"cookies": {"z": "1"}, "imei": "abc"}}])
    )

    with patch.object(repo, "ensure_tables"), patch.object(repo, "_tables_exist", return_value=True):
        repo.save_session({"cookies": {"z": "1"}, "imei": "abc"})
        loaded = repo.load_session()

    assert loaded == {"cookies": {"z": "1"}, "imei": "abc"}
    table.upsert.assert_called_once()


def test_log_message_inserts_row() -> None:
    repo = SupabaseRepo(_settings())
    table = MagicMock()
    repo._client = MagicMock()
    repo._client.table.return_value = table
    table.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

    with patch.object(repo, "ensure_tables"):
        repo.log_message(
            group_id="group_internal_demo",
            sender_id="u1",
            sender_name="Anh Bằng",
            gender="male",
            text="@QwenAssist xin chào",
        )

    table.insert.assert_called_once_with(
        {
            "group_id": "group_internal_demo",
            "sender_id": "u1",
            "sender_name": "Anh Bằng",
            "gender": "male",
            "text": "@QwenAssist xin chào",
        }
    )


def test_ensure_tables_creates_when_missing() -> None:
    repo = SupabaseRepo(_settings())

    with (
        patch.object(repo, "ensure_group_bindings_table"),
        patch.object(repo, "_core_tables_exist", side_effect=[False, True]),
        patch.object(repo, "_create_tables_with_psycopg") as create_mock,
    ):
        created = repo.ensure_tables()

    assert created == list(TABLES)
    create_mock.assert_called_once()


def test_bindings_fallback_to_session_cache_when_table_missing() -> None:
    repo = SupabaseRepo(_settings())
    saved: dict[str, object] = {"items": []}

    def _save(row_id: str, payload: dict[str, object]) -> None:
        saved.clear()
        saved.update(payload)

    def _load(_row_id: str) -> dict[str, object] | None:
        return dict(saved) if saved else None

    with (
        patch.object(repo, "_group_bindings_table_exists", return_value=False),
        patch.object(repo, "ensure_group_bindings_table"),
        patch.object(repo, "_core_tables_exist", return_value=True),
        patch.object(repo, "_save_session_payload", side_effect=_save),
        patch.object(repo, "_load_session_payload", side_effect=_load),
    ):
        repo.upsert_binding("7417141469033973442", "AI_Group", "internal")
        rows = repo.list_bindings()

    assert len(rows) == 1
    assert rows[0]["group_id"] == "7417141469033973442"
    assert rows[0]["group_type"] == "internal"
