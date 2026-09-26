# KABAN Production Deployment Stage 3 — v1.13

## Что добавлено

- provider-neutral Docker Compose deployment для KABAN;
- один non-root Python image, используемый независимыми `scheduler` и `admin` services;
- authenticated Caddy gateway; `admin:8088` не публикуется напрямую;
- `GET /healthz` для Review Console;
- scheduler heartbeat и `python scheduler.py health`;
- persistent host-backed `data/generated` и `data/runtime`;
- безопасный one-time bootstrap shipped `generated/` без overwrite host data;
- `restart: unless-stopped` и bounded Docker log rotation;
- `.dockerignore`/release hygiene, исключающие secrets, runtime state и caches;
- Linux production runbook с backup, reboot и non-destructive upgrade workflow.

## CAELUS

Production schedule не менялся:

- timezone `Pacific/Auckland`;
- `generate_ru` — `06:00`;
- `publish_ru` — `08:00`;
- публикация по-прежнему требует ручного Approve;
- EN jobs не включены.

## Совместимость

Локальный Windows workflow остаётся поддерживаемым: `run_daily.py`, `admin_app.py`, `scheduler.py` работают вне Docker как прежде.

## Docker runtime smoke в verification environment

**NOT AVAILABLE IN VERIFICATION ENVIRONMENT** — Docker daemon отсутствует. Static Dockerfile/Compose contracts и YAML parsing проверены автоматическими тестами; live container smoke не заявляется как PASS.
