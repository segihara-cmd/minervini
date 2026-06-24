"""
요청 간격 제어 — 크롤링 차단 완화.
"""

from __future__ import annotations

import time
from threading import Lock

_lock = Lock()
_last_request_at: float = 0.0


def throttle(delay_seconds: float) -> None:
    """전역 최소 요청 간격 유지."""
    global _last_request_at
    if delay_seconds <= 0:
        return
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_request_at
        if elapsed < delay_seconds:
            time.sleep(delay_seconds - elapsed)
        _last_request_at = time.monotonic()
