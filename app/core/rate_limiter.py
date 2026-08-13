"""Zalo outbound rate limiting with injectable clock for tests."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable

COOLDOWN_SEC = 60
WINDOW_SEC = 60


class RateLimiter:
    """Limit send rate and enforce random inter-message delays."""

    def __init__(
        self,
        max_per_min: int,
        min_delay_sec: int,
        max_delay_sec: int,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None] | None] | None = None,
        random_float: Callable[[float, float], float] | None = None,
    ) -> None:
        self.max_per_min = max_per_min
        self.min_delay_sec = min_delay_sec
        self.max_delay_sec = max_delay_sec
        self._now = now or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._random_float = random_float or random.uniform
        self._send_times: deque[float] = deque()
        self._last_send_at: float | None = None
        self._cooldown_until: float = 0.0

    async def wait_before_send(self) -> None:
        """Wait until the next outbound message is allowed."""
        while True:
            current = self._now()
            await self._maybe_sleep(current, self._cooldown_until - current)

            current = self._now()
            self._prune_old_sends(current)

            if len(self._send_times) >= self.max_per_min:
                self._cooldown_until = current + COOLDOWN_SEC
                continue

            if self._last_send_at is not None:
                delay = self._random_float(self.min_delay_sec, self.max_delay_sec)
                elapsed = current - self._last_send_at
                remaining = delay - elapsed
                if remaining > 0:
                    await self._maybe_sleep(current, remaining)

            current = self._now()
            self._send_times.append(current)
            self._last_send_at = current
            return

    def _prune_old_sends(self, current: float) -> None:
        cutoff = current - WINDOW_SEC
        while self._send_times and self._send_times[0] <= cutoff:
            self._send_times.popleft()

    async def _maybe_sleep(self, current: float, seconds: float) -> None:
        if seconds <= 0:
            return
        result = self._sleep(seconds)
        if asyncio.iscoroutine(result):
            await result
