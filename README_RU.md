# KABAN Content Engine + CAELUS

CAELUS v4.11 работает как первый Project внутри KABAN.

CAELUS — локальная система генерации, ревью и публикации ежедневного астрологического контента.

## Основной workflow v4.11

```text
Generate tomorrow
    ↓
DRAFT
    ↓
Review / edit
    ↓
Save & rebuild
    ↓
Approve All
    ↓
APPROVED
    ↓
Publish Telegram
    ↓
PUBLISHING / PARTIALLY PUBLISHED / FAILED / PUBLISHED
```

Ежедневная работа выполняется через Review Console. PowerShell для обычного workflow не нужен.


## Управление уникальностью в Review Console

В v4.11 уровень уникальности меняется прямо в интерфейсе. Доступны профили:

- `Soft` — 88%;
- `Balanced` — 84%;
- `Strict` — 80% (по умолчанию);
- `Very Strict` — 75%;
- `Custom` — любое значение 70–95%.

Окно истории выбирается отдельно: 30, 60, 90, 180 или 365 дней.

Быстрые кнопки **70% / 80% / 90% / 95%** переключают hard threshold локально; `Custom` позволяет задать любое значение 70–95%. Кнопка **Проверить без API** только пересчитывает локальные similarity-метрики и не расходует токены. После проверки **Regenerate conflicts** вызывает AI только для найденных hard-conflict полей.

**Uniqueness Inspector** показывает знак, поле, similarity, severity, дату совпадения, текущий текст и точный исторический текст. Для каждого совпадения доступен точечный **Regenerate this field**. После regeneration Review Console показывает число попыток и similarity до/после. 

Настройки сохраняются в `generated/_settings/content_diversity.json` и не требуют редактирования `.env`.

### Semantic metadata

Для новых AI datasets каждый знак получает внутренний `_diversity` блок (`theme`, `situation`, `tone`, `advice_pattern`). Он не попадает на карточки или в Telegram и предназначен для следующего этапа semantic/topic diversity.

## Запуск на Windows

Первичная установка:

```text
setup_windows.bat
```

Затем:

```text
start_admin.bat
```

Review Console откроется по адресу:

```text
http://127.0.0.1:8088
```

Сервер слушает только `127.0.0.1` и не предназначен для публикации в интернет.

## `.env`

В ZIP намеренно находится только `.env.example`. Настоящий `.env` с секретами в архив не включается.

Создайте рядом с `admin_app.py` файл `.env`:

```env
OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
CAELUS_OPENAI_MODEL=gpt-5.6-luna
CAELUS_OPENAI_REASONING_EFFORT=low

CONTENT_HISTORY_DAYS=90
CONTENT_UNIQUENESS_WARNING_THRESHOLD=0.76
CONTENT_UNIQUENESS_HARD_THRESHOLD=0.80
CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS=3

TELEGRAM_BOT_TOKEN=<REAL_TOKEN>
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
```

`TELEGRAM_CHAT_ID` должен быть числовым ID канала вида `-100...`. Реальный Telegram token не выводится в UI и не должен попадать в ZIP.

Если `.env` отсутствует, Review Console показывает инструкцию вместо аварийного падения при публикации. Для AI-генерации также выводится понятное сообщение о том, как создать `.env`.

## OpenAI API usage и стоимость

Начиная с v4.9 CAELUS явно использует `CAELUS_OPENAI_REASONING_EFFORT=low` по умолчанию. Значение можно изменить через `.env`, но для ежедневной генерации прогнозов `low` является базовой настройкой.

После каждого успешного OpenAI API-вызова CAELUS сохраняет в `generation.api_usage` фактические usage-поля ответа API: input/prompt tokens, cached input, output/completion tokens, reasoning tokens и total tokens. Usage суммируется для первоначальной генерации, автоматических uniqueness-regeneration и ручного `Regenerate`.

Review Console показывает:

- количество API-запросов для текущего dataset;
- input, cached input, output, reasoning и total tokens;
- рассчитанную стоимость текущего dataset;
- накопленную рассчитанную стоимость по всем языкам за календарный месяц.

Reasoning tokens являются частью completion/output tokens и **не прибавляются к стоимости второй раз**. Стоимость сохраняется как оценка по snapshot тарифа, встроенному в конкретную версию CAELUS. Для GPT-5.6 Luna в v4.9 используется snapshot 2026-09-23: `$0.20 / 1M input`, `$0.02 / 1M cached input`, `$1.20 / 1M output`. Если выбрана модель, для которой CAELUS не знает тариф, токены всё равно сохраняются, а стоимость помечается как недоступная.

Старые datasets остаются совместимыми: отсутствие `generation.api_usage` не является ошибкой.

