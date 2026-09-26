# KABAN Stage 4 — Serverless Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить к KABAN универсальный cloud/serverless runtime на Supabase PostgreSQL + Cloudflare R2 + Cloudflare Worker + GitHub Actions, сохранив существующую CAELUS business logic и полностью поддерживая local/Docker runtime v1.13.

**Architecture:** Supabase/R2 становятся canonical persistence только в `KABAN_PERSISTENCE=cloud`; в local mode filesystem остаётся canonical. GitHub Action создаёт disposable workspace, KABAN Core материализует canonical state, Project adapter раскладывает его в привычный Project filesystem, существующий CAELUS workflow выполняется без serverless-переписывания, затем Core собирает изменения, загружает immutable artifacts в R2 и атомарно commit-ит Supabase с version/fence checks. Cloudflare Worker остаётся лёгким control plane: Access auth, safe reads, command creation, Cron wakeup, wait-condition checks и условный GitHub `workflow_dispatch`.

**Tech Stack:** Python 3.13; existing KABAN/CAELUS; pytest + unittest; PostgreSQL/Supabase; Supabase PostgREST/RPC over HTTPS; Cloudflare R2 S3 API via `boto3`; Cloudflare Workers TypeScript; Wrangler; Vitest; GitHub Actions; Cloudflare Access.

**Spec:** `docs/superpowers/specs/2026-09-26-kaban-stage4-serverless-runtime-design.md` (утверждённый spec должен быть добавлен в repository без изменения содержания из `2026-09-26-KABAN-Stage4-Serverless-Runtime-Design-Spec.md`).

## Global Constraints

- Последняя стабильная база: KABAN v1.13, SHA-256 исходного ZIP `5fa83d2817545c713ce4ab10a9a19fbde54e55f758a053e4a143d0056f711691`.
- До начала Stage 4 baseline должен подтверждать `pytest: 210/210 PASS` и `unittest: 92/92 PASS` в текущем окружении.
- Любое изменение кода: **RED → GREEN → relevant regression → full regression перед merge/release**.
- `kaban/` никогда не импортирует `projects.caelus`.
- Универсальный механизм принадлежит `kaban/`; CAELUS-specific projection/UI/rules принадлежат `projects/caelus/`.
- Projects не импортируют друг друга.
- `KABAN_PERSISTENCE` отсутствует или равен `local` → cloud credentials не требуются.
- `KABAN_PERSISTENCE=cloud` → canonical JSON/state в Supabase, durable generated binaries в R2, local workspace disposable.
- В cloud persistence API каждый вызов требует explicit `project_id`; implicit active project запрещён.
- Один Supabase Project для всех KABAN Projects; разделение через `project_id` + composite foreign keys + RLS.
- Один private R2 bucket на environment; обязательный prefix `projects/{project_id}/`.
- R2 objects immutable; upload → verify → DB pointer commit. Overwrite existing canonical key запрещён.
- GitHub Action workspace: `$RUNNER_TEMP/kaban/<execution_id>/<project_id>/`; reuse между executions запрещён.
- Scheduled identity: `unique(project_id, job_id, slot_id)`.
- Mutating operation должна пройти execution lease/fence и resource lease/fence; stale runner не может commit/checkpoint.
- Edit/regeneration создаёт новую revision; approval старой revision не переносится.
- Publication связывается с exact `revision_id + content_hash + channel_id`.
- `sent` publication step автоматически повторно не отправляется.
- Ambiguous Telegram side effect → `unknown_delivery`; автоматический retry запрещён.
- Cloudflare Cron — wakeup/control plane, не Python business runtime.
- Blocked approval не запускает GitHub Action каждые 10 минут.
- Browser не получает Supabase service credential, R2 secret, GitHub token, OpenAI key или Telegram token.
- Migration additive, dry-run by default, source `generated/`/`runtime/` не изменяется.
- Cloud → local export обязателен до rollback после cloud-only mutations.
- Никаких auto-upgrade provider plans и auto-delete пользовательской canonical history/artifacts.
- EN production остаётся выключенным; Instagram publication и auto-approve не входят в Stage 4.
- Не выполнять unrelated refactor.

## Review Focus

1. **Stale runner после lease takeover:** любой поздний content commit или publication checkpoint старого fence должен завершаться conflict/no-op, а не перезаписью canonical state. Тест закрепляется в Tasks 3, 4, 8 и 10.
2. **Ambiguous Telegram delivery:** timeout/network failure после перехода step в `sending` должен давать `unknown_delivery` и запрещать автоматическую повторную отправку. Тест закрепляется в Task 10.
3. **Cross-project reference/access:** CAELUS execution/artifact/revision не может прочитать или связать row другого Project даже при ошибочном ID. Тест закрепляется в Tasks 3, 4, 5 и 11.
4. **Partial R2 success + failed DB commit:** canonical revision pointer остаётся прежним, uploaded object остаётся orphan до safe cleanup. Тест закрепляется в Tasks 5, 8 и 15.
5. **Stale Review Console tab / duplicate browser POST:** `expected_version` mismatch возвращает conflict; повтор с тем же `idempotency_key` возвращает существующую execution и не создаёт вторую operation. Тест закрепляется в Tasks 3, 11 и 12.

---

# File/Module Map

## KABAN Core — new universal modules

| File | Responsibility |
|---|---|
| `kaban/runtime/__init__.py` | public runtime-path helpers |
| `kaban/runtime/paths.py` | `KABAN_GENERATED_DIR`, `KABAN_RUNTIME_DIR`, persistence mode |
| `kaban/cloud/__init__.py` | public cloud runtime API |
| `kaban/cloud/models.py` | immutable dataclasses/enums for executions, snapshots, changes, artifacts, commands, leases |
| `kaban/cloud/contracts.py` | Protocol interfaces: control store, artifact store, project workspace adapter, checkpoint client |
| `kaban/cloud/config.py` | cloud env validation without importing project code |
| `kaban/cloud/supabase.py` | Supabase REST/RPC client implementing control-store contract |
| `kaban/cloud/r2.py` | immutable R2 artifact store |
| `kaban/cloud/adapters.py` | dynamic Project cloud-adapter loading |
| `kaban/cloud/workspace.py` | disposable workspace lifecycle + materialize/collect orchestration |
| `kaban/cloud/runner.py` | one execution lifecycle in GitHub Action |
| `kaban/cloud/core_operations.py` | registry/handlers for universal KABAN maintenance executions |
| `kaban/cloud/schedule_projection.py` | Python-owned cron/timezone projection into absolute cloud slots |
| `kaban/cloud/publication.py` | generic publication checkpoint API and local/cloud implementations |
| `kaban/cloud/backup.py` | compact application-level control-plane snapshot to R2 |
| `kaban/cloud/usage.py` | provider usage samples + threshold classification |
| `kaban/cloud/migration.py` | generic migration manifest primitives and cloud→local export orchestration |
| `kaban/cloud/ui_packager.py` | packages Project-owned static cloud UI into Worker asset directory |
| `cloud_runtime.py` | thin root CLI wrapper over `kaban.cloud` commands |

## CAELUS — Project-owned changes/new modules

| File | Responsibility |
|---|---|
| `projects/caelus/storage.py` | single generated-root resolver and existing filesystem paths |
| `projects/caelus/cloud_adapter.py` | CAELUS content key/resource key, materialization, existing workflow dispatch, change collection |
| `projects/caelus/publication.py` | existing Telegram strategy + generic checkpoint hook around each send |
| `projects/caelus/scheduler.py` | maps blocked publication to generic approval wait condition |
| `projects/caelus/project.yaml` | declarative cloud adapter reference; existing schedule unchanged |
| `projects/caelus/cloud_ui/manifest.json` | CAELUS cloud UI metadata |
| `projects/caelus/cloud_ui/index.html` | CAELUS cloud Review Console shell |
| `projects/caelus/cloud_ui/app.js` | CAELUS-specific screen/actions calling generic Worker API |
| `projects/caelus/cloud_ui/styles.css` | CAELUS visual styling |

## Database / deployment

