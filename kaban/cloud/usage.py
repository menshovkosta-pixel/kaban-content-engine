from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable


_ALLOWED_QUALITY = {"provider_exact", "db_exact", "estimated"}


@dataclass(frozen=True)
class UsageSample:
    metric: str
    value: float
    unit: str
    quality: str
    limit: float | None = None
    metadata: dict | None = None

    def __post_init__(self):
        if self.quality not in _ALLOWED_QUALITY:
            raise ValueError(f"Unknown usage quality: {self.quality}")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("Usage limit must be > 0")


@dataclass(frozen=True)
class CleanupResult:
    deleted: tuple[str, ...]
    skipped_referenced: tuple[str, ...]
    skipped_grace_period: tuple[str, ...] = ()


def quota_level(used: float, limit: float) -> str:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    ratio = used / limit
    if ratio >= 0.95:
        return "critical"
    if ratio >= 0.85:
        return "high"
    if ratio >= 0.70:
        return "warning"
    return "ok"


def serialize_usage(sample: UsageSample) -> dict:
    payload = {
        "metric": sample.metric,
        "value": sample.value,
        "unit": sample.unit,
        "quality": sample.quality,
        "limit": sample.limit,
        "metadata": dict(sample.metadata or {}),
    }
    if sample.limit is not None:
        payload["quota_level"] = quota_level(sample.value, sample.limit)
    return payload


def cleanup_orphans(
    candidates: Iterable[tuple[str, datetime]], *,
    is_referenced: Callable[[str], bool],
    delete: Callable[[str], None],
    now: datetime,
    grace_period: timedelta,
    before_delete: Callable[[str], None] | None = None,
) -> CleanupResult:
    if grace_period.total_seconds() < 0:
        raise ValueError("grace_period must be non-negative")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    deleted: list[str] = []
    skipped_referenced: list[str] = []
    skipped_grace: list[str] = []
    for key, discovered_at in candidates:
        if discovered_at.tzinfo is None:
            discovered_at = discovered_at.replace(tzinfo=timezone.utc)
        if now - discovered_at < grace_period:
            skipped_grace.append(key)
            continue
        if is_referenced(key):
            skipped_referenced.append(key)
            continue
        if before_delete is not None:
            before_delete(key)
        # Повторная проверка закрывает гонку между обнаружением orphan и delete.
        if is_referenced(key):
            skipped_referenced.append(key)
            continue
        delete(key)
        deleted.append(key)
    return CleanupResult(tuple(deleted), tuple(skipped_referenced), tuple(skipped_grace))
