from __future__ import annotations

import importlib
from pathlib import Path


def test_caelus_renderer_lives_under_project_package() -> None:
    renderer = importlib.import_module("projects.caelus.renderer")

    assert callable(renderer.render_card)
    assert callable(renderer.validate_assets)
    assert callable(renderer.load_data)
    assert callable(renderer.main)


def test_legacy_generate_cards_is_thin_compatibility_wrapper() -> None:
    legacy = importlib.import_module("generate_cards")
    renderer = importlib.import_module("projects.caelus.renderer")

    assert legacy.render_card is renderer.render_card
    assert legacy.validate_assets is renderer.validate_assets
    assert legacy.load_data is renderer.load_data
    assert legacy.ASSETS_DIR == renderer.ASSETS_DIR
    assert legacy.SIGN_ORDER is renderer.SIGN_ORDER
    assert legacy.SIGN_NAMES_RU is renderer.SIGN_NAMES_RU

    source = Path(legacy.__file__).read_text(encoding="utf-8")
    assert "from PIL import" not in source
    assert "Image.open" not in source
    assert "def draw_forecast" not in source


def test_caelus_workflow_invokes_project_renderer_directly() -> None:
    import projects.caelus.workflow as workflow

    source = Path(workflow.__file__).read_text(encoding="utf-8")
    assert '"generate_cards.py"' not in source
    assert '"-m", "projects.caelus.renderer"' in source