| File | Responsibility |
|---|---|
| `deploy/supabase/migrations/202609260001_stage4_schema.sql` | Stage 4 tables, indexes, composite FKs, constraints |
| `deploy/supabase/migrations/202609260002_stage4_rls.sql` | RLS deny-by-default policies/grants |
| `deploy/supabase/migrations/202609260003_stage4_rpc.sql` | atomic claim/renew/fence/CAS/publication RPC functions |
| `deploy/cloudflare/worker/package.json` | Worker dev/test dependencies/scripts |
| `deploy/cloudflare/worker/wrangler.jsonc` | Worker routes, cron, static assets, env bindings |
| `deploy/cloudflare/worker/src/*.ts` | generic control plane |
| `deploy/cloudflare/worker/test/*.test.ts` | Worker unit tests |
| `.github/workflows/kaban-cloud-execution.yml` | heavy Python execution workflow |
| `.github/workflows/kaban-cloud-ci.yml` | Python + Worker test workflow; no scheduled heavy run |
| `deploy/README_SERVERLESS_RU.md` | step-by-step setup, secrets, migration, acceptance, rollback |

## Tests — new Python suites

`tests/test_runtime_paths.py`, `tests/test_cloud_models.py`, `tests/test_cloud_supabase.py`, `tests/test_cloud_r2.py`, `tests/test_cloud_workspace.py`, `tests/test_caelus_cloud_adapter.py`, `tests/test_cloud_runner.py`, `tests/test_cloud_schedule_projection.py`, `tests/test_cloud_publication.py`, `tests/test_cloud_migration.py`, `tests/test_cloud_usage.py`, `tests/test_stage4_architecture.py`, `tests/test_stage4_acceptance.py`.

---

### Task 1: Add runtime roots without changing local/Docker behavior

**Deliverable:** Все CAELUS references к generated root идут через один Project resolver, `KABAN_GENERATED_DIR` работает для ephemeral cloud workspace, v1.13 local behavior byte-compatible.

**Files:**
- Create: `kaban/runtime/__init__.py`
- Create: `kaban/runtime/paths.py`
- Modify: `projects/caelus/storage.py`
- Modify: `projects/caelus/publication.py:18-31,263`
- Modify: `projects/caelus/workflow.py:27-39` и все direct `GENERATED / ...`
- Modify: `projects/caelus/automation_store.py:12-19,101`
- Modify: `projects/caelus/automation.py:12`
- Modify: `admin_app.py:13-14,451-547`
- Modify: `generate_forecasts.py`
- Test: `tests/test_runtime_paths.py`
- Update tests only where they patch old module constants: `tests/test_storage_core.py`, `tests/test_automation.py`, `tests/test_diversity.py`

**Interfaces:**
- Produces: `persistence_mode() -> Literal["local", "cloud"]`
- Produces: `generated_root(default_root: Path) -> Path`
- Produces: `runtime_root(default_root: Path) -> Path`
- Produces: `projects.caelus.storage.generated_root() -> Path`
- Existing `day_dir/content_path/status_path/publication_path` signatures remain unchanged.

- [ ] **Step 1: Write RED tests for runtime mode and generated-root override**

```python
# tests/test_runtime_paths.py
from pathlib import Path
import pytest

from kaban.runtime.paths import generated_root, persistence_mode


def test_persistence_defaults_to_local(monkeypatch):
    monkeypatch.delenv("KABAN_PERSISTENCE", raising=False)
    assert persistence_mode() == "local"


def test_generated_root_uses_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KABAN_GENERATED_DIR", str(tmp_path / "ephemeral" / "generated"))
    assert generated_root(Path("/repo/generated")) == tmp_path / "ephemeral" / "generated"


def test_unknown_persistence_mode_fails(monkeypatch):
    monkeypatch.setenv("KABAN_PERSISTENCE", "mixed")
    with pytest.raises(ValueError, match="KABAN_PERSISTENCE"):
        persistence_mode()
```

- [ ] **Step 2: Run RED**

Run:

```bash
python -m pytest tests/test_runtime_paths.py -q
```

Expected: FAIL because `kaban.runtime.paths` does not exist.

- [ ] **Step 3: Implement the minimal universal path helpers**

```python
# kaban/runtime/paths.py
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
```

In `projects/caelus/storage.py` make this the only CAELUS root resolver:

```python
from kaban.runtime.paths import generated_root as resolve_generated_root


def generated_root() -> Path:
    return resolve_generated_root(ROOT / "generated")


def day_dir(day: str, language: str) -> Path:
    ...
    return generated_root() / day / language
```

Replace direct `ROOT / "generated"` and module-local alternate roots with `projects.caelus.storage.generated_root()`/path helpers. Keep compatibility only where an existing test/API requires `GENERATED`; if retained temporarily, define `GENERATED = generated_root()` and forbid new uses with an architecture test.

- [ ] **Step 4: Add architecture test that no CAELUS module defines another hard-coded generated root**

```python
def test_caelus_has_single_generated_root_definition():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "projects" / "caelus").rglob("*.py"):
        if path.name == "storage.py":
            continue
        if 'ROOT / "generated"' in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
```

- [ ] **Step 5: GREEN and focused CAELUS regression**

```bash
python -m pytest tests/test_runtime_paths.py tests/test_storage_core.py tests/test_automation.py tests/test_diversity.py tests/test_project_workflow_publication.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Full pre-cloud regression gate**

```bash
python -m pytest -q
python -m unittest discover -s tests -p "test*.py"
```

Expected: all current + new tests PASS. Existing sample/mock artifacts used by v1.13 acceptance must remain byte-identical; if a byte diff appears, stop and diagnose before Task 2.

- [ ] **Step 7: Commit**

```bash
git add kaban/runtime projects/caelus admin_app.py generate_forecasts.py tests

git commit -m "refactor: add runtime path boundary"
```

---

### Task 2: Define universal cloud contracts and immutable data models

**Deliverable:** KABAN Core has stable project-agnostic interfaces before any provider code is added.

**Files:**
- Create: `kaban/cloud/__init__.py`
- Create: `kaban/cloud/models.py`
- Create: `kaban/cloud/contracts.py`
- Create: `kaban/cloud/config.py`
- Test: `tests/test_cloud_models.py`
- Test: `tests/test_stage4_architecture.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ExecutionCommand:
    execution_id: UUID
    project_id: str
    operation: str
    content_key: str | None
    content_set_id: UUID | None
    expected_version: int | None
    payload: Mapping[str, Any]
    requested_by: str
    idempotency_key: str

@dataclass(frozen=True)
class MaterializationSpec:
    content_keys: tuple[str, ...]
    history_locale: str | None
    history_before: date | None
    history_days: int
    setting_keys: tuple[str, ...]
    artifact_kinds: tuple[str, ...]

@dataclass(frozen=True)
class RevisionSnapshot:
    revision_id: UUID
    content_set_id: UUID
    revision_no: int
    payload: Mapping[str, Any]
    content_hash: str
    created_at: datetime

@dataclass(frozen=True)
class ContentSetSnapshot:
    content_set_id: UUID
    project_id: str
    content_key: str
    content_date: date | None
    locale: str | None
    version: int
    current_revision: RevisionSnapshot | None
    approved_revision_id: UUID | None
    approved_content_hash: str | None

@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: UUID
    project_id: str
    content_set_id: UUID | None
    revision_id: UUID | None
    kind: str
    logical_name: str
    r2_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, Any]

@dataclass(frozen=True)
class CanonicalSnapshot:
    project_id: str
    content_sets: tuple[ContentSetSnapshot, ...]
    history_revisions: tuple[RevisionSnapshot, ...]
    settings: Mapping[str, Any]
    artifacts: tuple[ArtifactRef, ...]
    publication: Mapping[str, Any]

@dataclass(frozen=True)
class ArtifactCandidate:
    kind: str
    logical_name: str
    source_path: Path
    sha256: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, Any]

@dataclass(frozen=True)
class ChangeSet:
    content_set_id: UUID | None
    expected_version: int | None
    new_payload: Mapping[str, Any] | None
    approval_action: str | None
    setting_updates: Mapping[str, Any]
    artifacts: tuple[ArtifactCandidate, ...]
    publication_updates: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]

@dataclass(frozen=True)
class LeaseClaim:
    owner: str
    fence_token: int
    lease_expires_at: datetime

@dataclass(frozen=True)
class CommitResult:
    content_set_id: UUID | None
    revision_id: UUID | None
    version: int | None

@dataclass(frozen=True)
class StoredArtifact:
    r2_key: str
    sha256: str
    size_bytes: int
    mime_type: str

@dataclass(frozen=True)
class PublicationStepState:
    publication_run_id: UUID
    step_key: str
    state: str
    request_fingerprint: str
    external_ids: Mapping[str, Any] | None
