"""Supabase persistence for Zalo session and message logs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from supabase import Client, create_client

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SESSION_ROW_ID = "default"
TABLES = ("zalo_session", "message_logs", "group_bindings")

CREATE_ZALO_SESSION_SQL = """
CREATE TABLE IF NOT EXISTS zalo_session (
    id text PRIMARY KEY,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""

CREATE_MESSAGE_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS message_logs (
    id bigserial PRIMARY KEY,
    group_id text NOT NULL,
    sender_id text NOT NULL DEFAULT '',
    sender_name text NOT NULL DEFAULT '',
    gender text NOT NULL DEFAULT 'unknown',
    text text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
"""

CREATE_GROUP_BINDINGS_SQL = """
CREATE TABLE IF NOT EXISTS group_bindings (
    group_id text PRIMARY KEY,
    group_type text NOT NULL,
    name text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class SupabaseRepo:
    """Supabase client wrapper for Zalo session + message logging."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        if self._client is None:
            if not self.settings.supabase_url or not self.settings.supabase_key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")
            self._client = create_client(
                self.settings.supabase_url,
                self.settings.supabase_key,
            )
        return self._client

    def ensure_tables(self) -> list[str]:
        """Create required tables when missing. Returns ensured table names."""
        if self._tables_exist():
            return list(TABLES)

        if self._try_rpc_bootstrap():
            pass
        elif self.settings.supabase_db_url.strip():
            self._create_tables_with_psycopg()
        else:
            self._create_tables_with_http()

        if not self._tables_exist():
            raise RuntimeError(
                "Failed to create Supabase tables. Run supabase/bootstrap.sql once "
                "or configure SUPABASE_DB_URL for automatic DDL."
            )

        logger.info("Ensured Supabase tables: %s", ", ".join(TABLES))
        return list(TABLES)

    def _try_rpc_bootstrap(self) -> bool:
        try:
            self.client.rpc("ensure_zalo_tables").execute()
            return True
        except Exception:
            return False

    def _create_tables_with_psycopg(self) -> None:
        import psycopg2

        with psycopg2.connect(self.settings.supabase_db_url.strip()) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(CREATE_ZALO_SESSION_SQL)
                cur.execute(CREATE_MESSAGE_LOGS_SQL)
                cur.execute(CREATE_GROUP_BINDINGS_SQL)

    def _create_tables_with_http(self) -> None:
        """Best-effort DDL bootstrap via Supabase HTTP when DB URL is unavailable."""
        sql = (
            f"{CREATE_ZALO_SESSION_SQL.strip()}\n"
            f"{CREATE_MESSAGE_LOGS_SQL.strip()}\n"
            f"{CREATE_GROUP_BINDINGS_SQL.strip()}"
        )
        headers = {
            "apikey": self.settings.supabase_key,
            "Authorization": f"Bearer {self.settings.supabase_key}",
            "Content-Type": "application/json",
        }
        project_ref = (
            self.settings.supabase_url.replace("https://", "")
            .replace(".supabase.co", "")
            .strip("/")
        )
        endpoints = (
            f"{self.settings.supabase_url.rstrip('/')}/pg/query",
            f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
            f"{self.settings.supabase_url.rstrip('/')}/rest/v1/rpc/exec_sql",
            f"{self.settings.supabase_url.rstrip('/')}/rest/v1/rpc/run_sql",
        )
        last_error = "no HTTP DDL endpoint responded successfully"
        for endpoint in endpoints:
            try:
                response = httpx.post(
                    endpoint,
                    headers=headers,
                    json={"query": sql},
                    timeout=30.0,
                )
                if response.status_code < 400:
                    return
                last_error = f"{endpoint}: HTTP {response.status_code}"
            except Exception as exc:
                last_error = f"{endpoint}: {exc}"
        raise RuntimeError(
            "Tables missing and SUPABASE_DB_URL is not configured for DDL bootstrap. "
            f"HTTP fallback failed ({last_error})."
        )

    def save_session(self, payload: dict[str, Any]) -> None:
        self.ensure_tables()
        row = {
            "id": SESSION_ROW_ID,
            "payload": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("zalo_session").upsert(row).execute()

    def load_session(self) -> dict[str, Any] | None:
        if not self._tables_exist():
            return None
        result = (
            self.client.table("zalo_session")
            .select("payload")
            .eq("id", SESSION_ROW_ID)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        payload = rows[0].get("payload")
        return payload if isinstance(payload, dict) else None

    def log_message(
        self,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        gender: str,
        text: str,
    ) -> None:
        self.ensure_tables()
        self.client.table("message_logs").insert(
            {
                "group_id": group_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "gender": gender,
                "text": text,
            }
        ).execute()

    def recent_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self._tables_exist():
            return []
        capped = max(1, min(limit, 100))
        result = (
            self.client.table("message_logs")
            .select("group_id,sender_name,text,created_at")
            .order("created_at", desc=True)
            .limit(capped)
            .execute()
        )
        return result.data or []

    def list_bindings(self) -> list[dict[str, Any]]:
        if not self._group_bindings_table_exists():
            return []
        result = (
            self.client.table("group_bindings")
            .select("group_id,group_type,name,updated_at")
            .order("updated_at", desc=True)
            .execute()
        )
        return result.data or []

    def upsert_binding(self, group_id: str, name: str, group_type: str) -> None:
        if group_type not in ("internal", "customer"):
            raise ValueError("group_type must be internal or customer")
        self.ensure_tables()
        row = {
            "group_id": group_id,
            "group_type": group_type,
            "name": name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("group_bindings").upsert(row).execute()

    def delete_binding(self, group_id: str) -> None:
        if not self._group_bindings_table_exists():
            return
        self.client.table("group_bindings").delete().eq("group_id", group_id).execute()

    def delete_old_messages(self, older_than_days: int | None = None) -> int:
        days = older_than_days if older_than_days is not None else self.settings.ttl_days
        if not self._tables_exist():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = (
            self.client.table("message_logs")
            .delete()
            .lt("created_at", cutoff.isoformat())
            .execute()
        )
        return len(result.data or [])

    def _tables_exist(self) -> bool:
        try:
            self.client.table("zalo_session").select("id").limit(1).execute()
            self.client.table("message_logs").select("id").limit(1).execute()
            return True
        except Exception as exc:
            message = str(exc).lower()
            if "could not find the table" in message or "pgrst205" in message:
                return False
            raise

    def _group_bindings_table_exists(self) -> bool:
        try:
            self.client.table("group_bindings").select("group_id").limit(1).execute()
            return True
        except Exception as exc:
            message = str(exc).lower()
            if "could not find the table" in message or "pgrst205" in message:
                return False
            raise
