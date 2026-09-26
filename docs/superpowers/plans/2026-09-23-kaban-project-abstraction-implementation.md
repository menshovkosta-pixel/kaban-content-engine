# KABAN Content Engine — Project Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the minimal KABAN project abstraction (`ProjectConfig` + `ProjectRegistry`) and register CAELUS as the first project without changing any existing CAELUS runtime, UI, generated-data, rendering, uniqueness or Telegram behaviour.

**Architecture:** Add a small generic `kaban` package that loads validated project definitions from `projects/*/project.yaml`. Keep `caelus/config.py` as a backward-compatible facade: existing callers continue importing the same functions, while their default values come from the active KABAN project. Step 1 intentionally does not move existing CAELUS workflow/storage/publication code or add generic pipeline/plugin abstractions.

**Tech Stack:** Python standard library (`dataclasses`, `pathlib`, `os`, `unittest`), PyYAML >= 6.0, existing CAELUS/content_engine codebase.

**Spec:** `docs/superpowers/specs/2026-09-23-kaban-project-abstraction-design.md`

## Global Constraints

- Source baseline is `CAELUS_generator_v4_11`; baseline regression suite is 31 tests and is currently passing.
- Product/platform name is **KABAN Content Engine**; **CAELUS** is the first Project.
- Existing CAELUS runtime behaviour must remain unchanged in Step 1.
- Active project defaults to `caelus` when `CONTENT_PROJECT_ID` is absent.
- Explicit unknown `CONTENT_PROJECT_ID` must fail fast; never silently fall back to CAELUS.
- Existing environment variables retain current names and precedence: `CAELUS_OPENAI_MODEL`, `CAELUS_OPENAI_REASONING_EFFORT`, `CONTENT_HISTORY_DAYS`, `CONTENT_UNIQUENESS_WARNING_THRESHOLD`, `CONTENT_UNIQUENESS_HARD_THRESHOLD`, `CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Credentials must never be stored in `project.yaml`.
- Existing generated-data layout remains `generated/YYYY-MM-DD/<language>/`; no data migration and no `project_id` directory in Step 1.
- Review Console HTML/routes, CLI command names/flags, card rendering, two-album Telegram behaviour, uniqueness algorithms and diversity profiles are out of scope.
- The root-level `caelus/` package is a transitional compatibility layer, not a template for future Projects.
- Add exactly one new runtime dependency: `PyYAML>=6.0`.
- This extracted source archive has no `.git` directory. Commit commands below should be run only when executing inside a real Git checkout; implementation in this workspace must not pretend a commit was created.

## File Structure

Create:

```text
kaban/
├── __init__.py          # public KABAN project-layer exports only
└── projects.py          # immutable project config, YAML loader, registry, active-project selection

projects/
└── caelus/
    └── project.yaml     # CAELUS v4.11 defaults, no secrets

