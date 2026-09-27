from datetime import date
from pathlib import Path
import runpy
import tempfile
from unittest.mock import patch


ns = runpy.run_path("tests/test_diversity.py")

workflow = ns["workflow"]
make_payload = ns["make_payload"]
write_payload = ns["write_payload"]


def test_manual_regenerate_conflicts_repairs_cross_field_target_only():
    target = date(2099, 3, 2)
    current = make_payload(target.isoformat())

    original_card = current["signs"]["aries"]["card"]
    original_career = current["signs"]["aries"]["career_money"]

    # Deliberately create one same-sign cross-field conflict.
    current["signs"]["aries"]["career_money"] = original_card

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_payload(root, current)

        with patch.object(workflow, "GENERATED", root), patch.object(
            workflow,
            "rebuild",
        ):
            payload, issues, changed = workflow.regenerate_conflicts(
                target.isoformat(),
                "ru",
            )

    assert changed == ["aries.career_money"]

    # Base/card content must remain untouched.
    assert payload["signs"]["aries"]["card"] == original_card

    # Only the targeted specialized field must be rewritten.
    assert payload["signs"]["aries"]["career_money"] != original_card
    assert payload["signs"]["aries"]["career_money"] != original_career

    remaining = [
        item
        for item in issues
        if item.get("severity") == "error"
        and item.get("code") == "cross_field_repetition"
        and item.get("sign") == "aries"
        and item.get("field") == "career_money"
    ]
    assert remaining == []

    feedback = payload["generation"]["uniqueness"]["last_regeneration"]
    assert feedback["kind"] == "conflicts"
    assert feedback["changed"] == ["aries.career_money"]
    assert feedback["resolved"] is True