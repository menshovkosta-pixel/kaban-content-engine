from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from kaban.cloud.migration import migrate_local, verify_manifest, export_local
from projects.caelus.cloud_adapter import CaelusCloudAdapter


def _sha_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _fixture(tmp_path: Path, *, unknown: bool = False):
    generated = tmp_path / "generated"
    runtime = tmp_path / "runtime"
    base = generated / "2026-09-26" / "ru"
    (base / "cards").mkdir(parents=True)
    (base / "telegram_media").mkdir()
    (generated / "_settings").mkdir(parents=True)
    (generated / "_automation" / "caelus" / "2026-09-26" / "ru").mkdir(parents=True)
    (runtime / "scheduler").mkdir(parents=True)
    content = {"iso_date":"2026-09-26","language":"ru","signs":{"aries":{"card":"hello"}},"generation":{"uniqueness":{"threshold":0.8,"conflicts":[]}}}
    (base / "content.json").write_text(json.dumps(content), encoding="utf-8")
    (base / "status.json").write_text(json.dumps({"state":"approved","content_hash":"hash-1"}), encoding="utf-8")
    (base / "publication.json").write_text(json.dumps({"state":"published","content_hash":"hash-1"}), encoding="utf-8")
    (base / "cards" / "caelus_aries.png").write_bytes(b"PNG-CARD")
    (base / "telegram_media" / "caelus_aries.jpg").write_bytes(b"JPEG-DERIVATIVE")
    (generated / "_settings" / "content_diversity.json").write_text(json.dumps({"profile":"balanced"}), encoding="utf-8")
    auto = generated / "_automation" / "caelus" / "2026-09-26" / "ru"
    (auto / "run.json").write_text(json.dumps({"operation":"generate","state":"success"}), encoding="utf-8")
    (auto / "generation.lock").write_text("123", encoding="utf-8")
    (runtime / "scheduler" / "state.json").write_text(json.dumps({"completed_slots":["slot-1"]}), encoding="utf-8")
    if unknown:
        (base / "mystery.bin").write_bytes(b"?")
    return generated, runtime


class FakeArtifactStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.new_uploads = 0

    def object_key(self, project_id, content_set_id, revision_id, kind, logical_name):
        return f"projects/{project_id}/content/{content_set_id}/revisions/{revision_id}/{kind}/{logical_name}"

    def put_immutable(self, project_id, key, source, expected_sha256):
        data = Path(source).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        if key not in self.objects:
            self.objects[key] = data
            self.new_uploads += 1
        elif self.objects[key] != data:
            raise AssertionError("immutable conflict")
        return type("Stored", (), {"r2_key":key,"sha256":expected_sha256,"size_bytes":len(data),"mime_type":"image/png"})()

    def verify_object(self, project_id, key, expected_sha256, expected_size):
        data = self.objects.get(key)
        return data is not None and len(data) == expected_size and hashlib.sha256(data).hexdigest() == expected_sha256

    def download(self, project_id, artifact, destination):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(self.objects[artifact.r2_key])


class FakeMigrationStore:
    def __init__(self, *, fail_bundle_once=False):
        self.fail_bundle_once = fail_bundle_once
        self.content_sets: dict[str, UUID] = {}
        self.bundles: dict[str, dict] = {}
        self.settings: dict[str, object] = {}
        self.history: dict[str, object] = {}
        self.bundle_commits = 0
        self.write_calls = 0
        self._snapshot = None
        self.identities: set[str] = set()

    def resolve_migration_content_set(self, project_id, content_key, proposed_id):
        self.write_calls += 1
        return self.content_sets.setdefault(content_key, proposed_id)

    def import_migration_bundle(self, **bundle):
        self.write_calls += 1
        identity = bundle["source_identity"]
        if identity in self.bundles:
            return self.bundles[identity]
        if self.fail_bundle_once:
            self.fail_bundle_once = False
            raise RuntimeError("db failed after R2 upload")
        self.bundles[identity] = bundle
        self.identities.add(identity)
        self.identities.update(item["source_identity"] for item in bundle.get("companion_identities", []))
        self.identities.update(item["source_identity"] for item in bundle.get("artifacts", []))
        self.bundle_commits += 1
        return {"content_set_id":bundle["content_set_id"],"revision_id":bundle["revision_id"]}

    def import_migration_setting(self, **item):
        self.write_calls += 1; self.settings[item["source_identity"]] = item; self.identities.add(item["source_identity"])

    def import_migration_history(self, **item):
        self.write_calls += 1; self.history[item["source_identity"]] = item; self.identities.add(item["source_identity"])

    def verify_migration_identity(self, project_id, source_identity, sha256):
        return source_identity in self.identities