tests/
└── test_projects.py     # project loading/registry/compatibility tests
```

Modify:

```text
requirements.txt         # add PyYAML>=6.0
caelus/config.py         # compatibility facade delegates defaults to active ProjectConfig
.env.example             # document optional CONTENT_PROJECT_ID=caelus
ARCHITECTURE.md           # document KABAN → CAELUS dependency seam and transitional layer
README_RU.md              # explain KABAN project layer without changing user workflow
```

Do not move or rename in Step 1:

```text
admin_app.py
caelus/workflow.py
caelus/storage.py
caelus/publication.py
content_engine/*
generate_cards.py
build_telegram.py
publish_telegram.py
generated/*
```

## Review Focus

1. **Malformed or semantically invalid project YAML** — startup/registry construction must fail with a message that identifies the offending file and never emits secrets; pinned in Task 1 validation tests.
2. **Duplicate project IDs in different folders** — registry must reject ambiguity deterministically; pinned in Task 2.
3. **Explicit unknown `CONTENT_PROJECT_ID`** — must fail rather than fall back to `caelus`; pinned in Task 2.
4. **Legacy environment overrides** — valid overrides must still win, while invalid reasoning effort/int/float values must retain existing validation behaviour; pinned in Task 3.
5. **Missing project directory or bad language/threshold relationships** — must fail at configuration boundary, before workflow execution; pinned in Tasks 1–2.

---

### Task 1: Add immutable ProjectConfig and load the CAELUS YAML definition

**Files:**
- Create: `kaban/__init__.py`
- Create: `kaban/projects.py`
- Create: `projects/caelus/project.yaml`
- Create: `tests/test_projects.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `ProjectConfig`, `AIConfig`, `UniquenessConfig`, `TelegramPublicationConfig`, `PublicationConfig`, `load_project_config(path: Path) -> ProjectConfig`
- Later tasks consume these exact types/functions from `kaban.projects`.

- [ ] **Step 1: Add PyYAML runtime dependency**

Append exactly this line to `requirements.txt`:

```text
PyYAML>=6.0
```

Do not change existing Pillow/OpenAI requirements.

- [ ] **Step 2: Write failing tests for loading the real CAELUS project and validating bad project definitions**

Create `tests/test_projects.py` with the first test class and helper:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from kaban.projects import ProjectConfigError, load_project_config


ROOT = Path(__file__).resolve().parent.parent


def valid_project_payload(**overrides):
    payload = {
        "id": "sample",
        "name": "Sample",
        "default_language": "ru",
        "supported_languages": ["ru", "en"],
        "timezone": "Pacific/Auckland",
        "ai": {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "low",
        },
        "uniqueness": {
            "history_days": 90,
            "warning_threshold": 0.76,
            "hard_threshold": 0.80,
            "max_regeneration_attempts": 3,
        },
        "publication": {
            "telegram": {
                "album_group_size": 6,
            }
        },
    }
    payload.update(overrides)
    return payload


def write_project(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class ProjectConfigTests(unittest.TestCase):
    def test_real_caelus_project_loads_with_v4_11_defaults(self):
        config = load_project_config(ROOT / "projects" / "caelus" / "project.yaml")
        self.assertEqual(config.id, "caelus")
        self.assertEqual(config.name, "CAELUS")
        self.assertEqual(config.default_language, "ru")
        self.assertEqual(config.supported_languages, ("ru", "en"))
        self.assertEqual(config.timezone, "Pacific/Auckland")
        self.assertEqual(config.ai.model, "gpt-5.6-luna")
        self.assertEqual(config.ai.reasoning_effort, "low")
        self.assertEqual(config.uniqueness.history_days, 90)
        self.assertEqual(config.uniqueness.warning_threshold, 0.76)
        self.assertEqual(config.uniqueness.hard_threshold, 0.80)
        self.assertEqual(config.uniqueness.max_regeneration_attempts, 3)
        self.assertEqual(config.publication.telegram.album_group_size, 6)

    def test_default_language_must_be_supported(self):
        payload = valid_project_payload(default_language="de")
        with tempfile.TemporaryDirectory() as td:
            path = write_project(Path(td) / "project.yaml", payload)
            with self.assertRaisesRegex(ProjectConfigError, "default_language"):
                load_project_config(path)

    def test_warning_threshold_must_not_exceed_hard_threshold(self):
        payload = valid_project_payload()
        payload["uniqueness"]["warning_threshold"] = 0.90
        payload["uniqueness"]["hard_threshold"] = 0.80
        with tempfile.TemporaryDirectory() as td:
            path = write_project(Path(td) / "project.yaml", payload)
            with self.assertRaisesRegex(ProjectConfigError, "warning_threshold"):
                load_project_config(path)

    def test_malformed_yaml_reports_source_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "project.yaml"
            path.write_text("id: [broken", encoding="utf-8")
            with self.assertRaises(ProjectConfigError) as raised:
                load_project_config(path)
            self.assertIn(str(path), str(raised.exception))
```

- [ ] **Step 3: Run the focused tests and verify they fail before implementation**

Run:

```bash
python -m unittest tests.test_projects.ProjectConfigTests -v
```

Expected: import failure because `kaban.projects` does not exist yet.

- [ ] **Step 4: Add the CAELUS project YAML with exact v4.11 defaults and no credentials**

Create `projects/caelus/project.yaml`:

```yaml
id: caelus
name: CAELUS
default_language: ru
supported_languages:
  - ru
  - en
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
```

Verify manually that the file contains none of: `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

- [ ] **Step 5: Implement immutable nested config objects and strict YAML loading**

Create `kaban/projects.py` with these public types and validation semantics:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ENGINE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = ENGINE_ROOT / "projects"
ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class ProjectConfigError(ValueError):
    """Ошибка чтения или валидации определения KABAN Project."""


@dataclass(frozen=True)
class AIConfig:
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class UniquenessConfig:
    history_days: int
    warning_threshold: float
    hard_threshold: float
    max_regeneration_attempts: int


@dataclass(frozen=True)
class TelegramPublicationConfig:
    album_group_size: int


@dataclass(frozen=True)
class PublicationConfig:
    telegram: TelegramPublicationConfig


@dataclass(frozen=True)
class ProjectConfig:
    id: str
    name: str
    default_language: str
    supported_languages: tuple[str, ...]
    timezone: str
    ai: AIConfig
    uniqueness: UniquenessConfig
    publication: PublicationConfig
```

Add small private helpers in the same file for mapping/string/int/threshold extraction. They must reject booleans where integers are expected, because `bool` is a subclass of `int` in Python.

Implement `load_project_config(path: Path) -> ProjectConfig` using `yaml.safe_load()`. Requirements:

```text
- top-level YAML value must be a mapping;
- id/name/default_language/timezone/model must be non-empty strings;
- supported_languages must be a non-empty list of non-empty strings, converted to tuple;
- default_language must occur in supported_languages;
- reasoning_effort must be in ALLOWED_REASONING_EFFORTS;
- history_days >= 1;
- warning_threshold and hard_threshold are numeric in (0, 1];
- warning_threshold <= hard_threshold;
- max_regeneration_attempts >= 0;
- album_group_size >= 1;
- YAML parser errors and semantic errors are wrapped/raised as ProjectConfigError and include the path.
```

Use this pattern around parse/validation errors so failures identify the source without dumping environment state:

```python
def load_project_config(path: Path) -> ProjectConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        # parse + validate raw
        ...
    except ProjectConfigError:
        raise
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ProjectConfigError(f"Не удалось загрузить Project config {path}: {exc}") from exc
```

Semantic validation errors raised inside parsing must also include the path, e.g. `Project config <path>: default_language ...`.

- [ ] **Step 6: Export only the stable project-layer surface from `kaban/__init__.py`**

Create:

```python
from .projects import ProjectConfig, ProjectConfigError, load_project_config

__all__ = ["ProjectConfig", "ProjectConfigError", "load_project_config"]
```

Do not expose internal parsing helpers.

- [ ] **Step 7: Run focused tests and verify Task 1 passes**

Run:

```bash
python -m unittest tests.test_projects.ProjectConfigTests -v
```

Expected: all `ProjectConfigTests` PASS.

- [ ] **Step 8: Commit when a Git checkout is available**

```bash
git add requirements.txt kaban projects/caelus/project.yaml tests/test_projects.py
git commit -m "feat: add KABAN project config model"
```

If `git rev-parse --is-inside-work-tree` fails, record that this archive has no repository metadata and do not claim a commit.

---

### Task 2: Add ProjectRegistry discovery and active-project selection

**Files:**
- Modify: `kaban/projects.py`
- Modify: `kaban/__init__.py`
- Modify: `tests/test_projects.py`

**Interfaces:**
- Consumes: `ProjectConfig`, `ProjectConfigError`, `load_project_config(path)` from Task 1.
- Produces: `ProjectRegistry(projects_dir: Path = PROJECTS_ROOT)`, `ProjectRegistry.get(project_id)`, `ProjectRegistry.active()`, `ProjectRegistry.registered()`, `project_registry()`, `active_project()`.
- `caelus/config.py` in Task 3 will consume `active_project()`.

- [ ] **Step 1: Write failing registry tests**

Append to `tests/test_projects.py`:

```python
import os
from unittest.mock import patch

from kaban.projects import ProjectRegistry


class ProjectRegistryTests(unittest.TestCase):
    def test_default_active_project_is_caelus_when_env_is_absent(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTENT_PROJECT_ID", None)
            self.assertEqual(registry.active().id, "caelus")

    def test_explicit_caelus_project_resolves(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {"CONTENT_PROJECT_ID": "caelus"}, clear=False):
            self.assertEqual(registry.active().name, "CAELUS")

    def test_explicit_unknown_project_fails_without_fallback(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {"CONTENT_PROJECT_ID": "does-not-exist"}, clear=False):
            with self.assertRaisesRegex(ProjectConfigError, "does-not-exist"):
                registry.active()

    def test_duplicate_project_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_project(root / "one" / "project.yaml", valid_project_payload(id="duplicate", name="One"))
            write_project(root / "two" / "project.yaml", valid_project_payload(id="duplicate", name="Two"))
            with self.assertRaisesRegex(ProjectConfigError, "duplicate"):
                ProjectRegistry(root)

    def test_missing_projects_directory_fails_fast(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing"
            with self.assertRaisesRegex(ProjectConfigError, "missing"):
                ProjectRegistry(missing)

    def test_registered_projects_are_deterministic_by_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_project(root / "z" / "project.yaml", valid_project_payload(id="zeta", name="Zeta"))
            write_project(root / "a" / "project.yaml", valid_project_payload(id="alpha", name="Alpha"))
            registry = ProjectRegistry(root)
            self.assertEqual(tuple(item.id for item in registry.registered()), ("alpha", "zeta"))
```

- [ ] **Step 2: Run registry tests and verify they fail**

Run:

```bash
python -m unittest tests.test_projects.ProjectRegistryTests -v
```

Expected: FAIL because `ProjectRegistry` is not implemented/exported yet.

- [ ] **Step 3: Implement ProjectRegistry in `kaban/projects.py`**

Add:

```python
import os


class ProjectRegistry:
    def __init__(self, projects_dir: Path = PROJECTS_ROOT):
        self.projects_dir = Path(projects_dir)
        self._projects = self._discover()

    def _discover(self) -> dict[str, ProjectConfig]:
        if not self.projects_dir.is_dir():
            raise ProjectConfigError(f"KABAN projects directory not found: {self.projects_dir}")

        discovered: dict[str, ProjectConfig] = {}
        config_paths = sorted(self.projects_dir.glob("*/project.yaml"))
        if not config_paths:
            raise ProjectConfigError(f"No KABAN projects found in: {self.projects_dir}")

        for path in config_paths:
            config = load_project_config(path)
            if config.id in discovered:
                raise ProjectConfigError(
                    f"Duplicate KABAN project id '{config.id}' while loading {path}"
                )
            discovered[config.id] = config
        return discovered

    def get(self, project_id: str) -> ProjectConfig:
        project_id = str(project_id).strip()
        try:
            return self._projects[project_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._projects))
            raise ProjectConfigError(
                f"Unknown KABAN project id '{project_id}'. Registered projects: {known}"
            ) from exc

    def active(self) -> ProjectConfig:
        return self.get(os.getenv("CONTENT_PROJECT_ID", "caelus"))

    def registered(self) -> tuple[ProjectConfig, ...]:
        return tuple(self._projects[key] for key in sorted(self._projects))
