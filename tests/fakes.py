"""Test doubles: a fake DeepSeek client that never touches the network."""

from __future__ import annotations

from typing import Any

from app.ai.parser import (
    parse_food_analysis,
    parse_online_nutrition,
    parse_recommendation,
)
from app.ai.schemas import (
    FoodAnalysisResult,
    MealRecommendation,
    OnlineNutritionReference,
)
from app.utils.config import Settings
from app.utils.images import PreparedImage

__all__ = [
    "FakeDeepSeekClient",
    "IMAGE_ANALYSIS_PAYLOAD",
    "RECOMMENDATION_PAYLOAD",
    "RECOMMENDATION_WITH_AVOCADO",
    "REFERENCE_PAYLOAD",
    "REFERENCE_PAYLOAD_MATCHING_LOCAL",
    "TEXT_ANALYSIS_PAYLOAD",
]

# The AI answers below are realistic DeepSeek-shaped JSON documents.

TEXT_ANALYSIS_PAYLOAD = """
{"foods":[
  {"name":"egg","amount":2,"unit":"pieces","estimated_weight_g":100,
   "calories_kcal":143,"protein_g":12.6,"carbs_g":0.7,"fat_g":9.5,"sugar_g":0.4,
   "fibre_g":0,"sodium_mg":142,"cooking_method":"boiled","confidence":0.92},
  {"name":"whole wheat toast","amount":1,"unit":"slice","estimated_weight_g":35,
   "calories_kcal":86,"protein_g":4.6,"carbs_g":14.4,"fat_g":1.2,"sugar_g":2.1,
   "fibre_g":2.5,"sodium_mg":140,"cooking_method":"toasted","confidence":0.9},
  {"name":"semi-skimmed milk","amount":1,"unit":"glass","estimated_weight_g":250,
   "calories_kcal":115,"protein_g":8.5,"carbs_g":12,"fat_g":4,"sugar_g":12.8,
   "fibre_g":0,"sodium_mg":110,"cooking_method":"none","confidence":0.88}
],"note":""}
"""

IMAGE_ANALYSIS_PAYLOAD = """
{"foods":[
  {"name":"beef fried rice","estimated_weight_g":350,"cooking_method":"fried",
   "calories_kcal":620,"protein_g":24,"carbs_g":75,"fat_g":24,"sugar_g":3,
   "fibre_g":3,"sodium_mg":900,"confidence":0.65}
],"note":"Oil and sauce amounts are estimates."}
"""

RECOMMENDATION_PAYLOAD = """
{"title":"鸡胸肉土豆西兰花",
 "foods":[
   {"name":"鸡胸肉","amount":150,"unit":"g","estimated_weight_g":150,
    "calories_kcal":248,"protein_g":46.5,"carbs_g":0,"fat_g":5.4,"sugar_g":0,
    "fibre_g":0,"sodium_mg":111,"cooking_method":"香煎","confidence":0.8},
   {"name":"土豆","estimated_weight_g":200,"calories_kcal":174,
    "protein_g":3.8,"carbs_g":40.2,"fat_g":0.2,"sugar_g":1.8,"fibre_g":3.6,
    "sodium_mg":8,"cooking_method":"水煮","confidence":0.85},
   {"name":"西兰花","estimated_weight_g":150,"calories_kcal":53,
    "protein_g":3.6,"carbs_g":10.8,"fat_g":0.6,"sugar_g":2.1,"fibre_g":5,
    "sodium_mg":62,"cooking_method":"水煮","confidence":0.85}
 ],
 "shopping_list":[
   {"name":"鸡胸肉","quantity":"约 500 g","difficulty":"easy"},
   {"name":"西兰花","quantity":"1 个","difficulty":"easy"}
 ],
 "difficulty":"easy",
 "reason":"你今天蛋白质摄入偏低，这份餐食主要使用你标记为方便购买的食材，热量也在剩余范围内。",
 "tips":"煎鸡胸肉时少放油，用不粘锅即可。"}
"""

