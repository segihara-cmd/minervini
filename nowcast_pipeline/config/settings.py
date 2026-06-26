"""nowcast_pipeline 설정 — 관세청 API·캐시 경로."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PIPELINE_ROOT.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(PIPELINE_ROOT / ".env")

DATA_DIR = PIPELINE_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

DATA_GO_KR_API_KEY: str = os.getenv("DATA_GO_KR_API_KEY", "")
CUSTOMS_TRADE_CACHE = RAW_DIR / "customs_trade_cache.json"
PARTIAL_EXPORT_OVERRIDE = RAW_DIR / "partial_semiconductor_export.json"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "1.2"))
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_WAIT_SECONDS = float(os.getenv("RETRY_WAIT_SECONDS", "2.0"))
