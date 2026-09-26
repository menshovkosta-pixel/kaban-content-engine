from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

import yaml

from .schedule_projection import project_schedule


@dataclass(frozen=True)
class ConfigSyncResult:
    project_id: str
    config_hash: str
    jobs_synced: int
    slots_seen: int
    slots_created: int
    horizon_days: int


class _SelectedRegistry:
    def __init__(self, registry, project_id: str):
        self._registry = registry
        self._project = registry.get(project_id)

    def registered(self):
        return (self._project,)

    def get(self, project_id: str):
        if project_id != self._project.id:
            return self._registry.get(project_id)
        return self._project


def _job_payload(project, job) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "handler": job.handler,
        "cron": job.cron,
        "timezone": project.timezone,
        "params": dict(job.params),
        "misfire_grace_minutes": job.misfire_grace_minutes,
        "retry_policy": {
            "interval_minutes": job.retry.interval_minutes,
            "window_minutes": job.retry.window_minutes,
            "max_attempts": job.retry.max_attempts,
        },
        "enabled": bool(project.automation.enabled and job.enabled),
    }


def sync_project_config(registry, store, *, project_id: str, horizon_days: int = 35, from_utc: datetime | None = None) -> ConfigSyncResult:
    if horizon_days < 1:
        raise ValueError("horizon_days должен быть >= 1")
    project = registry.get(project_id)
    config_path = registry.directory(project_id) / "project.yaml"
    raw = config_path.read_bytes()
    config_hash = sha256(raw).hexdigest()
    config_json = yaml.safe_load(raw.decode("utf-8"))
    jobs = [_job_payload(project, job) for job in project.automation.jobs]
    store.sync_project_config(
        project_id=project.id,
        name=project.name,
        config_hash=config_hash,
        config_json=config_json,
        jobs=jobs,
    )
    projection = project_schedule(
        _SelectedRegistry(registry, project_id),
        store,
        from_utc=from_utc or datetime.now(timezone.utc),
        horizon_days=horizon_days,
    )
    return ConfigSyncResult(
        project_id=project.id,
        config_hash=config_hash,
        jobs_synced=len(jobs),
        slots_seen=projection.slots_seen,
        slots_created=projection.commands_created,
        horizon_days=horizon_days,
    )
