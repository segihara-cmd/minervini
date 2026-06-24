"""
OpenDART(금융감독원 전자공시) API 수집기.

공시 메타데이터·기업개황 등을 수집합니다.
Forward PER/PBR은 DART에 직접 없으므로, 종목명·corp_code 매핑 보강용으로 사용합니다.

API 문서: https://opendart.fss.or.kr/guide/main.do
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

import pandas as pd
import requests

from collectors.base import BaseCollector
from config.settings import DART_API_KEY, REQUEST_TIMEOUT
from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"


class DartCollector(BaseCollector):
    """OpenDART REST API 클라이언트."""

    source_name = "dart"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or DART_API_KEY
        self._corp_code_map: dict[str, str] | None = None  # ticker(6) -> corp_code

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "DART_API_KEY가 설정되지 않았습니다. "
                ".env 파일에 DART_API_KEY=발급키 를 추가하세요."
            )

    @retry_on_failure()
    def _request(self, endpoint: str, params: dict[str, Any]) -> dict:
        self._require_key()
        params = {**params, "crtfc_key": self.api_key}
        url = f"{DART_BASE}/{endpoint}"
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            raise RuntimeError(
                f"DART API 오류: status={data.get('status')} "
                f"message={data.get('message')}"
            )
        return data

    @retry_on_failure()
    def load_corp_codes(self) -> pd.DataFrame:
        """
        고유번호(corp_code) 목록 다운로드 및 파싱.

        Returns
        -------
        pd.DataFrame
            corp_code, corp_name, stock_code, modify_date
        """
        self._require_key()
        url = f"{DART_BASE}/corpCode.xml"
        resp = requests.get(
            url, params={"crtfc_key": self.api_key}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()

        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_bytes = zf.read(xml_name)

        root = ElementTree.fromstring(xml_bytes)
        rows = []
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            if not stock_code or stock_code == " ":
                continue
            rows.append(
                {
                    "corp_code": item.findtext("corp_code"),
                    "corp_name": item.findtext("corp_name"),
                    "stock_code": stock_code.zfill(6),
                    "modify_date": item.findtext("modify_date"),
                }
            )
        return pd.DataFrame(rows)

    def get_corp_code(self, ticker: str) -> str | None:
        """6자리 종목코드 → DART corp_code."""
        code = self.normalize_ticker(ticker)
        if self._corp_code_map is None:
            df = self.load_corp_codes()
            self._corp_code_map = dict(zip(df["stock_code"], df["corp_code"]))
        return self._corp_code_map.get(code)

    def fetch_company_overview(self, ticker: str) -> dict[str, Any]:
        """기업개황(company.json) 조회."""
        corp_code = self.get_corp_code(ticker)
        if not corp_code:
            return {"ticker": self.normalize_ticker(ticker), "corp_name": ""}

        data = self._request(
            "company.json",
            {"corp_code": corp_code},
        )
        return {
            "ticker": self.normalize_ticker(ticker),
            "corp_code": corp_code,
            "corp_name": data.get("corp_name", ""),
            "industry_code": data.get("induty_code", ""),
            "ceo_name": data.get("ceo_nm", ""),
        }

    def collect(
        self,
        tickers: list[str] | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """
        종목별 DART 기업개황 수집.

        API 키가 없으면 빈 DataFrame과 경고 로그를 반환합니다.
        """
        from config.settings import DEFAULT_TICKERS

        if not self.api_key:
            logger.warning("DART_API_KEY 미설정 — DART 수집을 건너뜁니다.")
            return pd.DataFrame(columns=["ticker", "corp_code", "corp_name"])

        targets = tickers or DEFAULT_TICKERS
        rows = []
        for raw in targets:
            try:
                rows.append(self.fetch_company_overview(raw))
            except Exception as exc:
                logger.error("DART %s 수집 실패: %s", raw, exc)

        return pd.DataFrame(rows)

    def get_stock_name_map(self) -> dict[str, str]:
        """
        상장사 전체 ticker(6) → corp_name 매핑.

        OpenDART corpCode.xml 1회 다운로드로 전 종목명을 보강합니다.
        """
        if not self.api_key:
            return {}
        try:
            df = self.load_corp_codes()
            return dict(zip(df["stock_code"], df["corp_name"]))
        except Exception as exc:
            logger.warning("DART 종목명 맵 로드 실패: %s", exc)
            return {}
