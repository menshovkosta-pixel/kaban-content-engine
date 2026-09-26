from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID


def frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ExecutionCommand:
    execution_id: UUID
    project_id: str
    operation: str
    content_key: str | None
    content_set_id: UUID | None
    expected_version: int | None
    payload: Mapping[str, Any]
    requested_by: str
    idempotency_key: str

    def __post_init__(self):
        if not self.project_id.strip():
            raise ValueError("project_id не может быть пустым")
        if not self.operation.strip():
            raise ValueError("operation не может быть пустой")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key не может быть пустым")
        object.__setattr__(self, "payload", frozen_mapping(self.payload))


@dataclass(frozen=True)
class ExecutionRecord:
    command: ExecutionCommand
    state: str
    lease_owner: str | None = None
    fence_token: int = 0
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class MaterializationSpec:
    content_keys: tuple[str, ...] = ()
    history_locale: str | None = None
    history_before: date | None = None
    history_days: int = 0
    setting_keys: tuple[str, ...] = ()
    artifact_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RevisionSnapshot:
    revision_id: UUID
    content_set_id: UUID
    revision_no: int
    payload: Mapping[str, Any]
    content_hash: str
    created_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "payload", frozen_mapping(self.payload))


@dataclass(frozen=True)
class ContentSetSnapshot:
    content_set_id: UUID
    project_id: str
    content_key: str
    content_date: date | None
    locale: str | None
    version: int
    current_revision: RevisionSnapshot | None
    approved_revision_id: UUID | None
    approved_content_hash: str | None


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: UUID
    project_id: str
    content_set_id: UUID | None
    revision_id: UUID | None
    kind: str
    logical_name: str
    r2_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, Any]

    def __post_init__(self):
        if ".." in Path(self.r2_key).parts:
            raise ValueError("r2_key содержит traversal")
        object.__setattr__(self, "metadata", frozen_mapping(self.metadata))


@dataclass(frozen=True)
class CanonicalSnapshot:
    project_id: str
    content_sets: tuple[ContentSetSnapshot, ...] = ()
    history_revisions: tuple[RevisionSnapshot, ...] = ()
    settings: Mapping[str, Any] = MappingProxyType({})
    artifacts: tuple[ArtifactRef, ...] = ()
    publication: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self):
        object.__setattr__(self, "settings", frozen_mapping(self.settings))
        object.__setattr__(self, "publication", frozen_mapping(self.publication))
        for item in self.content_sets:
            if item.project_id != self.project_id:
                raise ValueError("snapshot содержит другой project_id")
        for item in self.artifacts:
            if item.project_id != self.project_id:
                raise ValueError("artifact содержит другой project_id")


@dataclass(frozen=True)
class ArtifactCandidate:
    kind: str
    logical_name: str
    source_path: Path
    sha256: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "metadata", frozen_mapping(self.metadata))


@dataclass(frozen=True)
class ChangeSet:
    content_set_id: UUID | None = None
    expected_version: int | None = None
    new_payload: Mapping[str, Any] | None = None
    approval_action: str | None = None
    setting_updates: Mapping[str, Any] = MappingProxyType({})
    artifacts: tuple[ArtifactCandidate, ...] = ()
    publication_updates: Mapping[str, Any] = MappingProxyType({})
    events: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self):
        if self.new_payload is not None:
            object.__setattr__(self, "new_payload", frozen_mapping(self.new_payload))
        object.__setattr__(self, "setting_updates", frozen_mapping(self.setting_updates))
        object.__setattr__(self, "publication_updates", frozen_mapping(self.publication_updates))
        object.__setattr__(self, "events", tuple(frozen_mapping(x) for x in self.events))


@dataclass(frozen=True)
class LeaseClaim:
    owner: str
    fence_token: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class CommitResult:
    content_set_id: UUID | None
    revision_id: UUID | None
    version: int | None


@dataclass(frozen=True)
class StoredArtifact:
    r2_key: str
    sha256: str
    size_bytes: int
    mime_type: str


@dataclass(frozen=True)
class PublicationStepState:
    publication_run_id: UUID
    step_key: str
    state: str
    request_fingerprint: str
    external_ids: Mapping[str, Any] | None


@dataclass(frozen=True)
class ProjectWorkspaceState:
    data: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "data", frozen_mapping(self.data))


@dataclass(frozen=True)
class ProjectExecutionResult:
    outcome: str
    data: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self):
        object.__setattr__(self, "data", frozen_mapping(self.data))
