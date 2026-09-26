from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from kaban.scheduler.models import JobContext, RetryPolicy, ScheduledJob


def _job(handler: str, params: dict):
    return ScheduledJob(
        project_id="caelus", job_id=f"job_{handler}", handler=handler,
        cron="0 6 * * *", timezone="Pacific/Auckland", params=params,
        misfire_grace_minutes=30, retry=RetryPolicy(),
        adapter="projects.caelus.scheduler:run_job",
    )


def _ctx(local: datetime):
    utc=local.astimezone(timezone.utc)
    return JobContext("caelus","job",utc,local,utc,"schedule",1)


def test_target_day_uses_project_local_date_and_offset():
    from projects.caelus.scheduler import run_job
    local=datetime(2026,9,26,0,30,tzinfo=ZoneInfo("Pacific/Auckland"))
    state=SimpleNamespace(last_result="success", state="review_required")
    with patch("projects.caelus.scheduler.run_generation_job", return_value=state) as call:
        result=run_job(_job("generate", {"language":"ru","mode":"mock","target_date_offset_days":-1}), _ctx(local))
    call.assert_called_once_with("2026-09-25","ru",mode="mock",model=None,force=False)
    assert result.outcome=="success"


def test_generate_mapping_validation_and_skip():
    from projects.caelus.scheduler import run_job
    local=datetime(2026,9,26,6,0,tzinfo=ZoneInfo("Pacific/Auckland"))
    state=SimpleNamespace(last_result="skipped", state="review_required")
    with patch("projects.caelus.scheduler.run_generation_job", return_value=state) as call:
        result=run_job(_job("generate", {"language":"en"}), _ctx(local))
    call.assert_called_once_with("2026-09-26","en",mode="ai",model=None,force=False)
    assert result.outcome=="skipped"
    with pytest.raises(ValueError): run_job(_job("generate", {"language":"de"}), _ctx(local))
    with pytest.raises(ValueError): run_job(_job("generate", {"language":"ru","target_date_offset_days":True}), _ctx(local))
    with pytest.raises(ValueError): run_job(_job("generate", {"language":"ru","mode":"weird"}), _ctx(local))
    with pytest.raises(ValueError): run_job(_job("unknown", {}), _ctx(local))


def test_publish_maps_preconditions_and_busy_to_retryable_blocked():
    from projects.caelus.scheduler import run_job
    from projects.caelus.automation import AutomationPreconditionError
    from projects.caelus.automation_store import AutomationBusyError
    local=datetime(2026,9,26,8,0,tzinfo=ZoneInfo("Pacific/Auckland"))
    job=_job("publish", {"language":"ru"})
    with patch("projects.caelus.scheduler.run_publication_job", side_effect=AutomationPreconditionError("wait approval")):
        r=run_job(job,_ctx(local))
    assert r.outcome=="blocked" and r.retryable is True
    with patch("projects.caelus.scheduler.run_publication_job", side_effect=AutomationBusyError("busy")):
        r=run_job(job,_ctx(local))
    assert r.outcome=="blocked" and r.retryable is True


def test_publish_success_skip_and_no_force_bypass():
    from projects.caelus.scheduler import run_job
    local=datetime(2026,9,26,8,0,tzinfo=ZoneInfo("Pacific/Auckland"))
    job=_job("publish", {"language":"ru"})
    for last_result, expected in [("success","success"),("skipped","skipped")]:
        state=SimpleNamespace(last_result=last_result, state="published")
        with patch("projects.caelus.scheduler.run_publication_job", return_value=state) as call:
            r=run_job(job,_ctx(local))
        call.assert_called_once_with("2026-09-26","ru",dry_run=False,force=False)
        assert r.outcome==expected
