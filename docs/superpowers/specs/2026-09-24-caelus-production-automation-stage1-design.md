# CAELUS Production Automation — Stage 1 Design

## Status

Proposed design for the first production-automation stage after CAELUS v1.10.

Stage 1 introduces a reliable automation/run-state layer and idempotent job entrypoints. It does **not** introduce a timer/cron scheduler and it does **not** auto-publish approved content.

## 1. Goal

Create a safe foundation for unattended CAELUS operation without changing the existing business workflow.

The system must be able to answer, for every `date + language` dataset:

- what business state it is currently in;
- what automation operation ran most recently;
- whether that operation succeeded or failed;
- whether a retry is safe;
- whether generation or publication should be skipped to avoid destructive overwrite or duplicate publication.

This stage must preserve all current manual workflows and CLI entrypoints.

## 2. Existing sources of truth

CAELUS already persists business state in the generated dataset directory:

```text
generated/YYYY-MM-DD/<language>/
├── content.json
├── status.json
├── publication.json
├── publication_history.json
├── cards/
└── telegram/
```

These files remain authoritative.

### `content.json`

Presence of this file means a dataset has already been generated.

### `status.json`

Current review state:

```text
draft
approved
```

Approval also stores the approved `content_hash`.

### `publication.json`

Current publication state when publication has started:

```text
publishing
partially_published
failed
published
```

The existing publisher already persists progress after confirmed Telegram steps and supports safe resume.

## 3. Design principle: derived business state

Stage 1 must **not** create another business-state source of truth.

A new automation layer derives the effective state from the existing dataset files.

```text
content/status/publication
          │
          ▼
     derive_state()
          │
          ▼
 AutomationState
```

The automation journal records execution metadata only.

This avoids disagreement such as:

```text
run.json says "published"
publication.json says "failed"
```

`publication.json` wins because business state is derived from the authoritative files.

## 4. Effective state model

The public automation state is one of:

```text
pending_generation
generation_failed
review_required
approval_invalid
ready_to_publish
publishing
publish_failed
published
```

### Derivation precedence

State is derived in this order:

1. If `publication.json.state == "published"` → `published`.
2. If `publication.json.state in {"publishing", "partially_published"}` → `publishing`.
3. If `publication.json.state == "failed"` → `publish_failed`.
4. If no `content.json` exists and the last generation run failed → `generation_failed`.
5. If no `content.json` exists → `pending_generation`.
6. If `status.json.state == "approved"` but the stored approved `content_hash` does not match the current `content.json` hash → `approval_invalid`.
7. If `status.json.state == "approved"` and the hash matches → `ready_to_publish`.
8. Otherwise, if `content.json` exists → `review_required`.

`generation_failed` is the only effective state that requires automation metadata to distinguish a failed attempt from a dataset that has simply never been generated. `approval_invalid` mirrors the protection already enforced by the existing publisher and prevents automation from treating externally modified approved content as publishable.

## 5. Automation journal

Execution metadata is stored separately from business files:

```text
generated/
└── _automation/
    └── caelus/
        └── YYYY-MM-DD/
            └── <language>/
                └── run.json
```

The path is deliberately outside normal `generated/YYYY-MM-DD/<language>/` datasets so existing history scans, uniqueness scans, and Review Console dataset enumeration are not confused by automation records.

### `run.json` schema

```json
{
  "project_id": "caelus",
  "date": "2026-09-25",
  "language": "ru",
  "last_operation": "generate",
  "last_result": "failed",
  "attempt": 2,
  "started_at": "2026-09-24T20:00:00+00:00",
  "finished_at": "2026-09-24T20:00:08+00:00",
  "last_error": {
    "type": "AIProviderError",
    "message": "OpenAI provider request failed"
  },
  "content_hash": null,
  "history": [
    {
      "operation": "generate",
      "attempt": 1,
      "result": "failed",
      "started_at": "...",
      "finished_at": "...",
      "error_type": "AIProviderError"
    }
  ]
}
```

### Required fields

Top-level:

- `project_id`
- `date`
- `language`
- `last_operation`: `generate` or `publish`
- `last_result`: `running`, `success`, `failed`, or `skipped`
- `attempt`: attempt number for the current operation type
- `started_at`
- `finished_at` when terminal
- `last_error` when failed
- `content_hash` when known
- `history`

### Error privacy

`last_error.message` must be safe for Review Console display.

It must not persist:

- OpenAI API keys;
- Telegram bot tokens;
- complete URLs containing credentials;
- raw environment-variable dumps.

The journal may store exception class/type and a sanitized summary.

## 6. Automation module

Create:

```text
projects/caelus/automation.py
```

Public Stage 1 API:

```python
@dataclass(frozen=True)
class AutomationState:
    state: str
    date: str
    language: str
    content_hash: str | None
    last_operation: str | None
    last_result: str | None
    attempt: int
    last_error: dict[str, str] | None
    updated_at: str | None


def derive_state(day: str, language: str) -> AutomationState:
    ...


def run_generation_job(
    day: str,
    language: str,
    *,
    mode: str = "ai",
    model: str | None = None,
    force: bool = False,
) -> AutomationState:
    ...


def run_publication_job(
    day: str,
    language: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> AutomationState:
    ...
```

Stage 1 keeps these functions synchronous. A background worker/queue is explicitly deferred.

## 7. Generation job semantics

### Normal generation

`run_generation_job()` delegates the actual CAELUS generation to the existing `projects.caelus.workflow.generate_bundle()`.

Before invoking generation:

- validate date/language through existing Project rules;
- read current effective state;
- start/update the automation journal atomically.

### Idempotency rule

If `content.json` already exists and `force=False`:

- do **not** call OpenAI;
- do **not** overwrite content/cards/Telegram artifacts;
- journal result is `skipped`;
- effective state remains whatever is derived from the existing dataset.

This protects reviewed or approved content from an accidental scheduled rerun.

### Explicit force

`force=True` is allowed only as an explicit operator action.

It re-runs generation using the existing workflow and therefore returns the dataset to the normal generated `draft/review_required` state.

Stage 1 does not expose `force` to any timer because Stage 1 has no timer.

### Transactional generation boundary

`generate_bundle()` writes several files in sequence, so the automation wrapper must make the bundle operation failure-safe.

Before invoking generation:

- if no dataset exists, remember that the target directory was absent;
- if `force=True` and a dataset exists, create a temporary local backup of that dataset directory before changing it.

On generation exception:

- if the dataset did not exist before the attempt, remove artifacts created by the failed attempt so a partial `content.json` cannot masquerade as a reviewable dataset;
- if a dataset existed before a forced attempt, restore the pre-attempt dataset from the temporary backup;
- write a failed automation attempt;
- store a sanitized error;
- re-raise the original application-level error to the caller;
- if no dataset exists after cleanup, effective state becomes `generation_failed`.

On success, discard the temporary backup. The automation journal itself must be written atomically using existing KABAN storage primitives.

## 8. Publication job semantics

`run_publication_job()` delegates publication to the existing CAELUS publication workflow; it must not duplicate Telegram strategy.

### Precondition

Without `force`, normal publication is eligible only when the derived state is:

```text
ready_to_publish
publish_failed
publishing
```

Interpretation:

- `ready_to_publish`: start publication;
- `publish_failed`: resume the existing publisher journal;
- `publishing`: resume/recover the existing publisher journal after interrupted orchestration.

### Not approved

For `pending_generation`, `generation_failed`, `review_required`, or `approval_invalid`:

- publication must not start;
- result is a safe refusal, not an automatic approval.

### Already published

If effective state is `published` and `force=False`:

- do not call Telegram;
- journal result is `skipped`;
- return `published`.

This is a second duplicate-publication guard on top of the existing publisher's own protection.

### Existing publication journal remains authoritative

The existing `publication.json` and `publication_history.json` remain responsible for:

- media/text step progress;
- partial-send recovery;
- Telegram message IDs/results;
- `publishing/partially_published/failed/published` state.

Automation `run.json` records only the job-level attempt/result.

## 9. Retry model

Stage 1 does not automatically retry in a loop.

A retry is an explicit second invocation of the same job.

This keeps retry behavior observable and avoids hidden repeated OpenAI cost or repeated Telegram sends.

The journal increments attempt count per operation type.

Examples:

```text
generate attempt 1 → failed
operator Retry      → generate attempt 2
```

```text
publish attempt 1 → partially_published
operator Retry    → existing publisher resumes unsent steps
```

Automatic retry policy/backoff belongs to a later scheduler stage.

## 10. Review Console integration

Stage 1 adds automation status to the existing Review Console without replacing current review/publication controls.

### Review page

Display a compact automation panel with:

- effective state;
- last operation;
- last result;
- attempt number;
- last updated time;
- sanitized last error, when present.

### Actions

Reuse the current Review Console controls rather than adding duplicate controls:

- existing empty-day / `Generate tomorrow` actions call `run_generation_job()`;
- generation retry is shown after a failed generation;
- the existing Telegram publish action calls `run_publication_job()`;
- failed/partial publication uses the same button as an explicit retry/resume action.

Existing `Approve All`, `Return to draft`, Regenerate, and publication UX remains valid. The user-visible publication workflow must not gain a second competing Publish button.

For Stage 1, these actions execute synchronously through the current HTTP process. Long-running/background execution is deferred to the scheduler/worker stage.

### History page

Add effective automation state and latest run result using derived state + `run.json`.

Do not replace existing review-state/publication-state columns unless doing so improves readability without losing information.

## 11. Root CLI compatibility

Existing commands remain operational:

```text
python run_daily.py ...
python publish_telegram.py ...
python admin_app.py
```

Stage 1 may add a dedicated automation CLI, for example:

```text
python automation.py generate --date YYYY-MM-DD --language ru
python automation.py publish --date YYYY-MM-DD --language ru
python automation.py status --date YYYY-MM-DD --language ru
```

If added, the root file must be a thin entrypoint to `projects.caelus.automation` and must not contain duplicate business logic.

Existing manual commands are not silently redirected through automation in Stage 1. This prevents a new journaling layer from changing historical manual behavior during its first release.

## 12. Storage and concurrency

### Atomic writes

All `run.json` writes use `kaban.storage.write_json()`.

### Single-host assumption

Stage 1 assumes one CAELUS runtime host/process operator at a time. It does not claim distributed locking.

### Concurrent duplicate invocation

Because atomic JSON writes alone do not provide a cross-process lock, Stage 1 must add a simple per-dataset operation lock before an automation job mutates state.

Preferred lock location:

```text
generated/_automation/caelus/YYYY-MM-DD/<language>/<operation>.lock
```

Lock semantics:

- acquire atomically with exclusive create;
- if a lock exists, reject the second invocation as already running;
- lock record contains PID and acquisition timestamp for diagnosis;
- remove the lock in `finally`;
- Stage 1 does not automatically steal or expire a lock, because unsafe stale-lock takeover could create concurrent OpenAI or Telegram work. A hard-kill stale lock is an explicit operator-recovery case and must produce a clear message naming the lock file.

The implementation must stay platform-compatible with Windows, since CAELUS is currently operated on Windows. Automatic stale-lock recovery belongs to the later unattended scheduler stage.

## 13. Failure safety

The design must preserve these existing guarantees:

1. Generated content is never silently overwritten by a routine rerun.
2. Approved content hash remains authoritative for publication.
3. Editing after publication begins remains blocked by the existing workflow.
4. A repeated normal publication request cannot intentionally resend an already published dataset.
5. Partial Telegram publication resumes from `publication.json` rather than starting from zero.
6. Automation failures remain inspectable after process exit.
7. Secrets are not persisted to run metadata or rendered in Review Console.

## 14. Observability

Stage 1 provides lightweight operational observability through `run.json` and console output.

Each job prints at minimum:

```text
[CAELUS] operation=generate date=2026-09-25 language=ru attempt=1
[CAELUS] result=success state=review_required
```

On failure:

```text
[CAELUS] result=failed state=generation_failed error=AIProviderError
```

No structured external logging backend is introduced in Stage 1.

## 15. Testing strategy

### Unit tests

Cover:

- every effective state derivation;
- missing/corrupt optional journal handling;
- generation skip when content already exists;
- generation force behavior;
- failed generation journal;
- already-published publication skip;
- publication refusal before approval;
- publication resume eligibility;
- attempt increments;
- error sanitization;
- lock rejection and cleanup.

### Integration tests

Using mock generation and fake/monkeypatched publication transport:

```text
pending_generation
    ↓ generate
review_required
    ↓ approve
ready_to_publish
    ↓ publish
published
```

Also test:

```text
publish fails after partial progress
    ↓ retry
publisher resumes
    ↓
published
```

### Regression tests

Stage 1 must preserve:

- current v1.10 mock daily output;
- 12 cards;
- Telegram artifact generation;
- Review Console review/regeneration/approval behavior;
- legacy CLI behavior;
- publication duplicate guards;
- current storage layout for normal datasets.

## 16. Non-goals for Stage 1

Explicitly deferred:

- cron/timer scheduling;
- automatic generation at a clock time;
- automatic publication at a clock time;
- auto-approval;
- background worker process;
- distributed queue;
- SQLite/PostgreSQL job database;
- cloud deployment;
- multi-project scheduler;
- automatic retry/backoff loops;
- email/Telegram operational alerts;
- RU/EN multi-channel scheduling.

These are later stages built on top of the run-state foundation.

## 17. Acceptance criteria

Stage 1 is complete only when all of the following are true:

1. `derive_state()` maps existing CAELUS files to the documented effective states.
2. `run_generation_job()` never overwrites existing content unless `force=True`.
3. `run_publication_job()` never publishes unapproved or hash-invalid approved content.
4. A normal second publication request after `published` sends nothing.
5. Partial/failed publication remains resumable through the existing publisher journal.
6. Failed generation cannot leave a newly-created partial dataset, and failed forced generation restores the pre-attempt dataset.
7. A failed job leaves a sanitized, inspectable `run.json`.
8. A second concurrent job for the same dataset/operation is rejected safely.
9. Review Console shows effective automation/run status without exposing secrets.
10. Existing manual CAELUS workflow continues to work unchanged.
11. No timer or unattended auto-publish behavior is introduced in Stage 1.
