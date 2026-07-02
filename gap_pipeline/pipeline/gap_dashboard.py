"""괴리율 대시보드 JSON (Investing 목표가 + 네이버 현재가)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from pipeline.sector_target_summary import top_gap_stocks
from run_sector_analysis import run
from utils.ticker import normalize_ticker_code

KST = timezone(timedelta(hours=9))
TOP_N = 100


def _clean(val):
    if val is None:
        return None
    try:
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
    except TypeError:
        pass
    if pd.isna(val):
        return None
    return val


def row_to_api_dict(row: pd.Series) -> dict:
    cross = bool(row.get("목표가변동_타기관"))
    basis = _clean(row.get("목표가변동기준")) or ""
    rev = _clean(row.get("목표가변동률"))
    return {
        "sector": _clean(row.get("산업")) or "",
        "name": _clean(row.get("종목명")) or "",
        "ticker": normalize_ticker_code(str(row.get("티커") or "")),
        "price": _clean(row.get("현재가")),
        "target": _clean(row.get("최근목표가")),
        "avgTarget6m": _clean(row.get("평균목표가_6M")),
        "highTarget6m": _clean(row.get("최고목표가_6M")),
        "lowTarget6m": _clean(row.get("최저목표가_6M")),
        "gap": _clean(row.get("괴리율_최근")),
        "revPct": rev,
        "revBasis": basis,
        "revCrossFirm": cross,
        "firm": _clean(row.get("최근증권사")) or "",
        "reportDate": _clean(row.get("최근발표일")) or "",
        "reportCount6m": _clean(row.get("리포트건수_6M")),
    }


def build_gap_payload(
    *,
    top_n: int = TOP_N,
    refresh_investing: bool = True,
    investing_parallel: int = 6,
) -> dict:
    """Investing 목표가 재수집 + 네이버 현재가 → Top N 괴리율 JSON."""
    summary, meta = run(
        refresh_investing=refresh_investing,
        from_cache=True,
        skip_etf=True,
        top_gap=top_n,
        investing_parallel=investing_parallel,
        return_meta=True,
    )
    top = top_gap_stocks(summary, n=top_n)
    rows = [row_to_api_dict(r) for _, r in top.iterrows()]
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    return {
        "updated": now,
        "count": len(rows),
        "title": f"목표주가 괴리율 Top {len(rows)} (Investing 해외)",
        "refreshedTickers": meta.get("refreshed_tickers", 0),
        "totalTargets": meta.get("total_targets", 0),
        "pricesRefreshed": meta.get("prices_refreshed", 0),
        "priceSource": "naver",
        "rows": rows,
        "_live": True,
    }
