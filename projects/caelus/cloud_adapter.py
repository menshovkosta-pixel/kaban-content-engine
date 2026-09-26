from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kaban.cloud.models import (
    ArtifactCandidate, CanonicalSnapshot, ChangeSet, ExecutionCommand,
    MaterializationSpec, ProjectExecutionResult, ProjectWorkspaceState,
)
from kaban.storage import content_hash, load_json, write_json
from projects.caelus.automation import run_generation_job, run_publication_job
from projects.caelus.workflow import (
    approve, regenerate_conflicts, regenerate_field, regenerate_sign,
    return_to_draft, save_diversity_settings, save_fields,
)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _parts(command: ExecutionCommand) -> tuple[str, str]:
    key = command.content_key or str(command.payload.get("content_key") or "")
    day, sep, language = key.partition(":")
    if not sep or language not in {"ru", "en"}:
        raise ValueError("CAELUS content_key должен быть YYYY-MM-DD:ru|en")
    return day, language


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CaelusCloudAdapter:
    def schedule_wait_condition(self, *, job, content_key: str, local_slot) -> dict[str, Any] | None:
        if getattr(job, "handler", None) != "publish":
            return None
        return {
            "kind": "content_approved",
            "project_id": "caelus",
            "data": {"content_key": content_key},
        }

    def materialization_spec(self, command: ExecutionCommand) -> MaterializationSpec:
        day, language = _parts(command)
        return MaterializationSpec(
            content_keys=(f"{day}:{language}",), history_locale=language,
            history_before=__import__("datetime").date.fromisoformat(day), history_days=90,
            setting_keys=("content_diversity",), artifact_kinds=("card_png",),
        )

    def resource_key(self, command: ExecutionCommand, snapshot: CanonicalSnapshot) -> str | None:
        if command.operation in {"generate","save","regenerate_field","regenerate_sign","regenerate_conflicts","approve","return_to_draft"}:
            target = command.content_set_id
            if target is None:
                day, language = _parts(command)
                found = next((x for x in snapshot.content_sets if x.content_key == f"{day}:{language}"), None)
                target = found.content_set_id if found else None
            return f"content:{target}" if target else f"content-key:{command.content_key}"
        if command.operation == "publish":
            return f"channel:{command.payload.get('channel_id','telegram_ru')}"
        return None

    def materialize(self, command: ExecutionCommand, snapshot: CanonicalSnapshot, workspace, artifacts) -> ProjectWorkspaceState:
        day, language = _parts(command)
        base = workspace.generated / day / language
        base.mkdir(parents=True, exist_ok=True)
        current = next((x for x in snapshot.content_sets if x.content_key == f"{day}:{language}"), None)
        if current and current.current_revision:
            write_json(base / "content.json", _plain(current.current_revision.payload))
            if current.approved_revision_id:
                write_json(base / "status.json", {
                    "state":"approved", "content_hash":current.approved_content_hash,
                    "revision_id":str(current.approved_revision_id),
                })
            else:
                write_json(base / "status.json", {"state":"draft"})
        if snapshot.publication:
            write_json(base / "publication.json", _plain(snapshot.publication))
        for revision in snapshot.history_revisions:
            payload = _plain(revision.payload)
            hist_day = str(payload.get("iso_date") or "")
            hist_lang = str(payload.get("language") or language)
            if hist_day and hist_day != day and hist_lang == language:
                write_json(workspace.generated / hist_day / hist_lang / "content.json", payload)
        if "content_diversity" in snapshot.settings:
            write_json(workspace.generated / "_settings" / "content_diversity.json", _plain(snapshot.settings["content_diversity"]))
        write_json(workspace.generated / "_automation" / "caelus" / day / language / "run.json", {
            "state":"cloud_materialized", "execution_id":str(command.execution_id), "operation":command.operation,
        })
        for artifact in snapshot.artifacts:
            if artifact.kind not in {"card_png","png","card"}:
                continue
            destination = base / "cards" / artifact.logical_name
            artifacts.download(command.project_id, artifact, destination)
        return ProjectWorkspaceState({"workspace":workspace,"base":base,"day":day,"language":language})

    def execute(self, command: ExecutionCommand, state: ProjectWorkspaceState) -> ProjectExecutionResult:
        day = str(state.data["day"]); language = str(state.data["language"]); payload = dict(command.payload)
        op = command.operation
        if op == "generate":
            result = run_generation_job(day, language, mode=str(payload.get("mode","ai")), model=payload.get("model"), force=bool(payload.get("force",False)))
        elif op == "save":
            result = save_fields(day, language, dict(payload.get("fields") or {}))
        elif op == "regenerate_field":
            if payload.get("fields"): save_fields(day, language, dict(payload["fields"]))
            result = regenerate_field(day, language, str(payload["sign"]), str(payload["field"]))
        elif op == "regenerate_sign":
            if payload.get("fields"): save_fields(day, language, dict(payload["fields"]))
            result = regenerate_sign(day, language, str(payload["sign"]))
        elif op == "regenerate_conflicts":
            if payload.get("settings"):
                settings=dict(payload["settings"]); save_diversity_settings(profile=str(settings["profile"]),history_days=int(settings["history_days"]),custom_threshold=float(settings["custom_threshold"]))
            if payload.get("fields"): save_fields(day, language, dict(payload["fields"]))
            result = regenerate_conflicts(day, language)
        elif op == "approve":
            result = approve(day, language, dict(payload.get("fields") or {}))
        elif op == "return_to_draft":
            return_to_draft(day, language); result = None
        elif op == "publish":
            result = run_publication_job(day, language, dry_run=bool(payload.get("dry_run",False)), force=bool(payload.get("force",False)))
        else:
            raise ValueError(f"Неизвестная CAELUS cloud operation: {op}")
        return ProjectExecutionResult("success", {"result_type":type(result).__name__})

    def collect(self, command: ExecutionCommand, state: ProjectWorkspaceState, before: CanonicalSnapshot) -> ChangeSet:
        base = Path(state.data["base"])
        content = load_json(base / "content.json", None)
        target = next((x for x in before.content_sets if x.content_key == command.content_key), None)
        old_payload = _plain(target.current_revision.payload) if target and target.current_revision else None
        new_payload = content if isinstance(content,dict) and content != old_payload else None
        status = load_json(base / "status.json", {}) or {}
        was_approved = bool(target and target.approved_revision_id)
        is_approved = status.get("state") == "approved"
        approval_action = "approve" if is_approved and not was_approved else "return_to_draft" if was_approved and not is_approved else None
        setting_updates: dict[str,Any] = {}
        settings_path = base.parents[1] / "_settings" / "content_diversity.json"
        if settings_path.exists():
            value = load_json(settings_path,{}) or {}
            if _plain(before.settings.get("content_diversity")) != value:
                setting_updates["content_diversity"] = value
        candidates=[]
        cards=base/"cards"
        if cards.is_dir():
            for path in sorted(cards.glob("*.png")):
                candidates.append(ArtifactCandidate("card_png",path.name,path,_sha(path),path.stat().st_size,"image/png",{}))
        publication = load_json(base / "publication.json", {}) or {}
        before_pub = _plain(before.publication)
        publication_updates = publication if publication != before_pub else {}
        return ChangeSet(
            content_set_id=target.content_set_id if target else command.content_set_id,
            expected_version=target.version if target else command.expected_version,
            new_payload=new_payload, approval_action=approval_action,
            setting_updates=setting_updates, artifacts=tuple(candidates),
            publication_updates=publication_updates, events=(),
        )

