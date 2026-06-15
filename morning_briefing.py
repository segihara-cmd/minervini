"""
포트폴리오 모닝 브리핑
보유 종목: TIGER 삼성전자단일종목레버리지(0195R0), TIGER SK하이닉스단일종목레버리지(0195S0)
매일 오전 9시 텔레그램 발송
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd
import numpy as np

# ──────────────────────────────────────────────
# .env 로드 (여러 경로 시도)
# ──────────────────────────────────────────────
def load_env(*paths):
    for p in paths:
        p = Path(p)
        if p.exists():
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip())
            return str(p)
    return None

_here = Path(__file__).parent
load_env(
    _here / '.env',
    _here.parent / 'fPER Research' / 'project' / '.env',
    _here.parent / 'fPER_Research' / 'project' / '.env',
)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID        = os.getenv('TELEGRAM_CHAT_ID', '')
KST = ZoneInfo('Asia/Seoul')

# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print('[경고] 텔레그램 환경변수 없음')
        return False
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        r = requests.post(url, json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, timeout=15)
        ok = r.json().get('ok', False)
        if not ok:
            print(f'[오류] 텔레그램: {r.text}')
        return ok
    except Exception as e:
        print(f'[오류] 텔레그램 전송: {e}')
        return False

def _close_series(df, ticker=''):
    """yfinance DataFrame에서 종가 Series 추출 (MultiIndex 대응)"""
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

def fetch(ticker: str, period: str = '5d'):
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        return df if not df.empty else None
    except Exception as e:
        print(f'[경고] {ticker} 조회 실패: {e}')
        return None

def latest_two(df, ticker=''):
    s = _close_series(df, ticker)
    if s is None or len(s) < 2:
        return None, None
    return float(s.iloc[-1]), float(s.iloc[-2])

def pct(cur, prev):
    try:
        return (cur - prev) / prev * 100
    except:
        return None

def fp(v, d=2):
    """float → 문자열 (소수 d자리)"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    return f'{v:,.{d}f}'

def fpct(v):
    if v is None:
        return 'N/A'
    sign = '+' if v >= 0 else ''
    return f'{sign}{v:.2f}%'

def arrow(v):
    if v is None:
        return ''
    return '▲' if v >= 0 else '▼'

# ──────────────────────────────────────────────
# 핵심 로직
# ──────────────────────────────────────────────
def kospi_ma_check(df):
    """KOSPI 50/150/200 이평선 정배열 체크"""
    s = _close_series(df)
    if s is None or len(s) < 210:
        return None, None, None, None
    sma50     = float(s.rolling(50).mean().iloc[-1])
    sma150    = float(s.rolling(150).mean().iloc[-1])
    sma200    = float(s.rolling(200).mean().iloc[-1])
    sma200_20 = float(s.rolling(200).mean().iloc[-21])
    curr      = float(s.iloc[-1])
    aligned   = (curr > sma50 > sma150 > sma200 and sma200 > sma200_20)
    return sma50, sma150, sma200, aligned

def compute_exit_level(kospi_cur, sma50, sma150, sma200, aligned, vix_cur, tnx_cur):
    """Exit Level 0~4 계산"""
    if kospi_cur is None or sma200 is None:
        return None
    if kospi_cur < sma200:
        return 4
    if kospi_cur < sma150:
        return 3
    if sma50 is not None and sma150 is not None and sma50 < sma150:
        return 2
    if vix_cur is not None and vix_cur > 30:
        return 2
    if aligned is False:
        return 1
    if vix_cur is not None and vix_cur > 25:
        return 1
    if tnx_cur is not None and tnx_cur > 4.5:
        return 1
    return 0

def vix_sideways_check(vix_df):
    """최근 15 거래일 VIX 가 모두 20~30 사이인지 확인 (레버리지 decay 위험)"""
    s = _close_series(vix_df)
    if s is None or len(s) < 15:
        return False
    last15 = s.iloc[-15:].values
    return bool((last15 >= 20).all() and (last15 <= 30).all())

