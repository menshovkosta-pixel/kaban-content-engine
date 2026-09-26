# KABAN Content Engine v1.5 — CAELUS Domain/Schema Isolation

## Что изменено

- CAELUS domain перенесён в `projects/caelus/domain.py`:
  - порядок 12 знаков;
  - RU/EN названия знаков;
  - формат отображаемой даты;
  - поля прогноза;
  - diversity metadata fields;
  - ограничения длины полей.
- CAELUS JSON Schema builders перенесены в `projects/caelus/schemas.py`.
- `content_engine/schemas.py` оставлен только как compatibility re-export для старых imports/tests.
- Production-код больше не импортирует CAELUS domain через `content_engine.schemas`.
- `generate_cards.py` и `publish_telegram.py` используют единый `projects.caelus.domain.SIGN_ORDER`; renderer также использует единый словарь RU-названий.
- `projects/caelus/prompts.py` больше не зависит от legacy `content_engine.schemas`.

## Архитектурная граница

`projects/caelus/domain.py` и `projects/caelus/schemas.py` принадлежат только CAELUS Project. KABAN Core (`kaban/*`) не импортирует их и не знает о знаках зодиака, названиях полей прогноза или CAELUS JSON Schema.

`content_engine/*` всё ещё является legacy CAELUS orchestration. Его прямые imports из `projects.caelus` — временный seam до последующего переноса generator/validation/uniqueness под Project.

## Что намеренно не изменено

- тексты prompts;
- provider contract;
- правила uniqueness;
- Review Console;
- изображения и renderer layout;
- storage layout;
- Telegram transport и publication strategy;
- формат content.json.

## Регрессия

- domain/schema contract v1.4 → v1.5 идентичен;
- 12 карточек и 14 Telegram artifacts при одинаковом mock input идентичны побайтно;
- существующие datasets не требуют миграции.
