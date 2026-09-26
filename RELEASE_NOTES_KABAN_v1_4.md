# KABAN Content Engine v1.4 — Project Prompt Isolation

## Что изменено

- Все CAELUS-specific prompt-тексты перенесены из `content_engine/prompts.py` в `projects/caelus/prompts.py`.
- `content_engine/generator.py` использует prompts первого Project через переходный import seam.
- Старый `content_engine/prompts.py` удалён, чтобы CAELUS branding и астрологические инструкции не находились в legacy engine-папке.
- KABAN Core (`kaban/*`) не получил зависимости от CAELUS prompts.

## Что намеренно не изменено

- текст и смысл RU/EN prompts;
- targeted sign/field regeneration prompts;
- provider contract;
- content schema и uniqueness;
- renderer;
- storage layout;
- Telegram publication strategy;
- Review Console.

## Совместимость

`content_engine/generator.py -> projects.caelus.prompts` является временным compatibility seam. Сам `content_engine` пока остаётся частью legacy CAELUS orchestration и будет переноситься в Project отдельными безопасными шагами.
