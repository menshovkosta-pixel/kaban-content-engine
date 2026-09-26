from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import yaml

from kaban.projects import ProjectConfigError, ProjectRegistry, load_project_config


ROOT = Path(__file__).resolve().parent.parent


def valid_project_payload(**overrides):
    payload = {
        "id": "sample",
        "name": "Sample",
        "default_language": "ru",
        "supported_languages": ["ru", "en"],
        "timezone": "Pacific/Auckland",
        "ai": {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "low",
        },
        "uniqueness": {
            "history_days": 90,
            "warning_threshold": 0.76,
            "hard_threshold": 0.80,
            "max_regeneration_attempts": 3,
        },
        "publication": {
            "telegram": {
                "album_group_size": 6,
            }
        },
    }
    payload.update(overrides)
    return payload


def write_project(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class ProjectConfigTests(unittest.TestCase):
    def test_real_caelus_project_loads_with_v4_11_defaults(self):
        config = load_project_config(ROOT / "projects" / "caelus" / "project.yaml")
        self.assertEqual(config.id, "caelus")
        self.assertEqual(config.name, "CAELUS")
        self.assertEqual(config.default_language, "ru")
        self.assertEqual(config.supported_languages, ("ru", "en"))
        self.assertEqual(config.timezone, "Pacific/Auckland")
        self.assertEqual(config.ai.model, "gpt-5.6-luna")
        self.assertEqual(config.ai.reasoning_effort, "low")
        self.assertEqual(config.uniqueness.history_days, 90)
        self.assertEqual(config.uniqueness.warning_threshold, 0.76)
        self.assertEqual(config.uniqueness.hard_threshold, 0.80)
        self.assertEqual(config.uniqueness.max_regeneration_attempts, 3)
        self.assertEqual(config.publication.telegram.album_group_size, 6)

    def test_default_language_must_be_supported(self):
        payload = valid_project_payload(default_language="de")
        with tempfile.TemporaryDirectory() as td:
            path = write_project(Path(td) / "project.yaml", payload)
            with self.assertRaisesRegex(ProjectConfigError, "default_language"):
                load_project_config(path)

    def test_warning_threshold_must_not_exceed_hard_threshold(self):
        payload = valid_project_payload()
        payload["uniqueness"]["warning_threshold"] = 0.90
        payload["uniqueness"]["hard_threshold"] = 0.80
        with tempfile.TemporaryDirectory() as td:
            path = write_project(Path(td) / "project.yaml", payload)
            with self.assertRaisesRegex(ProjectConfigError, "warning_threshold"):
                load_project_config(path)

    def test_malformed_yaml_reports_source_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "project.yaml"
            path.write_text("id: [broken", encoding="utf-8")
            with self.assertRaises(ProjectConfigError) as raised:
                load_project_config(path)
            self.assertIn(str(path), str(raised.exception))

    def test_malformed_yaml_error_does_not_echo_source_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "project.yaml"
            path.write_text("id: [super-secret-token", encoding="utf-8")
            with self.assertRaises(ProjectConfigError) as raised:
                load_project_config(path)
            message = str(raised.exception)
            self.assertIn(str(path), message)
            self.assertNotIn("super-secret-token", message)


class ProjectRegistryTests(unittest.TestCase):
    def test_default_active_project_is_caelus_when_env_is_absent(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTENT_PROJECT_ID", None)
            self.assertEqual(registry.active().id, "caelus")

    def test_explicit_caelus_project_resolves(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {"CONTENT_PROJECT_ID": "caelus"}, clear=False):
            self.assertEqual(registry.active().name, "CAELUS")

    def test_explicit_unknown_project_fails_without_fallback(self):
        registry = ProjectRegistry(ROOT / "projects")
        with patch.dict(os.environ, {"CONTENT_PROJECT_ID": "does-not-exist"}, clear=False):
            with self.assertRaisesRegex(ProjectConfigError, "does-not-exist"):
                registry.active()

    def test_duplicate_project_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_project(root / "one" / "project.yaml", valid_project_payload(id="duplicate", name="One"))
            write_project(root / "two" / "project.yaml", valid_project_payload(id="duplicate", name="Two"))
            with self.assertRaisesRegex(ProjectConfigError, "duplicate"):
                ProjectRegistry(root)

    def test_missing_projects_directory_fails_fast(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing"
            with self.assertRaisesRegex(ProjectConfigError, "missing"):
                ProjectRegistry(missing)

    def test_registered_projects_are_deterministic_by_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_project(root / "z" / "project.yaml", valid_project_payload(id="zeta", name="Zeta"))
            write_project(root / "a" / "project.yaml", valid_project_payload(id="alpha", name="Alpha"))
            registry = ProjectRegistry(root)
            self.assertEqual(tuple(item.id for item in registry.registered()), ("alpha", "zeta"))


from projects.caelus.config import (
    openai_model,
    openai_reasoning_effort,
    uniqueness_hard_threshold,
    uniqueness_history_days,
    uniqueness_max_regeneration_attempts,
    uniqueness_warning_threshold,
)


class LegacyConfigCompatibilityTests(unittest.TestCase):
    LEGACY_ENV_KEYS = (
        "CONTENT_PROJECT_ID",
        "CAELUS_OPENAI_MODEL",
        "CAELUS_OPENAI_REASONING_EFFORT",
        "CONTENT_HISTORY_DAYS",
        "CONTENT_UNIQUENESS_WARNING_THRESHOLD",
        "CONTENT_UNIQUENESS_HARD_THRESHOLD",
        "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS",
    )

    def _clear_legacy_env(self):
        for key in self.LEGACY_ENV_KEYS:
            os.environ.pop(key, None)

    def test_legacy_config_defaults_come_from_caelus_project(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_legacy_env()
            self.assertEqual(openai_model(), "gpt-5.6-luna")
            self.assertEqual(openai_reasoning_effort(), "low")
            self.assertEqual(uniqueness_history_days(), 90)
            self.assertEqual(uniqueness_warning_threshold(), 0.76)
            self.assertEqual(uniqueness_hard_threshold(), 0.80)
            self.assertEqual(uniqueness_max_regeneration_attempts(), 3)

    def test_project_defaults_are_delegated_not_hardcoded(self):
        fake_project = SimpleNamespace(
            ai=SimpleNamespace(model="project-model", reasoning_effort="medium"),
            uniqueness=SimpleNamespace(
                history_days=45,
                warning_threshold=0.61,
                hard_threshold=0.72,
                max_regeneration_attempts=7,
            ),
        )
        with patch.dict(os.environ, {}, clear=False), patch(
            "projects.caelus.config.active_project", return_value=fake_project
        ):
            self._clear_legacy_env()
            self.assertEqual(openai_model(), "project-model")
            self.assertEqual(openai_reasoning_effort(), "medium")
            self.assertEqual(uniqueness_history_days(), 45)
            self.assertEqual(uniqueness_warning_threshold(), 0.61)
            self.assertEqual(uniqueness_hard_threshold(), 0.72)
            self.assertEqual(uniqueness_max_regeneration_attempts(), 7)

    def test_existing_environment_variables_still_override_project_defaults(self):
        overrides = {
            "CONTENT_PROJECT_ID": "caelus",
            "CAELUS_OPENAI_MODEL": "override-model",
            "CAELUS_OPENAI_REASONING_EFFORT": "medium",
            "CONTENT_HISTORY_DAYS": "180",
            "CONTENT_UNIQUENESS_WARNING_THRESHOLD": "0.71",
            "CONTENT_UNIQUENESS_HARD_THRESHOLD": "0.83",
            "CONTENT_UNIQUENESS_MAX_REGEN_ATTEMPTS": "5",
        }
        with patch.dict(os.environ, overrides, clear=False):
            self.assertEqual(openai_model(), "override-model")
            self.assertEqual(openai_reasoning_effort(), "medium")
            self.assertEqual(uniqueness_history_days(), 180)
            self.assertEqual(uniqueness_warning_threshold(), 0.71)
            self.assertEqual(uniqueness_hard_threshold(), 0.83)
            self.assertEqual(uniqueness_max_regeneration_attempts(), 5)

    def test_invalid_reasoning_effort_keeps_legacy_validation(self):
        with patch.dict(os.environ, {"CAELUS_OPENAI_REASONING_EFFORT": "invalid"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CAELUS_OPENAI_REASONING_EFFORT"):
                openai_reasoning_effort()

    def test_invalid_legacy_numeric_overrides_keep_legacy_validation(self):
        with patch.dict(os.environ, {"CONTENT_HISTORY_DAYS": "not-int"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CONTENT_HISTORY_DAYS"):
                uniqueness_history_days()
        with patch.dict(os.environ, {"CONTENT_UNIQUENESS_HARD_THRESHOLD": "1.5"}, clear=False):
            with self.assertRaisesRegex(ValueError, "CONTENT_UNIQUENESS_HARD_THRESHOLD"):
                uniqueness_hard_threshold()