## Generate tomorrow

Кнопка **Generate tomorrow** всегда вычисляет календарное завтра относительно локальной даты машины, а не относительно открытого исторического дня.

Она:

1. создаёт `generated/YYYY-MM-DD/<language>/content.json`;
2. генерирует прогнозы для 12 знаков;
3. создаёт 12 карточек 1080×1350;
4. собирает Telegram-тексты;
5. записывает `status.json` со статусом `draft`;
6. автоматически открывает созданный день в Review Console.

Если комплект на завтра уже существует, новый платный AI-вызов не выполняется — открывается существующий комплект.

## Прикладная архитектура

После KABAN Final Cleanup CAELUS является полноценным Project. Legacy-пакеты `caelus/` и `content_engine/` удалены из production tree.

```text
kaban/
├── projects.py
├── assets.py
├── ai/
├── storage/
└── publishing/

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
└── assets/
```

Направление зависимостей: `root entrypoints -> projects/caelus -> kaban`. KABAN Core не импортирует CAELUS и не знает о zodiac domain, prompts, 12 карточках или схеме Telegram `6 + 6`.

Общие технические сервисы находятся в KABAN Core:

- `kaban/ai/` — structured JSON AI transport и usage accounting;
- `kaban/storage/` — JSON persistence и content hash;
- `kaban/publishing/telegram.py` — Telegram Bot API transport;
- `kaban/assets.py` — разрешение Project assets;
- `kaban/projects.py` — Project Registry/config.

Вся CAELUS-specific бизнес-логика находится в `projects/caelus/`: generation, prompts, schemas, validation, uniqueness, rendering, workflow, storage layout и publication strategy. Исходные PNG также находятся только в `projects/caelus/assets/`.

Исторический layout `generated/YYYY-MM-DD/<language>/...` сохранён без миграции, поэтому существующие datasets продолжают работать.

Корневые `generate_cards.py`, `build_telegram.py` и `publish_telegram.py` остаются тонкими CLI entrypoints для совместимости команд. `run_daily.py`, `generate_forecasts.py` и `admin_app.py` вызывают Project API напрямую.

Подробная граница модулей и state machine описаны в `ARCHITECTURE.md`.

## Adaptive Content Uniqueness

В v4.10 исправлен баг ручного `Regenerate` из v4.9: similarity-warning не передавал конфликтующий исторический текст в prompt, поэтому AI мог вернуть практически тот же вариант. Теперь ручная и автоматическая regeneration используют полный конфликтный контекст и проверяют, что новый текст действительно отличается.

Начиная с v4.8 каждый новый прогноз сравнивается с историей, а в v4.11 размер окна выбирается в Review Console: 30 / 60 / 90 / 180 / 365 дней. По умолчанию используется **90 календарных дней**. Историческое сравнение выполняется корректно по паре `sign + field`: например, новый `Aries/general` сравнивается только с прошлым `Aries/general`, а не с текстами других знаков.

Проверяются отдельно:

```text
card
general
love
career_money
advice
```

Алгоритм использует нормализацию, Sequence similarity, token cosine/Jaccard и character n-grams. По умолчанию:

```text
< 76%       OK
76–79.99%   quality warning
>= 80%      blocking repetition error + automatic regeneration
```

Точные дубли всегда имеют 100% и блокируют approval. В v4.11 основной способ менять пороги — профиль Content Diversity в Review Console; `.env` используется только как fallback до первого сохранения пользовательских настроек.

При `Generate tomorrow` CAELUS сначала генерирует весь комплект, затем проверяет выбранное историческое окно. При достижении hard threshold активного профиля автоматически перегенерируется только конфликтующее поле (`card`, `general`, `love`, `career_money` или `advice`), максимум 3 раунда. Остальные поля этого знака и остальные 11 знаков не меняются.

В prompt повторной попытки передаются **точный текущий текст и точный исторический текст**, с которым найден конфликт. Модели явно запрещено просто перефразировать их: требуется новая центральная тема, другая конкретная ситуация и другой практический вывод/совет. Если AI возвращает вариант с сходством не ниже hard threshold активного профиля к заменяемому тексту, CAELUS не принимает его и делает следующую попытку.

Если после лимита попыток конфликт остался, dataset всё равно сохраняется как `DRAFT`, чтобы редактор мог его исправить, но `Approve All` и `Publish Telegram` остаются заблокированы.

В Review Console показывается блок **Content Diversity** с профилем, длиной окна, hard threshold, количеством конфликтов и локальным preview. Быстрые пороги 70 / 80 / 90 / 95% и изменение history window выполняются без API. **Uniqueness Inspector** показывает обе стороны совпадения: текущий и исторический текст, дату, знак, поле, similarity и severity. Доступны точечный **Regenerate this field** и массовый **Regenerate conflicts**.

