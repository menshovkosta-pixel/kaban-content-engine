from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from projects.caelus.prompts import regenerate_field_prompt, regenerate_sign_prompt, system_prompt, user_prompt
from projects.caelus.provider import ContentProvider, ProviderError
from projects.caelus.quality import summarize_uniqueness, validate_content
from projects.caelus.domain import CONTENT_FIELDS, SIGN_NAMES, SIGN_ORDER, display_date
from projects.caelus.uniqueness.history import HistoryIndex, compact_prompt_history, load_history
from projects.caelus.uniqueness.rules import UniquenessRules
from projects.caelus.uniqueness.similarity import compare_texts
from projects.caelus.validators import get_field, has_errors, set_field
from kaban.ai.usage import merge_usage, provider_usage


REGENERATABLE_UNIQUENESS_CODES = {"history_repetition", "same_day_repetition", "cross_field_repetition"}


def _mark_diversity_metadata_stale(payload: dict[str, Any], sign: str, field: str) -> None:
    item = payload.get("signs", {}).get(sign)
    if not isinstance(item, dict):
        return
    meta = item.get("_diversity")
    if not isinstance(meta, dict):
        meta = {
            "theme": "pending",
            "situation": "pending",
            "tone": "pending",
            "advice_pattern": "pending",
        }
        item["_diversity"] = meta
    stale = meta.get("stale_fields")
    if not isinstance(stale, list):
        stale = []
    if field not in stale:
        stale.append(field)
    meta["stale_fields"] = stale


def load_recent_history(
    generated_dir: Path,
    language: str,
    before: date,
    days: int = 7,
) -> tuple[str, dict[str, list[str]]]:
    """Backward-compatible helper v4.7 поверх нового HistoryIndex."""
    index = load_history(generated_dir, language, before, days)
    recent_texts: dict[str, list[str]] = {field: [] for field in CONTENT_FIELDS}
    for record in index.records:
        recent_texts[record.field].append(record.text)
    return compact_prompt_history(index, max_dates=min(days, 10)), recent_texts


def _current_day_context(payload: dict[str, Any], exclude_sign: str) -> str:
    rows: list[str] = []
    signs = payload.get("signs", {})
    for sign in SIGN_ORDER:
        if sign == exclude_sign:
            continue
        item = signs.get(sign, {})
        if not isinstance(item, dict):
            continue
        card = get_field(item, "card").strip()
        advice = get_field(item, "advice").strip()
        if card or advice:
            rows.append(f"{sign}: card={card} | advice={advice}")
    return "\n".join(rows)


def _current_day_field_context(payload: dict[str, Any], exclude_sign: str, field: str) -> str:
    rows: list[str] = []
    signs = payload.get("signs", {})
    for sign in SIGN_ORDER:
        if sign == exclude_sign:
            continue
        item = signs.get(sign, {})
        if not isinstance(item, dict):
            continue
        value = get_field(item, field).strip()
        if value:
            rows.append(f"{sign}: {value}")
    return "\n".join(rows)


def _current_sign_text(payload: dict[str, Any], sign: str) -> str:
    item = payload.get("signs", {}).get(sign, {})
    if not isinstance(item, dict):
        return ""
    return "\n".join(f"{field}: {get_field(item, field).strip()}" for field in CONTENT_FIELDS)


