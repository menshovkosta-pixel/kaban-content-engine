# KABAN Content Engine — Stage 4 Design Spec

## KABAN Serverless Runtime

**Дата:** 2026-09-26
**Статус:** APPROVED
**Основа:** `KABAN_Content_Engine_Production_Deployment_Stage3_v1_13(1).zip`
**Версия исходной системы:** v1.13
**Цель документа:** архитектурный дизайн Stage 4. Этот документ **не является implementation plan** и **не разрешает реализацию** до отдельного подтверждения.

---

## 1. Цель Stage 4

Сделать KABAN способным работать в четырёх режимах без дублирования Project business logic:

1. local;
2. Docker/VPS;
3. GitHub Actions;
4. Cloudflare + Supabase + R2.

Ключевой принцип Stage 4:

> **Cloud runtime не переписывает CAELUS. Он материализует проверенный локальный контракт CAELUS во временный filesystem workspace, выполняет существующий Project workflow и синхронизирует каноническое состояние обратно в Supabase/R2.**

Stage 4 должен сохранить архитектурную зависимость:

```text
root entrypoints
    ↓
projects/<project_id>
    ↓
kaban
```

Запрещено:

```text
kaban
    ↓
projects/caelus
```

Все универсальные механизмы — KABAN Core.
Всё, что знает о знаках зодиака, 12 карточках, CAELUS-полях, Telegram-пакетах 6+6 и CAELUS UI, — `projects/caelus/`.

---

# 2. Проверка исходного v1.13

Перед проектированием Stage 4 был проверен приложенный архив.

## 2.1 Что проверено реально

SHA-256 архива:

```text
5fa83d2817545c713ce4ab10a9a19fbde54e55f758a053e4a143d0056f711691
```

Он совпал с контрольной суммой, указанной для стабильного v1.13.

Запущено на распакованной копии:

```text
python -m pytest -q
210 passed
```

Запущено отдельно:

```text
python -m unittest discover -s tests -q
Ran 92 tests
OK
```

## 2.2 Что в этом аудите не запускалось

- Docker live smoke;
- реальная публикация Telegram;
- реальные OpenAI-вызовы;
- реальные Supabase/Cloudflare/GitHub интеграции, потому что Stage 4 ещё не реализован.

Исходный ZIP не изменялся. В архиве нет `.git`, поэтому design spec не может быть корректно закоммичен в историю этого архива на текущем этапе.

---

# 3. Аудит filesystem-зависимостей v1.13

## 3.1 KABAN Core

### `kaban/storage/__init__.py`

Сейчас `kaban.storage` — это не persistence abstraction, а локальные helpers:

- `load_json(Path)`;
- `write_json(Path, payload)`;
- atomic file replace;
- `content_hash()`.

API принимает `Path`, поэтому backend фактически всегда filesystem.

### `kaban/scheduler/store.py`

Scheduler state хранится в:

```text
runtime/scheduler/<project_id>/<job_id>/state.json
runtime/scheduler/<project_id>/<job_id>/job.lock
```

Locks реализованы через exclusive file create и lease timestamp.

### `kaban/scheduler/health.py`

Heartbeat также хранится в `runtime/`.

### `kaban/projects.py`

`project.yaml` открывается из filesystem. Это нормально: исходный код и declarative project configuration остаются частью Git repository и в cloud runtime.

### `kaban/publishing/telegram.py`

Transport принимает локальные `Path` изображений. Это тоже допустимо: в GitHub Action изображения будут материализованы во временный workspace.

---

## 3.2 CAELUS

### `projects/caelus/storage.py`

Сейчас жёстко определено:

```text
ROOT/generated
```

Через этот каталог идут content/status/publication/history.

### `projects/caelus/workflow.py`

Прямая зависимость от `generated/` используется для:

- `content.json`;
- `status.json`;
- diversity settings;
- uniqueness history scan;
- generation/rebuild;
- editing;
- approval;
- monthly usage scan.

### `projects/caelus/uniqueness/history.py`

История уникальности строится glob-поиском:

```text
generated/*/<language>/content.json
```

### `projects/caelus/automation_store.py`

Automation journal и operation locks живут в:

```text
generated/_automation/caelus/<date>/<language>/run.json
generated/_automation/caelus/<date>/<language>/*.lock
```

### `projects/caelus/automation.py`

Локальная «транзакционная генерация» работает так:

1. копирует существующий day directory через `copytree()`;
2. запускает generation;
3. при ошибке удаляет новый directory через `rmtree()`;
4. восстанавливает backup через `copytree()`.

Это корректная локальная модель, но она не является cloud transaction model.

### `projects/caelus/publication.py`

Файл отдельно определяет собственный:

```text
GENERATED = ROOT / "generated"
```

Publication journal обновляется в `publication.json` после отдельных Telegram sends.

Это наиболее критичная точка для cloud runtime: локальный journal не может быть единственным canonical checkpoint после внешнего side effect.

### `admin_app.py`

Текущий Review Console:

- напрямую импортирует CAELUS modules;
- напрямую читает `generated/`;
- напрямую отдаёт PNG из `generated/`;
- напрямую запускает CAELUS mutations.

Он остаётся подходящим для local/Docker, но не должен переноситься в Cloudflare Worker «как есть».

---

# 4. Главный архитектурный вывод

Просто заменить `load_json()` на Supabase недостаточно.

Причины:

1. существующий CAELUS ожидает локальную directory structure;
2. Pillow/rendering и Telegram transport работают с `Path`;
3. uniqueness history читает прошлые локальные content files;
4. generation transaction основана на directory rollback;
5. publication journal имеет side-effect checkpoint semantics;
6. Review Console сейчас совмещает UI, storage и execution;
7. scheduler locks/state должны быть конкурентно безопасными между независимыми serverless invocations.

Поэтому Stage 4 требует **двухуровневой модели**:

```text
Canonical persistence
    Supabase PostgreSQL + Cloudflare R2
                ↓ ↑
Generic KABAN Workspace Bridge
                ↓ ↑
Temporary filesystem workspace
                ↓
Existing Project business logic
```

---

# 5. Рассмотренные варианты

## Вариант A — полный repository refactor CAELUS

Каждая CAELUS business function перестаёт работать с файлами и начинает напрямую работать через repositories.

### Плюсы

- архитектурно чистый cloud-native код;
- меньше временного filesystem;
- прямые транзакции для JSON-state.

### Минусы

- затрагивается почти весь уже проверенный CAELUS workflow;
- Pillow/Telegram всё равно потребуют временные файлы;
- высокий regression risk;
- сложно сохранить byte-identical local behavior;
- значительная часть Stage 4 превратится в переписывание CAELUS.

### Решение

**Не выбирать для Stage 4.** Возможно как будущий осознанный refactor, если workspace bridge когда-нибудь станет реальным bottleneck.

---

## Вариант B — canonical Supabase/R2 + ephemeral workspace bridge

Supabase/R2 являются cloud source of truth. GitHub Action временно материализует нужный Project/day в привычную файловую структуру, после чего запускает существующий Project workflow.

### Плюсы

- минимальное вмешательство в CAELUS business logic;
- local/Docker остаются рабочими;
- Pillow и Telegram получают обычные `Path`;
- cloud concurrency решается в PostgreSQL;
- можно мигрировать постепенно;
- подходит другим Projects через project adapter;
- даёт возможность сохранить текущие golden regression tests.

