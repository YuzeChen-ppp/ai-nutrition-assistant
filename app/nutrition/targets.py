"""Personal nutrition targets.

Every number produced here is an *estimate* produced by the Mifflin-St Jeor
equation plus standard activity factors and goal adjustments. It is not
medical advice.

All of this arithmetic happens in Python - the AI never calculates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.i18n import tr

__all__ = [
    "ACTIVITY_LABEL_KEYS",
    "ACTIVITY_LEVELS",
    "DISCLAIMER_KEY",
    "ESTIMATE_NOTE_KEY",
    "GOAL_LABEL_KEYS",
    "GOALS",
    "NutritionTargets",
    "SEX_LABEL_KEYS",
    "SEX_OPTIONS",
    "activity_labels",
    "activity_multiplier",
    "calculate_targets",
    "disclaimer",
    "estimate_note",
    "goal_labels",
    "mifflin_st_jeor_bmr",
    "normalise_activity",
    "normalise_goal",
    "normalise_sex",
    "sex_labels",
    "tdee_from_bmr",
]

# Translation keys (see app/translations.py). Keeping keys here means new
# languages need no changes in the nutrition layer.
DISCLAIMER_KEY = "profile.disclaimer"
ESTIMATE_NOTE_KEY = "dashboard.estimate_note"

SEX_OPTIONS: tuple[str, ...] = ("male", "female", "unspecified")
GOALS: tuple[str, ...] = ("lose", "maintain", "gain")
ACTIVITY_LEVELS: tuple[str, ...] = ("sedentary", "light", "moderate", "very_active")

SEX_LABEL_KEYS = {
    "male": "profile.sex_male",
    "female": "profile.sex_female",
    "unspecified": "profile.sex_unspecified",
}

GOAL_LABEL_KEYS = {
    "lose": "profile.goal_lose",
    "maintain": "profile.goal_maintain",
    "gain": "profile.goal_gain",
}

ACTIVITY_LABEL_KEYS = {
    "sedentary": "profile.activity_sedentary",
    "light": "profile.activity_light",
    "moderate": "profile.activity_moderate",
    "very_active": "profile.activity_very_active",
}


def disclaimer() -> str:
    """The "estimates only" sentence in the active language."""
    return tr(DISCLAIMER_KEY)


def estimate_note() -> str:
    """The "values are estimates" note in the active language."""
    return tr(ESTIMATE_NOTE_KEY)


def activity_labels() -> dict[str, str]:
    return {key: tr(value) for key, value in ACTIVITY_LABEL_KEYS.items()}


def goal_labels() -> dict[str, str]:
    return {key: tr(value) for key, value in GOAL_LABEL_KEYS.items()}


def sex_labels() -> dict[str, str]:
    return {key: tr(value) for key, value in SEX_LABEL_KEYS.items()}

_ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "very_active": 1.725,
}

_ACTIVITY_ALIASES = {
    "sedentary": "sedentary",
    "light": "light",
    "lightly active": "light",
    "light activity": "light",
    "moderate": "moderate",
    "moderately active": "moderate",
    "very": "very_active",
    "very active": "very_active",
    "very_active": "very_active",
    "high": "very_active",
    "久坐": "sedentary",
    "轻度": "light",
    "中度": "moderate",
    "高强度": "very_active",
}

_GOAL_ALIASES = {
    "lose": "lose",
    "lose weight": "lose",
    "cut": "lose",
    "减脂": "lose",
    "减肥": "lose",
    "maintain": "maintain",
    "maintain weight": "maintain",
    "保持": "maintain",
    "维持": "maintain",
    "gain": "gain",
    "gain weight": "gain",
    "bulk": "gain",
    "增重": "gain",
    "增肌": "gain",
}

_SEX_ALIASES = {
    "male": "male",
    "m": "male",
    "man": "male",
    "男": "male",
    "female": "female",
    "f": "female",
    "woman": "female",
    "女": "female",
    "unspecified": "unspecified",
    "other": "unspecified",
    "prefer not to say": "unspecified",
    "其他": "unspecified",
}


@dataclass(frozen=True, slots=True)
class NutritionTargets:
    """Daily estimates for one user profile."""

    bmr: float
    tdee: float
    calorie_target: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fibre_g: float
    sugar_limit_g: float
    sodium_limit_mg: float
    formula: str = "Mifflin-St Jeor"

    def as_dict(self) -> dict[str, float]:
        return {
            "bmr": self.bmr,
            "tdee": self.tdee,
            "calorie_target": self.calorie_target,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "fibre_g": self.fibre_g,
            "sugar_limit_g": self.sugar_limit_g,
            "sodium_limit_mg": self.sodium_limit_mg,
        }

    def as_totals(self) -> Any:
        """Return the targets in :class:`~app.nutrition.calculator.DailyTotals` shape."""
        from app.nutrition.calculator import DailyTotals

        return DailyTotals(
            calories_kcal=self.calorie_target,
            protein_g=self.protein_g,
            carbs_g=self.carbs_g,
            fat_g=self.fat_g,
            sugar_g=self.sugar_limit_g,
            fibre_g=self.fibre_g,
            sodium_mg=self.sodium_limit_mg,
        )


def normalise_sex(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _SEX_ALIASES.get(text, "unspecified")


def normalise_activity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _ACTIVITY_ALIASES:
        return _ACTIVITY_ALIASES[text]
    for alias, canonical in _ACTIVITY_ALIASES.items():
        if alias and alias in text:
            return canonical
    return "sedentary"


def normalise_goal(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _GOAL_ALIASES:
        return _GOAL_ALIASES[text]
    for alias, canonical in _GOAL_ALIASES.items():
        if alias and alias in text:
            return canonical
    return "maintain"


def activity_multiplier(activity_level: Any) -> float:
    return _ACTIVITY_MULTIPLIERS[normalise_activity(activity_level)]


def mifflin_st_jeor_bmr(
    *,
    sex: Any,
    weight_kg: float,
    height_cm: float,
    age: float,
) -> float:
    """Basal metabolic rate in kcal/day (Mifflin-St Jeor, 1990).

    ``unspecified`` uses the midpoint of the male and female constants.
    """
    base = 10.0 * float(weight_kg) + 6.25 * float(height_cm) - 5.0 * float(age)
    sex_key = normalise_sex(sex)
    if sex_key == "male":
        return base + 5.0
    if sex_key == "female":
        return base - 161.0
    return base - 78.0


def tdee_from_bmr(bmr: float, activity_level: Any) -> float:
    """Total daily energy expenditure."""
    return float(bmr) * activity_multiplier(activity_level)


def calculate_targets(
    *,
    age: float,
    sex: Any,
    height_cm: float,
    weight_kg: float,
    activity_level: Any,
    goal: Any,
) -> NutritionTargets:
    """Estimate BMR, TDEE and daily macro targets for one profile."""
    age = float(age)
    height_cm = float(height_cm)
    weight_kg = float(weight_kg)
    sex_key = normalise_sex(sex)
    goal_key = normalise_goal(goal)

    bmr = mifflin_st_jeor_bmr(sex=sex_key, weight_kg=weight_kg, height_cm=height_cm, age=age)
    tdee = tdee_from_bmr(bmr, activity_level)

    if goal_key == "lose":
        calorie_target = tdee * 0.80
        minimum = 1500.0 if sex_key == "male" else 1200.0
        calorie_target = max(calorie_target, minimum, bmr)
    elif goal_key == "gain":
        calorie_target = tdee * 1.15
    else:
        calorie_target = tdee

    # Protein: body-weight based, capped so it never eats the whole budget.
    protein_per_kg = 1.8 if goal_key == "lose" else 1.6
    protein_g = weight_kg * protein_per_kg
    protein_g = min(protein_g, (calorie_target * 0.35) / 4.0)
    protein_g = max(protein_g, weight_kg * 0.8)

    # Fat: ~27% of energy.
    fat_g = (calorie_target * 0.27) / 9.0

    # Carbohydrate: whatever energy is left.
    carb_kcal = calorie_target - (protein_g * 4.0) - (fat_g * 9.0)
    carbs_g = max(carb_kcal / 4.0, 50.0)

    # Fibre: 14 g per 1000 kcal (USDA/EFSA style guidance), clamped sensibly.
    fibre_g = min(max((calorie_target / 1000.0) * 14.0, 15.0), 50.0)

    # Free sugars: <=10% of energy. Sodium: 2300 mg (upper bound, not a goal).
    sugar_limit_g = (calorie_target * 0.10) / 4.0
    sodium_limit_mg = 2300.0

    return NutritionTargets(
        bmr=round(bmr, 1),
        tdee=round(tdee, 1),
        calorie_target=round(calorie_target, 1),
        protein_g=round(protein_g, 1),
        carbs_g=round(carbs_g, 1),
        fat_g=round(fat_g, 1),
        fibre_g=round(fibre_g, 1),
        sugar_limit_g=round(sugar_limit_g, 1),
        sodium_limit_mg=sodium_limit_mg,
    )
