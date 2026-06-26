"""
generate_dashboard.py
반도체 포트폴리오 대시보드 정적 HTML 생성기
GitHub Actions에서 매일 실행 → docs/index.html 갱신
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yfinance as yf
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent / 'nowcast_pipeline'))
from pipeline.export_dashboard import build_export_payload

KST = timezone(timedelta(hours=9))
OUTPUT = Path(__file__).parent / 'docs' / 'index.html'
EXPORT_JSON = Path(__file__).parent / 'docs' / 'semiconductor-export.json'

# ──────────────────────────────────────────────
# 데이터 수집
# ──────────────────────────────────────────────
def _close_list(df):
    if df is None or df.empty:
        return [], []
    if isinstance(df.columns, pd.MultiIndex):
        for pt in ('Close', 'Adj Close'):
            cols = [c for c in df.columns if c[0] == pt]
            if cols:
                s = df[cols[0]].dropna()
                if not s.empty:
                    return [d.strftime('%Y-%m-%d') for d in s.index], [round(float(v), 4) for v in s.values]
    else:
        for col in ('Close', 'Adj Close'):
            if col in df.columns:
                s = df[col].dropna()
                if not s.empty:
                    return [d.strftime('%Y-%m-%d') for d in s.index], [round(float(v), 4) for v in s.values]
    return [], []

def fetch(ticker, period):
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        return df if not df.empty else None
    except Exception as e:
        print(f'[경고] {ticker}: {e}')
        return None

def fetch_live(ticker, period):
    """일봉 + Yahoo 현재가(regularMarketPrice) 반영."""
    dates, values = _close_list(fetch(ticker, period))
    if not dates:
        return dates, values
    try:
        info = yf.Ticker(ticker).fast_info
        live = getattr(info, 'last_price', None) or getattr(info, 'regular_market_price', None)
        if live is not None:
            today = datetime.now(KST).strftime('%Y-%m-%d')
            live_f = round(float(live), 4)
            if today >= dates[-1]:
                if today == dates[-1]:
                    values[-1] = live_f
                else:
                    dates.append(today)
                    values.append(live_f)
    except Exception as e:
        print(f'[경고] live {ticker}: {e}')
    return dates, values

ADR_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
}

def _parse_adr_embed(html, name):
    m = re.search(rf'const {name}=\[(.*?)\];', html, re.S)
    if not m:
        return [], []
    pairs = re.findall(r'\[(\d+),\s*([\d.]+)\]', m.group(1))
    dates = [datetime.fromtimestamp(int(ts) / 1000).strftime('%Y-%m-%d') for ts, _ in pairs]
    values = [round(float(v), 2) for _, v in pairs]
    return dates, values

def fetch_adr(days=63):
    """adrinfo.kr/chart와 동일한 KOSPI/KOSDAQ ADR 시계열 수집"""
    try:
        r = requests.get('http://adrinfo.kr/chart', headers=ADR_HEADERS, timeout=30)
        r.raise_for_status()
        ks_dates, ks_vals = _parse_adr_embed(r.text, 'kospi_adr')
        kq_dates, kq_vals = _parse_adr_embed(r.text, 'kosdaq_adr')
        kq_map = dict(zip(kq_dates, kq_vals))
        dates, kospi, kosdaq = [], [], []
        for d, v in zip(ks_dates, ks_vals):
            if d in kq_map:
                dates.append(d)
                kospi.append(v)
                kosdaq.append(kq_map[d])
        take = min(days, len(dates))
        if take == 0:
            return [], [], []
        return dates[-take:], kospi[-take:], kosdaq[-take:]
    except Exception as e:
        print(f'[경고] ADR: {e}')
        return [], [], []

def latest(dates, values):
    if not values:
        return None
    return values[-1]

def pct_chg(values):
    if not values or len(values) < 2:
        return None
    return round((values[-1] - values[-2]) / values[-2] * 100, 2)

def sma(values, n):
    if len(values) < n:
        return None
    return round(float(np.mean(values[-n:])), 2)

def norm(values):
    """0-index 기준 누적 수익률 %)로 정규화"""
    if not values or values[0] == 0:
        return []
    base = values[0]
    return [round((v - base) / base * 100, 3) for v in values]

def align_and_norm(dates_a, vals_a, dates_b, vals_b):
    """두 시리즈를 날짜로 inner-join 후 정규화"""
    set_b = dict(zip(dates_b, vals_b))
    aligned_a, aligned_b, aligned_dates = [], [], []
    for d, va in zip(dates_a, vals_a):
        if d in set_b:
            aligned_a.append(va)
            aligned_b.append(set_b[d])
            aligned_dates.append(d)
    return aligned_dates, norm(aligned_a), norm(aligned_b)

# ──────────────────────────────────────────────
# Exit Level 계산
# ──────────────────────────────────────────────
EXIT_META = {
    0: ('🟢 L0', '정상 — 전종목 보유', '#16a34a'),
    1: ('🟡 L1', '경계 — 신규 매수 자제', '#ca8a04'),
    2: ('🟠 L2', '경고 — 30% 비중 축소', '#ea580c'),
    3: ('🔴 L3', '위험 — 추가 50% 축소', '#dc2626'),
    4: ('🚨 L4', '전량청산 — 즉시 매도', '#7f1d1d'),
}

def compute_exit(kospi_vals, vix_vals, tnx_vals):
    if not kospi_vals or len(kospi_vals) < 210:
        return None
    arr = kospi_vals
    ma50  = float(np.mean(arr[-50:]))
    ma150 = float(np.mean(arr[-150:]))
    ma200 = float(np.mean(arr[-200:]))
    ma200_21 = float(np.mean(arr[-221:-21])) if len(arr) >= 221 else ma200
    curr = arr[-1]
    aligned = (curr > ma50 > ma150 > ma200 and ma200 > ma200_21)

    vix = vix_vals[-1] if vix_vals else None
    tnx = tnx_vals[-1] if tnx_vals else None

    if curr < ma200: return 4, ma50, ma150, ma200, aligned
    if curr < ma150: return 3, ma50, ma150, ma200, aligned
    if ma50 < ma150: return 2, ma50, ma150, ma200, aligned
    if vix and vix > 30: return 2, ma50, ma150, ma200, aligned
    if not aligned:  return 1, ma50, ma150, ma200, aligned
    if vix and vix > 25: return 1, ma50, ma150, ma200, aligned
    if tnx and tnx > 4.5: return 1, ma50, ma150, ma200, aligned
    return 0, ma50, ma150, ma200, aligned

def vix_sideways(vix_vals):
    if not vix_vals or len(vix_vals) < 15:
        return False
    last15 = vix_vals[-15:]
    return all(20 <= v <= 30 for v in last15)

# ──────────────────────────────────────────────
# 메인 — 데이터 수집 및 HTML/JSON 생성
# ──────────────────────────────────────────────
def collect_data():
    now_kst = datetime.now(KST)

    # Fetch
    sam_d,  sam_v  = fetch_live('005930.KS', '3mo')
    hyn_d,  hyn_v  = fetch_live('000660.KS', '3mo')
    ks11_d, ks11_v = fetch_live('^KS11',     '1y')
    sox_d,  sox_v  = fetch_live('^SOX',      '3mo')
    nvda_d, nvda_v = fetch_live('NVDA',      '3mo')
    vix_d,  vix_v  = fetch_live('^VIX',      '3mo')
    tnx_d,  tnx_v  = fetch_live('^TNX',      '3mo')
    fx_d,   fx_v   = fetch_live('USDKRW=X',  '3mo')
    wti_d,  wti_v  = fetch_live('CL=F',      '3mo')
    mu_d,   mu_v   = fetch_live('MU',         '3mo')
    skew_d, skew_v = fetch_live('^SKEW',      '3mo')
    adr_d, adr_kospi, adr_kosdaq = fetch_adr()

    # 1년치 KOSPI for MA computation
    ks11_1y_d, ks11_1y_v = fetch_live('^KS11', '2y')

    # Exit Level
    exit_result = compute_exit(ks11_1y_v, vix_v, tnx_v)
    if exit_result:
        exit_lv, ma50, ma150, ma200, aligned = exit_result
    else:
        exit_lv, ma50, ma150, ma200, aligned = None, None, None, None, None

    sideways = vix_sideways(vix_v)
    el_badge, el_desc, el_color = EXIT_META.get(exit_lv, ('⚪', '데이터 부족', '#94a3b8'))

    # Normalized comparison series
    sam_hyn_dates, sam_norm, hyn_norm = align_and_norm(sam_d, sam_v, hyn_d, hyn_v)
    sox_nvda_dates, sox_norm, nvda_norm = align_and_norm(sox_d, sox_v, nvda_d, nvda_v)
    sox_mu_dates, sox_norm2, mu_norm = align_and_norm(sox_d, sox_v, mu_d, mu_v)

    # KOSPI MA series (last 1y only)
    ks_dates = ks11_1y_d[-252:] if len(ks11_1y_d) >= 252 else ks11_1y_d
    ks_vals  = ks11_1y_v[-252:] if len(ks11_1y_v) >= 252 else ks11_1y_v

    def rolling_ma(vals, n):
        result = []
        for i in range(len(vals)):
            if i + 1 >= n:
                result.append(round(float(np.mean(vals[i+1-n:i+1])), 2))
            else:
                result.append(None)
        return result

    ks_ma50  = rolling_ma(ks_vals, 50)
    ks_ma150 = rolling_ma(ks_vals, 150)
    ks_ma200 = rolling_ma(ks_vals, 200)

    # Spot values
    sam_last  = latest(sam_d,  sam_v)
    hyn_last  = latest(hyn_d,  hyn_v)
    ks_last   = latest(ks11_d, ks11_v)
    sox_last  = latest(sox_d,  sox_v)
    nvda_last = latest(nvda_d, nvda_v)
    vix_last  = latest(vix_d,  vix_v)
    tnx_last  = latest(tnx_d,  tnx_v)
    fx_last   = latest(fx_d,   fx_v)
    wti_last  = latest(wti_d,  wti_v)
    mu_last   = latest(mu_d,   mu_v)
    skew_last = latest(skew_d, skew_v)
    adr_kospi_last = latest(adr_d, adr_kospi)
    adr_kosdaq_last = latest(adr_d, adr_kosdaq)

    sam_pct  = pct_chg(sam_v)
    hyn_pct  = pct_chg(hyn_v)
    ks_pct   = pct_chg(ks11_v)
    sox_pct  = pct_chg(sox_v)
    nvda_pct = pct_chg(nvda_v)
    vix_pct  = pct_chg(vix_v)
    tnx_pct  = pct_chg(tnx_v)
    fx_pct   = pct_chg(fx_v)
    wti_pct  = pct_chg(wti_v)
    mu_pct   = pct_chg(mu_v)
    skew_pct = pct_chg(skew_v)
    adr_kospi_pct = pct_chg(adr_kospi)
    adr_kosdaq_pct = pct_chg(adr_kosdaq)

    # ── JSON payload (embed into HTML) ─────────
    data = {
        'updated': now_kst.strftime('%Y-%m-%d %H:%M KST'),
        'exit': {
            'level': exit_lv,
            'badge': el_badge,
            'desc':  el_desc,
            'color': el_color,
            'sideways': sideways,
            'ma50':  ma50,
            'ma150': ma150,
            'ma200': ma200,
            'aligned': aligned,
        },
        'kpi': {
            'sam':  {'val': sam_last,  'pct': sam_pct},
            'hyn':  {'val': hyn_last,  'pct': hyn_pct},
            'ks11': {'val': ks_last,   'pct': ks_pct},
            'sox':  {'val': sox_last,  'pct': sox_pct},
            'nvda': {'val': nvda_last, 'pct': nvda_pct},
            'vix':  {'val': vix_last,  'pct': vix_pct},
            'tnx':  {'val': tnx_last,  'pct': tnx_pct},
            'fx':   {'val': fx_last,   'pct': fx_pct},
            'wti':  {'val': wti_last,  'pct': wti_pct},
            'mu':   {'val': mu_last,   'pct': mu_pct},
            'skew': {'val': skew_last, 'pct': skew_pct},
            'adr_kospi':  {'val': adr_kospi_last,  'pct': adr_kospi_pct},
            'adr_kosdaq': {'val': adr_kosdaq_last, 'pct': adr_kosdaq_pct},
        },
        'charts': {
            'kospi': {
                'dates': ks_dates,
                'price': ks_vals,
                'ma50':  ks_ma50,
                'ma150': ks_ma150,
                'ma200': ks_ma200,
            },
            'sam_hyn': {
                'dates': sam_hyn_dates,
                'sam':   sam_norm,
                'hyn':   hyn_norm,
            },
            'sox_nvda': {
                'dates': sox_nvda_dates,
                'sox':   sox_norm,
                'nvda':  nvda_norm,
            },
            'vix_tnx': {
                'dates': vix_d,
                'vix':   vix_v,
                'tnx':   tnx_v,
                'tnx_dates': tnx_d,
            },
            'fx_wti': {
                'dates': fx_d,
                'fx':    fx_v,
                'wti':   wti_v,
                'wti_dates': wti_d,
            },
            'sox_mu': {
                'dates': sox_mu_dates,
                'sox':   sox_norm2,
                'mu':    mu_norm,
            },
            'adr': {
                'dates': adr_d,
                'kospi': adr_kospi,
                'kosdaq': adr_kosdaq,
            },
            'skew': {
                'dates': skew_d,
                'values': skew_v,
            },
        }
    }

    return data


def build_shell_html(build_ts):
    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>반도체 포트폴리오 대시보드</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;min-height:100vh}}
.header{{background:#1e293b;border-bottom:1px solid #334155;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.header h1{{font-size:1.1rem;font-weight:700;color:#f1f5f9}}
.updated{{font-size:.8rem;color:#94a3b8}}
.refresh-btn{{background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:.75rem;font-weight:600;cursor:pointer}}
.refresh-btn:hover{{background:#2563eb}}
.content{{padding:20px;max-width:1400px;margin:0 auto}}
.exit-panel{{margin-bottom:20px;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.exit-banner{{border-radius:0;padding:16px 20px;margin-bottom:0;border:2px solid;border-bottom:none}}
.signals-card{{background:#fff;border-radius:0;padding:16px 20px;border:none;margin-bottom:0}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px}}
.kpi-card{{background:#fff;border-radius:8px;padding:14px;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.kpi-label{{font-size:.75rem;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}}
.kpi-val{{font-size:1.1rem;font-weight:700;color:#0f172a}}
.kpi-pct{{font-size:.8rem;margin-top:2px}}
.charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.chart-card-wide{{grid-column:1/-1}}
.export-note{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:.8rem;color:#78350f;line-height:1.55}}
.export-note ul{{margin:8px 0 0 18px}}
.export-summary{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 16px;margin-top:12px;font-size:.8rem;color:#166534;line-height:1.55}}
.export-summary ul{{margin:6px 0 0 18px}}
@media(max-width:900px){{.charts-grid{{grid-template-columns:1fr}}}}
.chart-card{{background:#fff;border-radius:8px;padding:16px;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.chart-title-wrap{{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px 16px;margin-bottom:10px}}
.chart-title-wrap .chart-title{{margin-bottom:0}}
.chart-title{{font-size:.85rem;font-weight:600;color:#94a3b8;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em}}
.chart-sub{{font-weight:400;text-transform:none;letter-spacing:0;color:#64748b}}
.chart-latest{{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:.75rem;font-weight:500;text-transform:none;letter-spacing:0;color:#64748b}}
.latest-item{{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}}
.latest-dot{{width:6px;height:6px;border-radius:50%;flex-shrink:0}}
.latest-item strong{{font-weight:700}}
.chart-legend{{display:flex;gap:16px;margin-bottom:8px;flex-wrap:wrap}}
.leg{{display:flex;align-items:center;gap:5px;font-size:.75rem;color:#64748b}}
.leg-dot{{width:10px;height:10px;border-radius:50%}}
canvas{{max-height:220px}}
.signals-card h3{{font-size:.85rem;font-weight:600;color:#64748b;margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:6px 12px;font-size:.75rem;color:#64748b;border-bottom:1px solid #e2e8f0;text-transform:uppercase}}
tr:hover td{{background:#f8fafc}}
.loading-box{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;color:#64748b;gap:12px}}
.loading-box.error{{color:#dc2626}}
.loading-box .err-detail{{font-size:.8rem;color:#94a3b8;max-width:480px;text-align:center;word-break:break-all}}
.loading-box button{{margin-top:8px;padding:8px 16px;border:none;border-radius:6px;background:#3b82f6;color:#fff;font-weight:600;cursor:pointer}}
.spinner{{width:32px;height:32px;border:3px solid #e2e8f0;border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.footer{{text-align:center;padding:16px;color:#94a3b8;font-size:.75rem}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 반도체 포트폴리오 대시보드</h1>
  <div style="display:flex;align-items:center;gap:10px">
    <span class="updated" id="updated">실시간 데이터 로딩 중...</span>
    <button type="button" class="refresh-btn" onclick="loadDashboard()">↻ 새로고침</button>
  </div>
</div>
<nav style="background:#1e293b;border-bottom:2px solid #334155;display:flex;gap:0;flex-wrap:wrap">
  <a href="index.html" style="padding:12px 24px;color:#f1f5f9;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid #3b82f6">📊 매크로 대시보드</a>
  <a href="screener.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">🔍 ETF 스크리너</a>
  <a href="gap.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">📈 ETF 괴리율</a>
</nav>
<div class="content" id="app-content">
  <div class="loading-box"><div class="spinner"></div><p>시장 데이터 불러오는 중 (약 10~20초)</p></div>
</div>
<div class="footer">실시간 대시보드 · 새로고침 시 최신 데이터 반영 · 투자 권유 아님</div>
<script src="dashboard-app.js?v=20260626-export-split"></script>
</body>
</html>'''


def build_semiconductor_export_json() -> None:
    """관세청 API(nowcast_pipeline) → docs/semiconductor-export.json 스냅샷."""
    try:
        as_of = datetime.now(KST).date()
        payload = build_export_payload(as_of=as_of, use_cache=True)
        payload['_live'] = False
        EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        print(f'[OK] {EXPORT_JSON} (관세청 API, as-of {as_of})')
    except Exception as exc:
        print(f'[WARN] 수출 API 실패: {exc}')
        if EXPORT_JSON.exists():
            print(f'[SKIP] 기존 {EXPORT_JSON} 유지')
        else:
            print('[SKIP] semiconductor-export.json 미생성')


def main():
    now_kst = datetime.now(KST)
    print(f'[{now_kst.strftime("%Y-%m-%d %H:%M")} KST] 데이터 수집 중...')
    data = collect_data()
    build_ts = int(now_kst.timestamp())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_shell_html(build_ts), encoding='utf-8')
    (OUTPUT.parent / 'data.json').write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8'
    )
    print(f'[OK] Shell -> {OUTPUT}')
    print(f'[OK] Fallback data.json -> {OUTPUT.parent / "data.json"}')
    build_semiconductor_export_json()


if __name__ == '__main__':
    main()
