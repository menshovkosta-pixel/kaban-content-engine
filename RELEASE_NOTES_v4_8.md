# CAELUS Generator v4.8 — Release Notes

## Главная функция

Добавлен **Content Uniqueness Engine** с 90-дневной защитой от повторяющихся прогнозов.

## Что изменено

- История по умолчанию увеличена с 7 до 90 календарных дней.
- Историческое сравнение выполняется по `sign + field`, без ложного сравнения одного знака с другим.
- Отдельно проверяются `card`, `general`, `love`, `career_money`, `advice`.
- Добавлены нормализация, Sequence similarity, token cosine/Jaccard и character n-gram similarity.
- Exact duplicate всегда блокируется.
- По умолчанию 76–86% даёт warning, >=86% — blocking error.
- После полной генерации конфликтующие знаки автоматически перегенерируются точечно до 3 раз.
- Если конфликты не устранены, день сохраняется как DRAFT, но approval/publish блокируются.
- OpenAI provider получил отдельный `generate_sign()` contract и JSON schema для одного знака.
- Mock provider поддерживает regeneration workflow без API.
- В Review Console добавлен блок `Content uniqueness`.
- На каждой карточке появился `Regenerate` для точечной замены одного знака.
- При ручной regeneration остальные несохранённые правки сначала сохраняются.
- Approval повторно проверяет 90-дневную уникальность.
- Telegram publisher повторно проверяет уникальность непосредственно перед отправкой.
- Настройки uniqueness вынесены в `.env.example`.
- CLI `--history-days` теперь по умолчанию использует 90 дней.
- Добавлены тесты календарного окна, same-sign scope, exact duplicate, auto-regeneration и regeneration exhaustion.

## Backward compatibility

Старые datasets с `overview`, `relationships`, `work_money` продолжают читаться через compatibility aliases. Существующая структура `generated/YYYY-MM-DD/...`, card renderer и Telegram publication progress не менялись.
