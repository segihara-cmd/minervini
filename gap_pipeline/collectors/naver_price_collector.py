"""네이버 모바일 API 현재가 수집 (KRX 실시간 closePrice)."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import requests

from collectors.base import BaseCollector
from config.settings import REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://m.stock.naver.com/",
}


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(text).replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_naver_price(ticker: str) -> float | None:
    code = str(ticker).zfill(6)
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return _parse_price(resp.json().get("closePrice"))
    except Exception as exc:
        logger.debug("Naver price %s 실패: %s", code, exc)
        return None


class NaverPriceCollector(BaseCollector):
    """네이버 금융 모바일 API에서 KRX 현재가를 수집합니다."""

    source_name = "naver"

    def collect_batch(
        self,
        tickers: list[str],
        workers: int = 8,
    ) -> pd.DataFrame:
        codes = list(dict.fromkeys(self.normalize_ticker(t) for t in tickers))
        if not codes:
            return pd.DataFrame(
                columns=["ticker", "stock_name", "current_price", "market", "currency"]
            )

        merged: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_naver_price, c): c for c in codes}
            for fut in as_completed(futures):
                code = futures[fut]
                try:
                    price = fut.result()
                    if price is not None:
                        merged[code] = {
                            "ticker": code,
                            "stock_name": "",
                            "current_price": price,
                            "market": "",
                            "currency": "KRW",
                        }
                except Exception as exc:
                    logger.debug("Naver batch %s: %s", code, exc)

        if not merged:
            return pd.DataFrame(
                columns=["ticker", "stock_name", "current_price", "market", "currency"]
            )
        return pd.DataFrame(merged.values())

    def collect(
        self,
        tickers: list[str] | None = None,
        batch: bool = True,
        **kwargs: Any,
    ) -> pd.DataFrame:
        targets = tickers or []
        workers = int(kwargs.get("workers", 8))
        return self.collect_batch(targets, workers=workers)
