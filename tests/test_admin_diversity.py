from __future__ import annotations

import unittest

from projects.caelus.uniqueness.settings import DiversitySettings
from admin_app import diversity_controls_html, regeneration_feedback_html, validation_html


class AdminDiversityUiTests(unittest.TestCase):
    def test_controls_expose_presets_history_custom_and_safe_preview(self):
        html = diversity_controls_html(
            DiversitySettings(profile="strict", history_days=90, custom_threshold=0.80),
            conflict_count=3,
            locked=False,
            regenerate_disabled=False,
        )
        for value in ("soft", "balanced", "strict", "very_strict", "custom"):
            self.assertIn(f'value="{value}"', html)
        self.assertIn('name="history_days"', html)
        self.assertIn('name="custom_threshold"', html)
        self.assertIn('form="reviewForm"', html)
        self.assertIn('formaction="/diversity-settings"', html)
        self.assertIn('Проверить без API', html)
        self.assertIn('formaction="/regenerate-conflicts"', html)
        self.assertIn('Regenerate conflicts · 3', html)

    def test_regenerate_conflicts_button_can_be_disabled(self):
        html = diversity_controls_html(
            DiversitySettings(profile="strict"),
            conflict_count=0,
            locked=False,
            regenerate_disabled=True,
        )
        self.assertIn('Regenerate conflicts · 0', html)
        self.assertIn('disabled', html)


class AdminDiversityPhase11UiTests(unittest.TestCase):
    def test_card_target_length_warning_is_hidden_from_review_console(self):
        html = validation_html([
            {
                "severity": "warning",
                "code": "card_target_length",
                "message": "Карточка содержит 109 символов; рекомендуемый диапазон 120–180.",
                "sign": "aries",
                "field": "card",
            },
            {
                "severity": "warning",
                "code": "history_similarity_warning",
                "message": "Похожий текст: 2026-09-01, сходство 78%.",
                "sign": "aries",
                "field": "general",
            },
        ])
        self.assertNotIn("109 символов", html)
        self.assertNotIn("card_target_length", html)
        self.assertIn("Quality warnings · 1", html)
        self.assertIn("Похожий текст", html)

    def test_controls_offer_threshold_presets_up_to_95_percent(self):
        html = diversity_controls_html(
            DiversitySettings(profile="custom", history_days=90, custom_threshold=0.95),
            conflict_count=1,
            locked=False,
            regenerate_disabled=False,
        )
        for percent in (70, 80, 90, 95):
            self.assertIn(f'name="threshold_preset" value="{percent}"', html)
        self.assertIn('max="95"', html)
        for days in (30, 60, 90, 180, 365):
            self.assertIn(f'value="{days}"', html)

    def test_uniqueness_inspector_shows_current_and_matched_text_and_targeted_regenerate(self):
        html = diversity_controls_html(
            DiversitySettings(profile="strict", history_days=90),
            conflict_count=1,
            locked=False,
            regenerate_disabled=False,
            conflicts=[{
                "severity": "error",
                "code": "history_repetition",
                "sign": "pisces",
                "field": "love",
                "similarity": 0.87,
                "matched_date": "2026-08-17",
                "matched_sign": "pisces",
                "current_text": "Текущий текст прогноза.",
                "matched_text": "Старый конфликтующий текст.",
            }],
            day="2026-09-25",
            language="ru",
        )
        self.assertIn("87%", html)
        self.assertIn("2026-08-17", html)
        self.assertIn("Текущий текст прогноза.", html)
        self.assertIn("Старый конфликтующий текст.", html)
        self.assertIn('formaction="/regenerate-field"', html)
        self.assertIn('value="pisces__love"', html)

    def test_regeneration_feedback_shows_before_after_and_attempts(self):
        html = regeneration_feedback_html({
            "generation": {
                "uniqueness": {
                    "last_regeneration": {
                        "sign": "aries",
                        "field": "general",
                        "attempts": 2,
                        "before_similarity": 0.91,
                        "after_similarity": 0.42,
                        "resolved": True,
                    }
                }
            }
        })
        self.assertIn("Aries", html)
        self.assertIn("general", html)
        self.assertIn("2", html)
        self.assertIn("91%", html)
        self.assertIn("42%", html)
        self.assertIn("устранён", html)


if __name__ == "__main__":
    unittest.main()
