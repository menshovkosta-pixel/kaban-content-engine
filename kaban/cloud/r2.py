from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from .contracts import ImmutableArtifactConflict, ProjectIsolationError
from .models import ArtifactRef, StoredArtifact


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class R2ArtifactStore:
    def __init__(self, *, bucket: str, endpoint_url: str | None = None, access_key_id: str | None = None, secret_access_key: str | None = None, s3_client: Any = None):
        self.bucket = bucket

        if endpoint_url:
            parsed = urlparse(endpoint_url)
            if parsed.path not in {"", "/"}:
                raise ValueError("R2 endpoint must be the account root URL without a bucket path")

        if s3_client is None:
            import boto3
            s3_client = boto3.client("s3", endpoint_url=endpoint_url, aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key, region_name="auto")
        self.client = s3_client

    @staticmethod
    def _validate_project(project_id: str) -> str:
        if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
            raise ValueError("Некорректный project_id")
        return project_id

    def object_key(self, project_id: str, content_set_id: UUID, revision_id: UUID, kind: str, logical_name: str) -> str:
        self._validate_project(project_id)
        for value in (kind, logical_name):
            parts = PurePosixPath(value).parts
            if not value or value.startswith("/") or ".." in parts:
                raise ValueError("Некорректный R2 logical path")
        return f"projects/{project_id}/content/{content_set_id}/revisions/{revision_id}/{kind}/{logical_name}"

    def _require_prefix(self, project_id: str, key: str) -> None:
        prefix = f"projects/{self._validate_project(project_id)}/"
        if not key.startswith(prefix) or ".." in PurePosixPath(key).parts:
            raise ProjectIsolationError("R2 key выходит за project prefix")

    def _head(self, key: str) -> dict[str, Any] | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"} or isinstance(exc, KeyError):
                return None
            raise

    def put_immutable(self, project_id: str, key: str, source: Path, expected_sha256: str) -> StoredArtifact:
        self._require_prefix(project_id, key)
        source = Path(source)
        actual = _sha256(source)
        if actual != expected_sha256:
            raise ValueError("Локальный SHA-256 не совпадает с expected_sha256")
        head = self._head(key)
        if head is not None:
            remote_hash = (head.get("Metadata") or {}).get("sha256")
            if remote_hash == actual and int(head.get("ContentLength", -1)) == source.stat().st_size:
                return StoredArtifact(key, actual, source.stat().st_size, (head.get("ContentType") or "application/octet-stream"))
            raise ImmutableArtifactConflict(f"R2 object уже существует с другим содержимым: {key}")
        content_type = "image/png" if source.suffix.lower()==".png" else "application/octet-stream"
        self.client.upload_file(str(source), self.bucket, key, ExtraArgs={"Metadata":{"sha256":actual},"ContentType":content_type})
        head = self._head(key)
        if head is None or int(head.get("ContentLength", -1)) != source.stat().st_size or (head.get("Metadata") or {}).get("sha256") != actual:
            raise RuntimeError("R2 upload verification failed")
        return StoredArtifact(key, actual, source.stat().st_size, head.get("ContentType") or content_type)

    def download(self, project_id: str, artifact: ArtifactRef, destination: Path) -> None:
        self._require_prefix(project_id, artifact.r2_key)
        if artifact.project_id != project_id:
            raise ProjectIsolationError("Artifact принадлежит другому project_id")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, artifact.r2_key, str(destination))
        if _sha256(destination) != artifact.sha256:
            destination.unlink(missing_ok=True)
            raise RuntimeError("R2 download SHA-256 mismatch")

    def verify_object(self, project_id: str, key: str, expected_sha256: str, expected_size: int) -> bool:
        self._require_prefix(project_id, key)
        head = self._head(key)
        if head is None:
            return False
        return (
            int(head.get("ContentLength", -1)) == int(expected_size)
            and (head.get("Metadata") or {}).get("sha256") == expected_sha256
        )

    def delete_orphan(self, project_id: str, key: str) -> None:
        self._require_prefix(project_id, key)
        self.client.delete_object(Bucket=self.bucket, Key=key)
