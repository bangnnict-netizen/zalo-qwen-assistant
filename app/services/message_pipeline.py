"""Inbound Zalo message processing pipeline (mock stage)."""

from __future__ import annotations

import logging
import re
from typing import Any
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.repositories.supabase_repo import SupabaseRepo
from app.services.group_bindings import GroupBindingRegistry
from app.services.router_service import MessageRouter
from app.services.lead_capture import PendingLeadRegistry, create_lead_via_airtable
from app.services.search_service import tavily_search

logger = logging.getLogger(__name__)

CONTACT_REQUEST_MSG = (
    "Dạ, anh/chị vui lòng cho em xin thông tin liên hệ để nhân viên bên em liên lạc "
    "để tư vấn dịch vụ ạ.\n"
    "1. Công ty\n"
    "2. Số điện thoại\n"
    "3. Tên liên hệ"
)
COMPANY_INFO_REPLY = (
    "Dạ, thông tin Công ty ạ:\n"
    "- Tên Công ty: CÔNG TY CỔ PHẦN CHIẾU XẠ CẦN THƠ\n"
    "- Địa chỉ: Số 2 Khu vực Phú Thắng, Phường Hưng Phú, TP Cần Thơ, Việt Nam\n"
    "- Mã số thuế: 1801710194\n"
    "- Hotline: 0907 3456 07 - 0931 007 588"
)
CONSULTATION_PROMPT = "Dạ, anh/chị muốn tư vấn về vấn đề gì ạ?"
LEAD_SUCCESS_REPLY = "Dạ em đã ghi nhận thông tin, nhân viên bên em sẽ liên hệ sớm ạ."
LEAD_FAIL_REPLY = "Dạ em chưa lưu được thông tin lúc này, anh/chị vui lòng thử lại sau ạ."

CONTACT_INTENT_RE = re.compile(
    r"liên hệ|muốn liên hệ|kết nối|gọi cho tôi|gọi cho em|liên hệ chiếu xạ|"
    r"giá|báo giá|bao nhiêu|hợp đồng",
    re.IGNORECASE,
)
COMPANY_INFO_RE = re.compile(
    r"địa chỉ công ty|địa chỉ cty|mã số thuế|\bmst\b|hotline|thông tin công ty|"
    r"công ty ở đâu|số điện thoại công ty",
    re.IGNORECASE,
)
TECHNICAL_RE = re.compile(
    r"liều chiếu xạ|liều lượng|thời gian chiếu xạ|mất bao lâu|quy trình chiếu xạ|"
    r"quy trình gửi hàng",
    re.IGNORECASE,
)
TAVILY_DISCLAIMER = (
    "⚠️ Thông tin mang tính chất tham khảo, chưa được công ty xác nhận chính thức:"
)


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


