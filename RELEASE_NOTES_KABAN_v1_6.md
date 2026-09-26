# KABAN Content Engine v1.6 — CAELUS Renderer Isolation

## Что изменено

- CAELUS rendering implementation перенесён из корневого `generate_cards.py` в `projects/caelus/renderer.py`.
- Корневой `generate_cards.py` оставлен как тонкий compatibility CLI/re-export для старых внешних команд.
- Внутренний `caelus/workflow.py` вызывает renderer напрямую через `python -m projects.caelus.renderer`, поэтому production workflow больше не зависит от compatibility wrapper.
- Renderer использует `projects.caelus.domain` и project-scoped assets через `kaban.assets.project_assets_dir("caelus")`.
- Относительные CLI-пути для `data` и `--out` по-прежнему разрешаются относительно корня приложения, как в v1.5.

## Архитектурная граница

Вся логика построения карточки CAELUS — PIL, layout, fonts, zodiac art/glyph loading, wrapping и PNG output — теперь принадлежит CAELUS Project. KABAN Core не знает о визуальном layout CAELUS.

`generate_cards.py` не содержит rendering implementation и существует только для backward compatibility.

## Что намеренно не изменено

- layout и размеры карточек;
- PNG compression;
- fonts/fallback policy;
- project assets;
- workflow, prompts, schemas и uniqueness;
- storage layout;
- Telegram publication strategy.

## Регрессия

- 12/12 карточек для одинакового input идентичны v1.5 по SHA-256;
- полный mock-day даёт 12 карточек + 14 Telegram artifacts, 26/26 идентичны v1.5;
- legacy `python generate_cards.py ...` остаётся рабочим.
