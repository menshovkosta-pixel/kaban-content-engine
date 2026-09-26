# KABAN Serverless Runtime — развёртывание Stage 4

Stage 4 добавляет cloud runtime поверх существующего local/Docker режима. CAELUS business logic не переписывается: GitHub Actions материализует временный workspace, запускает существующий Project workflow и фиксирует результат обратно в Supabase/R2.

## Что где хранится

- Supabase PostgreSQL — каноническое состояние, approvals, scheduler/execution/publication state, uniqueness/history metadata.
- Cloudflare R2 — immutable PNG/assets и application backups.
- Cloudflare Worker — Review Console API, Access auth, Cron orchestration.
- GitHub Actions — тяжёлые Python jobs и Project workflow.
- Local/Docker — полностью сохраняется; cloud credentials для него не нужны.

## Обязательный порядок первого развёртывания

1. Создать один Supabase Project для всех KABAN Projects.
2. Применить SQL migrations по имени файла из `deploy/supabase/migrations/` в лексикографическом порядке.
3. Создать один private Cloudflare R2 bucket для окружения.
4. Настроить Cloudflare Worker variables/secrets и Cloudflare Access application/policy.
5. Настроить GitHub repository secrets.
6. Развернуть Worker и проверить `/healthz`.
7. Выполнить migration dry-run без изменения source data.
8. Проверить unknown/unmapped report и исправить только реальные несоответствия данных.
9. Выполнить migration apply.
10. Проверить hashes/read-back через `verify-migration`.
11. Выполнить `sync-config` для CAELUS и 35-дневного horizon.
12. Выполнить cloud mock generation acceptance.
13. Approve через cloud Review Console.
14. Выполнить Telegram dry-run acceptance.
15. Реальную Telegram-публикацию выполнять только после явного разрешения пользователя.
16. Сохранить local source backup до принятия cutover.

## Переменные окружения

### Local/Docker

```env
KABAN_PERSISTENCE=local
KABAN_RUNTIME_DIR=/path/to/runtime        # optional
KABAN_GENERATED_DIR=/path/to/generated    # optional
OPENAI_API_KEY=...                        # только AI generation
TELEGRAM_BOT_TOKEN=...                    # только real publication
TELEGRAM_CHAT_ID=...                      # только real publication
```

Supabase/R2/GitHub/Cloudflare credentials здесь не требуются.

### GitHub runner cloud mode

```env
KABAN_PERSISTENCE=cloud
KABAN_SUPABASE_URL=...
KABAN_SUPABASE_SERVICE_KEY=...
KABAN_R2_ENDPOINT_URL=...
KABAN_R2_BUCKET=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
OPENAI_API_KEY=...            # только operations с AI
TELEGRAM_BOT_TOKEN=...        # только publication
TELEGRAM_CHAT_ID=...          # текущий CAELUS RU destination
```

### Cloudflare Worker

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
R2 binding: ARTIFACTS
```

Worker не получает `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN` или `TELEGRAM_CHAT_ID`.

## Первичная синхронизация конфигурации

Из корня репозитория:

```bash
python cloud_runtime.py sync-config --project caelus --horizon-days 35
python cloud_runtime.py status --project caelus
python cloud_runtime.py health --project caelus
```

`sync-config`:

- SHA-256 хэширует `projects/caelus/project.yaml`;
- upsert-ит только non-secret project/job config mirror;
- проецирует 35 дней абсолютных UTC slots;
- не включает EN;
- не меняет текущие `06:00 generate_ru` / `08:00 publish_ru` в `Pacific/Auckland`.

Cloudflare Cron проверяет due work и wait conditions. GitHub Action запускается только когда действительно нужен job/retry; blocked approval не должен расходовать Actions minutes.

## Миграция существующих local data

Сначала только dry-run:

```bash
python cloud_runtime.py migrate-local --project caelus --source generated
```

Применение выполняйте только после просмотра отчёта:

```bash
python cloud_runtime.py migrate-local --project caelus --source generated --apply
python cloud_runtime.py verify-migration --project caelus --manifest <PATH_TO_MANIFEST>
```

Source tree при миграции не удаляется и не переписывается. Не удаляйте локальный backup после apply до принятия cloud cutover.

## Rollback / export

После любой cloud mutation сначала экспортируйте текущее каноническое состояние:

```bash
python cloud_runtime.py export-local --project caelus --destination ./rollback_export
```

Затем запускайте local/Docker на экспортированном `generated/` и сохранённом `runtime/`. **Telegram posts не откатываются**: уже отправленные сообщения являются внешним side effect и требуют отдельного ручного решения.

## Publication safety

- `sent` checkpoint не отправляется повторно.
- ambiguous Telegram timeout переводит шаг в `unknown_delivery`.
- `unknown_delivery` не auto-retry; нужна ручная сверка.
- stale fence не может подтвердить `sent` или canonical commit.
- абсолютное exactly-once для внешнего Telegram API не обещается.

## Usage monitoring

UI/API различает `provider_exact`, `db_exact` и `estimated`. Alert thresholds: 70%, 85%, 95%. KABAN не переключает тариф автоматически и не удаляет canonical content автоматически. Orphan cleanup работает только после grace period и повторной reference-check непосредственно перед delete.

## Acceptance перед production cutover

Обязательные non-live проверки:

```bash
python -m pytest -q
python -m unittest discover -s tests -p "test*.py"
cd deploy/cloudflare/worker
npm ci
npm test
npm run typecheck
```

Provider live проверки отражаются отдельно как PASS или NOT RUN. Отсутствие credentials — это `NOT RUN`, а не PASS.
