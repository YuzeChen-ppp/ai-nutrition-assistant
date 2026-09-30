"""Pydantic validation of AI output + shared coercion helpers."""

from __future__ import annotations

import unittest

from pydantic import ValidationError as PydanticValidationError

from app.ai.schemas import FoodAnalysisResult, FoodItem, MealRecommendation
from app.utils.validation import (
    ValidationError,
    coerce_confidence,
    coerce_number,
    looks_like_api_key,
    normalise_meal_type,
)


class ApiKeyShapeTests(unittest.TestCase):
    """粗粒度形状检查：只用来提醒用户，真正的判断交给「测试连接」。"""

    def test_typical_deepseek_key_is_accepted(self) -> None:
        self.assertTrue(looks_like_api_key("sk-1234567890abcdef"))

    def test_empty_key_is_rejected(self) -> None:
        self.assertFalse(looks_like_api_key(""))
        self.assertFalse(looks_like_api_key("   "))
        self.assertFalse(looks_like_api_key(None))

    def test_key_without_the_sk_prefix_is_rejected(self) -> None:
        self.assertFalse(looks_like_api_key("1234567890abcdef"))

    def test_short_key_is_rejected(self) -> None:
        self.assertFalse(looks_like_api_key("sk-12"))

    def test_key_with_inner_whitespace_is_rejected(self) -> None:
        self.assertFalse(looks_like_api_key("sk-1234 5678"))


class CoercionTests(unittest.TestCase):
    def test_plain_numbers(self) -> None:
        self.assertEqual(coerce_number(140), 140.0)
        self.assertEqual(coerce_number("140"), 140.0)
        self.assertEqual(coerce_number("140 kcal"), 140.0)
        self.assertEqual(coerce_number("1,200 mg"), 1200.0)
        self.assertEqual(coerce_number("0.9"), 0.9)
        self.assertEqual(coerce_number("0,5"), 0.5)

    def test_thousands_separators_are_not_read_as_decimal_commas(self) -> None:
        # 回归测试：界面上把钠显示成 "4,200"（千位分隔符），读回来必须还是 4200，
        # 而不是欧洲小数逗号的 4.2。
        self.assertEqual(coerce_number("4,200"), 4200.0)
        self.assertEqual(coerce_number("12,345"), 12345.0)
        self.assertEqual(coerce_number("1,234,567"), 1234567.0)
        self.assertEqual(coerce_number("4,200 mg"), 4200.0)
        self.assertEqual(coerce_number("1,000"), 1000.0)
        # 表格里带小数的格式（1,800.0）也不能出错
        self.assertEqual(coerce_number("1,800.0"), 1800.0)

    def test_decimal_comma_still_works(self) -> None:
        self.assertEqual(coerce_number("0,5"), 0.5)
        self.assertEqual(coerce_number("12,50"), 12.5)
        self.assertEqual(coerce_number("1,2345"), 1.2345)
        self.assertEqual(coerce_number("1.234,56"), 1234.56)
        self.assertEqual(coerce_number("1,234.56"), 1234.56)
        # 单个点号按小数点理解（1.234 = 1.234，不是 1234）
        self.assertEqual(coerce_number("1.234"), 1.234)

    def test_missing_values(self) -> None:
        self.assertIsNone(coerce_number(None, allow_none=True))
        with self.assertRaises(ValidationError):
            coerce_number(None)

    def test_garbage_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            coerce_number("not a number")
        with self.assertRaises(ValidationError):
            coerce_number(True)

    def test_a_number_must_stand_on_its_own(self) -> None:
        """回归测试：不要从 "1e400" / "1_000" 里硬抠出一个 1。

        以前的实现会把这些读成 1.0 / 1.0 / 0.0 —— 一个静默缩小的数值比报错更危险，
        因为「每 100 g 1430 kJ」会变成「1 kcal」还照样显示出来。
        """
        # 科学计数法整体读入，再由调用方的范围检查拒绝
        self.assertEqual(coerce_number("1e3"), 1000.0)
        self.assertEqual(coerce_number("1.5e2"), 150.0)
        self.assertEqual(coerce_number(float("inf")), float("inf"))
        # 数字前后紧贴着字母/下划线时不是数字
        with self.assertRaises(ValidationError):
            coerce_number("1_000")
        with self.assertRaises(ValidationError):
            coerce_number("v1.5")
        with self.assertRaises(ValidationError):
            coerce_number("about a lot")

    def test_units_glued_to_the_number_still_work(self) -> None:
        self.assertEqual(coerce_number("1500kJ"), 1500.0)
        self.assertEqual(coerce_number("250g"), 250.0)
        self.assertEqual(coerce_number("140kcal"), 140.0)
        self.assertEqual(coerce_number("90%"), 90.0)

    def test_confidence_percentages(self) -> None:
        self.assertEqual(coerce_confidence("90%"), 0.9)
        self.assertEqual(coerce_confidence(90), 0.9)
        self.assertEqual(coerce_confidence(0.95), 0.95)
        with self.assertRaises(ValidationError):
            coerce_confidence(500)

    def test_meal_type_normalisation(self) -> None:
        self.assertEqual(normalise_meal_type("Breakfast"), "breakfast")
        self.assertEqual(normalise_meal_type("午饭"), "lunch")
        self.assertEqual(normalise_meal_type("unknown thing"), "snack")


