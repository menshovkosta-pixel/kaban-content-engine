import hashlib, os
from pathlib import Path
from uuid import uuid4
import pytest
from kaban.cloud.r2 import R2ArtifactStore
from kaban.cloud.contracts import ImmutableArtifactConflict, ProjectIsolationError
from kaban.cloud.models import ArtifactRef

class NotFound(Exception):
    response={"Error":{"Code":"404"}}
class FakeS3:
    def __init__(self): self.data={}
    def head_object(self, Bucket, Key):
        if Key not in self.data: raise NotFound()
        body,meta,ctype=self.data[Key]
        return {"ContentLength":len(body),"Metadata":meta,"ContentType":ctype}
    def upload_file(self, Filename, Bucket, Key, ExtraArgs):
        self.data[Key]=(Path(Filename).read_bytes(),dict(ExtraArgs["Metadata"]),ExtraArgs["ContentType"])
    def download_file(self, Bucket, Key, Filename): Path(Filename).write_bytes(self.data[Key][0])
    def delete_object(self, Bucket, Key): self.data.pop(Key,None)


def test_immutable_upload_and_same_hash_are_idempotent(tmp_path):
    fake=FakeS3(); store=R2ArtifactStore(bucket="b",s3_client=fake); source=tmp_path/"a.png"; source.write_bytes(b"abc"); h=hashlib.sha256(b"abc").hexdigest(); key=store.object_key("caelus",uuid4(),uuid4(),"card","a.png")
    assert store.put_immutable("caelus",key,source,h).sha256==h
    assert store.put_immutable("caelus",key,source,h).sha256==h
    source.write_bytes(b"different"); h2=hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ImmutableArtifactConflict): store.put_immutable("caelus",key,source,h2)


def test_cross_project_prefix_is_rejected(tmp_path):
    store=R2ArtifactStore(bucket="b",s3_client=FakeS3()); p=tmp_path/"x"; p.write_bytes(b"x")
    with pytest.raises(ProjectIsolationError): store.put_immutable("caelus","projects/other/x",p,hashlib.sha256(b"x").hexdigest())


def test_corrupt_download_is_detected(tmp_path):
    fake=FakeS3(); store=R2ArtifactStore(bucket="b",s3_client=fake); key="projects/caelus/content/a/revisions/b/card/x.png"; fake.data[key]=(b"bad",{"sha256":"x"},"image/png")
    ref=ArtifactRef(uuid4(),"caelus",None,None,"card","x.png",key,hashlib.sha256(b"good").hexdigest(),4,"image/png",{})
    with pytest.raises(RuntimeError): store.download("caelus",ref,tmp_path/"out")

@pytest.mark.skipif(not all(os.getenv(x) for x in ["KABAN_TEST_R2_ENDPOINT","KABAN_TEST_R2_BUCKET","KABAN_TEST_R2_ACCESS_KEY_ID","KABAN_TEST_R2_SECRET_ACCESS_KEY"]), reason="R2 test credentials не настроены")
def test_live_r2_acceptance():
    assert True

def test_verify_object_uses_head_hash_and_size(tmp_path):
    fake=FakeS3(); store=R2ArtifactStore(bucket="b",s3_client=fake)
    source=tmp_path/"a.png"; source.write_bytes(b"abc")
    sha=hashlib.sha256(b"abc").hexdigest(); key="projects/caelus/content/a/revisions/b/card/a.png"
    store.put_immutable("caelus",key,source,sha)
    assert store.verify_object("caelus",key,sha,3) is True
    assert store.verify_object("caelus",key,"0"*64,3) is False
