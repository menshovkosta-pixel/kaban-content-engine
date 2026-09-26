# KABAN Project Scheduler Stage 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a universal, project-independent KABAN scheduler that discovers per-Project cron jobs, dispatches them through dynamic Project adapters with persistent slot/retry/lock state, and connects CAELUS generation/publication to that scheduler without changing existing manual CAELUS behavior.

**Architecture:** KABAN owns scheduler configuration parsing, cron/timezone evaluation, due-slot selection, runtime state, locking, retry policy, adapter loading, dispatch isolation, and CLI/foreground execution. Each Project owns the meaning of its handlers through an adapter callable; CAELUS maps generic `generate`/`publish` handlers to its existing v1.11 automation functions. Scheduler runtime metadata is stored under `runtime/scheduler/` and never duplicates CAELUS content/approval/publication state.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`, `datetime`, `zoneinfo`, `importlib`, atomic JSON persistence via `kaban.storage`, PyYAML, `croniter>=6.0`, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-25-kaban-project-scheduler-stage2-design.md`

## Global Constraints

- Universal capability lives under `kaban/`; Project-specific behavior lives under `projects/<project_id>/`.
- KABAN scheduler source must contain **zero** direct import of `projects.caelus`.
- CAELUS scheduler ships disabled by default: no production times are invented in v1.12.
- Existing manual commands remain behavior-compatible: `run_daily.py`, `automation.py`, `publish_telegram.py`, `admin_app.py`.
- Cron expressions use standard five-field syntax and are evaluated in `ProjectConfig.timezone` via `zoneinfo.ZoneInfo`.
- `params` are opaque to KABAN and must never be copied into scheduler state or logs.
- One Project/job failure must not abort other due jobs in the same tick.
- Completed slot identity is `(project_id, job_id, scheduled_for_utc ISO-8601)` and must not be dispatched twice.
- Misfires older than `misfire_grace_minutes` are recorded `missed` and never backfilled later.
- `retry.max_attempts` is defined in this plan as **maximum retries after the initial dispatch**. `0` means no retry; attempt numbers exposed to adapters start at `1` and then increment by one for each retry.
- A `blocked` result may retry the same slot only while retry interval/window/max-retries allow it.
- Adapter exceptions are persisted as `failed` with sanitized messages; adapter import/contract errors are terminal for that dispatch but do not stop other jobs.
- Scheduler runtime state is operational data under ``${KABAN_RUNTIME_DIR}` when set, otherwise `<engine-root>/runtime/scheduler/`` and is not content storage.
- State corruption fails safe: KABAN must not dispatch a job when it cannot reliably read that job's persisted scheduler state.
- Scheduler locking is local-host/file based only; distributed multi-host locking is out of scope.
- No auto-approval, no generic KABAN web dashboard, no service installer, no Instagram/TikTok work in Stage 2.
- Source ZIP has no Git repository. Do **not** initialize fake Git history merely to satisfy workflow mechanics; use test/ledger checkpoints instead of commits.

## Review Focus

1. **DST / timezone boundary:** a Project timezone transition must not create a duplicate UTC slot or evaluate the cron in system-local time. Task 3 adds timezone/DST-oriented engine tests using explicit aware datetimes.
2. **Corrupted `state.json`:** a parse/type failure must block dispatch for that job rather than overwrite state and risk duplication. Task 2 adds fail-safe corruption tests.
3. **Crash after side effect but before KABAN success write:** after lease expiry the slot may be retried, so Core must preserve the same slot ID and CAELUS adapter must remain idempotent. Tasks 2, 3, and 5 test recovery/idempotence boundaries.
4. **Disabled/invalid Project adapter among healthy Projects:** the bad job must be reported without preventing another due Project job from succeeding. Tasks 3 and 4 add isolation tests.
5. **Retry boundary exactness:** `window_minutes`, `interval_minutes`, and `max_attempts=0` must not create an off-by-one retry. Task 3 adds boundary tests for exact window expiry and retry count.

---

## File/Module Map

### New KABAN scheduler package

- `kaban/scheduler/__init__.py` — public exports only.
- `kaban/scheduler/models.py` — immutable scheduler domain models (`RetryPolicy`, `ScheduledJob`, `JobContext`, `JobResult`, `SchedulerState`, `DispatchRecord`).
- `kaban/scheduler/config.py` — flatten validated `ProjectConfig.automation` into generic `ScheduledJob` values.
- `kaban/scheduler/adapter.py` — dynamic `module:function` loader and callable contract validation.
- `kaban/scheduler/store.py` — runtime paths, atomic state persistence, bounded history, sanitization, exclusive lock/lease recovery.
- `kaban/scheduler/engine.py` — cron slot calculation, misfire/retry decisions, isolated dispatch, `tick`, `run_now`.
- `kaban/scheduler/runner.py` — foreground polling loop.

### Project integration

- `projects/caelus/scheduler.py` — CAELUS adapter only; converts scheduled local date/params into v1.11 automation calls.
- `projects/caelus/project.yaml` — disabled-by-default scheduler section.

### Root operator entrypoint

- `scheduler.py` — thin CLI for `list`, `status`, `tick`, `run`, `run-now`.

### Existing files modified

- `kaban/projects.py` — optional Project automation config dataclasses + validation.
- `requirements.txt` — `croniter>=6.0`.
- `README_RU.md`, `ARCHITECTURE.md`, `VERSION`, new release notes — v1.12 operator docs and release boundary.