class FoodItemTests(unittest.TestCase):
    payload = {
        "name": "egg",
        "amount": 2,
        "unit": "pieces",
        "estimated_weight_g": 100,
        "calories_kcal": 143,
        "protein_g": 12.6,
        "carbs_g": 0.7,
        "fat_g": 9.5,
        "sugar_g": 0.4,
        "fibre_g": 0,
        "sodium_mg": 142,
        "confidence": 0.9,
    }

    def test_reference_payload_is_valid(self) -> None:
        item = FoodItem.model_validate(self.payload)
        self.assertEqual(item.name, "egg")
        self.assertAlmostEqual(item.calories_kcal, 143.0)
        self.assertAlmostEqual(item.confidence or 0, 0.9)

    def test_alias_keys_are_accepted(self) -> None:
        item = FoodItem.model_validate(
            {
                "food": "rice",
                "weight_g": 200,
                "calories": 260,
                "protein": 5.4,
                "carbohydrates": 56,
                "fat": 0.6,
                "fiber": 0.8,
                "sodium": 2,
                "certainty": "80%",
            }
        )
        self.assertEqual(item.name, "rice")
        self.assertAlmostEqual(item.estimated_weight_g or 0, 200.0)
        self.assertAlmostEqual(item.fibre_g, 0.8)
        self.assertAlmostEqual(item.confidence or 0, 0.8)

    def test_numbers_as_text_are_accepted(self) -> None:
        item = FoodItem.model_validate(
            {"name": "toast", "calories_kcal": "80 kcal", "protein_g": "3 g", "weight": "35"}
        )
        self.assertAlmostEqual(item.calories_kcal, 80.0)
        self.assertAlmostEqual(item.protein_g, 3.0)
        self.assertAlmostEqual(item.estimated_weight_g or 0, 35.0)

    def test_negative_calories_are_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            FoodItem.model_validate({**self.payload, "calories_kcal": -500})

    def test_absurd_protein_is_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            FoodItem.model_validate({**self.payload, "protein_g": 999_999_999})

    def test_impossible_energy_density_is_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            FoodItem.model_validate(
                {"name": "bogus", "estimated_weight_g": 100, "calories_kcal": 5000}
            )

    def test_missing_name_is_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            FoodItem.model_validate({"calories_kcal": 100})

    def test_missing_optional_numbers_default_to_zero(self) -> None:
        item = FoodItem.model_validate({"name": "water", "calories_kcal": 0})
        self.assertEqual(item.protein_g, 0.0)
        self.assertEqual(item.sodium_mg, 0.0)

    def test_per_100g_scaling(self) -> None:
        item = FoodItem.model_validate(self.payload)
        per_100 = item.per_100g()
        self.assertAlmostEqual(per_100["calories_kcal"], 143.0, places=1)
        half = FoodItem.model_validate({**self.payload, "estimated_weight_g": 50})
        self.assertAlmostEqual(half.per_100g()["calories_kcal"], 286.0, places=1)

    def test_to_record_fields_matches_database_columns(self) -> None:
        fields = FoodItem.model_validate(self.payload).to_record_fields()
        for column in (
            "name", "amount", "unit", "estimated_weight_g", "calories_kcal",
            "protein_g", "carbs_g", "fat_g", "sugar_g", "fibre_g", "sodium_mg",
            "confidence", "cooking_method",
        ):
            self.assertIn(column, fields)


class ResultModelTests(unittest.TestCase):
    def test_food_analysis_accepts_items_alias(self) -> None:
        result = FoodAnalysisResult.model_validate(
            {"items": [{"name": "apple", "calories_kcal": 95}], "notes": "estimate"}
        )
        self.assertEqual(len(result.foods), 1)
        self.assertEqual(result.note, "estimate")

    def test_recommendation_model(self) -> None:
        recommendation = MealRecommendation.model_validate(
            {
                "meal": "Chicken rice bowl",
                "ingredients": [{"name": "chicken breast", "weight_g": 150, "calories": 248}],
                "why": "High protein.",
                "advice": "Add broccoli for fibre.",
            }
        )
        self.assertEqual(recommendation.title, "Chicken rice bowl")
        self.assertEqual(len(recommendation.foods), 1)
        self.assertEqual(recommendation.tips, "Add broccoli for fibre.")


if __name__ == "__main__":
    unittest.main()
