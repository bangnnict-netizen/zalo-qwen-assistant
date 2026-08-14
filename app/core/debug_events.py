"""In-memory ring buffer for raw inbound events for debugging."""

from __future__ import annotations

import time
import threading
from collections import deque
from typing import Any

_LOCK = threading.Lock()
_BUFFER: deque[dict[str, Any]] = deque(maxlen=200)


def record_event(payload: dict[str, Any]) -> None:
    """Append a new event snapshot (thread-safe)."""
    entry = {"ts": time.time(), "payload": payload}
    with _LOCK:
        _BUFFER.append(entry)


def recent_events() -> list[dict[str, Any]]:
    """Return a shallow copy of recent events (oldest->newest)."""
    with _LOCK:
        return list(_BUFFER)
