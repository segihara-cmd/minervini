"""
반도체(HS8542) 분기별 수출 집계 및 QoQ / YoY 분석.

미발표 월은 E.partial_month_scaleup 추정치 포함.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from collectors.customs_trade_collector import (
    fetch_semiconductor_trade_range,
    sum_quarter_export_usd,
)
from config.samsung_nowcast_config import CORE_EXPORT_COUNTRIES, DEFAULT_HS_CODE
from pipeline.export_nowcast import days_in_month, estimate_partial_month_scaleup
from pipeline.nowcast_helpers import (
    confirmed_month_cutoff,
    label_from_yymm,
    load_partial_overrides,
    quarter_label,
    quarter_months,
    yymm_from_label,
)

logger = logging.getLogger(__name__)


@dataclass
class QuarterExportRow:
    quarter: str
    year: int
    q: int
    export_usd: float
    months: dict[str, float]
    estimated_months: list[str]
    qoq_pct: float | None
    yoy_pct: float | None


def _pct_change(current: float, base: float) -> float | None:
    if base is None or base <= 0:
        return None
    return round((current / base - 1) * 100, 2)


def build_monthly_exports_with_estimates(
    as_of: date,
    hs_code: str = DEFAULT_HS_CODE,
    use_cache: bool = True,
    countries: tuple[str, ...] = CORE_EXPORT_COUNTRIES,
    start_year: int = 2023,
) -> dict[str, float]:
    """확정월 + 미발표월 E추정 포함 월별 수출 USD."""
    end_year, end_q = as_of.year, (as_of.month - 1) // 3 + 1
    end_month = end_q * 3

    needed: set[str] = set()
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            if y > end_year or (y == end_year and m > end_month):
                break
            needed.add(yymm_from_label(f"{y}-{m:02d}"))
            needed.add(yymm_from_label(f"{y - 1}-{m:02d}"))

    month_list = sorted(needed)
    if not month_list:
        return {}

    df = fetch_semiconductor_trade_range(
        month_list[0],
        month_list[-1],
        hs_code=hs_code,
        use_cache=use_cache,
        month_list=month_list,
        countries=countries,
    )
    monthly = {
        label_from_yymm(row.year_month): float(row.export_usd)
        for row in df.itertuples()
    }

    cutoff = confirmed_month_cutoff(as_of)
    overrides = load_partial_overrides()

    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            if y > end_year or (y == end_year and m > end_month):
                break
            ym = f"{y}-{m:02d}"
            if ym <= cutoff and monthly.get(ym, 0) > 0:
                continue
            if ym > f"{as_of.year}-{as_of.month:02d}":
                continue
            partial = overrides.get(ym)
            if partial is None:
                continue
            total_days = days_in_month(y, m)
            est = estimate_partial_month_scaleup(
                partial.partial_export_usd,
                partial.days_covered,
                total_days,
            )
            monthly[ym] = est
            logger.info(
                "E추정 %s: $%.2fB (%d일->%d일 스케일업)",
                ym,
                est / 1e9,
                partial.days_covered,
                total_days,
            )

    return monthly


def analyze_quarterly_exports(
    as_of: date | None = None,
    hs_code: str = DEFAULT_HS_CODE,
    use_cache: bool = True,
    start_year: int = 2023,
    countries: tuple[str, ...] = CORE_EXPORT_COUNTRIES,
) -> pd.DataFrame:
    """분기별 수출 합계 + 전분기(QoQ) / 전년동기(YoY) 증감률."""
    as_of = as_of or date.today()
    monthly = build_monthly_exports_with_estimates(
        as_of=as_of,
        hs_code=hs_code,
        use_cache=use_cache,
        countries=countries,
        start_year=start_year,
    )
    cutoff = confirmed_month_cutoff(as_of)
    overrides = load_partial_overrides()

    end_year, end_q = as_of.year, (as_of.month - 1) // 3 + 1
    rows: list[QuarterExportRow] = []
    totals: dict[str, float] = {}

    for y in range(start_year, end_year + 1):
        for q in range(1, 5):
            if y > end_year or (y == end_year and q > end_q):
                break
            ql = quarter_label(y, q)
            months = quarter_months(y, q)
            est_months: list[str] = []
            month_vals: dict[str, float] = {}
            complete = True
            for ym in months:
                val = monthly.get(ym, 0.0)
                if val <= 0:
                    complete = False
                    break
                month_vals[ym] = val
                if ym > cutoff or ym in overrides:
                    est_months.append(ym)
            if not complete:
                continue
            total = sum(month_vals.values())
            totals[ql] = total
            rows.append(
                QuarterExportRow(
                    quarter=ql,
                    year=y,
                    q=q,
                    export_usd=total,
                    months=month_vals,
                    estimated_months=est_months,
                    qoq_pct=None,
                    yoy_pct=None,
                )
            )

    for i, row in enumerate(rows):
        prev_q = rows[i - 1] if i > 0 else None
        py_q = totals.get(quarter_label(row.year - 1, row.q))
        qoq = _pct_change(row.export_usd, prev_q.export_usd) if prev_q else None
        yoy = _pct_change(row.export_usd, py_q) if py_q else None
        rows[i] = QuarterExportRow(
            quarter=row.quarter,
            year=row.year,
            q=row.q,
            export_usd=row.export_usd,
            months=row.months,
            estimated_months=row.estimated_months,
            qoq_pct=qoq,
            yoy_pct=yoy,
        )

    return pd.DataFrame(
        [
            {
                "분기": r.quarter,
                "수출USD_B": round(r.export_usd / 1e9, 2),
                "수출USD": r.export_usd,
                "전분기대비_%": r.qoq_pct,
                "전년동기대비_%": r.yoy_pct,
                "E추정월": ",".join(r.estimated_months) if r.estimated_months else "",
                "월별_USD_B": " / ".join(
                    f"{ym[-2:]}월 ${v/1e9:.2f}B" for ym, v in sorted(r.months.items())
                ),
            }
            for r in rows
        ]
    )
