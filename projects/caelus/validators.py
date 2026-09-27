from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .domain import CONTENT_FIELDS, SIGN_ORDER
from .uniqueness.similarity import compare_texts

FORBIDDEN_COPY_MARKERS = ("тестовая правка", "test edit")
LEGACY_ALIASES = {"general": "overview", "love": "relationships", "career_money": "work_money"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    sign: str | None = None
    field: str | None = None
    similarity: float | None = None
    matched_date: str | None = None
    matched_sign: str | None = None
    current_text: str | None = None
    matched_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_field(item: dict[str, Any], field: str) -> str:
    if field in item:
        return str(item.get(field, ""))
    legacy = LEGACY_ALIASES.get(field)
    if legacy:
        return str(item.get(legacy, ""))
    return str(item.get(field, ""))


def set_field(item: dict[str, Any], field: str, value: str) -> None:
    legacy = LEGACY_ALIASES.get(field)
    if field in item or not legacy or legacy not in item:
        item[field] = value
    else:
        item[legacy] = value


def _normalized(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.casefold(), flags=re.UNICODE)
    return " ".join(text.split())


def _first_sentence(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    first = re.split(r"(?<=[.!?])\s+", stripped, maxsplit=1)[0]
    return _normalized(first)


def validate_payload(payload: dict[str, Any], recent_texts: dict[str, list[str]] | None = None, same_day_hard_threshold: float | None = None) -> list[dict[str, Any]]:
    issues: list[ValidationIssue] = []
    signs = payload.get("signs")
    if not isinstance(signs, dict):
        return [ValidationIssue("error", "missing_signs", "В content.json отсутствует объект signs.").to_dict()]

    for sign in SIGN_ORDER:
        item = signs.get(sign)
        if not isinstance(item, dict):
            issues.append(ValidationIssue("error", "missing_sign", f"Отсутствует прогноз для {sign}.", sign=sign))
            continue
        for field in CONTENT_FIELDS:
            value = get_field(item, field).strip()
            if not value:
                issues.append(ValidationIssue("error", "empty_field", f"Поле {field} пустое.", sign, field))
                continue
            lowered = value.casefold()
            for marker in FORBIDDEN_COPY_MARKERS:
                if marker in lowered:
                    issues.append(ValidationIssue("error", "forbidden_marker", f"Найдена тестовая пометка: {marker}.", sign, field))
            if field == "card":
                if len(value) > 220:
                    issues.append(ValidationIssue("error", "card_too_long", f"Карточка содержит {len(value)} символов; максимум 220.", sign, field))
                elif len(value) < 80:
                    issues.append(ValidationIssue("error", "card_too_short", f"Карточка содержит {len(value)} символов; минимум 80.", sign, field))
                elif not 120 <= len(value) <= 180:
                    issues.append(ValidationIssue("warning", "card_target_length", f"Карточка содержит {len(value)} символов; рекомендуемый диапазон 120–180.", sign, field))

    # Проверяем повторы всех смысловых блоков между знаками одного дня.
    # Исторический 90-дневный контроль выполняет отдельный UniquenessService.
    for field in CONTENT_FIELDS:
        values: list[tuple[str, str]] = []
        for sign in SIGN_ORDER:
            item = signs.get(sign, {})
            value = get_field(item, field).strip() if isinstance(item, dict) else ""
            if value:
                values.append((sign, value))
        threshold = float(same_day_hard_threshold) if same_day_hard_threshold is not None else (0.90 if field == "advice" else 0.84)
        for i, (sign_a, text_a) in enumerate(values):
            for sign_b, text_b in values[i + 1:]:
                result = compare_texts(text_a, text_b)
                if result.score >= threshold:
                    issues.append(ValidationIssue(
                        "error",
                        "same_day_repetition",
                        f"Поля {field} у {sign_a} и {sign_b} слишком похожи ({result.score:.0%}).",
                        sign_a,
                        field,
                        result.score,
                        matched_sign=sign_b,
                        current_text=text_a,
                        matched_text=text_b,
                    ))

    # Different semantic fields inside one sign must not duplicate each other.
    # CONTENT_FIELDS is ordered from general/base content toward more
    # specialized blocks, so the later field becomes the regeneration target.
    cross_field_threshold = (
        float(same_day_hard_threshold)
        if same_day_hard_threshold is not None
        else 0.84
    )

    for sign in SIGN_ORDER:
        item = signs.get(sign, {})
        if not isinstance(item, dict):
            continue

        values = [
            (field, get_field(item, field).strip())
            for field in CONTENT_FIELDS
            if get_field(item, field).strip()
        ]

        for i, (field_a, text_a) in enumerate(values):
            for field_b, text_b in values[i + 1:]:
                result = compare_texts(text_a, text_b)

                first_a = _first_sentence(text_a)
                first_b = _first_sentence(text_b)
                repeated_opening = (
                    len(first_a) >= 40
                    and first_a == first_b
                )

                if result.score < cross_field_threshold and not repeated_opening:
                    continue

                reason = (
                    f"same opening sentence"
                    if repeated_opening and result.score < cross_field_threshold
                    else f"similarity {result.score:.0%}"
                )

                issues.append(ValidationIssue(
                    "error",
                    "cross_field_repetition",
                    (
                        f"Fields {field_a} and {field_b} for {sign} "
                        f"repeat each other ({reason})."
                    ),
                    sign,
                    field_b,
                    result.score,
                    current_text=text_b,
                    matched_text=text_a,
                ))

    # recent_texts оставлен в сигнатуре для backward compatibility v4.7.
    # Начиная с v4.8 исторические сравнения должны быть привязаны к тому же знаку,
    # поэтому агрегированный словарь v4.7 намеренно больше не используется.
    del recent_texts

    return [item.to_dict() for item in issues]


def has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "error" for issue in issues)
