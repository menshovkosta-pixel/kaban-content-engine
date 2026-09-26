from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID


def create_backup(project_id: str, execution_id: UUID, store, artifacts, *, temp_root: Path | None = None, now: str | datetime | None = None):
    if isinstance(now, str):
        timestamp = datetime.fromisoformat(now)
    elif isinstance(now, datetime):
        timestamp = now
    else:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    projection = store.backup_projection(project_id)
    payload = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    key = f"projects/{project_id}/backups/{timestamp:%Y}/{timestamp:%m}/{execution_id}.json"
    parent = Path(temp_root) if temp_root is not None else None
    with TemporaryDirectory(dir=parent) as temp:
        source = Path(temp) / f"{execution_id}.json"
        source.write_bytes(payload)
        return artifacts.put_immutable(project_id, key, source, digest)
