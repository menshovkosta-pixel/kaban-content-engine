from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Mapping
from uuid import UUID

from .contracts import LeaseConflict, ProjectIsolationError, VersionConflict
from .models import (
    ArtifactRef, CanonicalSnapshot, ChangeSet, CommitResult, ContentSetSnapshot,
    ExecutionCommand, ExecutionRecord, LeaseClaim, MaterializationSpec, RevisionSnapshot,
)

RPC_CREATE_COMMAND = "kaban_create_or_get_command"
RPC_UPSERT_SCHEDULE_SLOT = "kaban_upsert_schedule_slot"
RPC_START_EXECUTION = "kaban_start_execution"
RPC_RENEW_EXECUTION = "kaban_renew_execution"
RPC_CLAIM_RESOURCE = "kaban_claim_resource"
RPC_RENEW_RESOURCE = "kaban_renew_resource"
RPC_RELEASE_RESOURCE = "kaban_release_resource"
RPC_FINISH_EXECUTION = "kaban_finish_execution"
RPC_COMMIT_REVISION = "kaban_commit_content_revision"
RPC_CREATE_PUBLICATION_RUN = "kaban_create_or_get_publication_run"
RPC_TRANSITION_PUBLICATION_STEP = "kaban_transition_publication_step"
RPC_MARK_STALE_SENDING_UNKNOWN = "kaban_mark_stale_sending_unknown"
RPC_UPSERT_SCHEDULE_SLOT = "kaban_upsert_schedule_slot"


