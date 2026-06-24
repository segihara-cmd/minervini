-- 한국 주식 리서치 파이프라인 — DB 스키마 (참고용 DDL)
-- 실제 초기화는 db_manager.py의 SCHEMA_SQL에서 수행합니다.

CREATE TABLE IF NOT EXISTS stocks (
    ticker              TEXT NOT NULL,
    stock_name          TEXT,
    current_price       REAL,
    fper                REAL,
    fpbr                REAL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (ticker)
);

CREATE TABLE IF NOT EXISTS analyst_reports (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                  TEXT NOT NULL,
    securities_company      TEXT NOT NULL,
    report_date             TEXT,
    target_price            REAL,
    previous_target_price   REAL,
    target_revision_pct     REAL,
    opinion                 TEXT,
    created_at              TEXT NOT NULL,
    UNIQUE (ticker, securities_company, report_date, target_price)
);

CREATE INDEX IF NOT EXISTS idx_reports_ticker ON analyst_reports (ticker);
CREATE INDEX IF NOT EXISTS idx_reports_date ON analyst_reports (report_date);