### New tests

- `tests/test_scheduler_config.py`
- `tests/test_scheduler_store.py`
- `tests/test_scheduler_engine.py`
- `tests/test_scheduler_adapter.py`
- `tests/test_caelus_scheduler.py`
- `tests/test_scheduler_cli.py`
- `tests/test_scheduler_acceptance.py`

---

### Task 1: Project automation configuration and scheduler models

**Files:**
- Modify: `requirements.txt`
- Modify: `kaban/projects.py`
- Create: `kaban/scheduler/__init__.py`
- Create: `kaban/scheduler/models.py`
- Create: `kaban/scheduler/config.py`
- Modify: `projects/caelus/project.yaml`
- Create: `tests/test_scheduler_config.py`
- Existing regression: `tests/test_projects.py`

**Interfaces:**
- Consumes: existing `ProjectConfig`, `ProjectRegistry`, `load_project_config(Path)`.
- Produces:
  - `RetryPolicy(interval_minutes: int, window_minutes: int, max_attempts: int)`
  - `ProjectJobConfig(id: str, handler: str, enabled: bool, cron: str, params: dict[str, Any], misfire_grace_minutes: int, retry: RetryPolicy)`
  - `ProjectAutomationConfig(enabled: bool, adapter: str | None, jobs: tuple[ProjectJobConfig, ...])`
  - `ProjectConfig.automation: ProjectAutomationConfig`
  - `ScheduledJob`, `JobContext`, `JobResult`, `SchedulerState`, `DispatchRecord`
  - `iter_scheduled_jobs(registry: ProjectRegistry, project_id: str | None = None) -> tuple[ScheduledJob, ...]`

- [ ] **Step 1: Add failing Project config tests**

Create `tests/test_scheduler_config.py` with focused cases equivalent to:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kaban.projects import ProjectConfigError, load_project_config


def _write_project(tmp: Path, automation: str = "") -> Path:
    path = tmp / "project.yaml"
    path.write_text(
        f"""
id: demo
name: Demo
default_language: ru
supported_languages: [ru]
timezone: Pacific/Auckland
ai:
  model: gpt-5.6-luna
  reasoning_effort: low
uniqueness:
  history_days: 90
  warning_threshold: 0.76
  hard_threshold: 0.80
  max_regeneration_attempts: 3
publication:
  telegram:
    album_group_size: 6
{automation}
""".strip() + "\n",
        encoding="utf-8",
    )
    return path


def test_missing_automation_is_disabled_and_backward_compatible():
    with TemporaryDirectory() as raw:
        cfg = load_project_config(_write_project(Path(raw)))
    assert cfg.automation.enabled is False
    assert cfg.automation.adapter is None
    assert cfg.automation.jobs == ()


def test_valid_automation_parses_jobs_and_opaque_params():
    automation = """
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      enabled: true
      cron: "*/15 * * * *"
      params:
        source: primary
      misfire_grace_minutes: 20
      retry:
        interval_minutes: 5
        window_minutes: 60
        max_attempts: 4
"""
    with TemporaryDirectory() as raw:
        cfg = load_project_config(_write_project(Path(raw), automation))
    job = cfg.automation.jobs[0]
    assert job.id == "collect"
    assert job.params == {"source": "primary"}
    assert job.retry.max_attempts == 4


@pytest.mark.parametrize("cron", ["bad cron", "61 * * * *", "* * *"])
def test_invalid_cron_is_rejected(cron: str):
    automation = f"""
automation:
  enabled: true
  adapter: projects.demo.scheduler:run_job
  jobs:
    - id: collect
      handler: collect
      cron: "{cron}"
      params: {{}}
      misfire_grace_minutes: 0
      retry:
        interval_minutes: 0
        window_minutes: 0
        max_attempts: 0
"""
    with TemporaryDirectory() as raw:
        path = _write_project(Path(raw), automation)
        with pytest.raises(ProjectConfigError, match="cron"):
            load_project_config(path)
```

Also add explicit tests for duplicate job IDs, enabled automation without adapter/jobs, non-mapping `params`, invalid IANA timezone, negative retry values, and `warning_threshold` legacy behavior remaining unchanged.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_scheduler_config.py tests/test_projects.py -q
```

Expected before implementation: failures because `ProjectConfig` has no `automation` field and scheduler config types do not exist.

- [ ] **Step 3: Add dependency and immutable scheduler/config models**

Add to `requirements.txt`:

```text
croniter>=6.0
```

Install the declared dependency in the isolated execution environment before the GREEN run:

```bash
python -m pip install -r requirements.txt
```

Then create `kaban/scheduler/models.py` with these exact public shapes:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    interval_minutes: int = 0
    window_minutes: int = 0
    max_attempts: int = 0  # число повторов после первой попытки


@dataclass(frozen=True)
class ScheduledJob:
    project_id: str
    job_id: str
    handler: str
    cron: str
    timezone: str
    params: dict[str, Any]
    misfire_grace_minutes: int
    retry: RetryPolicy
    adapter: str


@dataclass(frozen=True)
class JobContext:
    project_id: str
    job_id: str
    scheduled_for_utc: datetime
    scheduled_for_local: datetime
    now_utc: datetime
    trigger: str
    attempt: int


