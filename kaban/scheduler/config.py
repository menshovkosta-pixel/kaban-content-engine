from __future__ import annotations

from kaban.projects import ProjectRegistry
from kaban.scheduler.models import ScheduledJob


def iter_scheduled_jobs(registry: ProjectRegistry, project_id: str | None = None) -> tuple[ScheduledJob, ...]:
    projects = (registry.get(project_id),) if project_id else registry.registered()
    jobs: list[ScheduledJob] = []
    for project in projects:
        automation = project.automation
        if not automation.enabled or not automation.adapter:
            continue
        for item in automation.jobs:
            if not item.enabled:
                continue
            jobs.append(ScheduledJob(
                project_id=project.id,
                job_id=item.id,
                handler=item.handler,
                cron=item.cron,
                timezone=project.timezone,
                params=dict(item.params),
                misfire_grace_minutes=item.misfire_grace_minutes,
                retry=item.retry,
                adapter=automation.adapter,
            ))
    return tuple(jobs)
