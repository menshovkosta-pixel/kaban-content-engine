from __future__ import annotations

from pathlib import Path

import pytest

from kaban.assets import ProjectAssetError, project_assets_dir
from kaban.projects import ProjectRegistry


ROOT = Path(__file__).resolve().parent.parent


def test_caelus_assets_resolve_inside_project_directory():
    expected = ROOT / "projects" / "caelus" / "assets"
    assert project_assets_dir("caelus") == expected


def test_unknown_project_asset_directory_fails_fast():
    with pytest.raises(ProjectAssetError, match="does-not-exist"):
        project_assets_dir("does-not-exist")


def test_caelus_renderer_uses_project_asset_directory():
    import generate_cards

    assert generate_cards.ASSETS_DIR == project_assets_dir("caelus")


def test_caelus_png_assets_are_isolated_from_engine_root():
    assets_dir = project_assets_dir("caelus")
    expected = {"background.png", "logo_caelus.png"}
    expected.update({f"art_{sign}.png" for sign in (
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
    )})
    expected.update({f"glyph_{sign}.png" for sign in (
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
    )})

    assert {path.name for path in assets_dir.glob("*.png")} == expected
    assert not any((ROOT / name).exists() for name in expected)


def test_asset_directory_uses_registry_discovered_project_folder(tmp_path):
    project_dir = tmp_path / "custom-folder"
    project_dir.mkdir(parents=True)
    source = ROOT / "projects" / "caelus" / "project.yaml"
    payload = source.read_text(encoding="utf-8").replace("id: caelus", "id: custom-id", 1)
    (project_dir / "project.yaml").write_text(payload, encoding="utf-8")

    registry = ProjectRegistry(tmp_path)
    assert project_assets_dir("custom-id", registry=registry) == project_dir / "assets"
