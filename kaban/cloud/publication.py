from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar
from uuid import UUID

from kaban.runtime.paths import persistence_mode
from kaban.storage import load_json, write_json
from kaban.publishing.telegram import AmbiguousTelegramError, DefinitiveTelegramError


@dataclass(frozen=True)
class StepDecision:
    action: str  # send | skip | manual
    state: str
    external_ids: Mapping[str, Any] | None = None


class ManualReconciliationRequired(RuntimeError):
    """Доставка могла состояться; автоматический повтор запрещён."""


class PublicationCheckpointClient(Protocol):
    def begin_step(self, publication_run_id: UUID, step_key: str, request_fingerprint: str) -> StepDecision: ...
    def mark_sent(self, publication_run_id: UUID, step_key: str, external_ids: Mapping[str, Any]) -> None: ...
    def mark_failed(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None: ...
    def mark_unknown(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None: ...


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _error_payload(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:2000]}


class LocalPublicationCheckpointClient:
    def __init__(self, journal_path: Path):
        self.journal_path = Path(journal_path)

    def _load(self) -> dict[str, Any]:
        payload = load_json(self.journal_path, {}) or {}
        return payload if isinstance(payload, dict) else {}

    def _write_checkpoint(self, step_key: str, payload: Mapping[str, Any]) -> None:
        journal = self._load()
        checkpoints = journal.get("checkpoints")
        if not isinstance(checkpoints, dict):
            checkpoints = {}
        checkpoints[step_key] = dict(payload)
        journal["checkpoints"] = checkpoints
        write_json(self.journal_path, journal)

    @staticmethod
    def _legacy_sent(journal: Mapping[str, Any], step_key: str) -> Mapping[str, Any] | None:
        kind, sep, raw_index = step_key.partition(":")
        if not sep or not raw_index.isdigit():
            return None
        index = int(raw_index)
        if kind == "media":
            for item in journal.get("media") or []:
                if isinstance(item, dict) and int(item.get("group") or 0) == index:
                    return {"message_ids": list(item.get("message_ids") or [])}
        if kind == "text":
            for item in journal.get("text") or []:
                if isinstance(item, dict) and int(item.get("batch") or 0) == index:
                    return {"message_id": item.get("message_id")}
        return None

    def begin_step(self, publication_run_id: UUID, step_key: str, request_fingerprint: str) -> StepDecision:
        journal = self._load()
        legacy = self._legacy_sent(journal, step_key)
        if legacy is not None:
            return StepDecision("skip", "sent", legacy)
        checkpoints = journal.get("checkpoints") if isinstance(journal.get("checkpoints"), dict) else {}
        existing = checkpoints.get(step_key) if isinstance(checkpoints, dict) else None
        if isinstance(existing, dict):
            old_fp = existing.get("request_fingerprint")
            state = str(existing.get("state") or "pending")
            if old_fp and old_fp != request_fingerprint:
                self._write_checkpoint(step_key, {**existing, "state":"unknown_delivery", "last_error":{"type":"FingerprintMismatch","message":"request fingerprint изменился"}})
                return StepDecision("manual", "unknown_delivery")
            if state == "sent":
                return StepDecision("skip", "sent", existing.get("external_ids"))
            if state == "unknown_delivery":
                return StepDecision("manual", state, existing.get("external_ids"))
            if state == "sending":
                self._write_checkpoint(step_key, {**existing, "state":"unknown_delivery", "last_error":{"type":"RecoveredSending","message":"обнаружен незавершённый sending checkpoint"}})
                return StepDecision("manual", "unknown_delivery")
        self._write_checkpoint(step_key, {"state":"sending", "request_fingerprint":request_fingerprint})
        return StepDecision("send", "sending")

    def mark_sent(self, publication_run_id: UUID, step_key: str, external_ids: Mapping[str, Any]) -> None:
        journal = self._load(); cp = ((journal.get("checkpoints") or {}).get(step_key) or {})
        self._write_checkpoint(step_key, {**cp, "state":"sent", "external_ids":dict(external_ids)})

    def mark_failed(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None:
        journal = self._load(); cp = ((journal.get("checkpoints") or {}).get(step_key) or {})
        self._write_checkpoint(step_key, {**cp, "state":"failed", "last_error":dict(error)})

    def mark_unknown(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None:
        journal = self._load(); cp = ((journal.get("checkpoints") or {}).get(step_key) or {})
        self._write_checkpoint(step_key, {**cp, "state":"unknown_delivery", "last_error":dict(error)})


class CloudPublicationCheckpointClient:
    def __init__(self, store, *, project_id: str, publication_run_id: UUID, execution_id: UUID, owner: str, fence_token: int):
        self.store = store
        self.project_id = project_id
        self.publication_run_id = publication_run_id
        self.execution_id = execution_id
        self.owner = owner
        self.fence_token = fence_token
        self._fingerprints: dict[str, str] = {}

    def _transition(self, step_key: str, request_fingerprint: str, from_state: str, to_state: str, *, external_ids=None, error=None):
        return self.store.transition_publication_step(
            project_id=self.project_id, publication_run_id=self.publication_run_id,
            step_key=step_key, request_fingerprint=request_fingerprint,
            from_state=from_state, to_state=to_state, external_ids=external_ids, error=error,
            execution_id=self.execution_id, owner=self.owner, fence_token=self.fence_token,
        )

    def begin_step(self, publication_run_id: UUID, step_key: str, request_fingerprint: str) -> StepDecision:
        if publication_run_id != self.publication_run_id:
            raise ValueError("publication_run_id mismatch")
        self._fingerprints[step_key] = request_fingerprint
        if hasattr(self.store, "get_publication_step"):
            existing = self.store.get_publication_step(self.project_id, self.publication_run_id, step_key)
            if existing:
                if existing.get("request_fingerprint") != request_fingerprint:
                    self._transition(step_key, existing.get("request_fingerprint") or request_fingerprint, str(existing.get("state") or "sending"), "unknown_delivery", error={"type":"FingerprintMismatch"})
                    return StepDecision("manual", "unknown_delivery")
                state = str(existing.get("state") or "pending")
                if state == "sent": return StepDecision("skip", "sent", existing.get("external_ids"))
                if state == "unknown_delivery": return StepDecision("manual", state, existing.get("external_ids"))
                if state == "sending":
                    self._transition(step_key, request_fingerprint, "sending", "unknown_delivery", error={"type":"RecoveredSending"})
                    return StepDecision("manual", "unknown_delivery")
                from_state = state
            else:
                from_state = "pending"
        else:
            from_state = "pending"
        row = self._transition(step_key, request_fingerprint, from_state, "sending")
        state = str((row or {}).get("state") or "sending")
        if state == "sent": return StepDecision("skip", "sent", (row or {}).get("external_ids"))
        if state == "unknown_delivery": return StepDecision("manual", state, (row or {}).get("external_ids"))
        return StepDecision("send", "sending")

    def _fp(self, step_key: str) -> str:
        try: return self._fingerprints[step_key]
        except KeyError as exc: raise RuntimeError("begin_step должен быть вызван до transition") from exc

    def mark_sent(self, publication_run_id: UUID, step_key: str, external_ids: Mapping[str, Any]) -> None:
        self._transition(step_key, self._fp(step_key), "sending", "sent", external_ids=dict(external_ids))

    def mark_failed(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None:
        self._transition(step_key, self._fp(step_key), "sending", "failed", error=dict(error))

    def mark_unknown(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None:
        self._transition(step_key, self._fp(step_key), "sending", "unknown_delivery", error=dict(error))


T = TypeVar("T")
def run_checkpointed_step(client: PublicationCheckpointClient, publication_run_id: UUID, step_key: str, fingerprint: str, send: Callable[[], T], *, external_ids: Callable[[T], Mapping[str, Any]]) -> T | None:
    decision = client.begin_step(publication_run_id, step_key, fingerprint)
    if decision.action == "skip":
        return None
    if decision.action == "manual":
        raise ManualReconciliationRequired(f"{step_key}: состояние {decision.state}; требуется ручная сверка Telegram")
    try:
        result = send()
    except DefinitiveTelegramError as exc:
        client.mark_failed(publication_run_id, step_key, _error_payload(exc))
        raise
    except AmbiguousTelegramError as exc:
        client.mark_unknown(publication_run_id, step_key, _error_payload(exc))
        raise ManualReconciliationRequired(f"{step_key}: результат Telegram неизвестен; автоматический повтор запрещён") from exc
    except Exception as exc:
        client.mark_unknown(publication_run_id, step_key, _error_payload(exc))
        raise ManualReconciliationRequired(f"{step_key}: результат внешнего side effect неизвестен") from exc
    client.mark_sent(publication_run_id, step_key, external_ids(result))
    return result


def checkpoint_client_from_env(*, journal_path: Path, project_id: str, publication_key: str):
    if persistence_mode() != "cloud":
        return LocalPublicationCheckpointClient(journal_path), UUID(int=0)
    from .supabase import SupabaseControlStore
    required = ["KABAN_SUPABASE_URL","KABAN_SUPABASE_SERVICE_KEY","KABAN_EXECUTION_ID","KABAN_EXECUTION_OWNER","KABAN_EXECUTION_FENCE"]
    missing=[name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Cloud publication env не настроен: " + ", ".join(missing))
    store=SupabaseControlStore(os.environ["KABAN_SUPABASE_URL"],os.environ["KABAN_SUPABASE_SERVICE_KEY"])
    execution_id=UUID(os.environ["KABAN_EXECUTION_ID"])
    content_set_id=UUID(os.environ["KABAN_CONTENT_SET_ID"]) if os.getenv("KABAN_CONTENT_SET_ID") else None
    revision_id=UUID(os.environ["KABAN_REVISION_ID"]) if os.getenv("KABAN_REVISION_ID") else None
    run_id=store.create_or_get_publication_run(project_id,publication_key,content_set_id,revision_id,execution_id)
    return CloudPublicationCheckpointClient(store,project_id=project_id,publication_run_id=run_id,execution_id=execution_id,owner=os.environ["KABAN_EXECUTION_OWNER"],fence_token=int(os.environ["KABAN_EXECUTION_FENCE"])), run_id
