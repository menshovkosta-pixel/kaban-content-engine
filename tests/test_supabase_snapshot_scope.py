from datetime import date
from uuid import UUID

from kaban.cloud.models import MaterializationSpec
from kaban.cloud.supabase import SupabaseControlStore


TARGET_SET = UUID("11111111-1111-4111-8111-111111111111")
OLD_SET = UUID("22222222-2222-4222-8222-222222222222")

TARGET_REV = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TARGET_OLD_REV = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OLD_REV = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class SnapshotFixtureStore(SupabaseControlStore):
    def __init__(self):
        super().__init__("https://example.supabase.co", "test-service-key")

    def _request(self, method, path, *, payload=None, query=None):
        query = query or {}

        if path == "kaban_content_sets":
            return [
                {
                    "content_set_id": str(TARGET_SET),
                    "project_id": "caelus",
                    "content_key": "2099-01-18:ru",
                    "content_date": "2099-01-18",
                    "locale": "ru",
                    "version": 1,
                    "current_revision_id": str(TARGET_REV),
                    "approved_revision_id": None,
                    "approved_content_hash": None,
                },
                {
                    "content_set_id": str(OLD_SET),
                    "project_id": "caelus",
                    "content_key": "2099-01-17:ru",
                    "content_date": "2099-01-17",
                    "locale": "ru",
                    "version": 1,
                    "current_revision_id": str(OLD_REV),
                    "approved_revision_id": None,
                    "approved_content_hash": None,
                },
            ]

        if path == "kaban_content_revisions":
            revision_filter = query.get("revision_id")

            if revision_filter == f"eq.{TARGET_REV}":
                return [{
                    "revision_id": str(TARGET_REV),
                    "content_set_id": str(TARGET_SET),
                    "revision_no": 2,
                    "payload": {"iso_date": "2099-01-18", "language": "ru"},
                    "content_hash": "target-current",
                    "created_at": "2099-01-18T00:00:00+00:00",
                    "project_id": "caelus",
                }]

            if revision_filter == f"eq.{OLD_REV}":
                return [{
                    "revision_id": str(OLD_REV),
                    "content_set_id": str(OLD_SET),
                    "revision_no": 1,
                    "payload": {"iso_date": "2099-01-17", "language": "ru"},
                    "content_hash": "old-current",
                    "created_at": "2099-01-17T00:00:00+00:00",
                    "project_id": "caelus",
                }]

            return []

        if path == "kaban_artifacts":
            return [
                # Correct artifact: selected content set + current revision + requested kind.
                {
                    "artifact_id": "10000000-0000-4000-8000-000000000001",
                    "project_id": "caelus",
                    "content_set_id": str(TARGET_SET),
                    "revision_id": str(TARGET_REV),
                    "kind": "card_png",
                    "logical_name": "caelus_aries.png",
                    "r2_key": "projects/caelus/target/current/aries.png",
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                    "mime_type": "image/png",
                    "metadata": {},
                },
                # Wrong revision of the selected content set.
                {
                    "artifact_id": "10000000-0000-4000-8000-000000000002",
                    "project_id": "caelus",
                    "content_set_id": str(TARGET_SET),
                    "revision_id": str(TARGET_OLD_REV),
                    "kind": "card_png",
                    "logical_name": "caelus_old_revision.png",
                    "r2_key": "projects/caelus/target/old/old.png",
                    "sha256": "b" * 64,
                    "size_bytes": 124,
                    "mime_type": "image/png",
                    "metadata": {},
                },
                # Wrong artifact kind.
                {
                    "artifact_id": "10000000-0000-4000-8000-000000000003",
                    "project_id": "caelus",
                    "content_set_id": str(TARGET_SET),
                    "revision_id": str(TARGET_REV),
                    "kind": "debug_json",
                    "logical_name": "debug.json",
                    "r2_key": "projects/caelus/target/current/debug.json",
                    "sha256": "c" * 64,
                    "size_bytes": 125,
                    "mime_type": "application/json",
                    "metadata": {},
                },
                # Artifact from another content key.
                {
                    "artifact_id": "10000000-0000-4000-8000-000000000004",
                    "project_id": "caelus",
                    "content_set_id": str(OLD_SET),
                    "revision_id": str(OLD_REV),
                    "kind": "card_png",
                    "logical_name": "caelus_old_day.png",
                    "r2_key": "projects/caelus/old/current/old-day.png",
                    "sha256": "d" * 64,
                    "size_bytes": 126,
                    "mime_type": "image/png",
                    "metadata": {},
                },
            ]

        if path == "kaban_project_settings":
            return []

        raise AssertionError(f"Unexpected request: {method} {path} {query}")


def test_load_snapshot_scopes_content_sets_and_artifacts_to_materialization_spec():
    store = SnapshotFixtureStore()

    snapshot = store.load_snapshot(
        "caelus",
        MaterializationSpec(
            content_keys=("2099-01-18:ru",),
            artifact_kinds=("card_png",),
        ),
    )

    assert [item.content_key for item in snapshot.content_sets] == [
        "2099-01-18:ru"
    ]

    assert len(snapshot.artifacts) == 1

    artifact = snapshot.artifacts[0]
    assert artifact.content_set_id == TARGET_SET
    assert artifact.revision_id == TARGET_REV
    assert artifact.kind == "card_png"
    assert artifact.logical_name == "caelus_aries.png"