from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from projects.caelus.generator import generate_daily_content, regenerate_daily_field, regenerate_daily_sign
from projects.caelus.provider import ContentProvider, MockProvider, OpenAIProvider
from projects.caelus.quality import validate_content
from projects.caelus.domain import CONTENT_FIELDS, SIGN_ORDER
from projects.caelus.uniqueness.rules import UniquenessRules
from projects.caelus.uniqueness.settings import DiversitySettings, load_diversity_settings, save_diversity_settings_file
from projects.caelus.validators import get_field, has_errors, set_field
from kaban.ai.usage import merge_usage, provider_usage

from projects.caelus.config import (
    ROOT,
    load_dotenv,
    openai_model,
    openai_reasoning_effort,
    uniqueness_hard_threshold,
    uniqueness_history_days,
    uniqueness_max_regeneration_attempts,
    uniqueness_warning_threshold,
)
from projects.caelus import storage as caelus_storage
from projects.caelus.storage import content_hash, content_path, day_dir, load_json, publication_path, status_path, write_json

GENERATED = caelus_storage.GENERATED

def _generated_root():
    # Совместимость с v1.13 module-level patching и cloud env override.
    if Path(GENERATED) != Path(caelus_storage.GENERATED):
        return Path(GENERATED)
    return caelus_storage.generated_root()

def tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def run_checked(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def _diversity_settings_path():
    return _generated_root() / "_settings" / "content_diversity.json"


def _fallback_diversity_settings() -> DiversitySettings:
    """Строит настройки из .env только как fallback для старых установок."""
    load_dotenv()
    hard = uniqueness_hard_threshold()
    warning = uniqueness_warning_threshold()
    profile = "strict" if abs(hard - 0.80) < 1e-9 and abs(warning - 0.76) < 1e-9 else "custom"
    custom = min(0.90, max(0.70, hard))
    return DiversitySettings(
        profile=profile,
        history_days=uniqueness_history_days(),
        custom_threshold=custom,
        max_regeneration_attempts=uniqueness_max_regeneration_attempts(),
    ).validate()


def diversity_settings() -> DiversitySettings:
    return load_diversity_settings(_diversity_settings_path(), _fallback_diversity_settings())


def save_diversity_settings(*, profile: str, history_days: int, custom_threshold: float) -> DiversitySettings:
    current = diversity_settings()
    settings = DiversitySettings(
        profile=profile,
        history_days=int(history_days),
        custom_threshold=float(custom_threshold),
        max_regeneration_attempts=current.max_regeneration_attempts,
    ).validate()
    save_diversity_settings_file(_diversity_settings_path(), settings)
    return settings


def uniqueness_rules(settings: DiversitySettings | None = None) -> UniquenessRules:
    return (settings or diversity_settings()).to_rules()


def preview_diversity(day: str, language: str, settings: DiversitySettings | None = None) -> dict[str, Any]:
    """Локально пересчитывает конфликты. AI/provider здесь принципиально не используется."""
    path = _generated_root() / day / language / "content.json"
    payload = load_json(path)
    if not payload:
        raise FileNotFoundError("Контент ещё не создан")
    target = datetime.strptime(day, "%Y-%m-%d").date()
    selected = settings or diversity_settings()
    issues, history = validate_content(
        payload,
        generated_dir=_generated_root(),
        language=language,
        target=target,
        rules=selected.to_rules(),
    )
    conflicts = [
        issue for issue in issues
        if issue.get("severity") == "error" and issue.get("code") in {"history_repetition", "same_day_repetition"}
    ]
    return {
        "settings": selected.to_dict(),
        "issues": issues,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "history_dates_loaded": len(history.dates()),
    }


def rebuild(day: str, language: str, changed_signs: list[str] | None = None) -> None:
    base = day_dir(day, language)
    content = base / "content.json"
    # None = пересобрать все карточки; [] = карточки не менялись, пересобрать только Telegram.
    if changed_signs is None:
        run_checked([sys.executable, "-m", "projects.caelus.renderer", str(content), "--out", str(base / "cards")])
    else:
        for sign in changed_signs:
            run_checked([sys.executable, "-m", "projects.caelus.renderer", str(content), "--out", str(base / "cards"), "--sign", sign])
    # Telegram batch зависит от всех 12 знаков, поэтому пересобирается целиком.
    run_checked([sys.executable, "-m", "projects.caelus.telegram_builder", str(content), "--out", str(base / "telegram")])


def _provider(mode: str, language: str, model: str | None = None) -> tuple[ContentProvider, str | None]:
    selected_model = model or openai_model()
    if mode in {"mock"}:
        return MockProvider(language), None
    if mode in {"ai", "openai"}:
        return OpenAIProvider(model=selected_model, reasoning_effort=openai_reasoning_effort()), selected_model
    raise ValueError(f"Неизвестный режим генерации: {mode}")


def _mode_from_payload(payload: dict[str, Any]) -> str:
    provider_name = str((payload.get("generation") or {}).get("provider") or "openai").lower()
    return "mock" if provider_name == "mock" else "ai"


def _validate_for_day(payload: dict[str, Any], day: str, language: str) -> list[dict[str, Any]]:
    target = datetime.strptime(day, "%Y-%m-%d").date()
    issues, _ = validate_content(
        payload,
        generated_dir=_generated_root(),
        language=language,
        target=target,
        rules=uniqueness_rules(),
    )
    return issues


def generate_bundle(day: str, language: str, mode: str = "ai", model: str | None = None) -> dict[str, Any]:
    load_dotenv()
    target = datetime.strptime(day, "%Y-%m-%d").date()
    provider, model_value = _provider(mode, language, model)
    rules = uniqueness_rules()

    payload = generate_daily_content(
        target=target,
        language=language,
        provider=provider,
        generated_dir=_generated_root(),
        history_days=rules.history_days,
        model=model_value,
        uniqueness_rules=rules,
    )
    write_json(content_path(day, language), payload)
    rebuild(day, language)
    issues = _validate_for_day(payload, day, language)
    write_json(status_path(day, language), {
        "state": "draft",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generation_mode": mode,
        "provider": provider.name,
        "model": model_value,
        "validation_errors": sum(1 for item in issues if item.get("severity") == "error"),
        "uniqueness_history_days": rules.history_days,
    })
    return payload


def monthly_api_usage(day: str) -> dict[str, Any]:
    """Суммирует уже сохранённые API usage/cost по всем языкам календарного месяца."""
    month = day[:7]
    totals: dict[str, Any] = {
        "month": month,
        "datasets": 0,
        "request_count": 0,
        "prompt_tokens": 0,
        "cached_prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "unpriced_datasets": 0,
    }
    if not _generated_root().exists():
        return totals
    for date_folder in _generated_root().iterdir():
        if not date_folder.is_dir() or not date_folder.name.startswith(month + "-"):
            continue
        for lang_folder in date_folder.iterdir():
            if not lang_folder.is_dir():
                continue
            payload = load_json(lang_folder / "content.json", {}) or {}
            usage = (payload.get("generation") or {}).get("api_usage")
            if not isinstance(usage, dict) or not int(usage.get("request_count") or 0):
                continue
            totals["datasets"] += 1
            for key in ("request_count", "prompt_tokens", "cached_prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
                totals[key] += int(usage.get(key) or 0)
            cost = usage.get("estimated_cost_usd")
            if cost is None:
                totals["unpriced_datasets"] += 1
            else:
                totals["estimated_cost_usd"] += float(cost)
    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 8)
    return totals


def publication_locks_editing(day: str, language: str) -> bool:
    publication = load_json(publication_path(day, language), {}) or {}
    state = publication.get("state")
    if state in {"publishing", "partially_published", "published"}:
        return True
    if state == "failed" and (publication.get("media") or publication.get("text")):
        return True
    return False


def _reset_approval_after_change(day: str, language: str, reason: str) -> None:
    old = load_json(status_path(day, language), {}) or {}
    if old.get("state") == "approved":
        write_json(status_path(day, language), {
            "state": "draft",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        })


def save_fields(day: str, language: str, form: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    if publication_locks_editing(day, language):
        raise ValueError("Редактирование заблокировано: публикация уже началась. Сначала завершите текущую публикацию.")
    path = content_path(day, language)
    payload = load_json(path)
    if not payload:
        raise FileNotFoundError("Контент ещё не создан")

    changed_signs: list[str] = []
    for sign in SIGN_ORDER:
        item = payload["signs"][sign]
        sign_changed = False
        for field in CONTENT_FIELDS:
            key = f"{sign}__{field}"
            if key not in form:
                continue
            value = form[key].strip()
            if value != get_field(item, field):
                set_field(item, field, value)
                sign_changed = True
        if sign_changed:
            changed_signs.append(sign)

    base = day_dir(day, language)
    missing_cards = [sign for sign in SIGN_ORDER if not (base / "cards" / f"caelus_{sign}.png").is_file()]
    telegram_missing = not (base / "telegram").is_dir()

    if changed_signs:
        write_json(path, payload)
        rebuild(day, language, changed_signs=None if missing_cards else changed_signs)
        _reset_approval_after_change(day, language, "content_changed_after_approval")
    elif missing_cards or telegram_missing:
        rebuild(day, language)

    issues = _validate_for_day(payload, day, language)
    return payload, issues, changed_signs




def _persist_failed_regeneration_usage(path, payload: dict[str, Any], provider: ContentProvider) -> None:
    """Не теряет стоимость уже выполненных API-вызовов, даже если regeneration завершилась ошибкой."""
    usage = provider_usage(provider)
    if not usage:
        return
    generation = payload.setdefault("generation", {})
    generation["api_usage"] = merge_usage(generation.get("api_usage"), usage)
    generation["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(path, payload)

def _issue_similarity(issues: list[dict[str, Any]], sign: str, field: str | None = None) -> float:
    values = [
        float(issue.get("similarity") or 0.0)
        for issue in issues
        if issue.get("sign") == sign
        and (field is None or issue.get("field") == field)
        and issue.get("code") in {"history_repetition", "history_similarity_warning", "same_day_repetition"}
    ]
    return max(values, default=0.0)


def _store_regeneration_feedback(
    payload: dict[str, Any],
    *,
    sign: str,
    field: str | None,
    before_similarity: float,
    after_similarity: float,
    issues: list[dict[str, Any]],
) -> None:
    generation = payload.setdefault("generation", {})
    uniqueness = generation.setdefault("uniqueness", {})
    attempts = int(uniqueness.get("manual_regeneration_attempts_used") or 0)
    unresolved = any(
        issue.get("severity") == "error"
        and issue.get("sign") == sign
        and (field is None or issue.get("field") == field)
        and issue.get("code") in {"history_repetition", "same_day_repetition"}
        for issue in issues
    )
    uniqueness["last_regeneration"] = {
        "kind": "field" if field is not None else "sign",
        "sign": sign,
        "field": field,
        "attempts": attempts,
        "before_similarity": round(before_similarity, 4),
        "after_similarity": round(after_similarity, 4),
        "resolved": not unresolved,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def regenerate_field(day: str, language: str, sign: str, field: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if publication_locks_editing(day, language):
        raise ValueError("Перегенерация заблокирована: публикация этого комплекта уже началась.")
    if sign not in SIGN_ORDER:
        raise ValueError(f"Неизвестный знак: {sign}")
    if field not in CONTENT_FIELDS:
        raise ValueError(f"Неизвестное поле: {field}")
    path = content_path(day, language)
    payload = load_json(path)
    if not payload:
        raise FileNotFoundError("Контент ещё не создан")

    before_issues = _validate_for_day(payload, day, language)
    before_similarity = _issue_similarity(before_issues, sign, field)
    mode = _mode_from_payload(payload)
    model = (payload.get("generation") or {}).get("model") or openai_model()
    provider, _ = _provider(mode, language, str(model) if model else None)
    target = datetime.strptime(day, "%Y-%m-%d").date()
    try:
        payload, issues = regenerate_daily_field(
            payload=payload,
            target=target,
            language=language,
            sign=sign,
            field=field,
            provider=provider,
            generated_dir=_generated_root(),
            rules=uniqueness_rules(),
        )
    except Exception:
        _persist_failed_regeneration_usage(path, payload, provider)
        raise
    _store_regeneration_feedback(
        payload,
        sign=sign,
        field=field,
        before_similarity=before_similarity,
        after_similarity=_issue_similarity(issues, sign, field),
        issues=issues,
    )
    write_json(path, payload)
    # Карточка зависит только от card. Остальные поля меняют Telegram-текст, но не PNG.
    rebuild(day, language, changed_signs=[sign] if field == "card" else [])
    _reset_approval_after_change(day, language, f"field_regenerated_after_approval:{sign}.{field}")
    return payload, issues

def regenerate_sign(day: str, language: str, sign: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if publication_locks_editing(day, language):
        raise ValueError("Перегенерация заблокирована: публикация этого комплекта уже началась.")
    if sign not in SIGN_ORDER:
        raise ValueError(f"Неизвестный знак: {sign}")
    path = content_path(day, language)
    payload = load_json(path)
    if not payload:
        raise FileNotFoundError("Контент ещё не создан")

    before_issues = _validate_for_day(payload, day, language)
    before_similarity = _issue_similarity(before_issues, sign)
    mode = _mode_from_payload(payload)
    model = (payload.get("generation") or {}).get("model") or openai_model()
    provider, _ = _provider(mode, language, str(model) if model else None)
    target = datetime.strptime(day, "%Y-%m-%d").date()
    try:
        payload, issues = regenerate_daily_sign(
            payload=payload,
            target=target,
            language=language,
            sign=sign,
            provider=provider,
            generated_dir=_generated_root(),
            rules=uniqueness_rules(),
        )
    except Exception:
        _persist_failed_regeneration_usage(path, payload, provider)
        raise
    _store_regeneration_feedback(
        payload,
        sign=sign,
        field=None,
        before_similarity=before_similarity,
        after_similarity=_issue_similarity(issues, sign),
        issues=issues,
    )
    write_json(path, payload)
    rebuild(day, language, changed_signs=[sign])
    _reset_approval_after_change(day, language, "sign_regenerated_after_approval")
    return payload, issues


def regenerate_conflicts(day: str, language: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Перегенерирует только поля, которые сейчас являются hard uniqueness conflicts."""
    if publication_locks_editing(day, language):
        raise ValueError("Перегенерация заблокирована: публикация этого комплекта уже началась.")
    path = _generated_root() / day / language / "content.json"
    payload = load_json(path)
    if not payload:
        raise FileNotFoundError("Контент ещё не создан")

    rules = uniqueness_rules()
    target = datetime.strptime(day, "%Y-%m-%d").date()
    issues, _ = validate_content(
        payload, generated_dir=_generated_root(), language=language, target=target, rules=rules
    )
    pairs: list[tuple[str, str]] = []
    for issue in issues:
        pair = (str(issue.get("sign") or ""), str(issue.get("field") or ""))
        if (
            issue.get("severity") == "error"
            and issue.get("code") in {"history_repetition", "same_day_repetition"}
            and pair[0] in SIGN_ORDER
            and pair[1] in CONTENT_FIELDS
            and pair not in pairs
        ):
            pairs.append(pair)
    if not pairs:
        return payload, issues, []

    before_similarity = max((_issue_similarity(issues, sign, field) for sign, field in pairs), default=0.0)
    mode = _mode_from_payload(payload)
    model = (payload.get("generation") or {}).get("model") or openai_model()
    changed: list[str] = []
    changed_cards: list[str] = []
    total_attempts = 0

    for sign, field in pairs:
        provider, _ = _provider(mode, language, str(model) if model else None)
        try:
            payload, _ = regenerate_daily_field(
                payload=payload,
                target=target,
                language=language,
                sign=sign,
                field=field,
                provider=provider,
                generated_dir=_generated_root(),
                rules=rules,
            )
        except Exception:
            _persist_failed_regeneration_usage(path, payload, provider)
            write_json(path, payload)
            if changed:
                rebuild(day, language, changed_signs=changed_cards)
            raise
        changed.append(f"{sign}.{field}")
        total_attempts += int(((payload.get("generation") or {}).get("uniqueness") or {}).get("manual_regeneration_attempts_used") or 0)
        if field == "card" and sign not in changed_cards:
            changed_cards.append(sign)
        write_json(path, payload)

    rebuild(day, language, changed_signs=changed_cards)
    _reset_approval_after_change(day, language, "uniqueness_conflicts_regenerated_after_approval")
    final_issues, _ = validate_content(
        payload, generated_dir=_generated_root(), language=language, target=target, rules=rules
    )
    changed_pairs = {tuple(item.split(".", 1)) for item in changed}
    after_similarity = max(
        (_issue_similarity(final_issues, sign, field) for sign, field in changed_pairs),
        default=0.0,
    )
    unresolved = any(
        issue.get("severity") == "error"
        and (str(issue.get("sign") or ""), str(issue.get("field") or "")) in changed_pairs
        and issue.get("code") in {"history_repetition", "same_day_repetition"}
        for issue in final_issues
    )
    generation = payload.setdefault("generation", {})
    uniqueness = generation.setdefault("uniqueness", {})
    uniqueness["last_regeneration"] = {
        "kind": "conflicts",
        "changed": changed,
        "attempts": total_attempts,
        "before_similarity": round(before_similarity, 4),
        "after_similarity": round(after_similarity, 4),
        "resolved": not unresolved,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(path, payload)
    return payload, final_issues, changed


def approve(day: str, language: str, form: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload, issues, _ = save_fields(day, language, form)
    if has_errors(issues):
        raise ValueError("Approve All заблокирован: исправьте ошибки валидации/уникальности, показанные в Review Console.")
    write_json(status_path(day, language), {
        "state": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash(payload),
        "uniqueness_history_days": uniqueness_rules().history_days,
    })
    return payload, issues


def return_to_draft(day: str, language: str) -> None:
    if publication_locks_editing(day, language):
        raise ValueError("Нельзя вернуть в draft комплект, публикация которого уже началась.")
    write_json(status_path(day, language), {
        "state": "draft",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def validation_issues(day: str, language: str) -> list[dict[str, Any]]:
    payload = load_json(content_path(day, language))
    if not payload:
        return []
    return _validate_for_day(payload, day, language)
