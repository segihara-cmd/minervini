"""
ETF 구성종목 역인덱스 및 기술·수익 지표 산출.

- 구성: 네이버 금융 ETF 상세 (finsum_more)
- 시세: FinanceDataReader
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config.settings import ETF_HOLDINGS_CACHE, REQUEST_HEADERS, REQUEST_TIMEOUT
from utils.rate_limit import throttle

logger = logging.getLogger(__name__)

NAVER_ETF_COMPOSITION = (
    "https://finance.naver.com/item/coinfo.naver?code={code}&target=finsum_more"
)

METRIC_COLUMNS = [
    "ETF코드",
    "ETF명",
    "편입종목코드",
    "편입종목명",
    "현재가",
    "거래량",
    "SMA50-SMA150",
    "SMA150-SMA200",
    "샤프지수",
    "3개월수익률(%)",
    "6개월수익률(%)",
    "6개월변동성(%)",
    "소르티노지수",
    "RSI",
    "MACD_Hist",
]


def load_etf_listing() -> pd.DataFrame:
    """KRX ETF 목록 (Symbol, Name)."""
    listing = fdr.StockListing("ETF/KR")
    df = pd.DataFrame(
        {
            "etf_ticker": listing["Symbol"].astype(str).str.zfill(6),
            "etf_name": listing["Name"].astype(str),
        }
    )
    return df.drop_duplicates("etf_ticker")


def _fetch_etf_holdings(etf_code: str) -> set[str]:
    """ETF 페이지에서 편입 종목 코드 집합."""
    url = NAVER_ETF_COMPOSITION.format(code=etf_code)
    throttle(0.25)
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.encoding = "euc-kr"
    resp.raise_for_status()
    codes = set(re.findall(r"/item/main\.naver\?code=(\d{6})", resp.text))
    codes.discard(etf_code)
    return codes


def build_holdings_cache(
    force_refresh: bool = False,
    max_etfs: int | None = None,
) -> dict[str, list[str]]:
    """
    ETF → 편입 종목 코드 맵 (파일 캐시).

    Returns
    -------
    dict
        etf_ticker -> [stock_ticker, ...]
    """
    if ETF_HOLDINGS_CACHE.exists() and not force_refresh:
        try:
            data = json.loads(ETF_HOLDINGS_CACHE.read_text(encoding="utf-8"))
            if data:
                return {k: list(v) for k, v in data.items()}
        except Exception:
            pass

    listing = load_etf_listing()
    if max_etfs and max_etfs > 0:
        listing = listing.head(max_etfs)

    holdings_map: dict[str, list[str]] = {}
    total = len(listing)

    for i, row in listing.iterrows():
        etf = str(row["etf_ticker"]).zfill(6)
        try:
            codes = sorted(_fetch_etf_holdings(etf))
            holdings_map[etf] = codes
        except Exception as exc:
            logger.debug("ETF %s 구성 실패: %s", etf, exc)
            holdings_map[etf] = []

        if (i + 1) % 50 == 0 or i + 1 == total:
            logger.info("ETF 구성 수집: %d/%d", i + 1, total)

    ETF_HOLDINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ETF_HOLDINGS_CACHE.write_text(
        json.dumps(holdings_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return holdings_map


def find_etfs_for_stocks(
    stock_tickers: list[str],
    holdings_map: dict[str, list[str]] | None = None,
    etf_names: dict[str, str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """종목별 편입 ETF 전체 (코드, 이름)."""
    if holdings_map is None:
        holdings_map = build_holdings_cache()

    if etf_names is None:
        listing = load_etf_listing()
        etf_names = dict(zip(listing["etf_ticker"], listing["etf_name"]))

    targets = {str(t).zfill(6) for t in stock_tickers}
    result: dict[str, list[tuple[str, str]]] = {t: [] for t in targets}

    for etf_code, members in holdings_map.items():
        member_set = set(members)
        hit = targets & member_set
        if not hit:
            continue
        name = etf_names.get(etf_code, "")
        for stock in hit:
            result[stock].append((etf_code, name))

    for stock in result:
        result[stock].sort(key=lambda x: x[0])
    return result


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    return float(val.iloc[-1]) if not val.empty and not np.isnan(val.iloc[-1]) else np.nan


def _macd_hist(close: pd.Series) -> float:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return float(hist.iloc[-1]) if not hist.empty else np.nan


def compute_etf_metrics(etf_ticker: str) -> dict[str, float | int | str]:
    """단일 ETF 기술·수익 지표."""
    code = str(etf_ticker).zfill(6)
    end = datetime.now()
    start = end - timedelta(days=400)
    try:
        px = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except Exception as exc:
        logger.warning("ETF %s 시세 실패: %s", code, exc)
        return {}

    if px.empty or "Close" not in px.columns:
        return {}

    close = px["Close"].dropna()
    volume = px["Volume"].dropna() if "Volume" in px.columns else pd.Series(dtype=float)

    if len(close) < 200:
        return {}

    sma50 = close.rolling(50).mean().iloc[-1]
    sma150 = close.rolling(150).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    cur = float(close.iloc[-1])

    ret = close.pct_change().dropna()
    vol_daily = ret.tail(126).std() if len(ret) >= 126 else ret.std()
    vol_6m = float(vol_daily * np.sqrt(252) * 100) if not np.isnan(vol_daily) else np.nan

    def _period_return(days: int) -> float:
        if len(close) <= days:
            return np.nan
        return float((close.iloc[-1] / close.iloc[-1 - days] - 1) * 100)

    ret_3m = _period_return(63)
    ret_6m = _period_return(126)

    rf_daily = 0.03 / 252
    excess = ret.tail(126) - rf_daily
    sharpe = np.nan
    sortino = np.nan
    if len(excess) > 5 and excess.std() > 0:
        sharpe = float(excess.mean() / excess.std() * np.sqrt(252))
        downside = excess[excess < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = float(excess.mean() / downside.std() * np.sqrt(252))

    return {
        "현재가": cur,
        "거래량": int(volume.iloc[-1]) if not volume.empty else np.nan,
        "SMA50-SMA150": float(sma50 - sma150),
        "SMA150-SMA200": float(sma150 - sma200),
        "샤프지수": sharpe,
        "3개월수익률(%)": ret_3m,
        "6개월수익률(%)": ret_6m,
        "6개월변동성(%)": vol_6m,
        "소르티노지수": sortino,
        "RSI": _rsi(close),
        "MACD_Hist": _macd_hist(close),
    }


def build_etf_metrics_table(
    stock_tickers: list[str],
    stock_names: dict[str, str] | None = None,
    holdings_map: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """
    종목별 편입 ETF 전체에 대해 지표 테이블 생성.
    """
    stock_names = stock_names or {}
    etf_by_stock = find_etfs_for_stocks(stock_tickers, holdings_map=holdings_map)

    rows: list[dict] = []
    for stock in stock_tickers:
        code = str(stock).zfill(6)
        sname = stock_names.get(code, "")
        for etf_code, etf_name in etf_by_stock.get(code, []):
            metrics = compute_etf_metrics(etf_code)
            row = {
                "ETF코드": etf_code,
                "ETF명": etf_name,
                "편입종목코드": code,
                "편입종목명": sname,
            }
            row.update(metrics)
            rows.append(row)
            time.sleep(0.05)

    if not rows:
        return pd.DataFrame(columns=METRIC_COLUMNS)

    return pd.DataFrame(rows)[METRIC_COLUMNS]
