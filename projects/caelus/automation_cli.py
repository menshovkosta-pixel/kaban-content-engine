from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from projects.caelus.automation import derive_state, run_generation_job, run_publication_job
from projects.caelus.config import load_dotenv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAELUS automation operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--date", required=True)
    status.add_argument("--language", choices=["ru", "en"], default="ru")

    generate = sub.add_parser("generate")
    generate.add_argument("--date", required=True)
    generate.add_argument("--language", choices=["ru", "en"], default="ru")
    generate.add_argument("--mock", action="store_true")
    generate.add_argument("--model")
    generate.add_argument("--force", action="store_true")

    publish = sub.add_parser("publish")
    publish.add_argument("--date", required=True)
    publish.add_argument("--language", choices=["ru", "en"], default="ru")
    publish.add_argument("--dry-run", action="store_true")
    publish.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    if args.command == "status":
        state = derive_state(args.date, args.language)
    elif args.command == "generate":
        state = run_generation_job(
            args.date,
            args.language,
            mode="mock" if args.mock else "ai",
            model=args.model,
            force=args.force,
        )
    else:
        state = run_publication_job(args.date, args.language, dry_run=args.dry_run, force=args.force)
    print(json.dumps(asdict(state), ensure_ascii=False, indent=2))
    return 0
