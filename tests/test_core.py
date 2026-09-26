from __future__ import annotations

import json
import os
import sys
import types
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from projects.caelus.workflow import tomorrow_iso
import projects.caelus.workflow as workflow_module
from projects.caelus.generator import generate_daily_content, regenerate_daily_field
from projects.caelus.provider import MockProvider, OpenAIProvider
from projects.caelus.quality import validate_content
from projects.caelus.domain import CONTENT_FIELDS, SIGN_NAMES, SIGN_ORDER
from projects.caelus.uniqueness.history import HistoricalText, HistoryIndex, load_history
from projects.caelus.uniqueness.rules import UniquenessRules
from projects.caelus.uniqueness.service import UniquenessService
from projects.caelus.uniqueness.similarity import SimilarityResult
from projects.caelus.validators import get_field, has_errors, validate_payload
from kaban.ai.usage import UsageTracker, merge_usage


def make_payload(language: str = "ru", iso_date: str = "2026-09-22") -> dict[str, Any]:
    signs = MockProvider(language).generate(system_prompt="", user_prompt="")
    return {
        "schema_version": 2,
        "iso_date": iso_date,
        "date": iso_date,
        "language": language,
        "brand": "CAELUS",
        "signs": {
            sign: {"name": SIGN_NAMES[language][sign], **signs[sign]}
            for sign in SIGN_ORDER
        },
    }


def write_history(root: Path, payload: dict[str, Any]) -> None:
    path = root / payload["iso_date"] / payload["language"] / "content.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class StickyProvider:
    """Provider для проверки исчерпания regeneration: всегда возвращает тот же текст."""

    name = "sticky-test"

    def __init__(self, language: str = "ru"):
        self.signs = MockProvider(language).generate(system_prompt="", user_prompt="")

    def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        del system_prompt, user_prompt
        return {sign: dict(item) for sign, item in self.signs.items()}

    def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        del system_prompt, user_prompt
        return dict(self.signs[sign])

    def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        return str(self.signs[sign][field])


