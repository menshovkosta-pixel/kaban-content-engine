import io, json, urllib.error
from uuid import uuid4
import pytest
from kaban.cloud.models import ExecutionCommand
from kaban.cloud.supabase import SupabaseControlStore
from kaban.cloud.contracts import LeaseConflict

class Response:
    def __init__(self, value): self.value=value
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return json.dumps(self.value).encode()


def test_create_command_is_project_scoped_and_idempotent():
    calls=[]; eid=uuid4()
    def opener(req, timeout):
        calls.append((req.full_url, json.loads(req.data)))
        return Response(str(eid))
    store=SupabaseControlStore("https://example.supabase.co", "secret", opener=opener)
    cmd=ExecutionCommand(eid,"caelus","generate","2026-09-26:ru",None,None,{},"scheduler","slot-x")
    assert store.create_or_get_command(cmd)==eid
    assert calls[0][1]["p_project_id"]=="caelus"


def test_secret_is_redacted_from_http_error():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO(b"secret leaked"))
    store=SupabaseControlStore("https://example.supabase.co", "secret", opener=opener)
    with pytest.raises(RuntimeError) as exc:
        store._request("GET","kaban_projects")
    assert "secret" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_stale_fence_maps_to_lease_conflict():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO(b"LEASE_CONFLICT"))
    store=SupabaseControlStore("https://example.supabase.co", "secret", opener=opener)
    with pytest.raises(LeaseConflict):
        store.start_execution(uuid4(),"runner",60)


def test_upsert_schedule_slot_persists_absolute_slot_metadata():
    calls=[]; eid=uuid4()
    def opener(req, timeout):
        calls.append((req.full_url, json.loads(req.data)))
        return Response({"execution_id": str(eid), "created": True})
    store=SupabaseControlStore("https://example.supabase.co", "secret", opener=opener)
    cmd=ExecutionCommand(eid,"caelus","generate_ru","2026-09-26:ru",None,None,{"language":"ru"},"scheduler","schedule:generate_ru:slot-x")
    from datetime import datetime, timezone, timedelta
    scheduled=datetime(2026,9,26,18,0,tzinfo=timezone.utc)
    created=store.upsert_schedule_slot(
        command=cmd, job_id="generate_ru", slot_id="2026-09-26T18:00:00+00:00",
        scheduled_for=scheduled,
        misfire_deadline_at=scheduled+timedelta(minutes=30),
        retry_deadline_at=scheduled+timedelta(minutes=120),
    )
    assert created is True
    payload=calls[0][1]
    assert payload["p_project_id"] == "caelus"
    assert payload["p_job_id"] == "generate_ru"
    assert payload["p_slot_id"] == "2026-09-26T18:00:00+00:00"
    assert payload["p_scheduled_for"] == scheduled.isoformat()


def test_upsert_schedule_slot_persists_wait_condition():
    calls=[]; eid=uuid4()
    def opener(req, timeout):
        calls.append(json.loads(req.data))
        return Response({"execution_id": str(eid), "created": True})
    store=SupabaseControlStore("https://example.supabase.co", "secret", opener=opener)
    cmd=ExecutionCommand(eid,"caelus","publish","2026-09-26:ru",None,None,{"language":"ru"},"scheduler","schedule:publish_ru:slot-x")
    from datetime import datetime, timezone, timedelta
    scheduled=datetime(2026,9,26,20,0,tzinfo=timezone.utc)
    wait={"kind":"content_approved","project_id":"caelus","data":{"content_key":"2026-09-26:ru"}}
    store.upsert_schedule_slot(
        command=cmd, job_id="publish_ru", slot_id="slot-x", scheduled_for=scheduled,
        misfire_deadline_at=scheduled+timedelta(minutes=30),
        retry_deadline_at=scheduled+timedelta(minutes=180), wait_condition=wait,
    )
    assert calls[0]["p_wait_condition"] == wait
