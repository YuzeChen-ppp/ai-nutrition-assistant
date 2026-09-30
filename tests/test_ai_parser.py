"""Parsing of AI answers (no network access is used in these tests)."""

from __future__ import annotations

import unittest

from app.ai.errors import AIResponseError, NoFoodRecognisedError
from app.ai.parser import extract_json, parse_food_analysis, parse_recommendation


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(extract_json('{"foods": []}'), {"foods": []})

    def test_markdown_fences(self) -> None:
        raw = '```json\n{"foods": [{"name": "rice"}]}\n```'
        self.assertEqual(extract_json(raw), {"foods": [{"name": "rice"}]})

    def test_json_with_surrounding_prose(self) -> None:
        raw = 'Sure! Here is the analysis:\n{"foods": []}\nHope that helps.'
        self.assertEqual(extract_json(raw), {"foods": []})

    def test_top_level_array(self) -> None:
        self.assertEqual(extract_json('[{"name": "egg"}]'), [{"name": "egg"}])

    def test_broken_json_raises(self) -> None:
        with self.assertRaises(AIResponseError):
            extract_json("I could not analyse this meal.")

    def test_empty_answer_raises(self) -> None:
        with self.assertRaises(AIResponseError):
            extract_json("")


class FoodAnalysisParseTests(unittest.TestCase):
    def test_standard_payload(self) -> None:
        raw = (
            '{"foods":[{"name":"egg","amount":2,"unit":"pieces","estimated_weight_g":100,'
            '"calories_kcal":143,"protein_g":12.6,"carbs_g":0.7,"fat_g":9.5,"sugar_g":0.4,'
            '"fibre_g":0,"sodium_mg":142,"confidence":0.95}],"note":""}'
        )
        result = parse_food_analysis(raw)
        self.assertEqual(len(result.foods), 1)
        self.assertEqual(result.foods[0].name, "egg")

    def test_bare_list_is_accepted(self) -> None:
        result = parse_food_analysis('[{"name":"rice","calories_kcal":260}]')
        self.assertEqual(result.foods[0].name, "rice")

    def test_single_item_object_is_accepted(self) -> None:
        result = parse_food_analysis('{"name":"toast","calories_kcal":80}')
        self.assertEqual(result.foods[0].name, "toast")

    def test_empty_food_list_raises_no_food_error(self) -> None:
        with self.assertRaises(NoFoodRecognisedError):
            parse_food_analysis('{"foods": [], "note": "The photo shows no food."}')

    def test_invalid_numbers_raise_response_error(self) -> None:
        with self.assertRaises(AIResponseError):
            parse_food_analysis('{"foods":[{"name":"egg","calories_kcal":-500}]}')

    def test_missing_food_list_raises(self) -> None:
        with self.assertRaises(AIResponseError):
            parse_food_analysis('{"answer": "yummy"}')

    def test_fenced_answer_is_parsed(self) -> None:
        raw = '```json\n{"foods":[{"name":"milk","weight_g":250,"calories":115}]}\n```'
        result = parse_food_analysis(raw)
        self.assertAlmostEqual(result.foods[0].calories_kcal, 115.0)


class RecommendationParseTests(unittest.TestCase):
    def test_recommendation(self) -> None:
        raw = (
            '{"title":"Grilled chicken rice bowl","foods":[{"name":"chicken breast",'
            '"estimated_weight_g":150,"calories_kcal":248,"protein_g":46.5,"carbs_g":0,'
            '"fat_g":5.4,"confidence":0.8}],"reason":"High protein, fits the target.",'
            '"tips":"Serve with vegetables."}'
        )
        recommendation = parse_recommendation(raw)
        self.assertEqual(recommendation.title, "Grilled chicken rice bowl")
        self.assertEqual(len(recommendation.foods), 1)
        self.assertIn("protein", recommendation.reason.lower())

    def test_recommendation_without_foods_raises(self) -> None:
        with self.assertRaises(AIResponseError):
            parse_recommendation('{"title":"nothing","foods":[]}')


if __name__ == "__main__":
    unittest.main()
