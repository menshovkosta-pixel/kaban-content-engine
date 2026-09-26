from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from kaban.scheduler.cron import cron_next
from .models import ExecutionCommand
from .adapters import load_cloud_adapter


@dataclass(frozen=True)
class ProjectionResult:
    slots_seen: int
    commands_created: int
    horizon_end_utc: datetime


def project_schedule(registry, store, *, from_utc: datetime, horizon_days: int = 35) -> ProjectionResult:
    if from_utc.tzinfo is None:
        raise ValueError("from_utc должен содержать timezone")
    start = from_utc.astimezone(timezone.utc)
    end = start + timedelta(days=horizon_days)
    seen = 0
    created = 0
    for project in registry.registered():
        zone = ZoneInfo(project.timezone)
        for job in project.automation.jobs:
            if not project.automation.enabled or not job.enabled:
                continue
            cursor_local = start.astimezone(zone) - timedelta(minutes=1)
            while True:
                local_slot = cron_next(job.cron, cursor_local)
                slot_utc = local_slot.astimezone(timezone.utc)
                if slot_utc > end:
                    break
                slot_id = slot_utc.isoformat()
                language = str(job.params.get("language") or project.default_language)
                content_key = f"{local_slot.date().isoformat()}:{language}" if language else None
                command = ExecutionCommand(
                    execution_id=uuid4(), project_id=project.id, operation=str(job.handler),
                    content_key=content_key, content_set_id=None, expected_version=None,
                    payload={"job_id":job.id,"slot_id":slot_id,"scheduled_for":slot_utc.isoformat(),**dict(job.params)},
                    requested_by="scheduler", idempotency_key=f"schedule:{job.id}:{slot_id}",
                )
                wait_condition = None
                cloud_config = getattr(project, "cloud", None)
                entrypoint = getattr(cloud_config, "adapter", None)
                if entrypoint:
                    adapter = load_cloud_adapter(entrypoint, project_id=project.id)
                    resolver = getattr(adapter, "schedule_wait_condition", None)
                    if callable(resolver):
                        wait_condition = resolver(job=job, content_key=content_key, local_slot=local_slot)
                seen += 1
                if hasattr(store, "upsert_schedule_slot"):
                    was_created = bool(store.upsert_schedule_slot(
                        command=command, job_id=job.id, slot_id=slot_id, scheduled_for=slot_utc,
                        misfire_deadline_at=slot_utc + timedelta(minutes=job.misfire_grace_minutes),
                        retry_deadline_at=slot_utc + timedelta(minutes=job.retry.window_minutes),
                        wait_condition=wait_condition,
                    ))
                    created += int(was_created)
                elif hasattr(store, "create_or_get_command"):
                    store.create_or_get_command(command)
                    created += 1
                cursor_local = local_slot
    return ProjectionResult(seen, created, end)
