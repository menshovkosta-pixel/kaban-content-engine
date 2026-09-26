from pathlib import Path


def test_kaban_core_never_imports_caelus():
    root = Path(__file__).resolve().parents[1] / "kaban"
    offenders = []
    for path in root.rglob("*.py"):
        if "projects.caelus" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
