from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from kaban.storage import load_json, write_json


class SchedulerHealthError(RuntimeError):
    """Heartbeat scheduler отсутствует, повреждён или устарел."""


@dataclass(frozen=True)
class Heartbeat:
    schema_version: int
    updated_at: datetime
    pid: int
    status: str


def _runtime_root(runtime_dir: Path | None = None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir)
    override = os.getenv("KABAN_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "runtime"


def _heartbeat_path(runtime_dir: Path | None = None) -> Path:
    return _runtime_root(runtime_dir) / "scheduler" / "heartbeat.json"


def _strict_heartbeat(payload: Any) -> Heartbeat:
    if not isinstance(payload, dict):
        raise SchedulerHealthError("Heartbeat повреждён: ожидается JSON object")
    if payload.get("schema_version") != 1:
        raise SchedulerHealthError("Heartbeat повреждён: неподдерживаемая schema_version")
    pid = payload.get("pid")
    status = payload.get("status")
    raw_updated = payload.get("updated_at")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise SchedulerHealthError("Heartbeat повреждён: некорректный pid")
    if status != "running":
        raise SchedulerHealthError("Heartbeat повреждён: некорректный status")
    if not isinstance(raw_updated, str):
        raise SchedulerHealthError("Heartbeat повреждён: отсутствует updated_at")
    try:
        updated_at = datetime.fromisoformat(raw_updated)
    except ValueError as exc:
        raise SchedulerHealthError("Heartbeat повреждён: некорректный updated_at") from exc
    if updated_at.tzinfo is None:
        raise SchedulerHealthError("Heartbeat повреждён: updated_at должен быть timezone-aware")
    return Heartbeat(
        schema_version=1,
        updated_at=updated_at.astimezone(timezone.utc),
        pid=pid,
        status=status,
    )


def write_heartbeat(
    *,
    now_utc: datetime | None = None,
    pid: int | None = None,
    runtime_dir: Path | None = None,
) -> Path:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc должен быть timezone-aware")
    now = now.astimezone(timezone.utc)
    process_id = os.getpid() if pid is None else pid
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise ValueError("pid должен быть положительным целым числом")
    path = _heartbeat_path(runtime_dir)
    write_json(
        path,
        {
            "schema_version": 1,
            "updated_at": now.isoformat(),
            "pid": process_id,
            "status": "running",
        },
    )
    return path


def read_heartbeat(*, runtime_dir: Path | None = None) -> Heartbeat:
    path = _heartbeat_path(runtime_dir)
    if not path.exists():
        raise SchedulerHealthError("Heartbeat scheduler не найден")
    try:
        payload = load_json(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SchedulerHealthError("Heartbeat scheduler повреждён") from exc
    return _strict_heartbeat(payload)


def check_heartbeat(
    *,
    max_age_seconds: int = 120,
    now_utc: datetime | None = None,
    runtime_dir: Path | None = None,
) -> Heartbeat:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds должен быть > 0")
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc должен быть timezone-aware")
    now = now.astimezone(timezone.utc)
    heartbeat = read_heartbeat(runtime_dir=runtime_dir)
    age = now - heartbeat.updated_at
    if age < -timedelta(seconds=30):
        raise SchedulerHealthError("Heartbeat находится слишком далеко в будущем")
    if age > timedelta(seconds=max_age_seconds):
        raise SchedulerHealthError("Heartbeat scheduler устарел")
    return heartbeat
