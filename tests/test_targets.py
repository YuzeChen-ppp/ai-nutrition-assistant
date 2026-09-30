"""BMR / TDEE / macro target maths."""

from __future__ import annotations

import unittest

from app.nutrition.targets import (
    activity_multiplier,
    calculate_targets,
    mifflin_st_jeor_bmr,
    normalise_activity,
    normalise_goal,
    normalise_sex,
    tdee_from_bmr,
)


class BmrTests(unittest.TestCase):
    def test_male_bmr_matches_hand_calculation(self) -> None:
        # 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5 = 1780
        self.assertAlmostEqual(
            mifflin_st_jeor_bmr(sex="male", weight_kg=80, height_cm=180, age=30),
            1780.0,
        )

    def test_female_bmr_matches_hand_calculation(self) -> None:
        # 10*60 + 6.25*165 - 5*30 - 161 = 600 + 1031.25 - 150 - 161 = 1320.25
        self.assertAlmostEqual(
            mifflin_st_jeor_bmr(sex="female", weight_kg=60, height_cm=165, age=30),
            1320.25,
        )

    def test_unspecified_sex_uses_midpoint(self) -> None:
        male = mifflin_st_jeor_bmr(sex="male", weight_kg=70, height_cm=170, age=40)
        female = mifflin_st_jeor_bmr(sex="female", weight_kg=70, height_cm=170, age=40)
        middle = mifflin_st_jeor_bmr(sex="unspecified", weight_kg=70, height_cm=170, age=40)
        self.assertAlmostEqual(middle, (male + female) / 2)


class TdeeTests(unittest.TestCase):
    def test_tdee_uses_activity_multiplier(self) -> None:
        self.assertAlmostEqual(tdee_from_bmr(1780, "sedentary"), 1780 * 1.2, places=6)
        self.assertAlmostEqual(tdee_from_bmr(1780, "moderate"), 1780 * 1.55, places=6)

    def test_activity_aliases(self) -> None:
        self.assertEqual(normalise_activity("Very Active"), "very_active")
        self.assertEqual(normalise_activity("轻度"), "light")
        self.assertEqual(activity_multiplier("sedentary"), 1.2)


class TargetTests(unittest.TestCase):
    def test_maintain_keeps_tdee(self) -> None:
        targets = calculate_targets(
            age=30, sex="male", height_cm=180, weight_kg=80,
            activity_level="moderate", goal="maintain",
        )
        self.assertAlmostEqual(targets.calorie_target, targets.tdee, places=1)

    def test_lose_is_lower_and_gain_is_higher(self) -> None:
        common = dict(age=30, sex="male", height_cm=180, weight_kg=80, activity_level="moderate")
        lose = calculate_targets(**common, goal="lose")
        maintain = calculate_targets(**common, goal="maintain")
        gain = calculate_targets(**common, goal="gain")
        self.assertLess(lose.calorie_target, maintain.calorie_target)
        self.assertGreater(gain.calorie_target, maintain.calorie_target)

    def test_lose_never_goes_below_bmr(self) -> None:
        targets = calculate_targets(
            age=20, sex="female", height_cm=150, weight_kg=45,
            activity_level="sedentary", goal="lose",
        )
        self.assertGreaterEqual(targets.calorie_target, targets.bmr)
        self.assertGreaterEqual(targets.calorie_target, 1200)

    def test_macros_stay_within_energy_budget(self) -> None:
        targets = calculate_targets(
            age=35, sex="female", height_cm=165, weight_kg=62,
            activity_level="light", goal="maintain",
        )
        macro_kcal = targets.protein_g * 4 + targets.carbs_g * 4 + targets.fat_g * 9
        self.assertLessEqual(macro_kcal, targets.calorie_target * 1.02)
        self.assertGreater(targets.protein_g, 0)
        self.assertGreater(targets.fibre_g, 0)

    def test_goal_aliases(self) -> None:
        self.assertEqual(normalise_goal("Lose Weight"), "lose")
        self.assertEqual(normalise_goal("增肌"), "gain")
        self.assertEqual(normalise_sex("女"), "female")


if __name__ == "__main__":
    unittest.main()
