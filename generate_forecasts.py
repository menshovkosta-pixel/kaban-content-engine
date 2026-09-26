from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from projects.caelus.config import (
    ROOT,
    load_dotenv,
    openai_model,
    openai_reasoning_effort,
    uniqueness_hard_threshold,
    uniqueness_history_days,
    uniqueness_max_regeneration_attempts,
    uniqueness_warning_threshold,
)
from projects.caelus.storage import generated_root
from projects.caelus.generator import generate_daily_content
from projects.caelus.provider import MockProvider, OpenAIProvider
from projects.caelus.uniqueness.rules import UniquenessRules


def parse_date(value: str | None):
    if not value:
        from datetime import date
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="CAELUS: генерация ежедневных прогнозов")
    parser.add_argument("--date", help="Дата YYYY-MM-DD. По умолчанию сегодня.")
    parser.add_argument("--language", choices=["ru", "en"], default="ru")
    parser.add_argument("--provider", choices=["openai", "mock"], default="openai")
    parser.add_argument("--model", default=openai_model())
    parser.add_argument("--reasoning-effort", default=openai_reasoning_effort(), choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--history-days", type=int, default=uniqueness_history_days())
    parser.add_argument("--mock", action="store_true", help="Совместимый shortcut для --provider mock")
    parser.add_argument("--out", help="Путь к content.json")
    args = parser.parse_args()

    target = parse_date(args.date)
    provider_name = "mock" if args.mock else args.provider
    if provider_name == "mock":
        provider = MockProvider(args.language)
        model = None
    else:
        provider = OpenAIProvider(args.model, reasoning_effort=args.reasoning_effort)
        model = args.model

    rules = UniquenessRules(
        history_days=args.history_days,
        warning_threshold=uniqueness_warning_threshold(),
        hard_threshold=uniqueness_hard_threshold(),
        max_regeneration_attempts=uniqueness_max_regeneration_attempts(),
    ).validate()
    payload = generate_daily_content(
        target=target,
        language=args.language,
        provider=provider,
        generated_dir=generated_root(),
        history_days=args.history_days,
        model=model,
        uniqueness_rules=rules,
    )
    out = Path(args.out) if args.out else generated_root() / target.isoformat() / args.language / "content.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Контент: {out}")
    usage = (payload.get("generation") or {}).get("api_usage")
    if usage:
        cost = usage.get("estimated_cost_usd")
        cost_text = f"${float(cost):.6f}" if cost is not None else "н/д"
        print(f"[API] requests={usage.get('request_count')} input={usage.get('prompt_tokens')} output={usage.get('completion_tokens')} total={usage.get('total_tokens')} cost≈{cost_text}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
