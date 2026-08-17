"""Lead capture helper: in-memory pending leads + Airtable integration."""

from __future__ import annotations

import logging
import time
import re
import asyncio
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"0\d{9,10}")


def parse_contact_response(text: str) -> dict[str, str]:
    """Parse contact info from a single customer message.

    Priority 1: numbered lines (1. Công ty / 2. SĐT / 3. Tên).
    Priority 2: find phone via PHONE_RE; store remainder in name, company empty.
    """
    company = ""
    phone = ""
    name = ""

    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    numbered: dict[int, str] = {}
    for line in lines:
        match = re.match(r"^(\d+)\.\s*(.+)$", line)
        if match:
            numbered[int(match.group(1))] = match.group(2).strip()

    if numbered:
        company = numbered.get(1, "")
        phone_val = numbered.get(2, "")
        phone_match = PHONE_RE.search(phone_val)
        if phone_match:
            phone = phone_match.group(0)
        name = numbered.get(3, "")
        if not phone:
            for value in numbered.values():
                fallback = PHONE_RE.search(value)
                if fallback:
                    phone = fallback.group(0)
                    break
        return {"company": company, "phone": phone, "name": name}

    # Fallback: phone required; remainder → name (company left empty).
    phone_match = PHONE_RE.search(text or "")
    if phone_match:
        phone = phone_match.group(0)
        remainder = PHONE_RE.sub("", text or "").strip()
        remainder = re.sub(r"^\d+\.\s*", "", remainder, flags=re.MULTILINE).strip()
        name = remainder
    return {"company": company, "phone": phone, "name": name}


class PendingLeadRegistry:
    """In-memory pending states for customer-group flows (TTL 600s)."""

    KIND_CONTACT = "contact"
    KIND_CONSULTATION = "consultation"

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._store: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set_contact_pending(self, group_id: str, sender_id: str, need: str) -> None:
        async with self._lock:
            self._store[(group_id, sender_id)] = {
                "kind": self.KIND_CONTACT,
                "need": need,
                "ts": time.time(),
            }

    async def set_consultation_pending(self, group_id: str, sender_id: str) -> None:
        async with self._lock:
            self._store[(group_id, sender_id)] = {
                "kind": self.KIND_CONSULTATION,
                "ts": time.time(),
            }

    async def pop_if_contact_response(
        self,
        group_id: str,
        sender_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        """If contact pending and text has a phone, return lead dict and clear pending."""
        parsed = parse_contact_response(text)
        if not parsed.get("phone"):
            return None

        async with self._lock:
            key = (group_id, sender_id)
            entry = self._store.get(key)
            if not entry or entry.get("kind") != self.KIND_CONTACT:
                return None
            if time.time() - entry.get("ts", 0) > self._ttl:
                self._store.pop(key, None)
                return None

            lead = {
                "name": parsed.get("name") or "",
                "phone": parsed["phone"],
                "company": parsed.get("company") or "",
                "need": entry.get("need") or "",
                "source_group": group_id,
            }
            self._store.pop(key, None)
            return lead

    async def pop_consultation_pending(self, group_id: str, sender_id: str) -> bool:
        """If consultation pending exists and valid, pop it and return True."""
        async with self._lock:
            key = (group_id, sender_id)
            entry = self._store.get(key)
            if not entry or entry.get("kind") != self.KIND_CONSULTATION:
                return False
            if time.time() - entry.get("ts", 0) > self._ttl:
                self._store.pop(key, None)
                return False
            self._store.pop(key, None)
            return True

    async def has_pending(self, group_id: str, sender_id: str) -> bool:
        async with self._lock:
            entry = self._store.get((group_id, sender_id))
            if not entry:
                return False
            if time.time() - entry.get("ts", 0) > self._ttl:
                self._store.pop((group_id, sender_id), None)
                return False
            return True


async def create_lead_via_airtable(lead: dict[str, Any]) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        logger.warning("create_lead_via_airtable: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not configured")
        return None

    url = f"https://api.airtable.com/v0/{settings.airtable_base_id}/leads"
    headers = {"Authorization": f"Bearer {settings.airtable_api_key}", "Content-Type": "application/json"}
    payload = {
        "fields": {
            "name": lead.get("name"),
            "phone": lead.get("phone"),
            "company": lead.get("company"),
            "need": lead.get("need"),
            "source_group": lead.get("source_group"),
            "status": "new",
        }
    }

    logger.info("create_lead_via_airtable: POST %s", url)
    logger.info("create_lead_via_airtable: payload=%s", payload)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            logger.info("create_lead_via_airtable: HTTP %s", resp.status_code)
            logger.info("create_lead_via_airtable: response_body=%s", resp.text)

            if resp.status_code >= 200 and resp.status_code < 300:
                return resp.json()
            logger.error("create_lead_via_airtable: HTTP %s - Full error: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("create_lead_via_airtable: Exception: %s", exc)
        return None
    return None


async def get_order_from_airtable(order_id: str) -> dict[str, Any] | None:
    """Query Airtable `orders` table by order_id. Returns fields dict or None."""
    logger.info(">>> get_order_from_airtable: CALLED with order_id=%s", order_id)

    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        logger.warning(
            ">>> get_order_from_airtable: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not configured "
            "(api_key=%s, base_id=%s)",
            bool(settings.airtable_api_key),
            bool(settings.airtable_base_id),
        )
        return None

    url = f"https://api.airtable.com/v0/{settings.airtable_base_id}/orders"
    headers = {"Authorization": f"Bearer {settings.airtable_api_key}"}
    filter_formula = f"{{order_id}}='{order_id}'"
    params = {"filterByFormula": filter_formula, "maxRecords": 1}

    logger.info("get_order_from_airtable: GET %s", url)
    logger.info("get_order_from_airtable: filter_formula=%s", filter_formula)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            logger.info("get_order_from_airtable: HTTP %s", resp.status_code)
            logger.info("get_order_from_airtable: response_body=%s", resp.text)

            if resp.status_code >= 200 and resp.status_code < 300:
                body = resp.json()
                records = body.get("records") or []
                if not records:
                    logger.info(">>> get_order_from_airtable: No records found for order_id=%s", order_id)
                    return None
                fields = records[0].get("fields", {})
                logger.info(">>> get_order_from_airtable: FOUND order %s: %s", order_id, fields)
                return {
                    "status": fields.get("status"),
                    "received_at": fields.get("received_at"),
                    "note": fields.get("note"),
                }
            logger.error("get_order_from_airtable: HTTP %s - Full error: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("get_order_from_airtable: Exception: %s", exc)
        return None
    return None
