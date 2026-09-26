from pathlib import Path

import pytest

from deploy.bootstrap_data import BootstrapError, bootstrap_generated


def test_bootstrap_copies_into_empty_destination(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    (src / "2026-09-22" / "ru").mkdir(parents=True)
    (src / "2026-09-22" / "ru" / "content.json").write_text("{}", encoding="utf-8")
    result = bootstrap_generated(src, dst)
    assert result.copied == 1
    assert (dst / "2026-09-22" / "ru" / "content.json").read_text() == "{}"


def test_bootstrap_refuses_nonempty_destination_without_merge(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    dst.mkdir(parents=True)
    (dst / "keep.txt").write_text("host", encoding="utf-8")
    with pytest.raises(BootstrapError, match="не пуст"):
        bootstrap_generated(src, dst)
    assert (dst / "keep.txt").read_text() == "host"


def test_merge_never_overwrites_existing_host_file(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    dst.mkdir(parents=True)
    (src / "same.json").write_text("source", encoding="utf-8")
    (src / "new.json").write_text("new", encoding="utf-8")
    (dst / "same.json").write_text("host", encoding="utf-8")
    result = bootstrap_generated(src, dst, merge=True)
    assert (dst / "same.json").read_text() == "host"
    assert (dst / "new.json").read_text() == "new"
    assert result.skipped_existing == 1
    assert result.copied == 1


def test_missing_source_fails_without_creating_destination(tmp_path):
    src = tmp_path / "missing"
    dst = tmp_path / "data" / "generated"
    with pytest.raises(BootstrapError, match="source"):
        bootstrap_generated(src, dst)
    assert not dst.exists()


def test_source_must_be_directory(tmp_path):
    src = tmp_path / "generated.txt"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(BootstrapError, match="директор"):
        bootstrap_generated(src, tmp_path / "out")


def test_bootstrap_rejects_symlinks(tmp_path):
    src = tmp_path / "generated"
    outside = tmp_path / "outside.txt"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    outside.write_text("secret", encoding="utf-8")
    try:
        (src / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink недоступен в этой среде")
    with pytest.raises(BootstrapError, match="символ"):
        bootstrap_generated(src, dst)
    assert not (dst / "link.txt").exists()


def test_bootstrap_does_not_copy_runtime_tree(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    (src / "content.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "secret-state.json").write_text("{}", encoding="utf-8")
    bootstrap_generated(src, dst)
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (dst / "runtime").exists()


def test_merge_rejects_destination_symlink_before_copy(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    outside = tmp_path / "outside"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "new.json").write_text("new", encoding="utf-8")
    dst.mkdir(parents=True)
    outside.mkdir()
    try:
        (dst / "nested").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink недоступен в этой среде")
    with pytest.raises(BootstrapError, match="destination.*символ"):
        bootstrap_generated(src, dst, merge=True)
    assert not (outside / "new.json").exists()


def test_merge_rejects_file_directory_collision_before_partial_copy(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    (src / "a_dir").mkdir(parents=True)
    (src / "a_dir" / "inside.json").write_text("inside", encoding="utf-8")
    (src / "z_new.json").write_text("new", encoding="utf-8")
    dst.mkdir(parents=True)
    (dst / "a_dir").write_text("host-file", encoding="utf-8")
    with pytest.raises(BootstrapError, match="конфликт"):
        bootstrap_generated(src, dst, merge=True)
    assert (dst / "a_dir").read_text(encoding="utf-8") == "host-file"
    assert not (dst / "z_new.json").exists()
