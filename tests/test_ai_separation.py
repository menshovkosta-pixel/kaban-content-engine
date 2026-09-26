from __future__ import annotations

import inspect
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class AISepTests(unittest.TestCase):
    def test_kaban_structured_json_client_is_project_agnostic(self):
        from kaban.ai.openai import StructuredJsonClient

        source = inspect.getsource(sys.modules[StructuredJsonClient.__module__]).casefold()
        self.assertNotIn("caelus", source)
        self.assertNotIn("zodiac", source)
        self.assertNotIn("sign_order", source)

    def test_kaban_client_uses_caller_supplied_schema(self):
        from kaban.ai.openai import StructuredJsonClient

        captured: dict[str, object] = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"answer":"ok"}'))],
                    usage=types.SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=4,
                        total_tokens=14,
                        prompt_tokens_details=types.SimpleNamespace(cached_tokens=2),
                        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=1),
                    ),
                )

        class FakeOpenAI:
            def __init__(self):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        with patch.dict(sys.modules, {"openai": fake_module}), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            client = StructuredJsonClient("gpt-5.6-luna", reasoning_effort="low")
            result = client.request(
                system_prompt="system",
                user_prompt="user",
                schema=schema,
                schema_name="generic_answer",
            )

        self.assertEqual(result, {"answer": "ok"})
        response_format = captured["response_format"]
        assert isinstance(response_format, dict)
        self.assertEqual(response_format["json_schema"]["schema"], schema)
        self.assertEqual(response_format["json_schema"]["name"], "generic_answer")
        self.assertEqual(client.usage.request_count, 1)

    def test_legacy_content_engine_package_is_removed(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "content_engine").exists())

    def test_production_entrypoints_use_project_generator_and_provider(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / "projects" / "caelus" / "workflow.py").read_text(encoding="utf-8")
        cli = (root / "generate_forecasts.py").read_text(encoding="utf-8")
        combined = workflow + "\n" + cli
        self.assertNotIn("from content_engine.generator import", combined)
        self.assertNotIn("from content_engine.provider import", combined)
        self.assertNotIn("from content_engine.usage import", combined)
        self.assertIn("from projects.caelus.generator import", combined)
        self.assertIn("from projects.caelus.provider import", combined)
        self.assertIn("from kaban.ai.usage import", combined)


if __name__ == "__main__":
    unittest.main()