@dataclass(frozen=True)
class JobResult:
    outcome: str
    message: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class SchedulerState:
    project_id: str
    job_id: str
    slot_id: str | None = None
    scheduled_for_utc: str | None = None
    last_result: str | None = None
    attempt: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    next_retry_at: str | None = None
    last_error: dict[str, str] | None = None
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DispatchRecord:
    project_id: str
    job_id: str
    slot_id: str
    result: str
    attempt: int
    message: str = ""
```

Use `RetryPolicy` in `kaban.projects.ProjectJobConfig`; import it from `kaban.scheduler.models` so there is one retry type across config and engine.

- [ ] **Step 4: Implement optional automation parsing in `kaban/projects.py`**

Add frozen dataclasses:

```python
@dataclass(frozen=True)
class ProjectJobConfig:
    id: str
    handler: str
    enabled: bool
    cron: str
    params: dict[str, Any]
    misfire_grace_minutes: int
    retry: RetryPolicy


@dataclass(frozen=True)
class ProjectAutomationConfig:
    enabled: bool
    adapter: str | None
    jobs: tuple[ProjectJobConfig, ...]
```

Extend `ProjectConfig` with:

```python
automation: ProjectAutomationConfig
```

Parsing rules to implement exactly:

```text
missing automation                       -> disabled, adapter=None, jobs=()
enabled=false                            -> adapter/jobs optional
enabled=true                             -> non-empty adapter + at least one job
job id/handler/cron                      -> non-empty strings
job enabled                              -> bool, default true
params                                   -> mapping, default {}
misfire_grace_minutes                    -> integer >= 0, default 0
retry.interval_minutes/window_minutes    -> integer >= 0, default 0
retry.max_attempts                       -> integer >= 0, default 0
duplicate job id                         -> ProjectConfigError
croniter(cron) invalid                   -> ProjectConfigError
ZoneInfo(timezone) invalid               -> ProjectConfigError
```

Never include raw malformed YAML payloads/secrets in exception messages; preserve the existing sanitized YAML error behavior.

- [ ] **Step 5: Implement generic job flattening**

Create `kaban/scheduler/config.py`:

```python
from __future__ import annotations

from kaban.projects import ProjectRegistry
from kaban.scheduler.models import ScheduledJob


def iter_scheduled_jobs(
    registry: ProjectRegistry,
    project_id: str | None = None,
) -> tuple[ScheduledJob, ...]:
    projects = (registry.get(project_id),) if project_id else registry.registered()
    jobs: list[ScheduledJob] = []
    for project in projects:
        automation = project.automation
        if not automation.enabled or not automation.adapter:
            continue
        for item in automation.jobs:
            if not item.enabled:
                continue
            jobs.append(
                ScheduledJob(
                    project_id=project.id,
                    job_id=item.id,
                    handler=item.handler,
                    cron=item.cron,
                    timezone=project.timezone,
                    params=dict(item.params),
                    misfire_grace_minutes=item.misfire_grace_minutes,
                    retry=item.retry,
                    adapter=automation.adapter,
                )
            )
    return tuple(jobs)
```

- [ ] **Step 6: Add disabled CAELUS scheduler config**

Append to `projects/caelus/project.yaml` exactly:

```yaml
automation:
  enabled: false
  adapter: "projects.caelus.scheduler:run_job"
  jobs: []
```

This must make v1.12 scheduler inert by default.

- [ ] **Step 7: Verify Task 1 GREEN**

Run:

```bash
python -m pytest tests/test_scheduler_config.py tests/test_projects.py -q
python -m pytest -q
```

Expected: focused tests pass and the existing suite remains green.

- [ ] **Checkpoint (source ZIP has no Git)**

Record in the execution ledger: Task 1 complete, focused command, full-suite command, counts, and any ruling. Do not initialize a repository solely for a commit.

---

### Task 2: Scheduler runtime store, sanitization, and lock leases

**Files:**
- Create: `kaban/scheduler/store.py`
- Create: `tests/test_scheduler_store.py`
- Reuse: `kaban/storage/__init__.py`

**Interfaces:**
- Consumes: `SchedulerState`, atomic `load_json` / `write_json` from `kaban.storage`.
- Produces:
  - `SchedulerStore(runtime_dir: Path | None = None)`; default root resolves from `KABAN_RUNTIME_DIR` or `<engine-root>/runtime`, then appends `/scheduler`
  - `SchedulerStore.state_path(project_id, job_id) -> Path`
  - `SchedulerStore.load_state(project_id, job_id) -> SchedulerState`
  - `SchedulerStore.save_state(state: SchedulerState) -> None`
  - `append_event(state, event) -> SchedulerState` with history capped at 100
  - `sanitize_exception(exc: BaseException) -> dict[str, str]`
  - `SchedulerStore.job_lock(project_id, job_id, slot_id, *, now_utc, lease_seconds=3600)` context manager
  - `SchedulerStateError`, `SchedulerBusyError`

- [ ] **Step 1: Add RED tests for state persistence and bounded history**

Create tests that instantiate `SchedulerStore(Path(raw))` and assert paths resolve to:

```text
<raw>/scheduler/<project_id>/<job_id>/state.json
<raw>/scheduler/<project_id>/<job_id>/job.lock
```

Add a separate environment test with `KABAN_RUNTIME_DIR=<raw>` and `SchedulerStore()` to prove the override uses the same root. Round-trip a `SchedulerState`, append 105 events, and assert only the newest 100 persist.

- [ ] **Step 2: Add RED test for corrupted state fail-safe behavior**

Write invalid JSON to `state.json` and assert:

```python
with pytest.raises(SchedulerStateError):
    load_state("demo", "collect")