```

Protocols:

```python
class ControlStore(Protocol):
    def load_execution(self, execution_id: UUID) -> ExecutionRecord: ...
    def start_execution(self, execution_id: UUID, owner: str, lease_seconds: int) -> LeaseClaim | None: ...
    def renew_execution(self, execution_id: UUID, owner: str, fence_token: int, lease_seconds: int) -> LeaseClaim: ...
    def claim_resource(self, project_id: str, resource_key: str, execution_id: UUID, lease_seconds: int) -> LeaseClaim: ...
    def load_snapshot(self, project_id: str, spec: MaterializationSpec) -> CanonicalSnapshot: ...
    def commit_changes(self, command: ExecutionCommand, changes: ChangeSet, *, execution_fence: int, resource_fence: int | None) -> CommitResult: ...
    def finish_execution(self, execution_id: UUID, fence_token: int, outcome: str, error: Mapping[str, Any] | None = None) -> None: ...

class ArtifactStore(Protocol):
    def download(self, project_id: str, artifact: ArtifactRef, destination: Path) -> None: ...
    def put_immutable(self, project_id: str, key: str, source: Path, expected_sha256: str) -> StoredArtifact: ...

class ProjectWorkspaceAdapter(Protocol):
    def materialization_spec(self, command: ExecutionCommand) -> MaterializationSpec: ...
    def resource_key(self, command: ExecutionCommand, snapshot: CanonicalSnapshot) -> str | None: ...
    def materialize(self, command: ExecutionCommand, snapshot: CanonicalSnapshot, workspace: WorkspacePaths, artifacts: ArtifactStore) -> ProjectWorkspaceState: ...
    def execute(self, command: ExecutionCommand, state: ProjectWorkspaceState) -> ProjectExecutionResult: ...
    def collect(self, command: ExecutionCommand, state: ProjectWorkspaceState, before: CanonicalSnapshot) -> ChangeSet: ...
```

- [ ] **Step 1: Write RED model validation tests**

Test explicit project IDs, non-empty idempotency keys, immutable tuples/mappings, invalid artifact key rejection, and execution states.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_cloud_models.py tests/test_stage4_architecture.py -q
```

Expected: FAIL because cloud contracts do not exist.

- [ ] **Step 3: Implement dataclasses, enums, Protocols and cloud config validation**

`CloudConfig.from_env()` must require cloud credentials only when `KABAN_PERSISTENCE=cloud`; importing `kaban.cloud` in local mode must not read secrets or perform network I/O.

- [ ] **Step 4: Add dependency-direction test**

```python
def test_kaban_core_never_imports_caelus():
    root = Path(__file__).resolve().parents[1] / "kaban"
    offenders = []
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "projects.caelus" in source:
            offenders.append(path.as_posix())
    assert offenders == []
```

Also create a synthetic `projects/demo` fixture in the test temp directory and prove `ProjectWorkspaceAdapter` loading does not require CAELUS.

- [ ] **Step 5: GREEN + regression**

```bash
python -m pytest tests/test_cloud_models.py tests/test_stage4_architecture.py tests/test_projects.py tests/test_scheduler_adapter.py -q
```

- [ ] **Step 6: Commit**

```bash
git add kaban/cloud tests/test_cloud_models.py tests/test_stage4_architecture.py

git commit -m "feat: define cloud runtime contracts"
```

---

### Task 3: Create Supabase schema, RLS and atomic RPCs

**Deliverable:** PostgreSQL schema enforces project isolation, immutable revision relations, idempotency, leases/fences and publication step transitions in the database itself.

**Files:**
- Create: `deploy/supabase/migrations/202609260001_stage4_schema.sql`
- Create: `deploy/supabase/migrations/202609260002_stage4_rls.sql`
- Create: `deploy/supabase/migrations/202609260003_stage4_rpc.sql`
- Test: `tests/test_stage4_schema_contract.py` — static contract always runs; optional live SQL checks invoke local `psql` against `KABAN_TEST_DATABASE_URL` when both are available

**Schema:**

Create exactly these Stage 4 tables from the approved spec:

- `kaban_projects`
- `kaban_channels`
- `kaban_project_settings`
- `kaban_content_sets`
- `kaban_content_revisions`
- `kaban_approvals`
- `kaban_artifacts`
- `kaban_scheduler_jobs`
- `kaban_executions`
- `kaban_resource_leases`
- `kaban_execution_events`
- `kaban_publication_runs`
- `kaban_publication_steps`
- `kaban_usage_samples`

Add to `kaban_executions` the command-envelope fields required by spec §42:

```sql
command_payload jsonb not null default '{}'::jsonb,
requested_by text,
idempotency_key text,
dispatch_nonce uuid
```

Constraints/indexes must include:

```sql
unique (project_id, content_key)
unique (project_id, content_set_id)
unique (project_id, revision_id)
unique (project_id, job_id, slot_id) where slot_id is not null
unique (project_id, idempotency_key) where idempotency_key is not null
unique (project_id, r2_key)
unique (project_id, publication_key)
```

Every FK between project-scoped entities uses `(project_id, foreign_id)`; no single-column FK may bypass project scope.

All domain/runtime tables enable RLS. `anon` and `authenticated` receive no direct mutation grants. Browser access is only through Worker server credentials. SQL functions use explicit search path and validate `project_id`.

**Required RPC functions:**

```text
kaban_create_or_get_command(... idempotency key ...)
kaban_start_execution(execution_id, owner, lease_seconds)
kaban_renew_execution(execution_id, owner, fence_token, lease_seconds)
kaban_claim_resource(project_id, resource_key, execution_id, lease_seconds)
kaban_renew_resource(project_id, resource_key, execution_id, fence_token, lease_seconds)
kaban_release_resource(...)
kaban_finish_execution(execution_id, fence_token, outcome, error)
kaban_commit_content_revision(... expected_version, execution_fence, resource_fence ...)
kaban_record_approval(... expected_version, fence checks ...)
kaban_create_or_get_publication_run(... publication_key ...)
kaban_transition_publication_step(... from_state, to_state, fence_token ...)
kaban_mark_stale_sending_unknown(...)
kaban_claim_due_execution(now, worker_owner, lease_seconds)
kaban_schedule_dispatch_retry(...)
```

- [ ] **Step 1: RED static schema contract**

Write a test that parses SQL text and requires all tables, `project_id`, RLS statements, composite unique/FK patterns and RPC names. This test runs even without a live DB.

- [ ] **Step 2: RED live database contract when test DB is configured**

Use `KABAN_TEST_DATABASE_URL`; if absent, mark only this provider integration test `skip`, never report it as executed. Live test creates projects `alpha` and `beta`, then proves a `beta` revision cannot be referenced by an `alpha` artifact/approval/publication row.

- [ ] **Step 3: Implement migrations**

Critical transition checks belong in SQL, not only Python. Example fence predicate used by mutation RPCs:

```sql
where e.execution_id = p_execution_id
  and e.project_id = p_project_id
  and e.lease_owner = p_owner
  and e.fence_token = p_execution_fence
  and e.lease_expires_at > now()
```

Content commit must lock the `kaban_content_sets` row, compare `version = p_expected_version`, insert immutable revision/artifact metadata, switch `current_revision_id`, increment version, and only then return committed version/revision.

- [ ] **Step 4: Add stale-fence and idempotency live tests**

Cases:

1. owner A obtains fence 1;
2. lease expires/takeover gives owner B fence 2;
3. owner A commit with fence 1 is rejected;
4. duplicate `idempotency_key` returns same execution ID;
5. duplicate scheduled `(project_id, job_id, slot_id)` creates no second row.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_stage4_schema_contract.py -q
```

If `KABAN_TEST_DATABASE_URL` is configured, expected: static + live tests PASS. If it is not configured, output must explicitly show provider integration tests SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add deploy/supabase tests/test_stage4_schema_contract.py tests/sql

git commit -m "feat: add stage4 supabase schema"
```

---

### Task 4: Implement the Python Supabase control store

**Deliverable:** GitHub runner and migration tools can use one project-scoped Python API; atomic semantics stay in database RPCs.

**Files:**
- Create: `kaban/cloud/supabase.py`
- Modify: `kaban/cloud/config.py`
- Test: `tests/test_cloud_supabase.py`

**Interfaces:** Implements `ControlStore` from Task 2 using Supabase REST/PostgREST and RPC endpoints. Do not embed CAELUS field names.

Use standard-library HTTPS (`urllib.request`) to avoid pulling a large Supabase SDK only for REST calls. Central request method:

```python
class SupabaseControlStore:
    def __init__(self, base_url: str, service_key: str, timeout: int = 30): ...

    def _request(self, method: str, path: str, *, payload: Any = None, query: Mapping[str, str] | None = None) -> Any: ...
```

