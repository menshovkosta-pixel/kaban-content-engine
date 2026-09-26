# KABAN Content Engine — Architecture Step 1: Project Abstraction

Date: 2026-09-23
Source baseline: `CAELUS_generator_v4_11`
Scope: architectural refactor only; runtime behaviour must remain unchanged.

Product name: **KABAN Content Engine**.

Naming model for future steps:

- **Project** = independently managed content product/brand, e.g. `caelus`, `ai_radar`, `tender_radar`.
- **Channel** = publication destination belonging to a project, e.g. `CAELUS / Telegram RU` or `AI Radar / X`.
- Step 1 implements **Project** identity/configuration only. Generic Channel management is intentionally deferred to the Publisher/Review Console stages.
- The existing CAELUS UI may continue displaying CAELUS branding in Step 1; KABAN branding of the shared administration shell is a later UI step.
- Release/archive naming should switch away from `CAELUS_generator_*` to `KABAN_Content_Engine_*`, while the source baseline remains v4.11 for traceability.

## 1. Goal

Turn the existing CAELUS application into the first registered project of KABAN Content Engine, a future multi-project/multi-channel content platform, without changing the current CAELUS user workflow.

After this step KABAN must have an explicit `ProjectConfig` and `ProjectRegistry`, and the active project must be `caelus` by default. Existing CAELUS entry points, generated-data layout, Review Console behaviour, OpenAI settings, uniqueness rules, rendering and Telegram publication must continue to work exactly as before.

This step intentionally does **not** generalise content schemas, storage layout, renderers, publishers, pipelines or Review Console UI. Those are later architecture steps.

## 2. Current architecture observed in v4.11

The existing repository already has useful boundaries:

- `content_engine/*` owns generation, provider abstraction, prompts, validation, usage and uniqueness.
- `caelus/workflow.py` orchestrates the application use cases.
- `caelus/storage.py` owns generated-data paths and JSON persistence.
- `caelus/publication.py` invokes the Telegram publisher.
- `admin_app.py` is the HTTP/HTML Review Console.
- `caelus/config.py` is currently the main configuration facade, but its defaults and environment variable names are CAELUS-specific.

The main coupling relevant to Step 1 is that scripts and workflow functions import configuration directly from `caelus.config`, while no first-class concept of a project exists.

## 3. Success criteria

Step 1 is complete only when all of the following are true:

1. A reusable `ProjectConfig` model exists.
2. A `ProjectRegistry` can load project definitions from `projects/*/project.yaml`.
3. `projects/caelus/project.yaml` describes the current CAELUS defaults.
4. `caelus.config` remains a backward-compatible facade and delegates default values to the active project config.
5. Existing environment variables keep their current precedence and names, including:
   - `CAELUS_OPENAI_MODEL`
   - `CAELUS_OPENAI_REASONING_EFFORT`
   - `CONTENT_HISTORY_DAYS`
   - `CONTENT_UNIQUENESS_WARNING_THRESHOLD`
   - `CONTENT_UNIQUENESS_HARD_THRESHOLD`
   - `CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS`
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
6. With no new environment variables, active project is `caelus` and all current defaults are identical to v4.11.
7. Existing generated data stays under `generated/YYYY-MM-DD/<language>/`; there is no migration and no `project_id` directory yet.
8. Existing CLI commands and flags remain valid.
9. Review Console HTML and routes do not change as part of this step.
10. Existing tests pass and new tests prove project loading, default selection and environment override compatibility.

## 4. Approaches considered

### Approach A — compatibility facade over a new project layer (selected)

Add the small generic `kaban` package for project configuration and registry, then keep `caelus/config.py` as the compatibility boundary. Existing application code continues importing the same functions while those functions obtain their defaults from the active project.

Advantages:
- lowest regression risk;
- no large import rewrite;
- no data migration;
- establishes the correct future KABAN seam;
- allows later steps to migrate one subsystem at a time.

Trade-off:
- `caelus.config` temporarily remains in the dependency graph as a compatibility adapter.

### Approach B — replace all CAELUS config imports immediately

Change `admin_app.py`, workflow, publication and CLI scripts to consume `ProjectConfig` directly.

Advantages:
- cleaner end state sooner.

Disadvantages:
- unnecessarily large blast radius for Step 1;
- mixes introduction of the abstraction with migration of every consumer;
- higher probability of changing runtime behaviour.

Rejected for Step 1.

### Approach C — duplicate CAELUS as a plugin now

Create a full plugin system with generic pipeline, renderer, publisher and storage contracts immediately.

Advantages:
- closest to the long-term KABAN Content Engine architecture.

Disadvantages:
- over-scoped;
- would combine Architecture Steps 1–7;
- high risk of breaking the already working v4.11 application.

Rejected. The project abstraction should be introduced before pluginisation.


## 4.1 Architectural invariants for KABAN

KABAN is intentionally built as an **evolutionary platform**, not as a fully generic plugin framework designed in advance. The following rules are architectural invariants for all later steps:

