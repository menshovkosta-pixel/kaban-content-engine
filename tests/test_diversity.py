from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import projects.caelus.workflow as workflow
from projects.caelus.provider import MockProvider
from projects.caelus.domain import SIGN_NAMES, SIGN_ORDER
from projects.caelus.uniqueness.settings import (
    DiversitySettings,
    load_diversity_settings,
    save_diversity_settings_file,
)


def make_payload(iso_date: str = "2026-09-23", language: str = "ru") -> dict:
    signs = MockProvider(language).generate(system_prompt="", user_prompt="")
    return {
        "schema_version": 2,
        "iso_date": iso_date,
        "date": iso_date,
        "language": language,
        "brand": "CAELUS",
        "generation": {"provider": "mock", "model": None},
        "signs": {
            sign: {"name": SIGN_NAMES[language][sign], **signs[sign]}
            for sign in SIGN_ORDER
        },
    }


def write_payload(root: Path, payload: dict) -> Path:
    path = root / payload["iso_date"] / payload["language"] / "content.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class DiversitySettingsTests(unittest.TestCase):
    def test_presets_change_real_validation_thresholds(self):
        self.assertEqual(DiversitySettings(profile="soft").to_rules().hard_threshold, 0.88)
        self.assertEqual(DiversitySettings(profile="balanced").to_rules().hard_threshold, 0.84)
        self.assertEqual(DiversitySettings(profile="strict").to_rules().hard_threshold, 0.80)
        self.assertEqual(DiversitySettings(profile="very_strict").to_rules().hard_threshold, 0.75)

    def test_custom_threshold_and_history_window_are_user_controlled(self):
        settings = DiversitySettings(profile="custom", history_days=180, custom_threshold=0.73)
        rules = settings.to_rules()
        self.assertEqual(rules.history_days, 180)
        self.assertEqual(rules.hard_threshold, 0.73)
        self.assertLess(rules.warning_threshold, rules.hard_threshold)

    def test_settings_round_trip_to_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "content_diversity.json"
            expected = DiversitySettings(profile="very_strict", history_days=365, custom_threshold=0.77)
            save_diversity_settings_file(path, expected)
            actual = load_diversity_settings(path, DiversitySettings())
        self.assertEqual(actual.profile, "very_strict")
        self.assertEqual(actual.history_days, 365)
        self.assertEqual(actual.custom_threshold, 0.77)


