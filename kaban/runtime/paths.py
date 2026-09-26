from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

PersistenceMode = Literal["local", "cloud"]


def persistence_mode() -> PersistenceMode:
    value = os.getenv("KABAN_PERSISTENCE", "local").strip().lower() or "local"
    if value not in {"local", "cloud"}:
        raise ValueError("KABAN_PERSISTENCE должен быть local или cloud")
    return value  # type: ignore[return-value]


def generated_root(default_root: Path) -> Path:
    override = os.getenv("KABAN_GENERATED_DIR")
    return Path(override).expanduser() if override else Path(default_root)


def runtime_root(default_root: Path) -> Path:
    override = os.getenv("KABAN_RUNTIME_DIR")
    return Path(override).expanduser() if override else Path(default_root)
