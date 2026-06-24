"""
산업 대표주 — 6개월 목표주가 요약·기관별 상향률 집계.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from pipeline.report_history import (
    calc_target_revision_pct,
    enrich_previous_from_batch,
)
from pipeline.research_table import calc_price_gap_pct
from utils.dates import parse_report_date
from utils.ticker import normalize_ticker_code

logger = logging.getLogger(__name__)

REGION_LABELS = {"domestic": "국내", "foreign": "해외", "all": "전체"}
REVISION_MAX_DAYS = 31 * 3  # 약 3개월


def _normalize_reports_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ticker"] = out["ticker"].map(normalize_ticker_code)
    return out


def resolve_target_revision(
    ticker_reports: pd.DataFrame,
    latest_row: pd.Series,
) -> dict:
    """
    최근 목표주가 대비 변동률.

    1) 동일 증권사 직전 목표(3개월 이내)
    2) 없거나 3개월 초과 시, 최근 발표일 이전 타 기관 목표 대비
    """
    empty = {"목표가변동률": None, "목표가변동기준": "", "목표가변동_타기관": False}
    latest_firm = str(latest_row.get("securities_company", ""))
    latest_dt = parse_report_date(latest_row.get("report_date"))
    if latest_dt is None or pd.isna(latest_row.get("target_price")):
        return empty

    latest_target = float(latest_row["target_price"])
    sub = ticker_reports.sort_values("_dt")

    same = sub[sub["securities_company"] == latest_firm].sort_values("_dt")
    if len(same) >= 2:
        prev_row = same.iloc[-2]
        prev_dt = parse_report_date(prev_row["report_date"])
        if prev_dt and (latest_dt - prev_dt).days <= REVISION_MAX_DAYS:
            return {
                "목표가변동률": calc_target_revision_pct(
                    float(prev_row["target_price"]), latest_target
                ),
                "목표가변동기준": latest_firm,
                "목표가변동_타기관": False,
            }

    prev_target = latest_row.get("previous_target_price")
    prev_date = parse_report_date(latest_row.get("previous_report_date"))
    if pd.notna(prev_target) and prev_date and (latest_dt - prev_date).days <= REVISION_MAX_DAYS:
        return {
            "목표가변동률": calc_target_revision_pct(float(prev_target), latest_target),
            "목표가변동기준": latest_firm,
            "목표가변동_타기관": False,
        }

    others = sub[
        (sub["securities_company"] != latest_firm) & (sub["_dt"] < latest_dt)
    ].sort_values("_dt")
    if others.empty:
        return empty

    ref = others.iloc[-1]
    ref_firm = str(ref["securities_company"])
    return {
        "목표가변동률": calc_target_revision_pct(
            float(ref["target_price"]), latest_target
        ),
        "목표가변동기준": ref_firm,
        "목표가변동_타기관": True,
    }


def _cutoff_date(months: int) -> datetime:
    return datetime.now() - timedelta(days=months * 31)


def _filter_window(df: pd.DataFrame, months: int) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = _cutoff_date(months).date()
    out = df.copy()
    out["_dt"] = out["report_date"].map(parse_report_date)
    out = out[out["_dt"].notna()]
    out = out[out["_dt"].map(lambda d: d.date() >= cutoff)]
    out = out[out["target_price"].notna()]
    return out.drop(columns=["_dt"], errors="ignore")


def build_firm_detail(
    reports_df: pd.DataFrame,
    leaders_df: pd.DataFrame,
    months: int = 6,
) -> pd.DataFrame:
    """종목·기관별 최근/이전 목표주가 및 상향률."""
    if reports_df.empty:
        return pd.DataFrame()

    df = _filter_window(reports_df, months)
    df = enrich_previous_from_batch(df)
    df["ticker"] = df["ticker"].map(normalize_ticker_code)
    df["_dt"] = df["report_date"].map(parse_report_date)
    df["target_revision_pct"] = df.apply(
        lambda r: calc_target_revision_pct(
            r.get("previous_target_price"), r.get("target_price")
        ),
        axis=1,
    )

    leaders_norm = leaders_df.copy()
    leaders_norm["ticker"] = leaders_norm["ticker"].map(normalize_ticker_code)
    sector_map = dict(zip(leaders_norm["ticker"], leaders_norm["sector"]))
    df["sector"] = df["ticker"].map(lambda t: sector_map.get(t, ""))
    df["구분"] = df["source_region"].map(
        lambda x: REGION_LABELS.get(str(x), str(x))
    )

    latest = (
        df.sort_values("_dt")
        .groupby(["ticker", "securities_company", "source_region"], as_index=False)
        .tail(1)
    )
    latest["ticker"] = latest["ticker"].map(normalize_ticker_code)

    cols = [
        "sector",
        "stock_name",
        "ticker",
        "구분",
        "securities_company",
        "target_price",
        "previous_target_price",
        "target_revision_pct",
        "report_date",
        "previous_report_date",
        "data_source",
    ]
    return latest[[c for c in cols if c in latest.columns]]


def build_stock_summary(
    reports_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    leaders_df: pd.DataFrame,
    months: int = 6,
    *,
    regions: list[str] | None = None,
) -> pd.DataFrame:
    """
    종목·구분별 6개월 목표주가 통계 및 최근 괴리율.

    regions: None이면 국내·해외·전체, ['foreign']이면 해외(Investing)만.
    """
    if reports_df.empty:
        return pd.DataFrame()

    reports_df = _normalize_reports_df(reports_df)
    leaders_df = leaders_df.copy()
    leaders_df["ticker"] = leaders_df["ticker"].map(normalize_ticker_code)

    df = _filter_window(reports_df, months)
    df = enrich_previous_from_batch(df)
    df["ticker"] = df["ticker"].map(normalize_ticker_code)
    df["_dt"] = df["report_date"].map(parse_report_date)

    sector_map = dict(zip(leaders_df["ticker"], leaders_df["sector"]))
    name_map = dict(zip(leaders_df["ticker"], leaders_df["stock_name"]))

    price_map: dict[str, float] = {}
    if not prices_df.empty and "ticker" in prices_df.columns:
        for _, row in prices_df.drop_duplicates("ticker").iterrows():
            code = normalize_ticker_code(row["ticker"])
            cur = row.get("current_price") or row.get("close")
            if code and cur is not None and not pd.isna(cur):
                price_map[code] = float(cur)

    rows: list[dict] = []

    def _summarize(sub: pd.DataFrame, region_key: str, label: str) -> None:
        for ticker, grp in sub.groupby("ticker"):
            code = normalize_ticker_code(ticker)
            if not code or not sector_map.get(code):
                continue
            targets = grp["target_price"].dropna().astype(float)
            if targets.empty:
                continue

            latest_row = grp.sort_values("_dt").iloc[-1]
            latest_target = float(latest_row["target_price"])
            current = price_map.get(code)
            rev = resolve_target_revision(grp, latest_row)

            rows.append(
                {
                    "산업": sector_map.get(code, ""),
                    "종목명": name_map.get(code, latest_row.get("stock_name", "")),
                    "티커": code,
                    "구분": label,
                    "현재가": current,
                    "평균목표가_6M": round(float(targets.mean()), 2),
                    "최고목표가_6M": round(float(targets.max()), 2),
                    "최저목표가_6M": round(float(targets.min()), 2),
                    "최근목표가": latest_target,
                    "최근발표일": latest_row.get("report_date"),
                    "최근증권사": latest_row.get("securities_company"),
                    "괴리율_최근": calc_price_gap_pct(current, latest_target),
                    "목표가변동률": rev["목표가변동률"],
                    "목표가변동기준": rev["목표가변동기준"],
                    "목표가변동_타기관": rev["목표가변동_타기관"],
                    "리포트건수_6M": len(grp),
                }
            )

    all_specs: list[tuple[str, str]] = [
        ("domestic", "국내"),
        ("foreign", "해외"),
    ]
    if regions is not None:
        allowed = set(regions)
        all_specs = [(k, l) for k, l in all_specs if k in allowed]
    else:
        all_specs.append(("all", "전체"))

    for region_key, label in all_specs:
        if region_key == "all":
            _summarize(df, region_key, label)
        else:
            sub = df[df["source_region"] == region_key]
            _summarize(sub, region_key, label)

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    return out.sort_values(["산업", "티커", "구분"]).reset_index(drop=True)


def top_gap_stocks(summary: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """괴리율 상위 N종목 (해외 행 기준)."""
    if summary.empty:
        return pd.DataFrame()
    sub = summary[summary["구분"] == "해외"].copy()
    sub["_gap"] = pd.to_numeric(sub["괴리율_최근"], errors="coerce")
    sub = sub.dropna(subset=["_gap"])
    return (
        sub.sort_values("_gap", ascending=False)
        .head(n)
        .drop(columns=["_gap"])
        .reset_index(drop=True)
    )
