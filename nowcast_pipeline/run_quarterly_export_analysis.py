#!/usr/bin/env python
"""반도체 분기별 수출 QoQ/YoY 분석 CLI."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import PROCESSED_DIR
from pipeline.quarterly_export_analysis import analyze_quarterly_exports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="HS8542 분기별 수출 QoQ/YoY 분석")
    p.add_argument("--as-of", type=str, default=None, help="기준일 YYYY-MM-DD")
    p.add_argument("--start-year", type=int, default=2023)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--save", action="store_true")
    args = p.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    try:
        df = analyze_quarterly_exports(
            as_of=as_of,
            use_cache=not args.no_cache,
            start_year=args.start_year,
        )
    except Exception as exc:
        logger.error("분석 실패: %s", exc)
        return 1

    print("=" * 72)
    print(f"반도체(HS8542) 분기별 수출 분석 (기준일 {as_of})")
    print("미발표 월: E.partial_month_scaleup 추정 포함")
    print("=" * 72)

    if df.empty:
        print("집계 가능한 분기 데이터가 없습니다.")
        return 1

    show = df[["분기", "수출USD_B", "전분기대비_%", "전년동기대비_%", "E추정월", "월별_USD_B"]]
    print(show.to_string(index=False))

    latest = df.iloc[-1]
    print(f"\n[최근 분기 {latest['분기']}]")
    print(f"  분기 수출: ${latest['수출USD_B']:.2f}B")
    if pd_notna(latest["전분기대비_%"]):
        print(f"  전분기(QoQ): {latest['전분기대비_%']:+.2f}%")
    if pd_notna(latest["전년동기대비_%"]):
        print(f"  전년동기(YoY): {latest['전년동기대비_%']:+.2f}%")
    if latest["E추정월"]:
        print(f"  E추정 포함 월: {latest['E추정월']}")

    if args.save:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        path = PROCESSED_DIR / f"quarterly_export_analysis_{as_of.isoformat()}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\n저장: {path}")

    return 0


def pd_notna(v) -> bool:
    import pandas as pd
    return v is not None and pd.notna(v)


if __name__ == "__main__":
    raise SystemExit(main())