def _is_only_tuvan(text: str) -> bool:
    """True when message is exactly 'tư vấn' (optional trailing !/?/.)."""
    cleaned = text.strip().rstrip("!?.").strip()
    return cleaned.lower() == "tư vấn"


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
        self.lead_registry = PendingLeadRegistry()

    def reload_bindings(self) -> None:
        self.bindings.reload()

    def is_declared_group(self, group_id: str) -> bool:
        return self.bindings.is_declared(group_id)

    async def handle(self, event: dict[str, Any]) -> dict[str, object] | None:
        text = str(event.get("text", ""))
        group_id = str(event.get("group_id", ""))
        sender_gender = str(event.get("sender_gender", "unknown"))
        sender_id = str(event.get("sender_id", ""))

        logger.info("RAW MESSAGE: group=%s, sender=%s, text='%s'", group_id, sender_id, text)

        # ADMIN command: /tomtat [hours]
        if text.strip().lower().startswith("/tomtat"):
            if sender_id not in (self.settings.admin_user_ids or []):
                return None
            parts = text.strip().split()
            hours = 24
            if len(parts) >= 2:
                try:
                    hours = int(parts[1])
                except Exception:
                    hours = 24
            try:
                rows = (self.repo.recent_logs(limit=1000) if self.repo else [])
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                items = [
                    r for r in rows
                    if r.get("group_id") == group_id
                    and datetime.fromisoformat(r.get("created_at").replace("Z", "+00:00")) >= cutoff
                ]
            except Exception:
                items = []
            context = "\n".join(
                f"[{r.get('created_at')}] {r.get('sender_name')}: {r.get('text')}" for r in items
            )
            system = (
                "Tóm tắt ngắn (5 ý chính, quyết định, việc cần làm, câu hỏi chưa trả lời) "
                "theo phong cách persona nội bộ."
            )
            llm_resp = await self.router.llm.chat(context or "Không có tin nào.", system=system)
            return {"answer": llm_resp.get("answer", ""), "model_used": llm_resp.get("model_used"), "sources": []}

        if not self.is_declared_group(group_id):
            return None

        # Order lookup: detect order codes like DH1001 or "đơn DH1001"
        order_match = re.search(r"DH\d+", text, re.IGNORECASE)
        if order_match:
            order_id = order_match.group(0).upper()
            logger.info("Order detection: matched DH with order_id=%s", order_id)
            try:
                from app.services.lead_capture import get_order_from_airtable

                order = await get_order_from_airtable(order_id)
                if order:
                    answer = (
                        f"Đơn {order_id}: status={order.get('status')}, "
                        f"received_at={order.get('received_at')}, note={order.get('note', '')}"
                    )
                    logger.info("Order lookup: found order %s: %s", order_id, order)
                else:
                    answer = "Em chưa tìm thấy mã đơn. Vui lòng kiểm tra lại mã đơn."
                    logger.info("Order lookup: no order found for order_id=%s", order_id)
            except Exception as exc:
                logger.exception("Order lookup: exception for order_id=%s: %s", order_id, exc)
                answer = "Em không truy xuất được dữ liệu đơn lúc này."
            return {"answer": answer, "model_used": None, "sources": []}

        if self._resolve_group_type(group_id) == "customer":
            customer_result = await self._handle_customer_intents(
                group_id=group_id,
                sender_id=sender_id,
                text=text,
            )
            if customer_result is not None:
                return customer_result

        group_type = self._resolve_group_type(group_id)
        if group_type is None:
            return None

        if not contains_bot_tag(text, self.settings.bot_tags):
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

    async def _handle_customer_intents(
        self,
        *,
        group_id: str,
        sender_id: str,
        text: str,
    ) -> dict[str, object] | None:
        """Customer-group intent routing (2.1 → 2.5). Returns None to fall through to bot-tag flow."""

        # 2.1a — pending contact response
        pending_lead = await self.lead_registry.pop_if_contact_response(group_id, sender_id, text)
        if pending_lead:
            logger.info("Lead capture: contact response for sender_id=%s, lead=%s", sender_id, pending_lead)
            created = await create_lead_via_airtable(pending_lead)
            if created is not None:
                logger.info("Lead capture: lead created in Airtable: %s", created)
                return {"answer": LEAD_SUCCESS_REPLY, "model_used": None, "sources": []}
            logger.warning("Lead capture: failed to create lead in Airtable for %s", pending_lead)
            return {"answer": LEAD_FAIL_REPLY, "model_used": None, "sources": []}

        # 2.1b — pending "tư vấn gì": pop and re-run classification below
        was_consultation_reply = await self.lead_registry.pop_consultation_pending(group_id, sender_id)
        if was_consultation_reply:
            logger.info("Consultation pending cleared for sender_id=%s, re-classifying", sender_id)

        # 2.2 — contact / price intent (merged)
        if CONTACT_INTENT_RE.search(text):
            logger.info(
                "Lead capture: contact intent for sender_id=%s, group_id=%s, text='%s'",
                sender_id,
                group_id,
                text,
            )
            await self.lead_registry.set_contact_pending(group_id, sender_id, need=text)
            return {"answer": CONTACT_REQUEST_MSG, "model_used": None, "sources": []}

        # 2.3 — company info (fixed reply, no LLM)
        if COMPANY_INFO_RE.search(text):
            return {"answer": COMPANY_INFO_REPLY, "model_used": None, "sources": []}

        # 2.4 — technical questions (RAG → Tavily fallback → LLM)
        if TECHNICAL_RE.search(text):
            return await self._handle_technical_question(text)

        # 2.5 — only "tư vấn"
        if _is_only_tuvan(text):
            await self.lead_registry.set_consultation_pending(group_id, sender_id)
            return {"answer": CONSULTATION_PROMPT, "model_used": None, "sources": []}

        # 2.6 — fall through, unless this was a consultation reply with no other match
        if was_consultation_reply:
            logger.info(
                "Consultation reply with no intent match for sender_id=%s, falling back to contact",
                sender_id,
            )
            await self.lead_registry.set_contact_pending(group_id, sender_id, need=text)
            return {"answer": CONTACT_REQUEST_MSG, "model_used": None, "sources": []}

        return None

    async def _handle_technical_question(self, text: str) -> dict[str, object]:
        sources = self.router.rag.search("customer", text, top_k=3)
        if sources:
            docs = "\n\n".join(
                f"### {item['heading']}\n{item['content']}" for item in sources
            )
            system = (
                "Bạn là trợ lý khách hàng của công ty chiếu xạ. "
                "Trả lời câu hỏi dựa trên tài liệu nội bộ sau, ngắn gọn và chính xác:\n\n"
                f"{docs}"
            )
        else:
            web_results = await tavily_search(text, max_results=3)
            if web_results:
                docs = "\n\n".join(
                    f"### {item.get('title', '')}\n{item.get('content', '')}"
                    for item in web_results
                )
                system = (
                    "Bạn là trợ lý khách hàng của công ty chiếu xạ. "
                    "Trả lời câu hỏi dựa trên kết quả tìm kiếm web sau. "
                    f"BẮT BUỘC mở đầu câu trả lời bằng dòng chính xác:\n{TAVILY_DISCLAIMER}\n\n"
                    f"{docs}"
                )
                sources = web_results
            else:
                system = (
                    "Bạn là trợ lý khách hàng của công ty chiếu xạ. "
                    "Trả lời câu hỏi kỹ thuật nếu có thể; nếu không chắc, hướng dẫn liên hệ hotline."
                )

        llm_resp = await self.router.llm.chat(text, system=system)
        return {
            "answer": llm_resp.get("answer", ""),
            "model_used": llm_resp.get("model_used"),
            "sources": sources,
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