def test_dry_run_is_source_immutable_and_performs_no_provider_writes(tmp_path):
    generated, runtime = _fixture(tmp_path)
    before = _sha_tree(tmp_path)
    store = FakeMigrationStore(); artifacts = FakeArtifactStore()
    manifest = tmp_path / "migration.json"
    result = migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), store, artifacts, apply=False, manifest_path=manifest)
    assert result.clean is True
    assert store.write_calls == 0
    assert artifacts.new_uploads == 0
    assert _sha_tree(tmp_path) | {} == _sha_tree(tmp_path)
    after_without_manifest = {k:v for k,v in _sha_tree(tmp_path).items() if k != "migration.json"}
    assert before == after_without_manifest
    records = json.loads(manifest.read_text())["records"]
    assert any(r["classification"] == "regenerable_derivative" and r["result"] == "not_migrated" for r in records)
    assert any(r["classification"] == "local_lock_not_migrated" for r in records)


def test_restart_after_r2_upload_does_not_duplicate_canonical_rows_or_objects(tmp_path):
    generated, runtime = _fixture(tmp_path)
    store = FakeMigrationStore(fail_bundle_once=True); artifacts = FakeArtifactStore()
    manifest = tmp_path / "migration.json"
    with pytest.raises(RuntimeError, match="db failed"):
        migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), store, artifacts, apply=True, manifest_path=manifest)
    assert artifacts.new_uploads == 1
    migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), store, artifacts, apply=True, manifest_path=manifest)
    assert artifacts.new_uploads == 1
    assert store.bundle_commits == 1
    assert len(store.bundles) == 1


def test_unknown_file_is_reported_and_blocks_clean_acceptance(tmp_path):
    generated, runtime = _fixture(tmp_path, unknown=True)
    result = migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), FakeMigrationStore(), FakeArtifactStore(), apply=False, manifest_path=tmp_path/"migration.json")
    assert result.clean is False
    unknown = [r for r in result.records if r.classification == "unknown"]
    assert [r.source_path for r in unknown] == ["generated/2026-09-26/ru/mystery.bin"]


def test_verify_manifest_detects_source_hash_tamper(tmp_path):
    generated, runtime = _fixture(tmp_path)
    manifest = tmp_path / "migration.json"
    migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), FakeMigrationStore(), FakeArtifactStore(), apply=False, manifest_path=manifest)
    (generated / "2026-09-26" / "ru" / "content.json").write_text("{}", encoding="utf-8")
    report = verify_manifest(manifest, source_root=tmp_path)
    assert report.ok is False
    assert "generated/2026-09-26/ru/content.json" in report.mismatches

def test_export_rebuilds_local_content_status_publication_and_png_bytes(tmp_path):
    from datetime import date, datetime, timezone
    from kaban.cloud.models import ArtifactRef, CanonicalSnapshot, ContentSetSnapshot, RevisionSnapshot

    content_set_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    revision_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    artifact_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    payload = {"iso_date":"2026-09-26","language":"ru","signs":{"aries":{"card":"hello"}}}
    revision = RevisionSnapshot(revision_id, content_set_id, 1, payload, "hash-content", datetime(2026,9,26,tzinfo=timezone.utc))
    content_set = ContentSetSnapshot(content_set_id, "caelus", "2026-09-26:ru", date(2026,9,26), "ru", 1, revision, revision_id, "hash-content")
    artifact = ArtifactRef(artifact_id, "caelus", content_set_id, revision_id, "card_png", "caelus_aries.png", "projects/caelus/card.png", hashlib.sha256(b"PNG-BYTES").hexdigest(), 9, "image/png", {})
    snapshot = CanonicalSnapshot("caelus", (content_set,), settings={"content_diversity":{"profile":"balanced"}}, artifacts=(artifact,))

    class ExportStore:
        def load_snapshot(self, project_id, spec): return snapshot
        def load_publication_projection(self, project_id, content_set_id): return {"state":"published","content_hash":"hash-content"}

    artifacts = FakeArtifactStore(); artifacts.objects[artifact.r2_key] = b"PNG-BYTES"
    destination = export_local("caelus", tmp_path/"rollback-export", CaelusCloudAdapter(), ExportStore(), artifacts)
    base = destination / "generated" / "2026-09-26" / "ru"
    assert json.loads((base/"content.json").read_text()) == payload
    assert json.loads((base/"status.json").read_text())["state"] == "approved"
    assert json.loads((base/"publication.json").read_text())["state"] == "published"
    assert (base/"cards"/"caelus_aries.png").read_bytes() == b"PNG-BYTES"
    assert json.loads((destination/"generated"/"_settings"/"content_diversity.json").read_text())["profile"] == "balanced"

