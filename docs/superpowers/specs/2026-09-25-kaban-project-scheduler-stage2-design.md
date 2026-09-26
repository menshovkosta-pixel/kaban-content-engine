# KABAN Project Scheduler — Stage 2 Design

**Date:** 2026-09-25  
**Status:** Draft for user review  
**Baseline:** KABAN Content Engine / CAELUS Production Automation Stage 1 v1.11

## 1. Purpose

Stage 2 adds a **universal KABAN scheduler** that can schedule jobs independently for every registered Project.

Architectural invariant:

```text
Universal capability -> kaban/
Project-specific behavior -> projects/<project_id>/
```

KABAN decides **when** and **which project job** is due. A Project adapter decides **what that job actually does**.

CAELUS is the first real Project connected to the scheduler. Future projects such as Tender Radar or AI Radar must be able to define completely different jobs without adding their business logic to KABAN Core.

## 2. Success Criteria

Stage 2 is successful when:

1. KABAN can discover scheduler configuration for every registered Project.
2. Each Project can independently enable/disable automation and individual jobs.
3. Jobs use cron schedules evaluated in the Project timezone.
4. A failure in one Project/job does not stop other jobs.
5. The same scheduled occurrence is not executed twice.
6. A Project can return `blocked` for a temporarily unmet prerequisite and be retried within a configured window.
7. Scheduler restarts do not cause duplicate publication/generation when the Project adapter is idempotent.
8. CAELUS scheduled generation/publication use the existing v1.11 automation functions rather than duplicating them.
9. Existing manual CAELUS commands remain unchanged.
10. KABAN Core contains no import of `projects.caelus`.

## 3. Non-goals

Stage 2 does **not** add:

- auto-approval of CAELUS content;
- a generic KABAN web dashboard;
- Instagram/TikTok publishing;
- a cloud deployment/service manager;
- OS service installation;
- distributed multi-host locking;
- changes to CAELUS generation, uniqueness, rendering, review, or Telegram business rules;
- hard-coded production times for CAELUS.

The scheduler will provide a foreground long-running process and a one-shot `tick` mode. Deployment as a Windows service/container/cloud worker is a later stage.

## 4. Alternatives Considered

### A. CAELUS-only scheduler

Fastest implementation, but every future Project would need its own scheduler and fixes would diverge.

**Rejected.** Scheduling is a universal engine capability.

### B. KABAN scheduler with project adapters — selected

KABAN owns schedule parsing, due-job detection, locking, retries, persistence, and dispatch. Projects own job semantics.

**Selected.** It preserves project isolation while centralizing genuinely reusable infrastructure.

### C. External scheduler with one script per Project

Windows Task Scheduler / cron could invoke each Project directly. This avoids scheduler code but duplicates operational configuration, gives no common run ledger, and makes multi-project observability harder.

**Not selected as the product architecture.** One-shot `tick` remains available so external schedulers can still be used operationally if desired.

## 5. High-level Architecture

```text
project.yaml (per Project)
        |
        v
KABAN ProjectRegistry
        |
        v
kaban.scheduler config + engine
        |
        +--> due job? ---- no ---> next job
        |
        v yes
project adapter entrypoint
        |
        v
projects/<project_id>/scheduler.py
        |
        v
project-specific automation/workflow
```

For CAELUS:

```text
kaban.scheduler
      |
      v
projects.caelus.scheduler
      |
      +--> generate -> projects.caelus.automation.run_generation_job()
      |
      +--> publish  -> projects.caelus.automation.run_publication_job()
```

KABAN does not know what `generate`, `publish`, `collect`, `analyse`, or any future handler means.

## 6. Project Scheduler Configuration

`project.yaml` gains an **optional** `automation` section. Projects without this section remain valid and simply have scheduling disabled.

Schema:

```yaml
automation:
  enabled: false
  adapter: "projects.caelus.scheduler:run_job"
  jobs: []
```

