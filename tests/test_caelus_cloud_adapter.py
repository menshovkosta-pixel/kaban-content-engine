from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from kaban.cloud.models import CanonicalSnapshot, ContentSetSnapshot, ExecutionCommand, RevisionSnapshot
from kaban.cloud.workspace import execution_workspace
from projects.caelus.cloud_adapter import adapter

class NoArtifacts:
    def download(self,*a,**k): raise AssertionError("unexpected artifact")


def test_materializes_content_status_history_and_settings(tmp_path):
    cs=uuid4(); rev=RevisionSnapshot(uuid4(),cs,1,{"iso_date":"2026-09-26","language":"ru","signs":{}},"h",datetime.now(timezone.utc))
    hist=RevisionSnapshot(uuid4(),uuid4(),1,{"iso_date":"2026-09-25","language":"ru","signs":{}},"hh",datetime.now(timezone.utc))
    snap=CanonicalSnapshot("caelus",(ContentSetSnapshot(cs,"caelus","2026-09-26:ru",None,"ru",1,rev,rev.revision_id,"h"),),(hist,),{"content_diversity":{"profile":"strict"}},(),{})
    cmd=ExecutionCommand(uuid4(),"caelus","save","2026-09-26:ru",cs,1,{},"user","x")
    with execution_workspace(tmp_path,cmd.execution_id,"caelus") as paths:
        state=adapter.materialize(cmd,snap,paths,NoArtifacts())
        assert (paths.generated/"2026-09-26"/"ru"/"content.json").is_file()
        assert (paths.generated/"2026-09-25"/"ru"/"content.json").is_file()
        assert state.data["day"]=="2026-09-26"
