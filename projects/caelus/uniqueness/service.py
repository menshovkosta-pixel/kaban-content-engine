from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ..domain import CONTENT_FIELDS, SIGN_ORDER
from ..validators import get_field

from .history import HistoricalText, HistoryIndex
from .rules import UniquenessRules
from .similarity import SimilarityResult, compare_texts


@dataclass(frozen=True)
class UniquenessIssue:
    severity: str
    code: str
    message: str
    sign: str
    field: str
    similarity: float
    matched_date: str | None = None
    matched_sign: str | None = None
    current_text: str | None = None
    matched_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _closest_history(text: str, candidates: list[HistoricalText]) -> tuple[HistoricalText | None, SimilarityResult | None]:
    best_item: HistoricalText | None = None
    best_result: SimilarityResult | None = None
    for item in candidates:
        result = compare_texts(text, item.text)
        if best_result is None or result.score > best_result.score:
            best_item = item
            best_result = result
    return best_item, best_result


class UniquenessService:
    def __init__(self, rules: UniquenessRules):
        self.rules = rules.validate()

    def validate(self, payload: dict[str, Any], history: HistoryIndex) -> list[dict[str, Any]]:
        signs = payload.get("signs", {})
        issues: list[UniquenessIssue] = []

        # Same-day повторы проверяются базовым validators.py.

        # История: сравниваем знак только с тем же знаком и тем же смысловым блоком.
        for sign in SIGN_ORDER:
            item = signs.get(sign, {})
            if not isinstance(item, dict):
                continue
            for field in CONTENT_FIELDS:
                text = get_field(item, field).strip()
                if not text:
                    continue
                matched, result = _closest_history(text, history.for_sign_field(sign, field))
                if matched is None or result is None or result.score < self.rules.warning_threshold:
                    continue
                severity = "error" if result.score >= self.rules.hard_threshold else "warning"
                code = "history_repetition" if severity == "error" else "history_similarity_warning"
                prefix = "Повтор" if severity == "error" else "Похожий текст"
                issues.append(UniquenessIssue(
                    severity,
                    code,
                    f"{prefix}: {matched.iso_date}, сходство {result.score:.0%} в окне {history.history_days} дней.",
                    sign,
                    field,
                    result.score,
                    matched_date=matched.iso_date,
                    matched_sign=matched.sign,
                    current_text=text,
                    matched_text=matched.text,
                ))
        return [issue.to_dict() for issue in issues]

    def hard_conflict_signs(self, issues: list[dict[str, Any]]) -> set[str]:
        return {
            str(issue["sign"])
            for issue in issues
            if issue.get("severity") == "error" and issue.get("sign") in SIGN_ORDER
        }