1. Every Project depends only on KABAN Core public interfaces and its own project-local code.
2. Projects do not depend on other Projects. In particular, future `tender_radar`, `ai_radar` or `remote_jobs` code must never import from the CAELUS project.
3. A Project may use only the Core capabilities it needs; unused capabilities are optional and must not be mandatory pipeline stages.
4. A Project may add project-specific workflows, services, pipeline steps, collectors, renderers, prompts and publication strategies without modifying unrelated Projects.
5. Project-specific functionality remains inside that Project until a second real Project demonstrates that the same capability is reusable. Only then is extraction into Core considered.
6. Core must not become a collection of every feature ever needed by any Project. It contains only stable cross-project platform services and extension contracts.
7. Shared state and shared services must eventually be project-scoped with a stable `project_id` boundary so one Project cannot read, overwrite or publish another Project's data by accident. The storage migration needed to enforce this physically is deferred beyond Step 1.
8. The transitional root-level `caelus/` package is not a template for future Projects. It exists only to preserve v4.11 compatibility during migration. Its generic responsibilities will be extracted into KABAN Core in later steps, while CAELUS-specific responsibilities move under `projects/caelus/`.

The intended dependency direction is therefore:

```text
             KABAN CORE
          ↑      ↑      ↑
          │      │      │
      CAELUS   AI RADAR  TENDER RADAR
```

Never:

```text
AI RADAR → CAELUS
TENDER RADAR → CAELUS
CAELUS → TENDER RADAR
```

## 5. Proposed structure

```text
KABAN_Content_Engine/
├── kaban/
│   ├── __init__.py
│   └── projects.py
├── projects/
│   └── caelus/
│       └── project.yaml
├── caelus/
│   ├── config.py          # compatibility facade
│   ├── workflow.py
│   ├── storage.py
│   └── publication.py
└── tests/
    └── test_projects.py
```

No existing application file is moved during this step.

## 6. ProjectConfig

`ProjectConfig` is an immutable validated configuration object. Step 1 only models values needed to represent current CAELUS defaults and future identity.

Required conceptual fields:

```text
id
name
default_language
supported_languages
timezone
ai.model
ai.reasoning_effort
uniqueness.history_days
uniqueness.warning_threshold
uniqueness.hard_threshold
uniqueness.max_regeneration_attempts
publication.telegram.album_group_size
```

`publication.telegram.album_group_size` is configuration metadata in Step 1 only. Existing Telegram code remains unchanged; wiring publication layout is a later Publisher abstraction step.

Validation rules:

- project id is non-empty and stable;
- `default_language` belongs to `supported_languages`;
- at least one supported language exists;
- uniqueness days >= 1;
- similarity thresholds are in `(0, 1]`;
- warning threshold <= hard threshold;
- regeneration attempts >= 0;
- Telegram album group size >= 1;
- reasoning effort is one of the values already supported by v4.11.

## 7. ProjectRegistry

`ProjectRegistry` owns discovery and selection of project definitions.

Responsibilities:

- discover `projects/*/project.yaml`;
- parse and validate each definition;
- reject duplicate project IDs;
- provide `get(project_id)`;
- provide `active()`;
- expose a deterministic list of registered projects.

Active project selection:

```text
CONTENT_PROJECT_ID environment variable
        ↓
if absent: "caelus"
```

Unknown project IDs must fail fast with a clear error. There is no silent fallback when the user explicitly supplies an invalid project ID.

The registry must not know anything about zodiac signs, Telegram tokens, OpenAI transport, generated data or Review Console routes.

## 8. Configuration precedence

To preserve v4.11 behaviour, current environment variables stay authoritative.

Example for OpenAI model:

```text
CAELUS_OPENAI_MODEL env
        ↓ if absent
active_project.ai.model
```

Example for uniqueness hard threshold:

```text
CONTENT_UNIQUENESS_HARD_THRESHOLD env
        ↓ if absent
active_project.uniqueness.hard_threshold
```

The `.env` loading behaviour remains unchanged.

No credentials are stored in `project.yaml`. In particular the following remain environment-only secrets/configuration:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 9. CAELUS project definition

The initial project definition must encode v4.11 defaults, including:

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

Timezone is project metadata in this step. Existing `tomorrow_iso()` continues to use the machine calendar so v4.11 behaviour is not silently changed. Timezone-aware scheduling belongs to a later Scheduler step.

## 10. Backward-compatible `caelus.config`

`caelus/config.py` remains import-compatible for all current callers.

The following public names must remain available:

- `ROOT`
- `load_dotenv()`
- `openai_model()`
- `openai_reasoning_effort()`
- `openai_config_message()`
- `telegram_config_message()`
- `uniqueness_history_days()`
- `uniqueness_warning_threshold()`
- `uniqueness_hard_threshold()`
- `uniqueness_max_regeneration_attempts()`

Internally, default values move from Python literals to the active project's configuration.

This preserves current imports in:

- `admin_app.py`
- `caelus/workflow.py`
- `caelus/publication.py`
- `run_daily.py`
- `generate_forecasts.py`
- `publish_telegram.py`

No consumer migration is required in Step 1.

## 11. YAML dependency decision

Use `PyYAML>=6.0` and add it to `requirements.txt`.

