"""분기·부분월 헬퍼 (run_samsung_nowcast / quarterly_export 공통)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config.settings import PARTIAL_EXPORT_OVERRIDE


@dataclass
class PartialMonthInput:
    year_month: str
    partial_export_usd: float
    days_covered: int
    confirmed: bool = False


def quarter_label(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def quarter_months(year: int, quarter: int) -> list[str]:
    start_month = (quarter - 1) * 3 + 1
    return [f"{year}-{m:02d}" for m in range(start_month, start_month + 3)]


def yymm_from_label(label: str) -> str:
    return label.replace("-", "")


def label_from_yymm(yymm: str) -> str:
    return f"{yymm[:4]}-{yymm[4:]}"


def confirmed_month_cutoff(as_of: date) -> str:
    if as_of.day >= 15:
        confirmed_through = as_of.month - 1
        year = as_of.year
    else:
        confirmed_through = as_of.month - 2
        year = as_of.year
    if confirmed_through <= 0:
        confirmed_through += 12
        year -= 1
    return f"{year}-{confirmed_through:02d}"


def load_partial_overrides(path: Path | None = None) -> dict[str, PartialMonthInput]:
    path = path or PARTIAL_EXPORT_OVERRIDE
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, PartialMonthInput] = {}
    for ym, item in raw.get("partials", {}).items():
        out[ym] = PartialMonthInput(
            year_month=ym,
            partial_export_usd=float(item["partial_export_usd"]),
            days_covered=int(item["days_covered"]),
            confirmed=bool(item.get("confirmed", False)),
        )
    return out
