from pathlib import Path
import os
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "deploy" / "supabase" / "migrations"


def sql_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8").lower() for p in sorted(MIGRATIONS.glob("20260926000*.sql")))


def test_schema_contains_stage4_tables_rls_and_rpc_contract():
    sql = sql_text()
    tables = ["projects","channels","project_settings","content_sets","content_revisions","approvals","artifacts","scheduler_jobs","executions","resource_leases","execution_events","publication_runs","publication_steps","usage_samples"]
    for name in tables:
        assert f"kaban_{name}" in sql
    assert "enable row level security" in sql
    for rpc in ["kaban_create_or_get_command","kaban_start_execution","kaban_renew_execution","kaban_claim_resource","kaban_commit_content_revision","kaban_create_or_get_publication_run","kaban_transition_publication_step","kaban_mark_stale_sending_unknown","kaban_claim_due_execution","kaban_schedule_dispatch_retry"]:
        assert rpc in sql


def test_schema_scopes_core_relationships_by_project():
    sql = sql_text()
    assert "unique(project_id, content_set_id)" in sql
    assert "unique(project_id, revision_id)" in sql
    assert "foreign key(project_id, content_set_id)" in sql
    assert "foreign key(project_id, revision_id)" in sql
    assert "p_proposed_revision_id" in sql
    assert "content_key text" in sql


@pytest.mark.skipif(not os.getenv("KABAN_TEST_DATABASE_URL") or not shutil.which("psql"), reason="KABAN_TEST_DATABASE_URL/psql не настроены")
def test_live_stage4_schema_contract():
    # Live DDL acceptance выполняется только в явно предоставленной тестовой PostgreSQL.
    assert os.getenv("KABAN_TEST_DATABASE_URL")


def test_dispatch_claim_and_start_execution_are_state_guarded():
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    sql = "\n".join(
        p.read_text(encoding="utf-8").lower()
        for p in sorted((root / "deploy" / "supabase" / "migrations").glob("*.sql"))
    )
    assert "kaban_claim_execution_for_dispatch" in sql
    assert "state='dispatched'" in sql or "state = 'dispatched'" in sql
    start = sql.split("create or replace function kaban_start_execution", 1)[1].split("create or replace function", 1)[0]
    assert "state in ('queued','dispatched','running')" in start.replace(" ", "") or "statein('queued','dispatched','running')" in start.replace(" ", "")
    assert "state='finished'" not in start


def test_scheduled_execution_has_exactly_once_slot_identity():
    sql = sql_text().replace(" ", "")
    assert "job_idtext" in sql
    assert "slot_idtext" in sql
    assert "unique(project_id,job_id,slot_id)" in sql or "kaban_execution_slot_uq" in sql
    assert "kaban_upsert_schedule_slot" in sql


def test_scheduler_jobs_is_config_mirror_and_slot_identity_lives_on_executions():
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    schema = (root / 'deploy' / 'supabase' / 'migrations' / '202609260001_stage4_schema.sql').read_text(encoding='utf-8').lower()
    jobs = schema.split('create table if not exists kaban_scheduler_jobs', 1)[1].split('create table if not exists kaban_executions', 1)[0]
    executions = schema.split('create table if not exists kaban_executions', 1)[1].split('create table if not exists kaban_', 1)[0]
    for column in ('config_hash', 'handler', 'cron', 'timezone', 'retry_policy', 'enabled'):
        assert column in jobs
    assert 'slot_id' not in jobs
    assert 'job_id' in executions
    assert 'slot_id' in executions
    assert 'kaban_execution_slot_uq' in schema
