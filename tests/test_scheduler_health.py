from datetime import datetime, timedelta, timezone

import pytest

import scheduler
from kaban.scheduler.health import (
    Heartbeat,
    SchedulerHealthError,
    check_heartbeat,
    read_heartbeat,
    write_heartbeat,
)
from kaban.scheduler.runner import run_forever

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)


def test_write_and_read_fresh_heartbeat(tmp_path):
    path = write_heartbeat(now_utc=NOW, pid=123, runtime_dir=tmp_path)
    assert path == tmp_path / "scheduler" / "heartbeat.json"
    heartbeat = read_heartbeat(runtime_dir=tmp_path)
    assert heartbeat.pid == 123
    assert heartbeat.status == "running"
    assert heartbeat.updated_at == NOW
    assert not list(path.parent.glob("*.tmp"))
    assert check_heartbeat(now_utc=NOW + timedelta(seconds=119), runtime_dir=tmp_path).pid == 123


def test_missing_heartbeat_is_unhealthy(tmp_path):
    with pytest.raises(SchedulerHealthError, match="не найден"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)


def test_stale_heartbeat_is_unhealthy(tmp_path):
    write_heartbeat(now_utc=NOW, pid=123, runtime_dir=tmp_path)
    with pytest.raises(SchedulerHealthError, match="устарел"):
        check_heartbeat(
            now_utc=NOW + timedelta(seconds=121), max_age_seconds=120, runtime_dir=tmp_path
        )


def test_corrupt_heartbeat_is_unhealthy(tmp_path):
    path = tmp_path / "scheduler" / "heartbeat.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SchedulerHealthError, match="поврежд"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)


def test_future_heartbeat_beyond_clock_tolerance_is_unhealthy(tmp_path):
    write_heartbeat(now_utc=NOW + timedelta(minutes=10), pid=123, runtime_dir=tmp_path)
    with pytest.raises(SchedulerHealthError, match="будущ"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)


def test_health_cli_returns_zero_for_fresh_heartbeat(monkeypatch):
    heartbeat = Heartbeat(schema_version=1, updated_at=NOW, pid=123, status="running")
    monkeypatch.setattr(scheduler, "check_heartbeat", lambda **_: heartbeat)
    assert scheduler.main(["health"]) == 0


def test_health_cli_returns_one_for_unhealthy_heartbeat(monkeypatch, capsys):
    def fail(**_):
        raise SchedulerHealthError("heartbeat устарел")

    monkeypatch.setattr(scheduler, "check_heartbeat", fail)
    assert scheduler.main(["health"]) == 1
    assert "устарел" in capsys.readouterr().err


def test_run_forever_writes_heartbeat_and_preserves_tick_timestamp():
    ticks = []
    heartbeats = []
    times = iter([NOW, NOW + timedelta(seconds=1)])

    class Engine:
        def tick(self, *, now_utc, project_id=None):
            ticks.append((now_utc, project_id))

    def heartbeat_writer(**kwargs):
        heartbeats.append(kwargs["now_utc"])

    def stop(_seconds):
        raise KeyboardInterrupt

    run_forever(
        Engine(),
        project_id="caelus",
        poll_seconds=30,
        heartbeat_writer=heartbeat_writer,
        now_fn=lambda: next(times),
        sleep_fn=stop,
    )

    assert ticks == [(NOW + timedelta(seconds=1), "caelus")]
    assert heartbeats == [NOW, NOW + timedelta(seconds=1)]