Мягкое предупреждение `card_target_length` (рекомендуемые 120–180 символов) остаётся внутренней диагностикой, но не показывается в Review Console и не засоряет пользовательский счётчик warnings. Реальные ограничения карточки — меньше 80 или больше 220 символов — по-прежнему являются validation errors.

У каждого поля есть собственная кнопка **Regenerate**. Она меняет только выбранный блок, например `Pisces → love`; для `love/general/career_money/advice` PNG-карточка не пересобирается, пересобираются только Telegram-тексты. Для `card` пересобирается только карточка выбранного знака. Кнопка **Regenerate sign** остаётся для полного нового варианта одного знака.

Все эти проверки выполняются повторно при `Approve All` и непосредственно перед Telegram publication.

## Review и validation

Review Console показывает:

- дату;
- язык;
- общий workflow status;
- preview 12 карточек;
- все текстовые поля;
- validation errors;
- quality warnings;
- Telegram publication state.

`card` имеет жёсткий максимум 220 символов и целевой диапазон 120–180. Ошибки блокируют `Approve All`; предупреждения показываются редактору, но не блокируют осознанное подтверждение.

После редактирования пересобираются только карточки изменённых знаков. Telegram batch-файлы пересобираются целиком, так как зависят от всего комплекта.

Любое изменение approved-контента автоматически возвращает его в `draft`.

## Approval

`Approve All`:

- сохраняет текущие правки;
- проверяет dataset;
- рассчитывает SHA-256 `content_hash`;
- сохраняет hash в `status.json`;
- переводит комплект в `approved`.

Telegram Publisher повторно сверяет hash перед отправкой. Изменённый после approval контент опубликовать нельзя.

## Telegram publication

Низкоуровневый Telegram Bot API transport находится в `kaban/publishing/telegram.py` и является общим сервисом KABAN. Сценарий публикации CAELUS находится в `projects/caelus/publication.py`; корневой `publish_telegram.py` сохранён как compatibility CLI.

Рабочая схема сохранена:

- 12 PNG автоматически оптимизируются во временные JPEG;
- album 1 = 6 карточек;
- album 2 = 6 карточек;
- затем отправляются расширенные текстовые блоки;
- сетевые timeout имеют retry;
- HTTP-ошибки Telegram не маскируются под timeout.

Кнопка **Publish Telegram** доступна только для `approved` комплекта и требует подтверждения.

### Состояния

Review Console отображает:

```text
DRAFT
APPROVED
PUBLISHING
PARTIALLY PUBLISHED
PUBLISHED
FAILED
```

`publication.json` — текущий progress journal и источник истины для продолжения после сбоя.

`publication_history.json` — append-only история попыток и шагов публикации.

После каждого успешно отправленного альбома и текстового batch progress сохраняется на диск. При повторном запуске уже записанные шаги пропускаются.

Если сбой произошёл после хотя бы одного успешного шага, состояние становится `partially_published`. Если ничего не было отправлено — `failed`.

Редактирование блокируется после начала фактической/частичной публикации, чтобы не разрушить `content_hash` и progress journal.

Один опубликованный день нельзя случайно отправить повторно через обычный Review Console workflow. Намеренный повтор остаётся только CLI-операцией с `--force`.

## Dry run Telegram

После approval можно проверить план без отправки:

```powershell
python .\publish_telegram.py --date 2026-09-23 --language ru --dry-run
```

Будет создан:

```text
generated/2026-09-23/ru/telegram_publish_plan.json
```

## Структура generated

```text
generated/YYYY-MM-DD/ru/
├── content.json
├── status.json
├── cards/
│   └── 12 PNG
├── telegram/
│   ├── 01_aries.md ... 12_pisces.md
│   └── batch_*.md
├── telegram_media/             # создаётся перед реальной публикацией
├── telegram_publish_plan.json  # создаётся publisher
├── publication.json            # текущий progress/status
└── publication_history.json    # история публикации
```

## CLI совместимость

Mock pipeline без API:

```powershell
python .\run_daily.py --date 2026-09-23 --language ru --mock
```

Генератор также сохраняет совместимые параметры `--mock`, `--model`, `--history-days`.

## Миграция с v4.8

Если v4.8 находится рядом с новой папкой, можно запустить:

```text
migrate_from_v4_8.bat
```

Скрипт локально переносит `.env` (если он есть) и папку `generated`. Реальный `.env` по-прежнему не должен распространяться вместе с ZIP. Старые helper-скрипты миграции сохранены для совместимости.

