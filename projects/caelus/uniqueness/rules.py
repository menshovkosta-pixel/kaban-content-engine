from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniquenessRules:
    history_days: int = 90
    warning_threshold: float = 0.76
    hard_threshold: float = 0.80
    same_day_hard_threshold: float = 0.84
    max_regeneration_attempts: int = 3

    def validate(self) -> "UniquenessRules":
        if self.history_days < 1:
            raise ValueError("history_days должен быть >= 1")
        if not 0 < self.warning_threshold < self.hard_threshold <= 1:
            raise ValueError("Некорректные пороги uniqueness")
        if not 0 < self.same_day_hard_threshold <= 1:
            raise ValueError("Некорректный same-day threshold")
        if self.max_regeneration_attempts < 0:
            raise ValueError("max_regeneration_attempts должен быть >= 0")
        return self