A configured job has this generic structure:

```yaml
- id: generate_ru
  handler: generate
  enabled: true
  cron: "0 6 * * *"
  params:
    language: ru
    mode: ai
    target_date_offset_days: 0
  misfire_grace_minutes: 30
  retry:
    interval_minutes: 10
    window_minutes: 120
    max_attempts: 12
```

### 6.1 Generic fields

KABAN understands only:

- `id`: unique inside the Project;
- `handler`: opaque Project-defined handler name;
- `enabled`;
- `cron`: standard five-field cron expression;
- `params`: opaque YAML mapping passed to the adapter;
- `misfire_grace_minutes`;
- `retry.interval_minutes`;
- `retry.window_minutes`;
- `retry.max_attempts`.

KABAN does **not** interpret fields inside `params`.

### 6.2 Safe default

The v1.12 release will not invent real CAELUS production times. The shipped CAELUS configuration remains:

```yaml
automation:
  enabled: false
  adapter: "projects.caelus.scheduler:run_job"
  jobs: []
```

README will include concrete examples. The operator explicitly enables and sets schedules after choosing the desired times.

## 7. KABAN Scheduler Core

New package:

```text
kaban/
└── scheduler/
    ├── __init__.py
    ├── config.py
    ├── models.py
    ├── adapter.py
    ├── store.py
    ├── engine.py
    └── runner.py
```

### 7.1 Models

Core types:

```python
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

@dataclass(frozen=True)
class JobContext:
    project_id: str
    job_id: str
    scheduled_for_utc: datetime
    scheduled_for_local: datetime
    now_utc: datetime
    trigger: str  # "schedule" | "manual" | "retry"
    attempt: int

@dataclass(frozen=True)
class JobResult:
    outcome: str  # "success" | "skipped" | "blocked"
    message: str = ""
    retryable: bool = False
```

Unhandled adapter exceptions are recorded by KABAN as `failed`.

### 7.2 Adapter contract

Project config provides an entrypoint string:

```text
module.path:function
```

The callable contract is:

```python
def run_job(job: ScheduledJob, context: JobContext) -> JobResult:
    ...
```

The dynamic loader validates that the imported object is callable. KABAN never imports a concrete Project module directly in source code.

## 8. Scheduling Semantics

### 8.1 Cron and timezone

- Cron has five fields: minute, hour, day-of-month, month, day-of-week.
- Cron is evaluated in `ProjectConfig.timezone` using `zoneinfo.ZoneInfo`.
- `croniter` is used for cron parsing/evaluation rather than implementing a custom parser.
- Every scheduled occurrence is converted to UTC before persistence.

### 8.2 Slot identity

Each cron occurrence has a stable slot ID:

```text
<scheduled_for_utc ISO-8601>
```

Scheduler state is keyed by:

```text
(project_id, job_id, slot_id)
```

The same completed slot is never dispatched twice unless explicitly executed with a manual force option.

### 8.3 Misfires

When the scheduler starts after a scheduled time:

- if delay <= `misfire_grace_minutes`, the missed slot is eligible to run;
- if delay > grace, the slot is recorded as `missed` and is not backfilled later.

This prevents a long outage from launching a backlog of stale daily content jobs.

### 8.4 Blocked prerequisites and retries

A Project may return:

```python
JobResult(outcome="blocked", retryable=True, message="awaiting approval")
```

KABAN keeps the **same slot** open and retries according to its retry policy.

Retries stop when any limit is reached:

- `max_attempts`;
- `window_minutes` from the original scheduled occurrence;
- Project returns a terminal `skipped` or `success`;
- Project returns `blocked/failed` with `retryable=False`.

This enables a publication job to wait for human approval without creating a second scheduled publication.

## 9. Scheduler Runtime State

Scheduler metadata is operational data, not generated content.

Default location:

```text
runtime/
└── scheduler/
    └── <project_id>/
        └── <job_id>/
            ├── state.json
            └── job.lock
```

