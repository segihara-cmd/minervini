"""
generate_screener.py
미너비니 한국 ETF 스크리너 — GitHub Actions에서 실행 → docs/screener.html 갱신
Colab 노트북 기반으로 이동평균 정배열 조건 + 보조지표 계산 후 테이블 출력
"""
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

KST = timezone(timedelta(hours=9))
OUTPUT = Path(__file__).parent / 'docs' / 'screener.html'

# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────
def normalize_code(ticker):
    t = str(ticker).strip()
    return t.zfill(6) if t.isdigit() else t

def to_yf_ticker(code):
    return f"{normalize_code(code)}.KS"

def get_price_series(df):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        for pt in ('Close', 'Adj Close'):
            cols = [c for c in df.columns if c[0] == pt]
            if cols:
                s = df[cols[0]].dropna()
                if not s.empty:
                    return s
    else:
        for col in ('Close', 'Adj Close'):
            if col in df.columns:
                s = df[col].dropna()
                if not s.empty:
                    return s
    return None

def safe_float(v, ndigits=2):
    try:
        if pd.isna(v):
            return None
        return round(float(v), ndigits)
    except Exception:
        return None

# ──────────────────────────────────────────────
# 지표 계산
# ──────────────────────────────────────────────
def calc_rsi(series, window=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calc_macd(series, s=12, l=26, sig=9):
    ema_s = series.ewm(span=s, adjust=False).mean()
    ema_l = series.ewm(span=l, adjust=False).mean()
    macd = ema_s - ema_l
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd, signal, macd - signal

def calc_returns_sharpe(series, months=6):
    daily = series.pct_change().dropna()
    lookback = int(months * 21)
    rec = daily.iloc[-lookback:] if len(daily) > lookback else daily
    if rec.empty or len(rec) < 2:
        return None, None, None
    ret = float((1 + rec).prod() - 1)
    mu  = float(rec.mean() * 252)
    std = float(rec.std() * np.sqrt(252))
    sharpe = mu / std if std > 0 else None
    vol = float(rec.std() * np.sqrt(len(rec)))
    return sharpe, ret, vol

def calc_sortino(series, months=6):
    daily = series.pct_change().dropna()
    lookback = int(months * 21)
    rec = daily.iloc[-lookback:] if len(daily) > lookback else daily
    if rec.empty:
        return None
    mu  = float(rec.mean() * 252)
    dd  = rec[rec < 0]
    if dd.empty:
        return None
    dstd = float(dd.std() * np.sqrt(252))
    return mu / dstd if dstd > 0 else None

# ──────────────────────────────────────────────
# ETF 목록 조회 (네이버 JSON API)
# ──────────────────────────────────────────────
def fetch_etf_list(min_volume=300_000):
    print(f'네이버 금융 API → 거래량 {min_volume:,} 이상 ETF 조회 중...')
    try:
        url = 'https://finance.naver.com/api/sise/etfItemList.nhn'
        r = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.naver.com/sise/etf.nhn'
        }, timeout=20)
        r.raise_for_status()
        items = r.json().get('result', {}).get('etfItemList', [])
        result = []
        for item in items:
            vol = int(item.get('quant', 0) or 0)
            if vol >= min_volume:
                result.append({
                    'code': normalize_code(item.get('itemcode', '')),
                    'name': str(item.get('itemname', '')).strip(),
                    'volume': vol,
                })
        print(f'  → {len(result)}개 수집')
        return result
    except Exception as e:
        print(f'  ❌ 네이버 API 오류: {e}')
        return []

# ──────────────────────────────────────────────
# 미너비니 스크리닝
# ──────────────────────────────────────────────
MIN_DAYS = 200