Headers:

```text
apikey: <service key>
Authorization: Bearer <service key>
Content-Type: application/json
Accept: application/json
```

Never place credentials in exception messages. Map HTTP 409 / RPC conflict responses to typed `VersionConflict`, `LeaseConflict`, `ProjectIsolationError`.

- [ ] **Step 1: RED tests with fake transport**

Test: explicit `project_id` appears in every project-scoped read; secret redaction; duplicate command returns same execution; stale fence maps to `LeaseConflict`; content snapshot rejects unexpected project IDs from server response.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_cloud_supabase.py -q
```

- [ ] **Step 3: Implement client + response parsers**

Keep RPC names centralized constants so Worker/Python contracts can be compared by tests.

- [ ] **Step 4: Add cross-project defensive check**

Even though DB constraints/RLS are primary protection, Python parser must reject any returned row whose `project_id != requested_project_id`.

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_supabase.py tests/test_cloud_models.py tests/test_stage4_architecture.py -q
```

- [ ] **Step 6: Commit**

```bash
git add kaban/cloud/supabase.py kaban/cloud/config.py tests/test_cloud_supabase.py

git commit -m "feat: add supabase control store"
```

---

### Task 5: Implement immutable Cloudflare R2 artifact store

**Deliverable:** Durable media can be uploaded/downloaded safely without treating R2 as a filesystem.

**Files:**
- Modify: `requirements.txt` — add `boto3>=1.35,<2`
- Create: `kaban/cloud/r2.py`
- Test: `tests/test_cloud_r2.py`

**Interfaces:**

```python
class R2ArtifactStore:
    def object_key(self, project_id: str, content_set_id: UUID, revision_id: UUID, kind: str, logical_name: str) -> str: ...
    def put_immutable(self, project_id: str, key: str, source: Path, expected_sha256: str) -> StoredArtifact: ...
    def download(self, project_id: str, artifact: ArtifactRef, destination: Path) -> None: ...
    def delete_orphan(self, project_id: str, key: str) -> None: ...
```

Canonical key format:

```text
projects/{project_id}/content/{content_set_id}/revisions/{revision_id}/{artifact_kind}/{logical_name}
```

Rules enforced in code:

- normalized project ID/key must not contain traversal;
- key must begin exactly `projects/{project_id}/`;
- upload computes local SHA-256 first;
- existing key with same hash is idempotent success;
- existing key with different hash raises `ImmutableArtifactConflict`;
- after upload, HEAD verifies `ContentLength` and stored `sha256` metadata;
- download re-hashes bytes before returning success.

- [ ] **Step 1: RED tests with fake S3 client**

Cover immutable conflict, cross-project prefix rejection, upload verification, corrupt download, idempotent same-hash upload.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_cloud_r2.py -q
```

- [ ] **Step 3: Implement R2 store with dependency injection**

Constructor accepts optional `s3_client` so unit tests perform zero network calls.

- [ ] **Step 4: Add optional live R2 acceptance test**

Environment guard:

```text
KABAN_TEST_R2_ENDPOINT
KABAN_TEST_R2_BUCKET
KABAN_TEST_R2_ACCESS_KEY_ID
KABAN_TEST_R2_SECRET_ACCESS_KEY
```

Live test uploads one temporary object under `projects/test-stage4/...`, verifies hash/read-back, then deletes only that test object. If credentials are absent, test is explicitly SKIPPED.

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_r2.py -q
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt kaban/cloud/r2.py tests/test_cloud_r2.py

git commit -m "feat: add immutable r2 artifact store"
```

---

### Task 6: Build the generic Workspace Bridge and dynamic Project cloud adapter loader

**Deliverable:** KABAN can create an isolated execution workspace without knowing CAELUS paths or data fields.

**Files:**
- Create: `kaban/cloud/adapters.py`
- Create: `kaban/cloud/workspace.py`
- Modify: `kaban/projects.py` to parse optional `cloud.adapter` declarative config without requiring it in local-only Projects
- Test: `tests/test_cloud_workspace.py`
- Test: `tests/test_projects.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    project_root: Path
    generated: Path
    runtime: Path

@contextmanager
def execution_workspace(base_temp: Path, execution_id: UUID, project_id: str) -> Iterator[WorkspacePaths]: ...

class WorkspaceBridge:
    def prepare(self, command: ExecutionCommand, adapter: ProjectWorkspaceAdapter) -> PreparedWorkspace: ...
    def collect(self, prepared: PreparedWorkspace) -> ChangeSet: ...
```

`project.yaml` optional block:

```yaml
cloud:
  adapter: "projects.caelus.cloud_adapter:adapter"
```

- [ ] **Step 1: RED tests**

Create synthetic project adapter fixture. Assert workspace path is `<temp>/<execution_id>/<project_id>`, directories start empty, `KABAN_GENERATED_DIR`/`KABAN_RUNTIME_DIR` are scoped only for project execution, and cleanup removes workspace even after exception.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_cloud_workspace.py tests/test_projects.py -q
```

- [ ] **Step 3: Implement loader and workspace context**

Dynamic adapter loading must follow existing scheduler adapter pattern but use a distinct error type `CloudAdapterLoadError`.

- [ ] **Step 4: Add project-isolation test**

Prepare `alpha` and `beta` workspaces for two executions; assert no shared generated/runtime parent below each project path and no adapter import from another Project.

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_workspace.py tests/test_projects.py tests/test_scheduler_adapter.py -q
```

- [ ] **Step 6: Commit**

```bash
git add kaban/cloud/adapters.py kaban/cloud/workspace.py kaban/projects.py tests/test_cloud_workspace.py tests/test_projects.py

git commit -m "feat: add project workspace bridge"
```

---

### Task 7: Add the CAELUS Workspace Adapter without rewriting CAELUS business logic

**Deliverable:** Supabase/R2 state can be projected into the exact CAELUS filesystem layout required by current workflows and collected back into generic `ChangeSet` objects.

**Files:**
- Create: `projects/caelus/cloud_adapter.py`
- Modify: `projects/caelus/project.yaml` — add cloud adapter only; do not change timezone/jobs
- Test: `tests/test_caelus_cloud_adapter.py`
- Extend: `tests/test_caelus_production_schedule.py`

**CAELUS mapping:**

```text
content_key = YYYY-MM-DD:<language>
content resource = content:<content_set_id>
settings resource = settings:content_diversity
channel resource = channel:<channel_id>
```

Materialization:

```text
content revision payload -> generated/<date>/<language>/content.json
approval projection      -> generated/<date>/<language>/status.json
publication progress     -> generated/<date>/<language>/publication.json
history revisions        -> generated/<historical-date>/<language>/content.json
settings                 -> generated/_settings/content_diversity.json
execution projection     -> generated/_automation/caelus/<date>/<language>/run.json
PNG artifacts            -> generated/<date>/<language>/cards/*.png
```

Telegram JPEG is not materialized from R2; existing publication code may regenerate it locally from canonical PNG.

**Operation dispatch mapping:**

- `generate` → `run_generation_job(..., force=...)`
- `save` → `save_fields(...)`
- `regenerate_field` → `save_fields(...)` then `regenerate_field(...)`
- `regenerate_sign` → `save_fields(...)` then `regenerate_sign(...)`
- `regenerate_conflicts` → settings/save + `regenerate_conflicts(...)`
- `approve` → `approve(...)`
- `return_to_draft` → `return_to_draft(...)`
- `publish` → `run_publication_job(...)`

CAELUS-specific payload validation remains in this adapter/project workflow.

- [ ] **Step 1: RED materialization tests**

Construct a `CanonicalSnapshot` entirely in memory and verify exact filesystem projection including 90-day history subset and settings.

- [ ] **Step 2: RED collection tests**

Mutate workspace files as the existing CAELUS workflow would. Assert `collect()` returns:

- candidate content payload + new hash;
- approval event only when status changed;
- durable PNG `ArtifactCandidate`s;
- publication projection/events;
- settings changes;
- no Telegram JPEG durable candidate.

- [ ] **Step 3: Implement adapter**

The adapter may import any `projects.caelus.*` module. No corresponding import may be added to `kaban/`.

- [ ] **Step 4: Prove existing scheduler schedule is unchanged**

Test exact values remain:

```text
timezone Pacific/Auckland
generate_ru 0 6 * * *
publish_ru  0 8 * * *
only generate_ru + publish_ru enabled
```

- [ ] **Step 5: Focused CAELUS regression**

