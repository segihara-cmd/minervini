"""
리포트 히스토리(이전 목표가·이전 발표일) 보정 로직.
"""

from __future__ import annotations

import logging

import pandas as pd

from utils.dates import is_valid_previous_date, parse_report_date

logger = logging.getLogger(__name__)


def calc_target_revision_pct(
    previous: float | None, current: float | None
) -> float | None:
    if previous is None or current is None:
        return None
    try:
        prev_f, cur_f = float(previous), float(current)
        if prev_f == 0:
            return None
        return round((cur_f / prev_f - 1) * 100, 2)
    except (TypeError, ValueError):
        return None


def enrich_previous_from_batch(df: pd.DataFrame) -> pd.DataFrame:
    """
    동일 종목·증권사 내에서 발표일 오름차순 기준 직전 리포트를 연결.

    Investing.com 등에서 이미 채워진 이전값이 있으면 유효할 때만 유지합니다.
    """
    if df.empty:
        return df

    out = df.copy()
    out["_dt"] = out["report_date"].map(parse_report_date)

    if "previous_target_price" not in out.columns:
        out["previous_target_price"] = None
    if "previous_report_date" not in out.columns:
        out["previous_report_date"] = None

    out = out.sort_values(
        ["ticker", "securities_company", "_dt"],
        na_position="last",
    )

    grouped = out.groupby(["ticker", "securities_company"], group_keys=False)

    shifted_target = grouped["target_price"].shift(1)
    shifted_date = grouped["report_date"].shift(1)

    for idx in out.index:
        cur_dt = out.at[idx, "_dt"]
        existing_prev = out.at[idx, "previous_target_price"]
        existing_prev_date = out.at[idx, "previous_report_date"]

        batch_prev_t = shifted_target.loc[idx]
        batch_prev_d = shifted_date.loc[idx]
        batch_prev_dt = parse_report_date(batch_prev_d)

        # 수집기(Investing) 값: 날짜가 올바를 때만 유지
        use_existing = False
        if pd.notna(existing_prev) and existing_prev_date:
            ex_dt = parse_report_date(existing_prev_date)
            if is_valid_previous_date(cur_dt, ex_dt):
                use_existing = True

        if use_existing:
            continue

        # 배치 내 직전 리포트
        if pd.notna(batch_prev_t) and is_valid_previous_date(cur_dt, batch_prev_dt):
            out.at[idx, "previous_target_price"] = batch_prev_t
            out.at[idx, "previous_report_date"] = batch_prev_d
        else:
            if pd.notna(existing_prev_date) and not is_valid_previous_date(
                cur_dt, parse_report_date(existing_prev_date)
            ):
                out.at[idx, "previous_target_price"] = None
                out.at[idx, "previous_report_date"] = None

    out.drop(columns=["_dt"], inplace=True)
    return out


def apply_db_previous(
    df: pd.DataFrame,
    db,  # DatabaseManager — 순환 import 방지
) -> pd.DataFrame:
    """DB에서 '현재 발표일 이전' 리포트로 이전값 보강."""
    out = df.copy()
    for idx, row in out.iterrows():
        cur_date = row.get("report_date")
        if not cur_date or (isinstance(cur_date, float) and pd.isna(cur_date)):
            continue

        cur_dt = parse_report_date(cur_date)
        prev_dt = parse_report_date(row.get("previous_report_date"))
        if is_valid_previous_date(cur_dt, prev_dt):
            continue

        prev = db.get_previous_report(
            row["ticker"],
            row.get("securities_company", ""),
            before_date=str(cur_date),
            exclude_nid=row.get("report_nid"),
        )
        if not prev:
            continue

        p_date = prev.get("report_date")
        p_dt = parse_report_date(p_date)
        if not is_valid_previous_date(cur_dt, p_dt):
            continue

        out.at[idx, "previous_target_price"] = prev.get("target_price")
        out.at[idx, "previous_report_date"] = p_date
        if pd.isna(row.get("target_revision_pct")):
            out.at[idx, "target_revision_pct"] = calc_target_revision_pct(
                prev.get("target_price"), row.get("target_price")
            )

    return out


def validate_and_fix_dates(df: pd.DataFrame) -> pd.DataFrame:
    """이전발표일 >= 발표일 인 행을 정리."""
    out = df.copy()
    fixed = 0
    for idx, row in out.iterrows():
        cur_dt = parse_report_date(row.get("report_date"))
        prev_dt = parse_report_date(row.get("previous_report_date"))
        if not is_valid_previous_date(cur_dt, prev_dt):
            if prev_dt is not None and cur_dt is not None:
                fixed += 1
            out.at[idx, "previous_report_date"] = None
            if prev_dt is not None and cur_dt is not None and prev_dt >= cur_dt:
                out.at[idx, "previous_target_price"] = None
                out.at[idx, "target_revision_pct"] = None
        elif pd.isna(row.get("target_revision_pct")):
            out.at[idx, "target_revision_pct"] = calc_target_revision_pct(
                row.get("previous_target_price"), row.get("target_price")
            )

    if fixed:
        logger.info("발표일 역전 %d건 보정 (이전발표일 제거)", fixed)
    return out
