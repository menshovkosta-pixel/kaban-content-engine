from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from kaban.projects import ProjectAutomationConfig, ProjectJobConfig
from kaban.scheduler.models import JobResult, RetryPolicy
from kaban.scheduler.store import SchedulerStore


def _registry(*jobs: ProjectJobConfig, timezone_name="Pacific/Auckland"):
    project = SimpleNamespace(
        id="demo",
        timezone=timezone_name,
        automation=ProjectAutomationConfig(enabled=True, adapter="demo.adapter:run", jobs=tuple(jobs)),
    )
    class R:
        def registered(self): return (project,)
        def get(self, project_id):
            if project_id != "demo": raise KeyError(project_id)
            return project
    return R()


def _job(job_id="collect", cron="0 6 * * *", grace=30, retry=None):
    return ProjectJobConfig(
        id=job_id, handler=job_id, enabled=True, cron=cron, params={},
        misfire_grace_minutes=grace, retry=retry or RetryPolicy(),
    )


def test_due_slot_dispatches_once_and_persists_utc_identity(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    calls=[]
    def adapter(job, ctx):
        calls.append(ctx)
        return JobResult("success", "ok")
    engine=SchedulerEngine(_registry(_job()), store=SchedulerStore(tmp_path), adapter_loader=lambda _: adapter)
    now=datetime(2026,9,25,18,0,tzinfo=timezone.utc)  # 06:00 NZST next day
    first=engine.tick(now)
    second=engine.tick(now)
    assert len(first)==1 and first[0].result=="success"
    assert second==()
    assert len(calls)==1
    state=engine.store.load_state("demo","collect")
    assert state.slot_id==now.isoformat()
    assert state.scheduled_for_utc==now.isoformat()


def test_timezone_wall_clock_and_dst_slots_are_unique(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine, latest_slot
    from kaban.scheduler.config import iter_scheduled_jobs
    engine=SchedulerEngine(_registry(_job(cron="30 0 * * *")), store=SchedulerStore(tmp_path), adapter_loader=lambda _: (lambda j,c: JobResult("success")))
    job=iter_scheduled_jobs(engine.registry)[0]
    a_utc,a_local=latest_slot(job, datetime(2026,9,25,12,30,tzinfo=timezone.utc))
    assert a_local.hour==0 and a_local.minute==30
    assert a_utc.tzinfo is not None
    # Around NZ DST transition, actual cron candidates still map to distinct UTC identities.
    b_utc,b_local=latest_slot(job, datetime(2026,9,27,12,30,tzinfo=timezone.utc))
    assert b_local.hour==0 and b_local.minute==30
    assert a_utc != b_utc


def test_misfire_within_grace_runs_beyond_grace_is_recorded_once(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    calls=[]
    adapter=lambda j,c: (calls.append(c) or JobResult("success"))
    now=datetime(2026,9,25,18,20,tzinfo=timezone.utc)
    engine=SchedulerEngine(_registry(_job(grace=30)), store=SchedulerStore(tmp_path/"a"), adapter_loader=lambda _: adapter)
    assert engine.tick(now)[0].result=="success"
    assert len(calls)==1
    calls.clear()
    late=SchedulerEngine(_registry(_job(grace=10)), store=SchedulerStore(tmp_path/"b"), adapter_loader=lambda _: adapter)
    assert late.tick(now)[0].result=="missed"
    assert late.tick(now)==()
    assert calls==[]


def test_blocked_retries_exact_count_and_window(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    calls=[]
    def adapter(job,ctx):
        calls.append(ctx.attempt)
        return JobResult("blocked","awaiting approval",True)
    policy=RetryPolicy(interval_minutes=10, window_minutes=30, max_attempts=2)
    engine=SchedulerEngine(_registry(_job(retry=policy)), store=SchedulerStore(tmp_path), adapter_loader=lambda _: adapter)
    t0=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    assert engine.tick(t0)[0].result=="blocked"
    assert engine.tick(t0+timedelta(minutes=9))==()
    assert engine.tick(t0+timedelta(minutes=10))[0].attempt==2
    assert engine.tick(t0+timedelta(minutes=20))[0].attempt==3
    assert engine.tick(t0+timedelta(minutes=30))==()
    assert calls==[1,2,3]


def test_max_attempts_zero_means_no_retry(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    calls=[]
    adapter=lambda j,c: (calls.append(c.attempt) or JobResult("blocked","wait",True))
    p=RetryPolicy(interval_minutes=1, window_minutes=10, max_attempts=0)
    e=SchedulerEngine(_registry(_job(retry=p)), store=SchedulerStore(tmp_path), adapter_loader=lambda _: adapter)
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    e.tick(t); e.tick(t+timedelta(minutes=1))
    assert calls==[1]


def test_older_retry_slot_finishes_before_newer_cron_slot(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    results=[JobResult("blocked","wait",True), JobResult("success","approved")]
    calls=[]
    def adapter(j,c):
        calls.append((c.scheduled_for_utc,c.attempt))
        return results.pop(0) if results else JobResult("success")
    p=RetryPolicy(interval_minutes=70, window_minutes=180, max_attempts=2)
    e=SchedulerEngine(_registry(_job(cron="0 * * * *", retry=p)), store=SchedulerStore(tmp_path), adapter_loader=lambda _: adapter)
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    e.tick(t)
    # 19:00 cron exists, but old 18:00 slot is waiting until 19:10.
    assert e.tick(t+timedelta(minutes=60))==()
    rec=e.tick(t+timedelta(minutes=70))[0]
    assert rec.attempt==2
    assert calls[1][0]==t


def test_failure_isolated_and_secret_sanitized(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    jobs=(_job("a"), _job("b"))
    def loader(_):
        def adapter(job,ctx):
            if job.job_id=="a":
                raise RuntimeError("OPENAI_API_KEY=sk-secret provider down")
            return JobResult("success")
        return adapter
    e=SchedulerEngine(_registry(*jobs), store=SchedulerStore(tmp_path), adapter_loader=loader)
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    records=e.tick(t)
    assert [r.result for r in records]==["failed","success"]
    assert "sk-secret" not in str(e.store.load_state("demo","a").last_error)


def test_corrupt_state_and_busy_lock_do_not_stop_other_jobs(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    jobs=(_job("a"), _job("b"))
    store=SchedulerStore(tmp_path)
    bad=store.state_path("demo","a"); bad.parent.mkdir(parents=True); bad.write_text("{broken",encoding="utf-8")
    calls=[]
    e=SchedulerEngine(_registry(*jobs), store=store, adapter_loader=lambda _: (lambda j,c: (calls.append(j.job_id) or JobResult("success"))))
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    records=e.tick(t)
    assert records[0].result=="failed" and records[1].result=="success"
    assert calls==["b"]


def test_run_now_uses_distinct_manual_slot(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    calls=[]
    e=SchedulerEngine(_registry(_job()), store=SchedulerStore(tmp_path), adapter_loader=lambda _: (lambda j,c: (calls.append(c.trigger) or JobResult("success"))))
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    r1=e.run_now("demo","collect",now_utc=t)
    r2=e.run_now("demo","collect",now_utc=t+timedelta(seconds=1))
    assert r1.slot_id.startswith("manual:") and r1.slot_id != r2.slot_id
    assert calls==["manual","manual"]


def test_adapter_load_error_is_terminal_even_when_retry_policy_exists(tmp_path: Path):
    from kaban.scheduler.adapter import AdapterLoadError
    from kaban.scheduler.engine import SchedulerEngine
    p=RetryPolicy(interval_minutes=5, window_minutes=60, max_attempts=5)
    e=SchedulerEngine(
        _registry(_job(retry=p)),
        store=SchedulerStore(tmp_path),
        adapter_loader=lambda _: (_ for _ in ()).throw(AdapterLoadError("bad adapter")),
    )
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    record=e.tick(t)[0]
    assert record.result=="failed"
    state=e.store.load_state("demo","collect")
    assert state.next_retry_at is None
    assert e.tick(t+timedelta(minutes=5))==()


def test_active_job_lock_returns_busy_without_adapter_call(tmp_path: Path):
    from kaban.scheduler.engine import SchedulerEngine
    store=SchedulerStore(tmp_path)
    t=datetime(2026,9,25,18,0,tzinfo=timezone.utc)
    calls=[]
    e=SchedulerEngine(_registry(_job()),store=store,adapter_loader=lambda _: (lambda j,c: (calls.append(1) or JobResult("success"))))
    with store.job_lock("demo","collect",t.isoformat(),now_utc=t):
        record=e.tick(t)[0]
    assert record.result=="busy"
    assert calls==[]
