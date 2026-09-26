# KABAN Content Engine — Project Asset Isolation

- Все 26 CAELUS PNG assets перенесены из корня в `projects/caelus/assets/`.
- Добавлен общий resolver `kaban/assets.py`.
- `ProjectRegistry` теперь хранит фактическую директорию каждого обнаруженного Project.
- `generate_cards.py` читает CAELUS background/art/glyphs только через project assets directory.
- Generated storage layout и CAELUS workflow не изменены.
- Перед/после миграции SHA-256 всех 12 карточек совпадают.