def leverage_decay_warning(sam_pct, hyn_pct):
    """레버리지 ETF 주의 메시지"""
    msgs = []
    for name, p in [('삼성전자', sam_pct), ('SK하이닉스', hyn_pct)]:
        if p is not None and abs(p) >= 3:
            msgs.append(f'  {name} 2x ETF: 기초 {fpct(p)} → 이론치 {fpct(p*2)} (실제는 decay 차감)')
    return msgs

EXIT_LEVEL_META = {
    0: ('🟢 L0 — 정상', '전종목 정상 보유'),
    1: ('🟡 L1 — 경계', '신규 매수 자제'),
    2: ('🟠 L2 — 경고', '30% 비중 축소 권고'),
    3: ('🔴 L3 — 위험', '추가 50% 축소 권고'),
    4: ('🚨 L4 — 전량청산', '즉시 전량 매도'),
}

# ──────────────────────────────────────────────
# 메시지 구성
# ──────────────────────────────────────────────
def build_message() -> str:
    now = datetime.now(KST)

    # ── 데이터 수집 ────────────────────────────
    sam_df   = fetch('005930.KS', '5d')
    hyn_df   = fetch('000660.KS', '5d')
    kospi_df = fetch('^KS11', '2y')
    sox_df   = fetch('^SOX', '5d')
    nvda_df  = fetch('NVDA', '5d')
    tnx_df   = fetch('^TNX', '5d')
    wti_df   = fetch('CL=F', '5d')
    fx_df    = fetch('USDKRW=X', '5d')
    vix_df   = fetch('^VIX', '1mo')   # 1mo for sideways check (15 거래일)
    mu_df    = fetch('MU', '5d')       # DRAM proxy

    sam_cur,  sam_prev   = latest_two(sam_df,  '005930.KS')
    hyn_cur,  hyn_prev   = latest_two(hyn_df,  '000660.KS')
    kospi_cur, kospi_prev = latest_two(kospi_df)
    sox_cur,  sox_prev   = latest_two(sox_df)
    nvda_cur, nvda_prev  = latest_two(nvda_df)
    tnx_cur,  tnx_prev   = latest_two(tnx_df)
    wti_cur,  wti_prev   = latest_two(wti_df)
    fx_cur,   fx_prev    = latest_two(fx_df)
    vix_cur,  vix_prev   = latest_two(vix_df)
    mu_cur,   mu_prev    = latest_two(mu_df)

    sam_p   = pct(sam_cur,   sam_prev)
    hyn_p   = pct(hyn_cur,   hyn_prev)
    kospi_p = pct(kospi_cur, kospi_prev)
    sox_p   = pct(sox_cur,   sox_prev)
    nvda_p  = pct(nvda_cur,  nvda_prev)
    tnx_p   = pct(tnx_cur,   tnx_prev)
    wti_p   = pct(wti_cur,   wti_prev)
    fx_p    = pct(fx_cur,    fx_prev)
    vix_p   = pct(vix_cur,   vix_prev)
    mu_p    = pct(mu_cur,    mu_prev)

    sma50, sma150, sma200, aligned = kospi_ma_check(kospi_df)
    exit_level = compute_exit_level(kospi_cur, sma50, sma150, sma200, aligned, vix_cur, tnx_cur)
    sideways   = vix_sideways_check(vix_df)

    # ── EXIT 레벨 배지 ────────────────────────
    el_badge, el_desc = EXIT_LEVEL_META.get(exit_level, ('⚪ 데이터 부족', ''))
    lines = [
        f'📊 <b>모닝 브리핑</b>  {now.strftime("%Y-%m-%d %H:%M")} KST',
        f'',
        f'<b>EXIT 레벨: {el_badge}</b>',
        f'  {el_desc}',
        f'',
    ]

    # 1. 보유 기초자산 ─────────────────────────
    lines.append('🇰🇷 <b>보유 기초자산 (전일 종가)</b>')
    lines.append(f'  삼성전자:   {fp(sam_cur, 0)}원  {arrow(sam_p)} {fpct(sam_p)}')
    lines.append(f'  SK하이닉스: {fp(hyn_cur, 0)}원  {arrow(hyn_p)} {fpct(hyn_p)}')
    lines.append(f'  ※ 보유 ETF = 각 2x 레버리지 (이론 수익률 약 2배)')

    # 2. KOSPI 이평선 ─────────────────────────
    lines.append(f'\n📈 <b>KOSPI 이평선</b>')
    lines.append(f'  지수: {fp(kospi_cur, 2)}  {arrow(kospi_p)} {fpct(kospi_p)}')
    if sma50:
        status = '✅ 정배열' if aligned else '⚠️ 정배열 깨짐'
        lines.append(f'  SMA50 {fp(sma50, 0)} | SMA150 {fp(sma150, 0)} | SMA200 {fp(sma200, 0)}')
        lines.append(f'  {status}')
    else:
        lines.append('  이평선 데이터 부족')

    # 3. 글로벌 반도체 ─────────────────────────
    lines.append('\n🌐 <b>글로벌 반도체</b>')
    lines.append(f'  SOX: {fp(sox_cur, 0)}  {arrow(sox_p)} {fpct(sox_p)}')
    lines.append(f'  NVIDIA: ${fp(nvda_cur, 2)}  {arrow(nvda_p)} {fpct(nvda_p)}')
    lines.append(f'  Micron (DRAM proxy): ${fp(mu_cur, 2)}  {arrow(mu_p)} {fpct(mu_p)}')

    # 4. 매크로 ────────────────────────────────
    lines.append('\n📉 <b>매크로</b>')
    if vix_cur:
        if vix_cur > 30:
            vix_lbl = '🔴 극도 공포'
        elif vix_cur > 20:
            vix_lbl = '🟡 경계'
        else:
            vix_lbl = '🟢 안정'
        lines.append(f'  VIX: {fp(vix_cur, 2)}  {arrow(vix_p)} {fpct(vix_p)}  {vix_lbl}')
    lines.append(f'  미국 10년물: {fp(tnx_cur, 2)}%  {arrow(tnx_p)} {fpct(tnx_p)}')
    lines.append(f'  달러/원: {fp(fx_cur, 1)}원  {arrow(fx_p)} {fpct(fx_p)}')
    lines.append(f'  WTI: ${fp(wti_cur, 2)}  {arrow(wti_p)} {fpct(wti_p)}')

    # 5. 경보 ──────────────────────────────────
    alerts = []

    if exit_level is not None and exit_level >= 2:
        alerts.append(f'🚨 Exit L{exit_level} — {el_desc}')

    if sideways:
        alerts.append('⚠️ VIX 20~30 횡보 3주↑ — 레버리지 베타 슬리피지 가속 주의')

    if vix_cur and vix_cur > 25:
        alerts.append(f'⚠️ VIX {fp(vix_cur, 1)} — 변동성 극심, 레버리지 decay 가속')

    if tnx_cur and tnx_cur > 4.5:
        alerts.append(f'⚠️ 금리 {fp(tnx_cur, 2)}% — 성장주/반도체 밸류에이션 압박')

    decay_warns = leverage_decay_warning(sam_p, hyn_p)
    if decay_warns:
        alerts.append('📌 <b>레버리지 2x — 당일 기초자산 변동</b>')
        alerts.extend(decay_warns)

    if alerts:
        lines.append('\n' + '\n'.join(alerts))

    # 6. 체크포인트 ────────────────────────────
    lines.append('\n🔍 <b>체크포인트</b>')
    lines.append('  · 삼성전자/하이닉스 애널리스트 리포트')
    lines.append('  · AI 빅테크 CAPEX 뉴스')
    lines.append('  · 미중 반도체 수출 규제 뉴스')
    lines.append('  · DRAM 현물가 변동 여부')

    return '\n'.join(lines)


# ──────────────────────────────────────────────
if __name__ == '__main__':
    print('브리핑 생성 중...')
    msg = build_message()
    print(msg)
    print('\n텔레그램 전송 중...')
    ok = send_telegram(msg)
    print('✅ 전송 완료' if ok else '❌ 전송 실패')
