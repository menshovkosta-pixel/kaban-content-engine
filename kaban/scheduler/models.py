from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class RetryPolicy:
    interval_minutes: int = 0
    window_minutes: int = 0
    max_attempts: int = 0  # число повторов после первой попытки


@dataclass(frozen=True)
class ScheduledJob:
    project_id: str
    job_id: str
    handler: str
    cron: str
    timezone: str
    params: dict[str, Any]
    misfire_grace_minutes: int
    retry: RetryPolicy
    adapter: str


@dataclass(frozen=True)
class JobContext:
    project_id: str
    job_id: str
    scheduled_for_utc: datetime
    scheduled_for_local: datetime
    now_utc: datetime
    trigger: str
    attempt: int


@dataclass(frozen=True)
class WaitCondition:
    kind: str
    project_id: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class JobResult:
    outcome: str
    message: str = ""
    retryable: bool = False
    wait_condition: WaitCondition | None = None


@dataclass(frozen=True)
class SchedulerState:
    project_id: str
    job_id: str
    slot_id: str | None = None
    scheduled_for_utc: str | None = None
    last_result: str | None = None
    attempt: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    next_retry_at: str | None = None
    last_error: dict[str, str] | None = None
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DispatchRecord:
    project_id: str
    job_id: str
    slot_id: str
    result: str
    attempt: int
    message: str = ""
