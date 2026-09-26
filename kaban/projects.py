from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from kaban.scheduler.cron import cron_is_valid
from kaban.scheduler.models import RetryPolicy


ENGINE_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = ENGINE_ROOT / "projects"
ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class ProjectConfigError(ValueError):
    """Ошибка чтения или валидации определения KABAN Project."""


@dataclass(frozen=True)
class AIConfig:
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class UniquenessConfig:
    history_days: int
    warning_threshold: float
    hard_threshold: float
    max_regeneration_attempts: int


@dataclass(frozen=True)
class TelegramPublicationConfig:
    album_group_size: int


@dataclass(frozen=True)
class PublicationConfig:
    telegram: TelegramPublicationConfig


@dataclass(frozen=True)
class ProjectJobConfig:
    id: str
    handler: str
    enabled: bool
    cron: str
    params: dict[str, Any]
    misfire_grace_minutes: int
    retry: RetryPolicy


@dataclass(frozen=True)
class ProjectCloudConfig:
    adapter: str | None = None


@dataclass(frozen=True)
class ProjectAutomationConfig:
    enabled: bool
    adapter: str | None
    jobs: tuple[ProjectJobConfig, ...]


@dataclass(frozen=True)
class ProjectConfig:
    id: str
    name: str
    default_language: str
    supported_languages: tuple[str, ...]
    timezone: str
    ai: AIConfig
    uniqueness: UniquenessConfig
    publication: PublicationConfig
    automation: ProjectAutomationConfig
    cloud: ProjectCloudConfig


def _error(path: Path, message: str) -> ProjectConfigError:
    return ProjectConfigError(f"Project config {path}: {message}")


def _mapping(value: Any, field: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, f"{field} должен быть mapping")
    return value


def _required_mapping(mapping: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    if key not in mapping:
        raise _error(path, f"отсутствует обязательный раздел {key}")
    return _mapping(mapping[key], key, path)


def _non_empty_string(mapping: dict[str, Any], key: str, path: Path) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error(path, f"{key} должен быть непустой строкой")
    return value.strip()


def _integer(mapping: dict[str, Any], key: str, path: Path, minimum: int, default: int | None = None) -> int:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(path, f"{key} должен быть целым числом")
    if value < minimum:
        raise _error(path, f"{key} должен быть >= {minimum}")
    return value


def _threshold(mapping: dict[str, Any], key: str, path: Path) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, f"{key} должен быть числом")
    result = float(value)
    if not 0 < result <= 1:
        raise _error(path, f"{key} должен быть в диапазоне (0, 1]")
    return result


def _supported_languages(mapping: dict[str, Any], path: Path) -> tuple[str, ...]:
    value = mapping.get("supported_languages")
    if not isinstance(value, list) or not value:
        raise _error(path, "supported_languages должен быть непустым списком")
    languages: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _error(path, "supported_languages должен содержать только непустые строки")
        languages.append(item.strip())
    return tuple(languages)


