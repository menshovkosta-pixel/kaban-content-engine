from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _project(enabled=True):
    job=SimpleNamespace(id="job1",handler="collect",enabled=True,cron="0 6 * * *")
    auto=SimpleNamespace(enabled=enabled,adapter="demo:run",jobs=(job,))
    return SimpleNamespace(id="demo",timezone="Pacific/Auckland",automation=auto)


class Registry:
    def __init__(self, enabled=True): self.p=_project(enabled)
    def registered(self): return (self.p,)
    def get(self, project_id):
        if project_id != "demo":
            from kaban.projects import ProjectConfigError
            raise ProjectConfigError("unknown demo")
        return self.p


def test_list_shows_project_job_and_enabled_state(capsys):
    import scheduler
    code=scheduler.main(["list"], registry=Registry())
    out=capsys.readouterr().out
    assert code==0
    assert "demo" in out and "job1" in out and "0 6 * * *" in out and "enabled" in out


def test_status_missing_state_is_never_run_without_adapter(capsys,tmp_path):
    import scheduler
    from kaban.scheduler.store import SchedulerStore
    engine=SimpleNamespace(store=SchedulerStore(tmp_path))
    code=scheduler.main(["status"], registry=Registry(), engine=engine)
    out=capsys.readouterr().out
    assert code==0 and "never-run" in out


def test_tick_parses_aware_now_and_rejects_naive(capsys):
    import scheduler
    engine=MagicMock()
    engine.tick.return_value=()
    code=scheduler.main(["tick","--now","2026-09-25T18:00:00+00:00"],registry=Registry(),engine=engine)
    assert code==0
    called=engine.tick.call_args.kwargs["now_utc"]
    assert called.tzinfo is not None
    code=scheduler.main(["tick","--now","2026-09-25T18:00:00"],registry=Registry(),engine=engine)
    assert code!=0


def test_run_now_delegates_and_unknown_job_is_nonzero(capsys):
    import scheduler
    from kaban.scheduler.models import DispatchRecord
    engine=MagicMock()
    engine.run_now.return_value=DispatchRecord("demo","job1","manual:x","success",1,"ok")
    assert scheduler.main(["run-now","--project","demo","--job","job1"],registry=Registry(),engine=engine)==0
    engine.run_now.side_effect=ValueError("Unknown enabled scheduler job")
    assert scheduler.main(["run-now","--project","demo","--job","missing"],registry=Registry(),engine=engine)!=0


def test_foreground_loop_stops_cleanly_and_validates_poll():
    from kaban.scheduler.runner import run_forever
    engine=MagicMock()
    engine.tick.side_effect=[(), KeyboardInterrupt()]
    with patch("kaban.scheduler.runner.time.sleep", return_value=None):
        assert run_forever(engine,poll_seconds=0.1) is None
    with pytest.raises(ValueError): run_forever(engine,poll_seconds=0)