def screen_etfs(etf_list):
    results = []
    total = len(etf_list)
    print(f'총 {total}개 종목 분석 시작...')

    for i, etf in enumerate(etf_list, 1):
        ticker = to_yf_ticker(etf['code'])
        try:
            df = yf.download(ticker, period='1y', progress=False, auto_adjust=True)
            if df is None or df.empty or len(df) < MIN_DAYS:
                continue
            price = get_price_series(df)
            if price is None or len(price) < MIN_DAYS:
                continue

            curr = float(price.iloc[-1])
            ma50  = float(price.rolling(50).mean().iloc[-1])
            ma150 = float(price.rolling(150).mean().iloc[-1])
            ma200 = float(price.rolling(200).mean().iloc[-1])
            ma200_20 = float(price.rolling(200).mean().iloc[-21]) if len(price) > 220 else ma200

            # 미너비니 6개 조건
            conds = [
                curr  > ma50,
                curr  > ma150,
                curr  > ma200,
                ma50  > ma150,
                ma150 > ma200,
                ma200 > ma200_20,
            ]
            if not all(conds):
                continue

            # 보조지표
            rsi_s = calc_rsi(price)
            rsi = safe_float(rsi_s.iloc[-1])

            _, _, hist = calc_macd(price)
            macd_h = safe_float(hist.iloc[-1], 4)
            macd_prev = safe_float(hist.iloc[-2], 4) if len(hist) > 1 else None
            macd_up = (macd_h is not None and macd_prev is not None and macd_h > macd_prev)

            sharpe, ret6, vol6 = calc_returns_sharpe(price, 6)
            _, ret3, _         = calc_returns_sharpe(price, 3)
            sortino            = calc_sortino(price, 6)

            results.append({
                '티커':         ticker,
                '종목명':       etf['name'],
                '현재가':       int(curr),
                '거래량':       etf['volume'],
                'SMA50-150':   round(ma50 - ma150, 0),
                'SMA150-200':  round(ma150 - ma200, 0),
                '3개월수익률': round(ret3 * 100, 2) if ret3 is not None else None,
                '6개월수익률': round(ret6 * 100, 2) if ret6 is not None else None,
                '6개월변동성': round(vol6 * 100, 2) if vol6 is not None else None,
                '샤프지수':    round(sharpe, 2) if sharpe is not None else None,
                '소르티노':    round(sortino, 2) if sortino is not None else None,
                'RSI':         rsi,
                'MACD_Hist':   macd_h,
                'MACD↑':       '✅' if macd_up else '❌',
            })
        except Exception as e:
            print(f'  [{i}/{total}] {ticker} 오류: {e}')
            continue

        if i % 10 == 0:
            print(f'  {i}/{total} 완료... (통과 {len(results)}개)')

    df_r = pd.DataFrame(results)
    if not df_r.empty and '6개월수익률' in df_r.columns:
        df_r = df_r.sort_values('6개월수익률', ascending=False).reset_index(drop=True)
    return df_r

# ──────────────────────────────────────────────
# 색상 유틸
# ──────────────────────────────────────────────
def _lerp_color(v, lo, hi, c_lo, c_hi):
    """v를 [lo,hi] 범위로 선형 보간해 hex 색상 반환"""
    if v is None or lo == hi:
        return '#1e293b'
    t = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    def parse(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    r0, g0, b0 = parse(c_lo)
    r1, g1, b1 = parse(c_hi)
    r = int(r0 + t * (r1 - r0))
    g = int(g0 + t * (g1 - g0))
    b = int(b0 + t * (b1 - b0))
    return f'#{r:02x}{g:02x}{b:02x}'

def color_pct(v, lo=0, hi=50):
    return _lerp_color(v, lo, hi, '#fca5a5', '#86efac')

def color_neutral(v, lo=0, hi=3):
    return _lerp_color(v, lo, hi, '#fca5a5', '#86efac')

def color_rsi(v):
    # 30 파란색(매도과다), 50 중립, 70+ 빨강(과매수)
    if v is None:
        return '#1e293b'
    if v < 30:
        return '#bfdbfe'
    if v > 70:
        return '#fca5a5'
    return _lerp_color(v, 30, 70, '#bfdbfe', '#86efac')

def color_macd(v, col_vals):
    numeric = [x for x in col_vals if x is not None]
    if not numeric or v is None:
        return '#1e293b'
    lo, hi = min(numeric), max(numeric)
    return _lerp_color(v, lo, hi, '#7f1d1d', '#14532d')

def td(val, bg='#f8fafc', fmt='{}', align='right'):
    display = fmt.format(val) if val is not None else 'N/A'
    return f'<td style="padding:7px 10px;background:{bg};text-align:{align};white-space:nowrap;color:#1e293b">{display}</td>'

# ──────────────────────────────────────────────
# HTML 생성
# ──────────────────────────────────────────────
TAB_NAV = '''<nav style="background:#1e293b;border-bottom:2px solid #334155;display:flex;gap:0">
  <a href="index.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">📊 매크로 대시보드</a>
  <a href="screener.html" style="padding:12px 24px;color:#f1f5f9;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid #3b82f6">🔍 ETF 스크리너</a>
</nav>'''

def build_html(df, updated):
    if df.empty:
        rows_html = '<tr><td colspan="14" style="text-align:center;padding:40px;color:#94a3b8">조건을 만족하는 ETF가 없습니다</td></tr>'
    else:
        ret6_vals = df['6개월수익률'].tolist()
        ret3_vals = df['3개월수익률'].tolist()
        shr_vals  = df['샤프지수'].tolist()
        srt_vals  = df['소르티노'].tolist()
        mcd_vals  = df['MACD_Hist'].tolist()

        rows = []
        for idx, row in df.iterrows():
            rank = idx + 1
            cells = (
                f'<td style="padding:7px 10px;text-align:center;color:#94a3b8;background:#f8fafc">{rank}</td>'
                + f'<td style="padding:7px 10px;background:#ffffff;white-space:nowrap"><span style="color:#94a3b8;font-size:.8rem">{row["티커"]}</span><br><b style="font-size:.9rem;color:#1e293b">{row["종목명"][:18]}</b></td>'
                + td(f'{row["현재가"]:,}원', '#f8fafc', '{}', 'right')
                + td(f'{row["거래량"]:,}', '#f8fafc', '{}', 'right')
                + td(row['3개월수익률'], color_pct(row['3개월수익률'], 0, 30), '{:.1f}%')
                + td(row['6개월수익률'], color_pct(row['6개월수익률'], 0, 50), '{:.1f}%')
                + td(row['6개월변동성'], '#f8fafc', '{:.1f}%')
                + td(row['샤프지수'],  color_neutral(row['샤프지수'], 0, 3),  '{:.2f}')
                + td(row['소르티노'],  color_neutral(row['소르티노'], 0, 3),   '{:.2f}')
                + td(row['SMA50-150'],  '#f8fafc', '{:,.0f}')
                + td(row['SMA150-200'], '#f8fafc', '{:,.0f}')
                + td(row['RSI'],        color_rsi(row['RSI']), '{:.1f}')
                + td(row['MACD_Hist'],  color_macd(row['MACD_Hist'], mcd_vals), '{:.4f}')
                + f'<td style="padding:7px 10px;background:#f8fafc;text-align:center">{row["MACD↑"]}</td>'
            )
            rows.append(f'<tr>{"".join(cells)}</tr>')
        rows_html = '\n'.join(rows)

    count = len(df)
    cols = ['#', '종목명', '현재가', '거래량', '3개월<br>수익률', '6개월<br>수익률',
            '6개월<br>변동성', '샤프지수', '소르티노', 'SMA50<br>-150', 'SMA150<br>-200',
            'RSI', 'MACD<br>Hist', 'MACD↑']
    th_row = ''.join(f'<th onclick="sortTable({i})" data-col="{i}" style="padding:8px 10px;text-align:right;background:#1e293b;color:#94a3b8;font-size:.75rem;border-bottom:2px solid #334155;white-space:nowrap;cursor:pointer;user-select:none">{c} <span style="opacity:.5;font-size:.7rem">⇅</span></th>' for i,c in enumerate(cols))

    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>미너비니 ETF 스크리너</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;min-height:100vh}}
