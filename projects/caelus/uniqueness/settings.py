from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rules import UniquenessRules

PROFILE_LABELS = {
    "soft": "Soft",
    "balanced": "Balanced",
    "strict": "Strict",
    "very_strict": "Very Strict",
    "custom": "Custom",
}

# Пресеты управляют и историческими, и same-day порогами. История выбирается отдельно.
PROFILE_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    # warning, hard history, hard same-day
    "soft": (0.84, 0.88, 0.90),
    "balanced": (0.80, 0.84, 0.87),
    "strict": (0.76, 0.80, 0.84),
    "very_strict": (0.70, 0.75, 0.80),
}

HISTORY_OPTIONS = (30, 60, 90, 180, 365)


@dataclass(frozen=True)
class DiversitySettings:
    """Пользовательские настройки контроля уникальности контента."""

    profile: str = "strict"
    history_days: int = 90
    custom_threshold: float = 0.80
    max_regeneration_attempts: int = 3

    def validate(self) -> "DiversitySettings":
        if self.profile not in PROFILE_LABELS:
            raise ValueError(f"Неизвестный профиль уникальности: {self.profile}")
        if not 1 <= int(self.history_days) <= 3650:
            raise ValueError("Период истории должен быть от 1 до 3650 дней")
        if not 0.70 <= float(self.custom_threshold) <= 0.95:
            raise ValueError("Custom threshold должен быть в диапазоне 70–95%")
        if int(self.max_regeneration_attempts) < 0:
            raise ValueError("max_regeneration_attempts должен быть >= 0")
        return self

    def to_rules(self) -> UniquenessRules:
        self.validate()
        if self.profile == "custom":
            hard = float(self.custom_threshold)
            warning = max(0.50, hard - 0.06)
            same_day = min(0.95, hard + 0.04)
        else:
            warning, hard, same_day = PROFILE_THRESHOLDS[self.profile]
        return UniquenessRules(
            history_days=int(self.history_days),
            warning_threshold=round(warning, 4),
            hard_threshold=round(hard, 4),
            same_day_hard_threshold=round(same_day, 4),
            max_regeneration_attempts=int(self.max_regeneration_attempts),
        ).validate()

    @property
    def label(self) -> str:
        return PROFILE_LABELS[self.profile]

    def to_dict(self) -> dict[str, Any]:
        rules = self.to_rules()
        return {
            "schema_version": 1,
            "profile": self.profile,
            "history_days": self.history_days,
            "custom_threshold": self.custom_threshold,
            "max_regeneration_attempts": self.max_regeneration_attempts,
            "effective": {
                "warning_threshold": rules.warning_threshold,
                "hard_threshold": rules.hard_threshold,
                "same_day_hard_threshold": rules.same_day_hard_threshold,
            },
        }


def load_diversity_settings(path: Path, fallback: DiversitySettings | None = None) -> DiversitySettings:
    default = (fallback or DiversitySettings()).validate()
    if not path.is_file():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DiversitySettings(
            profile=str(raw.get("profile", default.profile)),
            history_days=int(raw.get("history_days", default.history_days)),
            custom_threshold=float(raw.get("custom_threshold", default.custom_threshold)),
            max_regeneration_attempts=int(raw.get("max_regeneration_attempts", default.max_regeneration_attempts)),
        ).validate()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # Повреждённые пользовательские настройки не должны ломать Review Console.
        return default


def save_diversity_settings_file(path: Path, settings: DiversitySettings) -> None:
    settings = settings.validate()
    payload = settings.to_dict()
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
