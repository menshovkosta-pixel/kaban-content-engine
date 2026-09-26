from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from kaban.scheduler.models import SchedulerState
from kaban.storage import load_json, write_json


class SchedulerStateError(RuntimeError):
    """Scheduler state повреждён или не соответствует запрошенному job."""


class SchedulerBusyError(RuntimeError):
    """Scheduler job уже выполняется другим локальным процессом."""


def _runtime_root() -> Path:
    override = os.getenv("KABAN_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "runtime"


def append_event(state: SchedulerState, event: dict[str, Any]) -> SchedulerState:
    history = (*state.history, dict(event))[-100:]
    return replace(state, history=tuple(history))


def sanitize_exception(exc: BaseException) -> dict[str, str]:
    message = str(exc)
    patterns = [
        (r"(?i)(\b[A-Z][A-Z0-9_]*(?:_TOKEN|_KEY)\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(?i)(OPENAI_API_KEY\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(?i)(TELEGRAM_BOT_TOKEN\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(?i)Authorization:\s*Bearer\s+\S+", "Authorization: Bearer [REDACTED]"),
        (r"(?i)Bearer\s+\S+", "Bearer [REDACTED]"),
        (r"/bot[^/\s]+/", "/bot[REDACTED]/"),
        (r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message)
    return {"type": exc.__class__.__name__, "message": message[:2000]}


def _strict_state(payload: Any, project_id: str, job_id: str) -> SchedulerState:
    if not isinstance(payload, dict):
        raise SchedulerStateError("Scheduler state должен быть JSON object")
    if payload.get("project_id") != project_id or payload.get("job_id") != job_id:
        raise SchedulerStateError("Scheduler state identity mismatch")
    history = payload.get("history", [])
    if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
        raise SchedulerStateError("Scheduler state history повреждён")
    last_error = payload.get("last_error")
    if last_error is not None and not isinstance(last_error, dict):
        raise SchedulerStateError("Scheduler state last_error повреждён")
    try:
        attempt = payload.get("attempt", 0)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise ValueError
        return SchedulerState(
            project_id=project_id,
            job_id=job_id,
            slot_id=payload.get("slot_id"),
            scheduled_for_utc=payload.get("scheduled_for_utc"),
            last_result=payload.get("last_result"),
            attempt=attempt,
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            next_retry_at=payload.get("next_retry_at"),
            last_error=dict(last_error) if last_error else None,
            history=tuple(dict(item) for item in history[-100:]),
        )
    except (TypeError, ValueError) as exc:
        raise SchedulerStateError("Scheduler state содержит некорректные поля") from exc


class SchedulerStore:
    def __init__(self, runtime_dir: Path | None = None):
        base = Path(runtime_dir) if runtime_dir is not None else _runtime_root()
        self.root = base / "scheduler"

    def job_dir(self, project_id: str, job_id: str) -> Path:
        return self.root / project_id / job_id

    def state_path(self, project_id: str, job_id: str) -> Path:
        return self.job_dir(project_id, job_id) / "state.json"

    def lock_path(self, project_id: str, job_id: str) -> Path:
        return self.job_dir(project_id, job_id) / "job.lock"

    def load_state(self, project_id: str, job_id: str) -> SchedulerState:
        path = self.state_path(project_id, job_id)
        if not path.exists():
            return SchedulerState(project_id=project_id, job_id=job_id)
        try:
            payload = load_json(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SchedulerStateError("Не удалось прочитать scheduler state") from exc
        return _strict_state(payload, project_id, job_id)

    def save_state(self, state: SchedulerState) -> None:
        payload = asdict(state)
        payload["history"] = list(state.history[-100:])
        write_json(self.state_path(state.project_id, state.job_id), payload)

    @contextmanager
    def job_lock(
        self,
        project_id: str,
        job_id: str,
        slot_id: str,
        *,
        now_utc: datetime,
        lease_seconds: int = 3600,
    ) -> Iterator[Path]:
        if now_utc.tzinfo is None:
            raise ValueError("now_utc должен быть timezone-aware")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds должен быть > 0")
        path = self.lock_path(project_id, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        owner_id = str(uuid.uuid4())
        now_utc = now_utc.astimezone(timezone.utc)
        payload = {
            "owner_id": owner_id,
            "pid": os.getpid(),
            "project_id": project_id,
            "job_id": job_id,
            "slot_id": slot_id,
            "acquired_at": now_utc.isoformat(),
            "lease_expires_at": (now_utc + timedelta(seconds=lease_seconds)).isoformat(),
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        while True:
            try:
                with path.open("x", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                break
            except FileExistsError as exc:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    expires = datetime.fromisoformat(str(current["lease_expires_at"]))
                    if expires.tzinfo is None:
                        raise ValueError
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as parse_exc:
                    raise SchedulerBusyError("Scheduler lock повреждён; безопасное восстановление требует ручной проверки") from parse_exc
                if expires.astimezone(timezone.utc) > now_utc:
                    raise SchedulerBusyError(f"Scheduler job {project_id}/{job_id} уже заблокирован") from exc
                try:
                    path.unlink()
                except OSError as unlink_exc:
                    raise SchedulerBusyError(f"Не удалось удалить expired scheduler lock {project_id}/{job_id}") from unlink_exc

        try:
            yield path
        finally:
            try:
                if not path.exists():
                    return
                current = json.loads(path.read_text(encoding="utf-8"))
                if current.get("owner_id") == owner_id:
                    path.unlink()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
