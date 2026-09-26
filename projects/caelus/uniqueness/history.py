from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from ..domain import CONTENT_FIELDS, SIGN_ORDER
from ..validators import get_field


@dataclass(frozen=True)
class HistoricalText:
    iso_date: str
    sign: str
    field: str
    text: str


@dataclass
class HistoryIndex:
    records: list[HistoricalText]
    history_days: int

    def for_sign_field(self, sign: str, field: str) -> list[HistoricalText]:
        return [item for item in self.records if item.sign == sign and item.field == field]

    def dates(self) -> list[str]:
        return sorted({item.iso_date for item in self.records}, reverse=True)


def _payload_date(path: Path, payload: dict) -> date | None:
    raw = payload.get("iso_date") or path.parent.parent.name
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def load_history(generated_dir: Path, language: str, before: date, days: int = 90) -> HistoryIndex:
    """Загружает календарное окно истории [before-days, before)."""
    days = max(0, int(days))
    if days == 0 or not generated_dir.exists():
        return HistoryIndex([], days)
    cutoff = before - timedelta(days=days)
    records: list[HistoricalText] = []
    for path in generated_dir.glob(f"*/{language}/content.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload_date = _payload_date(path, payload)
            if payload_date is None or payload_date >= before or payload_date < cutoff:
                continue
            signs = payload.get("signs", {})
            if not isinstance(signs, dict):
                continue
            for sign in SIGN_ORDER:
                item = signs.get(sign)
                if not isinstance(item, dict):
                    continue
                for field in CONTENT_FIELDS:
                    value = get_field(item, field).strip()
                    if value:
                        records.append(HistoricalText(payload_date.isoformat(), sign, field, value))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Один повреждённый исторический файл не должен ломать весь Review Console.
            continue
    records.sort(key=lambda item: (item.iso_date, item.sign, item.field), reverse=True)
    return HistoryIndex(records, days)


def compact_prompt_history(index: HistoryIndex, max_dates: int = 10) -> str:
    """Формирует ограниченную выборку для prompt; полный 90-дневный контроль остаётся локальным."""
    allowed_dates = set(index.dates()[:max(0, max_dates)])
    if not allowed_dates:
        return ""
    rows: list[str] = []
    by_date_sign: dict[tuple[str, str], dict[str, str]] = {}
    for item in index.records:
        if item.iso_date not in allowed_dates or item.field not in {"card", "advice"}:
            continue
        by_date_sign.setdefault((item.iso_date, item.sign), {})[item.field] = item.text
    for (iso_date, sign), fields in sorted(by_date_sign.items(), reverse=True):
        card = fields.get("card", "")
        advice = fields.get("advice", "")
        rows.append(f"{iso_date} · {sign} · card: {card} · advice: {advice}")
    return "\n".join(rows)


def history_texts(index: HistoryIndex) -> Iterable[str]:
    for item in index.records:
        yield item.text
