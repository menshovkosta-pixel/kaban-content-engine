from pathlib import Path
from uuid import UUID

from kaban.cloud.models import ChangeSet, ExecutionCommand
from kaban.cloud.supabase import SupabaseControlStore


CONTENT_SET_ID = UUID("11111111-1111-4111-8111-111111111111")
REVISION_ID = UUID("22222222-2222-4222-8222-222222222222")
EXECUTION_ID = UUID("33333333-3333-4333-8333-333333333333")


class CapturingStore(SupabaseControlStore):
    def __init__(self):
        # HTTP здесь не используется: перехватываем RPC напрямую.
        super().__init__(
            "https://example.supabase.co",
            "test-service-key",
        )
        self.rpc_calls = []

    def _rpc(self, name, payload):
        self.rpc_calls.append((name, payload))

        return [{
            "content_set_id": str(CONTENT_SET_ID),
            "revision_id": str(REVISION_ID),
            "version": 1,
        }]


def _command():
    return ExecutionCommand(
        EXECUTION_ID,
        "caelus",
        "generate",
        "2099-01-16:ru",
        None,
        None,
        {
            "mode": "mock",
            "language": "ru",
            "date": "2099-01-16",
        },
        "operator:test",
        "regression:supabase-first-generation",
    )


def _changes():
    return ChangeSet(
        content_set_id=CONTENT_SET_ID,
        expected_version=0,
        new_payload={
            "iso_date": "2099-01-16",
            "language": "ru",
            "content_hash": "content-hash-001",
        },
    )


def test_commit_passes_claimed_resource_key_and_uploaded_artifacts_to_rpc():
    """
    Runtime commit должен использовать ровно тот resource_key,
    по которому runner получил lease, и передавать metadata уже
    загруженных immutable R2 artifacts в атомарный RPC.
    """

    store = CapturingStore()

    artifact_rows = (
        {
            "kind": "card_png",
            "logical_name": "aries.png",
            "r2_key": (
                "projects/caelus/content/"
                f"{CONTENT_SET_ID}/revisions/{REVISION_ID}/"
                "card_png/aries.png"
            ),
            "sha256": "abc123",
            "size_bytes": 12345,
            "mime_type": "image/png",
            "metadata": {},
        },
    )

    result = store.commit_changes(
        _command(),
        _changes(),
        execution_fence=7,
        resource_fence=11,
        lease_owner="runner:test",
        proposed_revision_id=REVISION_ID,
        resource_key="content-key:2099-01-16:ru",
        artifact_rows=artifact_rows,
    )

    assert result.content_set_id == CONTENT_SET_ID
    assert result.revision_id == REVISION_ID
    assert result.version == 1

    assert len(store.rpc_calls) == 1

    rpc_name, payload = store.rpc_calls[0]

    assert rpc_name == "kaban_commit_content_revision"

    # Нельзя заново конструировать другой lease identity из UUID.
    assert payload["p_resource_key"] == "content-key:2099-01-16:ru"

    # RPC должен знать canonical logical identity нового content set.
    assert payload["p_content_key"] == "2099-01-16:ru"

    # Durable R2 objects должны регистрироваться той же DB-транзакцией.
    assert payload["p_artifacts"] == list(artifact_rows)


def _commit_function_sql() -> str:
    root = Path(__file__).resolve().parents[1]
    migrations = root / "deploy" / "supabase" / "migrations"

    sql = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(migrations.glob("*.sql"))
    )

    start_marker = "create or replace function kaban_commit_content_revision("

    # ????????? ????????? ??????????? ??????????? RPC.
    # ??? ????????? ?????????? production schema ????? migration,
    # ?? ??????????? ??? ??????????? ???????????? migrations.
    start = sql.rfind(start_marker)

    assert start >= 0, "kaban_commit_content_revision definition not found"

    next_function = sql.find(
        "create or replace function ",
        start + len(start_marker),
    )

    if next_function < 0:
        return sql[start:]

    return sql[start:next_function]


def test_commit_rpc_atomically_creates_first_content_set_and_registers_artifacts():
    """
    Первая генерация не может зависеть от отдельного pre-create RPC:
    content set, revision и artifact rows должны стать canonical
    в одной PostgreSQL-транзакции commit-last.
    """

    sql = _commit_function_sql()

    # Новый content_set создаётся внутри canonical commit.
    assert "insert into kaban_content_sets" in sql
    assert "p_content_key" in sql

    # В том же RPC создаётся revision.
    assert "insert into kaban_content_revisions" in sql

    # И в той же транзакции регистрируются уже загруженные R2 objects.
    assert "jsonb_array_elements" in sql
    assert "insert into kaban_artifacts" in sql

def test_commit_rpc_qualifies_version_column_against_returns_table_output_name():
    sql = _commit_function_sql()

    assert "version = kaban_content_sets.version + 1" in sql
    assert "version = version + 1" not in sql
