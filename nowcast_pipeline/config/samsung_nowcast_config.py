"""

삼성전자 DS 매출 나우캐스팅 설정.



모든 DS 매출 숫자는 삼성전자 IR 분기 실적발표 자료에서 대조한 값만 사용합니다.

수출 USD는 관세청 API(또는 캐시)에서 런타임에 조회 — config에 하드코딩하지 않음.



분기합 vs 연간 공시 자동 검증:

  pipeline.disclosed_data_validation.validate_disclosed_ds_annual()

  (별칭: validate_annual_consistency)

"""



from __future__ import annotations



from dataclasses import dataclass



DEFAULT_HS_CODE = "8542"

MEMORY_HS_CODE = "854232"



# 관세청 API 기본: 주요 15개국 (일일 트래픽 절약). --full-countries 시 확장 목록 사용

CORE_EXPORT_COUNTRIES: tuple[str, ...] = (

    "US", "CN", "VN", "TW", "JP", "HK", "EU", "SG", "MY", "IN",

    "DE", "TH", "PH", "AU", "MX",

)



MAJOR_EXPORT_COUNTRIES: tuple[str, ...] = (

    *CORE_EXPORT_COUNTRIES,

    "GB", "ID", "CA", "RU", "AE", "FR", "IT", "NL", "ES", "PL",

    "CZ", "HU", "BR", "IL", "SA", "PK", "BD", "MM", "KH", "LA",

    "NZ", "CH", "SE", "NO", "DK", "FI", "BE", "AT", "IE", "PT",

    "TR", "ZA", "CL", "KW", "QA",

)



# DS부문 매출 vs 관세청 HS8542 수출 추세 Pearson 상관 (생산거점별 9년, 기사 확인)

# 의미: 추세 동행. 수출액 x 배수 = 매출 아님.

DS_EXPORT_TREND_CORRELATION = 0.962



# 분기 합계 vs 연간 공시 검증 허용 오차 (validate_disclosed_ds_annual 에서 사용)

ANNUAL_DS_VALIDATION_TOLERANCE_PCT = 5.0



# IR 출처 URL HEAD 검증 시 타임아웃(초) — disclosed_data_validation.check_disclosed_source_urls

SOURCE_URL_CHECK_TIMEOUT_SEC = 15.0





@dataclass(frozen=True)

class DisclosedDsQuarter:

    """삼성전자 IR 공시 DS(Device Solutions) 분기 매출."""



    quarter: str

    ds_revenue_krw: float

    source: str





# 삼성전자 IR 경영설명회 PDF (ASCII 경로, 인코딩 이슈 없음)

# 연간 합계: 2024=111.1조, 2025=130.1조

DISCLOSED_DS_QUARTERS: tuple[DisclosedDsQuarter, ...] = (

    DisclosedDsQuarter(

        "2024Q1", 23.14e12,

        "https://images.samsung.com/kdp/ir/events/2024/2024_1Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2024Q2", 28.56e12,

        "https://images.samsung.com/kdp/ir/events/2024/2024_2Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2024Q3", 29.27e12,

        "https://images.samsung.com/kdp/ir/events/2024/2024_3Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2024Q4", 30.10e12,

        "https://images.samsung.com/kdp/ir/events/2024/2024_4Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2025Q1", 25.10e12,

        "https://images.samsung.com/kdp/ir/events/2025/2025_1Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2025Q2", 27.90e12,

        "https://images.samsung.com/kdp/ir/events/2025/2025_2Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2025Q3", 33.10e12,

        "https://images.samsung.com/kdp/ir/events/2025/2025_3Q_conference_kor.pdf",

    ),

    DisclosedDsQuarter(

        "2025Q4", 44.00e12,

        "https://images.samsung.com/kdp/ir/events/2025/2025_4Q_conference_kor.pdf",

    ),

)



DISCLOSED_DS_ANNUAL_KRW: dict[int, tuple[float, str]] = {

    2024: (

        111.1e12,

        "https://images.samsung.com/kdp/ir/events/2024/2024_4Q_conference_kor.pdf",

    ),

    2025: (

        130.1e12,

        "https://images.samsung.com/kdp/ir/events/2025/2025_4Q_conference_kor.pdf",

    ),

}



# 분기별 평균 환율 (OLS 원화환산용) — OECD/FRED 분기 평균 (CCUSMA02KRQ618N) 또는 ECOS 근사

# 2026Q2~Q4: 분기 미종료 시 근사치 (config 갱신 필요)

QUARTERLY_FX_KRW_USD: dict[str, float] = {

    "2024Q1": 1325.0,

    "2024Q2": 1370.0,

    "2024Q3": 1355.0,

    "2024Q4": 1380.0,

    "2025Q1": 1450.9,

    "2025Q2": 1401.6,

    "2025Q3": 1386.2,

    "2025Q4": 1448.4,

    "2026Q1": 1465.7,  # ECOS 1~3월 평균 근사

    "2026Q2": 1410.0,  # 4~6월 진행 중 근사 (갱신 권장)

    "2026Q3": 1380.0,  # 미래 분기 추정

    "2026Q4": 1380.0,  # 미래 분기 추정

}



DISCLOSED_DS_REVENUE_KRW: dict[str, float] = {

    q.quarter: q.ds_revenue_krw for q in DISCLOSED_DS_QUARTERS

}



# --- 주가·목표주가 (검증된 증권사 목표가만) ---

SAMSUNG_TICKER = "005930"



BROKER_TARGET_PRICES: dict[str, float] = {

    "Nomura": 670_000,

}



# DS부문 단독 컨센서스 — 무료 공개 데이터 없음. CLI --consensus-ds 로만 입력.

# 빈 dict 유지: 미입력 시 컨센서스 비교 생략.

CONSENSUS_DS_REVENUE_KRW: dict[str, float] = {}


