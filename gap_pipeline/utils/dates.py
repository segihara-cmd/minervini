"""
날짜 정규화·비교 유틸.
"""

from __future__ import annotations

import pandas as pd


def parse_report_date(value) -> pd.Timestamp | None:
    """리포트 발표일 → pandas Timestamp (비교용)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    ts = pd.to_datetime(text, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def is_valid_previous_date(
    current: pd.Timestamp | None, previous: pd.Timestamp | None
) -> bool:
    """이전 발표일이 현재 발표일보다 반드시 이전인지 검증."""
    if current is None or previous is None:
        return previous is not None
    return previous < current
