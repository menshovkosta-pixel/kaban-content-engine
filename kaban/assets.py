from __future__ import annotations

from pathlib import Path

from .projects import ProjectConfigError, ProjectRegistry, project_registry


class ProjectAssetError(ProjectConfigError):
    """Ошибка разрешения каталога assets KABAN Project."""


def project_assets_dir(project_id: str, registry: ProjectRegistry | None = None) -> Path:
    """Возвращает каталог assets из фактически обнаруженной директории проекта."""
    project_id = str(project_id).strip()
    registry = registry or project_registry()
    try:
        project_dir = registry.directory(project_id)
    except ProjectConfigError as exc:
        raise ProjectAssetError(str(exc)) from exc
    return project_dir / "assets"
