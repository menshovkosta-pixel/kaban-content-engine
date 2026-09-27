import pytest

from kaban.cloud.r2 import R2ArtifactStore


def test_r2_endpoint_must_not_contain_bucket_path():
    with pytest.raises(ValueError, match="R2 endpoint"):
        R2ArtifactStore(
            bucket="kaban",
            endpoint_url="https://33c36f89446098de6dd15cea08e6001f.r2.cloudflarestorage.com/kaban",
            s3_client=object(),
        )


def test_r2_endpoint_accepts_account_root():
    store = R2ArtifactStore(
        bucket="kaban",
        endpoint_url="https://33c36f89446098de6dd15cea08e6001f.r2.cloudflarestorage.com",
        s3_client=object(),
    )

    assert store.bucket == "kaban"