from __future__ import annotations

from datetime import timedelta
from typing import Any

from kaban.scheduler.models import JobContext, JobResult, ScheduledJob, WaitCondition
from projects.caelus.automation import (
    AutomationManualReconciliationRequired,
    AutomationPreconditionError,
    run_generation_job,
    run_publication_job,
)
from projects.caelus.automation_store import AutomationBusyError


def _target_day(context: JobContext, params: dict[str, Any]) -> str:
    offset = params.get("target_date_offset_days", 0)
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError("target_date_offset_days должен быть целым числом")
    return (context.scheduled_for_local.date() + timedelta(days=offset)).isoformat()


def _language(params: dict[str, Any]) -> str:
    language = params.get("language")
    if language not in {"ru", "en"}:
        raise ValueError("language должен быть ru или en")
    return str(language)


def _bool_param(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} должен быть boolean")
    return value


def run_job(job: ScheduledJob, context: JobContext) -> JobResult:
    params = dict(job.params)
    if job.handler == "generate":
        language = _language(params)
        mode = params.get("mode", "ai")
        if mode not in {"ai", "mock"}:
            raise ValueError("mode должен быть ai или mock")
        model = params.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("model должен быть непустой строкой")
        day = _target_day(context, params)
        state = run_generation_job(
            day,
            language,
            mode=str(mode),
            model=model.strip() if isinstance(model, str) else None,
            force=False,
        )
        outcome = "skipped" if state.last_result == "skipped" else "success"
        return JobResult(outcome=outcome, message=f"CAELUS generate: {state.state}")

    if job.handler == "publish":
        language = _language(params)
        day = _target_day(context, params)
        dry_run = _bool_param(params, "dry_run", False)
        try:
            state = run_publication_job(day, language, dry_run=dry_run, force=False)
        except AutomationManualReconciliationRequired as exc:
            return JobResult(outcome="blocked", message=str(exc), retryable=False)
        except AutomationPreconditionError as exc:
            return JobResult(
                outcome="blocked", message=str(exc), retryable=True,
                wait_condition=WaitCondition(
                    kind="content_approved", project_id=job.project_id,
                    data={"content_key": f"{day}:{language}"},
                ),
            )
        except AutomationBusyError as exc:
            return JobResult(outcome="blocked", message=str(exc), retryable=True)
        outcome = "skipped" if state.last_result == "skipped" else "success"
        return JobResult(outcome=outcome, message=f"CAELUS publish: {state.state}")

    raise ValueError(f"Неизвестный CAELUS scheduler handler: {job.handler}")
