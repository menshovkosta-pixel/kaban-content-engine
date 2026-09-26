from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import shutil
import tempfile
from pathlib import Path
from typing import Any

from kaban.storage import content_hash as calculate_content_hash, load_json
from projects.caelus.automation_store import load_run, next_attempt, operation_lock, sanitize_exception, write_run
from projects.caelus.storage import GENERATED, content_path, day_dir, publication_path, status_path
from projects.caelus.workflow import generate_bundle
from projects.caelus.publication import run_telegram_publisher


@dataclass(frozen=True)
class AutomationState:
    state: str
    date: str
    language: str
    content_hash: str | None
    last_operation: str | None
    last_result: str | None
    attempt: int
    last_error: dict[str, str] | None
    updated_at: str | None


def _journal_fields(run: dict[str, Any]) -> tuple[str | None, str | None, int, dict[str, str] | None, str | None]:
    last_error = run.get("last_error") if isinstance(run.get("last_error"), dict) else None
    updated_at = run.get("finished_at") or run.get("started_at")
    return (
        str(run.get("last_operation")) if run.get("last_operation") else None,
        str(run.get("last_result")) if run.get("last_result") else None,
        int(run.get("attempt") or 0),
        last_error,
        str(updated_at) if updated_at else None,
    )


def derive_state(day: str, language: str) -> AutomationState:
    day_dir(day, language)  # Валидация входных параметров.
    run = load_run(day, language)
    last_operation, last_result, attempt, last_error, updated_at = _journal_fields(run)

    content = load_json(content_path(day, language))
    status = load_json(status_path(day, language), {}) or {}
    publication = load_json(publication_path(day, language), {}) or {}
    current_hash = calculate_content_hash(content) if isinstance(content, dict) else None

    publication_state = publication.get("state") if isinstance(publication, dict) else None
    if publication_state == "published":
        state = "published"
    elif publication_state in {"publishing", "partially_published"}:
        state = "publishing"
    elif publication_state == "failed":
        state = "publish_failed"
    elif publication_state == "unknown_delivery":
        state = "publication_unknown"
    elif not isinstance(content, dict) and last_operation == "generate" and last_result == "failed":
        state = "generation_failed"
    elif not isinstance(content, dict):
        state = "pending_generation"
    elif isinstance(status, dict) and status.get("state") == "approved":
        state = "ready_to_publish" if status.get("content_hash") == current_hash else "approval_invalid"
    else:
        state = "review_required"

    return AutomationState(
        state=state,
        date=day,
        language=language,
        content_hash=current_hash,
        last_operation=last_operation,
        last_result=last_result,
        attempt=attempt,
        last_error=last_error,
        updated_at=updated_at,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _start_operation(day: str, language: str, operation: str) -> tuple[dict[str, Any], int, str]:
    run = load_run(day, language)
    attempt = next_attempt(run, operation)
    started_at = _utc_now()
    history = run.get("history")
    updated = {
        **run,
        "project_id": "caelus",
        "date": day,
        "language": language,
        "last_operation": operation,
        "last_result": "running",
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": None,
        "last_error": None,
        "history": list(history) if isinstance(history, list) else [],
    }
    write_run(day, language, updated)
    return updated, attempt, started_at


def _finish_operation(
    day: str,
    language: str,
    operation: str,
    *,
    attempt: int,
    started_at: str,
    result: str,
    content_hash: str | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    run = load_run(day, language)
    finished_at = _utc_now()
    safe_error = sanitize_exception(error) if error is not None else None
    history = list(run.get("history") or [])
    event: dict[str, Any] = {
        "operation": operation,
        "attempt": attempt,
        "result": result,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    if safe_error is not None:
        event["error_type"] = safe_error["type"]
    if content_hash is not None:
        event["content_hash"] = content_hash
    history.append(event)
    updated = {
        **run,
        "project_id": "caelus",
        "date": day,
        "language": language,
        "last_operation": operation,
        "last_result": result,
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": finished_at,
        "last_error": safe_error,
        "content_hash": content_hash,
        "history": history[-100:],
    }
    write_run(day, language, updated)
    return updated


def run_generation_job(
    day: str,
    language: str,
    *,
    mode: str = "ai",
    model: str | None = None,
    force: bool = False,
) -> AutomationState:
    with operation_lock(day, language, "generate"):
        base = day_dir(day, language)
        run, attempt, started_at = _start_operation(day, language, "generate")
        print(f"[CAELUS] operation=generate date={day} language={language} attempt={attempt}")

        if content_path(day, language).is_file() and not force:
            current = load_json(content_path(day, language), {}) or {}
            current_hash = calculate_content_hash(current) if isinstance(current, dict) else None
            _finish_operation(
                day, language, "generate", attempt=attempt, started_at=started_at,
                result="skipped", content_hash=current_hash,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=skipped state={state.state}")
            return state

        existed_before = base.exists()
        temp_ctx = tempfile.TemporaryDirectory(prefix="caelus-generation-backup-") if existed_before else None
        backup: Path | None = None
        generation_started = False
        try:
            if temp_ctx is not None:
                backup = Path(temp_ctx.name) / "dataset"
                shutil.copytree(base, backup)
            generation_started = True
            generate_bundle(day, language, mode, model=model)
            payload = load_json(content_path(day, language), {}) or {}
            current_hash = calculate_content_hash(payload) if isinstance(payload, dict) else None
            _finish_operation(
                day, language, "generate", attempt=attempt, started_at=started_at,
                result="success", content_hash=current_hash,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=success state={state.state}")
            return state
        except Exception as exc:
            if generation_started:
                if base.exists():
                    shutil.rmtree(base)
                if backup is not None and backup.exists():
                    shutil.copytree(backup, base)
            restored_payload = load_json(content_path(day, language), {}) or {}
            restored_hash = calculate_content_hash(restored_payload) if isinstance(restored_payload, dict) and restored_payload else None
            _finish_operation(
                day, language, "generate", attempt=attempt, started_at=started_at,
                result="failed", content_hash=restored_hash, error=exc,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=failed state={state.state} error={type(exc).__name__}")
            raise
        finally:
            if temp_ctx is not None:
                temp_ctx.cleanup()


class AutomationPreconditionError(RuntimeError):
    pass


class AutomationManualReconciliationRequired(RuntimeError):
    """Автоматический retry публикации запрещён до ручной сверки Telegram."""



def run_publication_job(
    day: str,
    language: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> AutomationState:
    with operation_lock(day, language, "publish"):
        initial = derive_state(day, language)
        _, attempt, started_at = _start_operation(day, language, "publish")
        print(f"[CAELUS] operation=publish date={day} language={language} attempt={attempt}")

        if initial.state == "published" and not force:
            _finish_operation(
                day, language, "publish", attempt=attempt, started_at=started_at,
                result="skipped", content_hash=initial.content_hash,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=skipped state={state.state}")
            return state

        allowed = {"ready_to_publish", "publish_failed", "publishing"}
        if not force and initial.state not in allowed:
            _finish_operation(
                day, language, "publish", attempt=attempt, started_at=started_at,
                result="skipped", content_hash=initial.content_hash,
            )
            raise AutomationPreconditionError(
                f"Публикация недоступна для состояния {initial.state}. Требуется approved-комплект с актуальным content_hash."
            )

        try:
            code, output = run_telegram_publisher(day, language, dry_run=dry_run, force=force)
            if code == 3:
                raise AutomationManualReconciliationRequired(
                    output or "Telegram delivery требует ручной сверки; автоматический retry запрещён"
                )
            if code != 0:
                raise RuntimeError(output or f"CAELUS publisher завершился с кодом {code}")
            current = derive_state(day, language)
            _finish_operation(
                day, language, "publish", attempt=attempt, started_at=started_at,
                result="success", content_hash=current.content_hash,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=success state={state.state}")
            return state
        except Exception as exc:
            current = derive_state(day, language)
            _finish_operation(
                day, language, "publish", attempt=attempt, started_at=started_at,
                result="failed", content_hash=current.content_hash, error=exc,
            )
            state = derive_state(day, language)
            print(f"[CAELUS] result=failed state={state.state} error={type(exc).__name__}")
            raise
