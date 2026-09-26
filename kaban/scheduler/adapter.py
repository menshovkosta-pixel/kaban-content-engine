from __future__ import annotations

import importlib


class AdapterLoadError(RuntimeError):
    """Не удалось загрузить Project scheduler adapter."""


def load_adapter(entrypoint: str):
    module_name, sep, attr_name = entrypoint.partition(":")
    if not sep or not module_name or not attr_name:
        raise AdapterLoadError("Scheduler adapter must use module.path:function format")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise AdapterLoadError(
            f"Не удалось импортировать scheduler adapter module {module_name}: {exc.__class__.__name__}"
        ) from exc
    target = getattr(module, attr_name, None)
    if not callable(target):
        raise AdapterLoadError(f"Scheduler adapter {entrypoint} не является callable")
    return target
