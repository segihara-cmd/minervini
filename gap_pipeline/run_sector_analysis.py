"""
산업별 대표주 목표주가 분석 (Investing.com 해외만).

출력:
  sector_leaders.csv, sector_target_summary.csv, sector_firm_detail.csv,
  sector_etf_metrics.csv (괴리율 상위 10종목 편입 ETF)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.etf_analytics import build_etf_metrics_table, build_holdings_cache
from collectors.investing_collector import InvestingCollector, SLUG_CACHE_PATH
from collectors.naver_price_collector import NaverPriceCollector
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


def _load_slug_map() -> dict[str, str]:
    if SLUG_CACHE_PATH.exists():
        try:
            return json.loads(SLUG_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _collect_investing_for_tickers(
    tickers: list[str],
    name_map: dict[str, str],
    months: int,
    parallel_workers: int = 0,
) -> pd.DataFrame:
    collector = InvestingCollector()
    slug_map = _load_slug_map()
    frames: list[pd.DataFrame] = []
    pending = [normalize_ticker_code(t) for t in tickers]
    total = len(pending)
    no_data: list[str] = []
    failed_error: list[str] = []

    def _fetch_one(code: str) -> pd.DataFrame:
        slug = slug_map.get(code)
        df = collector.fetch_consensus(
            code,
            name_map.get(code),
            foreign_only=False,
            months=months,
            slug=slug,
        )
        if df.empty:
            df = collector.fetch_consensus(
                code,
                name_map.get(code),
                foreign_only=False,
                months=months,
            )
        return df

    def _run_pass(codes: list[str], *, pass_no: int, delay: float) -> list[str]:
        still_failed: list[str] = []
        for i, code in enumerate(codes, 1):
            if delay > 0 and i > 1:
                time.sleep(delay)
            try:
                df = _fetch_one(code)
                if not df.empty:
                    frames.append(df)
                elif pass_no == 1:
                    no_data.append(code)
                else:
                    still_failed.append(code)
                    logger.warning("Investing %s — 데이터 없음 (pass %d)", code, pass_no)
            except Exception as exc:
                still_failed.append(code)
                logger.warning("Investing %s 실패 (pass %d): %s", code, pass_no, exc)
            if pass_no == 1 and (i % 25 == 0 or i == len(codes)):
                rows = sum(len(f) for f in frames)
                logger.info(
                    "Investing 수집 %d/%d (누적 %d건, 커버리지없음 %d, 오류 %d)",
                    i,
                    total,
                    rows,
                    len(no_data),
                    len(still_failed),
                )
        return still_failed

    if parallel_workers > 0 and pending:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        parallel_failed: list[str] = []
        with ThreadPoolExecutor(max_workers=parallel_workers) as pool:
            futures = {pool.submit(_fetch_one, code): code for code in pending}
            done = 0
            for fut in as_completed(futures):
                code = futures[fut]
                done += 1
                try:
                    df = fut.result()
                    if not df.empty:
                        frames.append(df)
                    else:
                        no_data.append(code)
                except Exception as exc:
                    parallel_failed.append(code)
                    logger.warning("Investing %s 실패 (parallel): %s", code, exc)
                if done % 25 == 0 or done == len(pending):
                    rows = sum(len(f) for f in frames)
                    logger.info(
                        "Investing 병렬 %d/%d (누적 %d건, 오류 %d)",
                        done,
                        len(pending),
                        rows,
                        len(parallel_failed),
                    )
        failed_error = parallel_failed
    else:
        failed_error = _run_pass(pending, pass_no=1, delay=0.0)

    if failed_error and parallel_workers <= 0:
        logger.info("Investing 오류 %d종목 — 30초 후 재시도", len(failed_error))
        time.sleep(30)
        failed_error = _run_pass(failed_error, pass_no=2, delay=3.0)

    if failed_error:
        logger.warning(
            "Investing 오류 잔존 %d종목: %s",
            len(failed_error),
            ", ".join(failed_error[:20]) + ("…" if len(failed_error) > 20 else ""),
        )
    if no_data:
        logger.info("Investing 해외 리포트 없음 %d종목 (재시도 생략)", len(no_data))
    logger.info(
        "Investing 수집 완료: 성공 %d / %d (커버리지없음 %d, 오류 %d)",
        total - len(no_data) - len(failed_error),
        total,
        len(no_data),
        len(failed_error),
    )

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
    investing_parallel: int = 0,
    return_meta: bool = False,
):
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
    refreshed_count = 0
    if refresh_investing:
        logger.info("Investing 전 종목 재수집 (%d개)…", len(tickers))
        fresh_df = _collect_investing_for_tickers(
            tickers, name_map, months, parallel_workers=investing_parallel
        )
        if from_cache and cache_path.exists():
            investing_df = pd.read_csv(cache_path, encoding="utf-8-sig")
            investing_df = _merge_investing_cache(investing_df, fresh_df)
        else:
            investing_df = fresh_df
        save_csv(investing_df, cache_path)
        if not fresh_df.empty:
            refreshed_count = fresh_df["ticker"].nunique()
        stale = [
            normalize_ticker_code(t)
            for t in tickers
            if normalize_ticker_code(t) not in set(fresh_df["ticker"].astype(str))
        ] if not fresh_df.empty else list(tickers)
        if stale:
            logger.warning("Investing 미갱신 %d종목: %s", len(stale), ", ".join(stale[:15]))
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
        empty = pd.DataFrame()
        if return_meta:
            return empty, {
                "refreshed_tickers": refreshed_count,
                "total_targets": len(tickers),
                "prices_refreshed": 0,
            }
        return empty

    logger.info("Investing: %d건", len(investing_df))

    prices_df = NaverPriceCollector().collect(tickers, batch=True)
    if not prices_df.empty:
        prices_df["ticker"] = prices_df["ticker"].map(normalize_ticker_code)
    have = set(prices_df["ticker"].astype(str)) if not prices_df.empty else set()
    missing = [normalize_ticker_code(t) for t in tickers if normalize_ticker_code(t) not in have]
    if missing:
        yahoo_fill = YahooCollector().collect(missing, batch=True)
        if not yahoo_fill.empty:
            prices_df = pd.concat([prices_df, yahoo_fill], ignore_index=True)
            prices_df = prices_df.drop_duplicates(subset=["ticker"], keep="first")
    save_csv(prices_df, RAW_DIR / "sector_yahoo_prices.csv")
    prices_refreshed = len(prices_df) if not prices_df.empty else 0

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
    meta = {
        "refreshed_tickers": refreshed_count,
        "total_targets": len(tickers),
        "prices_refreshed": prices_refreshed,
    }
    if return_meta:
        return summary, meta
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