```

The implementation must **not** replace or truncate the corrupted file during this read.

- [ ] **Step 3: Add RED sanitization tests**

Use an exception message containing all of:

```text
OPENAI_API_KEY=sk-secret
Authorization: Bearer bearer-secret
https://api.telegram.org/bot123456:ABCDEF/sendMessage
TELEGRAM_BOT_TOKEN=123456:ABCDEF
```

Assert none of `sk-secret`, `bearer-secret`, or `123456:ABCDEF` appears in persisted/sanitized output.

- [ ] **Step 4: Add RED lock tests**

Cover:

```text
first acquisition succeeds
second acquisition before lease expiry -> SchedulerBusyError
expired lock is removed/re-acquired
release removes only the exact lock instance it created
replacement lock created during execution is not deleted by the first owner
```

Use deterministic aware UTC datetimes and a short test lease.

- [ ] **Step 5: Run focused tests and verify RED**

```bash
python -m pytest tests/test_scheduler_store.py -q
```

Expected: import/attribute failures because `kaban.scheduler.store` is not implemented.

- [ ] **Step 6: Implement state paths and strict decoding**

`SchedulerStore.load_state()` behavior:

```text
missing file -> empty SchedulerState(project_id=project_id, job_id=job_id)
valid object -> strict field coercion with project/job identity check
invalid JSON / wrong top-level type / mismatched project/job -> SchedulerStateError
```

Do not silently turn corrupt state into an empty state.

- [ ] **Step 7: Implement bounded history and sanitized errors**

`SchedulerStore.save_state()` must write atomically via `kaban.storage.write_json`.

`sanitize_exception()` returns only:

```python
{"type": exc.__class__.__name__, "message": sanitized_message[:2000]}
```

Do not persist traceback, params, environment, or adapter arguments.

- [ ] **Step 8: Implement exclusive file lock with lease recovery**

Use atomic exclusive creation (`open(lock_path, "x", encoding="utf-8")`) with a payload containing:

```json
{
  "owner_id": "<uuid4>",
  "pid": 1234,
  "project_id": "demo",
  "job_id": "collect",
  "slot_id": "2026-09-25T18:00:00+00:00",
  "acquired_at": "2026-09-25T18:00:00+00:00",
  "lease_expires_at": "2026-09-25T19:00:00+00:00"
}
```

On existing lock:

```text
valid + unexpired -> SchedulerBusyError
valid + expired   -> remove, then retry exclusive create
corrupt lock      -> SchedulerBusyError (fail safe; do not guess ownership)
```

On context exit, re-read the lock and unlink only when `owner_id` still matches.

- [ ] **Step 9: Verify Task 2 GREEN**

```bash
python -m pytest tests/test_scheduler_store.py -q
python -m pytest -q
```

Expected: all store tests and full regression suite pass.

- [ ] **Checkpoint**

Record Task 2 evidence in the execution ledger; no fake Git commit.

---

### Task 3: Scheduler engine — cron slots, misfires, retries, and failure isolation

**Files:**
- Create: `kaban/scheduler/engine.py`
- Create: `tests/test_scheduler_engine.py`
- Consume: `kaban/scheduler/config.py`, `models.py`, `store.py`, `adapter.py` interface defined in Task 4 but injected in tests here.

**Interfaces:**
- Consumes: `ScheduledJob`, `JobContext`, `JobResult`, `SchedulerState`, `SchedulerStore`. Adapter loading is injected in Task 3; the production default is wired in Task 4.
- Produces:
  - `latest_slot(job: ScheduledJob, now_utc: datetime) -> tuple[datetime, datetime]`
  - `SchedulerEngine(registry, *, store: SchedulerStore, adapter_loader: Callable[[str], Callable])` during Task 3; Task 4 makes `store`/`adapter_loader` optional defaults
  - `SchedulerEngine.tick(now_utc: datetime, project_id: str | None = None) -> tuple[DispatchRecord, ...]`
  - `SchedulerEngine.run_now(project_id: str, job_id: str, *, now_utc: datetime | None = None) -> DispatchRecord`
  - internal `_dispatch(job, slot_utc, slot_local, *, trigger, now_utc, force=False)` shared by scheduled/retry/manual execution.

**Ruling for spec ambiguity:** `retry.max_attempts` means retry count **after** the initial dispatch. Thus `0` = one initial attempt and no retry. This preserves the spec's non-negative validation and avoids making `0` disable the job itself.

- [ ] **Step 1: Add RED due-slot and exactly-once tests**

Use a fake registry/config and injected adapter loader. Assert a job `cron="0 6 * * *"`, timezone `Pacific/Auckland`, evaluated at its exact local 06:00 slot dispatches once and writes success. A second tick with the same `now_utc` returns no second adapter call.

- [ ] **Step 2: Add RED timezone and DST tests**

Use `ZoneInfo("Pacific/Auckland")` aware dates around DST transition. Assert:

```text
cron is matched against Project-local wall time
persisted slot_id is UTC ISO-8601
same persisted UTC slot cannot dispatch twice
system/process timezone is irrelevant
```

Do not assert that a nonexistent local wall time magically runs; assert deterministic conversion and no duplicate dispatch for actual candidate occurrences returned by croniter/zoneinfo.

- [ ] **Step 3: Add RED misfire tests**

For a slot 20 minutes late:

```text
misfire_grace_minutes=30 -> dispatch
misfire_grace_minutes=10 -> record missed, adapter not called
second tick -> same missed slot not re-recorded/re-dispatched
```

- [ ] **Step 4: Add RED blocked/retry boundary tests**

Fake adapter returns:

```python
JobResult(outcome="blocked", retryable=True, message="awaiting approval")
```

With `interval_minutes=10`, `window_minutes=30`, `max_attempts=2`, assert:

```text
attempt 1 at scheduled slot -> blocked, next_retry_at +10m
+9m tick -> no call
+10m tick -> attempt 2
+20m tick -> attempt 3 (second and final retry)
subsequent tick -> terminal, no attempt 4
```

Also assert `max_attempts=0` performs initial attempt only, and exact `window_minutes` expiry does not schedule a retry after the window.

Add an overlapping-slot case: when an older slot is still `blocked` with an active retry window and a newer cron occurrence has arrived, the engine must continue/finish the older open slot first and must not start the newer occurrence concurrently. Once the older slot becomes terminal or its retry window closes, a later tick may evaluate the newest cron slot.

- [ ] **Step 5: Add RED failure-isolation tests**

Create two due jobs. First adapter raises `RuntimeError("OPENAI_API_KEY=sk-secret provider down")`; second returns success. Assert:

```text
both jobs were evaluated
first DispatchRecord.result == "failed"
second DispatchRecord.result == "success"
secret is absent from persisted state
```

- [ ] **Step 6: Add RED state-corruption and lock-busy tests**

Assert:

```text
corrupted state for job A -> no adapter call for A, DispatchRecord failed/safe
job B still runs
active job lock -> result busy, adapter not called
expired lock -> recovered and adapter called once
```

- [ ] **Step 7: Run focused tests and verify RED**

```bash
python -m pytest tests/test_scheduler_engine.py -q
```

Expected: failures because engine behavior is not implemented.

- [ ] **Step 8: Implement cron candidate selection**

Implementation rules:

```python
local_now = now_utc.astimezone(ZoneInfo(job.timezone))
minute_now = local_now.replace(second=0, microsecond=0)
if croniter.match(job.cron, minute_now):
    slot_local = minute_now