```bash
python -m pytest tests/test_caelus_cloud_adapter.py tests/test_core.py tests/test_diversity.py tests/test_automation.py tests/test_project_renderer.py tests/test_project_workflow_publication.py tests/test_caelus_production_schedule.py -q
```

- [ ] **Step 6: Commit**

```bash
git add projects/caelus/cloud_adapter.py projects/caelus/project.yaml tests/test_caelus_cloud_adapter.py tests/test_caelus_production_schedule.py

git commit -m "feat: add caelus cloud workspace adapter"
```

---

### Task 8: Implement one cloud execution lifecycle with leases, fencing and commit-last generation

**Deliverable:** A GitHub runner can safely execute one command end-to-end and cannot overwrite newer canonical state.

**Files:**
- Create: `kaban/cloud/runner.py`
- Create: `kaban/cloud/cli.py`
- Create: `cloud_runtime.py` thin wrapper
- Test: `tests/test_cloud_runner.py`

**Interfaces:**

```python
def run_execution(
    execution_id: UUID,
    *,
    store: ControlStore,
    artifacts: ArtifactStore,
    registry: ProjectRegistry,
    temp_root: Path,
    owner: str,
) -> ExecutionOutcome: ...
```

Lifecycle is fixed:

1. load execution;
2. `start_execution` atomically obtains execution fence or returns duplicate/no-op;
3. if `operation` is a registered `kaban.*` Core operation, execute the Core handler under the same lease/fence and skip Project adapter/workspace; otherwise load the Project cloud adapter;
4. load canonical snapshot for Project operations;
5. derive and claim resource lease when mutating;
6. create disposable workspace;
7. materialize + verify hashes;
8. set scoped `KABAN_GENERATED_DIR`, `KABAN_RUNTIME_DIR`, `KABAN_PERSISTENCE=cloud` for child/project execution;
9. execute existing Project logic;
10. collect changes;
11. upload immutable durable artifacts;
12. verify uploads;
13. atomic DB commit with expected version + execution/resource fence for Project mutations, or Core-operation commit through its dedicated RPC;
14. finish execution;
15. optional backup hook after successful mutation;
16. always clean workspace; release/expire leases safely.

Use a lease-renewal helper during long work; it must stop committing if renewal fails.

- [ ] **Step 1: RED duplicate-dispatch test**

Two calls with same `execution_id`: first owns lease; second receives duplicate/no-op and adapter `execute()` is called once.

- [ ] **Step 2: RED stale-runner test**

Fake store issues fence 1, then takeover fence 2 before commit. Fence-1 commit raises `LeaseConflict`; canonical revision pointer remains N.

- [ ] **Step 3: RED R2-success/DB-failure test**

Artifact upload succeeds, DB commit raises version/fence conflict. Assert current revision remains N and uploaded object is returned/recorded as orphan candidate; runner must not delete it immediately because a racing DB transaction may still establish a reference.

- [ ] **Step 4: Implement runner and CLI**

CLI:

```bash
python cloud_runtime.py execute --execution-id <uuid>
```

No project ID is accepted as an implicit override; project scope comes from canonical execution row.

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_runner.py tests/test_cloud_workspace.py tests/test_cloud_supabase.py tests/test_cloud_r2.py -q
```

- [ ] **Step 6: Full Python regression checkpoint**

```bash
python -m pytest -q
python -m unittest discover -s tests -p "test*.py"
```

- [ ] **Step 7: Commit**

```bash
git add kaban/cloud/runner.py kaban/cloud/cli.py cloud_runtime.py tests/test_cloud_runner.py

git commit -m "feat: execute cloud jobs in disposable workspace"
```

---

### Task 9: Project the Python scheduler into absolute cloud slots and add generic wait conditions

**Deliverable:** Worker does not implement cron/timezone/DST; cloud scheduler receives 35 days of absolute slots and blocked publication waits in DB without wasting Actions minutes.

**Files:**
- Modify: `kaban/scheduler/models.py` — extend `JobResult` with optional generic wait condition while preserving existing constructor compatibility
- Create: `kaban/cloud/schedule_projection.py`
- Create: `kaban/cloud/core_operations.py`
- Modify: `kaban/scheduler/cron.py`
- Modify: `projects/caelus/scheduler.py`
- Test: `tests/test_cloud_schedule_projection.py`
- Extend: `tests/test_scheduler_engine.py`, `tests/test_caelus_scheduler.py`

**Interface:**

```python
@dataclass(frozen=True)
class WaitCondition:
    kind: str
    project_id: str
    data: Mapping[str, Any]

@dataclass(frozen=True)
class JobResult:
    outcome: str
    message: str = ""
    retryable: bool = False
    wait_condition: WaitCondition | None = None
```

Projection:

```python
def project_schedule(
    registry: ProjectRegistry,
    store: ControlStore,
    *,
    from_utc: datetime,
    horizon_days: int = 35,
) -> ProjectionResult: ...
```

For each occurrence create an execution row with precomputed:

```text
slot_id = scheduled UTC ISO identity
scheduled_for
misfire_deadline_at
retry_deadline_at
operation
job_id
project_id
```

CAELUS publish blocked because approval is missing maps to:

```json
{
  "kind": "content_approved",
  "project_id": "caelus",
  "data": {"content_key": "YYYY-MM-DD:ru"}
}
```

- [ ] **Step 1: RED timezone/DST projection tests**

Use Pacific/Auckland dates across DST boundary. Compare projected UTC occurrences against the existing Python cron engine, not a second parser.

- [ ] **Step 2: RED idempotent projection test**

Running projection twice for same 35-day horizon must not create duplicate `(project_id, job_id, slot_id)` rows.

- [ ] **Step 3: RED blocked publish test**

CAELUS scheduler returns `JobResult(outcome="blocked", retryable=True, wait_condition=...)`; existing local SchedulerEngine still behaves as before using `retryable`, ignoring the optional new field.

- [ ] **Step 4: Implement projection and universal horizon-replenishment operation**

Add `cron_next(expression: str, after: datetime) -> datetime` to `kaban/scheduler/cron.py`, implemented with the same parser/matching semantics as the existing cron functions and covered by focused tests. `schedule_projection.py` uses this Python helper; do not port cron parsing to TypeScript.

Register the Core-owned operation `kaban.schedule_projection` in `kaban/cloud/core_operations.py`. It reads the execution project scope, projects slots from the existing `project.yaml` scheduler config, and is idempotent through the same `(project_id, job_id, slot_id)` uniqueness constraints. `kaban/cloud/runner.py` must route `kaban.*` operations to this Core registry before loading a Project workspace adapter.

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_schedule_projection.py tests/test_scheduler_engine.py tests/test_caelus_scheduler.py tests/test_scheduler_acceptance.py -q
```

- [ ] **Step 6: Commit**

```bash
git add kaban/scheduler/models.py kaban/cloud/schedule_projection.py projects/caelus/scheduler.py tests

git commit -m "feat: project scheduler slots for cloud runtime"
```

---

### Task 10: Add canonical publication checkpoints and safe ambiguous-delivery handling

**Deliverable:** Cloud publication checkpoints happen around each external Telegram side effect; `sent` is never automatically repeated and ambiguous network outcomes become `unknown_delivery`.

**Files:**
- Create: `kaban/cloud/publication.py`
- Modify: `kaban/publishing/telegram.py`
- Modify: `projects/caelus/publication.py`
- Test: `tests/test_cloud_publication.py`
- Extend: `tests/test_telegram_transport.py`, `tests/test_automation.py`

**Core interfaces:**

```python
class PublicationCheckpointClient(Protocol):
    def begin_step(self, publication_run_id: UUID, step_key: str, request_fingerprint: str) -> StepDecision: ...
    def mark_sent(self, publication_run_id: UUID, step_key: str, external_ids: Mapping[str, Any]) -> None: ...
    def mark_failed(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None: ...
    def mark_unknown(self, publication_run_id: UUID, step_key: str, error: Mapping[str, Any]) -> None: ...
```

Implement `LocalPublicationCheckpointClient` that preserves current file-journal semantics and `CloudPublicationCheckpointClient` backed by Supabase RPC.

Telegram transport must distinguish:

```text
DefinitiveTelegramError  -- HTTP/API response proves failure
AmbiguousTelegramError   -- timeout/URLError/socket failure after request began
```

CAELUS step keys remain Project-owned:

```text
media:1
media:2
text:1
text:2
```

Before send: canonical `pending -> sending`.
After confirmed success: `sending -> sent` with message IDs.
Definitive failure: `sending -> failed`.
Ambiguous failure or recovered stale `sending`: `unknown_delivery`; no automatic resend.

