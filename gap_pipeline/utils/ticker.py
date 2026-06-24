"""종목코드(6자리) 정규화."""

from __future__ import annotations

import pandas as pd


def normalize_ticker_code(ticker) -> str:
    """6자리 KRX 종목코드 문자열."""
    if ticker is None or (isinstance(ticker, float) and pd.isna(ticker)):
        return ""
    code = str(ticker).strip().upper()
    for suffix in (".KS", ".KQ", ".KR"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    if code.endswith(".0") and code[:-2].isdigit():
        code = code[:-2]
    digits = "".join(ch for ch in code if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)
