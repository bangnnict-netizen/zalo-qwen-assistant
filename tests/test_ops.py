"""Tests for admin summary (/tomtat), lead capture, and order lookup flows."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings
from app.services.message_pipeline import MessagePipeline


@pytest.mark.asyncio
async def test_tomtat_admin_only() -> None:
    settings = Settings(
        groq_api_key="test",
        admin_user_ids=["admin1"],
        allowed_internal_group_ids=["group_internal_demo"],
    )
    repo = MagicMock()
    # recent_logs returns messages
    repo.recent_logs.return_value = [
        {"group_id": "group_internal_demo", "sender_name": "A", "text": "Hello", "created_at": "2026-08-14T00:00:00+00:00"}
    ]
    router = MagicMock()
    router.llm = AsyncMock()
    router.llm.chat.return_value = {"answer": "Tóm tắt ngắn"}
    pipeline = MessagePipeline(router=router, settings=settings, repo=repo)

    event = {"group_id": "group_internal_demo", "sender_id": "admin1", "text": "/tomtat 24"}
    result = await pipeline.handle(event)
    assert result is not None
    assert result["answer"] == "Tóm tắt ngắn"
    repo.recent_logs.assert_called_once()


@pytest.mark.asyncio
async def test_tomtat_non_admin_silent() -> None:
    settings = Settings(
        groq_api_key="test",
        admin_user_ids=["admin1"],
        allowed_internal_group_ids=["group_internal_demo"],
    )
    router = MagicMock()
    pipeline = MessagePipeline(router=router, settings=settings, repo=MagicMock())
    event = {"group_id": "group_internal_demo", "sender_id": "notadmin", "text": "/tomtat"}
    result = await pipeline.handle(event)
    assert result is None


@pytest.mark.asyncio
async def test_lead_capture_flow() -> None:
    settings = Settings(
        groq_api_key="test",
        allowed_customer_group_ids=["group_customer_demo"],
    )
    repo = MagicMock()
    router = MagicMock()
    pipeline = MessagePipeline(router=router, settings=settings, repo=repo)

    # Step 1: customer asks for price (merged into contact flow)
    event1 = {"group_id": "group_customer_demo", "sender_id": "u1", "text": "Giá bao nhiêu?"}
    res1 = await pipeline.handle(event1)
    assert res1 is not None
    assert "Công ty" in res1["answer"]
    assert "Số điện thoại" in res1["answer"]
    # Pending should exist
    assert await pipeline.lead_registry.has_pending("group_customer_demo", "u1")

    # Step 2: customer sends contact info (numbered format)
    with patch("app.services.message_pipeline.create_lead_via_airtable", new=AsyncMock(return_value={"id": "rec1"})) as mock_create:
        event2 = {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "text": "1. CTY Test\n2. 0987654321\n3. Nguyễn A",
        }
        res2 = await pipeline.handle(event2)
        assert res2 is not None
        assert "ghi nhận" in res2["answer"]
        mock_create.assert_awaited_once()
        lead = mock_create.await_args.args[0]
        assert lead["company"] == "CTY Test"
        assert lead["phone"] == "0987654321"


@pytest.mark.asyncio
async def test_order_lookup_found_and_not_found() -> None:
    settings = Settings(
        groq_api_key="test",
        allowed_internal_group_ids=["group_internal_demo"],
    )
    repo = MagicMock()
    router = MagicMock()
    pipeline = MessagePipeline(router=router, settings=settings, repo=repo)

    # not found (mock Airtable)
    with patch("app.services.lead_capture.get_order_from_airtable", new=AsyncMock(return_value=None)):
        event = {"group_id": "group_internal_demo", "sender_id": "u1", "text": "@Byron DH1001"}
        res = await pipeline.handle(event)
        assert res is not None
        assert "không tìm" in res["answer"] or "chưa" in res["answer"]

    # found
    with patch("app.services.lead_capture.get_order_from_airtable", new=AsyncMock(return_value={"status": "delivered", "received_at": "2026-08-01", "note": "ok"})):
        event2 = {"group_id": "group_internal_demo", "sender_id": "u1", "text": "đơn DH1001"}
        res2 = await pipeline.handle(event2)
        assert res2 is not None
        assert "Đơn 1001" in res2["answer"] or "status" in res2["answer"]