- [ ] **Step 1: RED sent-step resume test**

Materialize a publication run where `media:1=sent`; execute publish and assert Telegram client receives no call for album 1.

- [ ] **Step 2: RED ambiguous timeout test**

Fake transport raises `AmbiguousTelegramError` after `begin_step`. Assert checkpoint state becomes `unknown_delivery`, execution returns non-retryable/manual-reconciliation status, and a subsequent automatic execution does not invoke send for that step.

- [ ] **Step 3: RED stale fence test**

Cloud checkpoint RPC with stale execution/resource fence must reject `mark_sent`; old runner stops publication immediately.

- [ ] **Step 4: Implement transport exception taxonomy and checkpoint integration**

Do not remove current local `publication.json`/history. Cloud hook must be selected via runtime backend so local/Docker resumes continue to work.

- [ ] **Step 5: GREEN + publication regression**

```bash
python -m pytest tests/test_cloud_publication.py tests/test_telegram_transport.py tests/test_automation.py tests/test_project_workflow_publication.py -q
```

- [ ] **Step 6: Commit**

```bash
git add kaban/cloud/publication.py kaban/publishing/telegram.py projects/caelus/publication.py tests

git commit -m "feat: checkpoint cloud publication safely"
```

---

### Task 11: Implement the generic Cloudflare Worker control plane and Cron orchestrator

**Deliverable:** Authenticated Worker can read project-scoped state, create idempotent commands, expose execution status/artifacts, and dispatch only genuinely due work.

**Files:**
- Create: `deploy/cloudflare/worker/package.json`
- Create: `deploy/cloudflare/worker/tsconfig.json`
- Create: `deploy/cloudflare/worker/wrangler.jsonc`
- Create: `deploy/cloudflare/worker/src/types.ts`
- Create: `deploy/cloudflare/worker/src/auth.ts`
- Create: `deploy/cloudflare/worker/src/supabase.ts`
- Create: `deploy/cloudflare/worker/src/github.ts`
- Create: `deploy/cloudflare/worker/src/orchestrator.ts`
- Create: `deploy/cloudflare/worker/src/routes.ts`
- Create: `deploy/cloudflare/worker/src/index.ts`
- Create: `deploy/cloudflare/worker/test/*.test.ts`

**Worker environment/bindings:**

```text
SUPABASE_URL                 secret/config
SUPABASE_SERVICE_KEY         secret
GITHUB_OWNER                 config
GITHUB_REPO                  config
GITHUB_WORKFLOW_FILE         config
GITHUB_DISPATCH_TOKEN        secret
CF_ACCESS_TEAM_DOMAIN        config
CF_ACCESS_AUD                config
KABAN_PUBLIC_ORIGIN          config
ARTIFACTS                    R2 binding only if proxying via binding; credentials never go to browser
```

Cron:

```text
*/5 * * * *
```

Routes:

```text
GET  /healthz
GET  /api/projects
GET  /api/projects/:projectId/content/:contentKey
GET  /api/projects/:projectId/executions/:executionId
GET  /api/projects/:projectId/history
GET  /api/projects/:projectId/usage
GET  /api/projects/:projectId/artifacts/:artifactId
POST /api/projects/:projectId/commands
POST /api/projects/:projectId/publications/:runId/reconcile
```

Mutation envelope:

```json
{
  "execution_id": "client-generated UUID",
  "operation": "approve",
  "content_key": "2026-09-26:ru",
  "content_set_id": "uuid-or-null",
  "expected_version": 7,
  "payload": {},
  "idempotency_key": "uuid"
}
```

Worker fills `project_id` from route and `requested_by` from validated Cloudflare Access identity; it must ignore any conflicting project/actor supplied by browser body.

**Cron algorithm:**

1. record lightweight tick telemetry;
2. select bounded due candidates;
3. skip terminal/valid leased rows;
4. evaluate only generic wait conditions (`content_approved` initially);
5. mark expired misfire/retry windows;
6. if any enabled project/job has less than 7 days of projected future slots, create-or-get one idempotent `kaban.schedule_projection` maintenance execution for that project;
7. atomically claim runnable execution;
8. call GitHub `workflow_dispatch` with only `execution_id`;
9. on definitive GitHub error schedule dispatch retry;
10. on ambiguous dispatch leave same execution recoverable; duplicate Action is harmless because `start_execution` is atomic.

- [ ] **Step 1: Create package and RED tests**

Use Vitest. Tests must cover missing/invalid Access assertion, route project scope, origin/CSRF rejection, duplicate idempotency, waiting approval with zero GitHub dispatches, due approved execution with exactly one dispatch, horizon `< 7 days` creating exactly one idempotent schedule-projection maintenance execution, and GitHub timeout recovery.

- [ ] **Step 2: Run RED**

```bash
cd deploy/cloudflare/worker
npm ci
npm test
```

Expected: FAIL before implementation.

- [ ] **Step 3: Implement Access verification and same-origin mutation guard**

Validate Access JWT issuer/audience/signature using Cloudflare Access JWKS. `POST` requires exact allowed Origin and `application/json`; do not enable permissive CORS.

- [ ] **Step 4: Implement Supabase and GitHub clients**

Supabase service key stays Worker-side. GitHub fine-grained token is used only for repository workflow dispatch with Actions write permission.

- [ ] **Step 5: Implement orchestrator with bounded batch**

Initial batch limit: 10 due executions per Cron invocation. This is configuration, not a billing assumption.

- [ ] **Step 6: GREEN**

```bash
cd deploy/cloudflare/worker
npm test
npm run typecheck
```

- [ ] **Step 7: Commit**

```bash
git add deploy/cloudflare/worker

git commit -m "feat: add cloudflare control plane"
```

---

### Task 12: Build the CAELUS cloud Review Console as Project-owned frontend

**Deliverable:** Browser offers the existing CAELUS review operations without embedding CAELUS business logic in Worker/Core.

**Files:**
- Create: `projects/caelus/cloud_ui/manifest.json`
- Create: `projects/caelus/cloud_ui/index.html`
- Create: `projects/caelus/cloud_ui/app.js`
- Create: `projects/caelus/cloud_ui/styles.css`
- Create: `kaban/cloud/ui_packager.py`
- Modify: `deploy/cloudflare/worker/wrangler.jsonc` for static asset directory
- Test: `tests/test_cloud_ui_packager.py`
- Extend Worker tests for static route / API behavior

**UI responsibilities:**

- choose date/language;
- show current revision/version/approval/publication/automation state;
- render 12 CAELUS cards and fields;
- show uniqueness diagnostics from canonical payload/projection;
- Save Changes;
- Regenerate Field;
- Regenerate Sign;
- Regenerate Conflicts;
- Approve All;
- Return to Draft;
- Manual Generate;
- Manual Publish;
- execution progress/status;
- explicit `unknown_delivery` warning + reconciliation action;
- usage/health summary.

Every mutation obtains current `version`, creates one idempotency UUID, POSTs a command, then polls execution status. It never modifies Supabase directly.

`manifest.json` declares source ownership and route:

```json
{
  "project_id": "caelus",
  "entry": "index.html",
  "route": "/projects/caelus/"
}
```

- [ ] **Step 1: RED packager test**

Given project registry, packager copies only declared UI assets to a generated Worker assets directory under `deploy/cloudflare/worker/.generated-assets/projects/<project_id>/`; unknown traversal paths are rejected.

- [ ] **Step 2: Implement generic packager**

`kaban/cloud/ui_packager.py` knows project IDs/manifests only, not zodiac fields.

- [ ] **Step 3: Implement CAELUS UI source under Project**

Keep current CAELUS visual language, but do not copy server-side validation logic into JS. Display validation data returned by API/execution result.

- [ ] **Step 4: Add stale-tab UI test**

Worker/API fake returns HTTP 409 version conflict; UI must show “content changed; reload current revision” and must not automatically retry the mutation with a newer version.

- [ ] **Step 5: Add duplicate-click test**

Two submissions of the same in-flight action reuse the same generated `idempotency_key` until request resolves, so backend returns one execution.

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_cloud_ui_packager.py -q
cd deploy/cloudflare/worker && npm test && npm run typecheck
```

- [ ] **Step 7: Commit**

```bash
git add projects/caelus/cloud_ui kaban/cloud/ui_packager.py deploy/cloudflare/worker tests/test_cloud_ui_packager.py