```

Add a lazy process-wide registry to avoid re-reading YAML on every legacy config call while still reading `CONTENT_PROJECT_ID` dynamically:

```python
_DEFAULT_REGISTRY: ProjectRegistry | None = None


def project_registry() -> ProjectRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ProjectRegistry(PROJECTS_ROOT)
    return _DEFAULT_REGISTRY


def active_project() -> ProjectConfig:
    return project_registry().active()
```

Do not read OpenAI/Telegram credentials here.

- [ ] **Step 4: Export registry APIs from `kaban/__init__.py`**

Update exports to include:

```python
from .projects import (
    ProjectConfig,
    ProjectConfigError,
    ProjectRegistry,
    active_project,
    load_project_config,
    project_registry,
)

__all__ = [
    "ProjectConfig",
    "ProjectConfigError",
    "ProjectRegistry",
    "active_project",
    "load_project_config",
    "project_registry",
]
```

- [ ] **Step 5: Run all project-layer tests**

Run:

```bash
python -m unittest tests.test_projects -v
```

Expected: all Task 1 + Task 2 project tests PASS.

- [ ] **Step 6: Commit when a Git checkout is available**

```bash
git add kaban tests/test_projects.py
git commit -m "feat: add KABAN project registry"
```

If `.git` is absent, do not fabricate a commit.

---

### Task 3: Make `caelus.config` a compatibility facade over the active KABAN Project

**Files:**
- Modify: `caelus/config.py`
- Modify: `tests/test_projects.py`

**Interfaces:**
- Consumes: `active_project() -> ProjectConfig` from Task 2.
- Preserves these exact legacy call points: `ROOT`, `load_dotenv()`, `openai_model()`, `openai_reasoning_effort()`, `openai_config_message()`, `telegram_config_message()`, `uniqueness_history_days()`, `uniqueness_warning_threshold()`, `uniqueness_hard_threshold()`, `uniqueness_max_regeneration_attempts()`.
- No existing caller changes in this task.

- [ ] **Step 1: Write compatibility tests for default values and legacy environment precedence**

Append imports/tests to `tests/test_projects.py`:

```python
from caelus.config import (
    openai_model,
    openai_reasoning_effort,
    uniqueness_hard_threshold,
    uniqueness_history_days,
    uniqueness_max_regeneration_attempts,
    uniqueness_warning_threshold,
)