def test_supabase_migration_rpc_contract_is_provider_generic():
    from kaban.cloud.supabase import SupabaseControlStore

    class Stub(SupabaseControlStore):
        def __init__(self): self.calls=[]
        def _rpc(self, name, payload):
            self.calls.append((name,payload))
            if name == "kaban_resolve_migration_content_set": return [{"content_set_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}]
            if name == "kaban_import_migration_bundle": return [{"content_set_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","revision_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}]
            return True

    store=Stub()
    content_set_id=store.resolve_migration_content_set("caelus","2026-09-26:ru",UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
    assert str(content_set_id)=="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result=store.import_migration_bundle(
        project_id="caelus",content_key="2026-09-26:ru",content_set_id=content_set_id,
        revision_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),source_identity="identity",source_path="generated/x/content.json",source_sha256="abc",
        content_date="2026-09-26",locale="ru",payload={"x":1},content_hash="hash",approved=False,status={},publication={},artifacts=[])
    assert result["revision_id"]==UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert [name for name,_ in store.calls[:2]] == ["kaban_resolve_migration_content_set","kaban_import_migration_bundle"]


def test_migration_sql_is_generic_and_tracks_stable_source_identity():
    sql_path = Path(__file__).resolve().parents[1] / "deploy" / "supabase" / "migrations" / "202609260006_stage4_migration.sql"
    sql = sql_path.read_text(encoding="utf-8").lower()
    assert "kaban_migration_identities" in sql
    assert "kaban_resolve_migration_content_set" in sql
    assert "kaban_import_migration_bundle" in sql
    assert "kaban_import_migration_setting" in sql
    assert "kaban_import_migration_history" in sql
    assert "source_identity" in sql
    assert "caelus" not in sql

def test_migration_cli_defaults_to_dry_run_and_requires_explicit_apply():
    import kaban.cloud.cli as cloud_cli
    assert hasattr(cloud_cli, "build_parser")
    parser = cloud_cli.build_parser()
    args = parser.parse_args(["migrate-local","--project","caelus","--generated","./generated","--runtime","./runtime","--manifest","migration.json"])
    assert args.command == "migrate-local"
    assert args.apply is False
    applied = parser.parse_args(["migrate-local","--project","caelus","--generated","./generated","--runtime","./runtime","--apply","--manifest","migration.json"])
    assert applied.apply is True

def test_applied_manifest_readback_verifies_database_identities_and_r2_hashes(tmp_path):
    generated, runtime = _fixture(tmp_path)
    store = FakeMigrationStore(); artifacts = FakeArtifactStore()
    manifest = tmp_path / "migration.json"
    migrate_local("caelus", generated, runtime, CaelusCloudAdapter(), store, artifacts, apply=True, manifest_path=manifest)
    assert verify_manifest(manifest, source_root=tmp_path, store=store, artifacts=artifacts).ok is True
    key = next(iter(artifacts.objects))
    artifacts.objects[key] = b"CORRUPTED"
    report = verify_manifest(manifest, source_root=tmp_path, store=store, artifacts=artifacts)
    assert report.ok is False
    assert "generated/2026-09-26/ru/cards/caelus_aries.png" in report.mismatches