class SupabaseControlStore:
    def __init__(self, base_url: str, service_key: str, timeout: int = 30, opener=None):
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, *, payload: Any = None, query: Mapping[str, str] | None = None) -> Any:
        url = f"{self.base_url}/rest/v1/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method, headers={
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            response = self._opener(req, timeout=self.timeout)
            with response:
                raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            safe = raw.replace(self._service_key, "[REDACTED]")
            upper = safe.upper()
            if exc.code == 409 or "VERSION_CONFLICT" in upper:
                raise VersionConflict(safe) from exc
            if "LEASE_CONFLICT" in upper or "STALE" in upper:
                raise LeaseConflict(safe) from exc
            if "PROJECT" in upper and ("ISOLATION" in upper or "MISMATCH" in upper):
                raise ProjectIsolationError(safe) from exc
            raise RuntimeError(f"Supabase HTTP {exc.code}: {safe}") from exc
        except urllib.error.URLError as exc:
            message = str(exc.reason).replace(self._service_key, "[REDACTED]")
            raise RuntimeError(f"Supabase transport error: {message}") from exc

    def _rpc(self, name: str, payload: Mapping[str, Any]) -> Any:
        return self._request("POST", f"rpc/{name}", payload=dict(payload))

    @staticmethod
    def _first(value: Any) -> Any:
        if isinstance(value, list):
            return value[0] if value else None
        return value

    @staticmethod
    def _dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    def create_or_get_command(self, command: ExecutionCommand) -> UUID:
        result = self._rpc(RPC_CREATE_COMMAND, {
            "p_project_id": command.project_id,
            "p_operation": command.operation,
            "p_content_key": command.content_key,
            "p_content_set_id": str(command.content_set_id) if command.content_set_id else None,
            "p_expected_version": command.expected_version,
            "p_command_payload": dict(command.payload),
            "p_requested_by": command.requested_by,
            "p_idempotency_key": command.idempotency_key,
        })
        item = self._first(result)
        if isinstance(item, dict):
            item = item.get("kaban_create_or_get_command") or item.get("execution_id")
        return UUID(str(item))


    def upsert_schedule_slot(
        self, *, command: ExecutionCommand, job_id: str, slot_id: str,
        scheduled_for: datetime, misfire_deadline_at: datetime, retry_deadline_at: datetime,
        wait_condition: Mapping[str, Any] | None = None,
    ) -> bool:
        row = self._first(self._rpc(RPC_UPSERT_SCHEDULE_SLOT, {
            "p_project_id": command.project_id,
            "p_execution_id": str(command.execution_id),
            "p_job_id": job_id,
            "p_slot_id": slot_id,
            "p_operation": command.operation,
            "p_content_key": command.content_key,
            "p_content_set_id": str(command.content_set_id) if command.content_set_id else None,
            "p_expected_version": command.expected_version,
            "p_command_payload": dict(command.payload),
            "p_requested_by": command.requested_by,
            "p_idempotency_key": command.idempotency_key,
            "p_scheduled_for": scheduled_for.isoformat(),
            "p_misfire_deadline_at": misfire_deadline_at.isoformat(),
            "p_retry_deadline_at": retry_deadline_at.isoformat(),
            "p_wait_condition": dict(wait_condition) if wait_condition else None,
        }))
        if not isinstance(row, Mapping) or "execution_id" not in row:
            raise RuntimeError("Supabase schedule-slot RPC вернул неверный контракт")
        return bool(row.get("created"))

    def load_execution(self, execution_id: UUID) -> ExecutionRecord:
        rows = self._request("GET", "kaban_executions", query={"execution_id": f"eq.{execution_id}", "select": "*", "limit": "1"}) or []
        if not rows:
            raise KeyError(f"execution not found: {execution_id}")
        row = rows[0]
        command = ExecutionCommand(
            execution_id=UUID(row["execution_id"]), project_id=row["project_id"], operation=row["operation"],
            content_key=row.get("content_key"), content_set_id=UUID(row["content_set_id"]) if row.get("content_set_id") else None,
            expected_version=row.get("expected_version"), payload=row.get("command_payload") or {}, requested_by=row.get("requested_by") or "",
            idempotency_key=row.get("idempotency_key") or str(execution_id),
        )
        return ExecutionRecord(command=command, state=row.get("state") or "queued", lease_owner=row.get("lease_owner"), fence_token=int(row.get("fence_token") or 0), lease_expires_at=self._dt(row.get("lease_expires_at")))

    def start_execution(self, execution_id: UUID, owner: str, lease_seconds: int) -> LeaseClaim | None:
        row = self._first(self._rpc(RPC_START_EXECUTION, {"p_execution_id": str(execution_id), "p_owner": owner, "p_lease_seconds": lease_seconds}))
        if not row:
            return None
        return LeaseClaim(str(row["owner"]), int(row["fence_token"]), self._dt(row["lease_expires_at"]))  # type: ignore[arg-type]

    def renew_execution(self, execution_id: UUID, owner: str, fence_token: int, lease_seconds: int) -> LeaseClaim:
        row = self._first(self._rpc(RPC_RENEW_EXECUTION, {"p_execution_id": str(execution_id), "p_owner": owner, "p_fence_token": fence_token, "p_lease_seconds": lease_seconds}))
        if not row:
            raise LeaseConflict("execution lease renewal rejected")
        return LeaseClaim(str(row["owner"]), int(row["fence_token"]), self._dt(row["lease_expires_at"]))  # type: ignore[arg-type]

    def claim_resource(self, project_id: str, resource_key: str, execution_id: UUID, lease_seconds: int, owner: str | None = None) -> LeaseClaim:
        row = self._first(self._rpc(RPC_CLAIM_RESOURCE, {"p_project_id": project_id, "p_resource_key": resource_key, "p_execution_id": str(execution_id), "p_owner": owner or str(execution_id), "p_lease_seconds": lease_seconds}))
        if not row:
            raise LeaseConflict("resource lease rejected")
        return LeaseClaim(str(row["owner"]), int(row["fence_token"]), self._dt(row["lease_expires_at"]))  # type: ignore[arg-type]

    def renew_resource(self, project_id: str, resource_key: str, execution_id: UUID, owner: str, fence_token: int, lease_seconds: int) -> LeaseClaim:
        row = self._first(self._rpc(RPC_RENEW_RESOURCE, {"p_project_id": project_id, "p_resource_key": resource_key, "p_execution_id": str(execution_id), "p_owner": owner, "p_fence_token": fence_token, "p_lease_seconds": lease_seconds}))
        if not row:
            raise LeaseConflict("resource lease renewal rejected")
        return LeaseClaim(str(row["owner"]), int(row["fence_token"]), self._dt(row["lease_expires_at"]))  # type: ignore[arg-type]

    def release_resource(self, project_id: str, resource_key: str, execution_id: UUID, owner: str, fence_token: int) -> None:
        self._rpc(RPC_RELEASE_RESOURCE, {"p_project_id": project_id, "p_resource_key": resource_key, "p_execution_id": str(execution_id), "p_owner": owner, "p_fence_token": fence_token})

    def _revision(self, row: Mapping[str, Any]) -> RevisionSnapshot:
        return RevisionSnapshot(UUID(row["revision_id"]), UUID(row["content_set_id"]), int(row["revision_no"]), row.get("payload") or {}, row["content_hash"], self._dt(row["created_at"]))  # type: ignore[arg-type]

    def load_snapshot(self, project_id: str, spec: MaterializationSpec) -> CanonicalSnapshot:
        content_rows = self._request("GET", "kaban_content_sets", query={"project_id": f"eq.{project_id}", "select": "*"}) or []
        sets: list[ContentSetSnapshot] = []
        for row in content_rows:
            if row.get("project_id") != project_id:
                raise ProjectIsolationError("Supabase вернул content_set другого project_id")
            revision = None
            if row.get("current_revision_id"):
                rev_rows = self._request("GET", "kaban_content_revisions", query={"project_id": f"eq.{project_id}", "revision_id": f"eq.{row['current_revision_id']}", "select": "*", "limit": "1"}) or []
                if rev_rows:
                    if rev_rows[0].get("project_id") != project_id:
                        raise ProjectIsolationError("Supabase вернул revision другого project_id")
                    revision = self._revision(rev_rows[0])
            sets.append(ContentSetSnapshot(UUID(row["content_set_id"]), project_id, row["content_key"], date.fromisoformat(row["content_date"]) if row.get("content_date") else None, row.get("locale"), int(row.get("version") or 0), revision, UUID(row["approved_revision_id"]) if row.get("approved_revision_id") else None, row.get("approved_content_hash")))
        artifact_rows = self._request("GET", "kaban_artifacts", query={"project_id": f"eq.{project_id}", "select": "*"}) or []
        artifacts = []
        for row in artifact_rows:
            if row.get("project_id") != project_id:
                raise ProjectIsolationError("Supabase вернул artifact другого project_id")
            artifacts.append(ArtifactRef(UUID(row["artifact_id"]), project_id, UUID(row["content_set_id"]) if row.get("content_set_id") else None, UUID(row["revision_id"]) if row.get("revision_id") else None, row["kind"], row["logical_name"], row["r2_key"], row["sha256"], int(row["size_bytes"]), row["mime_type"], row.get("metadata") or {}))
        # История и settings читаются отдельными запросами только если они нужны adapter-у.
        history: list[RevisionSnapshot] = []
        if spec.history_days:
            rows = self._request("GET", "kaban_content_revisions", query={"project_id": f"eq.{project_id}", "select": "*", "order": "created_at.desc"}) or []
            for row in rows:
                if row.get("project_id") != project_id:
                    raise ProjectIsolationError("Supabase вернул history другого project_id")
                history.append(self._revision(row))
        settings_rows = self._request("GET", "kaban_project_settings", query={"project_id": f"eq.{project_id}", "select": "setting_key,value"}) or []
        settings = {row["setting_key"]: row.get("value") for row in settings_rows}
        return CanonicalSnapshot(project_id=project_id, content_sets=tuple(sets), history_revisions=tuple(history), settings=settings, artifacts=tuple(artifacts), publication={})

    def commit_changes(
        self,
        command: ExecutionCommand,
        changes: ChangeSet,
        *,
        execution_fence: int,
        resource_fence: int | None,
        lease_owner: str | None = None,
        proposed_revision_id: UUID | None = None,
        resource_key: str | None = None,
        artifact_rows: tuple[Mapping[str, Any], ...] = (),
    ) -> CommitResult:
        if changes.new_payload is None or changes.content_set_id is None:
            return CommitResult(
                changes.content_set_id,
                None,
                changes.expected_version,
            )

        if proposed_revision_id is None:
            raise ValueError(
                "proposed_revision_id ?????????? ??? commit-last"
            )

        result = self._first(
            self._rpc(
                RPC_COMMIT_REVISION,
                {
                    "p_project_id": command.project_id,
                    "p_execution_id": str(command.execution_id),
                    "p_owner": lease_owner or command.requested_by,
                    "p_execution_fence": execution_fence,
                    "p_resource_key": (
                        resource_key
                        if resource_fence is not None
                        else None
                    ),
                    "p_resource_fence": resource_fence,
                    "p_content_key": command.content_key,
                    "p_content_set_id": str(changes.content_set_id),
                    "p_expected_version": changes.expected_version,
                    "p_proposed_revision_id": str(
                        proposed_revision_id
                    ),
                    "p_payload": dict(changes.new_payload),
                    "p_content_hash": str(
                        dict(changes.new_payload).get(
                            "content_hash"
                        )
                        or ""
                    ),
                    "p_artifacts": [
                        dict(item)
                        for item in artifact_rows
                    ],
                },
            )
        )

        if not result:
            raise VersionConflict(
                "content commit rejected"
            )

        return CommitResult(
            UUID(result["content_set_id"]),
            UUID(result["revision_id"]),
            int(result["version"]),
        )


    def create_or_get_publication_run(self, project_id: str, publication_key: str, content_set_id: UUID | None, revision_id: UUID | None, execution_id: UUID | None) -> UUID:
        result = self._rpc(RPC_CREATE_PUBLICATION_RUN, {
            "p_project_id": project_id, "p_publication_key": publication_key,
            "p_content_set_id": str(content_set_id) if content_set_id else None,
            "p_revision_id": str(revision_id) if revision_id else None,
            "p_execution_id": str(execution_id) if execution_id else None,
        })
        item = self._first(result)
        if isinstance(item, dict):
            item = item.get("kaban_create_or_get_publication_run") or item.get("publication_run_id")
        return UUID(str(item))

    def get_publication_step(self, project_id: str, publication_run_id: UUID, step_key: str) -> Mapping[str, Any] | None:
        rows = self._request("GET", "kaban_publication_steps", query={
            "project_id": f"eq.{project_id}", "publication_run_id": f"eq.{publication_run_id}",
            "step_key": f"eq.{step_key}", "select": "state,request_fingerprint,external_ids", "limit": "1",
        }) or []
        return rows[0] if rows else None

    def transition_publication_step(self, *, project_id: str, publication_run_id: UUID, step_key: str, request_fingerprint: str, from_state: str, to_state: str, external_ids: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None, execution_id: UUID | None = None, owner: str | None = None, fence_token: int | None = None) -> Mapping[str, Any]:
        row = self._first(self._rpc(RPC_TRANSITION_PUBLICATION_STEP, {
            "p_project_id": project_id, "p_publication_run_id": str(publication_run_id), "p_step_key": step_key,
            "p_request_fingerprint": request_fingerprint, "p_from_state": from_state, "p_to_state": to_state,
            "p_external_ids": dict(external_ids) if external_ids else None, "p_error": dict(error) if error else None,
            "p_execution_id": str(execution_id) if execution_id else None, "p_owner": owner, "p_fence_token": fence_token,
        }))
        if not row:
            raise LeaseConflict("publication transition rejected")
        return row

    def finish_execution(self, execution_id: UUID, fence_token: int, outcome: str, error: Mapping[str, Any] | None = None) -> None:
        ok = self._rpc(RPC_FINISH_EXECUTION, {"p_execution_id": str(execution_id), "p_fence_token": fence_token, "p_outcome": outcome, "p_error": dict(error or {}) if error else None})
        if ok is False:
            raise LeaseConflict("finish rejected by stale fence")

