from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "caelus"


def _imports_legacy(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "caelus" or node.module.startswith("caelus.") or node.module == "content_engine" or node.module.startswith("content_engine."):
                found.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "caelus" or alias.name.startswith("caelus.") or alias.name == "content_engine" or alias.name.startswith("content_engine."):
                    found.append(alias.name)
    return found


def test_caelus_project_has_no_legacy_package_dependencies():
    offenders: dict[str, list[str]] = {}
    for path in PROJECT.rglob("*.py"):
        imports = _imports_legacy(path)
        if imports:
            offenders[str(path.relative_to(ROOT))] = imports
    assert offenders == {}


def test_legacy_packages_are_removed_from_production_tree():
    assert not (ROOT / "caelus").exists()
    assert not (ROOT / "content_engine").exists()


def test_caelus_project_owns_remaining_domain_services():
    from projects.caelus import config, quality, storage, validators
    from projects.caelus.uniqueness import history, rules, service, settings, similarity

    assert callable(config.openai_model)
    assert callable(storage.day_dir)
    assert callable(quality.validate_content)
    assert callable(validators.validate_payload)
    assert callable(history.load_history)
    assert rules.UniquenessRules
    assert service.UniquenessService
    assert settings.DiversitySettings
    assert callable(similarity.compare_texts)
