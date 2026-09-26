# CAELUS Generator v4.9 — API Usage & Cost Control

## Что изменено

- OpenAI provider теперь явно использует `reasoning_effort=low` по умолчанию.
- Настройка вынесена в `.env`: `CAELUS_OPENAI_REASONING_EFFORT=low`.
- После каждого успешного OpenAI Chat Completions запроса сохраняются фактические usage-поля API:
  - prompt/input tokens;
  - cached input tokens;
  - completion/output tokens;
  - reasoning tokens;
  - total tokens.
- Usage суммируется для основной генерации, автоматических uniqueness-regeneration и ручной кнопки Regenerate.
- В `generation.api_usage` сохраняется snapshot тарифа и рассчитанная стоимость каждого вызова.
- Review Console показывает расход текущего dataset и накопленный расход текущего календарного месяца.
- Reasoning tokens отображаются отдельно, но не прибавляются второй раз к output: это подмножество completion/output tokens.
- Для неизвестной/сменённой модели токены продолжают учитываться; стоимость показывается как недоступная, если в CAELUS нет тарифа для модели.
- Тариф GPT-5.6 Luna зафиксирован в v4.9 как snapshot на 2026-09-23: $0.20/1M input, $0.02/1M cached input, $1.20/1M output.

## Совместимость

Старые datasets v4.8 и ранее остаются читаемыми. У них просто нет `generation.api_usage`; статистика появится после новых AI-генераций или Regenerate через v4.9.
