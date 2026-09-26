from __future__ import annotations

from datetime import datetime, timezone
import time

from kaban.scheduler.health import write_heartbeat


def run_forever(
    engine,
    *,
    project_id: str | None = None,
    poll_seconds: float = 30.0,
    heartbeat_writer=write_heartbeat,
    now_fn=lambda: datetime.now(timezone.utc),
    sleep_fn=time.sleep,
) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds должен быть > 0")
    heartbeat_writer(now_utc=now_fn())
    try:
        while True:
            now_utc = now_fn()
            engine.tick(now_utc=now_utc, project_id=project_id)
            heartbeat_writer(now_utc=now_utc)
            sleep_fn(poll_seconds)
    except KeyboardInterrupt:
        return
