# KABAN Project Scheduler Stage 2 — v1.12

## Что добавлено

- универсальный `kaban.scheduler` для всех KABAN Projects;
- five-field cron + Project timezone;
- stable UTC slot identity и exactly-once completed-slot semantics;
- misfire grace;
- blocked/error retries в пределах retry window;
- per-job runtime state и bounded history;
- local file lease locks + expired-lock recovery;
- dynamic `module:function` Project adapters;
- failure isolation между Projects/jobs;
- universal `scheduler.py` CLI: `list`, `status`, `tick`, `run`, `run-now`;
- первый Project adapter: `projects.caelus.scheduler` поверх v1.11 automation.

## Безопасность

- generic `*_KEY` / `*_TOKEN`, bearer и Telegram bot URL secrets санитизируются;
- `params` не копируются в runtime state/history;
- corrupt state/lock fail safe;
- KABAN Core не импортирует CAELUS.

## CAELUS

Shipped config:

```yaml
automation:
  enabled: false
  adapter: "projects.caelus.scheduler:run_job"
  jobs: []
```

v1.12 **не придумывает production schedule** и ничего не запускает автоматически после распаковки.

Scheduled `generate` и `publish` используют Stage 1 `run_generation_job` / `run_publication_job` с `force=False`. Auto-approve отсутствует.

## Совместимость

Ручные команды v1.11 сохранены:

```text
run_daily.py
automation.py
publish_telegram.py
admin_app.py
```

Mock regression v1.11 → v1.12: 26/26 user-facing artifacts byte-identical на одинаковом clean history.

## Dependency

Добавлен `croniter>=6.0`. В минимальном/offline окружении Core содержит ограниченный five-field fallback, но production installation должна устанавливать зависимости через `requirements.txt`.

## Не входит в Stage 2

- Windows service installer;
- cloud deployment;
- generic web dashboard;
- auto-approval;
- hard-coded production times;
- distributed multi-host locking;
- Instagram/TikTok.
