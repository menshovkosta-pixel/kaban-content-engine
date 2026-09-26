from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kaban.projects import ProjectConfigError, ProjectRegistry, load_project_config


def _write_project(tmp: Path, automation: str = "") -> Path:
    path = tmp / "project.yaml"
    path.write_text(
        f"""
id: demo
name: Demo
default_language: ru
supported_languages: [ru]
timezone: Pacific/Auckland
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
{automation}
""".strip() + "\n",
        encoding="utf-8",
    )
    return path


def test_missing_automation_is_disabled_and_backward_compatible():
    with TemporaryDirectory() as raw:
        cfg = load_project_config(_write_project(Path(raw)))
    assert cfg.automation.enabled is False
    assert cfg.automation.adapter is None
    assert cfg.automation.jobs == ()


def test_valid_automation_parses_jobs_and_opaque_params():
    automation = """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      enabled: true
      cron: "*/15 * * * *"
      params:
        source: primary
      misfire_grace_minutes: 20
      retry:
        interval_minutes: 5
        window_minutes: 60
        max_attempts: 4
"""
    with TemporaryDirectory() as raw:
        cfg = load_project_config(_write_project(Path(raw), automation))
    job = cfg.automation.jobs[0]
    assert job.id == "collect"
    assert job.params == {"source": "primary"}
    assert job.retry.max_attempts == 4


@pytest.mark.parametrize("cron", ["bad cron", "61 * * * *", "* * *"])
def test_invalid_cron_is_rejected(cron: str):
    automation = f"""
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      cron: "{cron}"
      params: {{}}
      misfire_grace_minutes: 0
      retry:
        interval_minutes: 0
        window_minutes: 0
        max_attempts: 0
"""
    with TemporaryDirectory() as raw:
        with pytest.raises(ProjectConfigError, match="cron"):
            load_project_config(_write_project(Path(raw), automation))


def test_duplicate_job_ids_are_rejected():
    automation = """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: same
      handler: one
      cron: "0 6 * * *"
    - id: same
      handler: two
      cron: "0 7 * * *"
"""
    with TemporaryDirectory() as raw:
        with pytest.raises(ProjectConfigError, match="duplicate|повтор"):
            load_project_config(_write_project(Path(raw), automation))


def test_enabled_automation_requires_adapter_and_jobs():
    for automation in (
        "automation:\n  enabled: true\n  jobs: []\n",
        "automation:\n  enabled: true\n  adapter: projects.demo.scheduler:run_job\n  jobs: []\n",
    ):
        with TemporaryDirectory() as raw:
            with pytest.raises(ProjectConfigError):
                load_project_config(_write_project(Path(raw), automation))


def test_params_must_be_mapping_and_retry_values_non_negative():
    bad_params = """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      cron: "0 6 * * *"
      params: [bad]
"""
    with TemporaryDirectory() as raw:
        with pytest.raises(ProjectConfigError, match="params"):
            load_project_config(_write_project(Path(raw), bad_params))
    bad_retry = """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      cron: "0 6 * * *"
      retry:
        interval_minutes: -1
"""
    with TemporaryDirectory() as raw:
        with pytest.raises(ProjectConfigError, match="interval_minutes"):
            load_project_config(_write_project(Path(raw), bad_retry))


def test_invalid_iana_timezone_is_rejected():
    with TemporaryDirectory() as raw:
        path = _write_project(Path(raw))
        text = path.read_text(encoding="utf-8").replace("Pacific/Auckland", "Mars/Olympus")
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ProjectConfigError, match="timezone"):
            load_project_config(path)


def test_iter_scheduled_jobs_omits_disabled_and_preserves_project_timezone(tmp_path: Path):
    from kaban.scheduler.config import iter_scheduled_jobs
    root = tmp_path / "projects"
    p = root / "demo"
    p.mkdir(parents=True)
    path = _write_project(p, """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      cron: "0 6 * * *"
      params: {source: primary}
    - id: "off"
      handler: collect
      enabled: false
      cron: "0 7 * * *"
""")
    assert path.exists()
    registry = ProjectRegistry(root)
    jobs = iter_scheduled_jobs(registry)
    assert len(jobs) == 1
    assert jobs[0].job_id == "collect"
    assert jobs[0].timezone == "Pacific/Auckland"
    assert jobs[0].params == {"source": "primary"}
