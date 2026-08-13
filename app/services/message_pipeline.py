"""Inbound Zalo message processing pipeline (mock stage)."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.repositories.supabase_repo import SupabaseRepo
from app.services.group_bindings import GroupBindingRegistry
from app.services.router_service import MessageRouter


class MessagePipeline:
    """Filter, classify, and route tagged group messages to the LLM."""

    def __init__(
        self,
        router: MessageRouter | None = None,
        settings: Settings | None = None,
        bindings: GroupBindingRegistry | None = None,
        repo: SupabaseRepo | None = None,
    ) -> None:
        self.router = router or MessageRouter()
        self.settings = settings or get_settings()
        self.repo = repo
        self.bindings = bindings or GroupBindingRegistry(settings=self.settings, repo=repo)

    def reload_bindings(self) -> None:
        self.bindings.reload()

    def is_declared_group(self, group_id: str) -> bool:
        return self.bindings.is_declared(group_id)

    async def handle(self, event: dict[str, Any]) -> dict[str, object] | None:
        text = str(event.get("text", ""))
        group_id = str(event.get("group_id", ""))
        sender_gender = str(event.get("sender_gender", "unknown"))

        if not self.is_declared_group(group_id):
            return None

        if self.settings.bot_tag not in text:
            return None

        group_type = self._resolve_group_type(group_id)
        if group_type is None:
            return None

        question = text.replace(self.settings.bot_tag, "").strip()
        if not question:
            return None

        honorific = self._resolve_honorific(sender_gender)
        routed = await self.router.route(
            group_type=group_type,
            question=question,
            honorific=honorific,
        )

        return {
            "answer": routed["answer"],
            "model_used": routed["model_used"],
            "sources": routed["sources"],
            "honorific": honorific,
        }

    def _resolve_group_type(self, group_id: str) -> str | None:
        return self.bindings.resolve_group_type(group_id)

    @staticmethod
    def _resolve_honorific(sender_gender: str) -> str:
        if sender_gender == "male":
            return "anh"
        if sender_gender == "female":
            return "chị"
        return "anh/chị"