# Stage 4 migration methods are deliberately provider-generic; Project mapping lives in adapters.
def _migration_first_uuid(value, key: str) -> UUID:
    item = value[0] if isinstance(value, list) and value else value
    if isinstance(item, dict):
        item = item.get(key) or item.get(key.replace("kaban_", ""))
    return UUID(str(item))


def _ensure_migration_project(self, project_id: str) -> None:
    self._rpc("kaban_ensure_migration_project", {"p_project_id": project_id})


def _resolve_migration_content_set(self, project_id: str, content_key: str, proposed_id: UUID) -> UUID:
    value = self._rpc("kaban_resolve_migration_content_set", {
        "p_project_id": project_id, "p_content_key": content_key,
        "p_proposed_content_set_id": str(proposed_id),
    })
    item = self._first(value)
    if isinstance(item, dict): item = item.get("content_set_id")
    return UUID(str(item))


def _import_migration_bundle(self, **bundle):
    payload = {
        "p_project_id": bundle["project_id"], "p_content_key": bundle["content_key"],
        "p_content_set_id": str(bundle["content_set_id"]), "p_revision_id": str(bundle["revision_id"]),
        "p_source_identity": bundle["source_identity"], "p_source_path": bundle["source_path"], "p_source_sha256": bundle["source_sha256"],
        "p_content_date": bundle.get("content_date"), "p_locale": bundle.get("locale"), "p_payload": bundle["payload"],
        "p_content_hash": bundle["content_hash"], "p_approved": bool(bundle.get("approved")), "p_status": bundle.get("status") or {},
        "p_publication": bundle.get("publication") or {}, "p_artifacts": bundle.get("artifacts") or [],
        "p_companion_identities": bundle.get("companion_identities") or [],
    }
    item = self._first(self._rpc("kaban_import_migration_bundle", payload))
    return {"content_set_id": UUID(str(item["content_set_id"])), "revision_id": UUID(str(item["revision_id"]))}