# 故意违反购买条件的推荐（用来测试「不方便购买的食材」这道保险）
RECOMMENDATION_WITH_AVOCADO = """{"title":"牛油果三文鱼藜麦沙拉",
 "foods":[
   {"name":"牛油果","estimated_weight_g":100,"calories_kcal":160,"protein_g":2,
    "carbs_g":8.5,"fat_g":14.7,"sugar_g":0.7,"fibre_g":6.7,"sodium_mg":7,
    "confidence":0.6},
   {"name":"三文鱼","estimated_weight_g":120,"calories_kcal":250,"protein_g":24,
    "carbs_g":0,"fat_g":16,"sugar_g":0,"fibre_g":0,"sodium_mg":71,"confidence":0.6}
 ],
 "shopping_list":[{"name":"藜麦","quantity":"约 100 g","difficulty":"hard"}],
 "difficulty":"hard",
 "reason":"看起来营养不错，但食材不容易买到。",
 "tips":""}
"""

# 在线参考（第二参考）的假响应。故意留了几个 null：
# sugar_g 缺失（显示「未知」）、fibre_g 是 0（显示 0，不是「未知」）。
# 热量每 100 g 160 kcal，比本地蛋类参考值（143）高约 12%，属于「不触发提示」的量级。
REFERENCE_PAYLOAD = """
{"name":"egg","basis":"per_100g","calories_kcal":160,"protein_g":13.0,
 "carbs_g":1.1,"fat_g":11.0,"sugar_g":null,"fibre_g":0,"sodium_mg":124,
 "confidence":0.8,"note":""}
"""

# 与本地参考表几乎一致（蛋 143 kcal / 12.6 g 蛋白质）：用来验证「差异小于阈值不提示」。
REFERENCE_PAYLOAD_MATCHING_LOCAL = """
{"name":"egg","basis":"per_100g","calories_kcal":143,"protein_g":12.6,
 "carbs_g":0.7,"fat_g":9.5,"sugar_g":0.4,"fibre_g":0,"sodium_mg":142,
 "confidence":0.9,"note":""}
"""


class FakeDeepSeekClient:
    """Stands in for :class:`app.ai.deepseek_client.DeepSeekClient`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings(api_key="test-key")
        self.text_calls: list[str] = []
        self.image_calls: list[str] = []
        self.recommendation_calls: list[dict[str, Any]] = []
        self.reference_calls: list[str] = []
        self.recommendation_payload = RECOMMENDATION_PAYLOAD
        self.reference_payload = REFERENCE_PAYLOAD
        self.fail_with: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def analyze_text(self, description: str) -> FoodAnalysisResult:
        self._maybe_fail()
        self.text_calls.append(description)
        return parse_food_analysis(TEXT_ANALYSIS_PAYLOAD)

    def analyze_image(self, prepared: PreparedImage, *, note: str = "") -> FoodAnalysisResult:
        self._maybe_fail()
        self.image_calls.append(note)
        return parse_food_analysis(IMAGE_ANALYSIS_PAYLOAD)

    def recommend_next_meal(self, context: dict[str, Any]) -> MealRecommendation:
        self._maybe_fail()
        self.recommendation_calls.append(context)
        return parse_recommendation(self.recommendation_payload)

    def fetch_nutrition_reference(
        self,
        food_name: str,
        *,
        language: str | None = None,
    ) -> OnlineNutritionReference:
        """The second reference. Counts every call, so a test can prove that
        only pressing the button reaches it."""
        self.reference_calls.append(food_name)
        self._maybe_fail()
        return parse_online_nutrition(self.reference_payload, name=food_name)

    def check_connection(self) -> str:
        self._maybe_fail()
        return "Connected to DeepSeek (fake). Available models: deepseek-flash"

    @property
    def last_usage(self) -> dict[str, int]:
        return {}