class ContentEngineTests(unittest.TestCase):
    def test_mock_provider_has_all_signs_and_fields(self):
        for language in ("ru", "en"):
            signs = MockProvider(language).generate(system_prompt="", user_prompt="")
            self.assertEqual(list(signs), SIGN_ORDER)
            for item in signs.values():
                self.assertTrue(all(field in item for field in CONTENT_FIELDS))

    def test_same_day_duplicate_is_detected(self):
        signs = MockProvider("ru").generate(system_prompt="", user_prompt="")
        signs["taurus"]["card"] = signs["aries"]["card"]
        payload = {"signs": {sign: {"name": sign, **signs[sign]} for sign in SIGN_ORDER}}
        issues = validate_payload(payload)
        self.assertTrue(has_errors(issues))
        duplicate = next(x for x in issues if x["code"] == "same_day_repetition")
        self.assertEqual(duplicate["matched_sign"], "taurus")
        self.assertGreaterEqual(duplicate["similarity"], 0.99)

    def test_legacy_aliases_are_readable(self):
        item = {"overview": "A", "relationships": "B", "work_money": "C", "card": "D", "advice": "E"}
        self.assertEqual(get_field(item, "general"), "A")
        self.assertEqual(get_field(item, "love"), "B")
        self.assertEqual(get_field(item, "career_money"), "C")

    def test_tomorrow_uses_machine_calendar(self):
        self.assertEqual(tomorrow_iso(), (date.today() + timedelta(days=1)).isoformat())

    def test_history_uses_calendar_90_day_window(self):
        target = date(2026, 9, 23)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = make_payload(iso_date=(target - timedelta(days=89)).isoformat())
            outside = make_payload(iso_date=(target - timedelta(days=91)).isoformat())
            write_history(root, inside)
            write_history(root, outside)
            index = load_history(root, "ru", target, 90)
            self.assertIn(inside["iso_date"], index.dates())
            self.assertNotIn(outside["iso_date"], index.dates())

    def test_historical_uniqueness_compares_same_sign_only(self):
        target = date(2026, 9, 23)
        current = make_payload(iso_date=target.isoformat())
        history_payload = {
            "iso_date": (target - timedelta(days=1)).isoformat(),
            "language": "ru",
            "signs": {
                "aries": {
                    "card": current["signs"]["taurus"]["card"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, history_payload)
            rules = UniquenessRules(history_days=90)
            issues, _ = validate_content(current, generated_dir=root, language="ru", target=target, rules=rules)
            taurus_history = [x for x in issues if x.get("sign") == "taurus" and str(x.get("code", "")).startswith("history_")]
            self.assertEqual(taurus_history, [])

    def test_exact_history_repeat_is_blocking_error(self):
        target = date(2026, 9, 23)
        current = make_payload(iso_date=target.isoformat())
        previous = make_payload(iso_date=(target - timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, previous)
            rules = UniquenessRules(history_days=90)
            issues, _ = validate_content(current, generated_dir=root, language="ru", target=target, rules=rules)
            aries_card = next(
                x for x in issues
                if x.get("code") == "history_repetition" and x.get("sign") == "aries" and x.get("field") == "card"
            )
            self.assertEqual(aries_card["matched_date"], previous["iso_date"])
            self.assertEqual(aries_card["similarity"], 1.0)

    def test_generation_auto_regenerates_historical_conflicts(self):
        target = date(2026, 9, 23)
        previous = make_payload(iso_date=(target - timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, previous)
            rules = UniquenessRules(history_days=90, max_regeneration_attempts=3)
            payload = generate_daily_content(
                target=target,
                language="ru",
                provider=MockProvider("ru"),
                generated_dir=root,
                uniqueness_rules=rules,
            )
            issues, _ = validate_content(payload, generated_dir=root, language="ru", target=target, rules=rules)
            self.assertFalse(has_errors(issues))
            meta = payload["generation"]["uniqueness"]
            self.assertGreaterEqual(meta["regeneration_attempts_used"], 1)
            self.assertLessEqual(meta["regeneration_attempts_used"], 3)
            self.assertEqual(set(meta["regenerated_signs"]), set(SIGN_ORDER))
            self.assertFalse(meta["exhausted_with_errors"])

    def test_generation_regenerates_only_conflicting_field(self):
        target = date(2026, 9, 23)
        base = make_payload(iso_date=target.isoformat())
        history_payload = {
            "iso_date": (target - timedelta(days=1)).isoformat(),
            "language": "ru",
            "signs": {
                "pisces": {"love": base["signs"]["pisces"]["love"]}
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, history_payload)
            payload = generate_daily_content(
                target=target,
                language="ru",
                provider=MockProvider("ru"),
                generated_dir=root,
                uniqueness_rules=UniquenessRules(history_days=90, max_regeneration_attempts=3),
            )
        meta = payload["generation"]["uniqueness"]
        self.assertEqual(meta["regenerated_fields"], ["pisces.love"])
        self.assertNotEqual(payload["signs"]["pisces"]["love"], base["signs"]["pisces"]["love"])
        for field in CONTENT_FIELDS:
            if field != "love":
                self.assertEqual(payload["signs"]["pisces"][field], base["signs"]["pisces"][field])

    def test_generation_returns_draftable_payload_when_regeneration_exhausted(self):
        target = date(2026, 9, 23)
        previous = make_payload(iso_date=(target - timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, previous)
            rules = UniquenessRules(history_days=90, max_regeneration_attempts=2)
            payload = generate_daily_content(
                target=target,
                language="ru",
                provider=StickyProvider("ru"),
                generated_dir=root,
                uniqueness_rules=rules,
            )
            issues, _ = validate_content(payload, generated_dir=root, language="ru", target=target, rules=rules)
            self.assertTrue(has_errors(issues))
            meta = payload["generation"]["uniqueness"]
            self.assertEqual(meta["regeneration_attempts_used"], 2)
            self.assertTrue(meta["exhausted_with_errors"])

    def test_80_percent_is_blocking_historical_threshold(self):
        rules = UniquenessRules()
        self.assertEqual(rules.hard_threshold, 0.80)
        payload = make_payload()
        history = HistoryIndex([
            HistoricalText("2026-09-21", "pisces", "love", "Исторический конфликтующий текст")
        ], 90)
        fake = SimilarityResult(0.81, 0.81, 0.5, 0.7, 0.6, False)
        with patch("projects.caelus.uniqueness.service.compare_texts", return_value=fake):
            issues = UniquenessService(rules).validate(payload, history)
        issue = next(x for x in issues if x["sign"] == "pisces" and x["field"] == "love")
        self.assertEqual(issue["severity"], "error")
        self.assertEqual(issue["code"], "history_repetition")

    def test_field_regeneration_changes_only_requested_field(self):
        target = date(2026, 9, 23)
        current = make_payload(iso_date=target.isoformat())
        previous = make_payload(iso_date=(target - timedelta(days=1)).isoformat())
        before = dict(current["signs"]["pisces"])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, previous)
            payload, _ = regenerate_daily_field(
                payload=current,
                target=target,
                language="ru",
                sign="pisces",
                field="love",
                provider=MockProvider("ru"),
                generated_dir=root,
                rules=UniquenessRules(history_days=90, max_regeneration_attempts=3),
            )
        self.assertNotEqual(payload["signs"]["pisces"]["love"], before["love"])
        for field in CONTENT_FIELDS:
            if field != "love":
                self.assertEqual(get_field(payload["signs"]["pisces"], field), get_field(before, field))

    def test_field_regeneration_prompt_contains_exact_historical_conflict(self):
        target = date(2026, 9, 23)
        current = make_payload(iso_date=target.isoformat())
        previous = make_payload(iso_date=(target - timedelta(days=1)).isoformat())
        captured: dict[str, str] = {}

        class CaptureProvider:
            name = "capture"

            def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
                raise AssertionError("not used")

            def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
                raise AssertionError("not used")

            def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str:
                captured["prompt"] = user_prompt
                return (
                    "В отношениях полезно сменить привычный формат общения: предложите совместное небольшое дело, "
                    "где оба смогут действовать без давления и заранее заданного результата."
                )

        old_text = previous["signs"]["pisces"]["love"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, previous)
            regenerate_daily_field(
                payload=current,
                target=target,
                language="ru",
                sign="pisces",
                field="love",
                provider=CaptureProvider(),
                generated_dir=root,
                rules=UniquenessRules(history_days=90, max_regeneration_attempts=1),
            )
        self.assertIn(old_text, captured["prompt"])
        self.assertIn("другую центральную тему", captured["prompt"])
        self.assertIn("другую конкретную ситуацию", captured["prompt"])

    def test_field_regeneration_retries_when_provider_echoes_original(self):
        target = date(2026, 9, 23)
        current = make_payload(iso_date=target.isoformat())
        original = current["signs"]["pisces"]["love"]

        class EchoThenNewProvider:
            name = "echo-then-new"

            def __init__(self):
                self.calls = 0

            def generate(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
                raise AssertionError("not used")

            def generate_sign(self, *, sign: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
                raise AssertionError("not used")

            def generate_field(self, *, sign: str, field: str, system_prompt: str, user_prompt: str) -> str:
                self.calls += 1
                if self.calls == 1:
                    return original
                return (
                    "Вместо анализа намёков предложите близкому человеку выбрать общее занятие на вечер. "
                    "Совместное действие даст больше ясности, чем попытка заранее угадать настроение."
                )

        provider = EchoThenNewProvider()
        with tempfile.TemporaryDirectory() as td:
            payload, _ = regenerate_daily_field(
                payload=current,
                target=target,
                language="ru",
                sign="pisces",
                field="love",
                provider=provider,
                generated_dir=Path(td),
                rules=UniquenessRules(history_days=90, max_regeneration_attempts=3),
            )
        self.assertEqual(provider.calls, 2)
        self.assertNotEqual(payload["signs"]["pisces"]["love"], original)

    def test_usage_tracker_uses_actual_api_fields_without_double_counting_reasoning(self):
        tracker = UsageTracker("gpt-5.6-luna")
        tracker.add_openai_usage({
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "prompt_tokens_details": {"cached_tokens": 200},
            "completion_tokens_details": {"reasoning_tokens": 100},
        })
        result = tracker.to_dict(reasoning_effort="low")
        self.assertEqual(result["prompt_tokens"], 1000)
        self.assertEqual(result["cached_prompt_tokens"], 200)
        self.assertEqual(result["uncached_prompt_tokens"], 800)
        self.assertEqual(result["completion_tokens"], 500)
        self.assertEqual(result["reasoning_tokens"], 100)
        self.assertEqual(result["total_tokens"], 1500)
        self.assertAlmostEqual(result["estimated_cost_usd"], 0.000764, places=9)

    def test_usage_merge_accumulates_manual_regeneration(self):
        first = {
            "model": "gpt-5.6-luna", "reasoning_effort": "low", "request_count": 1,
            "prompt_tokens": 100, "cached_prompt_tokens": 10, "uncached_prompt_tokens": 90,
            "completion_tokens": 50, "reasoning_tokens": 5, "total_tokens": 150,
            "estimated_cost_usd": 0.001, "pricing": {"snapshot": "test"}, "calls": [{"id": 1}],
        }
        second = {
            "model": "gpt-5.6-luna", "reasoning_effort": "low", "request_count": 2,
            "prompt_tokens": 200, "cached_prompt_tokens": 20, "uncached_prompt_tokens": 180,
            "completion_tokens": 80, "reasoning_tokens": 8, "total_tokens": 280,
            "estimated_cost_usd": 0.002, "pricing": {"snapshot": "test"}, "calls": [{"id": 2}, {"id": 3}],
        }
        merged = merge_usage(first, second)
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(merged["request_count"], 3)
        self.assertEqual(merged["prompt_tokens"], 300)
        self.assertEqual(merged["completion_tokens"], 130)
        self.assertEqual(merged["reasoning_tokens"], 13)
        self.assertEqual(merged["total_tokens"], 430)
        self.assertAlmostEqual(merged["estimated_cost_usd"], 0.003, places=9)
        self.assertEqual(len(merged["calls"]), 3)

    def test_openai_provider_sends_low_reasoning_and_captures_usage(self):
        captured: dict[str, Any] = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"signs": {}}'))],
                    usage=types.SimpleNamespace(
                        prompt_tokens=120, completion_tokens=80, total_tokens=200,
                        prompt_tokens_details=types.SimpleNamespace(cached_tokens=20),
                        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=30),
                    ),
                )

        class FakeOpenAI:
            def __init__(self):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI
        with patch.dict(sys.modules, {"openai": fake_module}), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            provider = OpenAIProvider("gpt-5.6-luna", reasoning_effort="low")
            result = provider.generate(system_prompt="system", user_prompt="user")

        self.assertEqual(result, {})
        self.assertEqual(captured["reasoning_effort"], "low")
        usage = provider.usage.to_dict(reasoning_effort="low")
        self.assertEqual(usage["request_count"], 1)
        self.assertEqual(usage["reasoning_tokens"], 30)
        self.assertEqual(usage["total_tokens"], 200)

    def test_openai_provider_generate_field_uses_targeted_schema(self):
        captured: dict[str, Any] = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"text":"Новый самостоятельный текст отношений с другой ситуацией и практическим выводом для проверки."}'))],
                    usage=types.SimpleNamespace(
                        prompt_tokens=50, completion_tokens=20, total_tokens=70,
                        prompt_tokens_details=types.SimpleNamespace(cached_tokens=0),
                        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=5),
                    ),
                )

        class FakeOpenAI:
            def __init__(self):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI
        with patch.dict(sys.modules, {"openai": fake_module}), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            provider = OpenAIProvider("gpt-5.6-luna", reasoning_effort="low")
            result = provider.generate_field(sign="pisces", field="love", system_prompt="system", user_prompt="user")

        self.assertTrue(result.startswith("Новый самостоятельный"))
        schema = captured["response_format"]["json_schema"]["schema"]
        self.assertEqual(list(schema["properties"]), ["text"])
        self.assertEqual(schema["properties"]["text"]["minLength"], 50)
        self.assertEqual(captured["reasoning_effort"], "low")

    def test_monthly_usage_aggregates_saved_datasets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [
                ("2026-09-01", "ru", 1, 100, 50, 150, 0.001),
                ("2026-09-02", "en", 2, 200, 80, 280, 0.002),
                ("2026-08-31", "ru", 9, 900, 900, 1800, 0.900),
            ]
            for iso, lang, requests, prompt, completion, total, cost in rows:
                path = root / iso / lang / "content.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    "generation": {"api_usage": {
                        "request_count": requests, "prompt_tokens": prompt, "cached_prompt_tokens": 0,
                        "completion_tokens": completion, "reasoning_tokens": 0, "total_tokens": total,
                        "estimated_cost_usd": cost,
                    }}
                }), encoding="utf-8")
            with patch.object(workflow_module, "GENERATED", root):
                total = workflow_module.monthly_api_usage("2026-09-23")
            self.assertEqual(total["datasets"], 2)
            self.assertEqual(total["request_count"], 3)
            self.assertEqual(total["prompt_tokens"], 300)
            self.assertEqual(total["completion_tokens"], 130)
            self.assertEqual(total["total_tokens"], 430)
            self.assertAlmostEqual(total["estimated_cost_usd"], 0.003, places=9)


if __name__ == "__main__":
    unittest.main()