def _import_migration_setting(self, **item) -> None:
    self._rpc("kaban_import_migration_setting", {
        "p_project_id": item["project_id"], "p_setting_key": item["setting_key"], "p_value": item["value"],
        "p_source_identity": item["source_identity"], "p_source_path": item["source_path"], "p_source_sha256": item["source_sha256"],
    })


def _import_migration_history(self, **item) -> None:
    self._rpc("kaban_import_migration_history", {
        "p_project_id": item["project_id"], "p_source_identity": item["source_identity"], "p_source_path": item["source_path"],
        "p_source_sha256": item["source_sha256"], "p_payload": item["payload"],
    })


def _verify_migration_identity(self, project_id: str, source_identity: str, sha256: str) -> bool:
    rows = self._request("GET", "kaban_migration_identities", query={
        "project_id": f"eq.{project_id}", "source_identity": f"eq.{source_identity}", "source_sha256": f"eq.{sha256}", "select": "source_identity", "limit": "1",
    }) or []
    return bool(rows)


def _load_publication_projection(self, project_id: str, content_set_id: UUID):
    rows = self._request("GET", "kaban_publication_runs", query={
        "project_id": f"eq.{project_id}", "content_set_id": f"eq.{content_set_id}", "select": "state,metadata,updated_at", "order": "updated_at.desc", "limit": "1",
    }) or []
    if not rows: return {}
    metadata = rows[0].get("metadata") or {}
    return metadata if metadata else {"state": rows[0].get("state")}


