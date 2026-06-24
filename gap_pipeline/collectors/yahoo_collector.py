"""
Yahoo Finance(yfinance) 기반 한국 주식 현재가 수집기.

- KOSPI: {ticker}.KS
- KOSDAQ: {ticker}.KQ
- 다종목 배치 조회 지원 (전체 리포트 수집 모드)
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from collectors.base import BaseCollector
from config.settings import DEFAULT_TICKERS, YAHOO_BATCH_SIZE
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)


def _to_yahoo_symbol(ticker: str, market: str = "KS") -> str:
    code = str(ticker).zfill(6)
    return f"{code}.{market}"


class YahooCollector(BaseCollector):
    """Yahoo Finance에서 현재가·종목명 등 시세 데이터를 수집합니다."""

    source_name = "yahoo"

    @retry_on_failure()
    def _fetch_single(self, ticker: str) -> dict[str, Any]:
        code = self.normalize_ticker(ticker)
        last_error: Exception | None = None

        for market in ("KS", "KQ"):
            symbol = _to_yahoo_symbol(code, market)
            try:
                stock = yf.Ticker(symbol)
                info = stock.info or {}
                hist = stock.history(period="5d")

                current_price = (
                    info.get("currentPrice")
                    or info.get("regularMarketPrice")
                    or (hist["Close"].iloc[-1] if not hist.empty else None)
                )

                if current_price is None:
                    continue

                return {
                    "ticker": code,
                    "stock_name": info.get("longName") or info.get("shortName") or "",
                    "current_price": float(current_price),
                    "market": market,
                    "currency": info.get("currency", "KRW"),
                }
            except Exception as exc:
                last_error = exc
                logger.debug("Yahoo %s 조회 실패: %s", symbol, exc)

        raise RuntimeError(f"Yahoo Finance 조회 실패: {code}") from last_error

    def _fetch_batch_chunk(
        self, tickers: list[str], market: str = "KS"
    ) -> dict[str, dict[str, Any]]:
        """yfinance.download 로 청크 단위 시세 조회."""
        symbols = [_to_yahoo_symbol(t, market) for t in tickers]
        result: dict[str, dict[str, Any]] = {}

        try:
            data = yf.download(
                symbols,
                period="5d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            logger.warning("Yahoo 배치(%s) 실패: %s", market, exc)
            return result

        if data is None or data.empty:
            return result

        for code in tickers:
            sym = _to_yahoo_symbol(code, market)
            try:
                if len(symbols) == 1:
                    close = data["Close"].dropna()
                else:
                    close = data[sym]["Close"].dropna()
                if close.empty:
                    continue
                price = float(close.iloc[-1])
                result[code] = {
                    "ticker": code,
                    "stock_name": "",
                    "current_price": price,
                    "market": market,
                    "currency": "KRW",
                }
            except (KeyError, TypeError, AttributeError):
                continue

        return result

    def collect_batch(
        self,
        tickers: list[str],
        chunk_size: int | None = None,
    ) -> pd.DataFrame:
        """
        다수 종목 현재가 배치 수집.

        KOSPI(.KS) 우선, 실패 종목만 KOSDAQ(.KQ) 재시도.
        """
        codes = [self.normalize_ticker(t) for t in tickers]
        codes = list(dict.fromkeys(codes))  # 순서 유지 중복 제거
        if not codes:
            return pd.DataFrame(
                columns=["ticker", "stock_name", "current_price", "market", "currency"]
            )

        size = chunk_size or YAHOO_BATCH_SIZE
        merged: dict[str, dict[str, Any]] = {}

        for i in range(0, len(codes), size):
            chunk = codes[i : i + size]
            got = self._fetch_batch_chunk(chunk, market="KS")
            merged.update(got)
            missing = [c for c in chunk if c not in merged]
            if missing:
                kq = self._fetch_batch_chunk(missing, market="KQ")
                merged.update(kq)
            logger.info(
                "Yahoo 배치 진행: %d/%d 종목",
                min(i + size, len(codes)),
                len(codes),
            )

        # 배치에 이름이 없으면 단건 info 보강 (선택적, 느림) — 생략, 네이버/DART 이름 사용

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
        targets = tickers or DEFAULT_TICKERS
        codes = [self.normalize_ticker(t) for t in targets]

        # 다종목 + batch=True 이면 배치 모드
        if batch and len(codes) > 5:
            return self.collect_batch(codes)

        rows: list[dict[str, Any]] = []
        for code in codes:
            try:
                rows.append(self._fetch_single(code))
            except Exception as exc:
                logger.error("종목 %s Yahoo 수집 실패: %s", code, exc)

        if not rows:
            return pd.DataFrame(
                columns=["ticker", "stock_name", "current_price", "market", "currency"]
            )
        return pd.DataFrame(rows)
