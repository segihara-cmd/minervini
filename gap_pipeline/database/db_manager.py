"""
SQLite 기반 데이터베이스 관리자.

테이블:
- stocks: 종목 시세·밸류에이션 스냅샷
- analyst_reports: 증권사 리포트 히스토리

DuckDB로 전환 시 connect() 부분만 교체하면 됩니다.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable

import pandas as pd

from config.settings import DB_PATH, PROCESSED_DIR

logger = logging.getLogger(__name__)

# --- 스키마 DDL ---
SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_reports_ticker
    ON analyst_reports (ticker);

CREATE INDEX IF NOT EXISTS idx_reports_date
    ON analyst_reports (report_date);
"""


class DatabaseManager:
    """리서치 데이터 영속화 계층."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """기존 DB에 컬럼·인덱스 추가."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(analyst_reports)").fetchall()
        }
        if "report_nid" not in cols:
            conn.execute(
                "ALTER TABLE analyst_reports ADD COLUMN report_nid TEXT"
            )
        if "data_source" not in cols:
            conn.execute(
                "ALTER TABLE analyst_reports ADD COLUMN data_source TEXT"
            )
        if "source_region" not in cols:
            conn.execute(
                "ALTER TABLE analyst_reports ADD COLUMN source_region TEXT"
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_nid
            ON analyst_reports (report_nid)
            WHERE report_nid IS NOT NULL
            """
        )

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate_schema(conn)
            conn.commit()
        logger.info("DB 초기화 완료: %s", self.db_path)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_stocks(self, df: pd.DataFrame) -> int:
        """
        stocks 테이블 UPSERT.

        Expected columns: ticker, stock_name, current_price, fper, fpbr
        """
        if df.empty:
            return 0

        now = self._now_iso()
        rows = []
        for _, r in df.iterrows():
            rows.append(
                (
                    str(r.get("ticker", "")).zfill(6),
                    r.get("stock_name"),
                    r.get("current_price"),
                    r.get("fper"),
                    r.get("fpbr"),
                    now,
                )
            )

        sql = """
        INSERT INTO stocks (ticker, stock_name, current_price, fper, fpbr, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            stock_name    = excluded.stock_name,
            current_price = excluded.current_price,
            fper          = excluded.fper,
            fpbr          = excluded.fpbr,
            updated_at    = excluded.updated_at
        """
        with self.connection() as conn:
            conn.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def get_previous_report(
        self,
        ticker: str,
        securities_company: str,
        before_date: str | None = None,
        exclude_nid: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """
        동일 증권사의 '직전'(시간상 이전) 리포트 1건 조회.

        before_date가 있으면 해당 발표일보다 이전인 리포트만 대상으로 합니다.
        (최신 리포트가 아니라, 바로 이전 시점 리포트)
        """
        code = str(ticker).zfill(6)
        params: list[Any] = [code, securities_company]

        sql = """
        SELECT target_price, report_date, report_nid
        FROM analyst_reports
        WHERE ticker = ? AND securities_company = ?
        """
        if before_date:
            sql += " AND report_date < ?"
            params.append(before_date)
        if exclude_nid:
            sql += " AND (report_nid IS NULL OR report_nid != ?)"
            params.append(exclude_nid)

        sql += " ORDER BY report_date DESC, id DESC LIMIT 1"

        if conn is not None:
            row = conn.execute(sql, tuple(params)).fetchone()
        else:
            with self.connection() as c:
                row = c.execute(sql, tuple(params)).fetchone()

        if row is None:
            return None
        return dict(row)

    def insert_reports(self, df: pd.DataFrame) -> int:
        """
        analyst_reports에 신규 리포트 삽입.

        previous_target_price, target_revision_pct 가 없으면 DB에서 자동 계산.
        """
        if df.empty:
            return 0

        inserted = 0
        now = self._now_iso()

        with self.connection() as conn:
            for _, r in df.iterrows():
                ticker = str(r["ticker"]).zfill(6)
                securities = str(r.get("securities_company", ""))
                target = r.get("target_price")
                report_date = r.get("report_date")
                report_nid = r.get("report_nid")
                if report_nid is not None and pd.isna(report_nid):
                    report_nid = None

                prev = self.get_previous_report(
                    ticker,
                    securities,
                    before_date=report_date if report_date else None,
                    exclude_nid=(
                        str(report_nid)
                        if report_nid is not None
                        else None
                    ),
                    conn=conn,
                )
                prev_target = r.get("previous_target_price")
                if prev_target is None and prev:
                    prev_target = prev.get("target_price")

                revision_pct = r.get("target_revision_pct")
                if revision_pct is None and prev_target and target:
                    try:
                        revision_pct = (float(target) / float(prev_target) - 1) * 100
                    except (TypeError, ZeroDivisionError):
                        revision_pct = None
                data_source = r.get("data_source", "naver")
                if data_source is not None and pd.isna(data_source):
                    data_source = "naver"
                source_region = r.get("source_region", "domestic")
                if source_region is not None and pd.isna(source_region):
                    source_region = "domestic"

                try:
                    conn.execute(
                        """
                        INSERT INTO analyst_reports (
                            ticker, securities_company, report_date,
                            target_price, previous_target_price,
                            target_revision_pct, opinion, report_nid,
                            data_source, source_region, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ticker,
                            securities,
                            report_date,
                            target,
                            prev_target,
                            revision_pct,
                            r.get("opinion"),
                            report_nid,
                            data_source,
                            source_region,
                            now,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()

        return inserted

    def load_stocks(self) -> pd.DataFrame:
        with self.connection() as conn:
            return pd.read_sql_query("SELECT * FROM stocks", conn)

    def load_reports(self, ticker: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM analyst_reports"
        params: Iterable[Any] = ()
        if ticker:
            sql += " WHERE ticker = ?"
            params = (str(ticker).zfill(6),)
        with self.connection() as conn:
            if ticker:
                return pd.read_sql_query(sql, conn, params=(str(ticker).zfill(6),))
            return pd.read_sql_query(sql, conn)

    def load_all_reports(self) -> pd.DataFrame:
        """저장된 전체 리포트 (히스토리 포함)."""
        with self.connection() as conn:
            return pd.read_sql_query(
                """
                SELECT ticker, securities_company, report_date,
                       target_price, previous_target_price, target_revision_pct,
                       opinion, report_nid, data_source, source_region
                FROM analyst_reports
                ORDER BY ticker, securities_company, report_date
                """,
                conn,
            )

    def get_latest_reports_per_broker(self) -> pd.DataFrame:
        """종목·증권사별 최신 리포트 1건."""
        sql = """
        SELECT ar.*
        FROM analyst_reports ar
        INNER JOIN (
            SELECT ticker, securities_company, MAX(report_date) AS max_date
            FROM analyst_reports
            GROUP BY ticker, securities_company
        ) latest
        ON ar.ticker = latest.ticker
        AND ar.securities_company = latest.securities_company
        AND ar.report_date = latest.max_date
        """
        with self.connection() as conn:
            return pd.read_sql_query(sql, conn)