### Минусы

- нужен materialize/sync слой;
- есть две формы одного состояния: canonical DB и временная filesystem projection;
- публикации требуют специального cloud checkpoint hook, а не только sync в конце Action.

### Решение

**РЕКОМЕНДУЕМЫЙ ВАРИАНТ.**

---

## Вариант C — зеркалировать весь `generated/`/`runtime/` в R2

R2 становится почти сетевой файловой системой: каждый JSON и lock хранится объектом.

### Плюсы

- минимальные изменения CAELUS на первом шаге;
- понятное соответствие «локальный файл → объект».

### Минусы

- R2 не является транзакционной БД;
- object locks неудобны и рискованны;
- queries/history/status становятся дорогими и сложными;
- слабая модель concurrency;
- approval/scheduler/publication state трудно атомарно обновлять;
- project isolation зависит от строковых prefix, а не от relational constraints.

### Решение

**Не выбирать.**

---

# 6. Выбранная архитектура

```text
                         ┌─────────────────────────┐
                         │      Git repository     │
                         │ code / project.yaml /   │
                         │ templates/source assets │
                         └────────────┬────────────┘
                                      │ checkout
                                      ▼
┌──────────────────┐      ┌─────────────────────────┐
│ Cloudflare Access│─────▶│ Worker + Review Console │
└──────────────────┘      │ API / Cron Orchestrator │
                          └───────┬─────────┬───────┘
                                  │         │ workflow_dispatch
                         state/API│         ▼
                                  │   ┌─────────────────────┐
                                  │   │   GitHub Actions    │
                                  │   │ heavy Python jobs   │
                                  │   └─────────┬───────────┘
                                  │             │
                                  ▼             ▼
                         ┌─────────────────────────┐
                         │  Supabase PostgreSQL    │
                         │ canonical control/data  │
                         └────────────┬────────────┘
                                      │ metadata/pointers
                                      ▼
                         ┌─────────────────────────┐
                         │   Cloudflare R2         │
                         │ immutable media/artifact│
                         └─────────────────────────┘

GitHub Action:
Supabase/R2
    ↓ materialize
$RUNNER_TEMP/kaban/<execution_id>/generated
    ↓
existing projects/<project_id> workflow
    ↓ collect + verify
R2 upload
    ↓
atomic Supabase commit
```

---

# 7. Источники истины

Stage 4 вводит явное разделение ownership.

| Данные | Source of truth |
|---|---|
| Python code | Git |
| `project.yaml` | Git |
| CAELUS rendering templates/source static assets, уже находящиеся в repository | Git на Stage 4 |
| Project registration/config deployment mirror | Supabase |
| Content payload/history | Supabase |
| Current revision pointer | Supabase |
| Approval state/events | Supabase |
| Scheduler slot/execution state | Supabase |
| Publication state/checkpoints | Supabase |
| Automation history | Supabase |
| Generated PNG | R2 |
| Other durable generated binaries | R2 |
| Telegram JPEG derivatives | temporary workspace by default; не long-term canonical object |
| Local/Docker `generated/` | canonical только в local backend |
| Local/Docker `runtime/` | canonical только в local backend |

Важно: Stage 4 **не переносит source templates/assets из Git в R2 без необходимости**. Это уменьшает scope и сохраняет deterministic rendering. R2 становится canonical для generated artifacts. Возможность вынести крупные source assets в R2 позже остаётся.

---

# 8. Persistence boundary в KABAN

## 8.1 Core отвечает за универсальные механизмы

В KABAN Core должны находиться концепции:

- canonical persistence contracts;
- local backend;
- Supabase backend;
- R2 artifact store;
- workspace lifecycle;
- materialization engine;
- sync/commit engine;
- execution leases;
- fencing tokens;
- scheduler slot persistence;
- publication checkpoint infrastructure;
- usage monitoring;
- generic command/execution envelope;
- provider-independent error sanitization.

Core не должен знать:

- `aries`;
- 12 zodiac signs;
- CAELUS card JSON fields;
- CAELUS uniqueness formulas;
- Telegram groups 6+6;
- CAELUS text batch formatting.

## 8.2 Project adapter отвечает за projection

Каждый Project может предоставить cloud/workspace adapter через dynamic reference, аналогично scheduler adapter.

Задачи adapter:

- определить Project content key для operation;
- сказать, какие canonical records нужны workspace;
- разложить их в Project-specific local layout;
- определить необходимые history records;
- описать project-specific artifacts;
- собрать изменившееся состояние после выполнения;
- преобразовать local Project journal в canonical generic records, где это необходимо.

Для CAELUS это будет знать о:

```text
generated/<date>/<language>/content.json
generated/<date>/<language>/status.json
generated/<date>/<language>/publication.json
generated/_settings/...
generated/_automation/...
```

KABAN Workspace Bridge эти имена знать не должен.

---

# 9. Local backend остаётся first-class

Stage 4 не превращает cloud в обязательную зависимость.

По умолчанию:

```text
KABAN_PERSISTENCE=local
```

или отсутствие переменной означает local behavior.

Local/Docker должны продолжать:

- использовать filesystem;
- поддерживать существующие entrypoints;
- использовать host-mounted `generated/` и `runtime/` в Docker;
- не требовать Supabase/R2/Cloudflare/GitHub credentials;
- сохранять существующую CAELUS semantics.

Для serverless runtime выбирается:

```text
KABAN_PERSISTENCE=cloud
```

Cloud runtime получает временные directory roots, например:

```text
KABAN_GENERATED_DIR=<ephemeral>/generated
KABAN_RUNTIME_DIR=<ephemeral>/runtime
```

`KABAN_GENERATED_DIR` — новая универсальная capability, которой сейчас в v1.13 нет.

Все CAELUS filesystem references должны в итоге получать generated root через один Project-level resolver, а не через несколько независимых `ROOT / "generated"`.

Это targeted compatibility change, а не переписывание workflow.

---

# 10. Supabase data model

Один Supabase Project используется для всех KABAN Projects.

## 10.1 Базовое правило `project_id`

Каждая domain/runtime таблица, кроме технических schema migrations, содержит:

```text
project_id NOT NULL
```

Требования:

1. API Core всегда требует явный `project_id`;
2. cloud code не имеет понятия implicit `active_project` для persistence operations;
3. cross-project foreign keys запрещаются composite constraints;
4. browser не получает service-role credential;
5. RLS включается на всех таблицах;
6. direct anonymous access к данным по умолчанию запрещён.

---

## 10.2 `kaban_projects`

Назначение: deployment mirror зарегистрированных Projects.

Основные поля:

```text
project_id          text primary key
name                text
config_hash         text
config_json         jsonb        -- только non-secret config snapshot
enabled             boolean
created_at          timestamptz
updated_at          timestamptz
```

`project.yaml` остаётся source of truth; эта таблица — runtime/deployment projection.

---

## 10.3 `kaban_channels`

```text
project_id          text
channel_id          text
kind                text
locale              text null
enabled             boolean
metadata            jsonb        -- без secrets
created_at          timestamptz
updated_at          timestamptz
primary key(project_id, channel_id)
```

Пример CAELUS:

