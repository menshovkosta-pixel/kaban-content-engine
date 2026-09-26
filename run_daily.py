from __future__ import annotations

import argparse
import os
from datetime import date

from projects.caelus.config import load_dotenv, openai_model
from projects.caelus.storage import day_dir
from projects.caelus.workflow import generate_bundle


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Полный ежедневный конвейер CAELUS")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--language", choices=["ru", "en"], default="ru")
    parser.add_argument("--model", default=os.getenv("CAELUS_OPENAI_MODEL", openai_model()))
    parser.add_argument("--mock", action="store_true", help="Запуск без API для проверки")
    args = parser.parse_args()

    mode = "mock" if args.mock else "ai"
    generate_bundle(args.date, args.language, mode, model=args.model)

    base = day_dir(args.date, args.language)
    print("\nГотово:")
    print(f"  Контент:  {base / 'content.json'}")
    print(f"  Карточки: {base / 'cards'}")
    print(f"  Telegram: {base / 'telegram'}")
    print(f"  Статус:   {base / 'status.json'}")


if __name__ == "__main__":
    main()
