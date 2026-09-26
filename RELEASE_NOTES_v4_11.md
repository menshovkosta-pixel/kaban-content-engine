# CAELUS Generator v4.11 — Adaptive Content Diversity

## Главное

В Review Console добавлено пользовательское управление уровнем уникальности без ручного редактирования `.env`.

### Профили

- **Soft** — historical hard threshold 88%, same-day 90%.
- **Balanced** — historical hard threshold 84%, same-day 87%.
- **Strict** — historical hard threshold 80%, same-day 84%. Профиль по умолчанию.
- **Very Strict** — historical hard threshold 75%, same-day 80%.
- **Custom** — пользователь выбирает historical threshold 70–90%; warning и same-day threshold рассчитываются автоматически.

Окно истории выбирается отдельно: 30 / 60 / 90 / 180 / 365 дней.

## Безопасный Preview

Кнопка **«Проверить без API»**:

1. сохраняет выбранные настройки;
2. локально пересчитывает similarity по текущему dataset и истории;
3. показывает найденные конфликты;
4. не создаёт AI provider и не расходует API-токены.

## Regenerate conflicts

Кнопка **Regenerate conflicts** вызывает AI только для полей, которые являются hard uniqueness conflicts при текущем профиле.

Например, если конфликт найден только для `Pisces · love`, остальные четыре поля Pisces и остальные 11 знаков не перегенерируются.

## Semantic metadata groundwork

Новые AI datasets сохраняют служебный объект `_diversity` для каждого знака:

```json
{
  "theme": "...",
  "situation": "...",
  "tone": "...",
  "advice_pattern": "..."
}
```

Он не выводится на карточку и не публикуется в Telegram. Это подготовка к будущему semantic/topic diversity engine. Если отдельное поле позднее перегенерировано, metadata помечается через `stale_fields`, чтобы в будущем не использовать устаревшую смысловую разметку как достоверную.

## Совместимость

- Старые datasets без `_diversity` продолжают открываться и валидироваться.
- Card renderer не менялся.
- Telegram publisher не менялся.
- API usage/cost accounting v4.9+ сохраняется.
- Approval/Publication workflow сохраняется.

## Хранение настроек

Пользовательские настройки сохраняются в:

```text
generated/_settings/content_diversity.json
```

Поэтому они автоматически переносятся вместе с `generated`.
