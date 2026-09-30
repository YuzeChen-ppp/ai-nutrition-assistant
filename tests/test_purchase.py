"""购买条件：文本解析、默认清单、难度映射、违规检测、prompt 内容。"""

from __future__ import annotations

import unittest

from app.ai.prompts import (
    NEXT_MEAL_RECOMMENDATION_PROMPT,
    PURCHASABLE_INGREDIENT_MEAL_PROMPT,
)
from app.ai.parser import parse_recommendation
from app.database.models import PurchasePreferences
from app.nutrition.purchase import (
    DEFAULT_EASY_INGREDIENTS,
    budget_label,
    country_label,
    difficulty_label,
    find_inconvenient_matches,
    split_ingredient_text,
)


class SplitIngredientTests(unittest.TestCase):
    def test_plain_list(self) -> None:
        self.assertEqual(
            split_ingredient_text("鸡胸肉、鸡蛋、牛奶、西兰花、米饭、土豆、面包、香蕉"),
            ["鸡胸肉", "鸡蛋", "牛奶", "西兰花", "米饭", "土豆", "面包", "香蕉"],
        )

    def test_natural_language_sentence(self) -> None:
        text = "我附近超市可以买到鸡胸肉、鸡蛋、牛奶、西兰花、胡萝卜、土豆、米饭和面包。"
        self.assertEqual(
            split_ingredient_text(text),
            ["鸡胸肉", "鸡蛋", "牛奶", "西兰花", "胡萝卜", "土豆", "米饭", "面包"],
        )

    def test_english_list_keeps_multiword_items(self) -> None:
        self.assertEqual(
            split_ingredient_text("chicken breast, eggs; greek yogurt"),
            ["chicken breast", "eggs", "greek yogurt"],
        )

    def test_chinese_items_separated_by_space(self) -> None:
        self.assertEqual(split_ingredient_text("藜麦 羽衣甘蓝"), ["藜麦", "羽衣甘蓝"])

    def test_conjunctions_are_separators(self) -> None:
        self.assertEqual(split_ingredient_text("西兰花和胡萝卜"), ["西兰花", "胡萝卜"])
        self.assertEqual(split_ingredient_text("鸡蛋以及牛奶"), ["鸡蛋", "牛奶"])

    def test_duplicates_and_blank_entries_are_removed(self) -> None:
        self.assertEqual(split_ingredient_text("鸡蛋、鸡蛋、牛奶、 、"), ["鸡蛋", "牛奶"])

    def test_full_width_punctuation_and_trailing_etc(self) -> None:
        self.assertEqual(
            split_ingredient_text("鸡蛋，牛奶；面包（全麦）等"),
            ["鸡蛋", "牛奶", "面包", "全麦"],
        )

    def test_empty_input(self) -> None:
        self.assertEqual(split_ingredient_text(""), [])
        self.assertEqual(split_ingredient_text(None), [])
        self.assertEqual(split_ingredient_text("   "), [])

    def test_long_leftover_sentence_is_dropped(self) -> None:
        items = split_ingredient_text("鸡蛋\n这是一段没有分开的很长很长的说明文字不应该当成食材")
        self.assertEqual(items, ["鸡蛋"])


class InconvenientMatchTests(unittest.TestCase):
    def test_exact_and_contained_matches(self) -> None:
        matches = find_inconvenient_matches(
            ["牛油果", "牛油果沙拉", "鸡胸肉"], ["牛油果"]
        )
        self.assertEqual([name for name, _ in matches], ["牛油果", "牛油果沙拉"])

    def test_no_match_when_list_is_empty(self) -> None:
        self.assertEqual(find_inconvenient_matches(["牛油果"], []), [])

    def test_similar_name_is_flagged(self) -> None:
        matches = find_inconvenient_matches(["三文鱼刺身"], ["三文鱼"])
        self.assertEqual(len(matches), 1)

    def test_unrelated_food_is_not_flagged(self) -> None:
        self.assertEqual(find_inconvenient_matches(["鸡胸肉", "西兰花"], ["牛油果", "藜麦"]), [])


class DefaultsTests(unittest.TestCase):
    def test_default_list_is_common_supermarket_food(self) -> None:
        joined = "、".join(DEFAULT_EASY_INGREDIENTS)
        for expected in ("鸡蛋", "鸡胸肉", "牛奶", "米饭", "土豆", "面包", "西兰花", "胡萝卜"):
            self.assertIn(expected, joined)

    def test_default_list_has_no_specialty_food(self) -> None:
        joined = "、".join(DEFAULT_EASY_INGREDIENTS).lower()
        for unusual in ("藜麦", "牛油果", "羽衣甘蓝", "quinoa", "avocado", "kale"):
            self.assertNotIn(unusual, joined)

    def test_labels(self) -> None:
        self.assertEqual(country_label("United Kingdom"), "英国")
        self.assertEqual(country_label(""), "其他 / 不指定")
        self.assertEqual(budget_label("low"), "低预算")
        self.assertEqual(budget_label("unlimited"), "不限制")

    def test_difficulty_labels(self) -> None:
        self.assertEqual(difficulty_label("easy"), "容易")
        self.assertEqual(difficulty_label("moderate"), "一般")
        self.assertEqual(difficulty_label("hard"), "不容易")
        self.assertEqual(difficulty_label(""), "")