```text
caelus / telegram_ru / telegram / ru
caelus / telegram_en / telegram / en
caelus / instagram_ru / instagram / ru
```

Stage 4 не обязан включать Instagram publication; таблица только не блокирует расширение модели.

---

## 10.4 `kaban_project_settings`

```text
project_id          text
setting_key         text
value               jsonb
version             bigint
updated_at          timestamptz
primary key(project_id, setting_key)
```

Для CAELUS сюда может быть перенесён изменяемый пользователем diversity profile.

Project constants из Git не должны бесконтрольно дублироваться сюда.

---

## 10.5 `kaban_content_sets`

Это логический content object, например CAELUS `date + language`.

```text
content_set_id      uuid
project_id          text
content_key         text
content_date        date null
locale              text null
dimensions          jsonb
current_revision_id uuid null
version             bigint
created_at          timestamptz
updated_at          timestamptz
unique(project_id, content_key)
unique(project_id, content_set_id)
```

Пример CAELUS `content_key`:

```text
2026-09-26:ru
```

Core не интерпретирует формат строки.

---

## 10.6 `kaban_content_revisions`

Immutable content history.

```text
revision_id          uuid
project_id           text
content_set_id       uuid
revision_no          bigint
payload              jsonb
content_hash         text
producer_execution_id uuid null
created_at           timestamptz
unique(project_id, content_set_id, revision_no)
unique(project_id, revision_id)
```

Правила:

- опубликованный payload не меняется in-place;
- edit/regeneration создаёт новую revision;
- `current_revision_id` переключается атомарно;
- CAELUS payload остаётся opaque JSON для Core.

Это одновременно становится source для CAELUS uniqueness history; отдельная таблица «uniqueness history» не нужна на Stage 4.

---

## 10.7 `kaban_approvals`

Append-only approval audit.

```text
approval_id          uuid
project_id           text
content_set_id       uuid
revision_id          uuid
content_hash         text
action               text   -- approved / revoked
actor                text
created_at           timestamptz
```

Current approval определяется только для конкретной revision/hash.

Если edit/regeneration создаёт новую revision:

- старая approval history сохраняется;
- новая revision считается unapproved;
- publication старой approval не может автоматически использовать новый payload.

---

## 10.8 `kaban_artifacts`

Metadata для immutable R2 objects.

```text
artifact_id          uuid
project_id           text
content_set_id       uuid null
revision_id          uuid null
kind                 text
logical_name         text
r2_key               text
sha256               text
size_bytes           bigint
mime_type            text
metadata             jsonb
created_at           timestamptz
unique(project_id, r2_key)
```

DB содержит metadata/pointer, но не binary bytes.

---

## 10.9 `kaban_scheduler_jobs`

Runtime mirror job configuration.

```text
project_id           text
job_id               text
config_hash          text
handler              text
cron                  text
timezone              text
params                jsonb
misfire_grace_minutes integer
retry_policy          jsonb
enabled               boolean
updated_at            timestamptz
primary key(project_id, job_id)
```

Source of truth — `project.yaml`.

---

## 10.10 `kaban_executions`

Одна строка представляет логическую operation или scheduled slot.

```text
execution_id          uuid
project_id            text
job_id                text null
slot_id               text null
operation             text
trigger               text
scheduled_for         timestamptz null
misfire_deadline_at   timestamptz null
retry_deadline_at     timestamptz null
state                 text
attempt               integer
next_attempt_at       timestamptz null
wait_condition        jsonb null
lease_owner           text null
lease_expires_at      timestamptz null
fence_token           bigint
expected_version      bigint null
github_run_id         text null
github_run_url        text null
last_error            jsonb null
started_at            timestamptz null
finished_at           timestamptz null
created_at            timestamptz
updated_at            timestamptz
```

Для scheduled jobs:

```text
unique(project_id, job_id, slot_id)
```

Это сохраняет exactly-once **slot identity**.

Manual commands имеют `slot_id = NULL`, но получают стабильный `execution_id`.

---

## 10.11 `kaban_resource_leases`

Execution lease защищает одну operation, но сам по себе не запрещает двум разным operations одновременно менять один и тот же content object. Поэтому нужен отдельный generic resource lease.

```text
project_id             text
resource_key           text
owner_execution_id     uuid
fence_token            bigint
lease_expires_at       timestamptz
updated_at              timestamptz
primary key(project_id, resource_key)
```

Примеры resource keys:

```text
content:<content_set_id>
settings:content_diversity
channel:<channel_id>
```

CAELUS знает, какая operation требует какой resource scope; Core знает только opaque `resource_key`.

Mutating generation/edit/regeneration/approval/publication одного content set должны сериализоваться через exclusive content resource lease. Операции над разными content sets и разными Projects могут выполняться параллельно.

Claim/release/renew выполняются атомарно. Commit должен проверять как execution fence, так и resource fence, если operation владеет resource lease.

---

## 10.12 `kaban_execution_events`

Append-only operational audit.

```text
event_id              bigint/uuid
project_id            text
execution_id          uuid
event_type            text
payload               jsonb
created_at            timestamptz
```

Используется для диагностики без бесконечного раздувания основной execution row.

---

## 10.13 `kaban_publication_runs`

```text
publication_run_id    uuid
project_id            text
content_set_id        uuid
revision_id           uuid
channel_id            text
publication_key       text
state                 text
attempt               integer
masked_destination    text
last_error            jsonb null
created_at            timestamptz
updated_at            timestamptz
completed_at          timestamptz null
```

Normal publication exact revision/channel получает уникальный `publication_key`.

Intentional force-repeat создаёт новый explicit publication key и должен быть отдельным manual action.

---

## 10.14 `kaban_publication_steps`

Каждый внешний Telegram side effect фиксируется отдельно.

```text
project_id             text
publication_run_id     uuid
step_key               text
ordinal                integer
kind                   text
request_fingerprint    text
state                  text
external_ids           jsonb null
error                   jsonb null
started_at              timestamptz null
completed_at            timestamptz null
updated_at              timestamptz
primary key(project_id, publication_run_id, step_key)
```

Допустимые состояния:

```text
pending
sending
sent
failed
unknown_delivery
```

Для CAELUS step keys могут быть, например:

```text
media:1
media:2
text:1
text:2
```

Core не должен знать, почему именно этих шагов четыре.

---

## 10.15 `kaban_usage_samples`

```text
sample_id              bigint/uuid
project_id             text null
provider               text
resource               text
metric                 text
value                  numeric
unit                   text
quality                 text   -- provider_exact / db_exact / estimated
period_start            timestamptz null
period_end              timestamptz null
collected_at            timestamptz
metadata                jsonb
```

Нельзя отображать estimate как точную provider billing metric.

---

# 11. Composite foreign keys и защита от cross-project references

Недостаточно добавить `project_id` как обычную колонку.

Например, artifact должен ссылаться не просто на `revision_id`, а на пару:

```text
(project_id, revision_id)
```

То же правило применяется к:

- content set → revision;
- approval → revision;
- artifact → revision;
- publication → revision;
- publication step → publication run;
- execution event → execution;
- channel references.

Следствие: даже ошибка приложения не позволит связать CAELUS artifact с revision другого Project через foreign key.

---

# 12. R2 object naming