class LegacyConfigCompatibilityTests(unittest.TestCase):
    LEGACY_ENV_KEYS = (
        "CONTENT_PROJECT_ID",
        "CAELUS_OPENAI_MODEL",
        "CAELUS_OPENAI_REASONING_EFFORT",
        "CONTENT_HISTORY_DAYS",
        "CONTENT_UNIQUENESS_WARNING_THRESHOLD",
        "CONTENT_UNIQUENESS_HARD_THRESHOLD",
        "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS",
    )

    def _clear_legacy_env(self):
        for key in self.LEGACY_ENV_KEYS:
            os.environ.pop(key, None)

    def test_legacy_config_defaults_come_from_caelus_project(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_legacy_env()
            self.assertEqual(openai_model(), "gpt-5.6-luna")
            self.assertEqual(openai_reasoning_effort(), "low")
            self.assertEqual(uniqueness_history_days(), 90)
            self.assertEqual(uniqueness_warning_threshold(), 0.76)
            self.assertEqual(uniqueness_hard_threshold(), 0.80)
            self.assertEqual(uniqueness_max_regeneration_attempts(), 3)

    def test_existing_environment_variables_still_override_project_defaults(self):
        overrides = {
            "CONTENT_PROJECT_ID": "caelus",
            "CAELUS_OPENAI_MODEL": "override-model",
            "CAELUS_OPENAI_REASONING_EFFORT": "medium",
            "CONTENT_HISTORY_DAYS": "180",
            "CONTENT_UNIQUENESS_WARNING_THRESHOLD": "0.71",
            "CONTENT_UNIQUENESS_HARD_THRESHOLD": "0.83",
            "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS": "5",
        }
        with patch.dict(os.environ, overrides, clear=False):
            self.assertEqual(openai_model(), "override-model")
            self.assertEqual(openai_reasoning_effort(), "medium")
            self.assertEqual(uniqueness_history_days(), 180)
            self.assertEqual(uniqueness_warning_threshold(), 0.71)
            self.assertEqual(uniqueness_hard_threshold(), 0.83)
            self.assertEqual(uniqueness_max_regeneration_attempts(), 5)

    def test_invalid_reasoning_effort_keeps_legacy_validation(self):
        with patch.dict(os.environ, {"CAELUS_OPENAI_REASONING_EFFORT": "invalid"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CAELUS_OPENAI_REASONING_EFFORT"):
                openai_reasoning_effort()

    def test_invalid_legacy_numeric_overrides_keep_legacy_validation(self):
        with patch.dict(os.environ, {"CONTENT_HISTORY_DAYS": "not-int"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CONTENT_HISTORY_DAYS"):
                uniqueness_history_days()
        with patch.dict(os.environ, {"CONTENT_UNIQUENESS_HARD_THRESHOLD": "1.5"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CONTENT_UNIQUENESS_HARD_THRESHOLD"):
                uniqueness_hard_threshold()
```

- [ ] **Step 2: Run compatibility tests and verify at least the project-default coupling is not yet implemented**

Run:

```bash
python -m unittest tests.test_projects.LegacyConfigCompatibilityTests -v
```

Before modifying `caelus/config.py`, the numeric/default values happen to match v4.11 literals, so some tests may already pass. Treat this as a characterization test: inspect code and confirm defaults are still Python literals rather than `active_project()` values before proceeding.

- [ ] **Step 3: Replace only default literals in `caelus/config.py` with active-project defaults**

Add:

```python
from kaban.projects import active_project
```

Preserve `ROOT`, `.env` parsing and credential-message functions exactly.

Change `openai_model()` to:

```python
def openai_model() -> str:
    return os.getenv("CAELUS_OPENAI_MODEL", active_project().ai.model)
```

Change `openai_reasoning_effort()` to use the project default but keep the same legacy env validation/error message:

```python
def openai_reasoning_effort() -> str:
    value = os.getenv(
        "CAELUS_OPENAI_REASONING_EFFORT",
        active_project().ai.reasoning_effort,
    ).strip().lower()
    allowed = {"none", "low", "medium", "high", "xhigh", "max"}
    if value not in allowed:
        raise ValueError(
            "CAELUS_OPENAI_REASONING_EFFORT должен быть одним из: " + ", ".join(sorted(allowed))
        )
    return value
```

Change uniqueness functions to:

```python
def uniqueness_history_days() -> int:
    return _env_int(
        "CONTENT_HISTORY_DAYS",
        active_project().uniqueness.history_days,
        1,
    )


def uniqueness_warning_threshold() -> float:
    return _env_float(
        "CONTENT_UNIQUENESS_WARNING_THRESHOLD",
        active_project().uniqueness.warning_threshold,
    )


def uniqueness_hard_threshold() -> float:
    return _env_float(
        "CONTENT_UNIQUENESS_HARD_THRESHOLD",
        active_project().uniqueness.hard_threshold,
    )


def uniqueness_max_regeneration_attempts() -> int:
    return _env_int(
        "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS",
        active_project().uniqueness.max_regeneration_attempts,
        0,
    )
```

Do not modify `_env_int()` or `_env_float()` semantics in Step 1.

- [ ] **Step 4: Run compatibility and registry tests**

Run:

```bash
python -m unittest tests.test_projects -v
```

Expected: PASS.

- [ ] **Step 5: Prove legacy callers remain untouched**

Run:

```bash
grep -R "from caelus.config\|from \.config" -n --include='*.py' admin_app.py caelus run_daily.py generate_forecasts.py publish_telegram.py
```

Expected: existing import sites are still present; Step 1 must not rewrite them to import `kaban.projects` directly.

- [ ] **Step 6: Commit when a Git checkout is available**

```bash
git add caelus/config.py tests/test_projects.py
git commit -m "refactor: source CAELUS defaults from KABAN project"
```

If `.git` is absent, do not fabricate a commit.

---

### Task 4: Document the KABAN project seam without changing the user workflow

**Files:**
- Modify: `.env.example`
- Modify: `ARCHITECTURE.md`
- Modify: `README_RU.md`

**Interfaces:**
- Documentation only; no runtime API changes.

- [ ] **Step 1: Document the optional active-project selector in `.env.example`**

Insert near the top, before API credentials:

```text
# KABAN Content Engine. Пока зарегистрирован только CAELUS.
# Если переменная не задана, используется проект caelus.
CONTENT_PROJECT_ID=caelus
```

Keep all existing secret placeholders and legacy CAELUS/OpenAI/uniqueness variables unchanged.

- [ ] **Step 2: Update `ARCHITECTURE.md` title and top-level diagram**

Change the title to:

```markdown
# KABAN Content Engine / CAELUS v4.11 baseline — Architecture Notes
```

Add a `Project abstraction` section before the existing CAELUS application boundaries:

```text
KABAN Project Registry
        │
        ▼
Project: CAELUS
        │
        ▼
caelus/config.py compatibility facade
        │
        ▼
existing CAELUS workflow
```

Document explicitly:

```text
- `kaban/projects.py` knows project identity/config only.
- `projects/caelus/project.yaml` contains non-secret CAELUS defaults.
- `caelus/` remains transitional in Step 1.
- workflow/storage/publication are not genericised yet.
- future Projects depend on KABAN Core, never on CAELUS.
```

Do not rewrite existing state-machine/uniqueness/API-usage documentation except where naming context needs one sentence of clarification.

- [ ] **Step 3: Update the README architecture section**

Prepend to `## Прикладная архитектура` a concise explanation:

```text
KABAN Content Engine теперь содержит минимальный Project layer. CAELUS зарегистрирован как первый Project через `projects/caelus/project.yaml`. Текущий Review Console и ежедневный CAELUS workflow не изменены; `caelus/config.py` временно служит compatibility facade.
```

Show the structure:

```text
kaban/
└── projects.py
projects/
└── caelus/
    └── project.yaml
caelus/
├── config.py       # временный compatibility facade
├── storage.py
├── workflow.py
└── publication.py
```

State clearly that `storage.py` and `publication.py` are **not** intended to be copied for every future project; later extractions will be driven by real reuse after CAELUS is production-stable.

- [ ] **Step 4: Run a textual secret/config sanity check**

Run:

```bash
grep -R "OPENAI_API_KEY\|TELEGRAM_BOT_TOKEN\|TELEGRAM_CHAT_ID" -n projects kaban
```

Expected: no secret/config credential names appear in `projects/caelus/project.yaml`; if generic code mentions secret names unexpectedly, review before proceeding.

- [ ] **Step 5: Commit when a Git checkout is available**

```bash
git add .env.example ARCHITECTURE.md README_RU.md
git commit -m "docs: describe KABAN project abstraction"
```

If `.git` is absent, do not fabricate a commit.

---

### Task 5: Run full regression/offline verification and package Step 1 as KABAN

**Files:**
- No source changes expected unless verification reveals a regression.
- Create release artifact outside the source tree after all checks pass.

**Interfaces:**
- Verifies all previous tasks together.

- [ ] **Step 1: Run the complete unit-test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected:

```text
- all original 31 v4.11 tests PASS;
- all new `test_projects.py` tests PASS;
- zero failures/errors.
```

Do not claim PASS from test count alone; inspect the command exit status and final summary.

- [ ] **Step 2: Run the offline/mock daily generation path on a disposable date**

Use a date that is not a real production day:

```bash
python run_daily.py --date 2099-12-31 --language ru --mock
```

Expected:

```text
- command exits 0;
- generated/2099-12-31/ru/content.json exists;
- generated/2099-12-31/ru/cards/ contains 12 card images;
- generated/2099-12-31/ru/telegram/ contains Telegram artifacts;
- no external OpenAI call is required.
```

After verification, remove only the disposable `generated/2099-12-31` directory created by this test. Do not delete any other generated data.

- [ ] **Step 3: Verify project selection failure is early and clear**

Run:

```bash
CONTENT_PROJECT_ID=does-not-exist python -c "from caelus.config import openai_model; print(openai_model())"
```

On Windows PowerShell use:

```powershell
$env:CONTENT_PROJECT_ID='does-not-exist'; python -c "from caelus.config import openai_model; print(openai_model())"; Remove-Item Env:CONTENT_PROJECT_ID
```

Expected: non-zero exit and a clear `Unknown KABAN project id 'does-not-exist'` error. It must not print API keys/tokens and must not silently return the CAELUS model.

- [ ] **Step 4: Verify the default project keeps current behaviour**

Run:

```bash
python -c "from kaban.projects import active_project; from caelus.config import openai_model; print(active_project().id, openai_model())"
```

Expected:

```text
caelus gpt-5.6-luna
```

- [ ] **Step 5: Verify there was no Step-1 storage migration**

Run:

```bash
find generated -maxdepth 3 -type f 2>/dev/null | head -20
```

Expected: existing paths remain date/language based; no new `generated/caelus/...` hierarchy is introduced.

On Windows, equivalent inspection may be done with:

```powershell
Get-ChildItem generated -Recurse -File | Select-Object -First 20 FullName
```

- [ ] **Step 6: Build the release archive using KABAN naming**

From the directory containing the project folder, produce an archive named:

```text
KABAN_Content_Engine_Project_Abstraction_v1.zip
```

The archive must exclude:

```text
.env
.venv/
__pycache__/
*.pyc
generated/2099-12-31/
```

It may include existing non-secret generated/ assets only if they were already part of the source baseline; never add a live `.env`.

- [ ] **Step 7: Final verification report**

Record exactly:

```text
- baseline: CAELUS_generator_v4_11
- KABAN step: Project Abstraction
- active registered project: caelus
- unit-test result: <actual count>, PASS/FAIL
- offline mock result: PASS/FAIL
- storage migration: none
- Review Console changes: none
- Telegram behaviour changes: none
- archive: KABAN_Content_Engine_Project_Abstraction_v1.zip
```

Only mark a line PASS after its command actually completed successfully.

- [ ] **Step 8: Commit final documentation/verification metadata when a Git checkout is available**

If implementation required any verification-driven source/document changes, commit them explicitly. Otherwise no synthetic “empty” commit is required.

---

## Self-Review

### Spec coverage

- `ProjectConfig` model: Task 1.
- YAML `projects/caelus/project.yaml`: Task 1.
- Project discovery/default/explicit selection/duplicate handling: Task 2.
- Backward-compatible `caelus.config` and env precedence: Task 3.
- No secret storage: Tasks 1 and 4.
- No generated-data migration / no UI or CLI rewrite: Global Constraints + Task 5 verification.
- Documentation of transitional `caelus/` layer and future dependency direction: Task 4.
- Complete regression and mock generation: Task 5.
- KABAN archive naming: Task 5.

### Placeholder scan

No `TBD`, `TODO`, “implement later”, unspecified validation, or undefined neighboring interfaces remain in this plan.

### Type consistency

- `ProjectConfig` and nested dataclass field names are consistent across Tasks 1–3.
- `ProjectRegistry.active()` returns `ProjectConfig`.
- `active_project()` returns `ProjectConfig` and is the only new dependency of `caelus/config.py`.
- Legacy config function names/signatures remain unchanged.

### Scope check

The plan implements only Project Abstraction. It does not implement generic ContentItem, pipeline, storage, publisher, renderer, scheduler, Review Console project switching, or a second Project.
