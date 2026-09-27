from datetime import date
from pathlib import Path
import runpy
import tempfile

from projects.caelus.quality import validate_content
from projects.caelus.uniqueness.rules import UniquenessRules


ns = runpy.run_path("tests/test_diversity.py")
make_payload = ns["make_payload"]


def test_detects_cross_sign_cross_field_exact_repetition():
    target = date(2099, 3, 3)
    payload = make_payload(target.isoformat())

    payload["signs"]["taurus"]["career_money"] = payload["signs"]["aries"]["card"]

    with tempfile.TemporaryDirectory() as td:
        issues, _ = validate_content(
            payload,
            generated_dir=Path(td),
            language="ru",
            target=target,
            rules=UniquenessRules(history_days=90),
        )

    conflicts = [
        issue
        for issue in issues
        if issue.get("severity") == "error"
        and issue.get("code") == "cross_field_repetition"
        and issue.get("sign") == "taurus"
        and issue.get("field") == "career_money"
    ]

    assert conflicts, issues


def test_detects_cross_sign_cross_field_repeated_opening():
    target = date(2099, 3, 4)
    payload = make_payload(target.isoformat())

    opening = (
        "Сегодня полезно проверить альтернативный маршрут, "
        "прежде чем возвращаться к старому плану."
    )

    payload["signs"]["aries"]["card"] = (
        opening
        + " Оставьте пространство для спокойной реакции."
    )

    payload["signs"]["gemini"]["general"] = (
        opening
        + " Затем определите один конкретный следующий шаг."
    )

    with tempfile.TemporaryDirectory() as td:
        issues, _ = validate_content(
            payload,
            generated_dir=Path(td),
            language="ru",
            target=target,
            rules=UniquenessRules(history_days=90),
        )

    conflicts = [
        issue
        for issue in issues
        if issue.get("severity") == "error"
        and issue.get("code") == "cross_field_repetition"
        and issue.get("sign") == "gemini"
        and issue.get("field") == "general"
    ]

    assert conflicts, issues