Reason: the agreed long-term project format is `project.yaml`; introducing a real parser is safer than maintaining a custom YAML subset. The dependency is small, mature and only used at configuration load time.

If YAML loading fails because the file is invalid, startup should fail with an error identifying the project file and validation problem.

## 12. Data flow after Step 1

```text
startup / command
      ↓
load .env
      ↓
ProjectRegistry
      ↓
CONTENT_PROJECT_ID or "caelus"
      ↓
ProjectConfig
      ↓
caelus.config compatibility facade
      ↓
existing workflow unchanged
      ↓
content_engine / renderer / Telegram
```

There is deliberately no project-specific storage routing yet.

## 13. Error handling

The new layer must fail fast for configuration errors:

- projects directory missing;
- project YAML malformed;
- project ID missing;
- duplicate project ID;
- unsupported default language;
- invalid threshold/range;
- explicitly selected unknown project.

Error messages must not print secrets or the full environment.

Existing user-facing errors for missing OpenAI and Telegram credentials remain unchanged.

## 14. Testing strategy

### New project-layer tests

Add `tests/test_projects.py` covering at least:

1. CAELUS project loads successfully.
2. Default active project is `caelus` when `CONTENT_PROJECT_ID` is absent.
3. Explicit `CONTENT_PROJECT_ID=caelus` resolves correctly.
4. Unknown explicit project fails clearly.
5. Duplicate IDs are rejected.
6. Invalid project YAML/config is rejected.
7. `default_language` must belong to supported languages.
8. uniqueness thresholds are validated.

### Compatibility tests

Verify that with project config and no override environment variables:

- `openai_model()` == `gpt-5.6-luna`;
- `openai_reasoning_effort()` == `low`;
- history days == 90;
- warning threshold == 0.76;
- hard threshold == 0.80;
- max regeneration attempts == 3.

Verify existing environment variables override those defaults exactly as before.

### Regression suite

Run the complete existing test suite, including the current core and diversity tests.

Run the existing offline/mock path to ensure a dataset, cards and Telegram artifacts are still built without external API access.

## 15. Explicit non-goals for Step 1

Do not do any of the following in this step:

- introduce `ContentItem`/`ContentBatch` generic schemas;
- add `project_id` to existing generated content;
- move or migrate `generated/` data;
- change `generated/YYYY-MM-DD/<language>` layout;
- make zodiac schemas generic;
- move prompts out of `content_engine/prompts.py`;
- genericise `generate_cards.py`;
- genericise Telegram publisher;
- change two-album behaviour;
- change Review Console UI or routes;
- add scheduler behaviour;
- change `tomorrow_iso()` semantics;
- add AI Radar or another second project;
- change uniqueness algorithms or active diversity profiles.

These belong to later architecture steps.

## 16. Migration and rollback

There is no data migration.

Rollback is simple: restore the previous `caelus/config.py`, remove the new `kaban` project layer/project YAML/tests, and remove PyYAML from requirements. Existing generated datasets remain untouched throughout.

## 17. Definition of Done

Architecture Step 1 is done when:

- `ProjectConfig` and `ProjectRegistry` are implemented and tested;
- CAELUS configuration is represented by `projects/caelus/project.yaml`;
- legacy config APIs behave exactly as v4.11 unless an existing environment override is supplied;
- all existing tests pass;
- new project/config tests pass;
- offline/mock generation path passes;
- no generated content or publication behaviour changes;
- documentation describes the new project abstraction;
- a release archive is produced only after verification.
## 18. Product evolution after Step 1

The implementation sequence after Project Abstraction is deliberately CAELUS-first:

### Phase A — establish the KABAN seam

- implement Step 1 (`ProjectConfig`, `ProjectRegistry`, `project_id` identity);
- preserve CAELUS behaviour 1:1;
- switch release naming to KABAN while CAELUS remains the only Project.

### Phase B — finish CAELUS as the first production Project

Before creating a second Project, continue developing CAELUS under KABAN until it is production-ready. This includes functional completion, regression testing, publication reliability, uniqueness/history behaviour, Review Console usability, observability needed for real operation, and a controlled production launch.

KABAN may extract an obviously generic service during this phase (for example a Telegram transport or shared storage service), but only when that extraction directly improves CAELUS reliability or creates a stable platform boundary. Do not build speculative abstractions solely for hypothetical future Projects.

### Phase C — run CAELUS in production

Operate CAELUS long enough to validate real schedules, retries, content review, publishing, storage/history, failure recovery and day-to-day administration. Production feedback is part of KABAN architecture discovery.

### Phase D — build the second real Project

Only after CAELUS is stable in production, create the second Project (candidate: Tender Radar). Build it as an independent Project that depends on KABAN Core, not as a copy of CAELUS. Compare the two real use cases and extract only genuinely shared functionality into Core.

### Phase E — expand the project family

After the second Project validates the extension model, add further Projects such as AI Radar, Remote Jobs or Teacher Lab. New abstractions are introduced from demonstrated reuse rather than prediction.

This sequence is the governing roadmap unless later evidence from production justifies changing it.

