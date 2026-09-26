# KABAN Content Engine v1.9 — Final Cleanup

Финальный этап миграции CAELUS под KABAN.

## Изменения

- удалены legacy production packages `caelus/` и `content_engine/`;
- `config.py` и CAELUS storage layout перенесены в `projects/caelus/`;
- validation/quality/uniqueness полностью принадлежат CAELUS Project;
- production entrypoints импортируют Project API напрямую;
- добавлены явные package markers `projects/__init__.py` и `projects/caelus/__init__.py`;
- KABAN Core остаётся project-agnostic;
- исторический `generated/YYYY-MM-DD/<language>/` layout не менялся;
- root CLI для cards/Telegram сохранены для совместимости пользовательских команд.

## Архитектурный инвариант

```text
root entrypoints -> projects/caelus -> kaban
```

Обратная зависимость `kaban -> CAELUS` запрещена.

## Regression target

Финальный release должен сохранять пользовательский output v1.8 побайтно для deterministic mock pipeline и проходить полный suite, regeneration, Review Console, approval и Telegram dry-run.
