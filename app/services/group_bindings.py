"""In-memory registry of allowed Zalo groups (env + Supabase bindings)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.repositories.supabase_repo import SupabaseRepo

logger = logging.getLogger(__name__)


class GroupBindingRegistry:
    """Merged view of .env allowlists and Supabase group_bindings rows."""

    def __init__(
        self,
        settings: Settings | None = None,
        repo: SupabaseRepo | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repo = repo
        self._supabase_bindings: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        """Refresh Supabase bindings into memory."""
        if self.repo is None:
            self._supabase_bindings = {}
            return
        try:
            rows = self.repo.list_bindings()
            self._supabase_bindings = {
                str(row["group_id"]): row for row in rows if row.get("group_id")
            }
            logger.info("Loaded %d group binding(s) from Supabase", len(self._supabase_bindings))
        except Exception:
            logger.warning("Could not load group bindings from Supabase", exc_info=True)
            self._supabase_bindings = {}

    def resolve_group_type(self, group_id: str) -> str | None:
        if group_id in self.settings.allowed_internal_group_ids:
            return "internal"
        if group_id in self.settings.allowed_customer_group_ids:
            return "customer"
        binding = self._supabase_bindings.get(group_id)
        if binding and binding.get("group_type") in ("internal", "customer"):
            return str(binding["group_type"])
        return None

    def is_declared(self, group_id: str) -> bool:
        return self.resolve_group_type(group_id) is not None

    def status_label(self, group_id: str) -> str:
        group_type = self.resolve_group_type(group_id)
        if group_type == "internal":
            return "Nội bộ"
        if group_type == "customer":
            return "Khách hàng"
        return "Chưa khai báo"

    def supabase_binding(self, group_id: str) -> dict[str, Any] | None:
        return self._supabase_bindings.get(group_id)
