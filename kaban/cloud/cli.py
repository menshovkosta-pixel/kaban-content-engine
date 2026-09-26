from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from kaban.projects import project_registry

from .adapters import load_cloud_adapter
from .backup import create_backup
from .config import CloudConfig
from .config_sync import sync_project_config
from .migration import export_local, migrate_local, verify_manifest
from .r2 import R2ArtifactStore
from .runner import run_execution
from .supabase import SupabaseControlStore


def _cloud_dependencies():
    config = CloudConfig.from_env()
    if config is None:
        raise RuntimeError("KABAN_PERSISTENCE=cloud обязателен для cloud provider operations")
    store = SupabaseControlStore(config.supabase_url, config.supabase_service_key)
    artifacts = R2ArtifactStore(
        bucket=config.r2_bucket,
        endpoint_url=config.r2_endpoint,
        access_key_id=config.r2_access_key_id,
        secret_access_key=config.r2_secret_access_key,
    )
    return store, artifacts


def execute_from_env(execution_id: UUID, *, owner: str | None = None, temp_root: Path | None = None):
    store, artifacts = _cloud_dependencies()
    effective_owner = (owner or os.getenv("KABAN_EXECUTION_OWNER", "")).strip()
    if not effective_owner:
        raise ValueError("KABAN_EXECUTION_OWNER обязателен для cloud execution")
    root = Path(temp_root or os.getenv("KABAN_CLOUD_TEMP_ROOT", ".kaban-cloud-tmp"))
    return run_execution(
        execution_id,
        store=store,
        artifacts=artifacts,
        registry=project_registry(),
        temp_root=root,
        owner=effective_owner,
        backup_hook=lambda project_id, eid: create_backup(project_id, eid, store, artifacts, temp_root=root),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KABAN cloud runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    execute = sub.add_parser("execute")
    execute.add_argument("--execution-id", required=True)

    migrate = sub.add_parser("migrate-local")
    migrate.add_argument("--project", required=True)
    migrate.add_argument("--generated", required=True, type=Path)
    migrate.add_argument("--runtime", required=True, type=Path)
    mode = migrate.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Выполнить cloud writes; без флага используется dry-run")
    mode.add_argument("--dry-run", dest="apply", action="store_false", help="Только manifest/inventory (default)")
    migrate.set_defaults(apply=False)
    migrate.add_argument("--manifest", required=True, type=Path)

    verify = sub.add_parser("verify-migration")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--source-root", type=Path)

    export = sub.add_parser("export-local")
    export.add_argument("--project", required=True)
    export.add_argument("--destination", required=True, type=Path)

    sync = sub.add_parser("sync-config")
    sync.add_argument("--project", required=True)
    sync.add_argument("--horizon-days", type=int, default=35)

    status = sub.add_parser("status")
    status.add_argument("--project", required=True)

    health = sub.add_parser("health")
    health.add_argument("--project", required=True)
    return parser


def _project_adapter(project_id: str):
    registry = project_registry()
    project = registry.get(project_id)
    if not project.cloud.adapter:
        raise RuntimeError(f"Project {project_id} не имеет cloud.adapter")
    return load_cloud_adapter(project.cloud.adapter, project_id=project_id), registry


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "execute":
        execution_id = UUID(args.execution_id)
        outcome = execute_from_env(execution_id)
        print(json.dumps({"execution_id": str(outcome.execution_id), "outcome": outcome.outcome, "message": outcome.message}, ensure_ascii=False))
        if outcome.outcome == "conflict":
            raise SystemExit(2)
        return

    if args.command == "migrate-local":
        adapter, _ = _project_adapter(args.project)
        if args.apply:
            store, artifacts = _cloud_dependencies()
        else:
            store = artifacts = None
        result = migrate_local(
            args.project, args.generated, args.runtime, adapter, store, artifacts,
            apply=args.apply, manifest_path=args.manifest,
        )
        print(json.dumps({"project_id": result.project_id, "clean": result.clean, "applied": result.applied, "records": len(result.records), "manifest": str(result.manifest_path)}, ensure_ascii=False))
        if not result.clean:
            raise SystemExit(3)
        return

    if args.command == "verify-migration":
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        source_root = args.source_root or Path(payload.get("source_root") or args.manifest.parent)
        store = artifacts = None
        if payload.get("applied") and os.getenv("KABAN_PERSISTENCE", "").strip().lower() == "cloud":
            store, artifacts = _cloud_dependencies()
        report = verify_manifest(args.manifest, source_root=source_root, store=store, artifacts=artifacts)
        print(json.dumps({"ok": report.ok, "mismatches": list(report.mismatches)}, ensure_ascii=False))
        if not report.ok:
            raise SystemExit(4)
        return

    if args.command == "export-local":
        adapter, _ = _project_adapter(args.project)
        store, artifacts = _cloud_dependencies()
        destination = export_local(args.project, args.destination, adapter, store, artifacts)
        print(json.dumps({"project_id": args.project, "destination": str(destination)}, ensure_ascii=False))
        return

    if args.command == "sync-config":
        store, _ = _cloud_dependencies()
        result = sync_project_config(project_registry(), store, project_id=args.project, horizon_days=args.horizon_days)
        print(json.dumps({
            "project_id": result.project_id, "config_hash": result.config_hash,
            "jobs_synced": result.jobs_synced, "slots_seen": result.slots_seen,
            "slots_created": result.slots_created, "horizon_days": result.horizon_days,
        }, ensure_ascii=False))
        return

    if args.command == "status":
        store, _ = _cloud_dependencies()
        print(json.dumps(store.project_status(args.project), ensure_ascii=False, default=str))
        return

    if args.command == "health":
        store, _ = _cloud_dependencies()
        print(json.dumps(store.project_health(args.project), ensure_ascii=False, default=str))
        return


if __name__ == "__main__":
    main()
