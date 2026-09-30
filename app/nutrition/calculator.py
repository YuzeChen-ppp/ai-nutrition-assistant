"""Pure daily-nutrition arithmetic.

These helpers never call the AI. They work with anything that exposes the
standard nutrient attributes (Pydantic models from the AI layer, SQLite row
records, or the targets object), which keeps the maths unit-testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Sequence

from app.i18n import tr

__all__ = [
    "NUTRIENT_FIELDS",
    "DailyTotals",
    "describe_remaining_calories",
    "format_grams",
    "format_kcal",
    "progress_fraction",
    "rescale_values",
    "sanity_warnings",
    "sum_food_items",
]

# Field order is also the display order used by the UI.
NUTRIENT_FIELDS: tuple[str, ...] = (
    "calories_kcal",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sugar_g",
    "fibre_g",
    "sodium_mg",
)


@dataclass(frozen=True, slots=True)
class DailyTotals:
    """Sum of all nutrient values of one day (or one meal)."""

    calories_kcal: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    sugar_g: float = 0.0
    fibre_g: float = 0.0
    sodium_mg: float = 0.0
    item_count: int = 0

    def __add__(self, other: "DailyTotals") -> "DailyTotals":
        return DailyTotals(
            calories_kcal=self.calories_kcal + other.calories_kcal,
            protein_g=self.protein_g + other.protein_g,
            carbs_g=self.carbs_g + other.carbs_g,
            fat_g=self.fat_g + other.fat_g,
            sugar_g=self.sugar_g + other.sugar_g,
            fibre_g=self.fibre_g + other.fibre_g,
            sodium_mg=self.sodium_mg + other.sodium_mg,
            item_count=self.item_count + other.item_count,
        )

    def __sub__(self, other: "DailyTotals") -> "DailyTotals":
        return DailyTotals(
            calories_kcal=self.calories_kcal - other.calories_kcal,
            protein_g=self.protein_g - other.protein_g,
            carbs_g=self.carbs_g - other.carbs_g,
            fat_g=self.fat_g - other.fat_g,
            sugar_g=self.sugar_g - other.sugar_g,
            fibre_g=self.fibre_g - other.fibre_g,
            sodium_mg=self.sodium_mg - other.sodium_mg,
            item_count=self.item_count - other.item_count,
        )

    def as_dict(self) -> dict[str, float]:
        data = asdict(self)
        data.pop("item_count", None)
        return data

    def rounded(self, digits: int = 1) -> "DailyTotals":
        return replace(
            self,
            calories_kcal=round(self.calories_kcal, 0),
            protein_g=round(self.protein_g, digits),
            carbs_g=round(self.carbs_g, digits),
            fat_g=round(self.fat_g, digits),
            sugar_g=round(self.sugar_g, digits),
            fibre_g=round(self.fibre_g, digits),
            sodium_mg=round(self.sodium_mg, 0),
        )


def _value(item: Any, field: str, default: float = 0.0) -> float:
    if isinstance(item, dict):
        raw = item.get(field, default)
    else:
        raw = getattr(item, field, default)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def sum_food_items(items: Iterable[Any]) -> DailyTotals:
    """Add up every nutrient field of ``items``."""
    totals = DailyTotals()
    count = 0
    for item in items:
        count += 1
        totals = totals + DailyTotals(
            calories_kcal=_value(item, "calories_kcal"),
            protein_g=_value(item, "protein_g"),
            carbs_g=_value(item, "carbs_g"),
            fat_g=_value(item, "fat_g"),
            sugar_g=_value(item, "sugar_g"),
            fibre_g=_value(item, "fibre_g"),
            sodium_mg=_value(item, "sodium_mg"),
        )
    return replace(totals, item_count=count)


def progress_fraction(consumed: float, target: float) -> float:
    """Consumed/target clamped to 0-1 for progress bars."""
    try:
        consumed = float(consumed)
        target = float(target)
    except (TypeError, ValueError):
        return 0.0
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, consumed / target))


def rescale_values(
    item: Any,
    *,
    original_weight_g: float | None,
    new_weight_g: float | None,
) -> dict[str, float]:
    """Scale every nutrient when the user changes the weight of a food.

    When either weight is missing the current values are returned unchanged -
    we never invent data.
    """
    original = float(original_weight_g or 0.0)
    new = float(new_weight_g or 0.0)
    if original <= 0 or new <= 0:
        return {field: _value(item, field) for field in NUTRIENT_FIELDS}
    factor = new / original
    return {field: round(_value(item, field) * factor, 2) for field in NUTRIENT_FIELDS}


def sanity_warnings(items: Sequence[Any]) -> list[str]:
    """Flag values that are mathematically impossible or suspicious.

    Energy density above 9.5 kcal/g is not achievable by fat/protein/carbohydrate
    or alcohol, so it usually means the AI mixed up per-100 g and total values.
    """
    warnings: list[str] = []
    for item in items:
        name = str(
            item.get("name") if isinstance(item, dict) else getattr(item, "name", "food")
        ).strip() or "food"
        weight = _value(item, "estimated_weight_g")
        calories = _value(item, "calories_kcal")
        if weight > 0 and calories > weight * 9.5 + 1:
            warnings.append(
                tr(
                    "calc.warn_energy_density",
                    name=name,
                    kcal=f"{calories:.0f}",
                    weight=f"{weight:.0f}",
                )
            )
        macros = (
            _value(item, "protein_g") * 4.0
            + _value(item, "carbs_g") * 4.0
            + _value(item, "fat_g") * 9.0
        )
        if calories > 0 and macros > calories * 1.6 + 50:
            warnings.append(tr("calc.warn_macros", name=name))
        if weight > 0 and calories <= 0 and macros <= 0:
            warnings.append(tr("calc.warn_zero", name=name))
        confidence = item.get("confidence") if isinstance(item, dict) else getattr(
            item, "confidence", None
        )
        if confidence is not None and float(confidence) < 0.5:
            warnings.append(tr("calc.warn_low_confidence", name=name))
    return warnings


def format_kcal(value: float) -> str:
    return f"{value:,.0f} kcal"


def format_grams(value: float) -> str:
    return f"{value:,.1f} g"


def describe_remaining_calories(remaining_kcal: float) -> str:
    """Human sentence for the dashboard (no alarming language)."""
    if remaining_kcal > 25:
        return tr("calc.remaining", kcal=f"{remaining_kcal:,.0f}")
    if remaining_kcal < -25:
        return tr("calc.over_target", kcal=f"{abs(remaining_kcal):,.0f}")
    return tr("calc.near_target")
