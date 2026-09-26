from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import UUID

from .contracts import ArtifactStore, ProjectWorkspaceAdapter
from .models import CanonicalSnapshot, ChangeSet, ExecutionCommand, ProjectWorkspaceState


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    project_root: Path
    generated: Path
    runtime: Path


@dataclass
class PreparedWorkspace:
    command: ExecutionCommand
    adapter: ProjectWorkspaceAdapter
    before: CanonicalSnapshot
    paths: WorkspacePaths
    state: ProjectWorkspaceState


@contextmanager
def execution_workspace(base_temp: Path, execution_id: UUID, project_id: str) -> Iterator[WorkspacePaths]:
    root = Path(base_temp) / str(execution_id)
    project_root = root / project_id
    paths = WorkspacePaths(root=root, project_root=project_root, generated=project_root / "generated", runtime=project_root / "runtime")
    paths.generated.mkdir(parents=True, exist_ok=False)
    paths.runtime.mkdir(parents=True, exist_ok=False)
    try:
        yield paths
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def scoped_workspace_env(paths: WorkspacePaths, extra: dict[str, str] | None = None):
    names = {"KABAN_GENERATED_DIR": str(paths.generated), "KABAN_RUNTIME_DIR": str(paths.runtime), "KABAN_PERSISTENCE": "cloud"}
    if extra:
        names.update(extra)
    old = {key: os.environ.get(key) for key in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class WorkspaceBridge:
    def __init__(self, store, artifacts: ArtifactStore, base_temp: Path):
        self.store = store
        self.artifacts = artifacts
        self.base_temp = Path(base_temp)

    def prepare(self, command: ExecutionCommand, adapter: ProjectWorkspaceAdapter, paths: WorkspacePaths) -> PreparedWorkspace:
        spec = adapter.materialization_spec(command)
        snapshot = self.store.load_snapshot(command.project_id, spec)
        state = adapter.materialize(command, snapshot, paths, self.artifacts)
        return PreparedWorkspace(command, adapter, snapshot, paths, state)

    def collect(self, prepared: PreparedWorkspace) -> ChangeSet:
        return prepared.adapter.collect(prepared.command, prepared.state, prepared.before)
