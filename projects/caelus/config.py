from __future__ import annotations

import os
from pathlib import Path

from kaban.projects import active_project

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> bool:
    path = path or ROOT / ".env"
    if not path.is_file():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def openai_model() -> str:
    return os.getenv("CAELUS_OPENAI_MODEL", active_project().ai.model)



def openai_reasoning_effort() -> str:
    value = os.getenv(
        "CAELUS_OPENAI_REASONING_EFFORT",
        active_project().ai.reasoning_effort,
    ).strip().lower()
    allowed = {"none", "low", "medium", "high", "xhigh", "max"}
    if value not in allowed:
        raise ValueError(
            "CAELUS_OPENAI_REASONING_EFFORT должен быть одним из: " + ", ".join(sorted(allowed))
        )
    return value


def openai_config_message() -> str | None:
    if os.getenv("OPENAI_API_KEY"):
        return None
    env_path = ROOT / ".env"
    prefix = "Файл .env не найден. " if not env_path.exists() else "В .env не задан OPENAI_API_KEY. "
    return prefix + (
        "Создайте/отредактируйте .env рядом с admin_app.py и задайте OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>. "
        "Реальный API key не добавляйте в ZIP и не отправляйте в чат."
    )


def telegram_config_message() -> str | None:
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        return None
    env_path = ROOT / ".env"
    prefix = "Файл .env не найден. " if not env_path.exists() else "В .env не хватает Telegram-настроек. "
    return prefix + (
        "Создайте/отредактируйте .env рядом с admin_app.py и задайте TELEGRAM_BOT_TOKEN=<REAL_TOKEN> и "
        "TELEGRAM_CHAT_ID=-100xxxxxxxxxx. Токен не добавляйте в ZIP и не отправляйте в чат."
    )


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом") from exc
    if value < minimum:
        raise ValueError(f"{name} должен быть >= {minimum}")
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть числом") from exc
    if not 0 < value <= 1:
        raise ValueError(f"{name} должен быть в диапазоне (0, 1]")
    return value


def uniqueness_history_days() -> int:
    return _env_int(
        "CONTENT_HISTORY_DAYS",
        active_project().uniqueness.history_days,
        1,
    )


def uniqueness_warning_threshold() -> float:
    return _env_float(
        "CONTENT_UNIQUENESS_WARNING_THRESHOLD",
        active_project().uniqueness.warning_threshold,
    )


def uniqueness_hard_threshold() -> float:
    return _env_float(
        "CONTENT_UNIQUENESS_HARD_THRESHOLD",
        active_project().uniqueness.hard_threshold,
    )


def uniqueness_max_regeneration_attempts() -> int:
    return _env_int(
        "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS",
        active_project().uniqueness.max_regeneration_attempts,
        0,
    )
