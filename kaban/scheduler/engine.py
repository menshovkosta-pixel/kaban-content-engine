from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from kaban.scheduler.config import iter_scheduled_jobs
from kaban.scheduler.adapter import AdapterLoadError, load_adapter
from kaban.scheduler.cron import cron_matches, cron_previous
from kaban.scheduler.models import DispatchRecord, JobContext, JobResult, ScheduledJob, SchedulerState
from kaban.scheduler.store import (
    SchedulerBusyError,
    SchedulerStateError,
    SchedulerStore,
    append_event,
    sanitize_exception,
)


_TERMINAL_RESULTS = {"success", "skipped", "missed", "blocked", "failed"}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now_utc должен быть timezone-aware")
    return value.astimezone(timezone.utc)


def latest_slot(job: ScheduledJob, now_utc: datetime) -> tuple[datetime, datetime]:
    now_utc = _aware_utc(now_utc)
    zone = ZoneInfo(job.timezone)
    local_now = now_utc.astimezone(zone)
    minute_now = local_now.replace(second=0, microsecond=0)
    if cron_matches(job.cron, minute_now):
        slot_local = minute_now
    else:
        slot_local = cron_previous(job.cron, minute_now)
    slot_utc = slot_local.astimezone(timezone.utc)
    return slot_utc, slot_local


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("scheduler timestamp должен быть timezone-aware")
    return parsed.astimezone(timezone.utc)


def _slot_local(job: ScheduledJob, slot_utc: datetime) -> datetime:
    return slot_utc.astimezone(ZoneInfo(job.timezone))


