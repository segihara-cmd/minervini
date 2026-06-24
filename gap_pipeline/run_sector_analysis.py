"""
산업별 대표주 목표주가 분석 (Investing.com 해외만).

출력:
  sector_leaders.csv, sector_target_summary.csv, sector_firm_detail.csv,
  sector_etf_metrics.csv (괴리율 상위 10종목 편입 ETF)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.etf_analytics import build_etf_metrics_table, build_holdings_cache
from collectors.investing_collector import InvestingCollector
from collectors.yahoo_collector import YahooCollector
from config.settings import (
    PROCESSED_DIR,
    RAW_DIR,
    SECTOR_ANALYSIS_MONTHS,
    SECTOR_ETF_METRICS_CSV,
    SECTOR_FIRM_DETAIL_CSV,
    SECTOR_LEADERS_CSV,
    SECTOR_LEADERS_PER_SECTOR,
    SECTOR_TARGET_SUMMARY_CSV,
    SECTOR_TOP_GAP_COUNT,
)
from pipeline.industry_leaders import select_industry_leaders
from pipeline.sector_target_summary import (
    build_firm_detail,
    build_stock_summary,
    top_gap_stocks,
)
from utils.ticker import normalize_ticker_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sector_analysis")


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for col in ("ticker", "티커", "편입종목코드", "ETF코드"):
        if col in out.columns:
            out[col] = out[col].map(normalize_ticker_code)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("저장: %s (%d행)", path, len(out))


_DEDUP_COLS = ["ticker", "securities_company", "report_date", "target_price"]


def _collect_investing_for_tickers(
    tickers: list[str],
    name_map: dict[str, str],
    months: int,
) -> pd.DataFrame:
    collector = InvestingCollector()
    frames: list[pd.DataFrame] = []
    total = len(tickers)
    for i, raw in enumerate(tickers, 1):
        code = normalize_ticker_code(raw)
        try:
            df = collector.fetch_consensus(
                code,
                name_map.get(code),
                foreign_only=False,
                months=months,
            )
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning("Investing %s 실패: %s", code, exc)
        if i % 25 == 0 or i == total:
            rows = sum(len(f) for f in frames)
            logger.info("Investing 수집 %d/%d (누적 %d건)", i, total, rows)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=_DEDUP_COLS,
        keep="last",
    )


def _merge_investing_cache(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """재수집된 종목은 최신 행으로 교체, 실패 종목은 기존 캐시 유지."""
    if fresh.empty:
        return cached
    fresh = fresh.copy()
    fresh["ticker"] = fresh["ticker"].map(normalize_ticker_code)
    refreshed = set(fresh["ticker"].astype(str))
    cached = cached.copy()
    cached["ticker"] = cached["ticker"].map(normalize_ticker_code)
    kept = cached[~cached["ticker"].isin(refreshed)]
    out = pd.concat([kept, fresh], ignore_index=True)
    return out.drop_duplicates(subset=_DEDUP_COLS, keep="last")


def run(
    months: int | None = None,
    per_sector: int | None = None,
    refresh_etf_cache: bool = False,
    top_gap: int | None = None,
    from_cache: bool = False,
    refresh_investing: bool = False,
    skip_etf: bool = False,
    refresh_industry_cache: bool = False,
) -> pd.DataFrame:
    months = months or SECTOR_ANALYSIS_MONTHS
    per_sector = per_sector or SECTOR_LEADERS_PER_SECTOR
    top_n = top_gap or SECTOR_TOP_GAP_COUNT

    logger.info("=== 산업 대표주 분석 (Investing 해외, %d개월) ===", months)

    leaders = select_industry_leaders(
        per_sector=per_sector,
        force_refresh_cache=refresh_industry_cache,
    )
    save_csv(leaders, SECTOR_LEADERS_CSV)
    tickers = leaders["ticker"].drop_duplicates().tolist()
    name_map = dict(zip(leaders["ticker"], leaders["stock_name"]))

    logger.info(
        "대표주 %d종목 (%d개 업종, 중복 제거)",
        len(tickers),
        leaders["sector"].nunique(),
    )

    cache_path = RAW_DIR / "sector_investing_reports.csv"
    if refresh_investing:
        logger.info("Investing 전 종목 재수집 (%d개)…", len(tickers))
        fresh_df = _collect_investing_for_tickers(tickers, name_map, months)
        if from_cache and cache_path.exists():
            investing_df = pd.read_csv(cache_path, encoding="utf-8-sig")
            investing_df = _merge_investing_cache(investing_df, fresh_df)
        else:
            investing_df = fresh_df
        save_csv(investing_df, cache_path)
    elif from_cache and cache_path.exists():
        investing_df = pd.read_csv(cache_path, encoding="utf-8-sig")
        if "ticker" in investing_df.columns:
            investing_df["ticker"] = investing_df["ticker"].map(normalize_ticker_code)
        logger.info("Investing 캐시 로드: %d건", len(investing_df))
        existing = set(investing_df["ticker"].astype(str))
        missing = [t for t in tickers if normalize_ticker_code(t) not in existing]
        if missing:
            logger.info("캐시에 없는 종목 %d개 추가 수집…", len(missing))
            new_df = _collect_investing_for_tickers(missing, name_map, months)
            if not new_df.empty:
                investing_df = pd.concat([investing_df, new_df], ignore_index=True)
                investing_df = investing_df.drop_duplicates(
                    subset=_DEDUP_COLS,
                    keep="last",
                )
                save_csv(investing_df, cache_path)
    else:
        investing_df = _collect_investing_for_tickers(tickers, name_map, months)
        save_csv(investing_df, cache_path)

    if investing_df.empty:
        logger.error("Investing 데이터 없음")
        return pd.DataFrame()

    logger.info("Investing: %d건", len(investing_df))

    prices_df = YahooCollector().collect(tickers, batch=True)
    save_csv(prices_df, RAW_DIR / "sector_yahoo_prices.csv")

    summary = build_stock_summary(
        investing_df,
        prices_df,
        leaders,
        months=months,
        regions=["foreign"],
    )
    firm_detail = build_firm_detail(investing_df, leaders, months=months)
    firm_detail = firm_detail[firm_detail["구분"] == "해외"] if not firm_detail.empty else firm_detail

    save_csv(summary, SECTOR_TARGET_SUMMARY_CSV)
    save_csv(firm_detail, SECTOR_FIRM_DETAIL_CSV)

    top = top_gap_stocks(summary, n=top_n)
    if skip_etf:
        logger.info("ETF 수집 생략 (--skip-etf)")
    elif not top.empty:
        logger.info("괴리율 상위 %d종목 ETF 조회…", len(top))
        holdings = build_holdings_cache(force_refresh=refresh_etf_cache)
        top_names = dict(zip(top["티커"], top["종목명"]))
        etf_df = build_etf_metrics_table(
            top["티커"].tolist(),
            stock_names=top_names,
            holdings_map=holdings,
        )
        save_csv(etf_df, SECTOR_ETF_METRICS_CSV)
    else:
        logger.warning("괴리율 상위 종목 없음 — ETF 표 생략")

    _print_console(summary, top)
    return summary


def _print_console(summary: pd.DataFrame, top: pd.DataFrame) -> None:
    if summary.empty:
        return
    print("\n" + "=" * 80)
    print("산업 대표주 - Investing 해외 (괴리율 내림차순)")
    print("=" * 80)
    s = summary.copy()
    s["_g"] = pd.to_numeric(s["괴리율_최근"], errors="coerce")
    print(s.sort_values("_g", ascending=False).drop(columns=["_g"]).to_string(index=False))
    if not top.empty:
        print("\n괴리율 상위 종목:")
        print(top[["산업", "종목명", "티커", "괴리율_최근"]].to_string(index=False))
    print(f"\n  {SECTOR_TARGET_SUMMARY_CSV}")
    print(f"  {SECTOR_ETF_METRICS_CSV}")


def main() -> None:
    parser = argparse.ArgumentParser(description="산업 대표주 (Investing)")
    parser.add_argument("--months", type=int, default=None)
    parser.add_argument("--per-sector", type=int, default=None)
    parser.add_argument("--refresh-etf-cache", action="store_true")
    parser.add_argument("--top-gap", type=int, default=None)
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Investing CSV 캐시 병합 (재수집 시 기존 데이터 유지)",
    )
    parser.add_argument(
        "--refresh-investing",
        action="store_true",
        help="대표주 전 종목 Investing.com 재수집",
    )
    parser.add_argument(
        "--no-refresh-investing",
        action="store_true",
        help="Investing 재수집 생략 (캐시만 사용)",
    )
    parser.add_argument("--skip-etf", action="store_true", help="ETF 구성·지표 생략")
    parser.add_argument(
        "--refresh-industry-cache",
        action="store_true",
        help="네이버 업종 구성종목 캐시 재수집",
    )
    args = parser.parse_args()
    refresh_investing = args.refresh_investing and not args.no_refresh_investing
    run(
        months=args.months,
        per_sector=args.per_sector,
        refresh_etf_cache=args.refresh_etf_cache,
        top_gap=args.top_gap,
        from_cache=args.from_cache or refresh_investing,
        refresh_investing=refresh_investing,
        skip_etf=args.skip_etf,
        refresh_industry_cache=args.refresh_industry_cache,
    )


if __name__ == "__main__":
    main()
