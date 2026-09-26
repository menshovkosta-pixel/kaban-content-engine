from __future__ import annotations

import json
import shutil
from pathlib import Path


class UiManifestError(ValueError):
    """Некорректный manifest Project-owned cloud UI."""


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
        raise UiManifestError(f"Недопустимый asset path в UI manifest: {value}")
    return path


def _load_manifest(project_id: str, source: Path) -> dict:
    path = source / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UiManifestError(f"Не удалось прочитать UI manifest для {project_id}: {exc.__class__.__name__}") from exc
    if not isinstance(manifest, dict):
        raise UiManifestError("UI manifest должен быть JSON object")
    if manifest.get("project_id") != project_id:
        raise UiManifestError("UI manifest project_id не совпадает с Project")
    expected_route = f"/projects/{project_id}/"
    if manifest.get("route") != expected_route:
        raise UiManifestError(f"UI manifest route должен быть {expected_route}")
    entry = manifest.get("entry")
    assets = manifest.get("assets")
    if not isinstance(entry, str) or not entry:
        raise UiManifestError("UI manifest entry обязателен")
    if not isinstance(assets, list) or not assets or not all(isinstance(x, str) and x for x in assets):
        raise UiManifestError("UI manifest assets должен быть непустым списком путей")
    entry_path = _safe_relative(entry)
    asset_paths = tuple(_safe_relative(item) for item in assets)
    if entry_path not in asset_paths:
        raise UiManifestError("UI manifest entry должен входить в assets")
    manifest["_asset_paths"] = asset_paths
    return manifest


def package_project_uis(registry, destination: Path) -> dict[str, str]:
    destination = Path(destination)
    routes: dict[str, str] = {}
    for project in registry.registered():
        source = Path(registry.directory(project.id)) / "cloud_ui"
        if not source.is_dir():
            continue
        manifest = _load_manifest(project.id, source)
        target = destination / "projects" / project.id
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for relative in manifest["_asset_paths"]:
            src = (source / relative).resolve()
            source_root = source.resolve()
            if source_root not in src.parents and src != source_root:
                raise UiManifestError(f"Недопустимый asset path в UI manifest: {relative}")
            if not src.is_file():
                raise UiManifestError(f"UI asset не найден: {relative}")
            dst = target / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        routes[project.id] = str(manifest["route"])
    return routes


if __name__ == "__main__":
    from kaban.projects import project_registry
    root = Path(__file__).resolve().parents[2]
    out = root / "deploy" / "cloudflare" / "worker" / ".generated-assets"
    packaged = package_project_uis(project_registry(), out)
    for project_id, route in packaged.items():
        print(f"[OK] {project_id}: {route}")
