# KABAN Content Engine v1.3 — Storage Core

## Что изменено

- Общие JSON storage primitives вынесены в `kaban/storage/`.
- В KABAN Core теперь находятся:
  - `load_json`;
  - атомарный `write_json`;
  - стабильный `content_hash`.
- `caelus/storage.py` сохраняет старые публичные imports как compatibility facade.
- `publish_telegram.py` использует те же primitives напрямую из KABAN Core.

## Что намеренно не изменено

- layout `generated/YYYY-MM-DD/<language>/`;
- `content.json`, `status.json`, `publication.json`, `publication_history.json`;
- CAELUS day/language validation;
- publication event semantics;
- Review Console;
- AI generation, uniqueness, renderer и Telegram publication strategy.

## Архитектурная граница

KABAN Core отвечает только за универсальное файловое JSON-хранилище. CAELUS определяет, где и под какими именами лежат его datasets. Будущие Projects могут использовать `kaban.storage` без зависимости от CAELUS.
