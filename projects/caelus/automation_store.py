from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from kaban.storage import load_json, write_json
from projects.caelus import storage as caelus_storage
from projects.caelus.storage import day_dir

GENERATED = caelus_storage.GENERATED

def _generated_root() -> Path:
    # Совместимость с v1.13: внешние тесты/скрипты могли подменять GENERATED этого модуля.
    if GENERATED != caelus_storage.GENERATED:
        return Path(GENERATED)
    return caelus_storage.generated_root()

PROJECT_ID = "caelus"


def automation_dir(day: str, language: str) -> Path:
    day_dir(day, language)  # Только валидация даты и языка.
    return _generated_root() / "_automation" / PROJECT_ID / day / language


def run_path(day: str, language: str) -> Path:
    return automation_dir(day, language) / "run.json"


def load_run(day: str, language: str) -> dict[str, Any]:
    try:
        payload = load_json(run_path(day, language), {}) or {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_run(day: str, language: str, payload: dict[str, Any]) -> None:
    write_json(run_path(day, language), payload)


def next_attempt(run: dict[str, Any], operation: str) -> int:
    attempts = [
        int(item.get("attempt") or 0)
        for item in (run.get("history") or [])
        if isinstance(item, dict) and item.get("operation") == operation
    ]
    if run.get("last_operation") == operation:
        attempts.append(int(run.get("attempt") or 0))
    return max(attempts, default=0) + 1



class AutomationBusyError(RuntimeError):
    pass


@contextmanager
def operation_lock(day: str, language: str, operation: str) -> Iterator[Path]:
    if operation not in {"generate", "publish"}:
        raise ValueError("Unsupported automation operation")
    lock = automation_dir(day, language) / f"{operation}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AutomationBusyError(
            f"Операция {operation} уже заблокирована: {lock}. "
            "Stage 1 не удаляет stale lock автоматически; удалите файл вручную только после проверки, что процесс не работает."
        ) from exc
    payload = {
        "pid": os.getpid(),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    acquired_text = json.dumps(payload, ensure_ascii=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(acquired_text)
            fh.flush()
            os.fsync(fh.fileno())
        yield lock
    finally:
        try:
            if lock.is_file() and lock.read_text(encoding="utf-8") == acquired_text:
                lock.unlink()
        except OSError:
            pass


def sanitize_exception(exc: BaseException) -> dict[str, str]:
    message = str(exc)
    patterns = [
        (r"(?i)(OPENAI_API_KEY\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(?i)(TELEGRAM_BOT_TOKEN\s*=\s*)\S+", r"\1[REDACTED]"),
        (r"(?i)Bearer\s+\S+", "Bearer [REDACTED]"),
        (r"/bot[^/\s]+/", "/bot[REDACTED]/"),
        (r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message)
    return {"type": type(exc).__name__, "message": message[:2000]}


def iter_automation_targets() -> list[tuple[str, str]]:
    root = _generated_root() / "_automation" / PROJECT_ID
    if not root.is_dir():
        return []
    targets: list[tuple[str, str]] = []
    for day_dir_path in root.iterdir():
        if not day_dir_path.is_dir():
            continue
        for language_dir in day_dir_path.iterdir():
            if not language_dir.is_dir() or not (language_dir / "run.json").is_file():
                continue
            try:
                day_dir(day_dir_path.name, language_dir.name)
            except ValueError:
                continue
            targets.append((day_dir_path.name, language_dir.name))
    return sorted(set(targets))
