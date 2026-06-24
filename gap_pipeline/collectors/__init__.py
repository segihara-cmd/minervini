"""
데이터 수집기(collector) 패키지.

각 소스(Yahoo, 네이버, DART)는 독립 모듈로 분리되어 있으며,
향후 ETF holdings 수집기를 동일 패턴으로 추가할 수 있습니다.
"""

from collectors.base import BaseCollector
from collectors.yahoo_collector import YahooCollector
from collectors.naver_finance_collector import NaverFinanceCollector
from collectors.dart_collector import DartCollector
from collectors.investing_collector import InvestingCollector
from collectors.yahoo_global_collector import YahooGlobalCollector
from collectors.market_universe import load_krx_universe

__all__ = [
    "BaseCollector",
    "YahooCollector",
    "NaverFinanceCollector",
    "DartCollector",
    "InvestingCollector",
    "YahooGlobalCollector",
    "load_krx_universe",
]
