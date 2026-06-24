"""
KRX 전 종목 유니버스 (코스피·코스닥) 로더.

FinanceDataReader를 기본으로 사용하고, PyKRX는 보조(환경 변수 설정 시)로 사용합니다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def load_krx_universe_fdr() -> pd.DataFrame:
    """
    FinanceDataReader KRX 상장 종목 목록.

    Returns
    -------
    pd.DataFrame
        ticker, stock_name, market
    """
    import FinanceDataReader as fdr

    listing = fdr.StockListing("KRX")
    df = pd.DataFrame(
        {
            "ticker": listing["Code"].astype(str).str.zfill(6),
            "stock_name": listing["Name"].astype(str),
            "market": listing["Market"].astype(str),
        }
    )
    # ETF/ETN/스팩 등 제외 — 일반 주식 위주
    df = df[df["market"].isin(["KOSPI", "KOSDAQ", "KONEX"])]
    df = df.drop_duplicates("ticker", keep="first")
    return df.sort_values("ticker").reset_index(drop=True)


def load_krx_universe_pykrx() -> pd.DataFrame | None:
    """PyKRX 상장 종목 (KRX_ID/KRX_PW 또는 공개 API 가능 시)."""
    try:
        from pykrx import stock
    except ImportError:
        return None

    for i in range(15):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        rows: list[dict] = []
        try:
            for market in ("KOSPI", "KOSDAQ"):
                tickers = stock.get_market_ticker_list(day, market=market)
                for t in tickers:
                    rows.append(
                        {
                            "ticker": str(t).zfill(6),
                            "stock_name": stock.get_market_ticker_name(t),
                            "market": market,
                        }
                    )
            if rows:
                return pd.DataFrame(rows).drop_duplicates("ticker")
        except Exception as exc:
            logger.debug("PyKRX %s 실패: %s", day, exc)
    return None


def load_krx_universe(prefer: str | None = None) -> pd.DataFrame:
    """
    전 종목 유니버스 반환.

    Parameters
    ----------
    prefer : str, optional
        'fdr' | 'pykrx' — 미지정 시 FDR 우선
    """
    source = (prefer or os.getenv("UNIVERSE_SOURCE", "fdr")).lower()

    if source == "pykrx":
        df = load_krx_universe_pykrx()
        if df is not None and not df.empty:
            logger.info("유니버스: PyKRX %d종목", len(df))
            return df
        logger.warning("PyKRX 실패 — FinanceDataReader로 대체")

    df = load_krx_universe_fdr()
    logger.info("유니버스: FinanceDataReader %d종목", len(df))
    return df
