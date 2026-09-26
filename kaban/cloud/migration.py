from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .models import CanonicalSnapshot, ExecutionCommand, MaterializationSpec
from .workspace import WorkspacePaths


@dataclass
class MigrationRecord:
    source_path: str
    source_size: int
    source_sha256: str
    classification: str
    destination_type: str
    destination_id_or_r2_key: str | None = None
    destination_sha256: str | None = None
    result: str = "planned"


@dataclass(frozen=True)
class MigrationResult:
    project_id: str
    clean: bool
    applied: bool
    records: tuple[MigrationRecord, ...]
    manifest_path: Path


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    mismatches: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(project_id: str, record: MigrationRecord) -> str:
    raw = f"{project_id}\0{record.source_path}\0{record.source_sha256}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _deterministic_uuid(*parts: str) -> UUID:
    return uuid5(NAMESPACE_URL, "kaban-migration:" + ":".join(parts))


def _destination_type(classification: str) -> str:
    if classification == "durable_artifact":
        return "r2"
    if classification.startswith("durable_"):
        return "postgresql"
    if classification in {"regenerable_derivative", "local_lock_not_migrated", "runtime_ephemeral_not_migrated"}:
        return "none"
    return "unknown"


def inventory(project_id: str, generated: Path, runtime: Path, adapter) -> list[MigrationRecord]:
    records: list[MigrationRecord] = []
    for root_kind, root in (("generated", Path(generated)), ("runtime", Path(runtime))):
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            classification = adapter.classify_migration_path(root_kind, relative)
            result = "unknown" if classification == "unknown" else (
                "not_migrated" if classification in {"regenerable_derivative", "local_lock_not_migrated", "runtime_ephemeral_not_migrated"} else "planned"
            )
            records.append(MigrationRecord(
                source_path=f"{root_kind}/{relative}",
                source_size=path.stat().st_size,
                source_sha256=_sha256(path),
                classification=classification,
                destination_type=_destination_type(classification),
                result=result,
            ))
    return records


