from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .domain import SIGN_ORDER
from .validators import has_errors, validate_payload
from .uniqueness.history import HistoryIndex, load_history
from .uniqueness.rules import UniquenessRules
from .uniqueness.service import UniquenessService


def validate_content(
    payload: dict[str, Any],
    *,
    generated_dir: Path,
    language: str,
    target: date,
    rules: UniquenessRules,
) -> tuple[list[dict[str, Any]], HistoryIndex]:
    """Единая точка validation: schema/business rules + 90-day uniqueness."""
    history = load_history(generated_dir, language, target, rules.history_days)
    issues = validate_payload(payload, same_day_hard_threshold=rules.same_day_hard_threshold)
    issues.extend(UniquenessService(rules).validate(payload, history))
    return issues, history


def hard_conflict_signs(issues: list[dict[str, Any]]) -> set[str]:
    return {
        str(issue.get("sign"))
        for issue in issues
        if issue.get("severity") == "error" and issue.get("sign") in SIGN_ORDER
    }


def summarize_uniqueness(issues: list[dict[str, Any]], history: HistoryIndex, rules: UniquenessRules) -> dict[str, Any]:
    relevant = [item for item in issues if item.get("code") in {"history_repetition", "history_similarity_warning", "same_day_repetition"}]
    highest = max((float(item.get("similarity") or 0.0) for item in relevant), default=0.0)
    return {
        "history_days": rules.history_days,
        "history_dates_loaded": len(history.dates()),
        "hard_threshold": rules.hard_threshold,
        "warning_threshold": rules.warning_threshold,
        "max_regeneration_attempts": rules.max_regeneration_attempts,
        "highest_similarity": round(highest, 4),
        "errors": sum(1 for item in relevant if item.get("severity") == "error"),
        "warnings": sum(1 for item in relevant if item.get("severity") == "warning"),
        "passed": not has_errors(issues),
    }
