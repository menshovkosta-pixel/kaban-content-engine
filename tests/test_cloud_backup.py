from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from kaban.cloud.backup import create_backup


class Store:
    def backup_projection(self, project_id):
        return {
            "project":{"project_id":project_id,"config_hash":"abc"},
            "content_sets":[{"content_key":"2026-09-26:ru","version":3,"current_revision_id":"r1"}],
            "settings":{"content_diversity":{"profile":"balanced"}},
            "executions":[],"scheduler_jobs":[],"publication_runs":[],"publication_steps":[],"approvals":[],
        }


class Artifacts:
    def __init__(self): self.calls=[]
    def put_immutable(self, project_id,key,source,expected_sha256):
        data=Path(source).read_bytes(); self.calls.append((project_id,key,data,expected_sha256))
        return type("Stored",(),{"r2_key":key,"sha256":expected_sha256,"size_bytes":len(data),"mime_type":"application/json"})()


def test_backup_is_compact_json_and_uses_execution_scoped_immutable_key(tmp_path):
    artifacts=Artifacts()
    stored=create_backup("caelus",UUID("11111111-1111-4111-8111-111111111111"),Store(),artifacts,temp_root=tmp_path,now="2026-09-26T01:02:03+00:00")
    assert stored.r2_key=="projects/caelus/backups/2026/09/11111111-1111-4111-8111-111111111111.json"
    payload=json.loads(artifacts.calls[0][2])
    assert payload["project"]["project_id"]=="caelus"
    assert "content_sets" in payload
    assert b"\n  " not in artifacts.calls[0][2]