Рекомендуется один private bucket **на environment**, а не на Project.

Пример:

```text
kaban-artifacts-prod
kaban-artifacts-dev
```

Внутри обязательный prefix:

```text
projects/{project_id}/...
```

Canonical generated artifact:

```text
projects/{project_id}/content/{content_set_id}/revisions/{revision_id}/{artifact_kind}/{logical_name}
```

Пример:

```text
projects/caelus/content/6c.../revisions/a8.../cards/caelus_aries.png
```

## 12.1 Правила

1. Revision artifact keys immutable.
2. Не перезаписывать существующий key новой версией файла.
3. `sha256` и `size_bytes` хранятся в Supabase.
4. R2 upload выполняется до DB pointer commit.
5. DB не начинает ссылаться на object до успешной проверки upload/hash.
6. Если DB commit после upload не состоялся, object считается orphan и позже может быть удалён безопасным cleanup job.
7. Cleanup никогда не удаляет object, на который существует DB reference.

Так устраняется необходимость distributed transaction между PostgreSQL и R2.

---

# 13. Что хранить в R2

## Durable

- final PNG cards;
- будущие durable generated images/video/audio;
- migration manifests;
- application-level backup/export snapshots;
- при необходимости user-downloadable exports.

## Ephemeral / regenerable by default

- Telegram-optimized JPEG;
- temporary publish plan;
- scratch rendering intermediates;
- GitHub workspace files.

Telegram JPEG можно пересоздать из canonical PNG. Поэтому бессрочно хранить оба формата нецелесообразно.

---

# 14. GitHub Actions ephemeral workspace

Каждая heavy operation получает отдельную directory:

```text
$RUNNER_TEMP/kaban/<execution_id>/
```

Никакого reuse workspace между executions.

## 14.1 Flow

```text
1. Получить execution_id.
2. Атомарно claim execution в Supabase.
3. Получить fence_token.
4. Checkout Git repository.
5. Создать empty ephemeral workspace.
6. Materialize только нужный Project/context.
7. Проверить hashes materialized artifacts.
8. Установить KABAN_GENERATED_DIR/KABAN_RUNTIME_DIR.
9. Запустить существующий Project adapter/workflow.
10. Во время external side effects делать canonical checkpoints.
11. Собрать изменения через Project Workspace Adapter.
12. Загрузить новые durable artifacts в immutable R2 keys.
13. Проверить R2 metadata/hash.
14. Выполнить atomic Supabase commit с expected version + fence token.
15. Пометить execution terminal state.
16. Создать/обновить application-level backup snapshot при необходимости.
17. Workspace уничтожается runner-ом.
```

GitHub Actions artifacts не используются как primary persistence. Это экономит GitHub storage и устраняет второй artifact store.

---

# 15. Сколько истории материализовать

Нельзя скачивать весь R2/history на каждый Action.

Для CAELUS generation достаточно:

- текущего target content/status;
- CAELUS settings;
- content history только за configured uniqueness window;
- сейчас это 90 дней;
- только те payload, которые реально нужны uniqueness validator;
- source rendering assets приходят из Git checkout.

Для publish достаточно:

- exact approved revision;
- его PNG artifacts;
- publication journal/progress projection;
- минимально нужные settings/channel metadata.

Так cloud runtime сохраняет текущую CAELUS логику, но не превращает materialization в полную синхронизацию архива.

---

# 16. Transactional generation в cloud

Локальный `copytree()/rollback` остаётся valid для local backend.

В cloud backend canonical state **не изменяется во время generation**.

Flow:

```text
canonical revision N
    ↓ materialize
workspace
    ↓ generate/rebuild
candidate revision N+1
    ↓ validate
upload immutable artifacts
    ↓
atomic DB commit current_revision=N+1
```

Если Action падает на любом этапе до commit:

```text
current_revision = N
```

остаётся неизменной.

Следовательно cloud rollback генерации достигается не копированием directory, а **commit-last semantics**.

---

# 17. Optimistic concurrency для Review Console

Каждый editable `content_set` имеет `version`.

Browser получает:

```text
content_set_id
revision_id
version
```

Mutation command содержит `expected_version`.

Если пользователь открыл старую вкладку и между тем content был изменён другим Action:

```text
expected_version != current version
```

операция отклоняется как conflict.

Никакого silent last-write-wins.

---

# 18. Leases и fencing tokens

File lock недостаточен для serverless.

## 18.1 Lease

Claim происходит атомарной PostgreSQL function/RPC.

Она устанавливает:

```text
lease_owner
lease_expires_at
fence_token = fence_token + 1
```

Долгий Action периодически renew lease.

## 18.2 Fencing

Любой commit/checkpoint обязан передать текущий `fence_token`.

Если старый runner завис, lease истёк и другой runner получил новый token, старый runner больше не может записать состояние.

Это защищает от:

- stale GitHub runners;
- network partitions;
- duplicate workflow dispatch;
- manual restart во время старого job.

GitHub `concurrency` используется только как дополнительная защита, а не как canonical lock.

## 18.3 Resource-level concurrency

Execution lease отвечает на вопрос «кто выполняет эту execution». Resource lease отвечает на другой вопрос: «кто сейчас имеет право менять этот logical resource».

Для CAELUS content mutation resource key строится Project adapter-ом из `content_set_id`.

Это предотвращает гонки вида:

- scheduled generation и manual regeneration одного дня;
- approval и edit одной revision family;
- publication и edit того же content set;
- два manual Save из разных вкладок.

Optimistic `version` остаётся второй линией защиты: даже при ошибке lease stale commit будет отклонён.

Resource lease является универсальным KABAN mechanism и не содержит CAELUS-specific semantics.

---

# 19. Idempotency

## 19.1 Scheduled slots

Canonical identity:

```text
(project_id, job_id, slot_id)
```

UNIQUE constraint гарантирует, что один scheduled slot не создаётся дважды.

## 19.2 Manual commands

Каждый command получает stable `execution_id` до dispatch.

Повтор HTTP request из UI не должен создавать вторую operation, если client передал тот же idempotency key.

## 19.3 GitHub dispatch

Cloudflare Worker сначала создаёт/claim execution, только потом вызывает GitHub `workflow_dispatch`.

Workflow получает `execution_id`.

На старте Action делает `start_execution(execution_id)`.

Если duplicate workflow уже стартовал или execution terminal:

- второй workflow не выполняет Project business logic;
- он завершается как duplicate/no-op.

---

# 20. Scheduler architecture в cloud

## 20.1 Cloudflare Cron — только wakeup

Рекомендация:

```text
*/5 * * * *
```

Worker wake каждые 5 минут.

Он не запускает GitHub Action «на всякий случай».

Worker:

1. ищет due executions;
2. проверяет generic wait conditions;
3. atomic claim;
4. dispatch только если действительно есть работа.

На текущем масштабе это 288 wakeups/day, что далеко ниже текущего Free лимита Workers 100,000 requests/day.

## 20.2 Не дублировать cron semantics в TypeScript

Чтобы Worker не реализовывал отдельный cron parser/timezone/DST scheduler, Python KABAN Scheduler остаётся владельцем cron semantics.

Рекомендуемая модель — **schedule projection**:

- при deploy/config sync Python заранее создаёт scheduled execution rows на ограниченный horizon;
- после scheduled Action Python пополняет horizon;
- Worker работает только с абсолютными `scheduled_for` timestamps;
- если horizon становится меньше safety threshold, Worker создаёт одну lightweight maintenance execution, а не постоянно запускает scheduler Actions.

Рекомендуемый initial horizon:

```text
35 дней
```

Alert threshold:

```text
< 7 дней будущих slots
```

Значения являются design defaults и могут быть скорректированы в implementation plan без изменения архитектуры.

## 20.3 Misfire

Для каждого projected slot заранее записывается absolute:

```text
scheduled_for
misfire_deadline_at
retry_deadline_at
```

Worker может корректно решить, что slot ещё runnable или уже missed, не понимая cron выражение.

---

# 21. Как не сжигать GitHub Actions minutes на blocked publish

Плохой вариант:

```text
08:00 Action → not approved
08:10 Action → not approved
08:20 Action → not approved
...
```

Это запрещённый design pattern Stage 4.

## 21.1 Generic wait conditions

Project adapter может прикрепить к execution generic condition descriptor.

Например CAELUS publish:

```text
kind = content_approved
project_id = caelus
content_key = 2026-09-26:ru
```

Worker умеет проверять generic content approval, но ничего не знает о zodiac/CAELUS.

Если content draft:

- slot остаётся blocked/waiting;
- Worker не dispatch GitHub Action;
- каждые 5 минут выполняется только дешёвая DB condition check;
- после approval следующий wake dispatch-ит тот же slot, если retry window ещё открыт.

Если Project Action обнаружил другую retryable precondition, он может вернуть typed generic wait condition через расширенный `JobResult` contract.

---

# 22. Approval flow

Approval остаётся server-side operation.

Browser не пишет `approved=true` напрямую в Supabase.

## 22.1 Cloud flow

```text
Review Console
    ↓ command
Worker
    ↓ create execution
GitHub Action
    ↓ materialize exact revision
existing CAELUS validation
    ↓
existing uniqueness validation
    ↓
existing approval logic
    ↓
Supabase atomic approval event/commit
```

Так Stage 4 не копирует CAELUS validators в Worker/JavaScript.

## 22.2 Approval invalidation

Любой content edit/regeneration создаёт новую revision.

Approval exact old revision остаётся audit record, но current revision становится unapproved.

Publication всегда связывается с:

```text
revision_id + content_hash
```

а не просто с `date/language`.

---

# 23. Cloud Review Console

Local/Docker `admin_app.py` сохраняется.

Cloud Console — отдельный delivery layer:

```text
Cloudflare Worker static assets + API
```

Это предпочтительнее добавления отдельного Pages component на Stage 4: меньше moving parts.

## 23.1 Responsibilities Worker

Универсально:

- auth context;
- project scoping;
- safe reads;
- command creation;
- execution status;
- authorized artifact delivery;
- Cron orchestration;
- usage dashboard API.

Project-specific frontend/UI definitions для CAELUS должны находиться в:

```text
projects/caelus/
```

а не в KABAN Core.

## 23.2 Mutations

Cloud UI не должен переносить CAELUS business logic в Worker.

Операции вида:

- Save Changes;
- Regenerate Field;
- Regenerate Sign;
- Regenerate Conflicts;
- Approve All;
- Return to Draft;
- Manual Generate;
- Manual Publish;

создают command/execution, который выполняется существующим Python Project workflow в GitHub Action.

Browser показывает state operation и обновляет экран после completion.

---

# 24. Auth Review Console

Рекомендация: **Cloudflare Access** перед Worker route.

Для текущего небольшого admin user set это устраняет необходимость самостоятельно строить password storage/session/MFA.

Требования:

- Review Console не публичен;
- Worker доверяет identity только после Access validation;
- identity используется в audit fields `actor`;
- mutation endpoints требуют same-origin/CSRF protection;
- browser никогда не получает Supabase service key, R2 secret, GitHub dispatch token, OpenAI key или Telegram token.

Local Docker Basic Auth через Caddy остаётся без изменений для local/VPS deployment.

---

# 25. Secrets model

## 25.1 Cloudflare Worker secrets

Минимально:

- Supabase server credential;
- GitHub fine-grained credential для dispatch;
- Cloudflare internal bindings/credentials при необходимости.

GitHub token ограничивается одним repository и минимальным permission set. Для `workflow_dispatch` требуется repository `Actions: write`.

При существенном расширении лучше перейти с персонального PAT на GitHub App, но это не требуется для Stage 4 MVP.

## 25.2 GitHub Actions secrets

- `OPENAI_API_KEY`;
- `TELEGRAM_BOT_TOKEN`;
- destination/channel secrets;
- Supabase runner credential;
- R2 S3/API credential с доступом только к нужному bucket/environment.

## 25.3 Запрещено

Secrets не хранятся в:

- Supabase project config rows;
- `project.yaml`;
- Git;
- R2 metadata;
- GitHub artifacts;
- Action caches;
- execution error messages;
- browser payloads.

Existing exception sanitization сохраняется и расширяется provider secret patterns.

---

# 26. Publication: exactly-once semantics

## 26.1 Что KABAN может гарантировать строго

Stage 4 должен гарантировать:

1. exactly-once identity scheduled publication slot;
2. одна normal publication operation на exact revision/channel;
3. `sent` step автоматически повторно не отправляется;
4. stale runner не может продолжить publication после fence takeover;
5. content hash/revision mismatch блокирует resume;
6. intentional repeat возможен только explicit force action.

## 26.2 Что невозможно обещать честно

Telegram Bot API не предоставляет KABAN user-defined idempotency key для `sendMessage`/`sendMediaGroup`.

Существует неизбежное окно:

```text
Telegram принял сообщение
        ↓
process/network crash
        ↓
KABAN ещё не записал sent checkpoint
```

Поэтому абсолютная end-to-end exactly-once delivery во внешней системе недостижима без поддержки idempotency со стороны Telegram.

## 26.3 Безопасная политика Stage 4

Перед send:

```text
pending → sending
```

фиксируется canonical DB transaction.

После однозначного success:

```text
sending → sent
```

с `message_id`/external IDs записывается немедленно.

После однозначной ошибки до acceptance:

```text
sending → failed
```

и шаг может быть retryable.

Если результат ambiguous — timeout/crash после начала request:

```text
sending → unknown_delivery
```

или stale `sending` после lease recovery трактуется как `unknown_delivery`.

**Автоматический retry такого шага запрещён.**

Review Console должен показать manual reconciliation action. Это сознательно выбирает duplicate safety вместо blind liveness.

---

# 27. Publication checkpoints не могут ждать конца GitHub Action

Для generation достаточно commit-last.

Для Telegram side effects недостаточно materialize → run → sync-back в конце.

Поэтому KABAN Core должен предоставить generic cloud checkpoint client, который Project publication code может вызвать непосредственно вокруг external side effect.

Local backend этого client сохраняет текущую local semantics.

Cloud backend пишет publication step в Supabase немедленно.

Это **единственная область**, где Stage 4 намеренно добавляет online persistence hook внутрь выполняющегося Project workflow.

Она универсальна и находится в KABAN Core; CAELUS лишь сообщает step identity/result.

---

# 28. Retry model

Retries делятся на три класса.

## A. Precondition wait