def _automation(root: dict[str, Any], path: Path) -> ProjectAutomationConfig:
    value = root.get("automation")
    if value is None:
        return ProjectAutomationConfig(enabled=False, adapter=None, jobs=())
    raw = _mapping(value, "automation", path)
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _error(path, "automation.enabled должен быть boolean")
    adapter_value = raw.get("adapter")
    adapter: str | None = None
    if adapter_value is not None:
        if not isinstance(adapter_value, str) or not adapter_value.strip():
            raise _error(path, "automation.adapter должен быть непустой строкой")
        adapter = adapter_value.strip()
    jobs_raw = raw.get("jobs", [])
    if not isinstance(jobs_raw, list):
        raise _error(path, "automation.jobs должен быть списком")
    jobs: list[ProjectJobConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(jobs_raw):
        job_raw = _mapping(item, f"automation.jobs[{index}]", path)
        job_id = _non_empty_string(job_raw, "id", path)
        if job_id in seen:
            raise _error(path, f"повторяющийся automation job id: {job_id}")
        seen.add(job_id)
        handler = _non_empty_string(job_raw, "handler", path)
        cron_value = _non_empty_string(job_raw, "cron", path)
        if not cron_is_valid(cron_value):
            raise _error(path, f"cron некорректен для job {job_id}")
        job_enabled = job_raw.get("enabled", True)
        if not isinstance(job_enabled, bool):
            raise _error(path, f"automation job {job_id}.enabled должен быть boolean")
        params_value = job_raw.get("params", {})
        params = _mapping(params_value, "params", path)
        grace = _integer(job_raw, "misfire_grace_minutes", path, 0, 0)
        retry_raw = job_raw.get("retry", {})
        retry_map = _mapping(retry_raw, "retry", path)
        retry = RetryPolicy(
            interval_minutes=_integer(retry_map, "interval_minutes", path, 0, 0),
            window_minutes=_integer(retry_map, "window_minutes", path, 0, 0),
            max_attempts=_integer(retry_map, "max_attempts", path, 0, 0),
        )
        jobs.append(ProjectJobConfig(
            id=job_id,
            handler=handler,
            enabled=job_enabled,
            cron=cron_value,
            params=dict(params),
            misfire_grace_minutes=grace,
            retry=retry,
        ))
    if enabled and (not adapter or not jobs):
        raise _error(path, "automation.enabled=true требует adapter и хотя бы один job")
    return ProjectAutomationConfig(enabled=enabled, adapter=adapter, jobs=tuple(jobs))



def _cloud(root: dict[str, Any], path: Path) -> ProjectCloudConfig:
    value = root.get("cloud")
    if value is None:
        return ProjectCloudConfig()
    raw = _mapping(value, "cloud", path)
    adapter = raw.get("adapter")
    if adapter is not None and (not isinstance(adapter, str) or not adapter.strip()):
        raise _error(path, "cloud.adapter должен быть непустой строкой")
    return ProjectCloudConfig(adapter=adapter.strip() if isinstance(adapter, str) else None)

def load_project_config(path: Path) -> ProjectConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectConfigError(f"Не удалось прочитать Project config {path}: {exc.__class__.__name__}") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" (строка {mark.line + 1}, столбец {mark.column + 1})" if mark is not None else ""
        raise ProjectConfigError(f"Некорректный YAML в Project config {path}{location}") from exc

    try:
        root = _mapping(raw, "top-level YAML", path)
        project_id = _non_empty_string(root, "id", path)
        name = _non_empty_string(root, "name", path)
        default_language = _non_empty_string(root, "default_language", path)
        supported_languages = _supported_languages(root, path)
        timezone = _non_empty_string(root, "timezone", path)
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise _error(path, "timezone должен быть валидным IANA timezone") from exc
        if default_language not in supported_languages:
            raise _error(path, "default_language должен входить в supported_languages")

        ai_raw = _required_mapping(root, "ai", path)
        model = _non_empty_string(ai_raw, "model", path)
        reasoning_effort = _non_empty_string(ai_raw, "reasoning_effort", path).lower()
        if reasoning_effort not in ALLOWED_REASONING_EFFORTS:
            allowed = ", ".join(sorted(ALLOWED_REASONING_EFFORTS))
            raise _error(path, f"reasoning_effort должен быть одним из: {allowed}")

        uniqueness_raw = _required_mapping(root, "uniqueness", path)
        history_days = _integer(uniqueness_raw, "history_days", path, 1)
        warning_threshold = _threshold(uniqueness_raw, "warning_threshold", path)
        hard_threshold = _threshold(uniqueness_raw, "hard_threshold", path)
        if warning_threshold > hard_threshold:
            raise _error(path, "warning_threshold должен быть <= hard_threshold")
        max_regeneration_attempts = _integer(uniqueness_raw, "max_regeneration_attempts", path, 0)

        publication_raw = _required_mapping(root, "publication", path)
        telegram_raw = _required_mapping(publication_raw, "telegram", path)
        album_group_size = _integer(telegram_raw, "album_group_size", path, 1)

        return ProjectConfig(
            id=project_id,
            name=name,
            default_language=default_language,
            supported_languages=supported_languages,
            timezone=timezone,
            ai=AIConfig(model=model, reasoning_effort=reasoning_effort),
            uniqueness=UniquenessConfig(
                history_days=history_days,
                warning_threshold=warning_threshold,
                hard_threshold=hard_threshold,
                max_regeneration_attempts=max_regeneration_attempts,
            ),
            publication=PublicationConfig(telegram=TelegramPublicationConfig(album_group_size=album_group_size)),
            automation=_automation(root, path),
            cloud=_cloud(root, path),
        )
    except ProjectConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProjectConfigError(f"Не удалось загрузить Project config {path}: {exc.__class__.__name__}") from exc


class ProjectRegistry:
    def __init__(self, projects_dir: Path = PROJECTS_ROOT):
        self.projects_dir = Path(projects_dir)
        self._project_dirs: dict[str, Path] = {}
        self._projects = self._discover()

    def _discover(self) -> dict[str, ProjectConfig]:
        if not self.projects_dir.is_dir():
            raise ProjectConfigError(f"KABAN projects directory not found: {self.projects_dir}")
        discovered: dict[str, ProjectConfig] = {}
        config_paths = sorted(self.projects_dir.glob("*/project.yaml"))
        if not config_paths:
            raise ProjectConfigError(f"No KABAN projects found in: {self.projects_dir}")
        for path in config_paths:
            config = load_project_config(path)
            if config.id in discovered:
                raise ProjectConfigError(f"Duplicate KABAN project id '{config.id}' while loading {path}")
            discovered[config.id] = config
            self._project_dirs[config.id] = path.parent
        return discovered

    def get(self, project_id: str) -> ProjectConfig:
        project_id = str(project_id).strip()
        try:
            return self._projects[project_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._projects))
            raise ProjectConfigError(f"Unknown KABAN project id '{project_id}'. Registered projects: {known}") from exc

    def active(self) -> ProjectConfig:
        return self.get(os.getenv("CONTENT_PROJECT_ID", "caelus"))

    def registered(self) -> tuple[ProjectConfig, ...]:
        return tuple(self._projects[key] for key in sorted(self._projects))

    def directory(self, project_id: str) -> Path:
        project_id = str(project_id).strip()
        self.get(project_id)
        return self._project_dirs[project_id]


_DEFAULT_REGISTRY: ProjectRegistry | None = None


def project_registry() -> ProjectRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ProjectRegistry(PROJECTS_ROOT)
    return _DEFAULT_REGISTRY


def active_project() -> ProjectConfig:
    return project_registry().active()