SupabaseControlStore.ensure_migration_project = _ensure_migration_project
SupabaseControlStore.resolve_migration_content_set = _resolve_migration_content_set
SupabaseControlStore.import_migration_bundle = _import_migration_bundle
SupabaseControlStore.import_migration_setting = _import_migration_setting
SupabaseControlStore.import_migration_history = _import_migration_history
SupabaseControlStore.verify_migration_identity = _verify_migration_identity
SupabaseControlStore.load_publication_projection = _load_publication_projection


def _backup_projection(self, project_id: str):
    project_rows = self._request("GET", "kaban_projects", query={"project_id": f"eq.{project_id}", "select": "project_id,name,config_hash,config_json,enabled,updated_at", "limit": "1"}) or []
    content_sets = self._request("GET", "kaban_content_sets", query={"project_id": f"eq.{project_id}", "select": "*", "order": "content_key.asc"}) or []
    revision_ids = [row.get("current_revision_id") for row in content_sets if row.get("current_revision_id")]
    revisions = []
    for revision_id in revision_ids:
        rows = self._request("GET", "kaban_content_revisions", query={"project_id": f"eq.{project_id}", "revision_id": f"eq.{revision_id}", "select": "*", "limit": "1"}) or []
        revisions.extend(rows[:1])
    settings_rows = self._request("GET", "kaban_project_settings", query={"project_id": f"eq.{project_id}", "select": "setting_key,value,version,updated_at", "order": "setting_key.asc"}) or []
    approvals = self._request("GET", "kaban_approvals", query={"project_id": f"eq.{project_id}", "select": "*", "order": "created_at.desc", "limit": "200"}) or []
    scheduler_jobs = self._request("GET", "kaban_scheduler_jobs", query={"project_id": f"eq.{project_id}", "select": "*", "order": "job_id.asc"}) or []
    executions = self._request("GET", "kaban_executions", query={"project_id": f"eq.{project_id}", "select": "*", "order": "created_at.desc", "limit": "200"}) or []
    publication_runs = self._request("GET", "kaban_publication_runs", query={"project_id": f"eq.{project_id}", "select": "*", "order": "created_at.desc", "limit": "100"}) or []
    publication_steps = self._request("GET", "kaban_publication_steps", query={"project_id": f"eq.{project_id}", "select": "*", "order": "updated_at.desc", "limit": "300"}) or []
    return {
        "format": "kaban-backup-v1",
        "project": project_rows[0] if project_rows else {"project_id": project_id},
        "content_sets": content_sets,
        "current_revisions": revisions,
        "settings": {row["setting_key"]: row.get("value") for row in settings_rows},
        "settings_rows": settings_rows,
        "approvals": approvals,
        "scheduler_jobs": scheduler_jobs,
        "executions": executions,
        "publication_runs": publication_runs,
        "publication_steps": publication_steps,
    }