def _conflicts_for_prompt(
    sign: str,
    issues: list[dict[str, Any]],
    history: HistoryIndex,
    payload: dict[str, Any],
    *,
    field: str | None = None,
    include_warnings: bool = False,
) -> str:
    """Формирует точный конфликтный контекст, включая исходный исторический текст."""
    rows: list[str] = []
    seen: set[str] = set()
    allowed_severity = {"error", "warning"} if include_warnings else {"error"}
    for issue in issues:
        if issue.get("severity") not in allowed_severity or issue.get("sign") != sign:
            continue
        issue_field = str(issue.get("field") or "")
        if field is not None and issue_field != field:
            continue

        message = str(issue.get("message") or issue.get("code") or "uniqueness conflict")
        if message not in seen:
            rows.append(message)
            seen.add(message)

        matched_date = issue.get("matched_date")
        if matched_date and issue_field:
            for record in history.for_sign_field(sign, issue_field):
                if record.iso_date == matched_date:
                    line = f"Исторический {issue_field} от {matched_date}: {record.text}"
                    if line not in seen:
                        rows.append(line)
                        seen.add(line)
                    break

        matched_sign = issue.get("matched_sign")
        if matched_sign and issue_field:
            other = payload.get("signs", {}).get(matched_sign, {})
            if isinstance(other, dict):
                other_text = get_field(other, issue_field).strip()
                if other_text:
                    line = f"Текущий {matched_sign}/{issue_field}: {other_text}"
                    if line not in seen:
                        rows.append(line)
                        seen.add(line)
    return "\n".join(rows)


