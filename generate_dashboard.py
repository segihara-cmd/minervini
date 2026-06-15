"""
generate_dashboard.py
반도체 포트폴리오 대시보드 정적 HTML 생성기
GitHub Actions에서 매일 실행 → docs/index.html 갱신
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

KST = timezone(timedelta(hours=9))
OUTPUT = Path(__file__).parent / 'docs' / 'index.html'

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
    """0-index 기준 누적 수익률(%)로 정규화"""
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
# 메인 — 데이터 수집 및 HTML 생성
# ──────────────────────────────────────────────
def main():
    now_kst = datetime.now(KST)
    print(f'[{now_kst.strftime("%Y-%m-%d %H:%M")} KST] 데이터 수집 중...')

    # Fetch
    sam_d,  sam_v  = _close_list(fetch('005930.KS', '3mo'))
    hyn_d,  hyn_v  = _close_list(fetch('000660.KS', '3mo'))
    ks11_d, ks11_v = _close_list(fetch('^KS11',     '1y'))
    sox_d,  sox_v  = _close_list(fetch('^SOX',      '3mo'))
    nvda_d, nvda_v = _close_list(fetch('NVDA',      '3mo'))
    vix_d,  vix_v  = _close_list(fetch('^VIX',      '3mo'))
    tnx_d,  tnx_v  = _close_list(fetch('^TNX',      '3mo'))
    fx_d,   fx_v   = _close_list(fetch('USDKRW=X',  '3mo'))
    wti_d,  wti_v  = _close_list(fetch('CL=F',      '3mo'))
    mu_d,   mu_v   = _close_list(fetch('MU',         '3mo'))

    # 1년치 KOSPI for MA computation
    ks11_1y_d, ks11_1y_v = _close_list(fetch('^KS11', '2y'))

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
        }
    }

    # ── HTML 생성 ─────────────────────────────
    html = build_html(data)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding='utf-8')
    print(f'✅ 생성 완료 → {OUTPUT}')


def fmt_val(v, unit='', decimals=2):
    if v is None: return 'N/A'
    return f'{v:,.{decimals}f}{unit}'

def fmt_pct(v):
    if v is None: return ''
    sign = '+' if v >= 0 else ''
    color = '#16a34a' if v >= 0 else '#dc2626'
    arrow = '▲' if v >= 0 else '▼'
    return f'<span style="color:{color}">{arrow} {sign}{v:.2f}%</span>'


def build_html(d):
    data_json = json.dumps(d, ensure_ascii=False)
    kpi = d['kpi']
    ex  = d['exit']
    el  = ex['level']

    # Exit signals table rows
    aligned_ok  = ex['aligned'] is True
    vix_ok      = (kpi['vix']['val'] or 0) <= 25
    tnx_ok      = (kpi['tnx']['val'] or 0) <= 4.5
    ma_ok       = ex['ma50'] is not None and ex['ma150'] is not None and (ex['ma50'] or 0) >= (ex['ma150'] or 0)
    ks_vs_150   = (kpi['ks11']['val'] or 0) >= (ex['ma150'] or float('inf'))
    ks_vs_200   = (kpi['ks11']['val'] or 0) >= (ex['ma200'] or float('inf'))

    def sig_row(label, ok, detail=''):
        ico  = '🟢' if ok else '🔴'
        stat = '정상' if ok else '이탈'
        col  = '#16a34a' if ok else '#dc2626'
        return f'''<tr>
          <td style="padding:6px 12px">{label}</td>
          <td style="padding:6px 12px;text-align:center">{ico}</td>
          <td style="padding:6px 12px;color:{col};font-weight:600">{stat}</td>
          <td style="padding:6px 12px;color:#64748b;font-size:.85em">{detail}</td>
        </tr>'''

    sideways_row = f'''<tr>
      <td style="padding:6px 12px">VIX 횡보 (20~30, 3주↑)</td>
      <td style="padding:6px 12px;text-align:center">{'⚠️' if ex['sideways'] else '🟢'}</td>
      <td style="padding:6px 12px;color:{'#ca8a04' if ex['sideways'] else '#16a34a'};font-weight:600">
        {'감지됨' if ex['sideways'] else '미감지'}
      </td>
      <td style="padding:6px 12px;color:#64748b;font-size:.85em">레버리지 베타 슬리피지 가속 구간</td>
    </tr>'''

    ma50_v  = fmt_val(ex['ma50'],  '', 0)
    ma150_v = fmt_val(ex['ma150'], '', 0)
    ma200_v = fmt_val(ex['ma200'], '', 0)
    ks_v    = fmt_val(kpi['ks11']['val'], '', 2)

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>반도체 포트폴리오 대시보드</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.header{{background:#1e293b;border-bottom:1px solid #334155;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:1.1rem;font-weight:700;color:#f1f5f9}}
.updated{{font-size:.8rem;color:#94a3b8}}
.content{{padding:20px;max-width:1400px;margin:0 auto}}
.exit-banner{{border-radius:10px;padding:16px 20px;margin-bottom:20px;border:2px solid}}
.exit-title{{font-size:1.2rem;font-weight:700}}
.exit-sub{{font-size:.9rem;margin-top:4px;opacity:.9}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px}}
.kpi-card{{background:#1e293b;border-radius:8px;padding:14px;border:1px solid #334155}}
.kpi-label{{font-size:.75rem;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}}
.kpi-val{{font-size:1.1rem;font-weight:700;color:#f1f5f9}}
.kpi-pct{{font-size:.8rem;margin-top:2px}}
.charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
@media(max-width:900px){{.charts-grid{{grid-template-columns:1fr}}}}
.chart-card{{background:#1e293b;border-radius:8px;padding:16px;border:1px solid #334155}}
.chart-title{{font-size:.85rem;font-weight:600;color:#94a3b8;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em}}
.chart-legend{{display:flex;gap:16px;margin-bottom:8px;flex-wrap:wrap}}
.leg{{display:flex;align-items:center;gap:5px;font-size:.75rem;color:#cbd5e1}}
.leg-dot{{width:10px;height:10px;border-radius:50%}}
canvas{{max-height:220px}}
.signals-card{{background:#1e293b;border-radius:8px;padding:16px;border:1px solid #334155;margin-bottom:20py}}
.signals-card h3{{font-size:.85rem;font-weight:600;color:#94a3b8;margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:6px 12px;font-size:.75rem;color:#64748b;border-bottom:1px solid #334155;text-transform:uppercase}}
tr:hover td{{background:#263348}}
.footer{{text-align:center;padding:16px;color:#475569;font-size:.75rem}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 반도체 포트폴리오 대시보드</h1>
  <span class="updated">업데이트: {d['updated']}</span>
</div>
<nav style="background:#1e293b;border-bottom:2px solid #334155;display:flex;gap:0">
  <a href="index.html" style="padding:12px 24px;color:#f1f5f9;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid #3b82f6">📊 매크로 대시보드</a>
  <a href="screener.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">🔍 ETF 스크리너</a>
</nav>
<div class="content">

<!-- EXIT BANNER -->
<div class="exit-banner" style="background:{ex['color']}22;border-color:{ex['color']}">
  <div class="exit-title">{ex['badge']} EXIT 레벨</div>
  <div class="exit-sub">{ex['desc']}</div>
</div>

<!-- KPI CARDS -->
<div class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-label">삼성전자</div>
    <div class="kpi-val">{fmt_val(kpi['sam']['val'],'원',0)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['sam']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">SK하이닉스</div>
    <div class="kpi-val">{fmt_val(kpi['hyn']['val'],'원',0)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['hyn']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">KOSPI</div>
    <div class="kpi-val">{fmt_val(kpi['ks11']['val'],'',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['ks11']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">SOX</div>
    <div class="kpi-val">{fmt_val(kpi['sox']['val'],'',0)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['sox']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">NVIDIA</div>
    <div class="kpi-val">${fmt_val(kpi['nvda']['val'],'',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['nvda']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">VIX</div>
    <div class="kpi-val">{fmt_val(kpi['vix']['val'],'',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['vix']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">미국 10년물</div>
    <div class="kpi-val">{fmt_val(kpi['tnx']['val'],'%',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['tnx']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">달러/원</div>
    <div class="kpi-val">{fmt_val(kpi['fx']['val'],'원',1)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['fx']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">WTI</div>
    <div class="kpi-val">${fmt_val(kpi['wti']['val'],'',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['wti']['pct'])}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Micron (DRAM)</div>
    <div class="kpi-val">${fmt_val(kpi['mu']['val'],'',2)}</div>
    <div class="kpi-pct">{fmt_pct(kpi['mu']['pct'])}</div>
  </div>
</div>

<!-- CHARTS -->
<div class="charts-grid">

  <!-- KOSPI MA -->
  <div class="chart-card">
    <div class="chart-title">KOSPI 이평선 (1년)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#f1f5f9"></span>KOSPI</span>
      <span class="leg"><span class="leg-dot" style="background:#f59e0b"></span>SMA50</span>
      <span class="leg"><span class="leg-dot" style="background:#22d3ee"></span>SMA150</span>
      <span class="leg"><span class="leg-dot" style="background:#a78bfa"></span>SMA200</span>
    </div>
    <canvas id="cKospi"></canvas>
  </div>

  <!-- 삼성 vs 하이닉스 -->
  <div class="chart-card">
    <div class="chart-title">삼성전자 vs SK하이닉스 수익률 (3개월)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#1d4ed8"></span>삼성전자</span>
      <span class="leg"><span class="leg-dot" style="background:#dc2626"></span>SK하이닉스</span>
    </div>
    <canvas id="cSamHyn"></canvas>
  </div>

  <!-- SOX vs NVDA -->
  <div class="chart-card">
    <div class="chart-title">SOX vs NVIDIA 수익률 (3개월)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#1d4ed8"></span>SOX</span>
      <span class="leg"><span class="leg-dot" style="background:#dc2626"></span>NVIDIA</span>
    </div>
    <canvas id="cSoxNvda"></canvas>
  </div>

  <!-- VIX + TNX -->
  <div class="chart-card">
    <div class="chart-title">VIX + 미국 10년물 (3개월)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#f59e0b"></span>VIX</span>
      <span class="leg"><span class="leg-dot" style="background:#22d3ee"></span>10년물(%)</span>
    </div>
    <canvas id="cVixTnx"></canvas>
  </div>

  <!-- FX + WTI -->
  <div class="chart-card">
    <div class="chart-title">달러/원 + WTI (3개월)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#1d4ed8"></span>달러/원</span>
      <span class="leg"><span class="leg-dot" style="background:#dc2626"></span>WTI ($)</span>
    </div>
    <canvas id="cFxWti"></canvas>
  </div>

  <!-- SOX vs MU (DRAM proxy) -->
  <div class="chart-card">
    <div class="chart-title">DRAM 프록시: Micron vs SOX (3개월)</div>
    <div class="chart-legend">
      <span class="leg"><span class="leg-dot" style="background:#7c3aed"></span>Micron (MU)</span>
      <span class="leg"><span class="leg-dot" style="background:#94a3b8"></span>SOX</span>
    </div>
    <canvas id="cSoxMu"></canvas>
  </div>

</div>

<!-- EXIT SIGNALS TABLE -->
<div class="signals-card">
  <h3>🚦 Exit 신호 모니터링</h3>
  <table>
    <tr>
      <th>신호</th><th style="text-align:center">상태</th><th>판정</th><th>상세</th>
    </tr>
    {sig_row('KOSPI 정배열 (curr>SMA50>150>200)', aligned_ok, f'KOSPI {ks_v} | SMA50 {ma50_v} | SMA150 {ma150_v} | SMA200 {ma200_v}')}
    {sig_row('KOSPI > SMA200', ks_vs_200, f'KOSPI {ks_v} vs SMA200 {ma200_v}')}
    {sig_row('KOSPI > SMA150', ks_vs_150, f'KOSPI {ks_v} vs SMA150 {ma150_v}')}
    {sig_row('SMA50 > SMA150', ma_ok, f'SMA50 {ma50_v} vs SMA150 {ma150_v}')}
    {sig_row('VIX ≤ 25', vix_ok, f'현재 VIX {fmt_val(kpi["vix"]["val"],"",2)}')}
    {sig_row('미국 10년물 ≤ 4.5%', tnx_ok, f'현재 {fmt_val(kpi["tnx"]["val"],"%",2)}')}
    {sideways_row}
  </table>
</div>

</div>
<div class="footer">자동 생성 대시보드 · 매 영업일 오전 9시 KST 업데이트 · 투자 권유 아님</div>

<script>
const D = {data_json};
const BLUE='#1d4ed8',RED='#dc2626',AMBER='#f59e0b',CYAN='#22d3ee',PURPLE='#7c3aed',GRAY='#94a3b8',WHITE='#f1f5f9';

const cfg = (labels, datasets, yLabel='', y2Label='') => ({{
  type: 'line',
  data: {{ labels, datasets }},
  options: {{
    responsive: true,
    maintainAspectRatio: true,
    interaction: {{ mode:'index', intersect:false }},
    plugins: {{ legend:{{display:false}}, tooltip:{{
      backgroundColor:'#1e293b',
      titleColor:'#94a3b8',
      bodyColor:'#e2e8f0',
      borderColor:'#334155',
      borderWidth:1
    }} }},
    scales: {{
      x: {{ ticks:{{ color:'#64748b', maxTicksLimit:6, font:{{size:10}} }}, grid:{{color:'#1e3a5f22'}} }},
      y: {{ ticks:{{ color:'#64748b', font:{{size:10}} }}, grid:{{color:'#1e3a5f44'}}, title:{{display:!!yLabel,text:yLabel,color:'#64748b',font:{{size:10}}}} }},
      ...(y2Label ? {{y2:{{ type:'linear', position:'right', ticks:{{color:'#64748b',font:{{size:10}} }}, grid:{{drawOnChartArea:false}}, title:{{display:true,text:y2Label,color:'#64748b',font:{{size:10}}}} }}}} : {{}} )
    }}
  }}
}});

const ds = (label, data, color, yID='y', fill=false, dash=[]) => ({{
  label, data,
  borderColor:color, backgroundColor: fill ? color+'33' : 'transparent',
  borderWidth: 2, pointRadius: 0, tension: 0.3,
  yAxisID: yID,
  ...(dash.length ? {{borderDash:dash}} : {{}})
}});

// KOSPI MA
new Chart(document.getElementById('cKospi'), cfg(
  D.charts.kospi.dates,
  [
    ds('KOSPI',  D.charts.kospi.price, WHITE, 'y', true),
    ds('SMA50',  D.charts.kospi.ma50,  AMBER, 'y'),
    ds('SMA150', D.charts.kospi.ma150, CYAN,  'y'),
    ds('SMA200', D.charts.kospi.ma200, PURPLE,'y'),
  ]
));

// Samsung vs Hynix
new Chart(document.getElementById('cSamHyn'), cfg(
  D.charts.sam_hyn.dates,
  [
    ds('삼성전자',   D.charts.sam_hyn.sam, BLUE, 'y'),
    ds('SK하이닉스', D.charts.sam_hyn.hyn, RED,  'y'),
  ], '누적수익률(%)'
));

// SOX vs NVDA
new Chart(document.getElementById('cSoxNvda'), cfg(
  D.charts.sox_nvda.dates,
  [
    ds('SOX',    D.charts.sox_nvda.sox,  BLUE, 'y'),
    ds('NVIDIA', D.charts.sox_nvda.nvda, RED,  'y'),
  ], '누적수익률(%)'
));

// VIX + TNX (dual axis)
// Align TNX to VIX dates
(function(){{
  const vDates = D.charts.vix_tnx.dates;
  const tMap   = Object.fromEntries(D.charts.vix_tnx.tnx_dates.map((d,i)=>[d,D.charts.vix_tnx.tnx[i]]));
  const tAligned = vDates.map(d => tMap[d] ?? null);
  new Chart(document.getElementById('cVixTnx'), cfg(
    vDates,
    [
      ds('VIX',     D.charts.vix_tnx.vix, AMBER, 'y'),
      ds('10년물%', tAligned,              CYAN,  'y2'),
    ], 'VIX', '금리(%)'
  ));
}})();

// FX + WTI (dual axis)
(function(){{
  const fDates  = D.charts.fx_wti.dates;
  const wMap    = Object.fromEntries(D.charts.fx_wti.wti_dates.map((d,i)=>[d,D.charts.fx_wti.wti[i]]));
  const wAligned = fDates.map(d => wMap[d] ?? null);
  new Chart(document.getElementById('cFxWti'), cfg(
    fDates,
    [
      ds('달러/원', D.charts.fx_wti.fx, BLUE, 'y'),
      ds('WTI',     wAligned,           RED,  'y2'),
    ], '달러/원', 'WTI($)'
  ));
}})();

// SOX vs MU
new Chart(document.getElementById('cSoxMu'), cfg(
  D.charts.sox_mu.dates,
  [
    ds('Micron', D.charts.sox_mu.mu,  PURPLE, 'y'),
    ds('SOX',    D.charts.sox_mu.sox, GRAY,   'y'),
  ], '누적수익률(%)'
));
</script>
</body>
</html>'''
    return html


if __name__ == '__main__':
    main()
