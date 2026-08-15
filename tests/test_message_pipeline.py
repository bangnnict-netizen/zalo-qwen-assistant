"""Unit tests for MessagePipeline filtering and honorific routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.message_pipeline import MessagePipeline
from app.services.router_service import MessageRouter


def _settings() -> Settings:
    return Settings(
        groq_api_key="test-key",
        bot_tags=["@Byron", "@bot"],
        allowed_internal_group_ids=["group_internal_demo"],
        allowed_customer_group_ids=["group_customer_demo"],
    )


def _run(coro):
    return asyncio.run(coro)


def _mock_router() -> AsyncMock:
    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "ok",
        "model_used": "test-model",
        "sources": [],
    }
    return router


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
async def test_con_bot_not_a_tag_returns_none() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "con bot chạy nhanh",
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
            "text": "@Byron xin chào",
        }
    )

    assert result is None
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_byron_tag_replies() -> None:
    router = _mock_router()
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "@Byron mấy giờ?",
        }
    )

    assert result is not None
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ?",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_bot_tag_replies() -> None:
    router = _mock_router()
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "@bot mấy giờ?",
        }
    )

    assert result is not None
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ?",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_bot_tag_case_insensitive() -> None:
    router = _mock_router()
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "@BOT mấy giờ?",
        }
    )

    assert result is not None
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="mấy giờ?",
        honorific="anh",
    )


@pytest.mark.asyncio
async def test_both_tags_stripped() -> None:
    router = _mock_router()
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_internal_demo",
            "sender_gender": "male",
            "text": "@Byron @bot cả hai",
        }
    )

    assert result is not None
    router.route.assert_awaited_once_with(
        group_type="internal",
        question="cả hai",
        honorific="anh",
    )


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
            "text": "@Byron mấy giờ nhà máy nghỉ làm?",
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
async def test_order_lookup_queries_full_code_with_prefix() -> None:
    """Regression: order_id passed to Airtable must include the 'DH' prefix, not just digits."""
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    with patch(
        "app.services.lead_capture.get_order_from_airtable",
        new=AsyncMock(return_value={"status": "Đang chiếu xạ", "received_at": "2026-08-01", "note": ""}),
    ) as mock_get_order:
        result = await pipeline.handle(
            {
                "group_id": "group_internal_demo",
                "sender_gender": "male",
                "text": "DH1001",
            }
        )

    mock_get_order.assert_awaited_once_with("DH1001")
    assert result is not None
    assert "Đang chiếu xạ" in result["answer"]


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
            "text": "@bot gửi hàng chiếu xạ mất bao lâu?",
        }
    )

    assert result is not None
    assert result["honorific"] == "chị"
    router.route.assert_awaited_once_with(
        group_type="customer",
        question="gửi hàng chiếu xạ mất bao lâu?",
        honorific="chị",
    )
