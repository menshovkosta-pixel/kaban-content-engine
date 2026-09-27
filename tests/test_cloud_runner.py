from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from kaban.cloud.models import (
    CanonicalSnapshot,
    ChangeSet,
    CommitResult,
    ExecutionCommand,
    ExecutionRecord,
    LeaseClaim,
    ProjectExecutionResult,
    ProjectWorkspaceState,
    StoredArtifact,
)
from kaban.cloud.runner import run_execution


class Store:
    def __init__(self, cmd):
        self.cmd = cmd
        self.started = 0
        self.commits = 0
        self.last_changes = None
        self.last_commit_kwargs = None

    def load_execution(self, eid):
        return ExecutionRecord(self.cmd, "queued")

    def start_execution(self, eid, owner, lease_seconds):
        self.started += 1
        if self.started > 1:
            return None
        return LeaseClaim(owner, 1, datetime.now(timezone.utc))

    def load_snapshot(self, project_id, spec):
        return CanonicalSnapshot(project_id)

    def claim_resource(self, *args, **kwargs):
        return LeaseClaim(
            kwargs.get("owner", "r"),
            1,
            datetime.now(timezone.utc),
        )

    def commit_changes(self, command, changes, **kwargs):
        self.commits += 1
        self.last_changes = changes
        self.last_commit_kwargs = kwargs
        return CommitResult(None, None, None)

    def finish_execution(self, *args, **kwargs):
        pass


class Adapter:
    calls = 0

    def materialization_spec(self, command):
        from kaban.cloud.models import MaterializationSpec

        return MaterializationSpec()

    def resource_key(self, command, snapshot):
        return None

    def materialize(self, command, snapshot, workspace, artifacts):
        return ProjectWorkspaceState({})

    def execute(self, command, state):
        self.calls += 1
        return ProjectExecutionResult("success")

    def collect(self, command, state, before):
        return ChangeSet()


class Registry:
    def get(self, project_id):
        return SimpleNamespace(
            cloud=SimpleNamespace(
                adapter="test_cloud_runner:ADAPTER",
            )
        )


ADAPTER = Adapter()


def test_duplicate_dispatch_is_noop(tmp_path):
    eid = uuid4()
    cmd = ExecutionCommand(
        eid,
        "caelus",
        "noop",
        "2026-09-26:ru",
        None,
        None,
        {},
        "scheduler",
        "x",
    )
    store = Store(cmd)

    assert run_execution(
        eid,
        store=store,
        artifacts=object(),
        registry=Registry(),
        temp_root=tmp_path,
        owner="a",
    ).outcome == "success"

    assert run_execution(
        eid,
        store=store,
        artifacts=object(),
        registry=Registry(),
        temp_root=tmp_path,
        owner="b",
    ).outcome == "duplicate"

    assert ADAPTER.calls == 1


class MutatingAdapter(Adapter):
    def collect(self, command, state, before):
        return ChangeSet(
            setting_updates={
                "content_diversity": {
                    "profile": "balanced",
                }
            }
        )


MUTATING = MutatingAdapter()


class MutatingRegistry:
    def get(self, project_id):
        return SimpleNamespace(
            cloud=SimpleNamespace(
                adapter="test_cloud_runner:MUTATING",
            )
        )


def test_successful_mutation_invokes_backup_once_and_noop_does_not(tmp_path):
    eid = uuid4()
    cmd = ExecutionCommand(
        eid,
        "caelus",
        "save",
        "2026-09-26:ru",
        None,
        None,
        {},
        "user",
        "backup-1",
    )
    store = Store(cmd)
    calls = []

    run_execution(
        eid,
        store=store,
        artifacts=object(),
        registry=MutatingRegistry(),
        temp_root=tmp_path,
        owner="runner",
        backup_hook=lambda project_id, execution_id: calls.append(
            (project_id, execution_id)
        ),
    )

    assert calls == [("caelus", eid)]

    eid2 = uuid4()
    cmd2 = ExecutionCommand(
        eid2,
        "caelus",
        "noop",
        "2026-09-26:ru",
        None,
        None,
        {},
        "user",
        "backup-2",
    )
    store2 = Store(cmd2)
    calls2 = []

    run_execution(
        eid2,
        store=store2,
        artifacts=object(),
        registry=Registry(),
        temp_root=tmp_path,
        owner="runner",
        backup_hook=lambda *args: calls2.append(args),
    )

    assert calls2 == []


def test_backup_failure_after_commit_does_not_turn_committed_execution_into_retry(
    tmp_path,
):
    eid = uuid4()
    cmd = ExecutionCommand(
        eid,
        "caelus",
        "save",
        "2026-09-26:ru",
        None,
        None,
        {},
        "user",
        "backup-fail",
    )
    store = Store(cmd)

    outcome = run_execution(
        eid,
        store=store,
        artifacts=object(),
        registry=MutatingRegistry(),
        temp_root=tmp_path,
        owner="runner",
        backup_hook=lambda *_: (_ for _ in ()).throw(
            RuntimeError("backup unavailable")
        ),
    )

    assert outcome.outcome == "success"
    assert "backup" in outcome.message.lower()
    assert store.commits == 1


