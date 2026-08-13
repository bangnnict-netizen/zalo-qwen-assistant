"""Unit tests for MessagePipeline filtering and honorific routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.message_pipeline import MessagePipeline
from app.services.router_service import MessageRouter


def _settings() -> Settings:
    return Settings(
        groq_api_key="test-key",
        bot_tag="@QwenAssist",
        allowed_internal_group_ids=["group_internal_demo"],
        allowed_customer_group_ids=["group_customer_demo"],
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_no_bot_tag_returns_none() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "hôm nay trời đẹp",
        }
    )

    assert result is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_group_returns_none() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_unknown",
            "sender_gender": "male",
            "text": "@QwenAssist xin chào",
        }
    )

    assert result is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_internal_male_uses_anh_honorific() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "16h30",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "@QwenAssist mấy giờ nhà máy nghỉ làm?",
        }
    )

    assert result is not None
    assert result["honorific"] == "anh"
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ nhà máy nghỉ làm?",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_customer_female_uses_chi_honorific() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "24-48 giờ",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_gender": "female",
            "text": "@QwenAssist gửi hàng chiếu xạ mất bao lâu?",
        }
    )

    assert result is not None
    assert result["honorific"] == "chị"
    router.route.assert_awaited_once_with(
        group_type="customer",
        question="gửi hàng chiếu xạ mất bao lâu?",
        honorific="chị",
    )
