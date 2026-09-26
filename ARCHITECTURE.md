# KABAN Content Engine / CAELUS — Final Architecture

## 1. Главный инвариант

KABAN — общий движок и техническая инфраструктура. CAELUS — самостоятельный Project.

Направление зависимостей только такое:

```text
root entrypoints / Review Console
            │
            ▼
    projects/caelus/
            │
            ▼
         kaban/
```

Запрещённые зависимости:

```text
kaban/* -> projects/caelus/*
projects/caelus/* -> legacy caelus/*
projects/caelus/* -> legacy content_engine/*
Project A -> Project B
```

Legacy packages `caelus/` и `content_engine/` после Final Cleanup удалены из production tree.

## 2. Структура KABAN Core

```text
kaban/
├── projects.py          # registry/config Projects
├── assets.py            # разрешение Project assets
├── ai/
│   ├── openai.py        # project-agnostic structured JSON transport
│   └── usage.py         # token/cost accounting
├── storage/
│   └── __init__.py      # load_json/write_json/content_hash
└── publishing/
    └── telegram.py      # Telegram Bot API transport
```

KABAN Core не знает о знаках зодиака, 12 карточках, CAELUS prompts, схеме 6+6, Review approval или uniqueness-правилах CAELUS.

## 3. CAELUS Project

```text
projects/caelus/
├── project.yaml
├── config.py
├── storage.py
├── domain.py
├── schemas.py
├── prompts.py
├── provider.py
├── generator.py
├── renderer.py
├── validators.py
├── quality.py
├── workflow.py
├── telegram_builder.py
├── publication.py
├── uniqueness/
│   ├── history.py
│   ├── normalize.py
│   ├── rules.py
│   ├── service.py
│   ├── settings.py
│   └── similarity.py
└── assets/
    ├── background.png
    ├── logo_caelus.png
    ├── art_*.png
    └── glyph_*.png
```

Все знания о CAELUS находятся здесь: zodiac domain, prompts, schemas, validation, uniqueness, rendering, workflow и publication strategy.

## 4. Root entrypoints

Корневые файлы — пользовательские точки входа, а не бизнес-слой:

```text
admin_app.py             # HTTP/HTML Review Console
generate_forecasts.py    # CLI генерации
run_daily.py             # CLI полного daily workflow
generate_cards.py        # compatibility CLI -> projects.caelus.renderer
build_telegram.py        # compatibility CLI -> projects.caelus.telegram_builder
publish_telegram.py      # compatibility CLI -> projects.caelus.publication
```

Они импортируют Project API напрямую. В них не должно появляться дублирующей бизнес-логики.

## 5. Storage boundary

Общие primitives:

```text
kaban.storage
├── load_json
├── write_json
└── content_hash
```

CAELUS-specific layout:

```text
projects.caelus.storage
└── generated/YYYY-MM-DD/<language>/
    ├── content.json
    ├── status.json
    ├── publication.json
    ├── publication_history.json
    ├── cards/
    └── telegram/
```

Текущий layout сохранён специально: Final Cleanup не выполняет миграцию исторических datasets.

## 6. AI boundary

```text
projects/caelus/prompts.py
projects/caelus/schemas.py
projects/caelus/provider.py
projects/caelus/generator.py
             │
             ▼
kaban/ai/openai.py
             │
             ▼
          OpenAI API
```

`kaban.ai` принимает caller-supplied prompt/schema и не знает семантику результата.

## 7. Rendering boundary

```text
projects/caelus/renderer.py
        │
        ├── projects/caelus/assets/*
        └── generated/.../cards/*.png
```

Renderer implementation существует только внутри CAELUS Project. Корневой `generate_cards.py` оставлен как CLI compatibility entrypoint.

## 8. Validation / uniqueness

```text
projects/caelus/validators.py
projects/caelus/quality.py
projects/caelus/uniqueness/*
```

Эти модули CAELUS-specific, потому что работают с `SIGN_ORDER`, `CONTENT_FIELDS`, legacy aliases и правилами текстов гороскопа. Они не являются KABAN Core.

Профили Content Diversity и выбранное окно истории сохраняются в:

```text
generated/_settings/content_diversity.json
```

## 9. Publication boundary

```text
projects/caelus/publication.py
    ├── approval/content hash checks
    ├── 12 CAELUS cards
    ├── 6 + 6 albums
    ├── horoscope text batches
    ├── publication journal/resume
    └── Telegram media optimization
            │
            ▼
kaban/publishing/telegram.py
    └── generic Telegram Bot API transport
```

KABAN transport не знает publication strategy конкретного Project.

## 10. Workflow invariants

1. Новый dataset начинается как `draft`.
2. Изменение approved content сбрасывает approval в `draft`.
3. Approval сохраняет `content_hash`.
4. Publisher сверяет текущий hash с approved hash.
5. Dataset с validation errors нельзя approve/publish.
6. После начала частичной или успешной публикации content блокируется от редактирования.
7. Обычный UI workflow не публикует уже опубликованный dataset повторно.
8. `--force` остаётся явной CLI-операцией.
9. Точечная regeneration изменяет только выбранный sign/field и необходимые derived artifacts.
10. KABAN Core не импортирует CAELUS Project.