class SchedulerEngine:
    def __init__(self, registry, *, store: SchedulerStore | None = None, adapter_loader: Callable[[str], Callable] | None = None):
        self.registry = registry
        self.store = store or SchedulerStore()
        self.adapter_loader = adapter_loader or load_adapter

    def _next_retry(self, job: ScheduledJob, slot_utc: datetime, now_utc: datetime, attempt: int) -> str | None:
        policy = job.retry
        if policy.max_attempts <= 0 or attempt > policy.max_attempts:
            return None
        if policy.interval_minutes <= 0 or policy.window_minutes <= 0:
            return None
        deadline = slot_utc + timedelta(minutes=policy.window_minutes)
        candidate = now_utc + timedelta(minutes=policy.interval_minutes)
        if candidate >= deadline:
            return None
        return candidate.isoformat()

    def _event(self, state: SchedulerState, *, slot_id: str, result: str, attempt: int, trigger: str,
               started_at: str, finished_at: str, error_type: str | None = None) -> SchedulerState:
        event = {
            "slot_id": slot_id,
            "result": result,
            "attempt": attempt,
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        if error_type:
            event["error_type"] = error_type
        return append_event(state, event)

    def _dispatch(
        self,
        job: ScheduledJob,
        slot_utc: datetime,
        slot_local: datetime,
        *,
        trigger: str,
        now_utc: datetime,
        attempt: int,
        preserve_current: bool = False,
    ) -> DispatchRecord:
        now_utc = _aware_utc(now_utc)
        slot_id = f"manual:{slot_utc.isoformat()}" if trigger == "manual" else slot_utc.isoformat()
        try:
            with self.store.job_lock(job.project_id, job.job_id, slot_id, now_utc=now_utc):
                base_state = self.store.load_state(job.project_id, job.job_id)
                started_at = now_utc.isoformat()
                context = JobContext(
                    project_id=job.project_id,
                    job_id=job.job_id,
                    scheduled_for_utc=slot_utc,
                    scheduled_for_local=slot_local,
                    now_utc=now_utc,
                    trigger=trigger,
                    attempt=attempt,
                )
                result_name = "failed"
                message = ""
                error_payload = None
                next_retry = None
                try:
                    adapter = self.adapter_loader(job.adapter)
                except AdapterLoadError as exc:
                    error_payload = sanitize_exception(exc)
                    message = error_payload["message"]
                    result_name = "failed"
                else:
                    try:
                        result = adapter(job, context)
                    except Exception as exc:
                        error_payload = sanitize_exception(exc)
                        message = error_payload["message"]
                        result_name = "failed"
                        if trigger != "manual":
                            next_retry = self._next_retry(job, slot_utc, now_utc, attempt)
                    else:
                        if not isinstance(result, JobResult):
                            error_payload = sanitize_exception(TypeError("Scheduler adapter должен вернуть JobResult"))
                            message = error_payload["message"]
                            result_name = "failed"
                        elif result.outcome not in {"success", "skipped", "blocked"}:
                            error_payload = sanitize_exception(ValueError(f"Некорректный JobResult.outcome: {result.outcome}"))
                            message = error_payload["message"]
                            result_name = "failed"
                        else:
                            result_name = result.outcome
                            message = result.message
                            if result.outcome == "blocked" and result.retryable and trigger != "manual":
                                next_retry = self._next_retry(job, slot_utc, now_utc, attempt)
                finished_at = datetime.now(timezone.utc).isoformat()

                if preserve_current:
                    updated = self._event(
                        base_state, slot_id=slot_id, result=result_name, attempt=attempt, trigger=trigger,
                        started_at=started_at, finished_at=finished_at,
                        error_type=error_payload["type"] if error_payload else None,
                    )
                else:
                    updated = replace(
                        base_state,
                        slot_id=slot_id,
                        scheduled_for_utc=slot_utc.isoformat(),
                        last_result=result_name,
                        attempt=attempt,
                        started_at=started_at,
                        finished_at=finished_at,
                        next_retry_at=next_retry,
                        last_error=error_payload,
                    )
                    updated = self._event(
                        updated, slot_id=slot_id, result=result_name, attempt=attempt, trigger=trigger,
                        started_at=started_at, finished_at=finished_at,
                        error_type=error_payload["type"] if error_payload else None,
                    )
                self.store.save_state(updated)
                return DispatchRecord(job.project_id, job.job_id, slot_id, result_name, attempt, message)
        except SchedulerBusyError as exc:
            return DispatchRecord(job.project_id, job.job_id, slot_id, "busy", attempt, str(exc))
        except SchedulerStateError as exc:
            return DispatchRecord(job.project_id, job.job_id, slot_id, "failed", attempt, str(exc))

    def _process_job(self, job: ScheduledJob, now_utc: datetime) -> DispatchRecord | None:
        try:
            state = self.store.load_state(job.project_id, job.job_id)
        except SchedulerStateError as exc:
            return DispatchRecord(job.project_id, job.job_id, "unknown", "failed", 0, str(exc))

        # Старый открытый retry-slot всегда имеет приоритет над новым cron occurrence.
        if state.slot_id and state.next_retry_at and state.last_result in {"blocked", "failed"}:
            try:
                next_retry = _parse_iso(state.next_retry_at)
                slot_utc = _parse_iso(state.scheduled_for_utc)
            except ValueError as exc:
                return DispatchRecord(job.project_id, job.job_id, state.slot_id, "failed", state.attempt, str(exc))
            assert next_retry is not None and slot_utc is not None
            deadline = slot_utc + timedelta(minutes=job.retry.window_minutes)
            if now_utc < next_retry:
                return None
            if now_utc < deadline and state.attempt <= job.retry.max_attempts:
                return self._dispatch(
                    job, slot_utc, _slot_local(job, slot_utc), trigger="retry",
                    now_utc=now_utc, attempt=state.attempt + 1,
                )
            # Retry window закрыт: фиксируем terminal state и только затем разрешаем новый slot.
            state = replace(state, next_retry_at=None)
            self.store.save_state(state)

        slot_utc, slot_local = latest_slot(job, now_utc)
        slot_id = slot_utc.isoformat()
        if state.slot_id == slot_id and state.last_result in _TERMINAL_RESULTS and not state.next_retry_at:
            return None

        delay = now_utc - slot_utc
        if delay > timedelta(minutes=job.misfire_grace_minutes):
            started = finished = now_utc.isoformat()
            updated = replace(
                state,
                slot_id=slot_id,
                scheduled_for_utc=slot_id,
                last_result="missed",
                attempt=0,
                started_at=started,
                finished_at=finished,
                next_retry_at=None,
                last_error=None,
            )
            updated = self._event(updated, slot_id=slot_id, result="missed", attempt=0, trigger="schedule", started_at=started, finished_at=finished)
            self.store.save_state(updated)
            return DispatchRecord(job.project_id, job.job_id, slot_id, "missed", 0, "misfire grace exceeded")

        return self._dispatch(job, slot_utc, slot_local, trigger="schedule", now_utc=now_utc, attempt=1)

    def tick(self, now_utc: datetime, project_id: str | None = None) -> tuple[DispatchRecord, ...]:
        now_utc = _aware_utc(now_utc)
        records: list[DispatchRecord] = []
        for job in iter_scheduled_jobs(self.registry, project_id):
            try:
                record = self._process_job(job, now_utc)
            except Exception as exc:
                safe = sanitize_exception(exc)
                record = DispatchRecord(job.project_id, job.job_id, "unknown", "failed", 0, safe["message"])
            if record is not None:
                records.append(record)
        return tuple(records)

    def run_now(self, project_id: str, job_id: str, *, now_utc: datetime | None = None) -> DispatchRecord:
        now_utc = _aware_utc(now_utc or datetime.now(timezone.utc))
        jobs = [job for job in iter_scheduled_jobs(self.registry, project_id) if job.job_id == job_id]
        if not jobs:
            raise ValueError(f"Unknown enabled scheduler job: {project_id}/{job_id}")
        job = jobs[0]
        slot_utc = now_utc
        return self._dispatch(
            job, slot_utc, slot_utc.astimezone(ZoneInfo(job.timezone)), trigger="manual",
            now_utc=now_utc, attempt=1, preserve_current=True,
        )