git commit -m "feat: add caelus cloud review console"
```

---

### Task 13: Add GitHub Actions heavy execution workflow without polling waste

**Deliverable:** One workflow dispatch executes exactly one canonical execution ID; no ten-minute GitHub polling loop exists.

**Files:**
- Create: `.github/workflows/kaban-cloud-execution.yml`
- Create: `.github/workflows/kaban-cloud-ci.yml`
- Modify: `.gitignore` for generated Worker assets/local cloud temp files
- Test: `tests/test_github_workflow_contract.py`

**Execution workflow contract:**

```yaml
on:
  workflow_dispatch:
    inputs:
      execution_id:
        required: true
        type: string
```

Required runtime environment:

```text
KABAN_PERSISTENCE=cloud
KABAN_SUPABASE_URL
KABAN_SUPABASE_SERVICE_KEY
KABAN_R2_ENDPOINT_URL
KABAN_R2_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
OPENAI_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID (until channels receive secret mapping mechanism)
```

Workflow steps:

1. checkout;
2. setup Python 3.13;
3. pip install requirements;
4. validate `execution_id` UUID;
5. set owner identity from `${{ github.run_id }}:${{ github.run_attempt }}`;
6. run `python cloud_runtime.py execute --execution-id ...`;
7. no Actions artifact upload for canonical data;
8. no sleep/poll retry loop.

GitHub concurrency is secondary protection:

```text
group = kaban-execution-${{ inputs.execution_id }}
cancel-in-progress = false
```

- [ ] **Step 1: RED YAML contract test**

Assert workflow has only `workflow_dispatch`, requires `execution_id`, sets cloud mode, contains no `schedule:`, no `sleep 600`, no Action artifact persistence, and invokes only one execution.

- [ ] **Step 2: Implement workflow and CI**

CI runs Python tests and Worker unit/type tests on push/PR; it does not invoke OpenAI/Telegram/Supabase/R2 production services.

- [ ] **Step 3: GREEN**

```bash
python -m pytest tests/test_github_workflow_contract.py -q
```

- [ ] **Step 4: Commit**

```bash
git add .github .gitignore tests/test_github_workflow_contract.py

git commit -m "ci: add cloud execution workflow"
```

---

### Task 14: Implement restartable local→cloud migration and cloud→local export

**Deliverable:** Existing v1.13 data can be imported with dry-run/read-back verification and later exported for rollback without modifying source data.

**Files:**
- Create: `kaban/cloud/migration.py`
- Extend: `kaban/cloud/cli.py`
- Extend: `projects/caelus/cloud_adapter.py` with migration classification/project mapping methods
- Test: `tests/test_cloud_migration.py`

**CLI:**

```bash
python cloud_runtime.py migrate-local --project caelus --generated ./generated --runtime ./runtime --dry-run --manifest migration.json
python cloud_runtime.py migrate-local --project caelus --generated ./generated --runtime ./runtime --apply --manifest migration.json
python cloud_runtime.py verify-migration --manifest migration.json
python cloud_runtime.py export-local --project caelus --destination ./rollback-export
```

Dry-run is default; `--apply` must be explicit.

**Manifest record:**

```json
{
  "source_path": "generated/2026-09-26/ru/cards/caelus_aries.png",
  "source_size": 2312345,
  "source_sha256": "...",
  "classification": "durable_artifact",
  "destination_type": "r2",
  "destination_id_or_r2_key": "projects/caelus/...",
  "destination_sha256": "...",
  "result": "verified"
}
```

CAELUS mapping follows approved spec: content/status/PNG/publication/automation/diversity/scheduler history; Telegram JPEG is classified `regenerable_derivative` and explicitly recorded as not migrated. Local lock files are classified `local_lock_not_migrated`; never create cloud leases from them.

- [ ] **Step 1: RED dry-run immutability test**

Hash complete source fixture tree before/after dry-run; assert identical and no provider write methods called.

- [ ] **Step 2: RED restart/idempotency test**

Simulate successful R2 upload then DB failure, rerun importer with same `project_id + logical path + sha256`; assert no duplicate revision/artifact/event rows and existing same-hash object is accepted.

- [ ] **Step 3: RED unknown-file test**

Unknown file appears in manifest/report and prevents “clean migration” acceptance until explicitly classified; importer never silently drops it.

- [ ] **Step 4: Implement importer/exporter**

Export reconstructs a local CAELUS projection using the same Project Workspace Adapter, including current content/status/publication/settings/cards needed to run local/Docker after rollback.

- [ ] **Step 5: Read-back verification test**

Compare JSON canonical hashes and PNG byte hashes after materialize/export. Counts and hashes must match manifest.

- [ ] **Step 6: GREEN**

```bash
python -m pytest tests/test_cloud_migration.py tests/test_caelus_cloud_adapter.py tests/test_cloud_workspace.py -q
```

- [ ] **Step 7: Commit**

```bash
git add kaban/cloud/migration.py kaban/cloud/cli.py projects/caelus/cloud_adapter.py tests/test_cloud_migration.py

git commit -m "feat: add restartable cloud migration"
```

---

### Task 15: Add backups, observability, usage/cost monitoring and safe cleanup

**Deliverable:** Cloud runtime exposes recoverability/health/usage without auto-deleting canonical data or auto-upgrading plans.

**Files:**
- Create: `kaban/cloud/backup.py`
- Create: `kaban/cloud/usage.py`
- Extend: `kaban/cloud/runner.py` successful-mutation backup hook
- Extend: Worker `routes.ts`/`orchestrator.ts`
- Test: `tests/test_cloud_usage.py`
- Test: `tests/test_cloud_backup.py`
- Extend: Worker tests

**Backup:** after successful mutating execution, create compact JSON snapshot containing project config projection, current content pointers/revisions needed for recovery, approvals, settings, scheduler/execution/publication control state. Store immutable snapshot in R2 under:

```text
projects/{project_id}/backups/{YYYY}/{MM}/{execution_id}.json
```

Do not create a separate daily GitHub Action when no mutation occurred.

**Usage samples:**

- Supabase DB size: `provider_exact/db_exact` when obtained from PostgreSQL `pg_database_size`;
- egress: `provider_exact` only if provider API returns exact; otherwise `estimated`;
- R2 storage/object/operations: exact/provider metric when available;
- GitHub workflow durations/minutes: provider exact when API supplies it, otherwise derived estimate explicitly marked;
- Worker requests/errors.

Threshold status:

```python
def quota_level(used: float, limit: float) -> str:
    ratio = used / limit
    if ratio >= 0.95: return "critical"
    if ratio >= 0.85: return "high"
    if ratio >= 0.70: return "warning"
    return "ok"
```

Limits are configuration/provider metadata, not hard-coded eternal facts.

**Safe cleanup only:** expired leases via recovery RPC; orphan R2 objects only after grace period and only when DB has no reference; runner workspace; temporary Telegram derivatives. Never auto-delete content history, approved revisions, published artifacts, publication audit or migration manifests.

- [ ] **Step 1: RED backup-after-mutation test**

Successful mutating runner calls backup once after canonical commit; read-only/no-op execution does not create snapshot.

- [ ] **Step 2: RED quality-label test**

Estimated egress cannot be serialized/rendered as `provider_exact`.

- [ ] **Step 3: RED orphan cleanup race test**

Object discovered as orphan, then a DB reference appears before delete; cleanup re-checks and must not delete it.

- [ ] **Step 4: Implement backup, usage and cleanup services**

Worker health API includes:

```text
last cron tick
oldest due execution
stale leases
missed slots
schedule horizon days
unknown_delivery count
```

- [ ] **Step 5: GREEN**

```bash
python -m pytest tests/test_cloud_usage.py tests/test_cloud_backup.py tests/test_cloud_runner.py -q
cd deploy/cloudflare/worker && npm test && npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add kaban/cloud/backup.py kaban/cloud/usage.py kaban/cloud/runner.py deploy/cloudflare/worker tests