Optional environment override:

```text
KABAN_RUNTIME_DIR
```

`state.json` stores only scheduling/dispatch metadata:

```json
{
  "project_id": "caelus",
  "job_id": "generate_ru",
  "slot_id": "2026-09-25T18:00:00+00:00",
  "scheduled_for_utc": "2026-09-25T18:00:00+00:00",
  "last_result": "success",
  "attempt": 1,
  "started_at": "...",
  "finished_at": "...",
  "next_retry_at": null,
  "last_error": null,
  "history": []
}
```

KABAN scheduler state does not duplicate CAELUS `content.json`, approval state, publication state, or CAELUS `run.json`.

History is bounded to the latest 100 scheduler events per job.

## 10. Locking and Crash Recovery

Each Project/job has a KABAN scheduler lock.

Lock payload contains:

- owner PID;
- acquired timestamp;
- lease expiry;
- project/job/slot identity.

Acquisition uses atomic exclusive file creation.

If the lock lease is still active, a competing dispatcher returns `busy` and does not invoke the adapter.

If the lease is expired, a later dispatcher may safely remove it and compete to acquire a new lock. Exclusive create determines the winner.

If a process crashes after the Project side effect but before KABAN writes `success`, the slot may be retried after lease expiry. Therefore the Project adapter contract requires job handlers to be **idempotent**. CAELUS already satisfies this through Stage 1 automation guards.

## 11. Error and Security Rules

- KABAN records exception type plus a sanitized message only.
- Generic sanitization covers API-key-like values, bearer tokens, Telegram bot URL tokens, and common `*_TOKEN` / `*_KEY` assignments.
- Job `params` are **not** copied into runtime state or logs.
- Secrets remain in environment/config mechanisms used by each Project adapter.
- Adapter import errors disable only the affected Project/job dispatch; they do not stop evaluation of other projects.

## 12. Failure Isolation

One scheduler `tick` iterates all due jobs independently.

Example:

```text
CAELUS/generate_ru     -> failed
CAELUS/generate_en     -> success
TenderRadar/collect    -> success
AIRadar/collect        -> success
```

The first failure is recorded and evaluation continues.

The scheduler process itself exits non-zero only for a scheduler-wide fatal configuration/startup failure, not because one dispatched Project job failed.

## 13. CAELUS Adapter

New file:

```text
projects/caelus/scheduler.py
```

Supported handlers in Stage 2:

### `generate`

Expected params:

```yaml
language: ru | en
mode: ai | mock          # default ai
target_date_offset_days: integer  # default 0
model: optional string
```

The adapter computes the target date from the **scheduled local date**, applies the offset, then calls:

```python
run_generation_job(...)
```

Existing content without force remains a safe `skipped` result.

### `publish`

Expected params:

```yaml
language: ru | en
target_date_offset_days: integer  # default 0
```

The adapter calls:

```python
run_publication_job(...)
```

Mapping:

- published/success -> `success`;
- already published -> `skipped`;
- `AutomationPreconditionError` caused by review/approval state -> `blocked`, retryable;
- `AutomationBusyError` -> `blocked`, retryable;
- other exception -> exception propagates and KABAN records `failed`.

The adapter does not auto-approve content.

## 14. Universal Scheduler CLI

New root command:

```text
scheduler.py
```

Commands:

```text
python scheduler.py list
python scheduler.py status [--project PROJECT_ID]
python scheduler.py tick [--project PROJECT_ID] [--now ISO8601]
python scheduler.py run [--project PROJECT_ID] [--poll-seconds 30]
python scheduler.py run-now --project PROJECT_ID --job JOB_ID
```

### `list`

Shows configured projects/jobs and whether each is enabled.

### `status`

Shows last scheduler dispatch state without invoking a Project.

### `tick`

Evaluates current due slots once and exits. `--now` exists for deterministic tests/operator diagnostics.

