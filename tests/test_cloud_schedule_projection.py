from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from kaban.cloud.schedule_projection import project_schedule
from kaban.scheduler.cron import cron_next

class Store:
    def __init__(self): self.keys=set(); self.rows=[]
    def upsert_schedule_slot(self, **row):
        key=(row["command"].project_id,row["job_id"],row["slot_id"])
        if key in self.keys:return False
        self.keys.add(key); self.rows.append(row); return True
class Registry:
    def registered(self):
        job=SimpleNamespace(id="generate_ru",handler="generate",enabled=True,cron="0 6 * * *",params={"language":"ru"},misfire_grace_minutes=30,retry=SimpleNamespace(window_minutes=120))
        return (SimpleNamespace(id="caelus",timezone="Pacific/Auckland",default_language="ru",automation=SimpleNamespace(enabled=True,jobs=(job,))),)


def test_projection_is_idempotent_and_uses_utc_slots():
    store=Store(); start=datetime(2026,9,26,tzinfo=timezone.utc)
    first=project_schedule(Registry(),store,from_utc=start,horizon_days=3)
    second=project_schedule(Registry(),store,from_utc=start,horizon_days=3)
    assert first.slots_seen==second.slots_seen
    assert len(store.rows)==first.slots_seen
    assert all(row["scheduled_for"].tzinfo is not None for row in store.rows)


def test_cron_next_tracks_auckland_dst_offset_change():
    zone=ZoneInfo("Pacific/Auckland")
    before=cron_next("0 6 * * *",datetime(2026,9,25,6,1,tzinfo=zone))
    after=cron_next("0 6 * * *",before)
    assert before.utcoffset().total_seconds() == 12*3600
    assert after.utcoffset().total_seconds() == 13*3600


def test_caelus_publish_projection_carries_approval_wait_condition():
    class PublishRegistry:
        def registered(self):
            retry=SimpleNamespace(window_minutes=180)
            publish=SimpleNamespace(id="publish_ru",handler="publish",enabled=True,cron="0 8 * * *",params={"language":"ru"},misfire_grace_minutes=30,retry=retry)
            cloud=SimpleNamespace(adapter="projects.caelus.cloud_adapter:adapter")
            return (SimpleNamespace(id="caelus",timezone="Pacific/Auckland",default_language="ru",automation=SimpleNamespace(enabled=True,jobs=(publish,)),cloud=cloud),)
    store=Store()
    project_schedule(PublishRegistry(),store,from_utc=datetime(2026,9,26,tzinfo=timezone.utc),horizon_days=1)
    assert store.rows
    condition=store.rows[0]["wait_condition"]
    assert condition["kind"] == "content_approved"
    assert condition["project_id"] == "caelus"
    assert condition["data"]["content_key"].endswith(":ru")