git commit -m "feat: add cloud recovery and usage monitoring"
```

---

### Task 16: Documentation, deployment sync, full acceptance and release gate

**Deliverable:** Stage 4 can be configured by a non-programmer using explicit commands, while local/Docker v1.13 remains usable and provider checks are truthfully separated into static/mock/live acceptance.

**Files:**
- Add approved spec: `docs/superpowers/specs/2026-09-26-kaban-stage4-serverless-runtime-design.md`
- Add this plan: `docs/superpowers/plans/2026-09-26-kaban-stage4-serverless-runtime.md`
- Create: `deploy/README_SERVERLESS_RU.md`
- Modify: `.env.example` with documented optional cloud variables and no real secrets
- Modify: `README_RU.md`, `ARCHITECTURE.md`, release notes
- Create: `tests/test_stage4_acceptance.py`
- Extend: `tests/test_docker_image_contract.py`, `tests/test_compose_contract.py`, `tests/test_deployment_acceptance.py`

**Deployment sync CLI:**

```bash
python cloud_runtime.py sync-config --project caelus --horizon-days 35
python cloud_runtime.py status --project caelus
python cloud_runtime.py health --project caelus
```

`sync-config` hashes `project.yaml`, upserts non-secret `kaban_projects`/`kaban_scheduler_jobs` mirror and projects 35-day absolute slots. It does not enable EN or change current schedule.

**Runbook must include exact setup order:**

1. create one Supabase Project;
2. apply SQL migrations in order;
3. create one private R2 bucket for environment;
4. configure Worker secrets/vars + Cloudflare Access application/policy;
5. configure GitHub repository secrets;
6. deploy Worker;
7. run local migration dry-run;
8. inspect unknown/unmapped report;
9. run migration apply;
10. verify migration hashes/read-back;
11. run `sync-config`;
12. perform cloud mock generation acceptance;
13. approve through cloud Review Console;
14. perform Telegram dry-run acceptance;
15. only then perform controlled real Telegram live acceptance if user explicitly authorizes it;
16. keep local source backup until cutover is accepted.

**Rollback commands:** document export current cloud state first after any cloud mutation, then start local/Docker on exported dataset. Do not imply Telegram posts are rolled back.

- [ ] **Step 1: RED acceptance invariants test**

Test all spec §47 invariants that are mechanically checkable. At minimum:

```text
kaban does not import CAELUS
synthetic second Project loads without CAELUS
local mode requires no cloud env
Docker compose still uses generated/runtime persistence
cloud JSON/state uses control-store interfaces
R2 keys are project-prefixed/immutable
stale fence commit rejected
scheduled slots unique
wait condition prevents blocked dispatch
approval exact revision/hash
sent step no resend
unknown delivery no auto retry
browser routes expose no provider secrets
migration source unchanged
export reconstructs local projection
usage quality exact vs estimated
```

- [ ] **Step 2: Run Python full regression**

```bash
python -m pytest -q
python -m unittest discover -s tests -p "test*.py"
```

Record exact counts in release notes; do not pre-state expected final count before tests exist.

- [ ] **Step 3: Run Worker regression**

```bash
cd deploy/cloudflare/worker
npm ci
npm test
npm run typecheck
```

Record exact counts/output.

- [ ] **Step 4: Re-run v1.13 compatibility acceptance**

Use existing mock workflows and compare the same 26 user-facing mock artifacts used in v1.12.1→v1.13 acceptance. Expected: byte-identical unless a specifically approved Stage 4 compatibility exception is documented; no such exception is currently planned.

- [ ] **Step 5: Run static secret/ZIP hygiene checks**

Verify `.env`, provider keys, service credentials, generated cloud build secrets and local runtime data are absent from release archive/image.

- [ ] **Step 6: Provider live acceptance — only when credentials/environment exist**

Run and report separately:

```text
Supabase migration/RPC live contract
R2 put/head/get/hash/delete test object
Cloudflare Worker /healthz + Access-protected route
Cron orchestration with mock execution
GitHub workflow_dispatch of a mock execution
cloud materialize → CAELUS mock generate → commit
Review Console command → execution → current revision
Telegram dry-run
controlled Telegram real send only with explicit user authorization
```

If any provider test is not run, release notes must say **NOT RUN** rather than PASS.

- [ ] **Step 7: Validate Free-tier behavior without hard-coding billing promises**

Confirm dashboard/API displays configured limits and 70/85/95 thresholds; confirm it never upgrades plan or deletes canonical content automatically.

- [ ] **Step 8: Final release commit/tag only after all required non-live tests pass**

```bash
git add .
git commit -m "feat: complete kaban serverless runtime stage4"
```

Tag/version must be chosen at release time after final regression, not guessed in advance by this plan.

---

# Execution Order and Stop Gates

Implementation must follow the tasks in order because later tasks consume interfaces established earlier.

Mandatory stop/review gates during execution:

1. **After Task 1:** prove local/Docker compatibility before provider code.
2. **After Task 3:** review SQL schema/RPCs before writing provider clients against them.
3. **After Task 8:** prove stale-runner/commit-last behavior before adding orchestration.
4. **After Task 10:** review publication safety because this is the highest irreversible-side-effect risk.
5. **After Task 14:** review migration dry-run and export behavior before any real cutover.
6. **After Task 16:** user reviews full acceptance report before cloud production cutover.

No live production mutation is implicit in implementing this plan.

# Environment/Secret Matrix

## Local/Docker

Required only as today:

```text
OPENAI_API_KEY             only for AI generation
TELEGRAM_BOT_TOKEN         only for real Telegram publication
TELEGRAM_CHAT_ID           only for real Telegram publication
KABAN_RUNTIME_DIR          optional
KABAN_GENERATED_DIR        optional; new Stage 4 capability
KABAN_PERSISTENCE=local    default
```

No Supabase/R2/GitHub/Cloudflare credentials required.

## GitHub runner cloud mode

```text
KABAN_PERSISTENCE=cloud
KABAN_SUPABASE_URL
KABAN_SUPABASE_SERVICE_KEY
KABAN_R2_ENDPOINT_URL
KABAN_R2_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
OPENAI_API_KEY             only operations that need AI
TELEGRAM_BOT_TOKEN         only publication
TELEGRAM_CHAT_ID           current CAELUS RU publication destination until channel-secret abstraction is implemented
```

## Cloudflare Worker

```text
SUPABASE_URL
SUPABASE_SERVICE_KEY
GITHUB_OWNER
GITHUB_REPO
GITHUB_WORKFLOW_FILE
GITHUB_DISPATCH_TOKEN
CF_ACCESS_TEAM_DOMAIN
CF_ACCESS_AUD
KABAN_PUBLIC_ORIGIN
R2 binding ARTIFACTS
```

Worker does not receive OpenAI or Telegram secrets.

# Verification Matrix

| Property | Unit/static | Integration/mock | Provider live |
|---|---:|---:|---:|
| local generated-root compatibility | yes | yes | n/a |
| Core→Project dependency direction | yes | n/a | n/a |
| DB composite FK/RLS/RPC semantics | static | disposable DB if available | Supabase acceptance |
| R2 immutability/hash | yes fake | yes fake | R2 acceptance |
| workspace isolation | yes | yes | GitHub Action acceptance |
| fence takeover | yes | yes | Supabase runner acceptance |
| schedule projection | yes | yes | Cron acceptance |
| blocked approval avoids Actions | Worker unit | mocked GitHub | Cron + GitHub acceptance |
| publication sent no-resend | yes | fake Telegram | controlled live only if authorized |
| ambiguous Telegram no auto retry | yes | fake network timeout | no destructive live test required |
| migration idempotency | yes | fixture tree | real dataset dry-run/apply |
| cloud→local rollback export | yes | fixture | production export before rollback |
| usage quality labels | yes | provider-response fixtures | provider APIs when available |
| secret isolation | static | workflow/Worker tests | deployment inspection |

# Self-Review Against Approved Spec

- Persistence boundary/local-first: Tasks 1–2.
- Supabase schema/project isolation/composite FKs/RLS: Task 3.
- Supabase backend: Task 4.
- R2 immutable object naming/hash rules: Task 5.
- Ephemeral workspace/materialize/sync: Tasks 6–8.
- CAELUS-specific projection remains in Project: Task 7.
- Commit-last generation, version CAS, execution/resource fences: Tasks 3 and 8.
- Scheduler projection, 35-day horizon, <7-day monitoring path, no TS cron parser: Tasks 9, 11, 15.
- Approval flow and stale version: Tasks 3, 7, 11, 12.
- Publication checkpoints/unknown delivery/no blind retry: Task 10.
- Conditional Cloudflare Cron→GitHub dispatch/minute minimization: Tasks 11 and 13.
- Worker Access auth/CSRF/secret separation: Tasks 11–12.
- Migration manifest/idempotency/source immutability/export rollback: Task 14.
- Application-level R2 backup: Task 15.
- Observability and usage 70/85/95 with exact/estimated quality: Task 15.
- Docker/local preservation and full existing regression: Tasks 1, 8 and 16.
- Live acceptance truthfulness: Task 16.
- No EN production, Instagram, auto-approve, unrelated refactor: Global Constraints and Task 16 acceptance.

No implementation code should be started until the user approves this plan.