class DiversityWorkflowTests(unittest.TestCase):
    def test_preview_is_local_and_never_creates_provider(self):
        target = date(2026, 9, 23)
        current = make_payload(target.isoformat())
        previous = make_payload((target - timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_payload(root, current)
            write_payload(root, previous)
            with patch.object(workflow, "GENERATED", root), patch.object(
                workflow, "_provider", side_effect=AssertionError("preview must not call provider")
            ):
                result = workflow.preview_diversity(
                    target.isoformat(),
                    "ru",
                    DiversitySettings(profile="strict", history_days=90),
                )
        self.assertGreater(result["conflict_count"], 0)
        self.assertTrue(result["issues"])


    def test_regenerate_conflicts_records_batch_feedback(self):
        target = date(2026, 9, 23)
        current = make_payload(target.isoformat())
        previous = {
            "schema_version": 2,
            "iso_date": (target - timedelta(days=1)).isoformat(),
            "language": "ru",
            "signs": {
                "pisces": {"love": current["signs"]["pisces"]["love"]}
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_payload(root, current)
            write_payload(root, previous)
            with patch.object(workflow, "GENERATED", root), patch.object(workflow, "rebuild"):
                payload, _, changed = workflow.regenerate_conflicts(target.isoformat(), "ru")
        feedback = payload["generation"]["uniqueness"]["last_regeneration"]
        self.assertEqual(feedback["kind"], "conflicts")
        self.assertEqual(feedback["changed"], changed)
        self.assertGreaterEqual(feedback["attempts"], 1)
        self.assertGreaterEqual(feedback["before_similarity"], 0.80)
        self.assertLess(feedback["after_similarity"], feedback["before_similarity"])
        self.assertTrue(feedback["resolved"])

    def test_save_settings_does_not_regenerate_content(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(workflow, "GENERATED", root), patch.object(
                workflow, "_provider", side_effect=AssertionError("settings save must not call provider")
            ):
                saved = workflow.save_diversity_settings(
                    profile="very_strict",
                    history_days=180,
                    custom_threshold=0.72,
                )
                path = root / "_settings" / "content_diversity.json"
                self.assertTrue(path.exists())
                raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved.profile, "very_strict")
        self.assertEqual(raw["profile"], "very_strict")
        self.assertEqual(raw["history_days"], 180)

    def test_regenerate_conflicts_changes_only_detected_field(self):
        target = date(2026, 9, 23)
        current = make_payload(target.isoformat())
        previous = {
            "schema_version": 2,
            "iso_date": (target - timedelta(days=1)).isoformat(),
            "language": "ru",
            "signs": {
                "pisces": {"love": current["signs"]["pisces"]["love"]}
            },
        }
        before_pisces = dict(current["signs"]["pisces"])
        before_aries = dict(current["signs"]["aries"])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_payload(root, current)
            write_payload(root, previous)
            with patch.object(workflow, "GENERATED", root), patch.object(workflow, "rebuild"):
                payload, issues, changed = workflow.regenerate_conflicts(target.isoformat(), "ru")

        self.assertEqual(changed, ["pisces.love"])
        self.assertNotEqual(payload["signs"]["pisces"]["love"], before_pisces["love"])
        for field in ("card", "general", "career_money", "advice"):
            self.assertEqual(payload["signs"]["pisces"][field], before_pisces[field])
        self.assertEqual(payload["signs"]["aries"], before_aries)
        self.assertFalse(any(x.get("severity") == "error" and x.get("sign") == "pisces" and x.get("field") == "love" for x in issues))


if __name__ == "__main__":
    unittest.main()

class DiversityMetadataTests(unittest.TestCase):
    def test_new_generation_stores_semantic_metadata_for_each_sign(self):
        target = date(2026, 10, 1)
        with tempfile.TemporaryDirectory() as td:
            payload = workflow.generate_daily_content(
                target=target,
                language="ru",
                provider=MockProvider("ru"),
                generated_dir=Path(td),
                uniqueness_rules=DiversitySettings(profile="strict").to_rules(),
            )
        for sign in SIGN_ORDER:
            meta = payload["signs"][sign].get("_diversity")
            self.assertIsInstance(meta, dict)
            self.assertTrue(meta.get("theme"))
            self.assertTrue(meta.get("situation"))
            self.assertTrue(meta.get("tone"))
            self.assertTrue(meta.get("advice_pattern"))

    def test_profile_same_day_threshold_is_used_by_validation(self):
        from projects.caelus.quality import validate_content
        from projects.caelus.uniqueness.similarity import SimilarityResult

        payload = make_payload("2026-10-01")
        fake_similarity = SimilarityResult(0.82, 0.82, 0.7, 0.7, 0.7, False)
        with tempfile.TemporaryDirectory() as td, patch(
            "projects.caelus.validators.compare_texts", return_value=fake_similarity
        ):
            strict_issues, _ = validate_content(
                payload,
                generated_dir=Path(td),
                language="ru",
                target=date(2026, 10, 1),
                rules=DiversitySettings(profile="strict").to_rules(),
            )
            very_strict_issues, _ = validate_content(
                payload,
                generated_dir=Path(td),
                language="ru",
                target=date(2026, 10, 1),
                rules=DiversitySettings(profile="very_strict").to_rules(),
            )
        strict_same_day = [x for x in strict_issues if x.get("code") == "same_day_repetition"]
        very_strict_same_day = [x for x in very_strict_issues if x.get("code") == "same_day_repetition"]
        self.assertEqual(strict_same_day, [])
        self.assertTrue(very_strict_same_day)

class DiversityMetadataLifecycleTests(unittest.TestCase):
    def test_field_regeneration_marks_semantic_metadata_stale(self):
        from projects.caelus.generator import regenerate_daily_field

        target = date(2026, 10, 2)
        current = make_payload(target.isoformat())
        # Старые payload из make_payload не имеют metadata: добавляем реальную metadata как у v4.11.
        initial = MockProvider("ru").generate(system_prompt="", user_prompt="")
        current["signs"]["pisces"]["_diversity"] = dict(initial["pisces"]["_diversity"])
        with tempfile.TemporaryDirectory() as td:
            payload, _ = regenerate_daily_field(
                payload=current,
                target=target,
                language="ru",
                sign="pisces",
                field="love",
                provider=MockProvider("ru"),
                generated_dir=Path(td),
                rules=DiversitySettings(profile="strict").to_rules(),
            )
        meta = payload["signs"]["pisces"]["_diversity"]
        self.assertIn("love", meta.get("stale_fields", []))

class LegacyMetadataCompatibilityTests(unittest.TestCase):
    def test_failed_sign_regeneration_on_legacy_payload_raises_provider_error_not_type_error(self):
        from projects.caelus.generator import regenerate_daily_sign
        from projects.caelus.provider import ProviderError

        target = date(2026, 10, 3)
        current = make_payload(target.isoformat())
        current["signs"]["pisces"].pop("_diversity", None)  # имитация v4.10 до semantic metadata

        class EchoProvider:
            name = "echo"
            def generate(self, *, system_prompt: str, user_prompt: str):
                raise AssertionError("not used")
            def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str):
                raise AssertionError("not used")
            def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str):
                return {field: current["signs"][sign][field] for field in ("card", "general", "love", "career_money", "advice")}

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProviderError):
                regenerate_daily_sign(
                    payload=current,
                    target=target,
                    language="ru",
                    sign="pisces",
                    provider=EchoProvider(),
                    generated_dir=Path(td),
                    rules=DiversitySettings(profile="strict", max_regeneration_attempts=1).to_rules(),
                )
        self.assertEqual(current["signs"]["pisces"]["name"], SIGN_NAMES["ru"]["pisces"])


class DiversityPhase11Tests(unittest.TestCase):
    def test_custom_threshold_accepts_95_percent(self):
        settings = DiversitySettings(profile="custom", history_days=90, custom_threshold=0.95)
        self.assertEqual(settings.validate().to_rules().hard_threshold, 0.95)

    def test_history_issue_contains_current_and_matched_text_for_inspector(self):
        from projects.caelus.uniqueness.history import HistoricalText, HistoryIndex
        from projects.caelus.uniqueness.rules import UniquenessRules
        from projects.caelus.uniqueness.service import UniquenessService

        payload = make_payload("2026-09-25")
        current = payload["signs"]["aries"]["general"]
        history = HistoryIndex([
            HistoricalText("2026-09-01", "aries", "general", current),
        ], 90)
        issues = UniquenessService(UniquenessRules(
            history_days=90,
            warning_threshold=0.70,
            hard_threshold=0.80,
            same_day_hard_threshold=0.84,
            max_regeneration_attempts=3,
        )).validate(payload, history)
        issue = next(x for x in issues if x["sign"] == "aries" and x["field"] == "general")
        self.assertEqual(issue["current_text"], current)
        self.assertEqual(issue["matched_text"], current)

    def test_field_regeneration_records_before_after_feedback(self):
        target = date(2026, 9, 25)
        current = make_payload(target.isoformat())
        previous = make_payload((target - timedelta(days=1)).isoformat())
        previous["signs"]["aries"]["general"] = current["signs"]["aries"]["general"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_payload(root, current)
            write_payload(root, previous)
            content_file = root / target.isoformat() / "ru" / "content.json"
            with patch.object(workflow, "GENERATED", root), \
                 patch.object(workflow, "content_path", return_value=content_file), \
                 patch.object(workflow, "rebuild"), \
                 patch.object(workflow, "_reset_approval_after_change"):
                payload, _ = workflow.regenerate_field(target.isoformat(), "ru", "aries", "general")
        feedback = payload["generation"]["uniqueness"]["last_regeneration"]
        self.assertEqual(feedback["sign"], "aries")
        self.assertEqual(feedback["field"], "general")
        self.assertGreaterEqual(feedback["before_similarity"], 0.80)
        self.assertLess(feedback["after_similarity"], feedback["before_similarity"])
        self.assertGreaterEqual(feedback["attempts"], 1)
        self.assertTrue(feedback["resolved"])