## Production Automation Stage 1

Stage 1 добавляет безопасный automation-runner без расписания и без auto-publish.

```powershell
python automation.py status --date 2026-09-25 --language ru
python automation.py generate --date 2026-09-25 --language ru
python automation.py generate --date 2026-09-25 --language ru --mock
python automation.py publish --date 2026-09-25 --language ru --dry-run
python automation.py publish --date 2026-09-25 --language ru
```

Повторная генерация существующего дня пропускается без `--force`; повторная публикация уже опубликованного дня также пропускается. Stage 1 не содержит cron/scheduler и ничего не публикует автоматически по времени.

## KABAN Project Scheduler — Stage 2

Начиная с v1.12 расписание является общей функцией KABAN, а не CAELUS-specific кодом.

Архитектурная граница:

```text
KABAN scheduler
    -> ProjectRegistry
    -> dynamic adapter module:function
    -> projects/<project_id>/scheduler.py
    -> Project automation/workflow
```

KABAN знает только **когда** запускать job. Смысл `generate`, `publish`, `collect`, `analyse` и других handler определяется Project.

### Production schedule CAELUS — v1.12.1

В production-профиле v1.12.1 scheduler для CAELUS включён явно. Расписание интерпретируется в `Pacific/Auckland`:

- `06:00` — реальная AI-генерация RU на текущий локальный день;
- `08:00` — публикация RU, только если контент уже `approved`;
- EN jobs пока отсутствуют и автоматически не запускаются.

Если к 08:00 контент ещё не approved, publication job возвращает `blocked` и повторяется каждые 10 минут в пределах 3 часов. Auto-approve отсутствует.

Production-конфигурация:

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
        mode: ai
        target_date_offset_days: 0
      misfire_grace_minutes: 30
      retry:
        interval_minutes: 10
        window_minutes: 120
        max_attempts: 12

    - id: publish_ru
      handler: publish
      enabled: true
      cron: "0 8 * * *"
      params:
        language: ru
        target_date_offset_days: 0
      misfire_grace_minutes: 30
      retry:
        interval_minutes: 10
        window_minutes: 180
        max_attempts: 18
```

Cron имеет стандартные пять полей и интерпретируется в `timezone` конкретного Project. Для CAELUS это `Pacific/Auckland`.

Publication job **не делает auto-approve**. Пока контент не approved, CAELUS adapter возвращает `blocked`, а KABAN повторяет тот же slot в пределах настроенного retry-window.

### Scheduler CLI

```powershell
python scheduler.py list
python scheduler.py status
python scheduler.py status --project caelus
python scheduler.py tick
python scheduler.py tick --now 2026-09-25T18:00:00+00:00
python scheduler.py run --poll-seconds 30
python scheduler.py run-now --project caelus --job generate_ru
```

`tick` выполняет один проход и завершается. `run` — foreground loop; он **не устанавливает Windows service и не daemonize процесс**.

`run-now` проходит через тот же Project adapter, lock и state ledger, поэтому не обходит CAELUS safety/precondition rules.

### Runtime state

По умолчанию scheduler metadata хранится в:

```text
runtime/scheduler/<project_id>/<job_id>/
├── state.json
└── job.lock
```

Можно изменить runtime-root переменной окружения:

```powershell
$env:KABAN_RUNTIME_DIR = "C:\KABAN\runtime"
```

Один сбой Project/job не останавливает остальные due jobs того же tick.

## Production 24/7 через Docker

Начиная с v1.13 KABAN может работать на Docker-capable Linux host как два независимых application service (`scheduler` и Review Console) за защищённым Caddy gateway. Persistent данные находятся в `data/generated` и `data/runtime` и переживают restart/reboot контейнеров.

Полная пошаговая инструкция: `deploy/README_PRODUCTION_RU.md`.

Локальные Windows-команды (`python run_daily.py`, `python admin_app.py`, `python scheduler.py ...`) остаются поддерживаемыми и не требуют Docker.

## Stage 4 — KABAN Serverless Runtime

KABAN поддерживает два runtime режима без дублирования Project business logic:

```text
local/Docker -> generated/ + runtime/
cloud        -> Supabase PostgreSQL + Cloudflare R2 + ephemeral GitHub workspace
```

Local/Docker остаётся режимом по умолчанию и не требует cloud credentials. Для cloud deployment используйте `deploy/README_SERVERLESS_RU.md`.

Основные cloud-команды:

```bash
python cloud_runtime.py sync-config --project caelus --horizon-days 35
python cloud_runtime.py status --project caelus
python cloud_runtime.py health --project caelus
```

Stage 4 не включает CAELUS EN production, Instagram или auto-approve.
