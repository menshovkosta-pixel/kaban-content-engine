# CAELUS Production Automation — Stage 1 · v1.11

## Добавлено

- derived automation states: `pending_generation`, `generation_failed`, `review_required`, `approval_invalid`, `ready_to_publish`, `publishing`, `publish_failed`, `published`;
- `generated/_automation/caelus/.../run.json` с безопасным журналом execution attempts;
- idempotent generation: существующий dataset не перезаписывается без `--force`;
- transactional generation rollback: неудачная новая генерация удаляет partial dataset, forced-generation восстанавливает точный предыдущий dataset;
- Windows-compatible exclusive operation locks (`generate.lock`, `publish.lock`);
- error sanitization для API keys, Telegram tokens и credential-bearing URLs;
- idempotent publication wrapper: published dataset не отправляется повторно, partial/failed publication делегируется существующему resume-механизму;
- automation status в Review Console и automation-aware History;
- operator CLI: `status`, `generate`, `publish`;
- ручные `run_daily.py` и `publish_telegram.py` сохранены без скрытого redirect через automation.

## Не входит в Stage 1

- cron/timer scheduler;
- background worker/queue;
- автоматический retry loop;
- auto-approve;
- автоматическая публикация по времени;
- distributed locking.

Stage 1 создаёт только надёжный фундамент для следующего этапа scheduler automation.
