"""
애플리케이션 설정 모듈.

환경 변수(.env)를 로드하고 데이터 경로, API 키 등을 중앙에서 관리합니다.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트: project/ 디렉터리
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# .env 파일 로드 (없어도 동작은 하되, DART API는 키 필요)
load_dotenv(PROJECT_ROOT / ".env")

# --- 경로 ---
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# DB 파일 경로 (SQLite 기본; DuckDB로 교체 시 확장 가능)
DB_PATH = Path(os.getenv("DB_PATH", str(PROCESSED_DIR / "research.db")))

# --- API ---
DART_API_KEY: str = os.getenv("DART_API_KEY", "")

# --- HTTP / 크롤링 ---
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 재시도 설정
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_WAIT_SECONDS = float(os.getenv("RETRY_WAIT_SECONDS", "2.0"))

# --- 기본 수집 대상 (예시) ---
# KOSPI/KOSDAQ 티커 6자리; Yahoo에서는 .KS / .KQ 접미사 사용
DEFAULT_TICKERS: list[str] = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
]

# 네이버 금융 리서치 목록 최대 페이지 (0 = 전체, 맨끝 페이지까지)
NAVER_RESEARCH_MAX_PAGES = int(os.getenv("NAVER_RESEARCH_MAX_PAGES", "0"))

# HTTP 요청 최소 간격(초) — 전체 수집 시 차단 방지
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "1.2"))

# Yahoo Finance 배치 크기
YAHOO_BATCH_SIZE = int(os.getenv("YAHOO_BATCH_SIZE", "80"))

# 목표주가 상세(nid) 동시 요청 수
NAVER_DETAIL_WORKERS = int(os.getenv("NAVER_DETAIL_WORKERS", "4"))

# PER/PBR 일괄 조회 시 종목당 딜레이(초); 0이면 기본 REQUEST_DELAY 사용
VALUATION_DELAY_SECONDS = float(os.getenv("VALUATION_DELAY_SECONDS", "0.25"))

# PER/PBR 조회 최대 종목 수 (0 = 제한 없음, 리포트에 등장한 전 종목)
VALUATION_MAX_TICKERS = int(os.getenv("VALUATION_MAX_TICKERS", "0"))

# 전체 수집 기본 모드 (true면 --tickers 없을 때 전체 리포트 스캔)
FULL_SCAN_DEFAULT = os.getenv("FULL_SCAN_DEFAULT", "true").lower() in (
    "1",
    "true",
    "yes",
)

# 체크포인트 파일
NAVER_LIST_CHECKPOINT = RAW_DIR / "naver_list_checkpoint.json"
NAVER_LIST_CACHE_CSV = RAW_DIR / "naver_reports_list.csv"
NAVER_DETAIL_CACHE_CSV = RAW_DIR / "naver_details_cache.csv"

# Investing.com KRX 전 종목 스캔
INVESTING_UNIVERSE_MONTHS = int(os.getenv("INVESTING_UNIVERSE_MONTHS", "3"))
INVESTING_UNIVERSE_MAX = int(os.getenv("INVESTING_UNIVERSE_MAX", "0"))  # 0=전체
INVESTING_UNIVERSE_CSV = RAW_DIR / "investing_universe_reports.csv"
INVESTING_UNIVERSE_CHECKPOINT = RAW_DIR / "investing_universe_checkpoint.json"
# true면 main 실행 시 네이버 대신/추가로 Investing 유니버스 스캔
INVESTING_UNIVERSE_SCAN = os.getenv("INVESTING_UNIVERSE_SCAN", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Investing.com 전용 — 403 차단 완화
INVESTING_REQUEST_DELAY_SECONDS = float(
    os.getenv("INVESTING_REQUEST_DELAY_SECONDS", "3.0")
)
INVESTING_BATCH_EVERY = int(os.getenv("INVESTING_BATCH_EVERY", "15"))
INVESTING_BATCH_REST_SECONDS = float(os.getenv("INVESTING_BATCH_REST_SECONDS", "120"))
INVESTING_403_BACKOFF_BASE = float(os.getenv("INVESTING_403_BACKOFF_BASE", "45"))
INVESTING_403_MAX_RETRIES = int(os.getenv("INVESTING_403_MAX_RETRIES", "2"))
INVESTING_403_TICKER_REST = float(os.getenv("INVESTING_403_TICKER_REST", "90"))
INVESTING_403_COOLDOWN_AFTER = int(os.getenv("INVESTING_403_COOLDOWN_AFTER", "3"))
INVESTING_403_COOLDOWN_SECONDS = float(
    os.getenv("INVESTING_403_COOLDOWN_SECONDS", "300")
)

# 산업 대표주 분석
SECTOR_LEADERS_PER_SECTOR = int(os.getenv("SECTOR_LEADERS_PER_SECTOR", "5"))
SECTOR_ANALYSIS_MONTHS = int(os.getenv("SECTOR_ANALYSIS_MONTHS", "6"))
SECTOR_LEADERS_CSV = PROCESSED_DIR / "sector_leaders.csv"
SECTOR_TARGET_SUMMARY_CSV = PROCESSED_DIR / "sector_target_summary.csv"
SECTOR_FIRM_DETAIL_CSV = PROCESSED_DIR / "sector_firm_detail.csv"
SECTOR_ETF_METRICS_CSV = PROCESSED_DIR / "sector_etf_metrics.csv"
ETF_HOLDINGS_CACHE = RAW_DIR / "etf_holdings_cache.json"
SECTOR_TOP_GAP_COUNT = int(os.getenv("SECTOR_TOP_GAP_COUNT", "10"))

# Telegram 일일 리포트
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOP_GAP_COUNT = int(os.getenv("TELEGRAM_TOP_GAP_COUNT", "30"))
TELEGRAM_DAILY_HOUR = int(os.getenv("TELEGRAM_DAILY_HOUR", "7"))