Пример: content ещё не approved.

- GitHub Action не запускать;
- Worker ждёт generic DB condition;
- existing retry deadline сохраняется.

## B. Definitive retryable failure

Пример: provider вернул однозначную временную ошибку до side effect.

- `next_attempt_at` рассчитывается;
- Worker dispatch только когда наступило время;
- max attempts/retry deadline сохраняются из current scheduler semantics.

## C. Ambiguous external side effect

Пример: Telegram request timeout после отправки bytes.

- `unknown_delivery`;
- автоматический retry запрещён;
- manual reconciliation.

---

# 29. Cloudflare Cron → GitHub workflow dispatch

Worker Cron algorithm должен быть generic:

```text
1. Select executions where next_attempt_at <= now.
2. Exclude terminal rows.
3. Exclude active valid leases.
4. Evaluate generic wait_condition.
5. Expire rows beyond misfire/retry deadline where applicable.
6. Claim one/bounded batch atomically.
7. Set dispatching + dispatch nonce/lease.
8. Call GitHub workflow_dispatch with execution_id.
9. Record provider run metadata when available.
10. On definitive dispatch error, schedule safe dispatch retry.
```

Worker не запускает CAELUS code и не загружает Pillow/OpenAI libraries.

---

# 30. Failure windows при GitHub dispatch

Особый случай:

```text
Worker отправил workflow_dispatch
Worker умер до записи результата dispatch
```

Защита:

- execution существует до network request;
- GitHub workflow получает execution_id;
- Action обязан atomically `start_execution`;
- duplicate Action для того же execution видит уже `running`/terminal state и выходит;
- GitHub concurrency group является вторичной защитой.

Таким образом ambiguous dispatch не превращается в два Project business executions.

---

# 31. Migration existing `generated/` / `runtime/`

Migration должна быть отдельной one-time capability Stage 4 и по умолчанию работать в dry-run.

Исходные local directories не удаляются и не изменяются.

## 31.1 Mapping CAELUS

### Content

```text
generated/<date>/<language>/content.json
```

→ content_set + immutable content_revision.

### Status

```text
status.json
```

→ current approval projection + approval audit event, если applicable.

### PNG

```text
cards/*.png
```

→ immutable R2 objects + `kaban_artifacts`.

### Telegram JPEG

Не требуется бессрочно мигрировать как canonical, если он может быть детерминированно regenerated из PNG. Importer должен отразить decision в manifest, а не молча проигнорировать файлы.

### Publication

```text
publication.json
publication history
```

→ publication run/steps/events насколько информация позволяет однозначное mapping.

### Automation journal

```text
generated/_automation/...
```

→ execution/history records.

### Diversity settings

```text
generated/_settings/content_diversity.json
```

→ `kaban_project_settings`.

### Scheduler runtime

Valid completed scheduler state может быть импортирован как historical execution seed, но local `.lock` **никогда не становится cloud lease**.

Перед cutover активных local processes быть не должно.

---

# 32. Migration manifest

Каждый migration run создаёт manifest:

```text
source_path
source_size
source_sha256
classification
destination_type
destination_id_or_r2_key
destination_sha256
result
```

Правила:

- unknown files не теряются молча;
- importer завершает dry-run с отчётом unknown/unmapped;
- durable binary сначала загружается в R2;
- затем DB transaction фиксирует metadata/state;
- после commit выполняется read-back verification;
- source остаётся untouched.

---

# 33. Migration idempotency

Importer должен быть restartable.

Stable identity строится из:

```text
project_id + source logical path + sha256
```

Повтор одного migration run:

- не создаёт duplicate content revision;
- не создаёт duplicate R2 object pointer;
- не создаёт duplicate publication events.

Если DB transaction упал после R2 upload, orphan object разрешено оставить до cleanup; он не считается migrated, пока отсутствует committed DB reference.

---

# 34. Rollback strategy

## 34.1 До cloud cutover

Local v1.13 остаётся нетронутым.

Если Stage 4 deployment не проходит acceptance:

- cloud environment выключается;
- local/docker продолжает работать.

## 34.2 После cloud cutover без новых cloud mutations

Можно вернуться к local snapshot непосредственно.

## 34.3 После cloud-only mutations

Нельзя просто запустить старую local directory: она будет stale.

Перед rollback нужен cloud → local materialization/export current state.

Workspace Bridge должен уметь создать полноценную local projection для Project из canonical Supabase/R2.

## 34.4 R2/content rollback

Artifacts immutable, поэтому rollback revision — это pointer change, а не overwrite binary object.

## 34.5 Publication rollback

Внешняя Telegram публикация необратима с точки зрения transaction rollback.

KABAN не должен автоматически удалять Telegram posts при DB rollback.

Любое внешнее reconciliation — explicit manual action.

---

# 35. Application-level backups на Supabase Free

На текущем Free plan downloadable managed database backups недоступны. Supabase рекомендует Free projects регулярно делать logical export.

Поэтому Stage 4 не должен считать managed Free backup достаточным recovery mechanism.

Рекомендация без лишних GitHub minutes:

- после успешной mutating GitHub Action generic backup/export step создаёт compact control-plane snapshot;
- snapshot сохраняется в R2;
- если данных не было изменено, отдельный daily Action ради backup не нужен;
- migration manifests и immutable content/artifacts остаются дополнительным recovery source.

Это application-level backup, а не замена полноценному PostgreSQL PITR.

При повышении требований к SLA/RPO должен рассматриваться Supabase paid/PITR отдельно.

---

# 36. Observability

## 36.1 Correlation IDs

Каждый log/event содержит минимум:

```text
project_id
execution_id
job_id/operation
slot_id (если есть)
attempt
```

Publication дополнительно:

```text
publication_run_id
step_key
```

## 36.2 Worker

Наблюдать:

- Cron heartbeat;
- last successful orchestration tick;
- number due/claimed/dispatched;
- dispatch failures;
- schedule horizon.

## 36.3 GitHub Action

Наблюдать:

- materialize duration;
- Project execution duration;
- artifact upload bytes;
- sync duration;
- sanitized error;
- GitHub run ID/URL.

## 36.4 Scheduler health cloud

Local `runtime/heartbeat.json` остаётся local-only.

Cloud scheduler health выводится из Supabase:

- last Cron tick;
- oldest due execution;
- stale leases;
- missed slots;
- schedule horizon;
- unknown publication delivery.

---

# 37. Usage and cost monitoring

Stage 4 должен собирать provider usage и показывать понятные thresholds.

Рекомендуемые thresholds:

```text
70% — warning
85% — high
95% — critical
```

Никакого auto-upgrade и никакого auto-delete пользовательских данных.

## 37.1 Supabase

Мониторить:

- PostgreSQL database size;
- egress;
- API/request usage при необходимости.

Database size можно получать точно через PostgreSQL `pg_database_size(...)`.

Если точный billing egress нельзя получить доступным API, UI должен показывать значение как `estimated`, а не как точное.

## 37.2 R2

Мониторить:

- payload storage bytes;
- object count;
- Class A operations;
- Class B operations;
- bandwidth при необходимости.

Cloudflare предоставляет R2 metrics через dashboard/API/GraphQL.

## 37.3 GitHub Actions

Мониторить:

- included minutes;
- used minutes;
- workflow duration по operation type;
- retry minutes.

Основной оптимизационный показатель:

```text
Actions minutes per successful content-day
```

## 37.4 Worker

Мониторить request count и execution errors; на текущем масштабе Cron wakeups очень далеки от Free request quota.

---

# 38. Текущие Free-tier границы, учтённые design

Данные проверены по официальным источникам 2026-09-26.

## Supabase Free

- Database size: 500 MB/project.
- Egress: 5 GB.
- Free project может быть paused при низкой активности примерно за 7 дней.
- Downloadable managed DB backups на Free недоступны.

Для одного CAELUS JSON/control data 500 MB должен иметь большой запас, но usage monitor обязателен.

## Cloudflare R2 Free Standard

- 10 GB-month storage/month;
- 1 million Class A operations/month;
- 10 million Class B operations/month;
- Internet egress free.

## Cloudflare Workers Free

- 100,000 requests/day;
- 10 ms CPU/invocation.

Orchestrator должен оставаться очень лёгким; heavy Python запрещён в Worker.

## GitHub Actions

Для private repository на GitHub Free сейчас включено 2,000 minutes/month стандартных GitHub-hosted runners. Public standard runners не расходуют такую платную квоту.

Design не должен предполагать unlimited private minutes.

## Cloudflare Access

Free tier рассчитан до 50 users, что достаточно для текущего admin use case.

---

# 39. R2 capacity: реальная оценка на основе v1.13 sample

В приложенном v1.13 для RU sample day:

```text
12 PNG cards        ≈ 26.364 MiB
12 Telegram JPEG    ≈ 4.316 MiB
```

Если размер PNG останется примерно таким же и хранить RU ежедневно:

```text
PNG ≈ 10.09 GB/year (decimal)
```

Если бессрочно хранить и Telegram JPEG:

```text
ещё ≈ 1.65 GB/year
```

Это **экстраполяция одного sample day, не billing forecast**.

Вывод:

- Supabase DB, скорее всего, не будет первым Free bottleneck;
- R2 storage может приблизиться к Free 10 GB примерно в масштабе года только для RU при текущем PNG размере;
- включение EN приблизительно удвоит image growth;
- поэтому Telegram derivatives не хранятся long-term по умолчанию;
- перед 70–85% R2 quota нужно решить: оптимизировать PNG, вводить осознанный retention policy или оплачивать небольшой excess;
- Stage 4 **не должен автоматически удалять старые карточки**.

---

# 40. Project isolation

Обязательные уровни изоляции:

## Database

- `project_id` everywhere;
- composite FKs;
- project-scoped repository APIs;
- RLS;
- no implicit cloud active project.

## R2

```text
projects/<project_id>/...
```

## Workspace

```text
<runner-temp>/<execution_id>/<project_id>/...
```

## Scheduler

```text
(project_id, job_id, slot_id)
```

## Review Console

Route/action всегда содержит explicit project scope.

## Dynamic adapters

KABAN загружает adapter по declarative config; Project A не импортирует Project B.

---

# 41. Review Console reads и artifacts

Browser не читает private R2 напрямую по permanent public URL.

Worker:

- проверяет Access identity;
- проверяет project scope;
- отдаёт/проксирует artifact или создаёт короткоживущий authorized access mechanism;
- не раскрывает bucket credentials.

Для current small CAELUS usage proxy через Worker допустим. Если image traffic вырастет, signed URLs/private custom delivery можно оптимизировать отдельно.

---

# 42. Commands вместо прямых mutations из browser

Cloud UI формирует generic command envelope:

```text
execution_id
project_id
operation
content_set_id/content_key
expected_version
payload
requested_by
idempotency_key
```

`payload` project-specific и opaque для KABAN orchestration layer.

Project adapter валидирует payload в GitHub Action.

Это позволяет будущему Project иметь собственные commands без добавления его business schema в Core.

---

# 43. State ownership при materialize/sync

Очень важно избежать dual-master.

В cloud mode:

- Supabase/R2 — canonical;
- temporary local files — disposable projection;
- GitHub Action не считает старый workspace authoritative;
- sync-back выполняется только через expected version/fence transaction;
- stale workspace никогда не перезаписывает более новую canonical revision.

В local mode:

- filesystem — canonical;
- Supabase/R2 вообще не требуются.

Один runtime process не работает одновременно в двух persistence modes над одним production dataset.

---

# 44. Cleanup

Разрешены только безопасные виды cleanup:

1. expired DB leases — перевод через recovery rules;
2. orphan R2 objects без DB references после grace period;
3. ephemeral runner workspace — автоматически;
4. old temporary derivatives — автоматически, потому что они не canonical.

Не разрешается auto-cleanup:

- content history;
- approved revisions;
- published artifacts;
- publication audit;
- migration manifests;

без отдельной retention policy, которую пользователь подтвердит отдельно.

---

# 45. Scope Stage 4

## Входит

- generic persistence boundary;
- local + cloud backends;
- Supabase schema/migrations;
- R2 artifact backend;
- workspace bridge;
- CAELUS workspace adapter;
- cloud execution/lease/fencing;
- schedule projection;
- Cloudflare Cron orchestrator;
- conditional dispatch;
- GitHub Actions heavy runtime;
- cloud Review Console path;
- approval commands;
- generation/edit/regeneration commands;
- publication with canonical checkpoints;
- migration importer;
- cloud → local export;
- observability;
- usage/cost monitoring;
- application-level backup snapshots.

## Не входит

- переписывание CAELUS business logic в TypeScript;
- перенос OpenAI/Pillow generation в Cloudflare Worker;
- Instagram publication;
- EN production enablement;
- auto-approve;
- автоматическая покупка/upgrade provider plans;
- произвольный unrelated refactor v1.13;
- удаление старого local/Docker runtime;
- перенос всех static design assets из Git в R2;
- гарантирование абсолютного exactly-once Telegram delivery при ambiguous network outcome.

---

# 46. Testing principles для будущей реализации

Реализация Stage 4 должна следовать требованию:

```text
RED → GREEN → full regression
```

Но тесты и код начинаются только после отдельного approved implementation plan.

Design требует следующие классы проверок.

## 46.1 Contract tests

Одинаковая high-level persistence semantics для local/cloud backend там, где это применимо.

## 46.2 Existing regression

Существующие v1.13 local workflows не должны менять outputs без осознанной причины.

Особенно:

- generation;
- rendering;
- approval;
- publication planning;
- scheduler semantics;
- existing user mock artifacts.

## 46.3 Project isolation tests

Попытки cross-project access/reference должны завершаться отказом.

## 46.4 Concurrency/fault injection

Минимальные сценарии:

- два Workers одновременно claim один slot;
- duplicate GitHub dispatch;
- stale Action после lease takeover;
- R2 upload success + DB commit failure;
- DB version changed во время Action;
- user edits stale revision;
- Action crash during materialization;
- Action crash before final generation commit;
- publication crash before send;
- publication definitive API failure;
- publication ambiguous timeout after send start;
- migration restart after partial R2 upload.

## 46.5 Migration verification

- counts;
- JSON hashes;
- PNG byte hashes;
- unknown file report;
- read-back from Supabase/R2;
- idempotent second run.

## 46.6 Live acceptance

