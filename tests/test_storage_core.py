from __future__ import annotations

import json
from pathlib import Path

from kaban.storage import content_hash, load_json, write_json


def test_load_json_returns_default_for_missing_file(tmp_path: Path):
    missing = tmp_path / "missing.json"
    default = {"state": "draft"}

    assert load_json(missing, default) is default


def test_write_json_is_utf8_pretty_and_leaves_no_temp_file(tmp_path: Path):
    path = tmp_path / "nested" / "content.json"
    payload = {"text": "Между звёздами", "value": 7}

    write_json(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert "Между звёздами" in path.read_text(encoding="utf-8")
    assert list(path.parent.glob("content.json.*.tmp")) == []


def test_content_hash_is_stable_for_mapping_order_and_unicode():
    first = {"b": 2, "a": "звезда"}
    second = {"a": "звезда", "b": 2}

    assert content_hash(first) == content_hash(second)


def test_project_storage_reuses_kaban_json_primitives():
    import projects.caelus.storage as project_storage

    assert project_storage.load_json is load_json
    assert project_storage.write_json is write_json
    assert project_storage.content_hash is content_hash


def test_caelus_project_storage_layout_remains_generated_day_language(monkeypatch, tmp_path: Path):
    import projects.caelus.storage as project_storage

    generated = tmp_path / "generated"
    monkeypatch.setattr(project_storage, "GENERATED", generated)

    assert project_storage.day_dir("2099-12-31", "ru") == generated / "2099-12-31" / "ru"
    assert project_storage.content_path("2099-12-31", "ru") == generated / "2099-12-31" / "ru" / "content.json"
    assert project_storage.status_path("2099-12-31", "ru") == generated / "2099-12-31" / "ru" / "status.json"
    assert project_storage.publication_path("2099-12-31", "ru") == generated / "2099-12-31" / "ru" / "publication.json"


def test_kaban_storage_has_no_caelus_layout_contracts():
    import kaban.storage as core

    assert not hasattr(core, "day_dir")
    assert not hasattr(core, "content_path")
    assert not hasattr(core, "publication_path")
    assert not hasattr(core, "append_publication_event")


def test_caelus_publisher_uses_kaban_storage_primitives():
    import publish_telegram

    assert publish_telegram.load_json is load_json
    assert publish_telegram.write_json is write_json
    assert publish_telegram.content_hash is content_hash