adapter = CaelusCloudAdapter()

# --- Stage 4 migration mapping (Project-owned) ---------------------------------
def _migration_parts(source_path: str) -> tuple[str, str, tuple[str, ...]]:
    raw = source_path.split("/", 1)[-1] if source_path.startswith("generated/") else source_path
    parts = tuple(Path(raw).parts)
    if len(parts) < 3:
        raise ValueError(f"CAELUS migration path не содержит date/language: {source_path}")
    return parts[0], parts[1], parts


def _classify_migration_path(root_kind: str, relative: str) -> str:
    parts = Path(relative).parts
    if root_kind == "runtime":
        if relative.endswith(".lock"):
            return "local_lock_not_migrated"
        if relative == "scheduler/heartbeat.json":
            return "runtime_ephemeral_not_migrated"
        if parts and parts[0] == "scheduler" and relative.endswith(".json"):
            return "durable_history"
        return "unknown"
    if parts[:1] == ("_settings",) and relative == "_settings/content_diversity.json":
        return "durable_setting"
    if parts[:1] == ("_automation",):
        if relative.endswith(".lock"):
            return "local_lock_not_migrated"
        if relative.endswith(".json"):
            return "durable_history"
        return "unknown"
    if len(parts) >= 3 and parts[0][:4].isdigit() and parts[1] in {"ru", "en"}:
        tail = parts[2:]
        if tail == ("content.json",): return "durable_content"
        if tail == ("status.json",): return "durable_status"
        if tail == ("publication.json",): return "durable_publication"
        if len(tail) == 2 and tail[0] == "cards" and tail[1].lower().endswith(".png"): return "durable_artifact"
        if (tail and tail[0] in {"telegram", "telegram_media"}): return "regenerable_derivative"
    return "unknown"


def _migration_content_key(source_path: str) -> str:
    day, language, _ = _migration_parts(source_path)
    return f"{day}:{language}"


def _read_migration_content_bundle(generated: Path, content_key: str) -> dict[str, Any]:
    day, language = content_key.split(":", 1)
    base = Path(generated) / day / language
    payload = load_json(base / "content.json", {}) or {}
    status = load_json(base / "status.json", {}) or {}
    publication = load_json(base / "publication.json", {}) or {}
    companions = []
    for name in ("status.json", "publication.json"):
        if (base / name).is_file(): companions.append(f"{day}/{language}/{name}")
    return {
        "content_date": day,
        "locale": language,
        "payload": payload,
        "content_hash": content_hash(payload),
        "approved": status.get("state") == "approved",
        "status": status,
        "publication": publication,
        "artifacts": tuple(sorted((base / "cards").glob("*.png"))) if (base / "cards").is_dir() else (),
        "companion_relative_paths": tuple(companions),
    }


def _read_migration_setting(generated: Path, source_path: str):
    path = Path(generated) / source_path.split("/", 1)[-1]
    return "content_diversity", load_json(path, {}) or {}


def _read_migration_history(generated: Path, runtime: Path, source_path: str):
    root_name, relative = source_path.split("/", 1)
    path = (Path(generated) if root_name == "generated" else Path(runtime)) / relative
    value = load_json(path, {}) or {}
    return {"kind": "automation" if root_name == "generated" else "scheduler", "source": source_path, "data": value}


CaelusCloudAdapter.classify_migration_path = staticmethod(_classify_migration_path)
CaelusCloudAdapter.migration_content_key = staticmethod(_migration_content_key)
CaelusCloudAdapter.read_migration_content_bundle = staticmethod(_read_migration_content_bundle)
CaelusCloudAdapter.read_migration_setting = staticmethod(_read_migration_setting)
CaelusCloudAdapter.read_migration_history = staticmethod(_read_migration_history)
