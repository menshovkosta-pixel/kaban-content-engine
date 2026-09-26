from __future__ import annotations

import importlib
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CaelusProjectPromptIsolationTests(unittest.TestCase):
    def test_caelus_prompts_are_owned_by_caelus_project(self):
        prompts = importlib.import_module("projects.caelus.prompts")

        text = prompts.system_prompt("ru")
        self.assertIn("CAELUS", text)
        self.assertIn("астролог", text.lower())

    def test_content_engine_no_longer_contains_caelus_prompt_module(self):
        self.assertFalse((ROOT / "content_engine" / "prompts.py").exists())

    def test_project_prompts_keep_public_prompt_contract(self):
        prompts = importlib.import_module("projects.caelus.prompts")
        target = date(2026, 9, 24)

        self.assertTrue(prompts.system_prompt("en"))
        self.assertIn(target.isoformat(), prompts.user_prompt(target, "en", "history"))
        self.assertIn(
            "aries",
            prompts.regenerate_sign_prompt(
                target=target,
                language="en",
                sign="aries",
                current_day_context="taurus: context",
                conflicts="old text",
                attempt=1,
                current_sign_text="current text",
            ).lower(),
        )
        self.assertIn(
            "card",
            prompts.regenerate_field_prompt(
                target=target,
                language="en",
                sign="aries",
                field="card",
                current_text="current text",
                conflicts="old text",
                current_day_context="taurus: context",
                attempt=1,
            ).lower(),
        )


if __name__ == "__main__":
    unittest.main()
