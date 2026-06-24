"""
(레거시) 키워드 기반 산업 정의 — 현재는 collectors/naver_industry_collector.py 사용.

업종 분류는 네이버 금융 업종(79개)을 따릅니다:
https://stock.naver.com/market/stock/kr/industry/1
"""

from __future__ import annotations

import re
from typing import TypedDict


class SectorDef(TypedDict):
    name: str
    patterns: list[str]
    seeds: list[str]
    exclude: list[str]


DEFAULT_EXCLUDE = [
    r"SPAC",
    r"스팩",
    r"리츠",
    r"우B$",
    r"우$",
    r"홀딩스",
    r"지주$",
]

# 이전 13개 대분류 — 참고용 보존
INDUSTRY_SECTORS: list[SectorDef] = []


def _matches(name: str, patterns: list[str], exclude: list[str]) -> bool:
    if any(re.search(p, name) for p in exclude):
        return False
    return any(re.search(p, name) for p in patterns)
