"""
Yahoo Finance 글로벌 애널리스트 컨센서스 수집기.

개별 증권사명은 제공되지 않으나, 해외 포함 통합 컨센서스(평균·중간·고저)를
공개 API(yfinance)로 수집합니다. Refinitiv/야후 집계 데이터입니다.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from collectors.base import BaseCollector
from config.settings import DEFAULT_TICKERS
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)


def _to_symbol(ticker: str, market: str = "KS") -> str:
    return f"{ticker.zfill(6)}.{market}"


class YahooGlobalCollector(BaseCollector):
    """Yahoo Finance 글로벌 컨센서스 (집계) 수집."""

    source_name = "yahoo_global"
    data_region = "foreign"

    @retry_on_failure()
    def fetch_consensus(self, ticker: str) -> list[dict[str, Any]]:
        code = self.normalize_ticker(ticker)
        rows: list[dict[str, Any]] = []

        for market in ("KS", "KQ"):
            sym = _to_symbol(code, market)
            try:
                stock = yf.Ticker(sym)
                targets = stock.analyst_price_targets or {}
                info = stock.info or {}
                if not targets and not info.get("targetMeanPrice"):
                    continue

                mean = targets.get("mean") or info.get("targetMeanPrice")
                median = targets.get("median") or info.get("targetMedianPrice")
                high = targets.get("high") or info.get("targetHighPrice")
                low = targets.get("low") or info.get("targetLowPrice")
                rec_key = info.get("recommendationKey", "")
                n_analysts = info.get("numberOfAnalystOpinions")

                consensus_rows = [
                    ("평균", mean),
                    ("중간값", median),
                    ("최고", high),
                    ("최저", low),
                ]
                for label, price in consensus_rows:
                    if price is None:
                        continue
                    rows.append(
                        {
                            "ticker": code,
                            "stock_name": info.get("shortName") or info.get("longName") or "",
                            "securities_company": f"글로벌 컨센서스 ({label}, Yahoo/Refinitiv)",
                            "report_date": pd.Timestamp.now().strftime("%Y-%m-%d"),
                            "target_price": float(price),
                            "previous_target_price": None,
                            "target_revision_pct": None,
                            "opinion": rec_key or "",
                            "data_source": "yahoo_finance",
                            "source_region": "foreign",
                            "report_nid": f"yahoo_{code}_{label}",
                            "analyst_count": n_analysts,
                        }
                    )
                break
            except Exception as exc:
                logger.debug("Yahoo global %s.%s: %s", code, market, exc)

        return rows

    def collect(
        self,
        tickers: list[str] | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        targets = tickers or DEFAULT_TICKERS
        rows: list[dict[str, Any]] = []
        for raw in targets:
            code = self.normalize_ticker(raw)
            try:
                rows.extend(self.fetch_consensus(code))
            except Exception as exc:
                logger.error("Yahoo global %s 실패: %s", code, exc)

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
