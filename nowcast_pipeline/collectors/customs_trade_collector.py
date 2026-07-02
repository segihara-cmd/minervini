"""
관세청 공공데이터포털(data.go.kr) 수출입 API 수집기.

우선순위:
  1) 품목별 수출입실적 Itemtrade/getItemtradeList (품목별 단독 API)
  2) fallback: 품목별 국가별 nitemtrade (국가별 합산)

- serviceKey: 환경변수 DATA_GO_KR_API_KEY (.env)
- 금액 단위: USD (expDlr)
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Literal

import pandas as pd
import requests

from config.samsung_nowcast_config import (
    CORE_EXPORT_COUNTRIES,
    DEFAULT_HS_CODE,
    MAJOR_EXPORT_COUNTRIES,
    MEMORY_HS_CODE,
)
from config.settings import (
    CUSTOMS_TRADE_CACHE,
    DATA_GO_KR_API_KEY,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
)
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)

ITEMTRADE_URL = "http://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
NITEMTRADE_URL = "http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

TradeDirection = Literal["export", "import", "both"]


class CustomsApiError(RuntimeError):
    """관세청 API 호출 실패."""


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def _require_api_key() -> str:
    key = (DATA_GO_KR_API_KEY or "").strip()
    if not key:
        raise CustomsApiError(
            "DATA_GO_KR_API_KEY가 설정되지 않았습니다. "
            "project/.env 파일에 공공데이터포털 인증키를 추가하세요."
        )
    return key


_itemtrade_available: bool | None = None


@retry_on_failure(exceptions=(ConnectionError, TimeoutError, OSError, CustomsApiError))
def _fetch_itemtrade_page(
    service_key: str,
    yymm: str,
    hs_code: str,
) -> ET.Element:
    params = {
        "serviceKey": service_key,
        "strtYymm": yymm,
        "endYymm": yymm,
        "hsSgn": hs_code,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    resp = requests.get(ITEMTRADE_URL, params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 403:
        raise CustomsApiError("Itemtrade API 미승인(403) — data.go.kr에서 품목별 수출입실적 활용신청 필요")
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise CustomsApiError(
            f"Itemtrade HTTP {resp.status_code} (yymm={yymm})"
        ) from exc
    root = ET.fromstring(resp.content)
    result_code = root.findtext(".//resultCode")
    if result_code != "00":
        msg = root.findtext(".//resultMsg") or "unknown"
        raise CustomsApiError(f"Itemtrade 오류 (yymm={yymm}): {result_code} {msg}")
    return root


def _parse_itemtrade_month_total(root: ET.Element, yymm: str) -> dict[str, float]:
    """품목별 단독 API — 월별 수출/수입 총계."""
    year_label = f"{yymm[:4]}.{yymm[4:]}"
    export_total = 0.0
    import_total = 0.0
    for item in root.findall(".//item"):
        hs = item.findtext("hsCd") or item.findtext("hsSgn") or ""
        year = item.findtext("year") or item.findtext("statYm") or ""
        if hs not in ("-", ""):
            continue
        if year not in ("총계", year_label, yymm):
            continue
        export_total = float(item.findtext("expDlr") or item.findtext("expAmt") or 0)
        import_total = float(item.findtext("impDlr") or item.findtext("impAmt") or 0)
        break
    # 총계 행이 없으면 세부 HS 합산
    if export_total == 0 and import_total == 0:
        for item in root.findall(".//item"):
            yr = item.findtext("year") or ""
            if yr == year_label or yr == yymm:
                export_total += float(item.findtext("expDlr") or 0)
                import_total += float(item.findtext("impDlr") or 0)
    return {"export_usd": export_total, "import_usd": import_total}


@retry_on_failure(exceptions=(ConnectionError, TimeoutError, OSError, CustomsApiError))
def _fetch_nitemtrade_page(
    service_key: str,
    yymm: str,
    hs_code: str,
    country_code: str,
) -> ET.Element:
    params = {
        "serviceKey": service_key,
        "strtYymm": yymm,
        "endYymm": yymm,
        "hsSgn": hs_code,
        "cntyCd": country_code,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    resp = requests.get(NITEMTRADE_URL, params=params, timeout=REQUEST_TIMEOUT)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise CustomsApiError(
            f"nitemtrade HTTP {resp.status_code} (cnty={country_code}, yymm={yymm})"
        ) from exc
    root = ET.fromstring(resp.content)
    result_code = root.findtext(".//resultCode")
    if result_code != "00":
        msg = root.findtext(".//resultMsg") or "unknown"
        raise CustomsApiError(
            f"nitemtrade 오류 (cnty={country_code}, yymm={yymm}): "
            f"{result_code} {msg}"
        )
    return root


def _parse_country_month_total(root: ET.Element, yymm: str) -> dict[str, float]:
    """단일 국가·단일 월 조회 결과에서 수출/수입 합계(총계 행) 추출."""
    year_label = f"{yymm[:4]}.{yymm[4:]}"
    export_total = 0.0
    import_total = 0.0

    for item in root.findall(".//item"):
        hs = item.findtext("hsCd") or ""
        year = item.findtext("year") or ""
        if hs != "-":
            continue
        # 단월 조회: year='총계' 행 / 다월 조회: 'YYYY.MM' 형식
        if year not in ("총계", year_label):
            continue
        export_total = float(item.findtext("expDlr") or 0)
        import_total = float(item.findtext("impDlr") or 0)
        break

    return {"export_usd": export_total, "import_usd": import_total}


def _fetch_month_via_itemtrade(
    service_key: str,
    yymm: str,
    hs_code: str,
) -> dict[str, float] | None:
    global _itemtrade_available
    if _itemtrade_available is False:
        return None
    try:
        root = _fetch_itemtrade_page(service_key, yymm, hs_code)
        _itemtrade_available = True
        return _parse_itemtrade_month_total(root, yymm)
    except CustomsApiError as exc:
        if "403" in str(exc) or "미승인" in str(exc):
            _itemtrade_available = False
            logger.warning("Itemtrade API 사용 불가 → nitemtrade fallback: %s", exc)
            return None
        raise


def fetch_semiconductor_trade_month(
    yymm: str,
    hs_code: str = DEFAULT_HS_CODE,
    countries: tuple[str, ...] = CORE_EXPORT_COUNTRIES,
    direction: TradeDirection = "export",
    use_cache: bool = True,
) -> dict[str, float]:
    """
    특정 월 반도체(HS) 수출·수입 USD.

    Itemtrade(품목별 단독) 우선, 실패 시 nitemtrade 국가별 합산.
    """
    service_key = _require_api_key()
    cache_key = f"{hs_code}:{yymm}:{direction}"
    cache = _load_cache()

    if use_cache and cache_key in cache:
        cached = cache[cache_key]
        if direction != "export" or cached.get("export_usd", 0) > 0:
            cached["api_source"] = cached.get("api_source", "cache")
            return cached
        logger.info("yymm=%s stale zero cache — 재조회", yymm)

    itemtrade = _fetch_month_via_itemtrade(service_key, yymm, hs_code)
    api_source = "itemtrade"

    if itemtrade is not None and itemtrade["export_usd"] > 0:
        export_sum = itemtrade["export_usd"]
        import_sum = itemtrade["import_usd"]
        logger.info(
            "관세청 Itemtrade hs=%s yymm=%s exp=%.0f key=%s",
            hs_code,
            yymm,
            export_sum,
            _mask_key(service_key),
        )
    else:
        api_source = "nitemtrade"
        export_sum = 0.0
        import_sum = 0.0
        logger.info(
            "관세청 nitemtrade hs=%s yymm=%s countries=%d key=%s",
            hs_code,
            yymm,
            len(countries),
            _mask_key(service_key),
        )

        def _one_country(cnty: str) -> tuple[str, float, float]:
            root = _fetch_nitemtrade_page(service_key, yymm, hs_code, cnty)
            parsed = _parse_country_month_total(root, yymm)
            time.sleep(REQUEST_DELAY_SECONDS * 0.15)
            return cnty, parsed["export_usd"], parsed["import_usd"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_one_country, c): c for c in countries}
            for fut in as_completed(futures):
                try:
                    _, exp, imp = fut.result()
                except CustomsApiError:
                    logger.warning("국가 %s 조회 실패 — 건너뜀", futures[fut])
                    continue
                export_sum += exp
                import_sum += imp

    result = {
        "export_usd": export_sum,
        "import_usd": import_sum,
        "net_export_usd": export_sum - import_sum,
        "api_source": api_source,
    }

    if direction != "export" or export_sum > 0:
        cache[cache_key] = result
        _save_cache(cache)
    else:
        logger.warning("yymm=%s export=0 — 캐시 미저장 (API 미반영)", yymm)
    return result


def sum_quarter_export_usd(
    year: int,
    quarter: int,
    monthly: dict[str, float],
) -> float:
    """월별 dict에서 분기(3개월) 수출 USD 합."""
    start = (quarter - 1) * 3 + 1
    labels = [f"{year}-{m:02d}" for m in range(start, start + 3)]
    return sum(monthly.get(lb, 0.0) for lb in labels)


def fetch_semiconductor_trade_range(
    start_yymm: str,
    end_yymm: str,
    hs_code: str = DEFAULT_HS_CODE,
    countries: tuple[str, ...] = CORE_EXPORT_COUNTRIES,
    use_cache: bool = True,
    month_list: list[str] | None = None,
) -> pd.DataFrame:
    """월별 반도체 수출·수입 시계열 DataFrame."""
    if month_list:
        months = [pd.Period(m, freq="M") for m in month_list]
    else:
        months = pd.period_range(start_yymm, end_yymm, freq="M")
    rows = []
    for p in months:
        yymm = p.strftime("%Y%m")
        trade = fetch_semiconductor_trade_month(
            yymm, hs_code=hs_code, countries=countries, use_cache=use_cache
        )
        rows.append(
            {
                "year_month": yymm,
                "export_usd": trade["export_usd"],
                "import_usd": trade["import_usd"],
                "net_export_usd": trade["net_export_usd"],
                "api_source": trade.get("api_source", ""),
            }
        )
    return pd.DataFrame(rows)


def _load_cache() -> dict:
    if not CUSTOMS_TRADE_CACHE.exists():
        return {}
    try:
        payload = json.loads(CUSTOMS_TRADE_CACHE.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    CUSTOMS_TRADE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(),
        "data": cache,
    }
    CUSTOMS_TRADE_CACHE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
