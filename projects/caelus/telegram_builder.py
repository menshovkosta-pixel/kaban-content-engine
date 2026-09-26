from __future__ import annotations

import argparse
import json
from pathlib import Path

from projects.caelus.domain import SIGN_ORDER
from projects.caelus.validators import get_field

GLYPHS = {
    "aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
    "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
    "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒", "pisces": "♓",
}
LABELS = {
    "ru": {"rel": "Отношения", "work": "Работа и деньги", "advice": "Совет дня", "title": "Прогноз CAELUS"},
    "en": {"rel": "Relationships", "work": "Work & money", "advice": "Daily note", "title": "CAELUS Forecast"},
}


def sign_block(sign: str, item: dict, language: str) -> str:
    labels = LABELS[language]
    return (
        f"{GLYPHS[sign]} **{item['name']}**\n\n"
        f"{get_field(item, 'general')}\n\n"
        f"❤️ **{labels['rel']}**\n{get_field(item, 'love')}\n\n"
        f"💼 **{labels['work']}**\n{get_field(item, 'career_money')}\n\n"
        f"✨ **{labels['advice']}**\n{get_field(item, 'advice')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CAELUS: подготовка Telegram-текстов")
    parser.add_argument("content", help="content.json")
    parser.add_argument("--out", help="Папка результата")
    args = parser.parse_args()

    path = Path(args.content)
    data = json.loads(path.read_text(encoding="utf-8"))
    language = data["language"]
    labels = LABELS[language]
    out_dir = Path(args.out) if args.out else path.parent / "telegram"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Удаляем старые batch-файлы, чтобы уменьшение количества batch не оставляло мусор.
    for old in out_dir.glob("batch_*.md"):
        old.unlink()

    blocks = []
    for idx, sign in enumerate(SIGN_ORDER, start=1):
        block = sign_block(sign, data["signs"][sign], language)
        blocks.append(block)
        (out_dir / f"{idx:02d}_{sign}.md").write_text(block + "\n", encoding="utf-8")

    header = f"**{labels['title']} · {data['date']}**\n\n"
    batches: list[str] = []
    current = header
    for block in blocks:
        candidate = current + block + "\n\n———\n\n"
        if len(candidate) > 3600 and current != header:
            batches.append(current.rstrip("\n— "))
            current = header + block + "\n\n———\n\n"
        else:
            current = candidate
    if current.strip() != header.strip():
        batches.append(current.rstrip("\n— "))

    for i, batch in enumerate(batches, start=1):
        (out_dir / f"batch_{i:02d}.md").write_text(batch + "\n", encoding="utf-8")
    print(f"[OK] Telegram: {out_dir} ({len(batches)} batch-файла)")


if __name__ == "__main__":
    main()
