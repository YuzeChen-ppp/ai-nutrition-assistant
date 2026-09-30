"""The local nutrition reference table."""

from __future__ import annotations

import unittest

from app.nutrition.food_data import (
    LOCAL_FOODS,
    compare_with_reference,
    estimate,
    lookup,
    normalise_food_name,
)


class LookupTests(unittest.TestCase):
    def test_english_name(self) -> None:
        reference = lookup("chicken breast")
        self.assertIsNotNone(reference)
        assert reference is not None
        self.assertAlmostEqual(reference.per_100g["protein_g"], 31.0)

    def test_chinese_alias(self) -> None:
        self.assertEqual(lookup("鸡蛋").name, lookup("egg").name)
        self.assertEqual(lookup("米饭").name, lookup("white rice").name)

    def test_unknown_food_returns_none(self) -> None:
        self.assertIsNone(lookup("zzzz-unobtainium"))

    def test_normalisation(self) -> None:
        self.assertEqual(normalise_food_name("Whole-Wheat Bread!"), "whole wheat bread")

    def test_table_has_no_duplicate_keys(self) -> None:
        self.assertGreater(len(LOCAL_FOODS), 50)
        for food in LOCAL_FOODS:
            self.assertGreater(food.per_100g["calories_kcal"], 0)


class EstimateTests(unittest.TestCase):
    def test_estimate_scales_to_weight(self) -> None:
        values = estimate("white rice", 200)
        assert values is not None
        self.assertAlmostEqual(values["calories_kcal"], 260.0, places=1)

    def test_estimate_without_weight_returns_none(self) -> None:
        self.assertIsNone(estimate("rice", 0))

    def test_estimate_unknown_food_returns_none(self) -> None:
        self.assertIsNone(estimate("unobtainium", 100))


class CompareTests(unittest.TestCase):
    def test_close_values_produce_no_notes(self) -> None:
        item = {"name": "white rice", "estimated_weight_g": 200, "calories_kcal": 260,
                "protein_g": 5.4}
        self.assertEqual(compare_with_reference(item), [])

    def test_wildly_wrong_values_are_flagged(self) -> None:
        item = {"name": "white rice", "estimated_weight_g": 200, "calories_kcal": 900,
                "protein_g": 5.4}
        notes = compare_with_reference(item)
        self.assertTrue(any("热量" in note for note in notes))

    def test_unknown_food_is_not_flagged(self) -> None:
        item = {"name": "grandma's special stew", "estimated_weight_g": 300,
                "calories_kcal": 500}
        self.assertEqual(compare_with_reference(item), [])


if __name__ == "__main__":
    unittest.main()