### `run`

Foreground loop:

```text
tick -> sleep -> tick -> ...
```

It does not daemonize itself.

### `run-now`

Manually invokes one configured job through the same adapter/locking/ledger path, with `trigger="manual"`. It bypasses cron due-time evaluation but does not bypass Project safety/preconditions.

## 15. Project Registry Changes

`ProjectConfig` gains optional scheduler configuration types:

```python
@dataclass(frozen=True)
class ProjectAutomationConfig:
    enabled: bool
    adapter: str | None
    jobs: tuple[ProjectJobConfig, ...]
```

Requirements:

- missing `automation` -> disabled config, backward compatible;
- disabled automation may have no adapter/jobs;
- enabled automation requires a valid adapter and at least one job;
- job IDs unique per Project;
- cron expressions validated during config load;
- retry values non-negative and internally consistent;
- `params` must be a mapping;
- existing v1.11 Project fields and behavior remain unchanged.

## 16. Dependencies

Add:

```text
croniter>=6.0
```

Use Python stdlib `zoneinfo` for timezone support.

No scheduler framework such as APScheduler is introduced in Stage 2. KABAN owns a small deterministic dispatch loop, while cron expression semantics are delegated to `croniter`.

## 17. Backward Compatibility

The following v1.11 behavior must remain unchanged:

```text
python run_daily.py ...
python automation.py ...
python publish_telegram.py ...
python admin_app.py
```

Scheduler disabled means zero behavior change for current manual CAELUS operation.

Existing generated data and `generated/_automation/caelus/...` require no migration.

## 18. Testing Strategy

### Core config tests

- Project without `automation` remains valid and disabled.
- Valid scheduler config parses.
- Duplicate job IDs rejected.
- Invalid cron rejected.
- Invalid retry configuration rejected.

### Scheduler engine tests

- due slot dispatched once;
- repeated tick does not duplicate a completed slot;
- disabled project/job ignored;
- timezone evaluation uses Project timezone;
- within-grace misfire runs;
- expired misfire becomes `missed`;
- blocked result retries same slot;
- retry window/max attempts stop retries;
- failure in one job does not prevent next job;
- exception is sanitized;
- active lock blocks duplicate dispatch;
- expired lock can be recovered;
- state corruption fails safe and does not silently dispatch a duplicate.

### Adapter tests

- dynamic adapter loading;
- CAELUS generate maps to Stage 1 automation;
- CAELUS publish maps precondition to blocked/retryable;
- target date uses scheduled Project-local date and offset;
- CAELUS adapter is idempotent through existing Stage 1 behavior.

### CLI tests

- list/status/tick/run-now argument behavior;
- project filter;
- invalid Project/job fails clearly;
- `tick --now` deterministic.

### Regression tests

- full v1.11 suite remains green;
- normal manual mock generation produces the same 12 cards and Telegram artifacts;
- Review Console behavior unchanged;
- KABAN scheduler source contains no `projects.caelus` import.

## 19. Acceptance Scenario

With a temporary test configuration containing two independent jobs:

```text
CAELUS generate_ru -> due -> success
CAELUS publish_ru  -> due -> blocked (not approved)
```

Then:

1. `tick` runs generation exactly once.
2. A second `tick` does not regenerate the same slot.
3. Publish remains blocked and gets a retry time.
4. After approval, a later retry publishes the same slot.
5. A subsequent tick does not publish it again.
6. Restarting the scheduler preserves all of the above from disk state.

A second synthetic Project adapter in tests proves that a CAELUS failure does not stop another Project's due job.

## 20. Stage 2 Release Boundary

Proposed release: **v1.12 — KABAN Project Scheduler Stage 2**.

The release ships the universal scheduler **disabled by default for CAELUS** until the operator selects production times. After v1.12 acceptance, the next step is to choose CAELUS RU/EN generation/publication schedules and enable them, then deploy the foreground runner on a 24/7 host/service.
