"""
산업별 대표 종목 선별 — 네이버 업종 × 거래대금 상위 N.

업종 분류: https://stock.naver.com/market/stock/kr/industry/1
"""

from __future__ import annotations

import logging

import pandas as pd

from collectors.naver_industry_collector import select_leaders_by_trading_amount
from config.settings import SECTOR_LEADERS_PER_SECTOR

logger = logging.getLogger(__name__)


def select_industry_leaders(
    per_sector: int | None = None,
    *,
    force_refresh_cache: bool = False,
) -> pd.DataFrame:
    """
    네이버 업종별 거래대금 상위 N종목.

    Returns
    -------
    pd.DataFrame
        sector, ticker, stock_name, market, trading_amount, marcap, industry_no
    """
    n = per_sector or SECTOR_LEADERS_PER_SECTOR
    df = select_leaders_by_trading_amount(
        per_sector=n,
        force_refresh_cache=force_refresh_cache,
    )
    if df.empty:
        logger.warning("업종 대표주 없음 — naver_industry_stocks.json 확인")
        return df

    for sector_name, grp in df.groupby("sector"):
        codes = ", ".join(grp["ticker"].tolist())
        logger.info("업종 %s: %d종목 — %s", sector_name, len(grp), codes)

    return df