Cloud provider acceptance должна быть отдельной и явно отмеченной как live. Нельзя выдавать mock integration за проверенный deployment.

---

# 47. Stage 4 acceptance invariants

Stage 4 можно считать архитектурно завершённым только если одновременно верны следующие свойства.

1. `kaban/` не импортирует CAELUS.
2. Новый Project можно подключить без изменения CAELUS.
3. Projects не зависят друг от друга.
4. Local v1.13 workflow остаётся поддерживаемым.
5. Docker/VPS не требует cloud providers.
6. Cloud canonical JSON/state хранится в Supabase.
7. Durable generated media хранится в R2.
8. GitHub Action workspace disposable.
9. Stale runner не может commit после fence takeover.
10. Concurrent mutations одного logical resource сериализуются resource lease + version CAS.
11. Scheduled slot имеет unique identity.
12. Blocked approval не запускает GitHub Actions каждые 10 минут.
13. Edit invalidates approval exact revision.
14. Publication привязана к exact revision/hash.
15. `sent` step автоматически не повторяется.
16. Ambiguous Telegram delivery не retry-ится автоматически.
17. Browser не получает provider secrets.
18. Migration не модифицирует source dataset.
19. Cloud state может быть materialized обратно в local form для rollback/export.
20. Usage dashboard отличает exact metrics от estimates.
21. Никакой provider plan не повышается автоматически.

---

# 48. Основные design decisions

## DEC-1

**Выбрать hybrid canonical Supabase/R2 + ephemeral workspace bridge.**

## DEC-2

**Supabase PostgreSQL — canonical control/data store cloud mode.**

## DEC-3

**R2 — immutable durable artifact store, не distributed filesystem.**

## DEC-4

**Один Supabase Project для KABAN, все domain records project-scoped.**

## DEC-5

**Один private R2 bucket на environment, project isolation через mandatory prefix + DB metadata.**

## DEC-6

**`project.yaml` остаётся Git source of truth; DB хранит validated runtime projection.**

## DEC-7

**Cloudflare Worker — lightweight control plane; heavy Python только GitHub Actions.**

## DEC-8

**Cloudflare Cron не выполняет business jobs; только ищет due work и dispatch-ит при необходимости.**

## DEC-9

**Cloud scheduler использует precomputed absolute slots, чтобы не дублировать cron/timezone engine в Worker.**

## DEC-10

**Approval/block conditions предотвращают пустые retry Actions.**

## DEC-11

**Publication checkpoints записываются в canonical DB вокруг каждого external side effect.**

## DEC-12

**Ambiguous Telegram delivery → `unknown_delivery`, manual reconciliation, no auto retry.**

## DEC-13

**Local/Docker filesystem backend остаётся first-class и default.**

## DEC-14

**Migration additive, dry-run first, source untouched.**

## DEC-15

**Free-tier cost strategy управляется monitoring/thresholds, а не автоматическим удалением данных или upgrade.**

---

# 49. Риски и mitigation

| Риск | Mitigation |
|---|---|
| CAELUS regression | filesystem projection + targeted adapters + existing regression suite |
| stale concurrent Action | execution lease + fence token + expected version |
| concurrent mutations одного content | resource lease + resource fence + version CAS |
| duplicate scheduler execution | unique project/job/slot + atomic claim |
| duplicate Telegram message after ambiguous timeout | `unknown_delivery`, no automatic resend |
| R2/DB partial commit | immutable upload first, DB pointer commit last, orphan cleanup |
| blocked publish wastes Actions minutes | generic wait condition evaluated in Worker |
| stale Review Console edit | optimistic version check |
| Free Supabase paused | regular real application DB activity + health alert; paid plan if production SLA requires |
| no downloadable Free backup | application logical snapshots + retained local migration source/export |
| R2 Free storage grows | 70/85/95 monitoring; no long-term JPEG derivatives; later explicit optimization/retention/paid excess |
| provider credential leak | secret separation, least privilege, sanitization, no browser secrets |
| Core becomes CAELUS-specific | opaque payload/commands + dynamic Project adapters + project isolation tests |

---

# 50. Free-tier assessment for current CAELUS

На текущем масштабе архитектура обоснованно может стартовать около `$0/month`, если:

- repository остаётся в пределах GitHub included Actions minutes;
- Worker остаётся lightweight;
- Supabase JSON/control data остаётся далеко ниже 500 MB;
- R2 не превысит 10 GB-month free storage;
- не включаются платные add-ons;
- Access остаётся в пределах 50 users.

Но `$0 forever` **не является архитектурной гарантией**.

Наиболее вероятный первый лимит при ежедневном бессрочном хранении CAELUS media — R2 storage, особенно после включения EN.

Stage 4 должен сделать переход на paid predictable, а не аварийным: usage trend + thresholds + provider quota source.

---

# 51. Официальные источники для текущих provider limits

Проверено 2026-09-26:

- Supabase billing/quotas: https://supabase.com/docs/guides/platform/billing-on-supabase
- Supabase Free project pausing: https://supabase.com/docs/guides/platform/free-project-pausing
- Supabase production checklist: https://supabase.com/docs/guides/deployment/going-into-prod
- Supabase backups: https://supabase.com/docs/guides/platform/backups
- Supabase database size: https://supabase.com/docs/guides/platform/database-size
- Cloudflare R2 pricing: https://developers.cloudflare.com/r2/pricing/
- Cloudflare R2 metrics: https://developers.cloudflare.com/r2/platform/metrics-analytics/
- Cloudflare Workers pricing: https://developers.cloudflare.com/workers/platform/pricing/
- Cloudflare Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- Cloudflare plans / Access user limit: https://www.cloudflare.com/plans/
- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- GitHub workflow dispatch API: https://docs.github.com/en/rest/actions/workflows

Provider limits меняются; implementation не должен считать эти числа вечными константами без возможности конфигурации.

---

# 52. Итоговая рекомендация

Stage 4 не должен превращаться в «перепишем CAELUS под Supabase».

Правильная граница:

```text
KABAN Core
  ├── Persistence contracts
  ├── Local backend
  ├── Supabase control/data backend
  ├── R2 artifact backend
  ├── Workspace Bridge
  ├── Lease / fencing / idempotency
  ├── Cloud scheduler orchestration
  ├── Publication checkpoint infrastructure
  └── Usage/observability

Projects
  └── CAELUS
      ├── Existing business logic
      ├── Existing renderer/generator/publisher
      ├── Workspace projection adapter
      └── Project-specific cloud Review Console definitions/actions
```

В GitHub Action CAELUS по-прежнему работает с обычными локальными файлами. Разница только в том, что эти файлы являются temporary projection canonical cloud state.

Это даёт наименьший regression risk, сохраняет local/Docker deployment и создаёт универсальную основу для следующих KABAN Projects.

---

# 53. Gate после этого документа

Текущий gate:

```text
Stage 4 Design Spec → USER REVIEW
```

До явного подтверждения этого design spec:

- implementation plan не создаётся;
- production code не изменяется;
- schema migrations не создаются;
- GitHub workflows не создаются;
- Supabase/R2/Cloudflare resources не создаются.

После подтверждения следующий разрешённый шаг:

```text
Stage 4 Implementation Plan
```

И только после отдельного подтверждения implementation plan разрешается начать TDD implementation.