class PurchasePreferencesTests(unittest.TestCase):
    def test_empty_preferences_use_the_default_list(self) -> None:
        prefs = PurchasePreferences()
        self.assertEqual(prefs.easy_ingredients(), [])
        self.assertTrue(prefs.uses_default_ingredients())
        self.assertEqual(prefs.effective_easy_ingredients(), list(DEFAULT_EASY_INGREDIENTS))
        self.assertFalse(prefs.is_configured())

    def test_user_list_replaces_the_default(self) -> None:
        prefs = PurchasePreferences(easy_text="鸡胸肉、鸡蛋、土豆")
        self.assertEqual(prefs.easy_ingredients(), ["鸡胸肉", "鸡蛋", "土豆"])
        self.assertFalse(prefs.uses_default_ingredients())
        self.assertEqual(prefs.effective_easy_ingredients(), ["鸡胸肉", "鸡蛋", "土豆"])
        self.assertTrue(prefs.is_configured())

    def test_hard_list(self) -> None:
        prefs = PurchasePreferences(hard_text="牛油果、藜麦；羽衣甘蓝")
        self.assertEqual(prefs.hard_ingredients(), ["牛油果", "藜麦", "羽衣甘蓝"])
        self.assertTrue(prefs.is_configured())

    def test_cooking_time_label(self) -> None:
        self.assertEqual(PurchasePreferences().cooking_time_label(), "不限")
        self.assertEqual(PurchasePreferences(max_cooking_minutes=15).cooking_time_label(), "15 分钟")

    def test_summary_mentions_the_key_facts(self) -> None:
        prefs = PurchasePreferences(
            country="United Kingdom",
            stores=["Tesco", "Lidl"],
            easy_text="鸡胸肉、鸡蛋",
            hard_text="牛油果",
            budget="low",
        )
        summary = prefs.summary()
        self.assertIn("英国", summary)
        self.assertIn("低预算", summary)
        self.assertIn("Tesco", summary)
        self.assertIn("方便购买 2 项", summary)
        self.assertIn("不买 1 项", summary)


class RecommendationSchemaTests(unittest.TestCase):
    def test_shopping_list_and_difficulty_are_parsed(self) -> None:
        raw = (
            '{"title":"鸡胸肉土豆西兰花",'
            '"foods":[{"name":"鸡胸肉","estimated_weight_g":150,"calories_kcal":248,'
            '"protein_g":46.5,"carbs_g":0,"fat_g":5.4}],'
            '"shopping_list":[{"name":"鸡胸肉","quantity":"500","difficulty":"容易"},'
            '"西兰花"],'
            '"difficulty":"Easy","reason":"蛋白质不足。","tips":"少油。"}'
        )
        recommendation = parse_recommendation(raw)
        self.assertEqual(recommendation.difficulty, "easy")
        self.assertEqual(len(recommendation.shopping_list), 2)
        self.assertEqual(recommendation.shopping_list[0].name, "鸡胸肉")
        self.assertEqual(recommendation.shopping_list[0].quantity, "500")
        self.assertEqual(recommendation.shopping_list[0].difficulty, "easy")
        # 纯字符串形式的清单项也能解析
        self.assertEqual(recommendation.shopping_list[1].name, "西兰花")
        self.assertEqual(recommendation.shopping_names(), ["鸡胸肉", "西兰花"])

    def test_missing_new_fields_still_parse(self) -> None:
        raw = '{"title":"鸡蛋炒饭","foods":[{"name":"鸡蛋","calories_kcal":143}]}'
        recommendation = parse_recommendation(raw)
        self.assertEqual(recommendation.difficulty, "")
        self.assertEqual(recommendation.shopping_list, [])


class PromptTests(unittest.TestCase):
    def test_prompt_contains_the_priority_order(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        self.assertIn("easy_to_buy_ingredients", prompt)
        self.assertIn("inconvenient_ingredients", prompt)
        self.assertIn("PRIORITY ORDER", prompt)
        self.assertIn("supermarket staples", prompt)
        # JSON 模式要求 prompt 里出现 "json"
        self.assertIn("json", prompt)

    def test_prompt_forbids_specialty_and_unavailable_food(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        self.assertIn("NEVER use anything listed in", prompt)
        self.assertIn("quinoa", prompt)
        self.assertIn("avocado", prompt)
        self.assertIn("superfood", prompt)

    def test_prompt_respects_budget_equipment_and_time(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        for field in ("budget", "cooking_equipment", "max_cooking_minutes", "diet_preferences"):
            self.assertIn(field, prompt)

    def test_prompt_still_cares_about_nutrition_and_goal(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        self.assertIn("remaining calories", prompt)
        self.assertIn("protein", prompt)
        self.assertIn("fibre", prompt)
        self.assertIn("medical advice", prompt)

    def test_prompt_asks_for_chinese_output_and_shopping_list(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        self.assertIn("Simplified Chinese", prompt)
        self.assertIn("shopping_list", prompt)
        self.assertIn("difficulty", prompt)

    def test_prompt_does_not_claim_live_stock_or_prices(self) -> None:
        prompt = PURCHASABLE_INGREDIENT_MEAL_PROMPT
        self.assertIn("shop stock", prompt)
        self.assertIn("Never claim", prompt)

    def test_old_prompt_name_points_to_the_new_prompt(self) -> None:
        self.assertIs(NEXT_MEAL_RECOMMENDATION_PROMPT, PURCHASABLE_INGREDIENT_MEAL_PROMPT)


if __name__ == "__main__":
    unittest.main()