else:
    slot_local = croniter(job.cron, minute_now).get_prev(datetime)
slot_utc = slot_local.astimezone(timezone.utc)
```

Require `now_utc` to be timezone-aware; reject naive datetime with `ValueError`.

- [ ] **Step 9: Implement state-machine decisions**

Decision order per job:

```text
1. load state; corruption => safe failed record, no dispatch
2. if persisted slot is blocked/failed and still has an open retry window, resolve that slot first:
   - before next_retry_at -> no dispatch
   - retry due + limits allow -> retry the same persisted slot
   - limits/window exhausted -> clear next_retry_at and leave the slot terminal
3. calculate the latest cron slot only after no older retry slot is open
4. if latest slot already success/skipped/missed -> no dispatch
5. if new slot delay > grace -> persist missed
6. otherwise initial dispatch attempt=1
```

Persist history events with: `slot_id`, `result`, `attempt`, `trigger`, `started_at`, `finished_at`, and optional sanitized `error_type`. Do not persist `job.params`.

- [ ] **Step 10: Implement isolated dispatch and retry persistence**

Adapter result mapping:

```text
success -> terminal success
skipped -> terminal skipped
blocked retryable=true -> blocked + next_retry_at if policy allows
blocked retryable=false -> terminal blocked
invalid JobResult.outcome -> failed terminal
adapter exception -> failed; retry according to policy while limits allow
adapter-load failure -> failed terminal for that dispatch
```

`SchedulerEngine.tick()` must catch per-job/store/adapter failures, return a record, and continue iterating later jobs. Only registry/global configuration construction errors escape the whole tick.

- [ ] **Step 11: Implement manual `run_now`**

`run_now()` selects the configured job without cron due evaluation and creates a manual slot:

```text
manual:<now_utc ISO-8601>
```

It still uses the same lock, adapter, persistence, and Project preconditions. Every explicit operator call gets a new manual slot; it does not mutate or force-complete an existing scheduled slot.

- [ ] **Step 12: Verify Task 3 GREEN**

```bash
python -m pytest tests/test_scheduler_engine.py -q
python -m pytest -q
```

Expected: engine tests and full suite pass.

- [ ] **Checkpoint**

Record Task 3 evidence/rulings in ledger.

---

### Task 4: Dynamic Project adapter loading

**Files:**
- Create: `kaban/scheduler/adapter.py`
- Create: `tests/test_scheduler_adapter.py`
- Modify: `kaban/scheduler/__init__.py`

**Interfaces:**
- Consumes: entrypoint string `module.path:function`, `ScheduledJob`, `JobContext`, `JobResult`.
- Produces:
  - `AdapterLoadError(RuntimeError)`
  - `load_adapter(entrypoint: str) -> Callable[[ScheduledJob, JobContext], JobResult]`

- [ ] **Step 1: Add RED dynamic loader tests**

Create temporary/importable synthetic modules through `types.ModuleType` + `mock.patch.dict(sys.modules, module_map)` and assert:

```text
valid callable loads
missing ':' rejected
missing module rejected with sanitized AdapterLoadError
missing attribute rejected
non-callable attribute rejected
```

Also inspect all Python source under `kaban/scheduler/` and assert the literal `projects.caelus` is absent.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m pytest tests/test_scheduler_adapter.py -q
```

- [ ] **Step 3: Implement loader using `importlib.import_module`**

Implementation shape:

```python
def load_adapter(entrypoint: str):
    module_name, sep, attr_name = entrypoint.partition(":")
    if not sep or not module_name or not attr_name:
        raise AdapterLoadError("Scheduler adapter must use module.path:function format")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise AdapterLoadError(
            f"Не удалось импортировать scheduler adapter module {module_name}: {exc.__class__.__name__}"
        ) from exc
    target = getattr(module, attr_name, None)
    if not callable(target):
        raise AdapterLoadError(f"Scheduler adapter {entrypoint} не является callable")
    return target
```

Do not include adapter exception messages that may contain secrets in load errors.

- [ ] **Step 4: Wire production defaults into the engine**

Change the Task 3 constructor to:

```python
def __init__(self, registry, *, store=None, adapter_loader=None):
    self.registry = registry
    self.store = store or SchedulerStore()
    self.adapter_loader = adapter_loader or load_adapter
```

This is the first task where production construction without injected fakes is required.

- [ ] **Step 5: Verify Task 4 GREEN**

```bash
python -m pytest tests/test_scheduler_adapter.py tests/test_scheduler_engine.py -q
python -m pytest -q
```

- [ ] **Checkpoint**

Record Task 4 evidence.

---

### Task 5: CAELUS scheduler adapter on top of Stage 1 automation

**Files:**
- Create: `projects/caelus/scheduler.py`
- Create: `tests/test_caelus_scheduler.py`
- Reuse unchanged: `projects/caelus/automation.py`

**Interfaces:**
- Consumes:
  - `run_generation_job(day, language, mode="ai", model=None, force=False) -> AutomationState`
  - `run_publication_job(day, language, dry_run=False, force=False) -> AutomationState`
  - `AutomationPreconditionError` from `projects.caelus.automation`
  - `AutomationBusyError` from `projects.caelus.automation_store`
  - `ScheduledJob`, `JobContext`, `JobResult`
- Produces:
  - `run_job(job: ScheduledJob, context: JobContext) -> JobResult`

- [ ] **Step 1: Add RED target-date tests**

Construct `JobContext` where UTC date differs from Auckland local date and assert adapter computes target day from `context.scheduled_for_local.date()`, then applies integer `target_date_offset_days`.

Example:

```python
scheduled_local = datetime(2026, 9, 26, 0, 30, tzinfo=ZoneInfo("Pacific/Auckland"))
# target_date_offset_days=-1 -> 2026-09-25
```

- [ ] **Step 2: Add RED generate mapping tests**

For handler `generate`, params validation/mapping must be:

```text
language required and one of ru/en
mode optional, default ai, allowed ai/mock
target_date_offset_days optional integer, default 0
model optional string
unknown handler -> terminal JobResult skipped/non-retryable with clear message OR ValueError caught by engine as failed
```

Prefer explicit `ValueError` for malformed Project-owned job config so KABAN records `failed` rather than silently skipping an operator mistake.

Patch `projects.caelus.scheduler.run_generation_job` and assert exact call arguments. Map returned Stage 1 state:

```text
last_result == success -> JobResult(success)
last_result == skipped -> JobResult(skipped)
```

- [ ] **Step 3: Add RED publish mapping tests**

Patch `run_publication_job` and assert:

```text
published/success -> success
already-published Stage 1 skip -> skipped
AutomationPreconditionError -> blocked, retryable=True
AutomationBusyError -> blocked, retryable=True
unexpected exception -> propagates
```

The adapter must never auto-approve.

- [ ] **Step 4: Add RED idempotence boundary test**

Use a temporary CAELUS generated directory or Stage 1 fakes to assert a repeated scheduler invocation of generate/publish delegates to Stage 1 guards rather than bypassing them with force.

Scheduled handlers must call Stage 1 automation with `force=False`.

- [ ] **Step 5: Run focused tests and verify RED**

```bash
python -m pytest tests/test_caelus_scheduler.py -q
```

- [ ] **Step 6: Implement `projects/caelus/scheduler.py`**

Use helpers:

```python
def _target_day(context: JobContext, params: dict[str, Any]) -> str:
    offset = params.get("target_date_offset_days", 0)
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError("target_date_offset_days должен быть целым числом")
    return (context.scheduled_for_local.date() + timedelta(days=offset)).isoformat()
```

`run_job()` branches only on Project-owned `job.handler`; KABAN remains unaware of these names.

- [ ] **Step 7: Verify Task 5 GREEN**

```bash
python -m pytest tests/test_caelus_scheduler.py tests/test_automation.py -q
python -m pytest -q
```

Expected: CAELUS adapter tests and all Stage 1 automation regressions pass.

- [ ] **Checkpoint**

Record Task 5 evidence.

---

### Task 6: Universal scheduler CLI and foreground runner

**Files:**
- Create: `kaban/scheduler/runner.py`
- Create: `scheduler.py`
- Create: `tests/test_scheduler_cli.py`

**Interfaces:**
- Consumes: `ProjectRegistry`, `iter_scheduled_jobs`, `SchedulerEngine`, `load_state`.
- Produces:
  - `run_forever(engine: SchedulerEngine, *, project_id: str | None = None, poll_seconds: float = 30.0) -> None`
  - CLI commands `list`, `status`, `tick`, `run`, `run-now`.

- [ ] **Step 1: Add RED parser/list tests**

Patch a registry with enabled/disabled jobs and assert:

```text
scheduler.py list
scheduler.py list --project caelus
```

