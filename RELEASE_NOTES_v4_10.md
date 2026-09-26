# CAELUS Generator v4.10 — Targeted Regeneration & 80% Anti-Repeat

## Главное

- Исправлен фактический дефект v4.9: ручной `Regenerate` не передавал AI similarity-warning, поэтому при 81% конфликтующий исторический текст мог отсутствовать в prompt и новый вариант мог остаться практически тем же.
- Историческое сходство **>=80%** теперь является blocking repetition error и автоматически запускает regeneration.
- Автоматическая regeneration стала field-level: переписывается только конфликтующий `card/general/love/career_money/advice`, а не весь знак.
- AI получает точный текущий текст и точный исторический match. Prompt требует новую тему, новую ситуацию и новый практический вывод, а не перефразирование.
- Кандидат с similarity >=80% к заменяемому тексту не принимается и запускает следующую попытку (до `CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS`, по умолчанию 3).
- В Review Console у каждого текстового поля появилась отдельная кнопка `Regenerate`; `Regenerate sign` остаётся для полного нового варианта одного знака.
- Для field-level regeneration `love/general/career_money/advice` карточка PNG не пересобирается; обновляются Telegram artifacts. Для `card` пересобирается только PNG выбранного знака.
- В блоке Content uniqueness отображается активный порог auto-fix.

## Порог по умолчанию

```env
CONTENT_HISTORY_DAYS=90
CONTENT_UNIQUENESS_WARNING_THRESHOLD=0.76
CONTENT_UNIQUENESS_HARD_THRESHOLD=0.80
CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS=3
```

## Миграция с v4.9

Используйте `migrate_from_v4_9.bat`. Скрипт переносит `.env` и `generated`, а также обновляет старый `CONTENT_UNIQUENESS_HARD_THRESHOLD` до `0.80`, чтобы локальный `.env` не сохранял поведение v4.9 с порогом 86%.

## Совместимость

Формат `content.json` schema v2 и legacy aliases сохранены. Card renderer и Telegram publisher не переписывались.
