from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from kaban.projects import ProjectRegistry, load_project_config
from kaban.scheduler.engine import SchedulerEngine
from kaban.scheduler.models import JobResult
from kaban.scheduler.store import SchedulerStore


def _project_yaml(project_id: str, adapter: str, jobs: str) -> str:
    return f"""
id: {project_id}
name: {project_id}
default_language: ru
supported_languages: [ru]
timezone: UTC
ai:
  model: gpt-5.6-luna
  reasoning_effort: low
uniqueness:
  history_days: 90
  warning_threshold: 0.76
  hard_threshold: 0.80
  max_regeneration_attempts: 3
publication:
  telegram:
    album_group_size: 6
automation:
  enabled: true
  adapter: {adapter}
  jobs:
{jobs}
""".strip()+"\n"


def _write_project(root: Path, project_id: str, adapter: str, jobs: str):
    d=root/project_id; d.mkdir(parents=True)
    (d/"project.yaml").write_text(_project_yaml(project_id,adapter,jobs),encoding="utf-8")


def test_blocked_retry_same_slot_then_success_survives_engine_restart(tmp_path: Path):
    projects=tmp_path/"projects"
    jobs="""    - id: generate
      handler: generate
      cron: "0 6 * * *"
      misfire_grace_minutes: 30
    - id: publish
      handler: publish
      cron: "0 6 * * *"
      misfire_grace_minutes: 30
      retry:
        interval_minutes: 10
        window_minutes: 60
        max_attempts: 6
"""
    _write_project(projects,"demo","demo_accept:run",jobs)
    registry=ProjectRegistry(projects)
    state={"approved":False,"calls":[]}
    mod=ModuleType("demo_accept")
    def run(job,ctx):
        state["calls"].append((job.job_id,ctx.scheduled_for_utc,ctx.attempt))
        if job.handler=="publish" and not state["approved"]:
            return JobResult("blocked","waiting",True)
        return JobResult("success")
    mod.run=run
    runtime=tmp_path/"runtime"
    t0=datetime(2026,9,25,6,0,tzinfo=timezone.utc)
    with patch.dict(sys.modules,{"demo_accept":mod}):
        e1=SchedulerEngine(registry,store=SchedulerStore(runtime))
        r1=e1.tick(t0)
        assert [r.result for r in r1]==["success","blocked"]
        publish_slot=e1.store.load_state("demo","publish").slot_id
        assert e1.tick(t0+timedelta(minutes=9))==()
        r2=e1.tick(t0+timedelta(minutes=10))
        assert r2[0].result=="blocked"
        assert e1.store.load_state("demo","publish").slot_id==publish_slot
        state["approved"]=True
        e2=SchedulerEngine(registry,store=SchedulerStore(runtime))
        r3=e2.tick(t0+timedelta(minutes=20))
        assert r3[0].result=="success"
        assert e2.store.load_state("demo","publish").slot_id==publish_slot
        assert e2.tick(t0+timedelta(minutes=20))==()


def test_two_projects_isolate_failure(tmp_path: Path):
    projects=tmp_path/"projects"
    job="""    - id: work
      handler: work
      cron: "0 6 * * *"
      misfire_grace_minutes: 30
"""
    _write_project(projects,"a","adapter_a:run",job)
    _write_project(projects,"b","adapter_b:run",job)
    ma=ModuleType("adapter_a"); mb=ModuleType("adapter_b")
    def fail(job,ctx): raise RuntimeError("boom")
    def ok(job,ctx): return JobResult("success")
    ma.run=fail; mb.run=ok
    with patch.dict(sys.modules,{"adapter_a":ma,"adapter_b":mb}):
        e=SchedulerEngine(ProjectRegistry(projects),store=SchedulerStore(tmp_path/"runtime"))
        records=e.tick(datetime(2026,9,25,6,0,tzinfo=timezone.utc))
    assert [(r.project_id,r.result) for r in records]==[("a","failed"),("b","success")]


def test_core_has_no_direct_caelus_dependency_and_adapter_reuses_stage1():
    root=Path(__file__).resolve().parent.parent
    core="\n".join(p.read_text(encoding="utf-8") for p in (root/"kaban").rglob("*.py"))
    assert "projects.caelus" not in core
    adapter=(root/"projects"/"caelus"/"scheduler.py").read_text(encoding="utf-8")
    assert "run_generation_job" in adapter and "run_publication_job" in adapter
    assert "generate_bundle" not in adapter and "run_telegram_publisher" not in adapter


def test_shipped_caelus_scheduler_uses_confirmed_production_profile():
    root=Path(__file__).resolve().parent.parent
    cfg=load_project_config(root/"projects"/"caelus"/"project.yaml")
    assert cfg.automation.enabled is True
    assert cfg.timezone == "Pacific/Auckland"
    jobs = {job.id: job for job in cfg.automation.jobs}
    assert set(jobs) == {"generate_ru", "publish_ru"}
    assert jobs["generate_ru"].cron == "0 6 * * *"
    assert jobs["generate_ru"].params["language"] == "ru"
    assert jobs["publish_ru"].cron == "0 8 * * *"
    assert jobs["publish_ru"].params["language"] == "ru"
