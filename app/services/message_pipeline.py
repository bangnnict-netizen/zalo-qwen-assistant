"""Inbound Zalo message processing pipeline (mock stage)."""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings, get_settings
from app.repositories.supabase_repo import SupabaseRepo
from app.services.group_bindings import GroupBindingRegistry
from app.services.router_service import MessageRouter


def _tag_patterns(tags: list[str]) -> list[re.Pattern[str]]:
    """Build case-insensitive patterns: tag must be its own token (not mid-word)."""
    patterns: list[re.Pattern[str]] = []
    for tag in tags:
        if not tag:
            continue
        escaped = re.escape(tag)
        patterns.append(re.compile(rf"(?<![\w]){escaped}(?!\w)", re.IGNORECASE))
    return patterns


def contains_bot_tag(text: str, tags: list[str]) -> bool:
    """Return True if text contains any configured bot tag as a separate token."""
    return any(pattern.search(text) for pattern in _tag_patterns(tags))


def strip_bot_tags(text: str, tags: list[str]) -> str:
    """Remove all matched bot tags and normalize whitespace."""
    cleaned = text
    for pattern in _tag_patterns(tags):
        cleaned = pattern.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


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

        if not contains_bot_tag(text, self.settings.bot_tags):
            return None

        group_type = self._resolve_group_type(group_id)
        if group_type is None:
            return None

        question = strip_bot_tags(text, self.settings.bot_tags)
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

    async def handle_voice(
        self,
        event: dict[str, Any],
        question: str,
    ) -> dict[str, object] | None:
        """Route a voice transcript question (wake phrase already stripped)."""
        group_id = str(event.get("group_id", ""))
        sender_gender = str(event.get("sender_gender", "unknown"))

        if not self.is_declared_group(group_id):
            return None

        cleaned = question.strip()
        if not cleaned:
            return None

        group_type = self._resolve_group_type(group_id)
        if group_type is None:
            return None

        honorific = self._resolve_honorific(sender_gender)
        routed = await self.router.route(
            group_type=group_type,
            question=cleaned,
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
