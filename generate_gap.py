"""
목표주가 괴리율 Top 100 (Investing 해외) → docs/gap.json + docs/gap.html
fPER Research gap_pipeline 기반
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
GAP_ROOT = ROOT / "gap_pipeline"
sys.path.insert(0, str(GAP_ROOT))

from pipeline.sector_target_summary import top_gap_stocks  # noqa: E402
from run_sector_analysis import run  # noqa: E402
from utils.ticker import normalize_ticker_code  # noqa: E402

KST = timezone(timedelta(hours=9))
TOP_N = 100
DOCS = ROOT / "docs"
OUTPUT_JSON = DOCS / "gap.json"
OUTPUT_HTML = DOCS / "gap.html"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("generate_gap")


def _clean(val):
    if val is None:
        return None
    try:
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
    except TypeError:
        pass
    if pd.isna(val):
        return None
    return val


def row_to_dict(row: pd.Series) -> dict:
    cross = bool(row.get("목표가변동_타기관"))
    basis = _clean(row.get("목표가변동기준")) or ""
    rev = _clean(row.get("목표가변동률"))
    return {
        "sector": _clean(row.get("산업")) or "",
        "name": _clean(row.get("종목명")) or "",
        "ticker": normalize_ticker_code(str(row.get("티커") or "")),
        "price": _clean(row.get("현재가")),
        "target": _clean(row.get("최근목표가")),
        "avgTarget6m": _clean(row.get("평균목표가_6M")),
        "highTarget6m": _clean(row.get("최고목표가_6M")),
        "lowTarget6m": _clean(row.get("최저목표가_6M")),
        "gap": _clean(row.get("괴리율_최근")),
        "revPct": rev,
        "revBasis": basis,
        "revCrossFirm": cross,
        "firm": _clean(row.get("최근증권사")) or "",
        "reportDate": _clean(row.get("최근발표일")) or "",
        "reportCount6m": _clean(row.get("리포트건수_6M")),
    }


TAB_NAV = """<nav style="background:#1e293b;border-bottom:2px solid #334155;display:flex;gap:0;flex-wrap:wrap">
  <a href="index.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">📊 매크로 대시보드</a>
  <a href="screener.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">🔍 ETF 스크리너</a>
  <a href="gap.html" style="padding:12px 24px;color:#f1f5f9;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid #3b82f6">📈 ETF 괴리율</a>
</nav>"""


def build_shell_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>목표주가 괴리율 Top 100</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;min-height:100vh}}
.header{{background:#1e293b;border-bottom:1px solid #334155;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.header h1{{font-size:1.05rem;font-weight:700;color:#f1f5f9}}
.updated{{font-size:.78rem;color:#94a3b8}}
.refresh-btn{{background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:.75rem;font-weight:600;cursor:pointer}}
.refresh-btn:hover{{background:#2563eb}}
.content{{padding:20px;max-width:1800px;margin:0 auto;overflow-x:auto}}
.info-bar{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;margin-bottom:16px;font-size:.85rem;color:#64748b;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.badge{{background:#2563eb;color:#eff6ff;padding:3px 10px;border-radius:12px;font-size:.75rem;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
thead th:hover{{background:#334155 !important}}
tr:hover td{{filter:brightness(.96)}}
.loading-box{{display:flex;flex-direction:column;align-items:center;padding:60px 20px;color:#64748b;gap:12px}}
.loading-box.error{{color:#dc2626}}
.spinner{{width:32px;height:32px;border:3px solid #e2e8f0;border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.footer{{text-align:center;padding:20px;color:#94a3b8;font-size:.75rem}}
</style>
</head>
<body>
<div class="header">
  <h1>📈 목표주가 괴리율 Top 100 (Investing 해외)</h1>
  <div style="display:flex;align-items:center;gap:10px">
    <span class="updated" id="updated">데이터 로딩 중...</span>
    <button type="button" class="refresh-btn" onclick="loadGap()">↻ 새로고침</button>
  </div>
</div>
{TAB_NAV}
<div class="content" id="app-content">
  <div class="loading-box"><div class="spinner"></div><p>괴리율 데이터 불러오는 중</p></div>
</div>
<div class="footer">Investing.com 해외 목표가 · Yahoo 현재가 · 투자 권유 아님</div>
<script src="gap-app.js?v=20260625-sort-date"></script>
</body>
</html>"""


def main() -> None:
    now = datetime.now(KST)
    logger.info("[%s KST] gap Top %d 생성", now.strftime("%Y-%m-%d %H:%M"), TOP_N)

    summary_path = GAP_ROOT / "data" / "processed" / "sector_target_summary.csv"
    skip_refresh = "--no-refresh" in sys.argv

    if not skip_refresh:
        cache = GAP_ROOT / "data" / "raw" / "sector_investing_reports.csv"
        try:
            run(
                from_cache=cache.exists(),
                refresh_investing=True,
                skip_etf=True,
                top_gap=TOP_N,
            )
        except Exception as e:
            logger.warning("파이프라인 갱신 실패 (%s) — 기존 요약 CSV 사용", e)

    if not summary_path.exists():
        raise FileNotFoundError(f"요약 없음: {summary_path} (--refresh 로 생성)")

    summary = pd.read_csv(summary_path, encoding="utf-8-sig")
    top = top_gap_stocks(summary, n=TOP_N)
    rows = [row_to_dict(r) for _, r in top.iterrows()]

    payload = {
        "updated": now.strftime("%Y-%m-%d %H:%M KST"),
        "count": len(rows),
        "title": f"목표주가 괴리율 Top {len(rows)} (Investing 해외)",
        "rows": rows,
    }

    DOCS.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    OUTPUT_HTML.write_text(build_shell_html(), encoding="utf-8")
    logger.info("[OK] %s (%d rows)", OUTPUT_JSON, len(rows))
    logger.info("[OK] %s", OUTPUT_HTML)


if __name__ == "__main__":
    main()
