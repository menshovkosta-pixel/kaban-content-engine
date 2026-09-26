from __future__ import annotations


def execute_core_operation(command, *, store, registry):
    if command.operation == "kaban.schedule_projection":
        from .schedule_projection import project_schedule
        from datetime import datetime, timezone
        return project_schedule(registry, store, from_utc=datetime.now(timezone.utc))
    raise ValueError(f"Неизвестная Core cloud operation: {command.operation}")
