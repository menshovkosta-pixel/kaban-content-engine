# CAELUS Production Schedule — v1.12.1

## Что изменено

В универсальном KABAN Project Scheduler включён production schedule только для Project `caelus`. Scheduler Core и CAELUS adapter не изменялись.

Расписание (`Pacific/Auckland`):

- `generate_ru` — `0 6 * * *`; AI mode, RU, текущий локальный день;
- `publish_ru` — `0 8 * * *`; RU, текущий локальный день; publication требует существующий approved dataset.

EN jobs отсутствуют.

## Надёжность

Generation:
- misfire grace: 30 минут;
- retry: каждые 10 минут;
- retry window: 120 минут;
- максимум 12 повторов после первой попытки.

Publication:
- misfire grace: 30 минут;
- при отсутствии approval job остаётся blocked;
- retry: каждые 10 минут;
- retry window: 180 минут;
- максимум 18 повторов после первой попытки.

Auto-approve отсутствует. Exactly-once slot semantics, Project isolation, locks и runtime ledger обеспечиваются KABAN Scheduler v1.12.

## Эксплуатация

Расписание выполняется только пока scheduler runner работает:

```powershell
python scheduler.py run --poll-seconds 30
```

Для проверки без запуска job:

```powershell
python scheduler.py list
python scheduler.py status --project caelus
```
