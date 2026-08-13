"""Inbound Zalo message processing pipeline (mock stage)."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.services.router_service import MessageRouter


class MessagePipeline:
    """Filter, classify, and route tagged group messages to the LLM."""

    def __init__(
        self,
        router: MessageRouter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.router = router or MessageRouter()
        self.settings = settings or get_settings()

    async def handle(self, event: dict[str, Any]) -> dict[str, object] | None:
        text = str(event.get("text", ""))
        group_id = str(event.get("group_id", ""))
        sender_gender = str(event.get("sender_gender", "unknown"))

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
        if group_id in self.settings.allowed_internal_group_ids:
            return "internal"
        if group_id in self.settings.allowed_customer_group_ids:
            return "customer"
        return None

    @staticmethod
    def _resolve_honorific(sender_gender: str) -> str:
        if sender_gender == "male":
            return "anh"
        if sender_gender == "female":
            return "chị"
        return "anh/chị"
