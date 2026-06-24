"""
네이버 금융 크롤링 수집기.

- 종목리포트 전체 목록(전 페이지) 수집
- 리포트 상세(nid)에서 목표주가·투자의견 추출
- PER/PBR 종목별 조회 (배치, 캐시)
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from collectors.base import BaseCollector
from config.settings import (
    DEFAULT_TICKERS,
    NAVER_DETAIL_CACHE_CSV,
    NAVER_DETAIL_WORKERS,
    NAVER_LIST_CACHE_CSV,
    NAVER_LIST_CHECKPOINT,
    NAVER_RESEARCH_MAX_PAGES,
    REQUEST_DELAY_SECONDS,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    VALUATION_DELAY_SECONDS,
    VALUATION_MAX_TICKERS,
)
from utils.rate_limit import throttle
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)

NAVER_BASE = "https://finance.naver.com"
NAVER_ITEM_MAIN = "https://finance.naver.com/item/main.naver?code={code}"
NAVER_RESEARCH_LIST = (
    "https://finance.naver.com/research/company_list.naver?page={page}"
)
NAVER_TICKER_RESEARCH_LIST = (
    "https://finance.naver.com/research/company_list.naver"
    "?searchType=itemCode&itemCode={code}&page={page}"
)
NAVER_RESEARCH_READ = (
    "https://finance.naver.com/research/company_read.naver?nid={nid}&page=1"
)


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return NAVER_BASE + href
    return f"{NAVER_BASE}/research/{href}"


def _extract_code_from_href(href: str) -> str | None:
    m = re.search(r"code=(\d{6})", href or "")
    return m.group(1) if m else None


def _extract_nid_from_href(href: str) -> str | None:
    parsed = urlparse(href or "")
    qs = parse_qs(parsed.query)
    nid = (qs.get("nid") or [None])[0]
    return str(nid) if nid else None


def _parse_number(text: str) -> float | None:
    if not text or text.strip() in ("-", "N/A", ""):
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%y.%m.%d", "%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=dt.year + 2000)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


class NaverFinanceCollector(BaseCollector):
    """네이버 금융에서 목표주가·리포트·밸류에이션 지표를 수집합니다."""

    source_name = "naver_finance"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

    @retry_on_failure()
    def _get_html(self, url: str) -> str:
        throttle(REQUEST_DELAY_SECONDS)
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        return resp.text

    def discover_total_pages(self) -> int:
        """목록 1페이지에서 '맨끝' 링크로 전체 페이지 수 파악."""
        html = self._get_html(NAVER_RESEARCH_LIST.format(page=1))
        soup = BeautifulSoup(html, "html.parser")
        max_page = 1

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "company_list.naver?page=" not in href:
                continue
            m = re.search(r"page=(\d+)", href)
            if m:
                max_page = max(max_page, int(m.group(1)))

        logger.info("네이버 종목리포트 전체 페이지: %d", max_page)
        return max_page

    def _parse_list_page(self, html: str) -> list[dict[str, Any]]:
        """단일 목록 페이지 HTML → 행 목록."""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.type_1")
        if not table:
            return []

        rows: list[dict[str, Any]] = []
        for tr in table.select("tr"):
            tds = tr.select("td")
            if len(tds) != 6:
                continue

            stock_a = tds[0].select_one("a")
            title_a = tds[1].select_one("a")
            if not stock_a or not title_a:
                continue

            code = _extract_code_from_href(stock_a.get("href", ""))
            nid = _extract_nid_from_href(title_a.get("href", ""))
            if not code:
                continue

            pdf_a = tds[3].select_one("a")
            rows.append(
                {
                    "ticker": str(code).zfill(6),
                    "stock_name": stock_a.get_text(strip=True),
                    "securities_company": tds[2].get_text(strip=True),
                    "report_date": _parse_date(tds[4].get_text(strip=True)),
                    "target_price": None,
                    "opinion": "",
                    "report_url": _abs_url(title_a.get("href", "")),
                    "pdf_url": pdf_a["href"] if pdf_a and pdf_a.get("href") else "",
                    "report_nid": nid,
                }
            )
        return rows

    def fetch_research_list_page(self, page: int) -> list[dict[str, Any]]:
        html = self._get_html(NAVER_RESEARCH_LIST.format(page=page))
        return self._parse_list_page(html)

    def fetch_ticker_research_list(
        self,
        ticker: str,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """종목코드별 국내 리포트 목록 (네이버 금융)."""
        code = self.normalize_ticker(ticker)
        rows: list[dict[str, Any]] = []
        empty_streak = 0

        for page in range(1, max_pages + 1):
            url = NAVER_TICKER_RESEARCH_LIST.format(code=code, page=page)
            try:
                html = self._get_html(url)
                page_rows = self._parse_list_page(html)
            except Exception as exc:
                logger.warning("네이버 %s page=%d 실패: %s", code, page, exc)
                break

            if not page_rows:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0
            rows.extend(page_rows)

            # 최신순 목록 — 페이지 내 전부 기간 밖이면 다음 페이지 생략
            from utils.dates import parse_report_date

            page_dates = [
                parse_report_date(r.get("report_date")) for r in page_rows
            ]
            valid_dates = [d for d in page_dates if d is not None]
            if valid_dates and all(
                d.date() < (datetime.now() - timedelta(days=186)).date()
                for d in valid_dates
            ):
                break

        return rows

    def collect_for_tickers(
        self,
        tickers: list[str],
        months: int = 6,
        max_pages_per_ticker: int = 10,
        skip_details: bool = False,
    ) -> pd.DataFrame:
        """
        지정 종목만 네이버 종목리포트 수집 (6개월 등 기간 필터).
        """
        from utils.dates import parse_report_date

        cutoff = (datetime.now() - timedelta(days=months * 31)).date()
        all_rows: list[dict[str, Any]] = []

        for i, raw in enumerate(tickers, start=1):
            code = self.normalize_ticker(raw)
            page_rows = self.fetch_ticker_research_list(
                code, max_pages=max_pages_per_ticker
            )
            in_window = 0
            for row in page_rows:
                dt = parse_report_date(row.get("report_date"))
                if dt is not None and dt.date() < cutoff:
                    continue
                all_rows.append(row)
                in_window += 1

            if i % 5 == 0 or i == len(tickers):
                logger.info(
                    "네이버 종목별 수집: %d/%d (누적 %d건)",
                    i,
                    len(tickers),
                    len(all_rows),
                )

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows).drop_duplicates(
            subset=["report_nid"], keep="last"
        )

        if not skip_details:
            df = self.enrich_report_details(df)

        df["data_source"] = "naver"
        df["source_region"] = "domestic"
        df = df[df["target_price"].notna()]
        return df.drop_duplicates(
            subset=["ticker", "securities_company", "report_date", "report_nid"],
            keep="last",
        )

    def fetch_research_list_all(
        self,
        max_pages: int | None = None,
        tickers: set[str] | None = None,
        resume: bool = True,
    ) -> pd.DataFrame:
        """
        전체(또는 지정) 페이지의 종목리포트 목록 수집.

        Parameters
        ----------
        max_pages : int
            0 또는 None이면 맨끝 페이지까지 전부 수집
        tickers : set[str], optional
            지정 시 해당 종목만 포함
        resume : bool
            체크포인트·캐시 CSV에서 이어하기
        """
        total = self.discover_total_pages()
        limit = max_pages if max_pages and max_pages > 0 else total
        limit = min(limit, total)

        start_page = 1
        cached_rows: list[dict[str, Any]] = []

        if resume and NAVER_LIST_CHECKPOINT.exists():
            try:
                meta = json.loads(NAVER_LIST_CHECKPOINT.read_text(encoding="utf-8"))
                start_page = int(meta.get("last_completed_page", 0)) + 1
                logger.info("목록 수집 재개: page %d / %d", start_page, limit)
            except Exception:
                pass

        if resume and NAVER_LIST_CACHE_CSV.exists() and start_page > 1:
            cached_df = pd.read_csv(NAVER_LIST_CACHE_CSV, dtype=str)
            cached_rows = cached_df.to_dict("records")

        NAVER_LIST_CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)

        for page in range(start_page, limit + 1):
            try:
                page_rows = self.fetch_research_list_page(page)
            except Exception as exc:
                logger.warning("리서치 목록 page=%d 실패: %s", page, exc)
                continue

            if tickers:
                page_rows = [r for r in page_rows if r["ticker"] in tickers]

            cached_rows.extend(page_rows)

            # 페이지마다 캐시·체크포인트 저장 (중단 시 재개)
            pd.DataFrame(cached_rows).to_csv(
                NAVER_LIST_CACHE_CSV, index=False, encoding="utf-8-sig"
            )
            NAVER_LIST_CHECKPOINT.write_text(
                json.dumps(
                    {
                        "last_completed_page": page,
                        "total_pages": limit,
                        "row_count": len(cached_rows),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if page % 20 == 0 or page == limit:
                logger.info(
                    "목록 수집 진행: page %d/%d (누적 %d건)",
                    page,
                    limit,
                    len(cached_rows),
                )

            if not page_rows and page > 1:
                logger.info("page=%d 에 데이터 없음 — 조기 종료", page)
                break

        df = pd.DataFrame(cached_rows)
        if df.empty:
            return df

        return df.drop_duplicates(subset=["report_nid"], keep="last")

    @retry_on_failure()
    def fetch_report_detail(self, nid: str) -> dict[str, Any]:
        html = self._get_html(NAVER_RESEARCH_READ.format(nid=nid))
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        target_price = None
        m_target = re.search(r"목표가\s*([\d,]+)", page_text)
        if m_target:
            target_price = _parse_number(m_target.group(1))

        opinion = ""
        m_opinion = re.search(r"(?:투자의견|투자 의견|의견)\s*([^\s|]+)", page_text)
        if m_opinion:
            opinion = m_opinion.group(1).strip()

        return {
            "target_price": target_price,
            "opinion": opinion,
            "report_nid": nid,
        }

    def _load_detail_cache(self) -> dict[str, dict[str, Any]]:
        if not NAVER_DETAIL_CACHE_CSV.exists():
            return {}
        df = pd.read_csv(NAVER_DETAIL_CACHE_CSV, dtype=str)
        cache = {}
        for _, row in df.iterrows():
            nid = str(row.get("report_nid", ""))
            if nid and nid != "nan":
                cache[nid] = row.to_dict()
        return cache

    def _save_detail_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        NAVER_DETAIL_CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(cache.values()).to_csv(
            NAVER_DETAIL_CACHE_CSV, index=False, encoding="utf-8-sig"
        )

    def enrich_report_details(
        self,
        df: pd.DataFrame,
        max_workers: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        목록 DataFrame에 목표주가·의견 상세 조회 결과 병합.

        nid 캐시(CSV)를 사용해 이미 조회한 리포트는 건너뜁니다.
        """
        if df.empty:
            return df

        out = df.copy()
        cache = self._load_detail_cache()
        workers = max_workers or NAVER_DETAIL_WORKERS

        pending: list[tuple[int, str]] = []
        for idx, row in out.iterrows():
            nid = row.get("report_nid")
            if not nid or pd.isna(nid):
                continue
            nid = str(nid)
            if nid in cache and cache[nid].get("target_price") not in (None, "", "nan"):
                out.at[idx, "target_price"] = _parse_number(str(cache[nid]["target_price"]))
                out.at[idx, "opinion"] = cache[nid].get("opinion", "")
                continue
            if out.at[idx, "target_price"] is not None and not pd.isna(out.at[idx, "target_price"]):
                continue
            pending.append((idx, nid))

        if limit and limit > 0:
            pending = pending[:limit]

        logger.info("상세 조회 대상: %d건 (캐시 제외)", len(pending))

        def _fetch_one(item: tuple[int, str]) -> tuple[int, str, dict[str, Any]]:
            idx, nid = item
            throttle(REQUEST_DELAY_SECONDS)
            detail = self.fetch_report_detail(nid)
            return idx, nid, detail

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, p): p for p in pending}
            for fut in as_completed(futures):
                try:
                    idx, nid, detail = fut.result()
                    out.at[idx, "target_price"] = detail.get("target_price")
                    out.at[idx, "opinion"] = detail.get("opinion", "")
                    cache[nid] = {
                        "report_nid": nid,
                        "target_price": detail.get("target_price"),
                        "opinion": detail.get("opinion", ""),
                    }
                    done += 1
                    if done % 100 == 0:
                        self._save_detail_cache(cache)
                        logger.info("상세 조회 진행: %d/%d", done, len(pending))
                except Exception as exc:
                    logger.debug("상세 조회 실패: %s", exc)

        self._save_detail_cache(cache)
        return out

    def fetch_valuation(self, ticker: str) -> dict[str, Any]:
        code = self.normalize_ticker(ticker)
        html = self._get_html(NAVER_ITEM_MAIN.format(code=code))
        soup = BeautifulSoup(html, "html.parser")

        stock_name = ""
        name_tag = soup.select_one(".wrap_company h2 a")
        if name_tag:
            stock_name = name_tag.get_text(strip=True)

        fper = _parse_number((soup.select_one("#_per") or {}).get_text() or "")
        fpbr = _parse_number((soup.select_one("#_pbr") or {}).get_text() or "")

        if fper is None or fpbr is None:
            for row in soup.select("table.tb_type1 tbody tr, table tr"):
                cells = [c.get_text(strip=True) for c in row.select("th, td")]
                if len(cells) < 2:
                    continue
                label = cells[0]
                value = _parse_number(cells[1])
                if "PER" in label and "PBR" not in label and fper is None:
                    fper = value
                if "PBR" in label and fpbr is None:
                    fpbr = value

        return {
            "ticker": code,
            "stock_name": stock_name,
            "fper": fper,
            "fpbr": fpbr,
        }

    def fetch_valuations_batch(
        self,
        tickers: list[str],
        max_tickers: int | None = None,
    ) -> pd.DataFrame:
        """리포트에 등장한 종목들의 PER/PBR 일괄 조회."""
        codes = list(dict.fromkeys(self.normalize_ticker(t) for t in tickers))
        cap = max_tickers if max_tickers and max_tickers > 0 else VALUATION_MAX_TICKERS
        if cap > 0:
            codes = codes[:cap]

        delay = VALUATION_DELAY_SECONDS or REQUEST_DELAY_SECONDS
        rows: list[dict[str, Any]] = []

        for i, code in enumerate(codes, 1):
            try:
                throttle(delay)
                rows.append(self.fetch_valuation(code))
            except Exception as exc:
                logger.debug("밸류에이션 %s 실패: %s", code, exc)
            if i % 50 == 0:
                logger.info("PER/PBR 조회: %d/%d", i, len(codes))

        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["ticker", "stock_name", "fper", "fpbr"]
        )

    def collect_full(
        self,
        max_pages: int | None = None,
        skip_details: bool = False,
        detail_limit: int | None = None,
        skip_valuation: bool = False,
        resume: bool = True,
    ) -> pd.DataFrame:
        """
        전체 금융사·전체 종목 리포트 수집 (기본 모드).

        1) 전 페이지 목록
        2) nid 상세(목표주가)
        3) 고유 종목 PER/PBR
        """
        pages = max_pages if max_pages and max_pages > 0 else NAVER_RESEARCH_MAX_PAGES
        list_df = self.fetch_research_list_all(max_pages=pages or None, resume=resume)

        if list_df.empty:
            logger.warning("리포트 목록이 비어 있습니다.")
            return list_df

        logger.info(
            "목록 수집 완료: %d건, 종목 %d개, 증권사 %d개",
            len(list_df),
            list_df["ticker"].nunique(),
            list_df["securities_company"].nunique(),
        )

        if not skip_details:
            list_df = self.enrich_report_details(list_df, limit=detail_limit)

        if not skip_valuation:
            val_df = self.fetch_valuations_batch(list_df["ticker"].unique().tolist())
            if not val_df.empty:
                list_df = list_df.merge(
                    val_df[["ticker", "fper", "fpbr", "stock_name"]].rename(
                        columns={"stock_name": "_name_val"}
                    ),
                    on="ticker",
                    how="left",
                )
                list_df["stock_name"] = list_df["stock_name"].fillna(
                    list_df["_name_val"]
                )
                list_df.drop(columns=["_name_val"], inplace=True)

        list_df["data_source"] = "naver"
        list_df["source_region"] = "domestic"
        list_df = list_df.drop_duplicates(
            subset=["ticker", "securities_company", "report_date", "report_nid"],
            keep="last",
        )
        return list_df

    def collect(
        self,
        tickers: list[str] | None = None,
        include_research_list: bool = True,
        full_scan: bool = False,
        max_pages: int | None = None,
        skip_details: bool = False,
        detail_limit: int | None = None,
        skip_valuation: bool = False,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """
        수집 진입점.

        full_scan=True 이면 전체 리포트 스캔, 아니면 tickers 기준 제한 스캔.
        """
        if full_scan:
            return self.collect_full(
                max_pages=max_pages,
                skip_details=skip_details,
                detail_limit=detail_limit,
                skip_valuation=skip_valuation,
            )

        targets = set(self.normalize_ticker(t) for t in (tickers or DEFAULT_TICKERS))
        pages = max_pages or NAVER_RESEARCH_MAX_PAGES or 50

        list_df = self.fetch_research_list_all(
            max_pages=pages,
            tickers=targets,
            resume=False,
        )

        if not skip_details and not list_df.empty:
            list_df = self.enrich_report_details(list_df, limit=detail_limit)

        if not kwargs.get("skip_valuation", False) and not list_df.empty:
            val_df = self.fetch_valuations_batch(list(targets))
            if not val_df.empty:
                list_df = list_df.merge(
                    val_df[["ticker", "fper", "fpbr"]],
                    on="ticker",
                    how="left",
                    suffixes=("", "_y"),
                )

        if list_df.empty:
            return pd.DataFrame(
                columns=[
                    "ticker", "stock_name", "securities_company",
                    "report_date", "target_price", "opinion",
                    "fper", "fpbr", "report_nid",
                ]
            )

        df = list_df if isinstance(list_df, pd.DataFrame) else pd.DataFrame(list_df)
        df["data_source"] = "naver"
        df["source_region"] = "domestic"
        return df.drop_duplicates(
            subset=["ticker", "securities_company", "report_date", "report_nid"],
            keep="last",
        )
