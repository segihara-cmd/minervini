"""
분기 컨센서스 비교를 위한 수출 나우캐스팅(Nowcasting) 모듈.

E. partial_month_scaleup — 관세청 1~10일/1~20일 잠정치를 월 전체로 스케일업.
"""

from __future__ import annotations

import calendar
from datetime import date

import numpy as np
import pandas as pd


def estimate_flat(last_actual_month_value: float) -> float:
    """미발표월 = 직전 발표월과 동일."""
    return last_actual_month_value


def estimate_mom_extrapolate(month_t_minus_1: float, month_t: float) -> float:
    """최근 1개월 MoM 성장률을 다음 달에 동일 적용."""
    if month_t_minus_1 in (0, None) or pd.isna(month_t_minus_1):
        raise ValueError("직전월 값이 0이거나 비어있어 성장률 계산 불가")
    mom_growth = month_t / month_t_minus_1
    return month_t * mom_growth


def estimate_yoy_seasonal(
    current_month_t: float,
    last_year_month_t: float,
    last_year_month_t_plus_1: float,
) -> float:
    """작년 동월→익월 계절 패턴을 올해에 적용."""
    if last_year_month_t in (0, None) or pd.isna(last_year_month_t):
        raise ValueError("작년 동월 값이 0이거나 비어있어 계절성 계산 불가")
    seasonal_ratio = last_year_month_t_plus_1 / last_year_month_t
    return current_month_t * seasonal_ratio


def estimate_trend_regression(
    recent_values: list[float],
    n_months_ahead: int = 1,
) -> float:
    """최근 N개월 로그 선형회귀로 다음 달 외삽."""
    values = np.array(recent_values, dtype=float)
    if np.any(values <= 0):
        raise ValueError("0 이하 값이 있으면 로그변환 불가")

    log_values = np.log(values)
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, log_values, deg=1)
    next_x = len(values) - 1 + n_months_ahead
    predicted_log = slope * next_x + intercept
    return float(np.exp(predicted_log))


def estimate_partial_month_scaleup(
    partial_value: float,
    days_covered: int,
    total_days_in_month: int,
) -> float:
    """부분월 실측 잠정치를 일평균으로 환산해 해당 월 전체로 스케일업."""
    if days_covered <= 0:
        raise ValueError("days_covered는 1 이상이어야 합니다")
    daily_avg = partial_value / days_covered
    return daily_avg * total_days_in_month


def estimate_partial_month_scaleup_by_workdays(
    partial_value: float,
    workdays_covered: int,
    total_workdays_in_month: int,
) -> float:
    """조업일수 기준 부분월 스케일업."""
    if workdays_covered <= 0:
        raise ValueError("workdays_covered는 1 이상이어야 합니다")
    daily_avg = partial_value / workdays_covered
    return daily_avg * total_workdays_in_month


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def infer_partial_period(as_of: date) -> tuple[int, int] | None:
    """
    관세청 10일 단위 잠정치 공표 일정에 맞춰 (커버일수, 해당월) 추론.

    - 11일 이후: 1~10일(10일) 잠정치 이용 가능
    - 21일 이후: 1~20일(20일) 잠정치 이용 가능
    - 익월 1일: 전월 말일까지 확정 잠정치
    """
    if as_of.day >= 21:
        return 20, as_of.month
    if as_of.day >= 11:
        return 10, as_of.month
    return None


def build_quarter_nowcast(
    actual_months: dict[str, float],
    missing_month_label: str,
    method_inputs: dict,
) -> pd.DataFrame:
    """실측 월 + 미발표월 5가지 추정 → 분기 합산 비교표."""
    actual_sum = sum(actual_months.values())
    rows: list[dict] = []

    if "flat" in method_inputs:
        est = estimate_flat(**method_inputs["flat"])
        rows.append(
            {
                "method": "A. flat (전월유지)",
                "estimated_missing_month": est,
                "quarter_total": actual_sum + est,
            }
        )

    if "mom_extrapolate" in method_inputs:
        est = estimate_mom_extrapolate(**method_inputs["mom_extrapolate"])
        rows.append(
            {
                "method": "B. MoM 성장률 연장",
                "estimated_missing_month": est,
                "quarter_total": actual_sum + est,
            }
        )

    if "yoy_seasonal" in method_inputs:
        est = estimate_yoy_seasonal(**method_inputs["yoy_seasonal"])
        rows.append(
            {
                "method": "C. 전년 계절패턴",
                "estimated_missing_month": est,
                "quarter_total": actual_sum + est,
            }
        )

    if "trend_regression" in method_inputs:
        est = estimate_trend_regression(**method_inputs["trend_regression"])
        rows.append(
            {
                "method": "D. 추세선 외삽",
                "estimated_missing_month": est,
                "quarter_total": actual_sum + est,
            }
        )

    if "partial_month_scaleup" in method_inputs:
        est = estimate_partial_month_scaleup(**method_inputs["partial_month_scaleup"])
        rows.append(
            {
                "method": "E. 부분월 잠정치 스케일업 (★권장)",
                "estimated_missing_month": est,
                "quarter_total": actual_sum + est,
            }
        )

    df = pd.DataFrame(rows)
    df["missing_month"] = missing_month_label
    df["actual_months_sum"] = actual_sum
    return df.sort_values("quarter_total").reset_index(drop=True)
