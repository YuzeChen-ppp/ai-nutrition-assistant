"""Model capabilities and reasoning-effort mapping.

Pure logic: no network, no Qt, no environment. The DeepSeek API facts here
come from https://api-docs.deepseek.com/guides/thinking_mode
"""

from __future__ import annotations

import unittest

from app.ai.model_catalog import (
    DEFAULT_REASONING_EFFORT,
    EFFORT_TO_API,
    KNOWN_MODELS,
    REASONING_EFFORTS,
    capability_for,
    normalise_effort,
)


class CapabilityTests(unittest.TestCase):
    def test_flash_model_supports_thinking_and_vision(self) -> None:
        capability = capability_for("deepseek-flash")
        self.assertTrue(capability.supports_thinking)
        self.assertTrue(capability.supports_vision)

    def test_pro_model_supports_thinking_without_vision(self) -> None:
        capability = capability_for("deepseek-v4-pro")
        self.assertTrue(capability.supports_thinking)
        self.assertFalse(capability.supports_vision)

    def test_unknown_model_is_treated_as_unsupported(self) -> None:
        self.assertFalse(capability_for("gpt-4o").supports_thinking)

    def test_unknown_deepseek_model_keeps_thinking_support(self) -> None:
        # A future DeepSeek model must not silently lose the feature.
        self.assertTrue(capability_for("deepseek-v9-experimental").supports_thinking)

    def test_model_name_is_matched_case_insensitively(self) -> None:
        self.assertTrue(capability_for("  DeepSeek-Flash ").supports_thinking)
        self.assertTrue(capability_for("  DeepSeek-Flash ").supports_vision)

    def test_empty_model_name_is_unsupported(self) -> None:
        self.assertFalse(capability_for("").supports_thinking)

    def test_picker_offers_the_known_models(self) -> None:
        self.assertIn("deepseek-flash", KNOWN_MODELS)
        self.assertIn("deepseek-v4-pro", KNOWN_MODELS)


class EffortMappingTests(unittest.TestCase):
    def test_levels_map_to_low_medium_max(self) -> None:
        self.assertEqual(EFFORT_TO_API["low"], "low")
        self.assertEqual(EFFORT_TO_API["medium"], "medium")
        self.assertEqual(EFFORT_TO_API["high"], "max")

    def test_every_level_maps_to_a_distinct_api_value(self) -> None:
        # The whole point of the picker: three levels, three different efforts.
        self.assertEqual(set(EFFORT_TO_API), set(REASONING_EFFORTS))
        self.assertEqual(len(set(EFFORT_TO_API.values())), len(EFFORT_TO_API))

    def test_known_level_is_kept(self) -> None:
        self.assertEqual(normalise_effort("high"), "high")

    def test_level_is_normalised_from_loose_input(self) -> None:
        self.assertEqual(normalise_effort("  HIGH "), "high")

    def test_unknown_level_falls_back_to_the_default(self) -> None:
        self.assertEqual(normalise_effort("ultra"), DEFAULT_REASONING_EFFORT)

    def test_missing_level_falls_back_to_the_default(self) -> None:
        self.assertEqual(normalise_effort(None), DEFAULT_REASONING_EFFORT)
        self.assertEqual(normalise_effort(""), DEFAULT_REASONING_EFFORT)


if __name__ == "__main__":
    unittest.main()