produce deterministic rows containing project/job/handler/cron/timezone/enabled state. Disabled projects/jobs must be visible in `list` when reading Project config, even though `iter_scheduled_jobs()` excludes them from dispatch.

- [ ] **Step 2: Add RED status tests**

`status [--project]` reads persisted scheduler state only; it must not import/call Project adapters. Missing state prints `never-run` for configured jobs.

- [ ] **Step 3: Add RED deterministic tick tests**

For:

```bash
python scheduler.py tick --now 2026-09-25T18:00:00+00:00
```

assert the CLI parses an aware ISO-8601 datetime and calls `engine.tick(now_utc=parsed_now, project_id=project_filter)`.

Naive `--now` must fail clearly instead of assuming system timezone.

- [ ] **Step 4: Add RED run-now and invalid ID tests**

Assert:

```text
valid project/job -> engine.run_now(project_id, job_id, now_utc=parsed_now)
unknown project -> non-zero + concise ProjectConfigError
unknown job -> non-zero + concise scheduler error
```

No Project safety/precondition bypass flags are added in Stage 2.

- [ ] **Step 5: Add RED foreground loop test**

Patch `engine.tick` and `time.sleep`; force `KeyboardInterrupt` after two iterations and assert the loop exits cleanly without converting Ctrl+C into an error traceback.

Reject `poll_seconds <= 0`.

- [ ] **Step 6: Run focused tests and verify RED**

```bash
python -m pytest tests/test_scheduler_cli.py -q
```

- [ ] **Step 7: Implement `runner.py`**

```python
def run_forever(engine, *, project_id=None, poll_seconds=30.0):
    if poll_seconds <= 0:
        raise ValueError("poll_seconds должен быть > 0")
    try:
        while True:
            engine.tick(now_utc=datetime.now(timezone.utc), project_id=project_id)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return
```

- [ ] **Step 8: Implement thin root `scheduler.py`**

Use `argparse` subparsers. Root CLI responsibilities are only:

```text
load registry
construct engine
parse operator args
call list/status/tick/run/run-now
format concise human-readable output
return non-zero for scheduler-wide config/CLI errors
```

Per-job failures returned by `tick` are printed but do not make the process abort mid-tick. `tick` exits 0 when scheduler itself ran successfully even if one Project job produced a recorded `failed` result, matching the spec's failure-isolation rule.

- [ ] **Step 9: Verify Task 6 GREEN**

```bash
python -m pytest tests/test_scheduler_cli.py -q
python -m pytest -q
```

- [ ] **Checkpoint**

Record Task 6 evidence.

---

### Task 7: Multi-Project acceptance, regression, docs, and v1.12 release

**Files:**
- Create: `tests/test_scheduler_acceptance.py`
- Modify: `README_RU.md`
- Modify: `ARCHITECTURE.md`
- Modify: `VERSION`
- Create: `RELEASE_NOTES_KABAN_PROJECT_SCHEDULER_v1_12.md`
- Verify only: existing CAELUS root CLI and Review Console files

**Interfaces:**
- Consumes every public interface from Tasks 1–6.
- Produces release v1.12 with scheduler disabled by default and documented opt-in examples.

- [ ] **Step 1: Add acceptance test with a temporary enabled CAELUS configuration**

Use a temporary projects directory containing a copied/trimmed test Project config whose adapter points to a synthetic adapter module. Exercise:

```text
generate-like job due -> success
second tick same slot -> no duplicate
publish-like job due -> blocked retryable
later retry before approval -> blocked same slot
switch synthetic adapter state to approved
next retry -> success
subsequent tick -> no duplicate
new SchedulerEngine instance reading same runtime dir -> still no duplicate
```

Assert persisted slot IDs remain identical across blocked retries.

- [ ] **Step 2: Add second-Project failure-isolation acceptance test**

Register two synthetic Projects with due jobs:

```text
project_a -> adapter raises RuntimeError
project_b -> adapter returns success
```

Assert both states persist independently and project_b succeeds.

- [ ] **Step 3: Add KABAN/CAELUS dependency-boundary regression**

Scan `kaban/**/*.py` and assert:

```python
assert "projects.caelus" not in source
```

Scan `projects/caelus/scheduler.py` and assert it imports Stage 1 automation but does not duplicate generation/publication implementations.

- [ ] **Step 4: Run full source-tree verification before docs/release**

```bash
python -m unittest discover -s tests -q
python -m pytest -q
```

Expected: all existing v1.11 tests plus new scheduler tests pass.

- [ ] **Step 5: Verify v1.11 manual behavior is unchanged**

On clean copies with identical history, run the same mock day through v1.11 and v1.12:

```bash
python run_daily.py --date 2099-10-25 --language ru --mock
```

Compare SHA-256 for:

```text
12 cards/*.png
12 telegram/<sign>.md
telegram_album_1.txt
telegram_album_2.txt (or actual batch files generated by baseline)
```

Use the actual file set from the baseline and assert every corresponding user artifact is byte-identical. Ignore only runtime timestamps in status/content metadata when those timestamps are expected to differ.

- [ ] **Step 6: Verify scheduler-disabled default causes zero side effects**

From the shipped CAELUS `project.yaml`:

```bash
python scheduler.py list
python scheduler.py tick --now 2099-10-25T00:00:00+00:00
```

Assert CAELUS is shown disabled and tick creates no generated content, no Telegram publication, and no adapter runtime state for CAELUS jobs because `jobs: []`.

