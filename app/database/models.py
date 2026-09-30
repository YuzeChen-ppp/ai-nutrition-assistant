"""Data models for the database layer.

* ``UserProfile``  - Pydantic model for the profile the user types in
* ``FoodItemRecord`` / ``MealRecord`` / ``DailySummary`` - plain dataclasses
  mirroring the SQLite rows (cheap to create, easy to test)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.i18n import tr
from app.nutrition.calculator import DailyTotals
from app.nutrition.purchase import (
    DEFAULT_EASY_INGREDIENTS,
    budget_label,
    country_label,
    split_ingredient_text,
)
from app.nutrition.targets import (
    NutritionTargets,
    calculate_targets,
    normalise_activity,
    normalise_goal,
    normalise_sex,
)
from app.utils.validation import normalise_meal_type

__all__ = [
    "DailySummary",
    "FoodItemRecord",
    "MealRecord",
    "PurchasePreferences",
    "UserProfile",
]


class UserProfile(BaseModel):
    """Personal details used to estimate daily nutrition targets."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    age: int = Field(ge=10, le=120)
    sex: str = "unspecified"
    height_cm: float = Field(ge=100, le=250)
    weight_kg: float = Field(ge=25, le=300)
    activity_level: str = "sedentary"
    goal: str = "maintain"

    @field_validator("sex")
    @classmethod
    def _check_sex(cls, value: str) -> str:
        return normalise_sex(value)

    @field_validator("activity_level")
    @classmethod
    def _check_activity(cls, value: str) -> str:
        return normalise_activity(value)

    @field_validator("goal")
    @classmethod
    def _check_goal(cls, value: str) -> str:
        return normalise_goal(value)

    def calculate_targets(self) -> NutritionTargets:
        """BMR / TDEE / macro targets for this profile (Python only)."""
        return calculate_targets(
            age=self.age,
            sex=self.sex,
            height_cm=self.height_cm,
            weight_kg=self.weight_kg,
            activity_level=self.activity_level,
            goal=self.goal,
        )


@dataclass(slots=True)
class PurchasePreferences:
    """「我现在方便买到什么」——推荐下一餐时使用的购买条件。

    这里不是库存管理：程序记录的是**购买便利性**（哪里买、好不好买、预算、
    能用的厨具、愿意花的时间），而不是家里还剩多少食材。
    """

    country: str = ""
    stores: list[str] = field(default_factory=list)
    easy_text: str = ""
    hard_text: str = ""
    budget: str = "normal"
    equipment: list[str] = field(default_factory=list)
    max_cooking_minutes: int = 0
    diet_preferences: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------ 食材列表
    def easy_ingredients(self) -> list[str]:
        """用户填写「方便购买」的食材（未填写时为空）。"""
        return split_ingredient_text(self.easy_text)

    def hard_ingredients(self) -> list[str]:
        """用户填写「不方便购买 / 不想买」的食材。"""
        return split_ingredient_text(self.hard_text)

    def effective_easy_ingredients(self) -> list[str]:
        """发给 AI 的「容易买到」清单：用户自己的，否则用常见超市食材。"""
        user_items = self.easy_ingredients()
        if user_items:
            return user_items
        return list(DEFAULT_EASY_INGREDIENTS)

    def uses_default_ingredients(self) -> bool:
        return not self.easy_ingredients()

    # ------------------------------------------------------------ 显示文本
    def country_label(self) -> str:
        return country_label(self.country)

    def budget_label(self) -> str:
        return budget_label(self.budget)

    def cooking_time_label(self) -> str:
        if self.max_cooking_minutes and self.max_cooking_minutes > 0:
            return f"{self.max_cooking_minutes} 分钟"
        return tr("purchase.time_unlimited")

    def is_configured(self) -> bool:
        """用户是否设置过任何购买条件。"""
        return bool(
            self.country
            or self.stores
            or self.easy_text.strip()
            or self.hard_text.strip()
            or self.equipment
            or self.diet_preferences.strip()
            or (self.max_cooking_minutes or 0) > 0
            or self.budget not in {"", "normal"}
        )

    def summary(self) -> str:
        """设置页里显示的一行摘要。"""
        parts: list[str] = [self.country_label(), self.budget_label()]
        if self.stores:
            parts.append("、".join(self.stores))
        easy = self.easy_ingredients()
        if easy:
            parts.append(tr("purchase.summary_easy", count=len(easy)))
        else:
            parts.append(tr("purchase.summary_default"))
        hard = self.hard_ingredients()
        if hard:
            parts.append(tr("purchase.summary_hard", count=len(hard)))
        return " ・ ".join(part for part in parts if part)


@dataclass(frozen=True, slots=True)
class FoodItemRecord:
    """One row of ``food_items``."""

    id: int
    meal_id: int
    name: str
    amount: float | None = None
    unit: str | None = None
    estimated_weight_g: float | None = None
    calories_kcal: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    sugar_g: float = 0.0
    fibre_g: float = 0.0
    sodium_mg: float = 0.0
    confidence: float | None = None
    cooking_method: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "FoodItemRecord":
        data = dict(row)
        return cls(
            id=int(data["id"]),
            meal_id=int(data["meal_id"]),
            name=str(data["name"]),
            amount=_optional_float(data.get("amount")),
            unit=data.get("unit"),
            estimated_weight_g=_optional_float(data.get("estimated_weight_g")),
            calories_kcal=float(data.get("calories_kcal") or 0.0),
            protein_g=float(data.get("protein_g") or 0.0),
            carbs_g=float(data.get("carbs_g") or 0.0),
            fat_g=float(data.get("fat_g") or 0.0),
            sugar_g=float(data.get("sugar_g") or 0.0),
            fibre_g=float(data.get("fibre_g") or 0.0),
            sodium_mg=float(data.get("sodium_mg") or 0.0),
            confidence=_optional_float(data.get("confidence")),
            cooking_method=data.get("cooking_method"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def portion_label(self) -> str:
        """Human text such as ``180 g`` or ``2 pieces``."""
        if self.estimated_weight_g:
            return f"{self.estimated_weight_g:,.0f} g"
        if self.amount and self.unit:
            return f"{self.amount:g} {self.unit}"
        if self.amount:
            return f"{self.amount:g}"
        return tr("table.portion_unknown")


@dataclass(slots=True)
class MealRecord:
    """One row of ``meals`` plus its food items."""

    id: int
    date: str
    time: str
    meal_type: str
    source: str
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    items: list[FoodItemRecord] = field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        row: sqlite3.Row | dict[str, Any],
        items: list[FoodItemRecord] | None = None,
    ) -> "MealRecord":
        data = dict(row)
        return cls(
            id=int(data["id"]),
            date=str(data["date"]),
            time=str(data.get("time") or ""),
            meal_type=normalise_meal_type(data.get("meal_type")),
            source=str(data.get("source") or "manual"),
            note=str(data.get("note") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            items=list(items or []),
        )

    def totals(self) -> DailyTotals:
        from app.nutrition.calculator import sum_food_items

        return sum_food_items(self.items)


@dataclass(frozen=True, slots=True)
class DailySummary:
    """Aggregated totals for one calendar date."""

    date: str
    totals: DailyTotals
    meal_count: int = 0

    def as_row(self) -> tuple[str, str, str, str, str]:
        """Values for the history table: date, kcal, protein, carbs, fat."""
        return (
            self.date,
            f"{self.totals.calories_kcal:,.0f} kcal",
            f"{self.totals.protein_g:,.1f} g",
            f"{self.totals.carbs_g:,.1f} g",
            f"{self.totals.fat_g:,.1f} g",
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
