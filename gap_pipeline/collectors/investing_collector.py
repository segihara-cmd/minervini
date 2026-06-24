"""
Investing.com 애널리스트 목표주가 수집기.

- 종목별 consensus 페이지 (kr.investing.com)
- KRX 전 종목 × 최근 N개월 필터 지원
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from collectors.base import BaseCollector
from collectors.market_universe import load_krx_universe
from config.settings import (
    DEFAULT_TICKERS,
    INVESTING_403_BACKOFF_BASE,
    INVESTING_403_COOLDOWN_AFTER,
    INVESTING_403_COOLDOWN_SECONDS,
    INVESTING_403_MAX_RETRIES,
    INVESTING_403_TICKER_REST,
    INVESTING_BATCH_EVERY,
    INVESTING_BATCH_REST_SECONDS,
    INVESTING_REQUEST_DELAY_SECONDS,
    INVESTING_UNIVERSE_CHECKPOINT,
    INVESTING_UNIVERSE_CSV,
    INVESTING_UNIVERSE_MONTHS,
    RAW_DIR,
)
from utils.dates import parse_report_date
from utils.rate_limit import throttle
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)

INVESTING_BASE = "https://kr.investing.com"
CONSENSUS_PATH = "/equities/{slug}-consensus-estimates"
SEARCH_PATH = "/search/?q={query}"

FOREIGN_FIRM_KEYWORDS = (
    "jpmorgan", "jp morgan", "morgan stanley", "goldman", "citi", "citigroup",
    "barclays", "ubs", "credit suisse", "deutsche", "nomura", "macquarie",
    "hsbc", "clsa", "bernstein", "jefferies", "bofa", "bank of america",
    "wells fargo", "rbc", "societe", "bnp", "ing ", "instinet",
)

DOMESTIC_KEYWORDS = (
    "증권", "투자", "금융", "리서치", "한국", "미래에셋", "삼성", "kb", "nh",
    "신한", "하나", "대신", "한화", "키움", "메리츠", "sk ", "iM", "유진",
)

FIRM_ALIASES = {
    "jpmorgan": "JP Morgan",
    "jp morgan": "JP Morgan",
    "goldman sachs": "Goldman Sachs",
    "citigroup": "Citi",
    "citi": "Citi",
    "nomura/instinet": "Nomura",
    "nomura": "Nomura",
    "morgan stanley": "Morgan Stanley",
    "barclays": "Barclays",
    "ubs": "UBS",
    "clsa": "CLSA",
    "macquarie": "Macquarie",
}

SLUG_CACHE_PATH = RAW_DIR / "investing_slug_cache.json"

# 알려진 slug (검색 실패 감소)
KNOWN_SLUGS: dict[str, str] = {
    "005380": "hyundai-motor",
    "005930": "samsung-electronics-co-ltd",
    "000660": "sk-hynix-inc",
    "035420": "naver",
}

# 잘못된 slug → Investing KR 실제 slug
SLUG_ALIASES: dict[str, str] = {
    "sk-hynix": "sk-hynix-inc",
}


def _normalize_firm(name: str) -> str:
    key = name.strip().lower()
    return FIRM_ALIASES.get(key, name.strip())


def _is_foreign_firm(name: str) -> bool:
    lower = name.lower()
    if any(k in lower for k in DOMESTIC_KEYWORDS):
        if not any(k in lower for k in FOREIGN_FIRM_KEYWORDS):
            return False
    return any(k in lower for k in FOREIGN_FIRM_KEYWORDS)


def _parse_investing_date(text: str) -> str | None:
    text = (text or "").strip()
    m = re.search(r"(\d{4})\s*년?\s*(\d{1,2})\s*월?\s*(\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _parse_number(text: str) -> float | None:
    if not text or text in ("-", "N/A"):
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _filter_outlier_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """동일 페이지 내 비정상 목표가(단위 오류 등) 제거."""
    prices = [r["target_price"] for r in rows if r.get("target_price")]
    if len(prices) < 2:
        return rows
    median = float(sorted(prices)[len(prices) // 2])
    floor = max(median * 0.15, 1000.0)
    return [r for r in rows if (r.get("target_price") or 0) >= floor]


class InvestingCollector(BaseCollector):
    """Investing.com 애널리스트 평가 수집."""

    source_name = "investing_com"

    def __init__(self) -> None:
        self._slug_cache: dict[str, str] = self._load_slug_cache()
        self._slug_cache.update(KNOWN_SLUGS)
        self._consecutive_403 = 0

    def _load_slug_cache(self) -> dict[str, str]:
        if SLUG_CACHE_PATH.exists():
            try:
                return json.loads(SLUG_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_slug_cache(self) -> None:
        SLUG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SLUG_CACHE_PATH.write_text(
            json.dumps(self._slug_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _throttle_investing(self) -> None:
        """Investing 전용 간격 + 지터."""
        jitter = random.uniform(0.4, 1.5)
        throttle(INVESTING_REQUEST_DELAY_SECONDS + jitter)

    def _on_403_failure(self) -> None:
        self._consecutive_403 += 1
        if self._consecutive_403 >= INVESTING_403_COOLDOWN_AFTER:
            logger.warning(
                "Investing 연속 403 %d회 — %d초 휴식",
                self._consecutive_403,
                int(INVESTING_403_COOLDOWN_SECONDS),
            )
            time.sleep(INVESTING_403_COOLDOWN_SECONDS)
            self._consecutive_403 = 0

    def _fetch(self, url: str) -> str:
        """403 차단 시 지수 백오프 후 재시도; 반복 차단이면 예외."""
        last_exc: Exception | None = None
        max_attempts = max(1, INVESTING_403_MAX_RETRIES)
        for attempt in range(1, max_attempts + 1):
            self._throttle_investing()
            try:
                r = curl_requests.get(url, impersonate="chrome", timeout=30)
                if r.status_code == 403:
                    wait = min(
                        INVESTING_403_BACKOFF_BASE * (2 ** (attempt - 1)),
                        180.0,
                    )
                    logger.warning(
                        "Investing 403 — %.0f초 대기 (%d/%d): %s",
                        wait,
                        attempt,
                        max_attempts,
                        url[:70],
                    )
                    time.sleep(wait)
                    last_exc = curl_requests.exceptions.HTTPError("HTTP 403")
                    continue
                r.raise_for_status()
                self._consecutive_403 = 0
                return r.text
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    time.sleep(8 * attempt)
        self._on_403_failure()
        raise last_exc  # type: ignore[misc]

    def resolve_slug(self, ticker: str, stock_name: str | None = None) -> str | None:
        code = self.normalize_ticker(ticker)
        if code in self._slug_cache:
            slug = SLUG_ALIASES.get(self._slug_cache[code], self._slug_cache[code])
            return slug
        if code in KNOWN_SLUGS:
            return KNOWN_SLUGS[code]

        queries = [f"{stock_name} {code}" if stock_name else "", code, stock_name or ""]
        for query in queries:
            if not query or not str(query).strip():
                continue
            url = INVESTING_BASE + SEARCH_PATH.format(query=query.strip())
            html = self._fetch(url)
            soup = BeautifulSoup(html, "html.parser")

            candidates: list[tuple[str, str]] = []
            for a in soup.select("a[href*='/equities/']"):
                href = a.get("href", "")
                m = re.search(r"/equities/([a-z0-9\-]+)", href)
                if not m:
                    continue
                slug = m.group(1)
                skip_slugs = (
                    "south-korea", "trending", "most-", "world-", "americas",
                    "asia-pacific", "europe", "america", "australia", "indices",
                    "etf", "fund", "futures", "bonds", "currency", "crypto",
                )
                if any(x in slug for x in skip_slugs):
                    continue
                if "consensus" in slug or slug.count("-") < 1:
                    continue
                candidates.append((slug, a.get_text(strip=True)))

            # 티커·종목명이 텍스트에 명시된 경우만 (첫 후보 자동 매칭 제거)
            for slug, text in candidates:
                if code in text:
                    self._slug_cache[code] = slug
                    self._save_slug_cache()
                    return slug
                if stock_name and len(stock_name) >= 2 and stock_name in text:
                    self._slug_cache[code] = slug
                    self._save_slug_cache()
                    return slug

        logger.debug("Investing slug 없음: %s (%s)", code, stock_name)
        return None

    def fetch_consensus(
        self,
        ticker: str,
        stock_name: str | None = None,
        *,
        foreign_only: bool = False,
        months: int | None = None,
        slug: str | None = None,
    ) -> pd.DataFrame:
        """종목별 애널리스트 평가 테이블."""
        code = self.normalize_ticker(ticker)
        resolved = slug or self.resolve_slug(code, stock_name)
        if not resolved:
            return pd.DataFrame()
        resolved = SLUG_ALIASES.get(resolved, resolved)

        cutoff = None
        if months and months > 0:
            cutoff = (datetime.now() - timedelta(days=months * 31)).date()

        url = INVESTING_BASE + CONSENSUS_PATH.format(slug=resolved)
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        rows: list[dict[str, Any]] = []
        tables = soup.select("table.freeze-column-w-1, table.w-full.border-collapse")
        if not tables:
            tables = soup.select("table")

        for table in tables:
            hr = table.select_one("tr")
            header_text = hr.get_text(" ", strip=True) if hr else ""
            if "평가" not in header_text and "목표" not in header_text:
                continue

            for tr in table.select("tr"):
                tds = [td.get_text(strip=True) for td in tr.select("td")]
                if len(tds) < 6:
                    continue
                firm = tds[0]
                if not firm or "평가" in firm:
                    continue
                if foreign_only and not _is_foreign_firm(firm):
                    continue

                opinion = tds[2] if len(tds) > 2 else ""
                target = _parse_number(tds[3] if len(tds) > 3 else "")
                if target is None or target < 100:
                    continue
                prev_target = _parse_number(tds[5] if len(tds) > 5 else "")
                report_date = _parse_investing_date(tds[-1])

                if cutoff and report_date:
                    dt = parse_report_date(report_date)
                    if dt is not None and dt.date() < cutoff:
                        continue

                region = "foreign" if _is_foreign_firm(firm) else "domestic"
                revision_pct = None
                if prev_target and target:
                    revision_pct = round((target / prev_target - 1) * 100, 2)

                rows.append(
                    {
                        "ticker": code,
                        "stock_name": stock_name or "",
                        "securities_company": _normalize_firm(firm),
                        "report_date": report_date,
                        "target_price": target,
                        "previous_target_price": prev_target,
                        "previous_report_date": None,
                        "target_revision_pct": revision_pct,
                        "opinion": opinion,
                        "data_source": "investing.com",
                        "source_region": region,
                        "report_nid": f"investing_{resolved}_{firm}_{report_date}",
                        "investing_slug": resolved,
                    }
                )
            if rows:
                break

        rows = _filter_outlier_targets(rows)
        return pd.DataFrame(rows)

    def collect_universe(
        self,
        months: int | None = None,
        max_tickers: int | None = None,
        tickers: list[str] | None = None,
        resume: bool = True,
        foreign_only: bool = False,
    ) -> pd.DataFrame:
        """
        KRX 전 종목(또는 지정 종목) Investing.com 최근 N개월 목표주가 수집.
        """
        months = months if months is not None else INVESTING_UNIVERSE_MONTHS
        if tickers:
            universe = pd.DataFrame(
                {"ticker": [str(t).zfill(6) for t in tickers], "stock_name": "", "market": ""}
            )
        else:
            universe = load_krx_universe()

        if max_tickers and max_tickers > 0:
            universe = universe.head(max_tickers)

        all_rows: list[dict[str, Any]] = []
        start_idx = 0

        if resume and INVESTING_UNIVERSE_CHECKPOINT.exists():
            try:
                meta = json.loads(
                    INVESTING_UNIVERSE_CHECKPOINT.read_text(encoding="utf-8")
                )
                start_idx = int(meta.get("last_index", -1)) + 1
                if INVESTING_UNIVERSE_CSV.exists() and start_idx > 0:
                    cached = pd.read_csv(INVESTING_UNIVERSE_CSV, encoding="utf-8-sig")
                    all_rows = cached.to_dict("records")
                logger.info("Investing 유니버스 재개: %d/%d", start_idx, len(universe))
            except Exception:
                pass

        INVESTING_UNIVERSE_CSV.parent.mkdir(parents=True, exist_ok=True)
        hits = 0
        misses = 0

        for i in range(start_idx, len(universe)):
            row = universe.iloc[i]
            code = str(row["ticker"]).zfill(6)
            name = str(row.get("stock_name", "") or "")

            try:
                df = self.fetch_consensus(
                    code,
                    name,
                    foreign_only=foreign_only,
                    months=months,
                )
                if not df.empty:
                    all_rows.extend(df.to_dict("records"))
                    hits += 1
                else:
                    misses += 1
            except Exception as exc:
                misses += 1
                if "403" in str(exc):
                    logger.warning(
                        "Investing %s 403 — 종목 후 %d초 휴식",
                        code,
                        int(INVESTING_403_TICKER_REST),
                    )
                    time.sleep(INVESTING_403_TICKER_REST)
                logger.debug("Investing %s 실패: %s", code, exc)

            processed = i - start_idx + 1
            if (
                INVESTING_BATCH_EVERY > 0
                and processed > 0
                and processed % INVESTING_BATCH_EVERY == 0
            ):
                logger.info(
                    "Investing 배치 휴식: %d종목 처리 후 %d초",
                    INVESTING_BATCH_EVERY,
                    int(INVESTING_BATCH_REST_SECONDS),
                )
                time.sleep(INVESTING_BATCH_REST_SECONDS)

            checkpoint_every = max(INVESTING_BATCH_EVERY, 25)
            if (i + 1) % checkpoint_every == 0 or i == len(universe) - 1:
                pd.DataFrame(all_rows).to_csv(
                    INVESTING_UNIVERSE_CSV, index=False, encoding="utf-8-sig"
                )
                INVESTING_UNIVERSE_CHECKPOINT.write_text(
                    json.dumps(
                        {
                            "last_index": i,
                            "total": len(universe),
                            "hits": hits,
                            "misses": misses,
                            "rows": len(all_rows),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                logger.info(
                    "Investing 유니버스 %d/%d | 데이터있음 %d | 누적 %d행",
                    i + 1,
                    len(universe),
                    hits,
                    len(all_rows),
                )

        if not all_rows:
            return pd.DataFrame()

        out = pd.DataFrame(all_rows)
        return out.drop_duplicates(
            subset=["ticker", "securities_company", "report_date", "target_price"],
            keep="last",
        )

    def collect(
        self,
        tickers: list[str] | None = None,
        stock_names: dict[str, str] | None = None,
        foreign_only: bool = True,
        months: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        targets = tickers or DEFAULT_TICKERS
        names = stock_names or {}
        frames: list[pd.DataFrame] = []

        for raw in targets:
            code = self.normalize_ticker(raw)
            try:
                df = self.fetch_consensus(
                    code,
                    names.get(code),
                    foreign_only=foreign_only,
                    months=months,
                )
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.error("Investing %s 실패: %s", code, exc)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["ticker", "securities_company", "report_date", "target_price"],
            keep="last",
        )
