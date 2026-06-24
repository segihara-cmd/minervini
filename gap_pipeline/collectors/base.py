"""
수집기 베이스 클래스.

ETF holdings 등 새로운 데이터 소스를 추가할 때 이 인터페이스를 따릅니다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseCollector(ABC):
    """모든 데이터 수집기의 공통 인터페이스."""

    source_name: str = "base"

    @abstractmethod
    def collect(self, tickers: list[str] | None = None, **kwargs: Any) -> pd.DataFrame:
        """
        데이터를 수집하여 DataFrame으로 반환합니다.

        Parameters
        ----------
        tickers : list[str], optional
            6자리 종목코드 목록 (예: ['005930', '000660'])
        """
        ...

    def normalize_ticker(self, ticker: str) -> str:
        """6자리 숫자 종목코드로 정규화."""
        code = str(ticker).strip().upper()
        # Yahoo 접미사 제거
        for suffix in (".KS", ".KQ", ".KR"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
        return code.zfill(6)
