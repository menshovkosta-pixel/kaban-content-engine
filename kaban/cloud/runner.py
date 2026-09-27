from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .adapters import load_cloud_adapter
from .contracts import ArtifactStore, ControlStore, LeaseConflict, VersionConflict
from .models import StoredArtifact
from .workspace import WorkspaceBridge, execution_workspace, scoped_workspace_env


@dataclass(frozen=True)
class ExecutionOutcome:
    outcome: str
    execution_id: UUID
    commit: Any = None
    orphan_artifacts: tuple[StoredArtifact, ...] = ()
    message: str = ""


def _is_mutating_change(changes) -> bool:
    return any((
        changes.new_payload is not None,
        changes.approval_action is not None,
        bool(changes.setting_updates),
        bool(changes.artifacts),
        bool(changes.publication_updates),
        bool(changes.events),
    ))


def run_execution(
    execution_id: UUID,
    *,
    store: ControlStore,
    artifacts: ArtifactStore,
    registry,
    temp_root: Path,
    owner: str,
    lease_seconds: int = 300,
    backup_hook=None,
) -> ExecutionOutcome:
    record = store.load_execution(execution_id)
    command = record.command

    lease = store.start_execution(
        execution_id,
        owner,
        lease_seconds,
    )

    if lease is None:
        return ExecutionOutcome(
            "duplicate",
            execution_id,
            message="execution уже выполняется или завершён",
        )

    resource_claim = None
    resource_key = None
    uploaded: list[StoredArtifact] = []
    proposed_revision_id: UUID | None = None

    try:
        # Core operations выполняются без Project workspace.
        if command.operation.startswith("kaban."):
            from .core_operations import execute_core_operation

            result = execute_core_operation(
                command,
                store=store,
                registry=registry,
            )

            store.finish_execution(
                execution_id,
                lease.fence_token,
                "success",
            )

            return ExecutionOutcome(
                "success",
                execution_id,
                commit=result,
            )

        project = registry.get(command.project_id)

        if not project.cloud.adapter:
            raise RuntimeError(
                f"Project {command.project_id} не имеет cloud.adapter"
            )

        adapter = load_cloud_adapter(
            project.cloud.adapter,
            project_id=command.project_id,
        )

        spec = adapter.materialization_spec(command)

        snapshot = store.load_snapshot(
            command.project_id,
            spec,
        )

        resource_key = adapter.resource_key(
            command,
            snapshot,
        )

        if resource_key:
            try:
                resource_claim = store.claim_resource(
                    command.project_id,
                    resource_key,
                    execution_id,
                    lease_seconds,
                    owner=owner,
                )
            except TypeError:
                resource_claim = store.claim_resource(
                    command.project_id,
                    resource_key,
                    execution_id,
                    lease_seconds,
                )

        with execution_workspace(
            temp_root,
            execution_id,
            command.project_id,
        ) as paths:
            state = adapter.materialize(
                command,
                snapshot,
                paths,
                artifacts,
            )

            current_set = next(
                (
                    item
                    for item in snapshot.content_sets
                    if item.content_key == command.content_key
                ),
                None,
            )

            runtime_env = {
                "KABAN_EXECUTION_ID": str(command.execution_id),
                "KABAN_EXECUTION_OWNER": owner,
                "KABAN_EXECUTION_FENCE": str(lease.fence_token),
                "KABAN_PROJECT_ID": command.project_id,
            }

            if current_set is not None:
                runtime_env["KABAN_CONTENT_SET_ID"] = str(
                    current_set.content_set_id
                )

                if current_set.current_revision is not None:
                    runtime_env["KABAN_REVISION_ID"] = str(
                        current_set.current_revision.revision_id
                    )

            with scoped_workspace_env(paths, runtime_env):
                adapter.execute(
                    command,
                    state,
                )

            changes = adapter.collect(
                command,
                state,
                snapshot,
            )

            if changes.new_payload is not None:
                proposed_revision_id = uuid4()

                # Первая генерация нового content_key ещё не имеет
                # canonical content_set_id. UUID требуется до R2 upload,
                # потому что он является частью immutable object key.
                if changes.content_set_id is None:
                    changes = replace(
                        changes,
                        content_set_id=uuid4(),
                        expected_version=0,
                    )

            artifact_rows: list[dict[str, Any]] = []

            for candidate in changes.artifacts:
                if (
                    changes.content_set_id is None
                    or proposed_revision_id is None
                ):
                    continue

                if not hasattr(artifacts, "object_key"):
                    raise RuntimeError(
                        "ArtifactStore ??? durable artifacts "
                        "?????? ???????????? object_key"
                    )

                key = artifacts.object_key(
                    command.project_id,
                    changes.content_set_id,
                    proposed_revision_id,
                    candidate.kind,
                    candidate.logical_name,
                )

                stored = artifacts.put_immutable(
                    command.project_id,
                    key,
                    candidate.source_path,
                    candidate.sha256,
                )

                uploaded.append(stored)

                artifact_rows.append({
                    "kind": candidate.kind,
                    "logical_name": candidate.logical_name,
                    "r2_key": stored.r2_key,
                    "sha256": stored.sha256,
                    "size_bytes": stored.size_bytes,
                    "mime_type": stored.mime_type,
                    "metadata": dict(
                        getattr(candidate, "metadata", {}) or {}
                    ),
                })

            try:
                commit = store.commit_changes(
                    command,
                    changes,
                    execution_fence=lease.fence_token,
                    resource_fence=(
                        resource_claim.fence_token
                        if resource_claim
                        else None
                    ),
                    lease_owner=owner,
                    proposed_revision_id=proposed_revision_id,
                    resource_key=resource_key,
                    artifact_rows=tuple(artifact_rows),
                )
            except (LeaseConflict, VersionConflict):
                # Immutable upload не удаляем немедленно:
                # он может стать orphan artifact после проигранного commit.
                raise

        store.finish_execution(
            execution_id,
            lease.fence_token,
            "success",
        )

        backup_message = ""

        if (
            backup_hook is not None
            and _is_mutating_change(changes)
        ):
            try:
                backup_hook(
                    command.project_id,
                    execution_id,
                )
            except Exception as backup_exc:
                # Canonical commit уже завершён.
                # Ошибка backup не должна повторно запускать business logic.
                backup_message = (
                    "backup failed after canonical commit: "
                    f"{backup_exc.__class__.__name__}"
                )

        return ExecutionOutcome(
            "success",
            execution_id,
            commit=commit,
            message=backup_message,
        )

    except (LeaseConflict, VersionConflict) as exc:
        try:
            store.finish_execution(
                execution_id,
                lease.fence_token,
                "conflict",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except LeaseConflict:
            pass

        return ExecutionOutcome(
            "conflict",
            execution_id,
            orphan_artifacts=tuple(uploaded),
            message=str(exc),
        )

    except Exception as exc:
        try:
            store.finish_execution(
                execution_id,
                lease.fence_token,
                "failed",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except LeaseConflict:
            pass

        raise

    finally:
        if (
            resource_claim is not None
            and resource_key
            and hasattr(store, "release_resource")
        ):
            try:
                store.release_resource(
                    command.project_id,
                    resource_key,
                    execution_id,
                    resource_claim.owner,
                    resource_claim.fence_token,
                )
            except Exception:
                pass