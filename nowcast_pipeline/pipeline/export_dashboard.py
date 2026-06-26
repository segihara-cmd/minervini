"""관세청 API 기반 반도체 분기 수출 → 대시보드 JSON.

fPER Research `project/config/samsung_nowcast_config.py` +
`collectors/customs_trade_collector.py` (data.go.kr) 와 동일 소스.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

from collectors.customs_trade_collector import ITEMTRADE_URL, NITEMTRADE_URL
from config.samsung_nowcast_config import CORE_EXPORT_COUNTRIES, DEFAULT_HS_CODE
from config.settings import CUSTOMS_TRADE_CACHE, DATA_GO_KR_API_KEY
from pipeline.quarterly_export_analysis import analyze_quarterly_exports


def _customs_api_meta() -> dict:
    """관세청 공공데이터 API 메타 (samsung_nowcast_config + collector)."""
    key_set = bool((DATA_GO_KR_API_KEY or "").strip())
    sources: dict[str, int] = {}
    if CUSTOMS_TRADE_CACHE.exists():
        try:
            payload = json.loads(CUSTOMS_TRADE_CACHE.read_text(encoding="utf-8"))
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            for v in data.values():
                if isinstance(v, dict):
                    src = v.get("api_source", "unknown")
                    sources[src] = sources.get(src, 0) + 1
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "provider": "data.go.kr",
        "envKey": "DATA_GO_KR_API_KEY",
        "keyConfigured": key_set,
        "itemtradeUrl": ITEMTRADE_URL,
        "nitemtradeUrl": NITEMTRADE_URL,
        "hsCode": DEFAULT_HS_CODE,
        "countries": list(CORE_EXPORT_COUNTRIES),
        "countryCount": len(CORE_EXPORT_COUNTRIES),
        "cacheEntries": sum(sources.values()),
        "cacheBySource": sources,
    }

def _build_summary(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    records = df.to_dict("records")
    lines: list[str] = []
    neg_rows = [
        r for r in records
        if r.get("전분기대비_%") is not None and pd.notna(r["전분기대비_%"]) and r["전분기대비_%"] < 0
    ]
    if len(neg_rows) == 1:
        n = neg_rows[0]
        idx = records.index(n)
        plus_after = sum(
            1 for r in records[idx + 1:]
            if r.get("전분기대비_%") is not None and pd.notna(r["전분기대비_%"]) and r["전분기대비_%"] > 0
        )
        if plus_after:
            lines.append(
                f"{n['분기']}만 유일하게 QoQ 마이너스({n['전분기대비_%']:+.0f}%)였고, "
                f"이후 {plus_after}분기 연속 QoQ 플러스"
            )
    latest = records[-1]
    if latest.get("전년동기대비_%") is not None and pd.notna(latest["전년동기대비_%"]):
        if abs(latest["전년동기대비_%"]) >= 80:
            lines.append(
                f"{latest['분기']} YoY {latest['전년동기대비_%']:+.1f}% — "
                "수출 급증 추세가 분기 단위로 이어짐"
            )
    if len(records) >= 2:
        prev = records[-2]
        if (
            latest.get("전분기대비_%") is not None and pd.notna(latest["전분기대비_%"])
            and prev.get("전분기대비_%") is not None and pd.notna(prev["전분기대비_%"])
        ):
            slower = latest["전분기대비_%"] < prev["전분기대비_%"]
            lines.append(
                f"{latest['분기']}는 QoQ {latest['전분기대비_%']:+.0f}%로 "
                f"{prev['분기']}({prev['전분기대비_%']:+.0f}%)보다 "
                f"{'둔화' if slower else '가속'}했지만 "
                f"{'여전히 높은 성장' if latest['전분기대비_%'] > 0 else '역성장'}"
            )
    return lines[:3]


def _parse_monthly_row(row: pd.Series) -> list[dict]:
    est_months = set()
    if pd.notna(row.get("E추정월")) and str(row["E추정월"]).strip():
        est_months = {m.strip() for m in str(row["E추정월"]).split(",") if m.strip()}
    monthly: list[dict] = []
    year = int(str(row["분기"])[:4])
    for m in re.finditer(r"(\d{2})월\s*\$([\d.]+)B", str(row.get("월별_USD_B", ""))):
        mm = int(m.group(1))
        ym = f"{year}-{mm:02d}"
        monthly.append({
            "month": f"{mm}월",
            "exportB": round(float(m.group(2)), 2),
            "est": ym in est_months,
        })
    return monthly


def build_export_payload(
    as_of: date | None = None,
    use_cache: bool = True,
    start_year: int = 2023,
) -> dict:
    """관세청 API + E.partial_month_scaleup → 대시보드용 dict."""
    as_of = as_of or date.today()
    df = analyze_quarterly_exports(
        as_of=as_of,
        hs_code=DEFAULT_HS_CODE,
        use_cache=use_cache,
        start_year=start_year,
    )
    if df.empty:
        raise RuntimeError("집계 가능한 분기 수출 데이터가 없습니다")

    quarters = []
    for _, r in df.iterrows():
        qoq = r["전분기대비_%"] if pd.notna(r.get("전분기대비_%")) else None
        yoy = r["전년동기대비_%"] if pd.notna(r.get("전년동기대비_%")) else None
        est_raw = str(r["E추정월"]).strip() if pd.notna(r.get("E추정월")) else ""
        quarters.append({
            "q": str(r["분기"]),
            "exportB": round(float(r["수출USD_B"]), 2),
            "qoq": round(float(qoq), 2) if qoq is not None else None,
            "yoy": round(float(yoy), 2) if yoy is not None else None,
            "estMonth": est_raw or None,
            "note": "전분기 급감" if qoq is not None and float(qoq) < 0 else "",
        })

    last = df.iloc[-1]
    monthly_last_q = _parse_monthly_row(last)
    est_note = ""
    if pd.notna(last.get("E추정월")) and str(last["E추정월"]).strip():
        est_note = " · 미발표 월 E.partial_month_scaleup 추정 포함"

    api_meta = _customs_api_meta()
    source_label = (
        f"관세청 nitemtrade {api_meta['countryCount']}개국 합산 (Itemtrade fallback)"
    )

    return {
        "asOf": as_of.isoformat(),
        "hsCode": f"HS{DEFAULT_HS_CODE}",
        "title": "반도체(HS8542) 분기별 수출",
        "source": source_label,
        "note": f"관세청 API 실시간 조회{est_note}",
        "customsApi": api_meta,
        "pipeline": "run_quarterly_export_analysis / run_samsung_nowcast",
        "quarters": quarters,
        "monthlyLastQuarter": {
            "quarter": str(last["분기"]),
            "months": monthly_last_q,
        },
        "summary": _build_summary(df),
    }
