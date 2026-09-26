from __future__ import annotations

import importlib
from typing import Any


class CloudAdapterLoadError(RuntimeError):
    """Не удалось загрузить Project cloud adapter."""


def load_cloud_adapter(entrypoint: str, *, project_id: str | None = None) -> Any:
    module_name, sep, attr_name = entrypoint.partition(":")
    if not sep or not module_name or not attr_name:
        raise CloudAdapterLoadError("Cloud adapter должен использовать module.path:attribute")
    if project_id and module_name.startswith("projects."):
        parts = module_name.split(".")
        if len(parts) >= 2 and parts[1] != project_id:
            raise CloudAdapterLoadError("Project не может загружать cloud adapter другого Project")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise CloudAdapterLoadError(f"Не удалось импортировать cloud adapter module {module_name}: {exc.__class__.__name__}") from exc
    target = getattr(module, attr_name, None)
    if target is None:
        raise CloudAdapterLoadError(f"Cloud adapter {entrypoint} не найден")
    return target