def _write_manifest(path: Path, project_id: str, applied: bool, records: list[MigrationRecord], source_root: Path | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "kaban-migration-manifest-v1",
        "project_id": project_id,
        "source_root": str(source_root.resolve()) if source_root is not None else None,
        "applied": applied,
        "clean": not any(item.classification == "unknown" for item in records),
        "records": [asdict(item) for item in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def migrate_local(project_id: str, generated: Path, runtime: Path, adapter, store, artifacts, *, apply: bool = False, manifest_path: Path) -> MigrationResult:
    generated = Path(generated)
    runtime = Path(runtime)
    records = inventory(project_id, generated, runtime, adapter)
    source_root = Path(os.path.commonpath([str(generated.resolve()), str(runtime.resolve())]))
    unknown = [record for record in records if record.classification == "unknown"]
    _write_manifest(manifest_path, project_id, False, records, source_root)
    if not apply:
        return MigrationResult(project_id, not unknown, False, tuple(records), Path(manifest_path))
    if unknown:
        raise RuntimeError("Migration содержит unknown files; сначала добавьте явную классификацию")
    if hasattr(store, "ensure_migration_project"):
        store.ensure_migration_project(project_id)

    by_source = {record.source_path: record for record in records}
    content_records = [record for record in records if record.classification == "durable_content"]
    for content_record in content_records:
        content_key = adapter.migration_content_key(content_record.source_path)
        bundle = adapter.read_migration_content_bundle(generated, content_key)
        proposed_set = _deterministic_uuid(project_id, "content-set", content_key)
        content_set_id = store.resolve_migration_content_set(project_id, content_key, proposed_set)
        source_identity = _identity(project_id, content_record)
        revision_id = _deterministic_uuid(project_id, "revision", content_key, source_identity)
        artifact_rows: list[dict[str, Any]] = []
        for source in bundle.get("artifacts", ()):
            source_path = Path(source)
            source_record = by_source[f"generated/{source_path.relative_to(generated).as_posix()}"]
            key = artifacts.object_key(project_id, content_set_id, revision_id, "card_png", source_path.name)
            stored = artifacts.put_immutable(project_id, key, source_path, source_record.source_sha256)
            source_record.destination_id_or_r2_key = stored.r2_key
            source_record.destination_sha256 = stored.sha256
            source_record.result = "uploaded_pending_db"
            artifact_rows.append({
                "source_identity": _identity(project_id, source_record),
                "source_path": source_record.source_path,
                "logical_name": source_path.name,
                "kind": "card_png",
                "r2_key": stored.r2_key,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
                "mime_type": stored.mime_type,
            })
        committed = store.import_migration_bundle(
            project_id=project_id,
            content_key=content_key,
            content_set_id=content_set_id,
            revision_id=revision_id,
            source_identity=source_identity,
            source_path=content_record.source_path,
            source_sha256=content_record.source_sha256,
            content_date=bundle.get("content_date"),
            locale=bundle.get("locale"),
            payload=bundle["payload"],
            content_hash=bundle["content_hash"],
            approved=bundle.get("approved", False),
            status=bundle.get("status") or {},
            publication=bundle.get("publication") or {},
            artifacts=artifact_rows,
            companion_identities=[
                {"source_identity": _identity(project_id, by_source[f"generated/{rel}"]), "source_path": f"generated/{rel}", "source_sha256": by_source[f"generated/{rel}"].source_sha256}
                for rel in bundle.get("companion_relative_paths", ()) if f"generated/{rel}" in by_source
            ],
        )
        content_record.destination_id_or_r2_key = str(committed["revision_id"])
        content_record.destination_sha256 = content_record.source_sha256
        content_record.result = "verified"
        for companion in bundle.get("companion_relative_paths", ()):
            record = by_source.get(f"generated/{companion}")
            if record:
                record.destination_id_or_r2_key = str(committed["content_set_id"])
                record.destination_sha256 = record.source_sha256
                record.result = "verified"
        for row in artifact_rows:
            record = next((r for r in records if _identity(project_id, r) == row["source_identity"]), None)
            if record:
                record.result = "verified"

    for record in records:
        if record.classification == "durable_setting":
            key, value = adapter.read_migration_setting(generated, record.source_path)
            identity = _identity(project_id, record)
            store.import_migration_setting(project_id=project_id, setting_key=key, value=value, source_identity=identity, source_path=record.source_path, source_sha256=record.source_sha256)
            record.destination_id_or_r2_key = key
            record.destination_sha256 = record.source_sha256
            record.result = "verified"
        elif record.classification == "durable_history":
            payload = adapter.read_migration_history(generated, runtime, record.source_path)
            identity = _identity(project_id, record)
            store.import_migration_history(project_id=project_id, source_identity=identity, source_path=record.source_path, source_sha256=record.source_sha256, payload=payload)
            record.destination_id_or_r2_key = identity
            record.destination_sha256 = record.source_sha256
            record.result = "verified"

    _write_manifest(manifest_path, project_id, True, records, source_root)
    return MigrationResult(project_id, True, True, tuple(records), Path(manifest_path))


def verify_manifest(manifest_path: Path, *, source_root: Path, store=None, artifacts=None) -> VerificationReport:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for item in payload.get("records", []):
        source_path = str(item["source_path"])
        path = Path(source_root) / source_path
        if not path.is_file() or _sha256(path) != item["source_sha256"]:
            mismatches.append(source_path)
            continue
        if item.get("result") == "verified" and item.get("destination_type") == "postgresql" and store is not None:
            identity = hashlib.sha256(f"{payload['project_id']}\0{source_path}\0{item['source_sha256']}".encode()).hexdigest()
            if not store.verify_migration_identity(payload["project_id"], identity, item["source_sha256"]):
                mismatches.append(source_path)
        if item.get("result") == "verified" and item.get("destination_type") == "r2" and artifacts is not None:
            key = item.get("destination_id_or_r2_key")
            if not key or not artifacts.verify_object(payload["project_id"], key, item["destination_sha256"], item["source_size"]):
                mismatches.append(source_path)
    return VerificationReport(not mismatches, tuple(mismatches))


def export_local(project_id: str, destination: Path, adapter, store, artifacts) -> Path:
    """Восстанавливает текущую локальную проекцию из canonical cloud state."""
    destination = Path(destination)
    generated = destination / "generated"
    runtime = destination / "runtime"
    generated.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    snapshot = store.load_snapshot(
        project_id,
        MaterializationSpec(setting_keys=("content_diversity",), artifact_kinds=("card_png",)),
    )
    for content_set in snapshot.content_sets:
        if content_set.current_revision is None:
            continue
        publication = (
            store.load_publication_projection(project_id, content_set.content_set_id)
            if hasattr(store, "load_publication_projection") else {}
        )
        per_set = CanonicalSnapshot(
            project_id=project_id,
            content_sets=(content_set,),
            settings=snapshot.settings,
            artifacts=tuple(
                item for item in snapshot.artifacts
                if item.content_set_id == content_set.content_set_id
            ),
            publication=publication,
        )
        command = ExecutionCommand(
            execution_id=_deterministic_uuid(project_id, "export", content_set.content_key),
            project_id=project_id,
            operation="export",
            content_key=content_set.content_key,
            content_set_id=content_set.content_set_id,
            expected_version=content_set.version,
            payload={},
            requested_by="migration-export",
            idempotency_key=f"export:{content_set.content_key}",
        )
        paths = WorkspacePaths(
            root=destination,
            project_root=destination,
            generated=generated,
            runtime=runtime,
        )
        adapter.materialize(command, per_set, paths, artifacts)
    return destination
