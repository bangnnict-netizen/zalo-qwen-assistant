"""Zalo bridge abstraction and mock implementation."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.config import Settings, get_settings
from app.core.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class ZaloBridge(ABC):
    """Abstract bridge for receiving and sending Zalo group messages."""

    @abstractmethod
    async def on_message(self, event: dict[str, Any]) -> None:
        """Handle an inbound Zalo message event."""

    @abstractmethod
    async def send(self, group_id: str, text: str) -> None:
        """Send a text message to a Zalo group."""


class MockZaloBridge(ZaloBridge):
    """Mock bridge that logs outbound messages and applies rate limits."""

    def __init__(
        self,
        settings: Settings | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rate_limiter = rate_limiter or RateLimiter(
            max_per_min=self._settings.zalo_max_msg_per_min,
            min_delay_sec=self._settings.zalo_min_delay_sec,
            max_delay_sec=self._settings.zalo_max_delay_sec,
        )
        self.sent_messages: list[dict[str, str]] = []

    async def on_message(self, event: dict[str, Any]) -> None:
        logger.info("MockZaloBridge received event from group=%s", event.get("group_id"))

    async def send(self, group_id: str, text: str) -> None:
        await self._rate_limiter.wait_before_send()
        self.sent_messages.append({"group_id": group_id, "text": text})
        logger.info("MockZaloBridge sent to group=%s: %s", group_id, text)
