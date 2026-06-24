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
        print(f'  Naver API error: {e}')
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
            print(f'  [{i}/{total}] {ticker} error: {e}')
            continue

        if i % 10 == 0:
            print(f'  {i}/{total} 완료... (통과 {len(results)}개)')

    df_r = pd.DataFrame(results)
    if not df_r.empty and '6개월수익률' in df_r.columns:
        df_r = df_r.sort_values('6개월수익률', ascending=False).reset_index(drop=True)
    return df_r

# ──────────────────────────────────────────────
# HTML / JSON 출력
# ──────────────────────────────────────────────
TAB_NAV = '''<nav style="background:#1e293b;border-bottom:2px solid #334155;display:flex;gap:0;flex-wrap:wrap">
  <a href="index.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">📊 매크로 대시보드</a>
  <a href="screener.html" style="padding:12px 24px;color:#f1f5f9;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid #3b82f6">🔍 ETF 스크리너</a>
  <a href="gap.html" style="padding:12px 24px;color:#94a3b8;text-decoration:none;font-size:.9rem;font-weight:600;border-bottom:3px solid transparent">📈 ETF 괴리율</a>
</nav>'''

def df_to_api_rows(df):
    import math
    def clean(v):
        if v is None:
            return None
        try:
            if math.isnan(float(v)):
                return None
        except (TypeError, ValueError):
            pass
        return v

    rows = []
    for _, row in df.iterrows():
        rows.append({
            'ticker': row['티커'],
            'name': row['종목명'],
            'price': int(row['현재가']),
            'volume': int(row['거래량']),
            'sma50150': clean(float(row['SMA50-150']) if row['SMA50-150'] is not None else None),
            'sma150200': clean(float(row['SMA150-200']) if row['SMA150-200'] is not None else None),
            'ret3': clean(row['3개월수익률']),
            'ret6': clean(row['6개월수익률']),
            'vol6': clean(row['6개월변동성']),
            'sharpe': clean(row['샤프지수']),
            'sortino': clean(row['소르티노']),
            'rsi': clean(row['RSI']),
            'macdHist': clean(row['MACD_Hist']),
            'macdUp': row['MACD↑'] == '✅',
        })
    return rows


def build_shell_html():
    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>미너비니 ETF 스크리너</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;min-height:100vh}}
.header{{background:#1e293b;border-bottom:1px solid #334155;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.header h1{{font-size:1.05rem;font-weight:700;color:#f1f5f9}}
.updated{{font-size:.78rem;color:#94a3b8}}
.refresh-btn{{background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:.75rem;font-weight:600;cursor:pointer}}
.refresh-btn:hover{{background:#2563eb}}
.content{{padding:20px;max-width:1600px;margin:0 auto;overflow-x:auto}}
.info-bar{{background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;margin-bottom:16px;font-size:.85rem;color:#64748b;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.badge{{background:#2563eb;color:#eff6ff;padding:3px 10px;border-radius:12px;font-size:.75rem;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
thead th:hover{{background:#334155 !important;transition:.15s}}
tr:hover td{{filter:brightness(.96)}}
.loading-box{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;color:#64748b;gap:12px}}
.loading-box.error{{color:#dc2626}}
.loading-box .err-detail{{font-size:.8rem;color:#94a3b8;max-width:480px;text-align:center;word-break:break-all}}
.loading-box button{{margin-top:8px;padding:8px 16px;border:none;border-radius:6px;background:#3b82f6;color:#fff;font-weight:600;cursor:pointer}}
.spinner{{width:32px;height:32px;border:3px solid #e2e8f0;border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.footer{{text-align:center;padding:20px;color:#94a3b8;font-size:.75rem}}
</style>
</head>
<body>
<div class="header">
  <h1>🔍 미너비니 ETF 스크리너</h1>
  <div style="display:flex;align-items:center;gap:10px">
    <span class="updated" id="updated">실시간 데이터 로딩 중...</span>
    <button type="button" class="refresh-btn" onclick="loadScreener()">↻ 새로고침</button>
  </div>
</div>
{TAB_NAV}
<div class="content" id="app-content">
  <div class="loading-box"><div class="spinner"></div><p>ETF 스크리닝 중 (약 30~90초)</p></div>
</div>
<div class="footer">실시간 스크리너 · 새로고침 시 최신 데이터 반영 · 투자 권유 아님</div>
<script src="screener-app.js?v=20260624-live"></script>
</body>
</html>'''


# ──────────────────────────────────────────────
if __name__ == '__main__':
    now = datetime.now(KST)
    print(f'[{now.strftime("%Y-%m-%d %H:%M")} KST] ETF 스크리너 시작')

    etf_list = fetch_etf_list(min_volume=300_000)
    if not etf_list:
        print('ETF list empty - exit')
        exit(1)

    result_df = screen_etfs(etf_list)
    print(f'\n[OK] Minervini passed: {len(result_df)}')

    updated = now.strftime('%Y-%m-%d %H:%M KST')
    payload = {
        'updated': updated,
        'count': len(result_df),
        'rows': df_to_api_rows(result_df),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_shell_html(), encoding='utf-8')
    (OUTPUT.parent / 'screener.json').write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8'
    )
    print(f'[OK] Shell -> {OUTPUT}')
    print(f'[OK] Fallback screener.json -> {OUTPUT.parent / "screener.json"}')