.header{{background:#1e293b;border-bottom:1px solid #334155;padding:14px 24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:1.05rem;font-weight:700;color:#f1f5f9}}
.updated{{font-size:.78rem;color:#94a3b8}}
.content{{padding:20px;max-width:1600px;margin:0 auto;overflow-x:auto}}
.info-bar{{background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;margin-bottom:16px;font-size:.85rem;color:#64748b;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.badge{{background:#2563eb;color:#eff6ff;padding:3px 10px;border-radius:12px;font-size:.75rem;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
thead th:hover{{background:#334155 !important;transition:.15s}}
tr:hover td{{filter:brightness(.96)}}
.footer{{text-align:center;padding:20px;color:#94a3b8;font-size:.75rem}}
</style>
</head>
<body>
<div class="header">
  <h1>🔍 미너비니 ETF 스크리너</h1>
  <span class="updated">업데이트: {updated}</span>
</div>
{TAB_NAV}
<div class="content">
<div class="info-bar">
  <div>
    <span class="badge">{count}개 통과</span>
    &nbsp; 미너비니 6조건: 주가 &gt; SMA50/150/200 · SMA 정배열 · SMA200 상승추세
    &nbsp;|&nbsp; 기준 거래량: 30만주 이상
  </div>
  <div style="color:#475569;font-size:.78rem">6개월 수익률 높은 순</div>
</div>
<table>
<thead><tr>{th_row}</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
<div class="footer">데이터: yfinance · 네이버금융 | 투자 판단은 본인 책임입니다</div>
</body>
</html>'''


# ──────────────────────────────────────────────
if __name__ == '__main__':
    now = datetime.now(KST)
    print(f'[{now.strftime("%Y-%m-%d %H:%M")} KST] ETF 스크리너 시작')

    etf_list = fetch_etf_list(min_volume=300_000)
    if not etf_list:
        print('❌ ETF 목록 없음 — 종료')
        exit(1)

    result_df = screen_etfs(etf_list)
    print(f'\n✅ 미너비니 조건 통과: {len(result_df)}개')

    updated = now.strftime('%Y-%m-%d %H:%M KST')
    html = build_html(result_df, updated)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding='utf-8')
    print(f'✅ 생성 완료 → {OUTPUT}')
