from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from kaban.scheduler.models import SchedulerState


def test_state_round_trip_paths_and_bounded_history(tmp_path: Path):
    from kaban.scheduler.store import SchedulerStore, append_event
    store = SchedulerStore(tmp_path)
    assert store.state_path("demo", "collect") == tmp_path / "scheduler" / "demo" / "collect" / "state.json"
    state = SchedulerState(project_id="demo", job_id="collect")
    for i in range(105):
        state = append_event(state, {"n": i})
    store.save_state(state)
    loaded = store.load_state("demo", "collect")
    assert len(loaded.history) == 100
    assert loaded.history[0]["n"] == 5


def test_default_runtime_dir_honors_environment(tmp_path: Path):
    from kaban.scheduler.store import SchedulerStore
    with patch.dict("os.environ", {"KABAN_RUNTIME_DIR": str(tmp_path)}):
        store = SchedulerStore()
    assert store.state_path("p", "j") == tmp_path / "scheduler" / "p" / "j" / "state.json"


def test_corrupt_state_fails_safe_without_overwrite(tmp_path: Path):
    from kaban.scheduler.store import SchedulerStateError, SchedulerStore
    store = SchedulerStore(tmp_path)
    path = store.state_path("demo", "collect")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SchedulerStateError):
        store.load_state("demo", "collect")
    assert path.read_text(encoding="utf-8") == "{broken"


def test_identity_mismatch_fails_safe(tmp_path: Path):
    from kaban.scheduler.store import SchedulerStateError, SchedulerStore
    store = SchedulerStore(tmp_path)
    path = store.state_path("demo", "collect")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"project_id": "other", "job_id": "collect"}), encoding="utf-8")
    with pytest.raises(SchedulerStateError):
        store.load_state("demo", "collect")


def test_sanitize_exception_redacts_secrets():
    from kaban.scheduler.store import sanitize_exception
    exc = RuntimeError(
        "OPENAI_API_KEY=sk-secret Authorization: Bearer bearer-secret "
        "https://api.telegram.org/bot123456:ABCDEF/sendMessage TELEGRAM_BOT_TOKEN=123456:ABCDEF"
    )
    payload = sanitize_exception(exc)
    text = json.dumps(payload)
    assert "sk-secret" not in text
    assert "bearer-secret" not in text
    assert "123456:ABCDEF" not in text
    assert payload["type"] == "RuntimeError"


def test_lock_busy_expired_and_replacement_safety(tmp_path: Path):
    from kaban.scheduler.store import SchedulerBusyError, SchedulerStore
    store = SchedulerStore(tmp_path)
    now = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    ctx = store.job_lock("demo", "collect", "slot", now_utc=now, lease_seconds=60)
    lock_path = ctx.__enter__()
    try:
        with pytest.raises(SchedulerBusyError):
            with store.job_lock("demo", "collect", "slot", now_utc=now + timedelta(seconds=10), lease_seconds=60):
                pass
        original = json.loads(lock_path.read_text(encoding="utf-8"))
        replacement = dict(original)
        replacement["owner_id"] = "replacement"
        lock_path.write_text(json.dumps(replacement), encoding="utf-8")
    finally:
        ctx.__exit__(None, None, None)
    assert lock_path.exists(), "Первый владелец не должен удалять чужой replacement lock"
    lock_path.unlink()

    # expired lock can be recovered
    expired = {
        "owner_id": "old",
        "pid": 1,
        "project_id": "demo",
        "job_id": "collect",
        "slot_id": "slot",
        "acquired_at": (now - timedelta(hours=2)).isoformat(),
        "lease_expires_at": (now - timedelta(hours=1)).isoformat(),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(expired), encoding="utf-8")
    with store.job_lock("demo", "collect", "slot2", now_utc=now, lease_seconds=60):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_corrupt_lock_fails_safe(tmp_path: Path):
    from kaban.scheduler.store import SchedulerBusyError, SchedulerStore
    store = SchedulerStore(tmp_path)
    lock = tmp_path / "scheduler" / "demo" / "collect" / "job.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("not-json", encoding="utf-8")
    now = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    with pytest.raises(SchedulerBusyError):
        with store.job_lock("demo", "collect", "slot", now_utc=now):
            pass


def test_sanitize_exception_redacts_generic_key_and_token_assignments():
    from kaban.scheduler.store import sanitize_exception
    payload=sanitize_exception(RuntimeError("SERVICE_TOKEN=token-secret CUSTOM_KEY=key-secret"))
    text=json.dumps(payload)
    assert "token-secret" not in text
    assert "key-secret" not in text
