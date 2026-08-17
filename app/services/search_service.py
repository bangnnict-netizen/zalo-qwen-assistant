"""Tavily web search — fallback khi RAG nội bộ không đủ trả lời."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"


async def tavily_search(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Return list of {title, url, content} or [] on failure/missing key."""
    settings = get_settings()
    if not settings.tavily_api_key:
        logger.warning("tavily_search: TAVILY_API_KEY not configured")
        return []

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(TAVILY_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {"title": r.get("title"), "url": r.get("url"), "content": r.get("content")}
                    for r in data.get("results", [])
                ]
            logger.warning("tavily_search: HTTP %s - %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("tavily_search: exception: %s", exc)
    return []
