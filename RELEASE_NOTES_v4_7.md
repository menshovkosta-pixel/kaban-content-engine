# CAELUS Generator v4.7 — Release Notes

## Что было обнаружено в v4.6

- `Generate tomorrow` уже существовал, но напрямую запускал legacy generator без provider abstraction.
- OpenAI integration находилась внутри `generate_forecasts.py`, из-за чего AI provider был связан с генерацией/CLI.
- `admin_app.py` совмещал HTTP/UI, storage, generation, approval и запуск publication subprocess.
- Telegram progress различал в основном `publishing/published`; ошибки могли оставлять неясное состояние.
- Не было отдельной append-only истории попыток публикации.
- Защита от повторной публикации была привязана к hash, а не к факту уже опубликованного дня.
- При редактировании пересобирались все 12 карточек.
- Старый EN mock dataset содержал фактически одинаковые прогнозы для всех знаков.
- `setup_windows.bat` создавал `.venv`, а `start_admin.bat` использовал глобальный Python.
- `test_without_api.bat` мог использовать текущую дату и перезаписать рабочий dataset.
- README содержал устаревшие фрагменты и старые примеры Telegram target.

## Что сделано в v4.7

- Добавлен `content_engine/` с provider abstraction: OpenAI / mock / расширяемый contract.
- Введена schema v2: `card/general/love/career_money/advice`.
- Сохранена совместимость со старыми `overview/relationships/work_money`.
- Добавлены `caelus/config.py`, `storage.py`, `workflow.py`, `publication.py`.
- `Generate tomorrow` использует реальную календарную дату завтра и не перегенерирует существующий день.
- Добавлены validation errors и quality warnings, включая контроль повторов.
- Изменённый знак пересобирает только соответствующую карточку.
- Approval блокируется при validation errors.
- Publisher повторно валидирует dataset и approved hash.
- Статусы: `DRAFT`, `APPROVED`, `PUBLISHING`, `PARTIALLY PUBLISHED`, `PUBLISHED`, `FAILED`.
- `publication.json` сохраняет progress после каждого шага.
- `publication_history.json` хранит историю попыток/шагов.
- После частичного сбоя retry не отправляет подтверждённые альбомы/тексты повторно.
- Опубликованный день блокируется от случайной повторной отправки через обычный workflow.
- Добавлена понятная конфигурация `.env` без вывода секретов.
- Исправлены Windows launch scripts и добавлены unit tests.