## 11. Publication state machine

```text
APPROVED
   │ publish
   ▼
PUBLISHING
   ├── all steps OK ───────────────► PUBLISHED
   ├── failure before any send ────► FAILED
   └── failure after >=1 step ─────► PARTIALLY PUBLISHED
                                         │ retry
                                         └────────► PUBLISHING
```

После каждого подтверждённого Telegram step progress сохраняется атомарно.

## 12. Статус миграции

KABAN migration для CAELUS завершён на v1.9 Final Cleanup.

Дальнейшие изменения должны быть продуктовой разработкой CAELUS. Новая функциональность сначала остаётся внутри `projects/caelus/`; перенос в KABAN Core допускается только после появления второго реального Project/use case, подтверждающего повторное использование.

## CAELUS Production Automation — Stage 1

Stage 1 добавляет проектный automation-layer поверх существующего CAELUS workflow. Бизнес-состояние не дублируется: оно выводится из `content.json`, `status.json` и `publication.json`.

```text
root operator CLI / Review Console
              ↓
projects.caelus.automation
              ↓
existing CAELUS workflow / publication
              ↓
KABAN Core services
```

Execution metadata хранится отдельно:

```text
generated/_automation/caelus/YYYY-MM-DD/<language>/run.json
```

Automation обеспечивает idempotency, per-operation lock, журнал попыток, безопасный retry, transactional rollback генерации и защиту от повторной публикации. `publication.json` остаётся авторитетным для Telegram progress/resume.

**Stage 1 не содержит scheduler/cron, background worker, auto-approve или auto-publish по времени.** Существующие `run_daily.py` и `publish_telegram.py` остаются прямыми ручными entrypoints.

## KABAN Project Scheduler — Stage 2

Scheduler — универсальный Core capability:

```text
projects/*/project.yaml
        │
        ▼
kaban.projects.ProjectRegistry
        │
        ▼
kaban.scheduler
├── config.py      # generic flattening Project jobs
├── models.py      # generic scheduler contracts
├── store.py       # runtime state + lease locks
├── adapter.py     # dynamic module:function loading
├── engine.py      # cron slots / misfires / retries / isolation
└── runner.py      # foreground polling loop
        │
        ▼
dynamic adapter
        │
        ▼
projects/<project_id>/scheduler.py
        │
        ▼
Project-specific automation/workflow
```

Инвариант: `kaban/**` не импортирует `projects.caelus` и не знает семантику handler names. `params` считаются opaque Project data и не сохраняются в scheduler state/log history.

Runtime scheduler-state находится отдельно от content storage:

```text
runtime/scheduler/<project_id>/<job_id>/state.json
```

CAELUS adapter делегирует `generate/publish` существующему `projects.caelus.automation`, поэтому scheduler не дублирует Stage 1 idempotency, approval или Telegram resume logic.

v1.12 поставлялся с отключённым CAELUS scheduler. В production profile v1.12.1 оператор явно подтвердил расписание Pacific/Auckland: RU generate 06:00, RU publish 08:00; EN jobs не включены.

## Stage 3: Production Deployment

Production topology:

```text
Internet / private network
        |
        v
     gateway
        |
        v
      admin -----------+
                       |
                       v
              data/generated
                       ^
                       |
    scheduler ----------+
        |
        v
   data/runtime
```

Deployment layer универсален для всех KABAN Projects. `scheduler` и `admin` используют один immutable image, но работают как независимые процессы и могут перезапускаться отдельно. Host-backed `data/generated` и `data/runtime` являются persistent state.

Архитектурный инвариант сохраняется: KABAN Core/deployment не импортирует `projects.caelus` напрямую. Project-specific business logic остаётся внутри `projects/<project_id>/`; deployment отвечает только за process runtime, storage mounts, health, gateway и container lifecycle.

## 15. Stage 4 — Serverless Runtime

Stage 4 добавляет provider-neutral cloud boundary внутри `kaban/cloud/`:

```text
Cloudflare Cron / Review Console
            │
            ▼
      KABAN control plane
            │
   ┌────────┴─────────┐
   ▼                  ▼
Supabase            GitHub Actions
canonical state       ephemeral workspace
                         │
                         ▼
                 projects/<project_id>/
                         │
                         ▼
                   existing workflow
                         │
                 ┌───────┴───────┐
                 ▼               ▼
             Supabase            R2
```

Архитектурные инварианты:

- `kaban/` не импортирует `projects.caelus`;
- Project-specific materialize/collect находится в `projects/<project_id>/cloud_adapter.py`;
- Supabase является canonical control state в cloud mode;
- R2 хранит immutable binary artifacts и backups, но не scheduler truth;
- GitHub workspace является временным и удаляется после execution;
- execution/resource leases используют fencing tokens;
- revision commit выполняется последним;
- publication checkpoint `unknown_delivery` требует manual reconciliation;
- Cloudflare Worker не получает OpenAI/Telegram secrets;
- local/Docker остаётся first-class runtime.
