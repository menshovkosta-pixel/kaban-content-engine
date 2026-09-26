from __future__ import annotations

from kaban.projects import ProjectRegistry
from kaban.scheduler.config import iter_scheduled_jobs


def test_caelus_production_schedule_is_ru_only_and_uses_auckland_time():
    registry = ProjectRegistry()
    project = registry.get("caelus")

    assert project.timezone == "Pacific/Auckland"
    assert project.automation.enabled is True
    assert project.automation.adapter == "projects.caelus.scheduler:run_job"

    jobs = {job.job_id: job for job in iter_scheduled_jobs(registry, "caelus")}
    assert set(jobs) == {"generate_ru", "publish_ru"}

    generate = jobs["generate_ru"]
    assert generate.handler == "generate"
    assert generate.cron == "0 6 * * *"
    assert generate.timezone == "Pacific/Auckland"
    assert generate.params == {
        "language": "ru",
        "mode": "ai",
        "target_date_offset_days": 0,
    }
    assert generate.misfire_grace_minutes == 30
    assert generate.retry.interval_minutes == 10
    assert generate.retry.window_minutes == 120
    assert generate.retry.max_attempts == 12

    publish = jobs["publish_ru"]
    assert publish.handler == "publish"
    assert publish.cron == "0 8 * * *"
    assert publish.timezone == "Pacific/Auckland"
    assert publish.params == {
        "language": "ru",
        "target_date_offset_days": 0,
    }
    assert publish.misfire_grace_minutes == 30
    assert publish.retry.interval_minutes == 10
    assert publish.retry.window_minutes == 180
    assert publish.retry.max_attempts == 18


def test_caelus_production_schedule_has_no_en_jobs():
    registry = ProjectRegistry()
    jobs = iter_scheduled_jobs(registry, "caelus")
    assert all(job.params.get("language") == "ru" for job in jobs)
