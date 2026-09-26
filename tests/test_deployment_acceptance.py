from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_production_runbook_contains_canonical_commands_and_safety_rules():
    text = (ROOT / "deploy" / "README_PRODUCTION_RU.md").read_text(encoding="utf-8")
    required = [
        "docker compose up -d --build",
        "docker compose ps",
        "docker compose logs -f scheduler",
        "docker compose logs -f admin",
        "docker compose exec scheduler python scheduler.py list",
        "docker compose exec scheduler python scheduler.py status",
        "docker compose exec scheduler python scheduler.py run-now",
        "docker compose stop",
        "docker compose restart",
        "python deploy/bootstrap_data.py",
        "systemctl enable --now docker",
        "data/generated",
        "data/runtime",
    ]
    for item in required:
        assert item in text
    assert "docker-compose" not in text
    assert "down -v" not in text


def test_runbook_documents_backup_and_non_destructive_upgrade():
    text = (ROOT / "deploy" / "README_PRODUCTION_RU.md").read_text(encoding="utf-8")
    assert "tar -czf" in text
    assert "docker compose build --pull" in text
    assert "docker compose up -d --remove-orphans" in text
    assert "не удал" in text.lower()


def test_stage3_does_not_change_caelus_schedule():
    project = yaml.safe_load((ROOT / "projects" / "caelus" / "project.yaml").read_text(encoding="utf-8"))
    jobs = {job["id"]: job for job in project["automation"]["jobs"]}
    assert project["timezone"] == "Pacific/Auckland"
    assert jobs["generate_ru"]["cron"] == "0 6 * * *"
    assert jobs["publish_ru"]["cron"] == "0 8 * * *"
    assert set(jobs) == {"generate_ru", "publish_ru"}


def test_scheduler_completed_slot_and_retry_state_survive_new_engine_instance(tmp_path, monkeypatch):
    import sys
    from datetime import datetime, timedelta, timezone
    from types import ModuleType
    from unittest.mock import patch

    from kaban.projects import ProjectRegistry
    from kaban.scheduler.engine import SchedulerEngine
    from kaban.scheduler.models import JobResult

    monkeypatch.setenv("KABAN_RUNTIME_DIR", str(tmp_path / "runtime"))
    projects = tmp_path / "projects"
    project_dir = projects / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(
        """
id: demo
name: demo
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
  adapter: deploy_restart_adapter:run
  jobs:
    - id: generate
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
""".strip() + "\n",
        encoding="utf-8",
    )

    state = {"approved": False, "calls": []}
    module = ModuleType("deploy_restart_adapter")

    def run(job, ctx):
        state["calls"].append((job.job_id, ctx.attempt))
        if job.handler == "publish" and not state["approved"]:
            return JobResult("blocked", "waiting", True)
        return JobResult("success")

    module.run = run
    t0 = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
    registry = ProjectRegistry(projects)

    with patch.dict(sys.modules, {"deploy_restart_adapter": module}):
        first = SchedulerEngine(registry)
        records = first.tick(t0)
        assert [record.result for record in records] == ["success", "blocked"]
        publish_before = first.store.load_state("demo", "publish")
        assert publish_before.next_retry_at is not None
        assert state["calls"] == [("generate", 1), ("publish", 1)]

        second = SchedulerEngine(registry)
        assert second.tick(t0) == ()
        assert state["calls"] == [("generate", 1), ("publish", 1)]
        publish_after = second.store.load_state("demo", "publish")
        assert publish_after.next_retry_at == publish_before.next_retry_at

        state["approved"] = True
        retry_records = second.tick(t0 + timedelta(minutes=10))
        assert [record.result for record in retry_records] == ["success"]
        assert state["calls"][-1] == ("publish", 2)


def test_stage4_serverless_runbook_has_ordered_cutover_and_no_implicit_live_send():
    text = (ROOT / "deploy" / "README_SERVERLESS_RU.md").read_text(encoding="utf-8")
    ordered = [
        "1. Создать один Supabase Project",
        "2. Применить SQL migrations",
        "3. Создать один private Cloudflare R2 bucket",
        "4. Настроить Cloudflare Worker",
        "5. Настроить GitHub repository secrets",
        "6. Развернуть Worker",
        "7. Выполнить migration dry-run",
        "8. Проверить unknown/unmapped",
        "9. Выполнить migration apply",
        "10. Проверить hashes/read-back",
        "11. Выполнить `sync-config`",
        "12. Выполнить cloud mock generation acceptance",
        "13. Approve через cloud Review Console",
        "14. Выполнить Telegram dry-run",
        "15. Реальную Telegram-публикацию выполнять только после явного разрешения",
        "16. Сохранить local source backup до принятия cutover",
    ]
    positions = [text.index(item) for item in ordered]
    assert positions == sorted(positions)
    for command in (
        "python cloud_runtime.py sync-config --project caelus --horizon-days 35",
        "python cloud_runtime.py status --project caelus",
        "python cloud_runtime.py health --project caelus",
        "python cloud_runtime.py migrate-local",
        "python cloud_runtime.py verify-migration",
        "python cloud_runtime.py export-local",
    ):
        assert command in text
    assert "Telegram posts не откатываются" in text
