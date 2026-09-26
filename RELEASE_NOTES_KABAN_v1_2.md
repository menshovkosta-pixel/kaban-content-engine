# KABAN Content Engine v1.2 — Telegram Core Transport

## Что изменено

- Telegram Bot API transport вынесен из `publish_telegram.py` в `kaban/publishing/telegram.py`.
- В KABAN Core теперь находятся:
  - `TelegramClient`;
  - form POST для Bot API;
  - `getChat`;
  - `sendMessage`;
  - `sendMediaGroup`;
  - multipart/form-data builder;
  - network timeout/retry;
  - обработка Telegram HTTP/API errors.
- `publish_telegram.py` остаётся CAELUS-specific orchestration-слоем.

## Что не изменено

- два альбома CAELUS по 6 карточек;
- текстовые batches;
- approval/content hash;
- publication journal и resume;
- `--start-album`, `--force`, `--dry-run`;
- Review Console;
- storage layout;
- генерация карточек и project assets.

## Архитектурная граница

Будущие Projects используют `kaban.publishing.telegram.TelegramClient` напрямую и не зависят от CAELUS publisher.
