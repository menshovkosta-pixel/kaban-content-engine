from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from kaban.cloud.cli import build_parser
from kaban.cloud.config_sync import sync_project_config
from kaban.cloud.publication import LocalPublicationCheckpointClient
from kaban.cloud.r2 import R2ArtifactStore
from kaban.cloud.usage import UsageSample, quota_level, serialize_usage
from kaban.projects import ProjectRegistry

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "deploy" / "supabase" / "migrations"


class SyncStore:
    def __init__(self):
        self.config_calls = []
        self.slots = []

    def sync_project_config(self, **kwargs):
        self.config_calls.append(kwargs)

    def upsert_schedule_slot(self, **kwargs):
        self.slots.append(kwargs)
        return True


def test_stage4_cli_exposes_sync_status_and_health_commands():
    parser = build_parser()
    assert parser.parse_args(["sync-config", "--project", "caelus"]).command == "sync-config"
    assert parser.parse_args(["status", "--project", "caelus"]).command == "status"
    assert parser.parse_args(["health", "--project", "caelus"]).command == "health"


def test_sync_config_hashes_project_yaml_and_projects_only_selected_project():
    registry = ProjectRegistry(ROOT / "projects")
    store = SyncStore()
    result = sync_project_config(
        registry, store, project_id="caelus", horizon_days=35,
        from_utc=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    assert len(store.config_calls) == 1
    call = store.config_calls[0]
    assert call["project_id"] == "caelus" and len(call["config_hash"]) == 64
    assert [job["job_id"] for job in call["jobs"]] == ["generate_ru", "publish_ru"]
    assert result.project_id == "caelus" and result.horizon_days == 35
    assert store.slots and {item["job_id"] for item in store.slots} == {"generate_ru", "publish_ru"}
    assert all(item["command"].project_id == "caelus" for item in store.slots)


def test_core_has_no_caelus_import_and_local_mode_has_no_cloud_requirement(monkeypatch):
    for path in (ROOT / "kaban").rglob("*.py"):
        assert "projects.caelus" not in path.read_text(encoding="utf-8")
    monkeypatch.delenv("KABAN_PERSISTENCE", raising=False)
    monkeypatch.delenv("KABAN_SUPABASE_URL", raising=False)
    from kaban.cloud.config import CloudConfig
    assert CloudConfig.from_env() is None


def test_synthetic_second_project_loads_without_caelus_dependency(tmp_path):
    template = """id: {id}\nname: {name}\ndefault_language: ru\nsupported_languages: [ru]\ntimezone: UTC\nai:\n  model: mock\n  reasoning_effort: low\nuniqueness:\n  history_days: 30\n  warning_threshold: 0.7\n  hard_threshold: 0.8\n  max_regeneration_attempts: 1\npublication:\n  telegram:\n    album_group_size: 6\nautomation:\n  enabled: false\n  jobs: []\n"""
    for project_id in ("alpha", "beta"):
        directory = tmp_path / project_id
        directory.mkdir()
        (directory / "project.yaml").write_text(template.format(id=project_id, name=project_id.upper()), encoding="utf-8")
    registry = ProjectRegistry(tmp_path)
    assert [project.id for project in registry.registered()] == ["alpha", "beta"]
    assert all(project.cloud.adapter is None for project in registry.registered())


def test_docker_local_persistence_contract_remains_present():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "generated" in compose and "runtime" in compose and "restart: unless-stopped" in compose
    assert "KABAN_SUPABASE_SERVICE_KEY" not in compose


def test_cloud_runner_uses_control_store_instead_of_canonical_json_files():
    text = (ROOT / "kaban" / "cloud" / "runner.py").read_text(encoding="utf-8")
    assert "store.load_snapshot" in text
    assert "store.commit_changes" in text
    assert "write_json(" not in text


def test_r2_keys_are_project_prefixed_and_revision_immutable():
    store = R2ArtifactStore(bucket="fake", s3_client=object())
    content_set = UUID("11111111-1111-4111-8111-111111111111")
    revision = UUID("22222222-2222-4222-8222-222222222222")
    key = store.object_key("caelus", content_set, revision, "card_png", "aries.png")
    assert key == f"projects/caelus/content/{content_set}/revisions/{revision}/card_png/aries.png"


def test_sql_enforces_fences_unique_slots_and_exact_approval_identity():
    text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
    assert "kaban_execution_slot_uq" in text and "project_id, job_id, slot_id" in text
    assert "fence_token" in text and "lease_owner" in text
    assert "approved_revision_id=p_revision_id" in text
    assert "approved_content_hash=p_content_hash" in text


def test_worker_checks_wait_condition_before_github_claim_and_has_no_provider_secrets():
    orchestrator = (ROOT / "deploy/cloudflare/worker/src/orchestrator.ts").read_text(encoding="utf-8")
    assert orchestrator.index("waitSatisfied") < orchestrator.index("claimDue") < orchestrator.index("github.dispatch")
    worker_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "deploy/cloudflare/worker/src").glob("*.ts"))
    for secret in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert secret not in worker_text


def test_publication_sent_is_skipped_and_unknown_delivery_requires_manual(tmp_path):
    run_id = UUID("33333333-3333-4333-8333-333333333333")
    client = LocalPublicationCheckpointClient(tmp_path / "publication.json")
    assert client.begin_step(run_id, "media:1", "fp").action == "send"
    client.mark_sent(run_id, "media:1", {"message_ids": [1]})
    assert client.begin_step(run_id, "media:1", "fp").action == "skip"
    assert client.begin_step(run_id, "text:1", "fp2").action == "send"
    client.mark_unknown(run_id, "text:1", {"type": "Timeout"})
    assert client.begin_step(run_id, "text:1", "fp2").action == "manual"


def test_usage_quality_and_thresholds_are_explicit():
    assert [quota_level(value, 100) for value in (69, 70, 85, 95)] == ["ok", "warning", "high", "critical"]
    exact = serialize_usage(UsageSample("db_storage", 10, "bytes", "db_exact", limit=100))
    estimated = serialize_usage(UsageSample("egress", 10, "bytes", "estimated", limit=100))
    assert exact["quality"] == "db_exact" and estimated["quality"] == "estimated"


def test_release_hygiene_ignores_superpowers_scratch_workspace():
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".superpowers/" in patterns
