"""Unit tests for lead capture parsing and pending registry."""

from __future__ import annotations

import pytest

from app.services.lead_capture import PendingLeadRegistry, parse_contact_response


def test_parse_contact_numbered_format() -> None:
    text = "1. CTy ABC\n2. 0987654321\n3. Nguyễn Văn A"
    parsed = parse_contact_response(text)
    assert parsed["company"] == "CTy ABC"
    assert parsed["phone"] == "0987654321"
    assert parsed["name"] == "Nguyễn Văn A"


def test_parse_contact_fallback_phone_only() -> None:
    text = "Công ty XYZ - Nguyễn B - 0912345678"
    parsed = parse_contact_response(text)
    assert parsed["phone"] == "0912345678"
    assert parsed["company"] == ""
    assert "Nguyễn B" in parsed["name"]


def test_parse_contact_no_phone_returns_empty() -> None:
    parsed = parse_contact_response("Chỉ có tên thôi")
    assert parsed["phone"] == ""


@pytest.mark.asyncio
async def test_contact_pending_and_response() -> None:
    registry = PendingLeadRegistry()
    await registry.set_contact_pending("g1", "u1", need="muốn liên hệ")
    assert await registry.has_pending("g1", "u1")

    lead = await registry.pop_if_contact_response("g1", "u1", "1. ABC\n2. 0901234567\n3. An")
    assert lead is not None
    assert lead["company"] == "ABC"
    assert lead["phone"] == "0901234567"
    assert lead["name"] == "An"
    assert lead["need"] == "muốn liên hệ"
    assert not await registry.has_pending("g1", "u1")


@pytest.mark.asyncio
async def test_consultation_pending_pop() -> None:
    registry = PendingLeadRegistry()
    await registry.set_consultation_pending("g1", "u1")
    assert await registry.pop_consultation_pending("g1", "u1")
    assert not await registry.has_pending("g1", "u1")


@pytest.mark.asyncio
async def test_contact_pending_ignores_consultation_kind() -> None:
    registry = PendingLeadRegistry()
    await registry.set_consultation_pending("g1", "u1")
    lead = await registry.pop_if_contact_response("g1", "u1", "0901234567")
    assert lead is None
