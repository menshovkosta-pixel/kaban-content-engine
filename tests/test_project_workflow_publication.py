from __future__ import annotations

import ast
from pathlib import Path


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_caelus_project_owns_workflow_and_publication_modules():
    from projects.caelus import publication, telegram_builder, workflow

    assert callable(workflow.generate_bundle)
    assert callable(publication.run_telegram_publisher)
    assert callable(publication.main)
    assert callable(telegram_builder.main)


def test_legacy_caelus_package_is_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "caelus").exists()


def test_root_cli_files_are_compatibility_wrappers_only():
    root = Path(__file__).resolve().parents[1]

    assert _function_names(root / "publish_telegram.py") == set()
    assert _function_names(root / "build_telegram.py") == set()


def test_production_entrypoints_use_project_automation_without_redirecting_manual_cli():
    root = Path(__file__).resolve().parents[1]
    admin_source = (root / "admin_app.py").read_text(encoding="utf-8")
    daily_source = (root / "run_daily.py").read_text(encoding="utf-8")
    publish_source = (root / "publish_telegram.py").read_text(encoding="utf-8")

    assert "from projects.caelus.automation import derive_state, run_generation_job, run_publication_job" in admin_source
    assert "from projects.caelus.workflow import (" in admin_source
    assert "from projects.caelus.workflow import generate_bundle" in daily_source
    assert "run_generation_job" not in daily_source
    assert "from projects.caelus.publication import *" in publish_source
    assert "run_publication_job" not in publish_source
