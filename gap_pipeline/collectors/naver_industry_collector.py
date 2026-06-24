"""
네이버 금융 업종(업종별 시세) 분류 수집.

참고: https://stock.naver.com/market/stock/kr/industry/1
      (구) https://finance.naver.com/sise/sise_group.naver?type=upjong
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config.settings import RAW_DIR, REQUEST_DELAY_SECONDS
from utils.ticker import normalize_ticker_code

logger = logging.getLogger(__name__)

NAVER_UPJONG_LIST_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
NAVER_UPJONG_DETAIL_URL = (
    "https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}"
)
INDUSTRY_CACHE_PATH = RAW_DIR / "naver_industry_stocks.json"

DEFAULT_EXCLUDE_NAME = re.compile(
    r"(SPAC|스팩|리츠|홀딩스|지주$|우B$|우$|ETN\b)",
    re.IGNORECASE,
)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }


def _parse_amount(text: str) -> float:
    """'1,234,567' 또는 '1234567' → float."""
    if not text or str(text).strip() in ("-", "N/A", "nan"):
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", str(text))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def fetch_industry_list() -> list[dict[str, str]]:
    """네이버 업종 목록 (no, name)."""
    resp = requests.get(NAVER_UPJONG_LIST_URL, headers=_headers(), timeout=20)
    resp.encoding = "euc-kr"
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a"):
        href = a.get("href", "")
        m = re.search(r"no=(\d+)", href)
        if "sise_group_detail" not in href or not m:
            continue
        no = m.group(1)
        if no in seen:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        seen.add(no)
        items.append({"no": no, "name": name})

    logger.info("네이버 업종 %d개 로드", len(items))
    return items


def fetch_industry_members(industry_no: str) -> list[dict]:
    """
    업종 구성 종목 + 네이버 페이지 거래대금.

    Returns
    -------
    list[dict]
        ticker, stock_name, naver_amount
    """
    members: list[dict] = []
    seen: set[str] = set()

    for page in range(1, 50):
        url = NAVER_UPJONG_DETAIL_URL.format(no=industry_no) + f"&page={page}"
        resp = requests.get(url, headers=_headers(), timeout=20)
        resp.encoding = "euc-kr"
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.select_one("table.type_5")
        if not table:
            break

        page_count = 0
        for tr in table.select("tr"):
            a = tr.select_one('a[href*="code="]')
            if not a:
                continue
            m = re.search(r"code=(\d+)", a.get("href", ""))
            if not m:
                continue
            code = normalize_ticker_code(m.group(1))
            name = a.get_text(strip=True).replace("*", "").strip()
            if not code or code in seen:
                continue
            if DEFAULT_EXCLUDE_NAME.search(name):
                continue

            tds = tr.select("td")
            naver_amount = 0.0
            if len(tds) >= 8:
                naver_amount = _parse_amount(tds[7].get_text(strip=True))

            seen.add(code)
            members.append(
                {
                    "ticker": code,
                    "stock_name": name,
                    "naver_amount": naver_amount,
                }
            )
            page_count += 1

        if page_count == 0:
            break
        time.sleep(REQUEST_DELAY_SECONDS * 0.15)

    return members


def build_industry_cache(force_refresh: bool = False) -> dict:
    """업종별 구성종목 JSON 캐시 생성."""
    if INDUSTRY_CACHE_PATH.exists() and not force_refresh:
        data = json.loads(INDUSTRY_CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("industries"):
            return data

    industries = fetch_industry_list()
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": NAVER_UPJONG_LIST_URL,
        "industries": [],
    }

    for i, ind in enumerate(industries, start=1):
        no = ind["no"]
        members = fetch_industry_members(no)
        payload["industries"].append(
            {
                "no": no,
                "name": ind["name"],
                "members": members,
            }
        )
        if i % 10 == 0 or i == len(industries):
            logger.info("업종 캐시 %d/%d — %s (%d종목)", i, len(industries), ind["name"], len(members))
        time.sleep(REQUEST_DELAY_SECONDS * 0.2)

    INDUSTRY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDUSTRY_CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("업종 캐시 저장: %s", INDUSTRY_CACHE_PATH)
    return payload


def _load_fdr_amounts() -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
    import FinanceDataReader as fdr

    listing = fdr.StockListing("KRX")
    listing["Code"] = listing["Code"].astype(str).map(normalize_ticker_code)
    amounts = pd.to_numeric(listing.get("Amount"), errors="coerce").fillna(0)
    amount_map = dict(zip(listing["Code"], amounts))
    name_map = dict(zip(listing["Code"], listing["Name"].astype(str)))
    market_map = dict(zip(listing["Code"], listing["Market"].astype(str)))
    return amount_map, name_map, market_map


def select_leaders_by_trading_amount(
    per_sector: int = 5,
    *,
    force_refresh_cache: bool = False,
    exclude_industry_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    네이버 업종별 거래대금 상위 N종목.

    거래대금: FDR StockListing Amount 우선, 없으면 네이버 업종 페이지 값.
    """
    cache = build_industry_cache(force_refresh=force_refresh_cache)
    amount_map, fdr_names, market_map = _load_fdr_amounts()
    skip = set(exclude_industry_names or ["기타"])

    rows: list[dict] = []
    for ind in cache.get("industries", []):
        name = ind["name"]
        if name in skip:
            continue

        ranked: list[dict] = []
        for m in ind.get("members", []):
            code = normalize_ticker_code(m.get("ticker"))
            if not code:
                continue
            fdr_amt = float(amount_map.get(code, 0) or 0)
            naver_amt = float(m.get("naver_amount", 0) or 0)
            trading_amount = fdr_amt if fdr_amt > 0 else naver_amt
            ranked.append(
                {
                    "sector": name,
                    "industry_no": ind.get("no"),
                    "ticker": code,
                    "stock_name": fdr_names.get(code) or m.get("stock_name", ""),
                    "market": market_map.get(code, ""),
                    "trading_amount": trading_amount,
                    "marcap": None,
                }
            )

        ranked.sort(key=lambda x: x["trading_amount"], reverse=True)
        rows.extend(ranked[:per_sector])

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # 시가총액 보조 컬럼
    try:
        import FinanceDataReader as fdr

        listing = fdr.StockListing("KRX")
        listing["Code"] = listing["Code"].astype(str).map(normalize_ticker_code)
        cap = dict(
            zip(
                listing["Code"],
                pd.to_numeric(listing.get("Marcap"), errors="coerce"),
            )
        )
        df["marcap"] = df["ticker"].map(cap)
    except Exception:
        pass

    return df.reset_index(drop=True)
