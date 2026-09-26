# KABAN Content Engine v1.8 — Workflow + Publication Strategy Isolation

## Что изменено

- CAELUS application workflow перенесён в `projects/caelus/workflow.py`.
- CAELUS Telegram publication strategy перенесена в `projects/caelus/publication.py`.
- CAELUS Telegram text builder перенесён в `projects/caelus/telegram_builder.py`.
- `admin_app.py` и `run_daily.py` используют Project API напрямую.
- Внутренний workflow вызывает `python -m projects.caelus.telegram_builder`, а не корневой compatibility script.
- `caelus/workflow.py` и `caelus/publication.py` сохранены как module aliases для обратной совместимости и корректного monkeypatch старых tests/integrations.
- Корневые `build_telegram.py` и `publish_telegram.py` сохранены как тонкие compatibility CLI.

## Архитектурная граница

KABAN Core по-прежнему отвечает только за общие сервисы: AI transport/usage, JSON storage primitives и Telegram transport. Правила CAELUS — 12 знаков, validation/uniqueness orchestration, approval, 6+6 albums, text batches, publication journal/resume — принадлежат `projects/caelus/`.

## Что намеренно не изменено

- storage layout `generated/YYYY-MM-DD/<language>/`;
- config/env compatibility layer;
- validation/uniqueness algorithms;
- prompts/schemas/domain;
- renderer;
- Review Console UI;
- Telegram transport;
- publication state machine и journal format.

## Regression contract

- legacy CLI остаются рабочими;
- Project CLI дают эквивалентный результат;
- mock daily output должен быть побайтно идентичен v1.7;
- approval и Telegram dry-run должны сохранять прежний publication plan.
