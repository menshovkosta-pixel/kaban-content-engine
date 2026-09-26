# CAELUS Product Phase 1.1 — Review Console / Uniqueness UX v1.10

## Что изменено

- добавлен Uniqueness Inspector с current/matched text, датой, sign/field, similarity и severity;
- добавлены быстрые hard-threshold пресеты 70 / 80 / 90 / 95% и Custom 70–95%;
- history window остаётся 30 / 60 / 90 / 180 / 365 дней;
- смена threshold/history выполняется локально и не вызывает AI;
- для каждого similarity issue доступен Regenerate this field;
- после точечной, sign и массовой conflict-regeneration показываются attempts и similarity до/после;
- Approve All при errors показывает явную причину блокировки и ссылки на поля;
- `card_target_length` скрыт из Review Console, но внутренняя проверка сохранена;
- KABAN Core, generator prompts, renderer и Telegram publication strategy не менялись.

## Совместимость

Структура generated/, content.json, cards и Telegram artifacts не меняется.
