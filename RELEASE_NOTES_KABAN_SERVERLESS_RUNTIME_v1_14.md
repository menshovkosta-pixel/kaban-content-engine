# KABAN Serverless Runtime Stage 4 — v1.14

## Что добавлено

- provider-neutral cloud runtime в `kaban/cloud/` без переноса CAELUS business rules в Core;
- Supabase PostgreSQL canonical control state с `project_id` isolation, composite relationships, RLS/RPC contracts, leases и fencing tokens;
- immutable Cloudflare R2 artifacts/backups с project-prefixed object keys и SHA-256 verification;
- ephemeral workspace bridge для GitHub Actions;
- CAELUS-specific cloud adapter в `projects/caelus/`;
- commit-last generation и revision CAS;
- absolute UTC schedule projection с Auckland DST и 35-дневным horizon;
- Cloudflare Cron orchestration, которое не запускает GitHub Action при blocked approval;
- Telegram publication checkpoints: `sent` не пересылается, ambiguous delivery -> `unknown_delivery` без auto-retry;
- Cloudflare Access protected Review Console API;
- restartable migration local -> cloud и cloud -> local export;
- application-level R2 backups, health и usage monitoring с quality labels `provider_exact` / `db_exact` / `estimated`;
- quota warning levels 70% / 85% / 95%; автоматический upgrade тарифа и автоматическое удаление canonical content отсутствуют;
- `sync-config`, `status`, `health` CLI для non-secret project/scheduler config mirror.

## Сохранённая совместимость

Local/Docker остаётся first-class runtime. Cloud credentials не требуются при `KABAN_PERSISTENCE=local` (default). Текущий CAELUS production schedule не менялся:

- timezone `Pacific/Auckland`;
- `generate_ru` — 06:00;
- `publish_ru` — 08:00;
- manual approval обязателен;
- EN production jobs не включены.

Детерминированная compatibility-проверка на чистом v1.13 и v1.14 для `2030-01-15 --language ru --mock`:

- 26/26 user-facing artifacts byte-identical;
- `content.json.signs` semantic payload identical;
- различия только в runtime timestamps `content.json/status.json`, которые не входят в 26 user-facing artifact contract.

## Verification 2026-09-26

### Python

- Stage 4 acceptance/deployment focused: **28/28 PASS**.
- Full pytest: **288 PASS, 2 SKIPPED**.
- unittest discover: **92/92 PASS**.
- `2 SKIPPED` — live Supabase schema/RPC acceptance и live R2 acceptance без provider credentials.

### Cloudflare Worker

- `npm ci`: **PASS** (`0 vulnerabilities`).
- Worker tests: **33/33 PASS**.
- TypeScript `tsc -p tsconfig.json`: **PASS**.

### Provider live acceptance

В verification environment credentials отсутствуют, поэтому следующие проверки имеют статус **NOT RUN**, не PASS:

- Supabase migration/RPC live contract — NOT RUN;
- R2 put/head/get/hash/delete live object — NOT RUN;
- deployed Cloudflare Worker `/healthz` + Access route — NOT RUN;
- deployed Cron orchestration — NOT RUN;
- GitHub `workflow_dispatch` — NOT RUN;
- cloud materialize -> CAELUS mock generate -> commit against real providers — NOT RUN;
- cloud Review Console against deployed providers — NOT RUN;
- Telegram dry-run against deployed cloud runtime — NOT RUN;
- controlled real Telegram send — NOT RUN and requires explicit user authorization.

Docker command/daemon в verification environment отсутствует; live Docker smoke — **NOT RUN**. Static Docker/Compose contracts входят в passing Python regression.

## Production cutover

Production cutover этим релизом автоматически не выполняется. Следуйте `deploy/README_SERVERLESS_RU.md`. Реальную Telegram отправку разрешается выполнять только отдельным явно подтверждённым действием.
