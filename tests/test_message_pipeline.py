"""Unit tests for MessagePipeline filtering and honorific routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
            "text": "@bot xin chào, cần hỗ trợ",
        }
    )

    assert result is not None
    assert result["honorific"] == "chị"
    router.route.assert_awaited_once_with(
        group_type="customer",
        question="xin chào, cần hỗ trợ",
        honorific="chị",
    )


@pytest.mark.asyncio
async def test_company_info_fixed_reply_no_llm() -> None:
    router = MagicMock(spec=MessageRouter)
    router.llm = AsyncMock()
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "Cho em xin địa chỉ công ty",
        }
    )

    assert result is not None
    assert "CÔNG TY CỔ PHẦN CHIẾU XẠ CẦN THƠ" in result["answer"]
    assert "1801710194" in result["answer"]
    router.llm.chat.assert_not_called()
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_contact_intent_sets_pending_and_creates_lead_with_company() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    res1 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "Em muốn liên hệ chiếu xạ",
        }
    )
    assert res1 is not None
    assert "Công ty" in res1["answer"]
    assert "Số điện thoại" in res1["answer"]
    assert await pipeline.lead_registry.has_pending("group_customer_demo", "u1")

    with patch(
        "app.services.message_pipeline.create_lead_via_airtable",
        new=AsyncMock(return_value={"id": "rec1"}),
    ) as mock_create:
        res2 = await pipeline.handle(
            {
                "group_id": "group_customer_demo",
                "sender_id": "u1",
                "sender_gender": "male",
                "text": "1. CTY Demo\n2. 0987654321\n3. Trần A",
            }
        )

    assert res2 is not None
    assert "ghi nhận" in res2["answer"]
    mock_create.assert_awaited_once()
    lead_arg = mock_create.await_args.args[0]
    assert lead_arg["company"] == "CTY Demo"
    assert lead_arg["phone"] == "0987654321"
    assert lead_arg["name"] == "Trần A"


@pytest.mark.asyncio
async def test_price_intent_merged_into_contact_flow() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "Giá bao nhiêu?",
        }
    )

    assert result is not None
    assert "Công ty" in result["answer"]
    assert await pipeline.lead_registry.has_pending("group_customer_demo", "u1")


@pytest.mark.asyncio
async def test_only_tuvan_prompts_and_reclassifies() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    res1 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "tư vấn",
        }
    )
    assert res1 is not None
    assert res1["answer"] == "Dạ, anh/chị muốn tư vấn về vấn đề gì ạ?"

    res2 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "Cho em xin địa chỉ công ty",
        }
    )
    assert res2 is not None
    assert "CÔNG TY CỔ PHẦN CHIẾU XẠ CẦN THƠ" in res2["answer"]


@pytest.mark.asyncio
async def test_consultation_reply_no_match_falls_back_to_contact() -> None:
    from app.services.message_pipeline import CONTACT_REQUEST_MSG

    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    res1 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "tư vấn",
        }
    )
    assert res1 is not None
    assert res1["answer"] == "Dạ, anh/chị muốn tư vấn về vấn đề gì ạ?"

    res2 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "em muốn hỏi về dịch vụ đóng gói",
        }
    )

    assert res2 is not None
    assert res2["answer"] == CONTACT_REQUEST_MSG
    assert await pipeline.lead_registry.has_pending("group_customer_demo", "u1")


@pytest.mark.asyncio
async def test_consultation_reply_with_bot_tag_still_falls_back_to_contact() -> None:
    """Regression: a tagged reply to 'muốn tư vấn gì ạ?' must still go to contact capture,
    not the free-form LLM flow, even though it contains a bot tag."""
    from app.services.message_pipeline import CONTACT_REQUEST_MSG

    router = AsyncMock(spec=MessageRouter)
    router.route.return_value = {
        "answer": "câu trả lời tự do",
        "model_used": "test-model",
        "sources": [],
    }
    pipeline = MessagePipeline(router=router, settings=_settings())

    res1 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "tư vấn",
        }
    )
    assert res1 is not None

    res2 = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "@Byron em muốn hỏi thêm chút",
        }
    )

    assert res2 is not None
    assert res2["answer"] == CONTACT_REQUEST_MSG
    router.route.assert_not_called()


@pytest.mark.asyncio
async def test_tuvan_with_extra_text_not_matched() -> None:
    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "em muốn tư vấn về giá",
        }
    )

    assert result is not None
    assert "Công ty" in result["answer"]


@pytest.mark.asyncio
async def test_technical_question_no_tag_still_routed() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.rag = MagicMock()
    router.rag.search.return_value = [{"heading": "Tech", "content": "Gamma"}]
    router.llm = AsyncMock()
    router.llm.chat.return_value = {"answer": "Công nghệ Gamma", "model_used": "test"}
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "bên em chiếu xạ công nghệ gì vậy?",
        }
    )

    assert result is not None
    assert "Công nghệ Gamma" in result["answer"]
    router.llm.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_technical_question_with_bao_nhieu_not_contact_intent() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.rag = MagicMock()
    router.rag.search.return_value = [{"heading": "Liều", "content": "10kGy"}]
    router.llm = AsyncMock()
    router.llm.chat.return_value = {"answer": "Liều 10kGy", "model_used": "test"}
    pipeline = MessagePipeline(router=router, settings=_settings())
    
    # "liều bao nhiêu" nên vào nhánh kỹ thuật
    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "Liều chiếu cho cá fillet đông lạnh thì chiếu liều bao nhiêu?",
        }
    )
    
    print(f"DEBUG: result={result}")
    assert result is not None
    assert "Liều 10kGy" in result["answer"]
    router.llm.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_contact_intent_with_bao_nhieu_price_context() -> None:
    from app.services.message_pipeline import CONTACT_REQUEST_MSG

    router = AsyncMock(spec=MessageRouter)
    pipeline = MessagePipeline(router=router, settings=_settings())

    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "chiếu 500kg tôm bao nhiêu tiền?",
        }
    )

    assert result is not None
    assert CONTACT_REQUEST_MSG in result["answer"]


@pytest.mark.asyncio
async def test_technical_question_time_bao_nhieu_not_contact_intent() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.rag = MagicMock()
    router.rag.search.return_value = [{"heading": "Time", "content": "2h"}]
    router.llm = AsyncMock()
    router.llm.chat.return_value = {"answer": "2h", "model_used": "test"}
    pipeline = MessagePipeline(router=router, settings=_settings())
    
    # "bao nhiêu thời gian" khớp với từ khóa "thời gian" trong TECHNICAL_RE
    result = await pipeline.handle(
        {
            "group_id": "group_customer_demo",
            "sender_id": "u1",
            "sender_gender": "male",
            "text": "mất bao nhiêu thời gian để chiếu xong?",
        }
    )

    assert result is not None
    assert "2h" in result["answer"]
    router.llm.chat.assert_awaited_once()

@pytest.mark.asyncio
async def test_technical_rag_hit_no_tavily() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.rag = MagicMock()
    router.rag.search.return_value = [{"heading": "Liều", "content": "10-25 kGy"}]
    router.llm = AsyncMock()
    router.llm.chat.return_value = {"answer": "Liều tiêu chuẩn 10-25 kGy", "model_used": "test"}
    pipeline = MessagePipeline(router=router, settings=_settings())

    with patch("app.services.message_pipeline.tavily_search", new=AsyncMock()) as mock_tavily:
        result = await pipeline.handle(
            {
                "group_id": "group_customer_demo",
                "sender_id": "u1",
                "sender_gender": "male",
                "text": "Cho em hỏi về liều chiếu xạ thực phẩm",
            }
        )

    assert result is not None
    assert "10-25 kGy" in result["answer"]
    mock_tavily.assert_not_called()
    router.llm.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_technical_rag_empty_calls_tavily() -> None:
    router = AsyncMock(spec=MessageRouter)
    router.rag = MagicMock()
    router.rag.search.return_value = []
    router.llm = AsyncMock()
    router.llm.chat.return_value = {
        "answer": "⚠️ Thông tin mang tính chất tham khảo, chưa được công ty xác nhận chính thức:\n\nNguồn web",
        "model_used": "test",
    }
    pipeline = MessagePipeline(router=router, settings=_settings())

    with patch(
        "app.services.message_pipeline.tavily_search",
        new=AsyncMock(return_value=[{"title": "Web", "url": "http://x", "content": "data"}]),
    ) as mock_tavily:
        result = await pipeline.handle(
            {
                "group_id": "group_customer_demo",
                "sender_id": "u1",
                "sender_gender": "male",
                "text": "Quy trình chiếu xạ thực phẩm?",
            }
        )

    assert result is not None
    mock_tavily.assert_awaited_once()
    system_arg = router.llm.chat.await_args.kwargs.get("system") or router.llm.chat.await_args.args[1]
    assert "tham khảo" in system_arg.lower() or "⚠️" in system_arg

