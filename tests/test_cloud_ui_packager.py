import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _project(tmp_path: Path, manifest: dict):
    project_dir = tmp_path / "projects" / "alpha"
    ui = project_dir / "cloud_ui"
    ui.mkdir(parents=True)
    (ui / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (ui / "index.html").write_text("<h1>Alpha</h1>", encoding="utf-8")
    (ui / "app.js").write_text("export const alpha = true;", encoding="utf-8")
    (ui / "secret.txt").write_text("do-not-copy", encoding="utf-8")

    class Registry:
        def registered(self):
            return (SimpleNamespace(id="alpha"),)
        def directory(self, project_id):
            assert project_id == "alpha"
            return project_dir
    return Registry()


def test_packager_copies_only_manifest_declared_assets(tmp_path):
    from kaban.cloud.ui_packager import package_project_uis

    registry = _project(tmp_path, {
        "project_id": "alpha",
        "entry": "index.html",
        "route": "/projects/alpha/",
        "assets": ["index.html", "app.js"],
    })
    out = tmp_path / "worker-assets"
    packaged = package_project_uis(registry, out)

    target = out / "projects" / "alpha"
    assert packaged == {"alpha": "/projects/alpha/"}
    assert (target / "index.html").read_text() == "<h1>Alpha</h1>"
    assert (target / "app.js").is_file()
    assert not (target / "secret.txt").exists()
    assert not (target / "manifest.json").exists()


def test_packager_rejects_path_traversal(tmp_path):
    from kaban.cloud.ui_packager import UiManifestError, package_project_uis

    registry = _project(tmp_path, {
        "project_id": "alpha",
        "entry": "index.html",
        "route": "/projects/alpha/",
        "assets": ["index.html", "../secret.txt"],
    })
    with pytest.raises(UiManifestError, match="asset path"):
        package_project_uis(registry, tmp_path / "out")


def test_packager_rejects_manifest_project_mismatch(tmp_path):
    from kaban.cloud.ui_packager import UiManifestError, package_project_uis

    registry = _project(tmp_path, {
        "project_id": "other",
        "entry": "index.html",
        "route": "/projects/alpha/",
        "assets": ["index.html"],
    })
    with pytest.raises(UiManifestError, match="project_id"):
        package_project_uis(registry, tmp_path / "out")


def test_caelus_ui_source_exposes_required_review_operations():
    root = Path(__file__).resolve().parents[1]
    app = (root / "projects" / "caelus" / "cloud_ui" / "app.js").read_text(encoding="utf-8")
    html = (root / "projects" / "caelus" / "cloud_ui" / "index.html").read_text(encoding="utf-8")
    for operation in (
        "save", "regenerate_field", "regenerate_sign", "regenerate_conflicts",
        "approve", "return_to_draft", "generate", "publish",
    ):
        assert operation in app or operation in html
    assert "/executions/" in app
    assert "/reconcile" in app
    assert "/artifacts/" in app
    assert "unknown_delivery" in app