SupabaseControlStore.backup_projection = _backup_projection


def _sync_project_config(self, *, project_id: str, name: str, config_hash: str, config_json, jobs) -> None:
    self._rpc("kaban_sync_project_config", {
        "p_project_id": project_id,
        "p_name": name,
        "p_config_hash": config_hash,
        "p_config_json": config_json,
        "p_jobs": jobs,
    })


def _project_status(self, project_id: str):
    project = self._request("GET", "kaban_projects", query={"project_id": f"eq.{project_id}", "select": "project_id,name,enabled,config_hash,updated_at", "limit": "1"}) or []
    jobs = self._request("GET", "kaban_scheduler_jobs", query={"project_id": f"eq.{project_id}", "select": "job_id,handler,cron,timezone,enabled,updated_at", "order": "job_id.asc"}) or []
    executions = self._request("GET", "kaban_executions", query={"project_id": f"eq.{project_id}", "select": "execution_id,operation,state,outcome,scheduled_for,created_at,finished_at", "order": "created_at.desc", "limit": "20"}) or []
    return {"project": project[0] if project else None, "jobs": jobs, "recent_executions": executions}


def _project_health(self, project_id: str):
    now = datetime.now().astimezone()
    now_iso = now.isoformat()
    health = self._request("GET", "kaban_runtime_health", query={"project_id": f"eq.{project_id}", "select": "last_cron_tick", "limit": "1"}) or []
    due = self._request("GET", "kaban_executions", query={"project_id": f"eq.{project_id}", "state": "in.(queued,pending,dispatched,running)", "scheduled_for": f"lte.{now_iso}", "select": "execution_id,scheduled_for,misfire_deadline_at,state", "order": "scheduled_for.asc", "limit": "100"}) or []
    leases = self._request("GET", "kaban_resource_leases", query={"project_id": f"eq.{project_id}", "lease_expires_at": f"lt.{now_iso}", "select": "resource_key", "limit": "100"}) or []
    unknown = self._request("GET", "kaban_publication_steps", query={"project_id": f"eq.{project_id}", "state": "eq.unknown_delivery", "select": "publication_step_id", "limit": "100"}) or []
    future = self._request("GET", "kaban_executions", query={"project_id": f"eq.{project_id}", "scheduled_for": f"gt.{now_iso}", "select": "scheduled_for", "order": "scheduled_for.desc", "limit": "1"}) or []
    furthest = self._dt(future[0].get("scheduled_for")) if future else None
    missed = sum(1 for row in due if self._dt(row.get("misfire_deadline_at")) and self._dt(row.get("misfire_deadline_at")) < now)
    oldest_due = next((row.get("scheduled_for") for row in due if row.get("scheduled_for")), None)
    return {
        "project_id": project_id,
        "last_cron_tick": health[0].get("last_cron_tick") if health else None,
        "oldest_due_execution_at": oldest_due,
        "stale_lease_count": len(leases),
        "missed_slot_count": missed,
        "schedule_horizon_days": max(0.0, (furthest - now).total_seconds() / 86400) if furthest else 0.0,
        "unknown_delivery_count": len(unknown),
    }


SupabaseControlStore.sync_project_config = _sync_project_config
SupabaseControlStore.project_status = _project_status
SupabaseControlStore.project_health = _project_health
