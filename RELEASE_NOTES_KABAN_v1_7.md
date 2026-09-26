# KABAN Content Engine v1.7 — Generator / AI Separation

## Что изменено

- Generic OpenAI strict-JSON transport перенесён в `kaban/ai/openai.py`.
- Token/cost accounting перенесён в `kaban/ai/usage.py`.
- CAELUS-specific `OpenAIProvider`, `MockProvider` и provider contract перенесены в `projects/caelus/provider.py`.
- CAELUS generation/regeneration orchestration перенесена в `projects/caelus/generator.py`.
- `caelus/workflow.py` и `generate_forecasts.py` используют Project generator/provider напрямую.
- `content_engine/generator.py`, `provider.py`, `usage.py` оставлены как compatibility facades.
- `content_engine/__init__.py` переведён на lazy compatibility exports, чтобы Project modules могли использовать оставшиеся legacy validation/uniqueness modules без циклического импорта.

## Архитектурная граница

KABAN AI Core получает только prompts + caller-supplied JSON Schema и возвращает generic dict. Он не знает о CAELUS, zodiac signs, forecast fields или CAELUS schema names.

CAELUS Project отвечает за выбор schema, mock content и весь generation/regeneration workflow.

## Что намеренно не изменено

- prompts и JSON Schemas;
- validation/uniqueness rules;
- renderer;
- storage layout;
- Review Console;
- Telegram publication strategy.

## Регрессия

- полный test suite: 78 tests;
- mock-day v1.6 → v1.7: 12 cards + 14 Telegram artifacts, 26/26 SHA-256 identical;
- approval: 0 errors;
- Telegram dry-run: 6 + 6 cards, 2 text batches.
