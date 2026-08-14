"""Lead capture helper: in-memory pending leads + Airtable integration."""

from __future__ import annotations

import time
import re
import asyncio
from typing import Any

import httpx

from app.config import get_settings

PHONE_RE = re.compile(r"0\d{9,10}")


class PendingLeadRegistry:
    """Simple in-memory pending lead registry with expiry."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._store: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set_pending(self, group_id: str, sender_id: str, need: str, name: str | None = None) -> None:
        async with self._lock:
            self._store[(group_id, sender_id)] = {
                "need": need,
                "name": name,
                "ts": time.time(),
            }

    async def pop_if_phone(self, group_id: str, sender_id: str, text: str) -> dict[str, Any] | None:
        """If a pending lead exists and text contains a phone, return a merged lead dict and remove pending."""
        m = PHONE_RE.search(text or "")
        if not m:
            return None
        async with self._lock:
            key = (group_id, sender_id)
            entry = self._store.get(key)
            if not entry:
                return None
            # check expiry
            if time.time() - entry.get("ts", 0) > self._ttl:
                self._store.pop(key, None)
                return None
            phone = m.group(0)
            lead = {
                "name": entry.get("name") or "",
                "phone": phone,
                "need": entry.get("need") or "",
                "source_group": group_id,
            }
            self._store.pop(key, None)
            return lead

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
        return None
    url = f"https://api.airtable.com/v0/{settings.airtable_base_id}/Leads"
    headers = {"Authorization": f"Bearer {settings.airtable_api_key}", "Content-Type": "application/json"}
    payload = {"fields": {"Name": lead.get("name"), "Phone": lead.get("phone"), "Need": lead.get("need"), "Source": lead.get("source_group"), "Status": "new"}}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 200 and resp.status_code < 300:
                return resp.json()
    except Exception:
        return None
    return None


async def get_order_from_airtable(order_id: str) -> dict[str, Any] | None:
    """Query Airtable `orders` table by order_id. Returns fields dict or None."""
    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        return None
    url = f"https://api.airtable.com/v0/{settings.airtable_base_id}/orders"
    headers = {"Authorization": f"Bearer {settings.airtable_api_key}"}
    params = {"filterByFormula": f"{{order_id}}='{order_id}'", "maxRecords": 1}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 200 and resp.status_code < 300:
                body = resp.json()
                records = body.get("records") or []
                if not records:
                    return None
                fields = records[0].get("fields", {})
                # normalize to expected keys
                return {
                    "status": fields.get("status"),
                    "received_at": fields.get("received_at"),
                    "note": fields.get("note"),
                }
    except Exception:
        return None
    return None
