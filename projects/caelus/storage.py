from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kaban.storage import content_hash, load_json, write_json
from kaban.runtime.paths import generated_root as resolve_generated_root

from .config import ROOT

GENERATED = ROOT / "generated"


def generated_root() -> Path:
    # Совместимость со старыми тестами, которые подменяли GENERATED напрямую.
    if GENERATED != ROOT / "generated":
        return Path(GENERATED)
    return resolve_generated_root(ROOT / "generated")


def day_dir(day: str, language: str) -> Path:
    if language not in {"ru", "en"}:
        raise ValueError("Unsupported language")
    datetime.strptime(day, "%Y-%m-%d")
    return generated_root() / day / language


def content_path(day: str, language: str) -> Path:
    return day_dir(day, language) / "content.json"


def status_path(day: str, language: str) -> Path:
    return day_dir(day, language) / "status.json"


def publication_path(day: str, language: str) -> Path:
    return day_dir(day, language) / "publication.json"


def publication_history_path(day: str, language: str) -> Path:
    return day_dir(day, language) / "publication_history.json"


def append_publication_event(base: Path, event: str, **data: Any) -> None:
    path = base / "publication_history.json"
    history = load_json(path, []) or []
    if not isinstance(history, list):
        history = []
    history.append({"at": datetime.now(timezone.utc).isoformat(), "event": event, **data})
    write_json(path, history)
