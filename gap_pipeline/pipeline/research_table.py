"""
최종 리서치 테이블(DataFrame) 생성 파이프라인.

데이터 수집(collectors)과 분리된 정규화·병합 레이어입니다.
AI 분석 모듈은 이 단계 이후에 연결하도록 설계합니다.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from database.db_manager import DatabaseManager
from pipeline.report_history import (
    apply_db_previous,
    calc_target_revision_pct,
    enrich_previous_from_batch,
    validate_and_fix_dates,
)

logger = logging.getLogger(__name__)

# 최종 출력 컬럼 (한글)
OUTPUT_COLUMNS = [
    "종목명",
    "티커",
    "현재가",
    "목표가",
    "괴리율",
    "fPER",
    "fPBR",
    "이전목표가",
    "목표가상향률",
    "발표일",
    "이전발표일",
    "증권사",
    "구분",
    "데이터출처",
]

REGION_LABELS = {
    "domestic": "국내",
    "foreign": "해외",
}


def calc_price_gap_pct(current: float | None, target: float | None) -> float | None:
    """
    현재주가 대비 목표주가 괴리율(%).

    (목표가 - 현재가) / 현재가 * 100
    """
    if current is None or target is None:
        return None
    try:
        current_f, target_f = float(current), float(target)
        if current_f == 0:
            return None
        return round((target_f - current_f) / current_f * 100, 2)
    except (TypeError, ValueError):
        return None


class ResearchTableBuilder:
    """수집 데이터 + DB 히스토리를 병합해 리서치 테이블을 만듭니다."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager()

    def build(
        self,
        prices_df: pd.DataFrame,
        reports_df: pd.DataFrame,
        valuation_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        Parameters
        ----------
        prices_df : pd.DataFrame
            Yahoo 등 — ticker, stock_name, current_price
        reports_df : pd.DataFrame
            Naver 등 — ticker, securities_company, target_price, report_date, ...
        valuation_df : pd.DataFrame, optional
            ticker, fper, fpbr (없으면 reports_df 컬럼 사용)
        """
        if reports_df.empty:
            logger.warning("리포트 데이터가 비어 있습니다.")
            return pd.DataFrame(columns=OUTPUT_COLUMNS)

        reports = reports_df.copy()
        reports["ticker"] = reports["ticker"].astype(str).str.zfill(6)

        # 밸류에이션 병합
        if valuation_df is not None and not valuation_df.empty:
            val = valuation_df.copy()
            val["ticker"] = val["ticker"].astype(str).str.zfill(6)
            reports = reports.merge(
                val[["ticker", "fper", "fpbr"]].drop_duplicates("ticker"),
                on="ticker",
                how="left",
                suffixes=("", "_val"),
            )

        # 시세 병합
        if not prices_df.empty:
            prices = prices_df.copy()
            prices["ticker"] = prices["ticker"].astype(str).str.zfill(6)
            merged = reports.merge(
                prices[["ticker", "stock_name", "current_price"]],
                on="ticker",
                how="left",
                suffixes=("_report", "_price"),
            )
            # 종목명: 리포트 우선, 없으면 시세
            if "stock_name_report" in merged.columns:
                merged["stock_name"] = merged["stock_name_report"].fillna(
                    merged.get("stock_name_price")
                )
            elif "stock_name" not in merged.columns:
                merged["stock_name"] = merged.get("stock_name_price", "")
        else:
            merged = reports.copy()
            if "stock_name" not in merged.columns:
                merged["stock_name"] = ""

        # 이전 목표가·이전 발표일: 배치 내 직전 → DB(현재일 이전) → 검증
        if "previous_report_date" not in merged.columns:
            merged["previous_report_date"] = None
        merged = enrich_previous_from_batch(merged)
        merged = apply_db_previous(merged, self.db)
        merged = validate_and_fix_dates(merged)

        if "target_revision_pct" not in merged.columns:
            merged["target_revision_pct"] = merged.apply(
                lambda r: calc_target_revision_pct(
                    r.get("previous_target_price"), r.get("target_price")
                ),
                axis=1,
            )

        # 괴리율
        merged["price_gap_pct"] = merged.apply(
            lambda r: calc_price_gap_pct(r.get("current_price"), r.get("target_price")),
            axis=1,
        )

        if "source_region" in merged.columns:
            region = merged["source_region"].map(
                lambda x: REGION_LABELS.get(str(x), "국내")
                if pd.notna(x)
                else "국내"
            )
        else:
            region = "국내"
        if "data_source" not in merged.columns:
            merged["data_source"] = "naver"

        result = pd.DataFrame(
            {
                "종목명": merged["stock_name"],
                "티커": merged["ticker"],
                "현재가": merged.get("current_price"),
                "목표가": merged["target_price"],
                "괴리율": merged["price_gap_pct"],
                "fPER": merged.get("fper"),
                "fPBR": merged.get("fpbr"),
                "이전목표가": merged["previous_target_price"],
                "목표가상향률": merged["target_revision_pct"],
                "발표일": merged.get("report_date"),
                "이전발표일": merged["previous_report_date"],
                "증권사": merged.get("securities_company"),
                "구분": region,
                "데이터출처": merged["data_source"],
            }
        )

        return result[OUTPUT_COLUMNS]

    def build_from_db(self, latest_only: bool = False) -> pd.DataFrame:
        """DB에 저장된 데이터로 테이블 재생성."""
        stocks = self.db.load_stocks()
        if latest_only:
            reports = self.db.get_latest_reports_per_broker()
        else:
            reports = self.db.load_all_reports()
        return self.build(stocks, reports)
