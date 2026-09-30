"""Nutrition arithmetic: totals, remaining calories, progress, warnings."""

from __future__ import annotations

import unittest

from app.ai.schemas import FoodItem
from app.nutrition.calculator import (
    DailyTotals,
    describe_remaining_calories,
    progress_fraction,
    rescale_values,
    sanity_warnings,
    sum_food_items,
)
from app.nutrition.targets import calculate_targets


def item(name: str, kcal: float, protein: float, carbs: float, fat: float,
         sugar: float = 0.0, fibre: float = 0.0, sodium: float = 0.0) -> FoodItem:
    return FoodItem(
        name=name,
        calories_kcal=kcal,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        sugar_g=sugar,
        fibre_g=fibre,
        sodium_mg=sodium,
    )


class SumTests(unittest.TestCase):
    def test_three_foods_sum_to_1500_kcal(self) -> None:
        foods = [item("Food A", 500, 30, 50, 20), item("Food B", 700, 40, 70, 25),
                 item("Food C", 300, 10, 30, 15)]
        totals = sum_food_items(foods)
        self.assertAlmostEqual(totals.calories_kcal, 1500.0)
        self.assertEqual(totals.item_count, 3)

    def test_macros_are_summed(self) -> None:
        foods = [
            item("A", 100, 10, 5, 2, sugar=3, fibre=1, sodium=100),
            item("B", 200, 20, 15, 4, sugar=7, fibre=2, sodium=300),
        ]
        totals = sum_food_items(foods)
        self.assertAlmostEqual(totals.protein_g, 30.0)
        self.assertAlmostEqual(totals.carbs_g, 20.0)
        self.assertAlmostEqual(totals.fat_g, 6.0)
        self.assertAlmostEqual(totals.sugar_g, 10.0)
        self.assertAlmostEqual(totals.fibre_g, 3.0)
        self.assertAlmostEqual(totals.sodium_mg, 400.0)

    def test_empty_day_is_zero(self) -> None:
        totals = sum_food_items([])
        self.assertEqual(totals, DailyTotals())
        self.assertEqual(totals.item_count, 0)

    def test_rows_from_database_style_dicts_work(self) -> None:
        totals = sum_food_items([{"name": "rice", "calories_kcal": 130, "protein_g": 2.7}])
        self.assertAlmostEqual(totals.calories_kcal, 130.0)
        self.assertAlmostEqual(totals.protein_g, 2.7)


class RemainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = calculate_targets(
            age=25, sex="male", height_cm=175, weight_kg=70,
            activity_level="moderate", goal="maintain",
        )

    def test_remaining_calories(self) -> None:
        consumed = sum_food_items([item("lunch", 1450, 85, 160, 45)])
        remaining = self.targets.as_totals() - consumed
        self.assertAlmostEqual(remaining.calories_kcal, round(self.targets.calorie_target - 1450, 6), places=4)

    def test_remaining_is_negative_when_over_target(self) -> None:
        consumed = sum_food_items([item("big day", self.targets.calorie_target + 150, 100, 200, 60)])
        remaining = self.targets.as_totals() - consumed
        self.assertLess(remaining.calories_kcal, 0)
        message = describe_remaining_calories(remaining.calories_kcal)
        self.assertIn("超出今日目标", message)
        self.assertNotIn("!", message)

    def test_progress_fraction_is_clamped(self) -> None:
        self.assertAlmostEqual(progress_fraction(1100, 2200), 0.5)
        self.assertEqual(progress_fraction(3000, 2200), 1.0)
        self.assertEqual(progress_fraction(0, 0), 0.0)
        self.assertEqual(progress_fraction(-50, 2200), 0.0)


class HelperTests(unittest.TestCase):
    def test_rescale_halves_every_value(self) -> None:
        original = item("chicken", 330, 62, 0, 7.2)
        scaled = rescale_values(original, original_weight_g=200, new_weight_g=100)
        self.assertAlmostEqual(scaled["calories_kcal"], 165.0)
        self.assertAlmostEqual(scaled["protein_g"], 31.0)
        self.assertAlmostEqual(scaled["fat_g"], 3.6)

    def test_rescale_without_weights_keeps_values(self) -> None:
        original = item("chicken", 330, 62, 0, 7.2)
        scaled = rescale_values(original, original_weight_g=None, new_weight_g=100)
        self.assertAlmostEqual(scaled["calories_kcal"], 330.0)

    def test_impossible_energy_density_is_flagged(self) -> None:
        # A dict is used here on purpose: the Pydantic model already rejects
        # this value, but the warning helper must also cope with raw data.
        bad = {"name": "mystery", "estimated_weight_g": 100, "calories_kcal": 9000}
        self.assertTrue(sanity_warnings([bad]))

    def test_low_confidence_is_flagged(self) -> None:
        unsure = FoodItem(name="beef fried rice", calories_kcal=620, confidence=0.3)
        warnings = sanity_warnings([unsure])
        self.assertTrue(any("置信度较低" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