class FirstGenerationStore(Store):
    """
    Имитирует canonical store для первой генерации нового content_key.

    GREEN-контракт:
    runner обязан перед commit уже иметь proposed content_set_id.
    """

    def commit_changes(self, command, changes, **kwargs):
        self.commits += 1
        self.last_changes = changes
        self.last_commit_kwargs = kwargs

        revision_id = kwargs.get("proposed_revision_id")

        if changes.content_set_id is None:
            return CommitResult(None, None, None)

        return CommitResult(
            changes.content_set_id,
            revision_id,
            1,
        )


class FirstGenerationAdapter(Adapter):
    def resource_key(self, command, snapshot):
        return f"content-key:{command.content_key}"

    def materialize(self, command, snapshot, workspace, artifacts):
        card = workspace.generated / "2099-01-15" / "ru" / "cards" / "aries.png"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_bytes(b"fake-png")

        return ProjectWorkspaceState(
            {
                "card": card,
            }
        )

    def collect(self, command, state, before):
        card = Path(state.data["card"])

        candidate = SimpleNamespace(
            kind="card_png",
            logical_name="aries.png",
            source_path=card,
            sha256="test-sha256",
            size_bytes=card.stat().st_size,
            mime_type="image/png",
            metadata={},
        )

        return ChangeSet(
            content_set_id=None,
            expected_version=None,
            new_payload={
                "iso_date": "2099-01-15",
                "language": "ru",
                "content_hash": "test-content-hash",
            },
            artifacts=(candidate,),
        )


FIRST_GENERATION = FirstGenerationAdapter()


class FirstGenerationRegistry:
    def get(self, project_id):
        return SimpleNamespace(
            cloud=SimpleNamespace(
                adapter="test_cloud_runner:FIRST_GENERATION",
            )
        )


class FirstGenerationArtifacts:
    def __init__(self):
        self.object_key_calls = []
        self.uploads = []

    def object_key(
        self,
        project_id,
        content_set_id,
        revision_id,
        kind,
        logical_name,
    ):
        self.object_key_calls.append(
            (
                project_id,
                content_set_id,
                revision_id,
                kind,
                logical_name,
            )
        )

        return (
            f"projects/{project_id}/content/{content_set_id}/"
            f"revisions/{revision_id}/{kind}/{logical_name}"
        )

    def put_immutable(
        self,
        project_id,
        key,
        source_path,
        expected_sha256,
    ):
        self.uploads.append(
            (
                project_id,
                key,
                Path(source_path),
                expected_sha256,
            )
        )

        return StoredArtifact(
            r2_key=key,
            sha256=expected_sha256,
            size_bytes=Path(source_path).stat().st_size,
            mime_type="image/png",
        )


def test_first_generation_allocates_content_set_before_artifact_upload_and_commit(
    tmp_path,
):
    """
    Regression: первая cloud generation нового content_key не должна
    превращаться в ложный success без canonical content/revision.
    """

    eid = uuid4()

    cmd = ExecutionCommand(
        eid,
        "caelus",
        "generate",
        "2099-01-15:ru",
        None,
        None,
        {
            "mode": "mock",
            "language": "ru",
            "date": "2099-01-15",
        },
        "operator:test",
        "regression:first-generation",
    )

    store = FirstGenerationStore(cmd)
    artifacts = FirstGenerationArtifacts()

    outcome = run_execution(
        eid,
        store=store,
        artifacts=artifacts,
        registry=FirstGenerationRegistry(),
        temp_root=tmp_path,
        owner="test-runner",
    )

    assert store.commits == 1

    # Новый content set обязан получить UUID до построения R2 key.
    assert store.last_changes.content_set_id is not None

    # Первая генерация обязана реально отправить durable artifact в R2.
    assert len(artifacts.object_key_calls) == 1
    assert len(artifacts.uploads) == 1

    project_id, content_set_id, revision_id, kind, logical_name = (
        artifacts.object_key_calls[0]
    )

    assert project_id == "caelus"
    assert content_set_id == store.last_changes.content_set_id
    assert revision_id is not None
    assert kind == "card_png"
    assert logical_name == "aries.png"

    # Runner ?????? ???????? ? canonical commit ??? ?? resource lease identity.
    assert store.last_commit_kwargs["resource_key"] is not None

    # Artifact metadata must describe the exact R2 object committed canonically.
    artifact_rows = store.last_commit_kwargs["artifact_rows"]
    assert len(artifact_rows) == 1

    artifact_row = artifact_rows[0]
    assert artifact_row["kind"] == "card_png"
    assert artifact_row["logical_name"] == "aries.png"
    assert artifact_row["r2_key"] == artifacts.uploads[0][1]
    assert artifact_row["sha256"] == artifacts.uploads[0][3]
    assert artifact_row["size_bytes"] > 0
    assert artifact_row["mime_type"] == "image/png"

    # Success допустим только после canonical revision commit.
    assert outcome.outcome == "success"
    assert outcome.commit.content_set_id is not None
    assert outcome.commit.revision_id is not None
