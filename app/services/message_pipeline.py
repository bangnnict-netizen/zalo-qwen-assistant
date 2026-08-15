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

logger = logging.getLogger(__name__)


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
        # in-memory pending leads
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
        
        logger.info(f"RAW MESSAGE: group={group_id}, sender={sender_id}, text='{text}'")

        # ADMIN command: /tomtat [hours]
        if text.strip().lower().startswith("/tomtat"):
            # only configured admins can use this
            if sender_id not in (self.settings.admin_user_ids or []):
                return None
            parts = text.strip().split()
            hours = 24
            if len(parts) >= 2:
                try:
                    hours = int(parts[1])
                except Exception:
                    hours = 24
            # collect recent logs for this group within hours
            try:
                rows = (self.repo.recent_logs(limit=1000) if self.repo else [])
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                items = [r for r in rows if r.get("group_id") == group_id and datetime.fromisoformat(r.get("created_at").replace("Z", "+00:00")) >= cutoff]
            except Exception:
                items = []
            # build prompt and call LLM for an organized summary
            context = "\n".join(f"[{r.get('created_at')}] {r.get('sender_name')}: {r.get('text')}" for r in items)
            system = "Tóm tắt ngắn (5 ý chính, quyết định, việc cần làm, câu hỏi chưa trả lời) theo phong cách persona nội bộ."
            llm_resp = await self.router.llm.chat(context or "Không có tin nào.", system=system)
            return {"answer": llm_resp.get("answer", ""), "model_used": llm_resp.get("model_used"), "sources": []}

        if not self.is_declared_group(group_id):
            return None

        # Order lookup: detect order codes like DH1001 or "đơn DH1001"
        order_match = re.search(r"DH\d+", text, re.IGNORECASE)
        if order_match:
            order_id = order_match.group(0).upper()
            logger.info(f"Order detection: matched DH with order_id={order_id}")
            logger.info(f"Order lookup: detected order_id={order_id} in text='{text}'")
            try:
                from app.services.lead_capture import get_order_from_airtable

                order = await get_order_from_airtable(order_id)
                if order:
                    answer = f"Đơn {order_id}: status={order.get('status')}, received_at={order.get('received_at')}, note={order.get('note','')}"
                    logger.info(f"Order lookup: found order {order_id}: {order}")
                else:
                    answer = "Em chưa tìm thấy mã đơn. Vui lòng kiểm tra lại mã đơn."
                    logger.info(f"Order lookup: no order found for order_id={order_id}")
            except Exception as exc:
                logger.exception(f"Order lookup: exception for order_id={order_id}: {exc}")
                answer = "Em không truy xuất được dữ liệu đơn lúc này."
            return {"answer": answer, "model_used": None, "sources": []}

        # Lead capture (customer groups): detect price/quote intents
        if self._resolve_group_type(group_id) == "customer":
            if re.search(r"giá|báo giá|bao nhiêu|hợp đồng", text, re.IGNORECASE):
                # set pending lead
                logger.info(f"Lead capture: price/quote intent detected for sender_id={sender_id}, group_id={group_id}, text='{text}'")
                await self.lead_registry.set_pending(group_id, sender_id, need=text)
                return {"answer": "Em xin tên + số điện thoại để bộ phận kinh doanh liên hệ ạ.", "model_used": None, "sources": []}
            # check if pending and message contains phone
            pending_lead = await self.lead_registry.pop_if_phone(group_id, sender_id, text)
            if pending_lead:
                logger.info(f"Lead capture: phone detected for sender_id={sender_id}, pending_lead={pending_lead}")
                # create via airtable
                created = await create_lead_via_airtable(pending_lead)
                if created is not None:
                    logger.info(f"Lead capture: lead created in Airtable: {created}")
                    return {"answer": "Em đã ghi nhận, nhân viên sẽ gọi lại ạ.", "model_used": None, "sources": []}
                logger.warning(f"Lead capture: failed to create lead in Airtable for {pending_lead}")
                return {"answer": "Em chưa lưu được thông tin lúc này, bạn thử lại sau nhé.", "model_used": None, "sources": []}

        group_type = self._resolve_group_type(group_id)
        if group_type is None:
            return None

        # Existing bot-tag flow: require bot tags
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
