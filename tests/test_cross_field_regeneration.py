from datetime import date
from pathlib import Path
import tempfile

from projects.caelus.generator import generate_daily_content
from projects.caelus.provider import MockProvider
from projects.caelus.quality import validate_content
from projects.caelus.uniqueness.rules import UniquenessRules


class CrossFieldConflictProvider(MockProvider):
    def __init__(self):
        super().__init__("ru")
        self.generated_fields = []

    def generate(self, *, system_prompt: str, user_prompt: str):
        signs = super().generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        signs["aries"]["career_money"] = signs["aries"]["card"]
        return signs

    def generate_field(
        self,
        *,
        sign: str,
        field: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        self.generated_fields.append((sign, field))

        if sign == "aries" and field == "career_money":
            return (
                "В работе сегодня полезно проверить один конкретный результат "
                "и только после этого принимать финансовое решение."
            )

        return super().generate_field(
            sign=sign,
            field=field,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


def test_generation_auto_regenerates_cross_field_conflict_only_in_target_field():
    provider = CrossFieldConflictProvider()
    target = date(2099, 3, 1)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rules = UniquenessRules(
            history_days=90,
            max_regeneration_attempts=3,
        )

        payload = generate_daily_content(
            target=target,
            language="ru",
            provider=provider,
            generated_dir=root,
            uniqueness_rules=rules,
        )

        issues, _ = validate_content(
            payload,
            generated_dir=root,
            language="ru",
            target=target,
            rules=rules,
        )

    aries = payload["signs"]["aries"]

    assert aries["career_money"] != aries["card"]
    assert ("aries", "career_money") in provider.generated_fields
    assert ("aries", "card") not in provider.generated_fields

    remaining = [
        item
        for item in issues
        if item.get("severity") == "error"
        and item.get("code") == "cross_field_repetition"
        and item.get("sign") == "aries"
    ]

    assert remaining == []