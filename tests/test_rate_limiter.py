"""Unit tests for RateLimiter with a fake clock."""

from __future__ import annotations

import asyncio

import pytest

from app.core.rate_limiter import COOLDOWN_SEC, RateLimiter


class FakeClock:
    """Monotonic fake clock that advances only when sleep() is called."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@pytest.mark.asyncio
async def test_seventh_message_triggers_cooldown() -> None:
    clock = FakeClock()
    limiter = RateLimiter(
        max_per_min=6,
        min_delay_sec=0,
        max_delay_sec=0,
        now=clock.now,
        sleep=clock.sleep,
        random_float=lambda _a, _b: 0.0,
    )

    for _ in range(6):
        await limiter.wait_before_send()

    assert clock.t == 0.0
    assert not clock.slept

    await limiter.wait_before_send()

    assert COOLDOWN_SEC in clock.slept
    assert clock.t == COOLDOWN_SEC


@pytest.mark.asyncio
async def test_random_delay_between_sends() -> None:
    clock = FakeClock()
    limiter = RateLimiter(
        max_per_min=100,
        min_delay_sec=3,
        max_delay_sec=6,
        now=clock.now,
        sleep=clock.sleep,
        random_float=lambda _a, _b: 4.0,
    )

    await limiter.wait_before_send()
    await limiter.wait_before_send()

    assert clock.slept == [4.0]
    assert clock.t == 4.0
