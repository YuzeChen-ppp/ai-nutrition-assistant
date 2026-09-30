"""A small local nutrition reference table.

The AI should not have to *invent* every number, and the app must stay useful
without an internet connection. This module holds typical values per 100 g for
common foods (USDA-style reference values) and is used to:

* cross-check the numbers the AI returned ("AI said 400 kcal for 100 g of rice,
  which is three times the reference value") and
* offer reference values when the user edits a food item.

It is deliberately a plain Python table so it can later be replaced by a
professional nutrition database without touching the rest of the app.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.i18n import tr

__all__ = [
    "FoodReference",
    "LOCAL_FOODS",
    "compare_with_reference",
    "estimate",
    "lookup",
    "normalise_food_name",
    "per_100g_values",
]

_FIELDS = (
    "calories_kcal",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sugar_g",
    "fibre_g",
    "sodium_mg",
)


@dataclass(frozen=True, slots=True)
class FoodReference:
    """Reference nutrition values for 100 g of a food."""

    name: str
    per_100g: dict[str, float]
    typical_serving_g: float = 100.0
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def for_weight(self, weight_g: float) -> dict[str, float]:
        factor = float(weight_g) / 100.0
        return {key: round(value * factor, 2) for key, value in self.per_100g.items()}


def _food(
    name: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    sugar: float,
    fibre: float,
    sodium: float,
    serving: float = 100.0,
    aliases: Iterable[str] = (),
) -> FoodReference:
    return FoodReference(
        name=name,
        per_100g={
            "calories_kcal": calories,
            "protein_g": protein,
            "carbs_g": carbs,
            "fat_g": fat,
            "sugar_g": sugar,
            "fibre_g": fibre,
            "sodium_mg": sodium,
        },
        typical_serving_g=serving,
        aliases=tuple(aliases),
    )


# Values are typical reference values per 100 g of the food as eaten.
LOCAL_FOODS: tuple[FoodReference, ...] = (
    _food("Egg, whole, cooked", 143, 12.6, 0.7, 9.5, 0.4, 0, 142, 50, ("egg", "eggs", "鸡蛋", "boiled egg", "fried egg")),
    _food("Egg white", 52, 10.9, 0.7, 0.2, 0.7, 0, 166, 33, ("egg white", "蛋清")),
    _food("Chicken breast, cooked", 165, 31, 0, 3.6, 0, 0, 74, 150, ("chicken breast", "chicken", "鸡胸肉", "鸡肉")),
    _food("Chicken thigh, cooked", 209, 26, 0, 10.9, 0, 0, 88, 120, ("chicken thigh", "chicken leg", "鸡腿")),
    _food("Chicken, roasted with skin", 239, 27, 0, 13.6, 0, 0, 82, 140, ("roast chicken", "roasted chicken", "烤鸡")),
    _food("Beef, lean, cooked", 217, 26, 0, 11.8, 0, 0, 66, 120, ("beef", "steak", "牛肉", "牛排")),
    _food("Beef, ground, cooked", 250, 26, 0, 15, 0, 0, 75, 120, ("minced beef", "ground beef", "牛肉末")),
    _food("Pork, lean, cooked", 242, 27, 0, 14, 0, 0, 62, 120, ("pork", "猪肉", "瘦肉")),
    _food("Pork belly", 518, 9.3, 0, 53, 0, 0, 32, 100, ("pork belly", "五花肉")),
    _food("Lamb, cooked", 258, 25, 0, 17, 0, 0, 72, 120, ("lamb", "羊肉")),
    _food("Salmon, cooked", 208, 20, 0, 13, 0, 0, 59, 120, ("salmon", "三文鱼", "鲑鱼")),
    _food("Tuna, canned in water", 116, 25.5, 0, 0.8, 0, 0, 247, 100, ("tuna", "金枪鱼")),
    _food("Shrimp, cooked", 99, 24, 0.2, 0.3, 0, 0, 111, 100, ("shrimp", "prawn", "虾", "虾仁")),
    _food("Cod, cooked", 105, 22.8, 0, 0.9, 0, 0, 78, 120, ("cod", "white fish", "鳕鱼", "鱼")),
    _food("Tofu, firm", 144, 17.3, 2.8, 8.7, 0.6, 2.3, 14, 100, ("tofu", "豆腐")),
    _food("Tempeh", 195, 19.9, 7.6, 11.4, 0, 1.4, 9, 100, ("tempeh", "天贝")),
    _food("Edamame", 122, 10.9, 9.9, 5.2, 2.2, 5.2, 6, 100, ("edamame", "毛豆")),
    _food("White rice, cooked", 130, 2.7, 28.2, 0.3, 0.1, 0.4, 1, 200, ("rice", "white rice", "米饭", "白米饭", "steamed rice")),
    _food("Brown rice, cooked", 123, 2.7, 25.6, 1, 0.2, 1.6, 4, 200, ("brown rice", "糙米", "杂粮饭")),
    _food("Fried rice", 163, 4.7, 24, 5.4, 0.6, 0.9, 500, 300, ("fried rice", "炒饭", "蛋炒饭")),
    _food("Noodles, wheat, cooked", 138, 4.5, 25, 2.1, 0.4, 1.2, 5, 250, ("noodles", "面条", "面", "ramen")),
    _food("Pasta, cooked", 158, 5.8, 30.9, 0.9, 0.6, 1.8, 1, 220, ("pasta", "spaghetti", "意面", "意大利面")),
    _food("Congee (rice porridge)", 46, 0.9, 10, 0.2, 0, 0.2, 3, 300, ("congee", "rice porridge", "粥", "白粥")),
    _food("Bread, white", 265, 9, 49, 3.2, 5, 2.7, 490, 30, ("white bread", "bread", "白面包", "面包")),
    _food("Bread, whole wheat", 247, 13, 41, 3.4, 6, 7, 400, 35, ("whole wheat bread", "wholemeal bread", "全麦面包", "全麦吐司")),
    _food("Oats, dry", 389, 16.9, 66.3, 6.9, 0, 10.6, 2, 40, ("oats", "oatmeal", "燕麦", "燕麦片")),
    _food("Potato, boiled", 87, 1.9, 20.1, 0.1, 0.9, 1.8, 4, 150, ("potato", "土豆", "马铃薯")),
    _food("Sweet potato, baked", 86, 1.6, 20.1, 0.1, 4.2, 3, 55, 150, ("sweet potato", "红薯", "地瓜")),
    _food("French fries", 312, 3.4, 41, 15, 0.3, 3.8, 210, 120, ("fries", "chips", "薯条")),
    _food("Broccoli, cooked", 35, 2.4, 7.2, 0.4, 1.4, 3.3, 41, 100, ("broccoli", "西兰花", "花椰菜")),
    _food("Spinach, cooked", 23, 2.9, 3.6, 0.4, 0.4, 2.2, 79, 100, ("spinach", "菠菜")),
    _food("Lettuce", 15, 1.4, 2.9, 0.2, 0.8, 1.3, 28, 80, ("lettuce", "生菜", "沙拉菜")),
    _food("Tomato", 18, 0.9, 3.9, 0.2, 2.6, 1.2, 5, 120, ("tomato", "番茄", "西红柿")),
    _food("Cucumber", 15, 0.7, 3.6, 0.1, 1.7, 0.5, 2, 120, ("cucumber", "黄瓜")),
    _food("Carrot, cooked", 41, 0.9, 9.6, 0.2, 4.7, 2.8, 69, 100, ("carrot", "胡萝卜")),
    _food("Onion", 40, 1.1, 9.3, 0.1, 4.2, 1.7, 4, 80, ("onion", "洋葱")),
    _food("Bell pepper", 26, 1, 6, 0.3, 3.5, 2.1, 3, 100, ("bell pepper", "pepper", "彩椒", "青椒", "甜椒")),
    _food("Mushroom", 22, 3.1, 3.3, 0.3, 2, 1, 5, 100, ("mushroom", "蘑菇", "香菇")),
    _food("Cabbage", 25, 1.3, 5.8, 0.1, 3.2, 2.5, 18, 100, ("cabbage", "卷心菜", "白菜", "包菜")),
    _food("Cauliflower", 25, 1.9, 5, 0.3, 1.9, 2, 30, 100, ("cauliflower", "花菜", "菜花")),
    _food("Green beans", 31, 1.8, 7, 0.1, 3.3, 2.7, 6, 100, ("green beans", "四季豆", "豆角")),
    _food("Peas", 81, 5.4, 14.5, 0.4, 5.7, 5.7, 5, 100, ("peas", "豌豆", "青豆")),
    _food("Sweet corn", 86, 3.3, 19, 1.4, 3.2, 2.7, 15, 120, ("corn", "玉米", "甜玉米")),
    _food("Avocado", 160, 2, 8.5, 14.7, 0.7, 6.7, 7, 100, ("avocado", "牛油果", "鳄梨")),
    _food("Banana", 89, 1.1, 22.8, 0.3, 12.2, 2.6, 1, 118, ("banana", "香蕉")),
    _food("Apple", 52, 0.3, 13.8, 0.2, 10.4, 2.4, 1, 182, ("apple", "苹果")),
    _food("Orange", 47, 0.9, 11.8, 0.1, 9.4, 2.4, 0, 131, ("orange", "橙子", "橘子")),
    _food("Strawberry", 32, 0.7, 7.7, 0.3, 4.9, 2, 1, 100, ("strawberry", "草莓")),
    _food("Blueberry", 57, 0.7, 14.5, 0.3, 10, 2.4, 1, 100, ("blueberry", "蓝莓")),
    _food("Grapes", 69, 0.7, 18.1, 0.2, 15.5, 0.9, 2, 100, ("grapes", "葡萄")),
    _food("Watermelon", 30, 0.6, 7.6, 0.2, 6.2, 0.4, 1, 200, ("watermelon", "西瓜")),
    _food("Milk, whole", 61, 3.2, 4.8, 3.3, 5.1, 0, 43, 250, ("whole milk", "全脂牛奶", "牛奶")),
    _food("Milk, semi-skimmed", 46, 3.4, 4.8, 1.6, 5.1, 0, 44, 250, ("semi skimmed milk", "semi-skimmed milk", "half skimmed milk", "半脱脂牛奶", "低脂牛奶")),
    _food("Milk, skimmed", 34, 3.4, 5, 0.1, 5.1, 0, 42, 250, ("skimmed milk", "skim milk", "脱脂牛奶")),
    _food("Soy milk, unsweetened", 33, 2.9, 1.5, 1.6, 0.6, 0.3, 26, 250, ("soy milk", "豆浆", "豆奶")),
    _food("Yogurt, plain", 61, 3.5, 4.7, 3.3, 4.7, 0, 46, 150, ("yogurt", "yoghurt", "酸奶")),
    _food("Greek yogurt, plain", 97, 9, 3.9, 5, 4, 0, 35, 150, ("greek yogurt", "希腊酸奶")),
    _food("Cheddar cheese", 403, 24.9, 1.3, 33.1, 0.5, 0, 621, 30, ("cheese", "cheddar", "奶酪", "芝士")),
    _food("Butter", 717, 0.9, 0.1, 81.1, 0.1, 0, 11, 10, ("butter", "黄油")),
    _food("Olive oil", 884, 0, 0, 100, 0, 0, 2, 10, ("olive oil", "橄榄油")),
    _food("Vegetable oil", 884, 0, 0, 100, 0, 0, 0, 10, ("vegetable oil", "cooking oil", "oil", "食用油", "植物油")),
    _food("Peanut butter", 588, 25, 20, 50, 9, 6, 429, 20, ("peanut butter", "花生酱")),
    _food("Almonds", 579, 21.2, 21.6, 49.9, 4.4, 12.5, 1, 30, ("almonds", "almond", "杏仁")),
    _food("Walnuts", 654, 15.2, 13.7, 65.2, 2.6, 6.7, 2, 30, ("walnuts", "walnut", "核桃")),
    _food("Cashews", 553, 18.2, 30.2, 43.9, 5.9, 3.3, 12, 30, ("cashews", "cashew", "腰果")),
    _food("Dark chocolate (70%)", 598, 7.8, 45.9, 42.6, 24, 10.9, 20, 30, ("dark chocolate", "黑巧克力", "巧克力")),
    _food("Cola", 42, 0, 10.6, 0, 10.6, 0, 4, 330, ("cola", "coke", "soda", "可乐", "汽水")),
    _food("Orange juice", 45, 0.7, 10.4, 0.2, 8.4, 0.2, 1, 250, ("orange juice", "橙汁")),
    _food("Beer", 43, 0.5, 3.6, 0, 0, 0, 4, 330, ("beer", "啤酒")),
    _food("Latte", 42, 2.1, 3.7, 2.1, 3.7, 0, 26, 300, ("latte", "cafe latte", "拿铁")),
    _food("Coffee, black", 1, 0.1, 0, 0, 0, 0, 2, 250, ("coffee", "black coffee", "americano", "咖啡", "黑咖啡")),
    _food("Pizza, cheese", 266, 11, 33, 10, 3.6, 2.3, 598, 150, ("pizza", "披萨")),
    _food("Hamburger", 295, 17, 24, 14, 5, 1.6, 500, 200, ("hamburger", "burger", "汉堡")),
    _food("Dumplings, boiled", 220, 8, 30, 7, 1, 1.5, 400, 200, ("dumplings", "饺子", "水饺")),
    _food("Steamed bun (mantou)", 223, 7.5, 47, 1, 5, 1.6, 165, 100, ("mantou", "steamed bun", "馒头", "包子")),
    _food("Whey protein powder", 370, 75, 10, 4, 5, 1, 400, 30, ("whey", "protein powder", "蛋白粉", "乳清蛋白")),
    _food("Mayonnaise", 680, 1, 0.6, 75, 0.6, 0, 635, 15, ("mayonnaise", "mayo", "蛋黄酱", "沙拉酱")),
    _food("Ketchup", 101, 1.1, 25, 0.1, 22, 0.3, 907, 15, ("ketchup", "tomato sauce", "番茄酱")),
    _food("Instant noodles, cooked", 138, 3.4, 20, 5, 1, 1, 700, 300, ("instant noodles", "ramen", "泡面", "方便面")),
    _food("Sushi roll", 145, 6, 20, 4.5, 4, 1.2, 350, 200, ("sushi", "sushi roll", "寿司")),
)


def normalise_food_name(name: Any) -> str:
    """Lower-case, strip punctuation and spacing for matching."""
    text = str(name or "").lower()
    text = re.sub(r"[\s_\-/]+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff ]+", "", text)
    return text.strip()


_ALIAS_INDEX: dict[str, FoodReference] = {}
for _reference in LOCAL_FOODS:
    for _key in (
        normalise_food_name(_reference.name),
        *(normalise_food_name(alias) for alias in _reference.aliases),
    ):
        if _key:
            _ALIAS_INDEX.setdefault(_key, _reference)


def lookup(name: Any) -> FoodReference | None:
    """Find a local reference entry for a food name (best effort)."""
    key = normalise_food_name(name)
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    for alias, reference in _ALIAS_INDEX.items():
        if len(alias) >= 3 and (alias in key or key in alias):
            return reference
    matches = difflib.get_close_matches(key, list(_ALIAS_INDEX), n=1, cutoff=0.85)
    if matches:
        return _ALIAS_INDEX[matches[0]]
    return None


def estimate(name: Any, weight_g: float) -> dict[str, float] | None:
    """Reference values for ``weight_g`` of ``name`` (``None`` if unknown)."""
    reference = lookup(name)
    if reference is None or not weight_g:
        return None
    return reference.for_weight(float(weight_g))


def per_100g_values(item: Any) -> dict[str, float] | None:
    """The item's own values scaled to 100 g (``None`` when the weight is unknown).

    Used by the online-reference comparison to put both sides in the same unit.
    Nothing is invented: without a weight there is no per-100 g value.
    """
    def read(field: str) -> Any:
        return item.get(field) if isinstance(item, dict) else getattr(item, field, None)

    try:
        weight = float(read("estimated_weight_g") or 0.0)
    except (TypeError, ValueError):
        return None
    if weight <= 0:
        return None
    factor = 100.0 / weight
    values: dict[str, float] = {}
    for field in _FIELDS:
        try:
            value = float(read(field) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        values[field] = round(value * factor, 2)
    return values


def compare_with_reference(item: Any, *, tolerance: float = 0.6) -> list[str]:
    """Compare an AI-produced item with the local table.

    Returns human-readable notes for the confirmation dialog. It never changes
    the AI values - the user stays in control.
    """
    def read(field: str) -> float:
        raw = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
        try:
            return float(raw or 0.0)
        except (TypeError, ValueError):
            return 0.0

    name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
    weight = read("estimated_weight_g")
    reference = lookup(name)
    if reference is None or weight <= 0:
        return []

    expected = reference.for_weight(weight)
    notes: list[str] = []
    for field, label_key in (
        ("calories_kcal", "nutrient.calories"),
        ("protein_g", "nutrient.protein"),
    ):
        value = read(field)
        target = expected[field]
        if target <= 0 or value <= 0:
            continue
        if abs(value - target) / target > tolerance:
            unit = (
                tr("nutrient.unit_kcal") if field == "calories_kcal" else tr("nutrient.unit_gram")
            )
            notes.append(
                tr(
                    "foodref.compare",
                    field=tr(label_key),
                    value=f"{value:.0f}",
                    target=f"{target:.0f}",
                    unit=unit,
                    weight=f"{weight:.0f}",
                    name=reference.name,
                )
            )
    return notes
