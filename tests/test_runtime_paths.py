from pathlib import Path
import pytest

from kaban.runtime.paths import generated_root, persistence_mode


def test_persistence_defaults_to_local(monkeypatch):
    monkeypatch.delenv("KABAN_PERSISTENCE", raising=False)
    assert persistence_mode() == "local"


def test_generated_root_uses_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KABAN_GENERATED_DIR", str(tmp_path / "ephemeral" / "generated"))
    assert generated_root(Path("/repo/generated")) == tmp_path / "ephemeral" / "generated"


def test_unknown_persistence_mode_fails(monkeypatch):
    monkeypatch.setenv("KABAN_PERSISTENCE", "mixed")
    with pytest.raises(ValueError, match="KABAN_PERSISTENCE"):
        persistence_mode()


def test_caelus_has_single_generated_root_definition():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "projects" / "caelus").rglob("*.py"):
        if path.name == "storage.py":
            continue
        if 'ROOT / "generated"' in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