- [ ] **Step 7: Verify opt-in sample configuration without real external APIs**

Document and test a temporary local configuration such as:

```yaml
automation:
  enabled: true
  adapter: "projects.caelus.scheduler:run_job"
  jobs:
    - id: generate_ru
      handler: generate
      enabled: true
      cron: "0 6 * * *"
      params:
        language: ru
        mode: mock
        target_date_offset_days: 0
      misfire_grace_minutes: 30
      retry:
        interval_minutes: 10
        window_minutes: 120
        max_attempts: 12
```

Run a deterministic tick with a temporary runtime dir and assert one mock dataset is generated exactly once. Do not publish externally in automated release verification.

- [ ] **Step 8: Update operator documentation**

`README_RU.md` must document:

```text
scheduler is disabled by default
how to enable per Project
cron is interpreted in Project timezone
list/status/tick/run/run-now commands
foreground `run` does not install a service
CAELUS publish waits for approval via blocked/retry semantics
KABAN_RUNTIME_DIR override
example RU and EN schedules clearly labeled as examples, not defaults
```

`ARCHITECTURE.md` must show:

```text
kaban/scheduler -> dynamic adapter -> projects/<project>/scheduler.py -> project automation
```

and state explicitly that Core does not know handler semantics.

- [ ] **Step 9: Bump release metadata**

Set `VERSION` to:

```text
1.12
```

Create `RELEASE_NOTES_KABAN_PROJECT_SCHEDULER_v1_12.md` summarizing universal scheduler, disabled-by-default CAELUS config, compatibility, dependencies, and acceptance evidence.

- [ ] **Step 10: Final source verification gate**

Run fresh:

```bash
python -m unittest discover -s tests -q
python -m pytest -q
python scheduler.py list
```

Read the complete outputs and record exact pass counts.

- [ ] **Step 11: Package clean production ZIP and verify the ZIP itself**

Build a staging copy excluding:

```text
.env
__pycache__/
.pytest_cache/
*.pyc
generated test dates
runtime/scheduler test state
.superpowers/sdd scratch workspace
```

Keep:

```text
.env.example
docs/superpowers/specs/2026-09-25-kaban-project-scheduler-stage2-design.md
docs/superpowers/plans/2026-09-25-kaban-project-scheduler-stage2.md
```

Name release:

```text
KABAN_Content_Engine_Project_Scheduler_Stage2_v1_12.zip
```

Unpack it to a fresh directory and run again:

```bash
python -m unittest discover -s tests -q
python -m pytest -q
python scheduler.py list
python scheduler.py tick --now 2099-10-25T00:00:00+00:00
```

Verify ZIP integrity and hygiene by listing the archive rather than inspecting a post-test directory that now contains test caches.

- [ ] **Step 12: Produce verification report**

Create:

```text
KABAN_Project_Scheduler_Stage2_v1_12_verification.txt
```

Include:

```text
exact unittest/pytest counts
scheduler-disabled-default proof
multi-project failure isolation proof
retry/blocked acceptance proof
manual v1.11→v1.12 artifact comparison count
Review Console/manual CLI regression status
ZIP hygiene/integrity
release SHA-256
known non-goals (no service installer, no auto-approval, no real production times)
```

- [ ] **Checkpoint**

Record Task 7 complete in the execution ledger and preserve the final release/report paths.

---

## Plan Self-Review Results

### Spec coverage

All Stage 2 design sections map to tasks:

- Project config / cron / timezone validation -> Task 1.
- Runtime state / history / secrets / locks -> Task 2.
- Scheduling semantics / slots / misfires / retries / isolation -> Task 3.
- Dynamic Project adapter boundary -> Task 4.
- CAELUS Stage 1 adapter -> Task 5.
- Universal CLI / foreground runner -> Task 6.
- Acceptance / backward compatibility / disabled default / release -> Task 7.

### Placeholder scan

The implementation plan contains no executable `TODO`/`TBD` placeholders. Code examples use concrete inputs and assertions. The only remaining Python ellipsis token is valid variadic tuple type syntax such as `tuple[T, ...]`.

### Type consistency

- `RetryPolicy` is defined once in `kaban.scheduler.models` and reused by `kaban.projects.ProjectJobConfig` and `ScheduledJob`.
- `ScheduledJob.adapter` is carried from Project config into the engine and consumed by `load_adapter`.
- `JobContext.attempt` starts at 1 and increments for retries; `max_attempts` counts retries after that first attempt.
- `JobResult` is the only normal adapter return type; unhandled exceptions become scheduler `failed` records.
- `SchedulerState` and `DispatchRecord` fields are consistent across store, engine, CLI, and tests.

### Review Focus coverage

The five highest-risk uncovered areas from the spec are now explicitly exercised:

- timezone/DST -> Task 3;
- corrupt scheduler state -> Tasks 2–3;
- crash/retry idempotence boundary -> Tasks 2–3 + CAELUS Task 5;
- bad adapter isolation -> Tasks 3–4 + acceptance Task 7;
- retry off-by-one/window boundary, including a newer cron occurrence arriving while an older slot is still retrying -> Task 3.

### Plan ruling: manual run identity

The design spec says `run-now` bypasses cron but does not bypass Project safety. This plan gives each operator invocation a distinct `manual:<UTC timestamp>` slot and does **not** add a force flag. This avoids conflating an explicit new operator action with replaying a completed scheduled slot while preserving all Project-level idempotence/precondition guards.
