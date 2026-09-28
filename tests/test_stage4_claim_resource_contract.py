from pathlib import Path


def test_claim_resource_returns_no_row_when_conflict_rejects_claim():
    sql = Path(
        "deploy/supabase/migrations/202609280010_fix_resource_claim_null_row.sql"
    ).read_text(encoding="utf-8").lower()

    assert "create or replace function kaban_claim_resource" in sql
    assert "if owner is null then" in sql
    assert "return;" in sql
    assert "return next;" in sql