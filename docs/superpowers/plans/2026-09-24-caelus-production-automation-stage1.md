# CAELUS Production Automation — Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a failure-safe, idempotent automation/run-state foundation for CAELUS generation and Telegram publication without introducing scheduling or changing the existing business workflow.

**Architecture:** Business state continues to come from `content.json`, `status.json`, and `publication.json`. A new CAELUS automation layer derives effective state and stores only execution metadata under `generated/_automation/caelus/...`; job wrappers add locking, idempotency, transactional generation rollback, and explicit publication retry/resume while delegating all content and Telegram work to the existing CAELUS workflow.

**Tech Stack:** Python 3.10+, standard library (`dataclasses`, `contextlib`, `tempfile`, `shutil`, `os`, `argparse`, `json`), existing KABAN atomic JSON storage, existing CAELUS workflow/publication modules, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-24-caelus-production-automation-stage1-design.md`

## Global Constraints

- Do not add cron, timer scheduling, automatic clock-time generation, or automatic clock-time publication in Stage 1.
- Do not auto-approve content.
- Do not add SQLite/PostgreSQL, a background worker, a queue, or a distributed lock.
- Keep `content.json`, `status.json`, and `publication.json` authoritative for business state.
- Keep existing normal dataset layout exactly `generated/YYYY-MM-DD/<language>/`.
- Store automation metadata only under `generated/_automation/caelus/YYYY-MM-DD/<language>/`.
- Keep existing manual CLI commands operational and do not silently redirect `run_daily.py` or `publish_telegram.py` through automation.
- A routine automation generation run must never overwrite an existing `content.json`; overwrite requires explicit `force=True`.
- A routine automation publication run must never resend an already published dataset.
- `force=True` must never bypass approval/content-hash validation.
- A failed new generation must not leave a partial dataset; a failed forced generation must restore the pre-attempt dataset.
- Automation error metadata must not persist OpenAI keys, Telegram bot tokens, credential-bearing URLs, or environment dumps.
- Operation locks must work on Windows using exclusive file creation; Stage 1 must not auto-expire or steal stale locks.
- Existing CAELUS Review Console editing, regeneration, approval, uniqueness, and publication semantics remain unchanged.
- Source release snapshot has no `.git`; do not initialize Git or fabricate commit history solely for this plan.

## Review Focus

1. **Stale/existing operation lock:** a second invocation must fail before OpenAI/Telegram work starts, leave the existing lock untouched, and name the lock file in the error. Covered in Task 2.
2. **Corrupt optional `run.json`:** effective state must still derive from authoritative business files and a later job must be able to replace the unusable automation journal. Covered in Tasks 1 and 2.
3. **Forced generation fails after partially overwriting files:** the exact previous dataset must be restored, including cards/Telegram files, while the failure remains visible in `run.json`. Covered in Task 3.
4. **Publication fails after Telegram partial progress:** automation must record a failed job but leave `publication.json` authoritative so the next invocation resumes rather than resends completed steps. Covered in Task 4.
5. **Automation-only failed day:** Review Console history must show `generation_failed` even though no `content.json` exists, while `_automation` must never appear as a fake date/dataset. Covered in Task 5.

---

## File Structure

### Create

- `projects/caelus/automation_store.py` — automation journal paths, safe loading/writing, error sanitization, attempt calculation, target enumeration, and per-operation lock.
- `projects/caelus/automation.py` — public `AutomationState`, state derivation, generation/publication job wrappers, logging.
- `projects/caelus/automation_cli.py` — operator CLI parser for `status`, `generate`, and `publish`.
- `automation.py` — thin root compatibility/operator entrypoint to `projects.caelus.automation_cli.main`.
- `tests/test_automation.py` — unit/integration tests for Stage 1 state, journal, locks, generation, publication, and CLI.
- `RELEASE_NOTES_CAELUS_AUTOMATION_STAGE1_v1_11.md` — user-visible release notes after acceptance tests pass.

### Modify

- `projects/caelus/publication.py` — extend `run_telegram_publisher()` to pass `dry_run`/`force` flags without moving Telegram business logic.
- `admin_app.py` — display automation state, route generation/publication actions through automation jobs, include automation-only failed runs in history.
- `README_RU.md` — document automation status/generate/publish commands and explicitly state that Stage 1 has no scheduler.
- `ARCHITECTURE.md` — document automation layer as CAELUS project functionality over KABAN services.
- `VERSION` — bump from `1.10` to `1.11` only after full regression passes.

### Intentionally unchanged

- `run_daily.py` — remains the direct manual generation command.
- `publish_telegram.py` — remains the direct manual publication command.
- `projects/caelus/workflow.py` — remains owner of generation/review/regeneration business logic.
- `kaban/*` — no KABAN Core changes are required for Stage 1.

---

### Task 1: Automation journal and derived state model

**Files:**
- Create: `projects/caelus/automation_store.py`
- Create: `projects/caelus/automation.py`
- Create: `tests/test_automation.py`

**Interfaces:**
- Consumes: `projects.caelus.storage.GENERATED`, `content_path()`, `status_path()`, `publication_path()`, `day_dir()`, plus `kaban.storage.content_hash`, `load_json`, `write_json`.
- Produces:
  - `AutomationState(state: str, date: str, language: str, content_hash: str | None, last_operation: str | None, last_result: str | None, attempt: int, last_error: dict[str, str] | None, updated_at: str | None)`
  - `derive_state(day: str, language: str) -> AutomationState`
  - `automation_dir(day: str, language: str) -> Path`
  - `run_path(day: str, language: str) -> Path`
  - `load_run(day: str, language: str) -> dict[str, Any]`
  - `write_run(day: str, language: str, payload: dict[str, Any]) -> None`
  - `next_attempt(run: dict[str, Any], operation: str) -> int`
  - `sanitize_exception(exc: BaseException) -> dict[str, str]`
  - `iter_automation_targets() -> list[tuple[str, str]]`

- [ ] **Step 1: Write failing state-derivation and journal tests**

Add these concrete tests to `tests/test_automation.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase, mock

from kaban.storage import content_hash, write_json


class AutomationStateTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.root = Path(self.tmp)
        self.generated = self.root / "generated"
        self.generated.mkdir()
        self.generated_patch = mock.patch("projects.caelus.storage.GENERATED", self.generated)
        self.generated_patch.start()
        self.addCleanup(self.generated_patch.stop)

    def _paths(self, day="2099-01-15", language="ru"):
        base = self.generated / day / language
        base.mkdir(parents=True, exist_ok=True)
        return base

    def test_derive_state_pending_generation_without_dataset(self):
        from projects.caelus import automation
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch("projects.caelus.automation_store.GENERATED", self.generated):
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "pending_generation")
        self.assertIsNone(state.content_hash)

    def test_derive_state_generation_failed_from_last_failed_generate(self):
        from projects.caelus import automation, automation_store
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            automation_store.write_run("2099-01-15", "ru", {
                "project_id": "caelus",
                "date": "2099-01-15",
                "language": "ru",
                "last_operation": "generate",
                "last_result": "failed",
                "attempt": 1,
                "started_at": "2099-01-14T20:00:00+00:00",
                "finished_at": "2099-01-14T20:00:01+00:00",
                "last_error": {"type": "RuntimeError", "message": "provider failed"},
                "content_hash": None,
                "history": [],
            })
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "generation_failed")
        self.assertEqual(state.attempt, 1)
        self.assertEqual(state.last_error["type"], "RuntimeError")

    def test_derive_state_review_ready_invalid_and_published_precedence(self):
        from projects.caelus import automation, automation_store
        base = self._paths()
        payload = {"date": "15 января 2099", "iso_date": "2099-01-15", "language": "ru", "signs": {}}
        write_json(base / "content.json", payload)
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "review_required")
            write_json(base / "status.json", {"state": "approved", "content_hash": "wrong"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "approval_invalid")
            write_json(base / "status.json", {"state": "approved", "content_hash": content_hash(payload)})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "ready_to_publish")
            write_json(base / "publication.json", {"state": "partially_published"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publishing")
            write_json(base / "publication.json", {"state": "failed"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publish_failed")
            write_json(base / "publication.json", {"state": "published"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "published")

    def test_corrupt_optional_run_json_does_not_break_business_state(self):
        from projects.caelus import automation, automation_store
        base = self._paths()
        write_json(base / "content.json", {"iso_date": "2099-01-15", "language": "ru", "signs": {}})
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            path = automation_store.run_path("2099-01-15", "ru")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "review_required")
        self.assertIsNone(state.last_operation)
```

Also add a sanitization test:

```python
class AutomationStoreTests(TestCase):
    def test_sanitize_exception_removes_api_keys_tokens_and_credential_urls(self):
        from projects.caelus.automation_store import sanitize_exception
        exc = RuntimeError(
            "OPENAI_API_KEY=sk-secret123 TELEGRAM_BOT_TOKEN=123456:ABCDEF "
            "https://api.telegram.org/bot123456:ABCDEF/sendMessage Bearer sk-othersecret"
        )
        safe = sanitize_exception(exc)
        self.assertEqual(safe["type"], "RuntimeError")
        self.assertNotIn("sk-secret123", safe["message"])
        self.assertNotIn("123456:ABCDEF", safe["message"])
        self.assertNotIn("sk-othersecret", safe["message"])
        self.assertIn("[REDACTED]", safe["message"])
```

- [ ] **Step 2: Run the new test file and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: import failures because `projects.caelus.automation` and `projects.caelus.automation_store` do not exist yet.

- [ ] **Step 3: Implement journal primitives and state derivation**

Create `projects/caelus/automation_store.py` with these exact responsibilities:

```python
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kaban.storage import load_json, write_json
from projects.caelus.storage import GENERATED, day_dir

PROJECT_ID = "caelus"


def automation_dir(day: str, language: str) -> Path:
    day_dir(day, language)  # validation only
    return GENERATED / "_automation" / PROJECT_ID / day / language


def run_path(day: str, language: str) -> Path:
    return automation_dir(day, language) / "run.json"


def load_run(day: str, language: str) -> dict[str, Any]:
    try:
        payload = load_json(run_path(day, language), {}) or {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_run(day: str, language: str, payload: dict[str, Any]) -> None:
    write_json(run_path(day, language), payload)


def next_attempt(run: dict[str, Any], operation: str) -> int:
    attempts = [
        int(item.get("attempt") or 0)
        for item in (run.get("history") or [])
        if isinstance(item, dict) and item.get("operation") == operation
    ]
    if run.get("last_operation") == operation:
        attempts.append(int(run.get("attempt") or 0))
    return max(attempts, default=0) + 1
```

Implement `sanitize_exception()` with explicit patterns for `OPENAI_API_KEY=...`, `TELEGRAM_BOT_TOKEN=...`, `sk-...`, `Bearer ...`, and `/bot<TOKEN>/` URL path segments, returning only:

```python
{"type": type(exc).__name__, "message": sanitized_message[:2000]}
```

Implement `iter_automation_targets()` by scanning only:

```text
generated/_automation/caelus/<YYYY-MM-DD>/<ru|en>/run.json
```

and returning validated `(day, language)` pairs; ignore malformed directories.

Create `projects/caelus/automation.py` with the exact dataclass from the spec and `derive_state()` precedence exactly as documented. Read the optional run journal through `load_run()`, not directly through `load_json()`.

- [ ] **Step 4: Run the automation tests and verify GREEN**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: all Task 1 tests PASS.

- [ ] **Step 5: Run the existing full suite for regression**

Run:

```text
python -m unittest discover -s tests -v
```

Expected: all pre-existing tests plus Task 1 tests PASS.

---

### Task 2: Per-operation lock and journal lifecycle helpers

**Files:**
- Modify: `projects/caelus/automation_store.py`
- Modify: `projects/caelus/automation.py`
- Test: `tests/test_automation.py`

**Interfaces:**
- Consumes: Task 1 automation paths and journal primitives.
- Produces:
  - `AutomationBusyError(RuntimeError)`
  - `operation_lock(day: str, language: str, operation: str)` context manager
  - private journal lifecycle helpers in `projects.caelus.automation.py`: `_start_operation(...)`, `_finish_operation(...)`

- [ ] **Step 1: Add failing lock and attempt-lifecycle tests**

Add:

```python
class AutomationLockTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()

    def test_existing_lock_rejects_second_invocation_and_names_lock_file(self):
        from projects.caelus import automation_store
        with mock.patch.object(automation_store, "GENERATED", self.generated):
            lock = automation_store.automation_dir("2099-01-15", "ru") / "generate.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"pid":999,"acquired_at":"2099-01-14T20:00:00+00:00"}', encoding="utf-8")
            with self.assertRaises(automation_store.AutomationBusyError) as ctx:
                with automation_store.operation_lock("2099-01-15", "ru", "generate"):
                    pass
        self.assertIn(str(lock), str(ctx.exception))
        self.assertTrue(lock.exists())

    def test_operation_lock_is_removed_after_exception(self):
        from projects.caelus import automation_store
        with mock.patch.object(automation_store, "GENERATED", self.generated):
            lock = automation_store.automation_dir("2099-01-15", "ru") / "publish.lock"
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with automation_store.operation_lock("2099-01-15", "ru", "publish"):
                    self.assertTrue(lock.exists())
                    raise RuntimeError("boom")
            self.assertFalse(lock.exists())

    def test_next_attempt_counts_attempts_per_operation(self):
        from projects.caelus.automation_store import next_attempt
        run = {
            "last_operation": "publish",
            "attempt": 2,
            "history": [
                {"operation": "generate", "attempt": 1},
                {"operation": "publish", "attempt": 1},
            ],
        }
        self.assertEqual(next_attempt(run, "generate"), 2)
        self.assertEqual(next_attempt(run, "publish"), 3)
```

- [ ] **Step 2: Run and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: failures for missing `AutomationBusyError` and `operation_lock()`.

- [ ] **Step 3: Implement Windows-compatible exclusive lock**

In `projects/caelus/automation_store.py`, implement exclusive creation with `os.open(..., os.O_CREAT | os.O_EXCL | os.O_WRONLY)`. Write only PID and UTC acquisition time. On `FileExistsError`, raise `AutomationBusyError` with the exact lock path and explicit operator guidance that Stage 1 does not auto-remove stale locks. Delete only the lock acquired by the current context in `finally`.

The operation name must be restricted to `{"generate", "publish"}` so arbitrary filenames cannot be created.

- [ ] **Step 4: Implement journal start/finish helpers**

In `projects/caelus/automation.py`, add private helpers with this behavior:

```python
def _start_operation(day: str, language: str, operation: str) -> tuple[dict[str, Any], int, str]:
    run = load_run(day, language)
    attempt = next_attempt(run, operation)
    started_at = utc_now()
    updated = {
        **run,
        "project_id": "caelus",
        "date": day,
        "language": language,
        "last_operation": operation,
        "last_result": "running",
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": None,
        "last_error": None,
        "history": list(run.get("history") or []),
    }
    write_run(day, language, updated)
    return updated, attempt, started_at
```

`_finish_operation()` must update the top-level terminal fields and append exactly one terminal history entry containing `operation`, `attempt`, `result`, `started_at`, `finished_at`, `error_type` when failed, and `content_hash` when known. Cap `history` at the latest 100 entries to keep `run.json` bounded.

- [ ] **Step 5: Run focused and full suites**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
python -m unittest discover -s tests -v
```

Expected: both commands PASS.

---

### Task 3: Transactional and idempotent generation job

**Files:**
- Modify: `projects/caelus/automation.py`
- Test: `tests/test_automation.py`

**Interfaces:**
- Consumes: `operation_lock()`, journal helpers, `projects.caelus.workflow.generate_bundle()`, normal CAELUS dataset path.
- Produces:
  - `run_generation_job(day: str, language: str, *, mode: str = "ai", model: str | None = None, force: bool = False) -> AutomationState`

- [ ] **Step 1: Add failing generation-job tests**

Add tests covering skip, success, cleanup, and forced rollback:

```python
class AutomationGenerationJobTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        self.store_patch = mock.patch("projects.caelus.automation_store.GENERATED", self.generated)
        self.auto_patch = mock.patch("projects.caelus.automation.GENERATED", self.generated)
        self.storage_patch = mock.patch("projects.caelus.storage.GENERATED", self.generated)
        for patcher in (self.store_patch, self.auto_patch, self.storage_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_existing_content_skips_generation_without_force(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        original = {"iso_date": "2099-01-15", "language": "ru", "signs": {"keep": "me"}}
        write_json(base / "content.json", original)
        with mock.patch("projects.caelus.automation.generate_bundle") as generate:
            state = automation.run_generation_job("2099-01-15", "ru", mode="mock")
        generate.assert_not_called()
        self.assertEqual(state.last_result, "skipped")
        self.assertEqual(json.loads((base / "content.json").read_text(encoding="utf-8")), original)

    def test_successful_generation_records_success_and_review_required(self):
        from projects.caelus import automation
        payload = {"iso_date": "2099-01-15", "language": "ru", "signs": {}}
        def fake_generate(day, language, mode, model=None):
            base = self.generated / day / language
            base.mkdir(parents=True, exist_ok=True)
            write_json(base / "content.json", payload)
            write_json(base / "status.json", {"state": "draft"})
            return payload
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=fake_generate):
            state = automation.run_generation_job("2099-01-15", "ru", mode="mock")
        self.assertEqual(state.state, "review_required")
        self.assertEqual(state.last_result, "success")
        self.assertEqual(state.attempt, 1)

    def test_failed_new_generation_removes_partial_dataset_and_records_failure(self):
        from projects.caelus import automation
        def broken(day, language, mode, model=None):
            base = self.generated / day / language
            (base / "cards").mkdir(parents=True, exist_ok=True)
            write_json(base / "content.json", {"partial": True})
            (base / "cards" / "partial.png").write_bytes(b"partial")
            raise RuntimeError("generation exploded")
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "generation exploded"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock")
        self.assertFalse((self.generated / "2099-01-15" / "ru").exists())
        state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "generation_failed")
        self.assertEqual(state.last_result, "failed")

    def test_failed_forced_generation_restores_previous_dataset_exactly(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        (base / "cards").mkdir(parents=True)
        (base / "telegram").mkdir()
        write_json(base / "content.json", {"stable": True})
        write_json(base / "status.json", {"state": "approved", "content_hash": "oldhash"})
        (base / "cards" / "caelus_aries.png").write_bytes(b"OLDPNG")
        (base / "telegram" / "01.md").write_bytes(b"OLDTEXT")
        before = {
            p.relative_to(base).as_posix(): p.read_bytes()
            for p in base.rglob("*") if p.is_file()
        }
        def broken(day, language, mode, model=None):
            write_json(base / "content.json", {"new": "partial"})
            (base / "cards" / "caelus_aries.png").write_bytes(b"BROKEN")
            raise RuntimeError("forced generation failed")
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "forced generation failed"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock", force=True)
        after = {
            p.relative_to(base).as_posix(): p.read_bytes()
            for p in base.rglob("*") if p.is_file()
        }
        self.assertEqual(after, before)
```

Also add a test that a pre-existing directory without `content.json` is restored on failure rather than silently destroyed:

```python
def test_failed_generation_restores_preexisting_non_dataset_directory(self):
    from projects.caelus import automation
    base = self.generated / "2099-01-15" / "ru"
    base.mkdir(parents=True)
    (base / "operator-note.txt").write_text("keep", encoding="utf-8")
    def broken(day, language, mode, model=None):
        write_json(base / "content.json", {"partial": True})
        raise RuntimeError("boom")
    with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            automation.run_generation_job("2099-01-15", "ru", mode="mock")
    self.assertEqual((base / "operator-note.txt").read_text(encoding="utf-8"), "keep")
    self.assertFalse((base / "content.json").exists())
```

- [ ] **Step 2: Run and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: generation job tests fail because `run_generation_job()` is not implemented.

- [ ] **Step 3: Implement transactional generation wrapper**

Implementation requirements inside `projects/caelus/automation.py`:

1. Acquire `operation_lock(day, language, "generate")` before inspecting/mutating generation state.
2. If `content.json` exists and `force=False`, record one `skipped` attempt and return `derive_state()` without calling `generate_bundle()`.
3. Start a `running` journal before calling generation.
4. If the target dataset directory exists for any reason, snapshot it to a `tempfile.TemporaryDirectory()` outside `generated/` before invoking generation. This protects both valid datasets and operator-created files in a directory that does not yet have `content.json`.
5. Call only the existing `generate_bundle(day, language, mode, model=model)` for generation business logic.
6. On success, delete the temporary snapshot, finish the journal with `success`, compute the final content hash from `content.json`, print the required `[CAELUS] ...` lines, and return `derive_state()`.
7. On exception, remove the post-failure target directory; restore the snapshot if one existed; finish the journal with `failed` and `sanitize_exception(exc)`; print a secret-safe failure line; re-raise the original exception.
8. Never place backups under `generated/`, so uniqueness/history scans cannot see them.

- [ ] **Step 4: Verify generation tests GREEN**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: generation tests PASS.

- [ ] **Step 5: Run a real mock integration using an isolated `GENERATED` path**

Add an integration test that uses the actual `generate_bundle(..., mode="mock")` and project assets, while patching all three `GENERATED` references used by storage/workflow/automation to a temporary folder. Assert:

```python
self.assertEqual(state.state, "review_required")
self.assertTrue((base / "content.json").is_file())
self.assertEqual(len(list((base / "cards").glob("caelus_*.png"))), 12)
self.assertTrue((base / "telegram").is_dir())
```

Run the test file again and require PASS.

---

### Task 4: Idempotent publication job and resume delegation

**Files:**
- Modify: `projects/caelus/publication.py:55-72`
- Modify: `projects/caelus/automation.py`
- Test: `tests/test_automation.py`

**Interfaces:**
- Consumes: `derive_state()`, automation journal/lock helpers, existing `run_telegram_publisher()` subprocess delegation and authoritative `publication.json`.
- Produces:
  - `AutomationPreconditionError(RuntimeError)`
  - `run_publication_job(day: str, language: str, *, dry_run: bool = False, force: bool = False) -> AutomationState`
  - `run_telegram_publisher(day: str, language: str, *, dry_run: bool = False, force: bool = False) -> tuple[int, str]`

- [ ] **Step 1: Add failing publication-job tests**

Add:

```python
class AutomationPublicationJobTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        for target in (
            "projects.caelus.automation_store.GENERATED",
            "projects.caelus.automation.GENERATED",
            "projects.caelus.storage.GENERATED",
        ):
            patcher = mock.patch(target, self.generated)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _approved(self):
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True, exist_ok=True)
        payload = {"iso_date": "2099-01-15", "language": "ru", "signs": {}}
        write_json(base / "content.json", payload)
        write_json(base / "status.json", {"state": "approved", "content_hash": content_hash(payload)})
        return base, payload

    def test_unapproved_publication_is_refused_without_calling_publisher(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        write_json(base / "content.json", {"iso_date": "2099-01-15", "language": "ru", "signs": {}})
        write_json(base / "status.json", {"state": "draft"})
        with mock.patch("projects.caelus.automation.run_telegram_publisher") as publisher:
            with self.assertRaises(automation.AutomationPreconditionError):
                automation.run_publication_job("2099-01-15", "ru")
        publisher.assert_not_called()
        self.assertEqual(automation.derive_state("2099-01-15", "ru").last_result, "skipped")

    def test_already_published_is_skipped_without_second_send(self):
        from projects.caelus import automation
        base, _ = self._approved()
        write_json(base / "publication.json", {"state": "published"})
        with mock.patch("projects.caelus.automation.run_telegram_publisher") as publisher:
            state = automation.run_publication_job("2099-01-15", "ru")
        publisher.assert_not_called()
        self.assertEqual(state.state, "published")
        self.assertEqual(state.last_result, "skipped")

    def test_ready_dataset_calls_existing_publisher_and_records_success(self):
        from projects.caelus import automation
        base, payload = self._approved()
        def fake_publisher(day, language, dry_run=False, force=False):
            write_json(base / "publication.json", {"state": "published", "content_hash": content_hash(payload)})
            return 0, "published"
        with mock.patch("projects.caelus.automation.run_telegram_publisher", side_effect=fake_publisher):
            state = automation.run_publication_job("2099-01-15", "ru")
        self.assertEqual(state.state, "published")
        self.assertEqual(state.last_result, "success")

    def test_partial_failure_remains_resumable_and_second_job_delegates_resume(self):
        from projects.caelus import automation
        base, payload = self._approved()
        calls = []
        def fake_publisher(day, language, dry_run=False, force=False):
            calls.append((day, language, dry_run, force))
            if len(calls) == 1:
                write_json(base / "publication.json", {
                    "state": "partially_published",
                    "content_hash": content_hash(payload),
                    "media": [{"group": 1, "message_ids": [101]}],
                    "text": [],
                })
                return 1, "network failed after album 1"
            write_json(base / "publication.json", {
                "state": "published",
                "content_hash": content_hash(payload),
                "media": [{"group": 1, "message_ids": [101]}, {"group": 2, "message_ids": [102]}],
                "text": [{"batch": 1, "message_id": 201}],
            })
            return 0, "resumed"
        with mock.patch("projects.caelus.automation.run_telegram_publisher", side_effect=fake_publisher):
            with self.assertRaisesRegex(RuntimeError, "network failed"):
                automation.run_publication_job("2099-01-15", "ru")
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publishing")
            state = automation.run_publication_job("2099-01-15", "ru")
        self.assertEqual(state.state, "published")
        self.assertEqual(len(calls), 2)
```

Add a flag-delegation test for `projects.caelus.publication.run_telegram_publisher()` that monkeypatches `subprocess.run` and asserts `--dry-run` and `--force` are added only when requested.

- [ ] **Step 2: Run and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: failures because publication automation and flag delegation are missing.

- [ ] **Step 3: Extend publisher subprocess wrapper without touching Telegram strategy**

Change only the wrapper at `projects/caelus/publication.py:55-72`:

```python
def run_telegram_publisher(
    day: str,
    language: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, str]:
    load_project_dotenv()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, "-m", "projects.caelus.publication", "--date", day, "--language", language]
    if dry_run:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    result = subprocess.run(...same existing options...)
    return result.returncode, result.stdout.strip()
```

Do not move or duplicate `validate_approved()`, Telegram grouping, progress journal, or send logic.

- [ ] **Step 4: Implement publication job semantics**

In `projects/caelus/automation.py`:

1. Acquire `operation_lock(..., "publish")`.
2. Derive current effective state.
3. If `published` and `force=False`, journal `skipped` and return without subprocess call.
4. If `force=False` and state is not one of `ready_to_publish`, `publish_failed`, `publishing`, journal `skipped` and raise `AutomationPreconditionError` with the effective state.
5. For `force=True`, do not bypass the existing publisher's `validate_approved()`/hash protection; simply delegate with `force=True` and let invalid approval fail before any send.
6. Start a `running` automation attempt, call `run_telegram_publisher(day, language, dry_run=dry_run, force=force)`, and treat nonzero return code as a failed automation attempt.
7. Never edit `publication.json` from automation code. After subprocess completion, derive effective state again from authoritative files.
8. On failure, journal sanitized automation error and re-raise a `RuntimeError` whose message contains only the subprocess output already returned by the CAELUS publisher.
9. On success, journal `success`; for `dry_run=True`, effective state is expected to remain `ready_to_publish` because no Telegram send occurred.

- [ ] **Step 5: Run focused and full suites**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
python -m unittest discover -s tests -v
```

Expected: all PASS.

---

### Task 5: Review Console automation panel, routed actions, and automation-aware history

**Files:**
- Modify: `admin_app.py:1-613`
- Modify: `tests/test_admin_diversity.py`
- Modify: `tests/test_automation.py`

**Interfaces:**
- Consumes: `derive_state()`, `run_generation_job()`, `run_publication_job()`, `iter_automation_targets()`.
- Produces:
  - `automation_status_html(day: str, language: str) -> str`
  - history rows that show effective state and latest automation result, including automation-only failed days.

- [ ] **Step 1: Add failing HTML/unit tests for automation panel**

Add to `tests/test_automation.py`:

```python
class AutomationAdminUiTests(TestCase):
    def test_automation_status_html_shows_state_attempt_and_safe_error(self):
        import admin_app
        from projects.caelus.automation import AutomationState
        state = AutomationState(
            state="generation_failed",
            date="2099-01-15",
            language="ru",
            content_hash=None,
            last_operation="generate",
            last_result="failed",
            attempt=2,
            last_error={"type": "AIProviderError", "message": "provider unavailable"},
            updated_at="2099-01-14T20:00:01+00:00",
        )
        with mock.patch("admin_app.derive_state", return_value=state):
            html = admin_app.automation_status_html("2099-01-15", "ru")
        self.assertIn("GENERATION FAILED", html)
        self.assertIn("generate", html)
        self.assertIn("Попытка: 2", html)
        self.assertIn("provider unavailable", html)

    def test_history_includes_automation_only_failed_generation(self):
        import admin_app
        from projects.caelus.automation import AutomationState
        failed = AutomationState(
            state="generation_failed", date="2099-01-15", language="ru",
            content_hash=None, last_operation="generate", last_result="failed",
            attempt=1, last_error={"type": "RuntimeError", "message": "failed"},
            updated_at="2099-01-14T20:00:01+00:00",
        )
        with mock.patch("admin_app.iter_automation_targets", return_value=[("2099-01-15", "ru")]), \
             mock.patch("admin_app.derive_state", return_value=failed), \
             mock.patch("admin_app.GENERATED", Path("/path/that/does/not/exist")):
            html = admin_app.history_html().decode("utf-8")
        self.assertIn("2099-01-15", html)
        self.assertIn("GENERATION FAILED", html)
        self.assertNotIn("_AUTOMATION", html)
```

Add source-level route tests that assert `admin_app.py` imports and uses `run_generation_job` for `/generate` and `/generate-tomorrow`, and `run_publication_job` for `/publish-telegram`, while `run_daily.py` still imports `generate_bundle` directly.

- [ ] **Step 2: Run and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
python -m unittest discover -s tests -p "test_admin_diversity.py" -v
```

Expected: missing automation UI/import/route failures.

- [ ] **Step 3: Add a compact automation panel to Review Console**

In `admin_app.py`:

- import `derive_state`, `run_generation_job`, `run_publication_job` from `projects.caelus.automation` and `iter_automation_targets` from `projects.caelus.automation_store`;
- add CSS only for the new compact panel/chips, reusing existing colors and `.notice`/`.quality-chip` where possible;
- implement `automation_status_html(day, language)` showing:
  - effective state;
  - last operation/result;
  - attempt;
  - updated time;
  - sanitized last error.

State labels must be human-readable but preserve the exact state token, for example `GENERATION FAILED`, `REVIEW REQUIRED`, `READY TO PUBLISH`, `PUBLISHED`.

Insert this panel into both the empty-dataset and existing-dataset review pages.

For `generation_failed`, the empty-page AI generation button text becomes `Retry AI generation`; it still calls the same `/generate` route and does not loop automatically.

- [ ] **Step 4: Route Review Console actions through automation jobs**

Change only these POST routes:

```text
/generate-tomorrow -> run_generation_job(target_day, language, mode="ai")
/generate          -> run_generation_job(day, language, mode=form.get("mode", "mock"))
/publish-telegram  -> run_publication_job(day, language)
```

Keep all other routes (`save`, regeneration, diversity, approve, return-to-draft) on their existing code paths.

For successful publication, keep `publish_result_html()` but provide a concise automation summary string:

```text
Automation result: success
Effective state: published
Attempt: N
```

On publication failure, allow the existing exception handler to render the error page; `run.json` and the automation panel provide persistent diagnostics after the operator returns to Review Console.

Do not add a second Publish button.

- [ ] **Step 5: Make History page use the union of datasets and automation targets**

Build a set of `(day, language)` pairs from:

1. normal dataset directories that contain `content.json`;
2. `iter_automation_targets()`.

For each pair, call `derive_state()`. Render existing review/publication columns plus:

```text
AUTOMATION STATE | LAST RUN
```

where last run is `operation / result / attempt` or `—`.

Explicitly skip `generated/_automation` from normal dataset scanning.

- [ ] **Step 6: Run focused UI tests and full suite**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
python -m unittest discover -s tests -p "test_admin_diversity.py" -v
python -m unittest discover -s tests -v
```

Expected: all PASS.

- [ ] **Step 7: HTTP smoke test the actual Review Console**

Using a temporary generated dataset and a non-default test port, start `admin_app.py`, then request:

```text
/review?date=<test-day>&language=ru
/history
```

Verify HTTP 200 after following redirects and assert the HTML contains `Automation`, the effective state, and no secret-like values. Stop the server in `finally`.

---

### Task 6: Dedicated operator automation CLI

**Files:**
- Create: `projects/caelus/automation_cli.py`
- Create: `automation.py`
- Modify: `tests/test_automation.py`
- Modify: `README_RU.md`

**Interfaces:**
- Consumes: public Stage 1 API from `projects.caelus.automation`.
- Produces operator commands:
  - `python automation.py status --date YYYY-MM-DD --language ru`
  - `python automation.py generate --date YYYY-MM-DD --language ru [--mock] [--model MODEL] [--force]`
  - `python automation.py publish --date YYYY-MM-DD --language ru [--dry-run] [--force]`

- [ ] **Step 1: Add failing CLI tests**

Add:

```python
class AutomationCliTests(TestCase):
    def test_root_automation_entrypoint_is_thin(self):
        source = Path("automation.py").read_text(encoding="utf-8")
        self.assertIn("projects.caelus.automation_cli", source)
        self.assertNotIn("def run_generation_job", source)
        self.assertNotIn("def run_publication_job", source)

    def test_status_cli_prints_machine_readable_json(self):
        from projects.caelus import automation_cli
        from projects.caelus.automation import AutomationState
        state = AutomationState(
            state="review_required", date="2099-01-15", language="ru",
            content_hash="abc", last_operation="generate", last_result="success",
            attempt=1, last_error=None, updated_at="2099-01-14T20:00:00+00:00",
        )
        with mock.patch("projects.caelus.automation_cli.derive_state", return_value=state), \
             mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            code = automation_cli.main(["status", "--date", "2099-01-15", "--language", "ru"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["state"], "review_required")
        self.assertEqual(payload["last_result"], "success")
```

Add tests that `generate --mock` calls `run_generation_job(..., mode="mock")`, and `publish --dry-run` calls `run_publication_job(..., dry_run=True)`.

- [ ] **Step 2: Run and verify RED**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
```

Expected: CLI module/entrypoint failures.

- [ ] **Step 3: Implement CLI**

`projects/caelus/automation_cli.py` must:

- call `projects.caelus.config.load_dotenv()` once before execution;
- accept an optional `argv: list[str] | None` for testability;
- use subparsers `status`, `generate`, `publish`;
- print the returned `AutomationState` as UTF-8 JSON using `dataclasses.asdict()` and `json.dumps(..., ensure_ascii=False, indent=2)`;
- return `0` on success;
- let application exceptions propagate to the root entrypoint, which prints a concise `[ERROR] ...` and exits nonzero.

Root `automation.py` must contain only imports, `main()`, and the normal `if __name__ == "__main__"` exit wrapper.

- [ ] **Step 4: Verify CLI tests and manual compatibility**

Run:

```text
python -m unittest discover -s tests -p "test_automation.py" -v
python automation.py status --date 2099-01-15 --language ru
python run_daily.py --help
python publish_telegram.py --help
python admin_app.py --help
```

For `admin_app.py`, if `--help` is not supported by its server-style entrypoint, validate import instead:

```text
python -c "import admin_app; print('admin import OK')"
```

Expected: automation status emits JSON; legacy commands/import remain operational.

---

### Task 7: Full integration, docs, version, and release verification

**Files:**
- Modify: `README_RU.md`
- Modify: `ARCHITECTURE.md`
- Modify: `VERSION`
- Create: `RELEASE_NOTES_CAELUS_AUTOMATION_STAGE1_v1_11.md`
- Test: all tests and runtime acceptance paths

**Interfaces:**
- Consumes: completed Stage 1 automation API and existing CAELUS v1.10 behavior.
- Produces: a releasable v1.11 snapshot with Stage 1 automation foundation and no scheduler.

- [ ] **Step 1: Add end-to-end mock state-flow integration test**

Add one test that drives:

```text
pending_generation
    -> run_generation_job(mode="mock")
review_required
    -> existing workflow.approve(...)
ready_to_publish
    -> run_publication_job() with monkeypatched publisher writing published journal
published
```

Assertions must check each derived state and that the automation journal history contains one successful generate and one successful publish attempt.

- [ ] **Step 2: Add regression tests for unchanged manual paths**

Assert source/import boundaries:

```python
def test_manual_cli_paths_are_not_redirected_through_automation():
    run_daily = Path("run_daily.py").read_text(encoding="utf-8")
    publish = Path("publish_telegram.py").read_text(encoding="utf-8")
    self.assertIn("projects.caelus.workflow import generate_bundle", run_daily)
    self.assertNotIn("run_generation_job", run_daily)
    self.assertIn("projects.caelus.publication import *", publish)
    self.assertNotIn("run_publication_job", publish)
```

Also assert `kaban/` contains no import of `projects.caelus.automation`.

- [ ] **Step 3: Run the complete automated test suite**

Run:

```text
python -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Compare v1.10 and Stage 1 mock user artifacts**

From two clean extracted copies with identical starting history, run the same mock day and compare SHA-256 for:

- 12 `cards/caelus_*.png` files;
- all Telegram markdown artifacts generated by the normal manual `run_daily.py --mock` path;
- semantic `content.json.signs` equality.

Expected: user artifacts are byte-identical; differences are permitted only in runtime timestamps/status metadata.

- [ ] **Step 5: Verify automation-specific runtime acceptance**

Using a clean temporary day:

1. `python automation.py status ...` → `pending_generation`.
2. `python automation.py generate ... --mock` → `review_required`.
3. Repeat the same generate without `--force` → `skipped`, no artifact hash changes.
4. Approve through the existing workflow/test helper → `ready_to_publish`.
5. `python automation.py publish ... --dry-run` → no Telegram send and still `ready_to_publish`, automation `last_result=success`.
6. Simulate failed publication with monkeypatched transport → `publish_failed` or `publishing` according to authoritative publication journal; next run resumes.
7. Create a stale `generate.lock` manually and confirm a second generate refuses with the lock path and does not call generation.

- [ ] **Step 6: Verify Review Console runtime behavior**

Start Review Console and confirm:

- empty day shows `PENDING GENERATION` or `GENERATION FAILED` panel;
- generated draft shows `REVIEW REQUIRED`;
- approved day shows `READY TO PUBLISH`;
- published day shows `PUBLISHED`;
- History shows automation-only failed generation days;
- no API key/token substring appears in HTML;
- existing regeneration, Approve All, Return to draft, and Telegram publication button behavior remains intact.

- [ ] **Step 7: Update documentation and version only after verification**

Update `README_RU.md` with the three new operator commands and a prominent statement:

```text
Stage 1 не запускает задачи по времени и не публикует автоматически.
```

Update `ARCHITECTURE.md` to show:

```text
root operator CLI / Review Console
              ↓
projects.caelus.automation
              ↓
existing CAELUS workflow/publication
              ↓
KABAN Core services
```

Set `VERSION` to:

```text
1.11
```

Create `RELEASE_NOTES_CAELUS_AUTOMATION_STAGE1_v1_11.md` summarizing state derivation, run journal, idempotency, rollback, locks, Review Console integration, and explicit non-goals.

- [ ] **Step 8: Final release hygiene and unpacked-artifact verification**

Create the clean ZIP only after deleting test dates, local `.env`, `__pycache__`, `.pytest_cache`, and `.pyc` files. Preserve `.env.example`.

After creating the ZIP, extract it to a fresh directory and re-run:

```text
python -m unittest discover -s tests -v
python automation.py status --date 2099-01-15 --language ru
python -c "import admin_app; import projects.caelus.automation; print('imports OK')"
```

Inspect ZIP entries and fail release verification if any of these are present:

```text
.env
__pycache__/
.pytest_cache/
*.pyc
known test-date directories under generated/
```

Record the final ZIP SHA-256 and the exact automated test count in the verification report.

---

## Self-Review Results

### Spec coverage

All acceptance criteria map to tasks:

- derived states → Task 1;
- generation skip/force/rollback → Task 3;
- publication approval/duplicate/resume protections → Task 4;
- failed job diagnostics and error sanitization → Tasks 1–4;
- concurrent invocation lock → Task 2;
- Review Console and history visibility → Task 5;
- manual CLI preservation → Tasks 6–7;
- no scheduler/auto-publish → Global Constraints and Task 7 regression boundaries.

### Placeholder scan

The plan contains no `TBD`, no deferred implementation placeholders, and no generic “add tests/error handling” steps. Every implementation task names exact interfaces and verification commands.

### Type consistency

`AutomationState`, `derive_state()`, `run_generation_job()`, and `run_publication_job()` signatures match the approved spec. Storage helper signatures are introduced once in Task 1 and reused consistently in later tasks.

### Review Focus coverage

All five Review Focus risks have explicit tests in Tasks 1–5, including stale locks, corrupt run journal, forced-generation rollback, partial publication resume, and automation-only failed history entries.

### Execution note

This release snapshot has no Git metadata. Execution must not initialize a repository or fabricate commits merely to satisfy workflow conventions. Verification evidence is the test suite, runtime acceptance checks, artifact comparison, and final ZIP SHA-256.
