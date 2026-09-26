from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kaban.cloud.usage import UsageSample, quota_level, serialize_usage, cleanup_orphans


def test_quota_levels_use_configured_limits():
    assert quota_level(69,100)=="ok"
    assert quota_level(70,100)=="warning"
    assert quota_level(85,100)=="high"
    assert quota_level(95,100)=="critical"


def test_estimated_egress_cannot_be_serialized_as_provider_exact():
    sample=UsageSample(metric="egress_bytes",value=123,unit="bytes",quality="estimated",limit=1000)
    payload=serialize_usage(sample)
    assert payload["quality"]=="estimated"
    assert payload["quality"]!="provider_exact"
    with pytest.raises(ValueError):
        UsageSample(metric="egress_bytes",value=123,unit="bytes",quality="made_up",limit=1000)


def test_orphan_cleanup_rechecks_reference_immediately_before_delete():
    refs={"projects/caelus/a.png":False}
    deleted=[]
    now=datetime(2026,9,26,12,0,tzinfo=timezone.utc)
    def referenced(key): return refs[key]
    def before_delete(key): refs[key]=True
    result=cleanup_orphans(
        [("projects/caelus/a.png", now-timedelta(hours=2))],
        is_referenced=referenced,
        delete=lambda key: deleted.append(key),
        now=now,
        grace_period=timedelta(hours=1),
        before_delete=before_delete,
    )
    assert result.deleted==()
    assert result.skipped_referenced==("projects/caelus/a.png",)
    assert deleted==[]


def test_orphan_cleanup_never_deletes_before_grace_period():
    now=datetime(2026,9,26,12,0,tzinfo=timezone.utc)
    deleted=[]
    result=cleanup_orphans(
        [("projects/caelus/recent.png", now-timedelta(minutes=59))],
        is_referenced=lambda key: False,
        delete=lambda key: deleted.append(key),
        now=now,
        grace_period=timedelta(hours=1),
    )
    assert result.deleted==()
    assert result.skipped_grace_period==("projects/caelus/recent.png",)
    assert deleted==[]


def test_observability_migration_adds_quality_heartbeat_and_exact_db_size():
    from pathlib import Path
    sql = Path("deploy/supabase/migrations/202609260007_stage4_observability.sql").read_text(encoding="utf-8")
    assert "kaban_runtime_health" in sql
    assert "kaban_record_cron_tick" in sql
    assert "kaban_database_size_bytes" in sql
    assert "quality" in sql
    assert "provider_exact" in sql and "db_exact" in sql and "estimated" in sql