def _hard_conflict_fields(issues: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for issue in issues:
        sign = str(issue.get("sign") or "")
        field = str(issue.get("field") or "")
        if (
            issue.get("severity") == "error"
            and issue.get("code") in REGENERATABLE_UNIQUENESS_CODES
            and sign in SIGN_ORDER
            and field in CONTENT_FIELDS
        ):
            pairs.add((sign, field))
    return sorted(pairs, key=lambda pair: (SIGN_ORDER.index(pair[0]), CONTENT_FIELDS.index(pair[1])))


def _field_issues(issues: list[dict[str, Any]], sign: str, field: str, *, errors_only: bool = False) -> list[dict[str, Any]]:
    return [
        issue for issue in issues
        if issue.get("sign") == sign
        and issue.get("field") == field
        and (not errors_only or issue.get("severity") == "error")
    ]


def _max_similarity(issues: list[dict[str, Any]], sign: str, field: str) -> float:
    return max(
        (float(issue.get("similarity") or 0.0) for issue in _field_issues(issues, sign, field)),
        default=0.0,
    )


def generate_daily_content(
    *,
    target: date,
    language: str,
    provider: ContentProvider,
    generated_dir: Path,
    history_days: int = 90,
    model: str | None = None,
    uniqueness_rules: UniquenessRules | None = None,
) -> dict[str, Any]:
    if language not in {"ru", "en"}:
        raise ValueError("Unsupported language")

    rules = (uniqueness_rules or UniquenessRules(history_days=history_days)).validate()
    history = load_history(generated_dir, language, target, rules.history_days)
    prompt_history = compact_prompt_history(history, max_dates=10)

    signs = provider.generate(
        system_prompt=system_prompt(language),
        user_prompt=user_prompt(target, language, prompt_history),
    )
    payload: dict[str, Any] = {
        "schema_version": 2,
        "iso_date": target.isoformat(),
        "date": display_date(target, language),
        "language": language,
        "brand": "CAELUS",
        "generation": {
            "provider": provider.name,
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "signs": {},
    }
    for sign in SIGN_ORDER:
        source = signs.get(sign, {}) if isinstance(signs, dict) else {}
        payload["signs"][sign] = {"name": SIGN_NAMES[language][sign], **source}

    issues, history = validate_content(
        payload,
        generated_dir=generated_dir,
        language=language,
        target=target,
        rules=rules,
    )

    regeneration_log: list[dict[str, Any]] = []
    # Начиная с v4.10 автоматически переписываем только конфликтующее поле,
    # а не весь знак. Это уменьшает стоимость и не трогает качественный текст.
    for attempt in range(1, rules.max_regeneration_attempts + 1):
        conflict_fields = _hard_conflict_fields(issues)
        if not conflict_fields:
            break

        for sign, field in conflict_fields:
            current_text = get_field(payload["signs"][sign], field).strip()
            candidate = provider.generate_field(
                sign=sign,
                field=field,
                system_prompt=system_prompt(language),
                user_prompt=regenerate_field_prompt(
                    target=target,
                    language=language,
                    sign=sign,
                    field=field,
                    current_text=current_text,
                    conflicts=_conflicts_for_prompt(sign, issues, history, payload, field=field),
                    current_day_context=_current_day_field_context(payload, sign, field),
                    attempt=attempt,
                ),
            ).strip()
            original_similarity = compare_texts(current_text, candidate).score
            accepted_for_validation = bool(candidate) and original_similarity < rules.hard_threshold
            regeneration_log.append({
                "attempt": attempt,
                "sign": sign,
                "field": field,
                "similarity_to_replaced_text": round(original_similarity, 4),
                "accepted_for_validation": accepted_for_validation,
            })
            if accepted_for_validation:
                set_field(payload["signs"][sign], field, candidate)
                _mark_diversity_metadata_stale(payload, sign, field)

        issues, history = validate_content(
            payload,
            generated_dir=generated_dir,
            language=language,
            target=target,
            rules=rules,
        )

    # Ошибки без конкретного знака означают повреждённую структуру, которую нельзя
    # безопасно отдать редактору как обычный DRAFT.
    fatal = [issue for issue in issues if issue.get("severity") == "error" and issue.get("sign") not in SIGN_ORDER]
    if fatal:
        details = "; ".join(str(issue.get("message")) for issue in fatal)
        raise ValueError(f"Сгенерированный контент не прошёл структурную валидацию: {details}")

    uniqueness_meta = summarize_uniqueness(issues, history, rules)
    uniqueness_meta.update({
        "regeneration_attempts_used": max((item["attempt"] for item in regeneration_log), default=0),
        "regenerated_signs": sorted({item["sign"] for item in regeneration_log}, key=SIGN_ORDER.index),
        "regenerated_fields": [
            f"{sign}.{field}"
            for sign, field in sorted(
                {(item["sign"], item["field"]) for item in regeneration_log},
                key=lambda pair: (SIGN_ORDER.index(pair[0]), CONTENT_FIELDS.index(pair[1])),
            )
        ],
        "regeneration_log": regeneration_log,
        "exhausted_with_errors": has_errors(issues),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })
    payload["generation"]["uniqueness"] = uniqueness_meta
    api_usage = provider_usage(provider)
    if api_usage:
        payload["generation"]["api_usage"] = api_usage
    return payload


def regenerate_daily_field(
    *,
    payload: dict[str, Any],
    target: date,
    language: str,
    sign: str,
    field: str,
    provider: ContentProvider,
    generated_dir: Path,
    rules: UniquenessRules,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Перегенерирует одно поле и гарантирует, что результат не равен/не близок к исходнику."""
    if sign not in SIGN_ORDER:
        raise ValueError(f"Неизвестный знак: {sign}")
    if field not in CONTENT_FIELDS:
        raise ValueError(f"Неизвестное поле: {field}")

    rules = rules.validate()
    issues, history = validate_content(
        payload,
        generated_dir=generated_dir,
        language=language,
        target=target,
        rules=rules,
    )
    original_text = get_field(payload["signs"][sign], field).strip()
    if not original_text:
        raise ValueError(f"Нельзя перегенерировать пустое поле {sign}.{field}")

    # Для ручной операции передаём AI и errors, и warnings — это исправляет баг v4.9,
    # где 81% similarity был warning и конфликтующий исторический текст терялся.
    conflicts = _conflicts_for_prompt(
        sign,
        issues,
        history,
        payload,
        field=field,
        include_warnings=True,
    )

    max_attempts = max(1, rules.max_regeneration_attempts)
    best_candidate: str | None = None
    best_rank: tuple[int, float, float] | None = None
    best_issues: list[dict[str, Any]] | None = None
    attempts_log: list[dict[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        candidate = provider.generate_field(
            sign=sign,
            field=field,
            system_prompt=system_prompt(language),
            user_prompt=regenerate_field_prompt(
                target=target,
                language=language,
                sign=sign,
                field=field,
                current_text=original_text,
                conflicts=conflicts,
                current_day_context=_current_day_field_context(payload, sign, field),
                attempt=attempt,
            ),
        ).strip()
        similarity_to_original = compare_texts(original_text, candidate).score if candidate else 1.0
        if not candidate or similarity_to_original >= rules.hard_threshold:
            attempts_log.append({
                "attempt": attempt,
                "candidate_similarity_to_original": round(similarity_to_original, 4),
                "accepted": False,
                "reason": "candidate_too_similar_to_original",
            })
            continue

        set_field(payload["signs"][sign], field, candidate)
        candidate_issues, candidate_history = validate_content(
            payload,
            generated_dir=generated_dir,
            language=language,
            target=target,
            rules=rules,
        )
        field_errors = _field_issues(candidate_issues, sign, field, errors_only=True)
        max_similarity = _max_similarity(candidate_issues, sign, field)
        rank = (len(field_errors), max_similarity, similarity_to_original)
        attempts_log.append({
            "attempt": attempt,
            "candidate_similarity_to_original": round(similarity_to_original, 4),
            "historical_or_same_day_similarity": round(max_similarity, 4),
            "accepted": not field_errors,
        })

        if best_rank is None or rank < best_rank:
            best_candidate = candidate
            best_rank = rank
            best_issues = candidate_issues

        if not field_errors:
            issues, history = candidate_issues, candidate_history
            break

        # Следующая попытка получает точный исторический текст, с которым столкнулся
        # уже новый кандидат. Исходный текст при этом остаётся явно запрещённым.
        conflicts = _conflicts_for_prompt(
            sign,
            candidate_issues,
            candidate_history,
            payload,
            field=field,
            include_warnings=True,
        ) or conflicts
        set_field(payload["signs"][sign], field, original_text)
    else:
        if best_candidate is None:
            set_field(payload["signs"][sign], field, original_text)
            raise ProviderError(
                f"Regenerate не смог создать достаточно новый текст для {sign}.{field} после {max_attempts} попыток. "
                f"Исходный текст сохранён; AI каждый раз возвращал вариант с сходством >= {rules.hard_threshold:.0%}."
            )
        set_field(payload["signs"][sign], field, best_candidate)
        issues = best_issues or issues
        history = load_history(generated_dir, language, target, rules.history_days)

    if get_field(payload["signs"][sign], field).strip() != original_text:
        _mark_diversity_metadata_stale(payload, sign, field)

    generation = payload.setdefault("generation", {})
    uniqueness_meta = summarize_uniqueness(issues, history, rules)
    uniqueness_meta.update({
        "manual_regenerated_sign": sign,
        "manual_regenerated_field": field,
        "manual_regeneration_attempts_used": len(attempts_log),
        "manual_regeneration_log": attempts_log,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "exhausted_with_errors": has_errors(issues),
    })
    generation["uniqueness"] = uniqueness_meta
    generation["api_usage"] = merge_usage(generation.get("api_usage"), provider_usage(provider))
    if generation.get("api_usage") is None:
        generation.pop("api_usage", None)
    generation["updated_at"] = datetime.now(timezone.utc).isoformat()
    return payload, issues


def regenerate_daily_sign(
    *,
    payload: dict[str, Any],
    target: date,
    language: str,
    sign: str,
    provider: ContentProvider,
    generated_dir: Path,
    rules: UniquenessRules,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Создаёт новый вариант всего знака; v4.10 проверяет реальное отличие от исходника."""
    if sign not in SIGN_ORDER:
        raise ValueError(f"Неизвестный знак: {sign}")

    rules = rules.validate()
    issues, history = validate_content(
        payload,
        generated_dir=generated_dir,
        language=language,
        target=target,
        rules=rules,
    )
    original = {field: get_field(payload["signs"][sign], field).strip() for field in CONTENT_FIELDS}
    original_meta = payload["signs"][sign].get("_diversity")
    original_meta = dict(original_meta) if isinstance(original_meta, dict) else None
    current_sign_text = _current_sign_text(payload, sign)
    conflicts = _conflicts_for_prompt(sign, issues, history, payload, include_warnings=True)

    max_attempts = max(1, rules.max_regeneration_attempts)
    best_replacement: dict[str, Any] | None = None
    best_rank: tuple[int, float] | None = None
    best_issues: list[dict[str, Any]] | None = None
    attempts_log: list[dict[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        replacement = provider.generate_sign(
            sign=sign,
            system_prompt=system_prompt(language),
            user_prompt=regenerate_sign_prompt(
                target=target,
                language=language,
                sign=sign,
                current_day_context=_current_day_context(payload, sign),
                conflicts=conflicts,
                attempt=attempt,
                current_sign_text=current_sign_text,
            ),
        )
        similarities = {
            field: compare_texts(original[field], str(replacement.get(field, ""))).score
            for field in CONTENT_FIELDS
        }
        too_similar = [field for field, score in similarities.items() if score >= rules.hard_threshold]
        if too_similar:
            attempts_log.append({
                "attempt": attempt,
                "accepted": False,
                "reason": "candidate_too_similar_to_original",
                "too_similar_fields": too_similar,
                "similarities": {k: round(v, 4) for k, v in similarities.items()},
            })
            continue

        payload["signs"][sign] = {"name": SIGN_NAMES[language][sign], **replacement}
        candidate_issues, candidate_history = validate_content(
            payload,
            generated_dir=generated_dir,
            language=language,
            target=target,
            rules=rules,
        )
        sign_errors = [
            issue for issue in candidate_issues
            if issue.get("severity") == "error" and issue.get("sign") == sign
        ]
        max_similarity = max(
            (float(issue.get("similarity") or 0.0) for issue in candidate_issues if issue.get("sign") == sign),
            default=0.0,
        )
        rank = (len(sign_errors), max_similarity)
        attempts_log.append({
            "attempt": attempt,
            "accepted": not sign_errors,
            "similarities_to_original": {k: round(v, 4) for k, v in similarities.items()},
            "validation_similarity": round(max_similarity, 4),
        })
        if best_rank is None or rank < best_rank:
            best_replacement = dict(replacement)
            best_rank = rank
            best_issues = candidate_issues

        if not sign_errors:
            issues, history = candidate_issues, candidate_history
            break

        conflicts = _conflicts_for_prompt(sign, candidate_issues, candidate_history, payload, include_warnings=True) or conflicts
        payload["signs"][sign] = {"name": SIGN_NAMES[language][sign], **original}
        if original_meta is not None:
            payload["signs"][sign]["_diversity"] = dict(original_meta)
    else:
        if best_replacement is None:
            payload["signs"][sign] = {"name": SIGN_NAMES[language][sign], **original}
            if original_meta is not None:
                payload["signs"][sign]["_diversity"] = dict(original_meta)
            raise ProviderError(
                f"Regenerate не смог создать достаточно новый прогноз для {sign} после {max_attempts} попыток. "
                f"Исходный прогноз сохранён."
            )
        payload["signs"][sign] = {"name": SIGN_NAMES[language][sign], **best_replacement}
        issues = best_issues or issues
        history = load_history(generated_dir, language, target, rules.history_days)

    generation = payload.setdefault("generation", {})
    uniqueness_meta = summarize_uniqueness(issues, history, rules)
    uniqueness_meta.update({
        "manual_regenerated_sign": sign,
        "manual_regeneration_attempts_used": len(attempts_log),
        "manual_regeneration_log": attempts_log,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "exhausted_with_errors": has_errors(issues),
    })
    generation["uniqueness"] = uniqueness_meta
    generation["api_usage"] = merge_usage(generation.get("api_usage"), provider_usage(provider))
    if generation.get("api_usage") is None:
        generation.pop("api_usage", None)
    generation["updated_at"] = datetime.now(timezone.utc).isoformat()
    return payload, issues
