from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from kaban.cloud.models import CanonicalSnapshot, ChangeSet, ExecutionCommand, ExecutionRecord, LeaseClaim, ProjectExecutionResult, ProjectWorkspaceState, CommitResult
from kaban.cloud.runner import run_execution

class Store:
    def __init__(self,cmd): self.cmd=cmd; self.started=0; self.commits=0
    def load_execution(self,eid): return ExecutionRecord(self.cmd,"queued")
    def start_execution(self,eid,owner,lease_seconds):
        self.started+=1
        if self.started>1:return None
        return LeaseClaim(owner,1,datetime.now(timezone.utc))
    def load_snapshot(self,p,s): return CanonicalSnapshot(p)
    def claim_resource(self,*a,**k): return LeaseClaim(k.get("owner","r"),1,datetime.now(timezone.utc))
    def commit_changes(self,*a,**k): self.commits+=1; return CommitResult(None,None,None)
    def finish_execution(self,*a,**k): pass
class Adapter:
    calls=0
    def materialization_spec(self,c): from kaban.cloud.models import MaterializationSpec; return MaterializationSpec()
    def resource_key(self,c,s): return None
    def materialize(self,c,s,w,a): return ProjectWorkspaceState({})
    def execute(self,c,s): self.calls+=1; return ProjectExecutionResult("success")
    def collect(self,c,s,b): return ChangeSet()
class Registry:
    def get(self,p): return SimpleNamespace(cloud=SimpleNamespace(adapter="test_cloud_runner:ADAPTER"))
ADAPTER=Adapter()


def test_duplicate_dispatch_is_noop(tmp_path):
    eid=uuid4(); cmd=ExecutionCommand(eid,"caelus","noop","2026-09-26:ru",None,None,{},"scheduler","x"); store=Store(cmd)
    assert run_execution(eid,store=store,artifacts=object(),registry=Registry(),temp_root=tmp_path,owner="a").outcome=="success"
    assert run_execution(eid,store=store,artifacts=object(),registry=Registry(),temp_root=tmp_path,owner="b").outcome=="duplicate"
    assert ADAPTER.calls==1

class MutatingAdapter(Adapter):
    def collect(self,c,s,b): return ChangeSet(setting_updates={"content_diversity":{"profile":"balanced"}})
MUTATING=MutatingAdapter()
class MutatingRegistry:
    def get(self,p): return SimpleNamespace(cloud=SimpleNamespace(adapter="test_cloud_runner:MUTATING"))


def test_successful_mutation_invokes_backup_once_and_noop_does_not(tmp_path):
    eid=uuid4(); cmd=ExecutionCommand(eid,"caelus","save","2026-09-26:ru",None,None,{},"user","backup-1")
    store=Store(cmd); calls=[]
    run_execution(eid,store=store,artifacts=object(),registry=MutatingRegistry(),temp_root=tmp_path,owner="runner",backup_hook=lambda project_id,execution_id: calls.append((project_id,execution_id)))
    assert calls == [("caelus",eid)]

    eid2=uuid4(); cmd2=ExecutionCommand(eid2,"caelus","noop","2026-09-26:ru",None,None,{},"user","backup-2")
    store2=Store(cmd2); calls2=[]
    run_execution(eid2,store=store2,artifacts=object(),registry=Registry(),temp_root=tmp_path,owner="runner",backup_hook=lambda *args: calls2.append(args))
    assert calls2 == []

def test_backup_failure_after_commit_does_not_turn_committed_execution_into_retry(tmp_path):
    eid=uuid4(); cmd=ExecutionCommand(eid,"caelus","save","2026-09-26:ru",None,None,{},"user","backup-fail")
    store=Store(cmd)
    outcome=run_execution(
        eid,store=store,artifacts=object(),registry=MutatingRegistry(),temp_root=tmp_path,owner="runner",
        backup_hook=lambda *_: (_ for _ in ()).throw(RuntimeError("backup unavailable")),
    )
    assert outcome.outcome == "success"
    assert "backup" in outcome.message.lower()
    assert store.commits == 1
