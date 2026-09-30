"""Pydantic schemas for everything the AI returns.

The AI is an untrusted data source: it can return strings instead of numbers,
confidence values as percentages, negative calories, impossible amounts, or
missing fields. Every answer is validated here before it can reach the
database.

:class:`OnlineNutritionReference` is the one exception to "before it can reach
the database": its values are a *second reference*, shown next to the local
reference table for comparison only. They are never stored and never summed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from app.ai.errors import OnlineReferenceUnavailableError
from app.utils.validation import ValidationError, coerce_confidence, coerce_number

__all__ = [
    "FoodAnalysisResult",
    "FoodItem",
    "MealRecommendation",
    "OnlineNutritionReference",
    "ShoppingItem",
]


def _number(label: str) -> BeforeValidator:
    """Build a validator that also accepts ``"140 kcal"``-style strings."""

    def _coerce(value: Any) -> Any:
        try:
            return coerce_number(value, field=label, allow_none=True)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    return BeforeValidator(_coerce)


def _confidence_validator() -> BeforeValidator:
    def _coerce(value: Any) -> Any:
        try:
            return coerce_confidence(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    return BeforeValidator(_coerce)


def _to_text(value: Any) -> Any:
    """把数字/列表宽松地变成文本（AI 有时把「500」当数量返回）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value)
    return str(value)


def _difficulty_validator() -> BeforeValidator:
    def _coerce(value: Any) -> Any:
        text = str(value or "").strip().lower()
        if text in {"easy", "容易", "简单", "容易买到", "很方便"}:
            return "easy"
        if text in {"moderate", "medium", "一般", "中等", "普通"}:
            return "moderate"
        if text in {"hard", "difficult", "不容易", "难", "不容易买到", "很难"}:
            return "hard"
        return text

    return BeforeValidator(_coerce)


def _shopping_items(value: Any) -> Any:
    """允许 AI 既返回对象数组，也返回纯字符串数组。"""
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    items: list[Any] = []
    for entry in value:
        if isinstance(entry, str):
            items.append({"name": entry})
        else:
            items.append(entry)
    return items


# Reusable annotated types: coercion happens *before* the range checks below.
CalorieValue = Annotated[float, _number("calories")]
GramValue = Annotated[float, _number("grams")]
SodiumValue = Annotated[float, _number("sodium")]
OptionalNumber = Annotated[float | None, _number("value")]
ConfidenceValue = Annotated[float | None, _confidence_validator()]
LooseText = Annotated[str, BeforeValidator(_to_text)]
DifficultyValue = Annotated[str, _difficulty_validator()]


class FoodItem(BaseModel):
    """One food item, with nutrition for the whole stated amount."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    name: str = Field(
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("name", "food", "food_name", "item", "description"),
    )
    amount: OptionalNumber = Field(
        default=None,
        ge=0,
        le=100_000,
        validation_alias=AliasChoices("amount", "quantity", "qty", "servings", "count"),
    )
    unit: str | None = Field(
        default=None,
        max_length=40,
        validation_alias=AliasChoices("unit", "units", "measure", "portion_unit"),
    )
    estimated_weight_g: OptionalNumber = Field(
        default=None,
        ge=0,
        le=20_000,
        validation_alias=AliasChoices(
            "estimated_weight_g",
            "weight_g",
            "weight",
            "grams",
            "weight_grams",
            "estimated_weight",
        ),
    )
    calories_kcal: CalorieValue = Field(
        default=0.0,
        ge=0,
        le=10_000,
        validation_alias=AliasChoices(
            "calories_kcal", "calories", "kcal", "energy_kcal", "energy"
        ),
    )
    protein_g: GramValue = Field(
        default=0.0,
        ge=0,
        le=1_000,
        validation_alias=AliasChoices("protein_g", "protein", "protein_grams", "proteins"),
    )
    carbs_g: GramValue = Field(
        default=0.0,
        ge=0,
        le=1_500,
        validation_alias=AliasChoices(
            "carbs_g", "carbs", "carbohydrate_g", "carbohydrates", "carbohydrate"
        ),
    )
    fat_g: GramValue = Field(
        default=0.0,
        ge=0,
        le=1_000,
        validation_alias=AliasChoices("fat_g", "fat", "fats", "total_fat_g"),
    )
    sugar_g: GramValue = Field(
        default=0.0,
        ge=0,
        le=1_500,
        validation_alias=AliasChoices("sugar_g", "sugar", "sugars", "total_sugar_g"),
    )
    fibre_g: GramValue = Field(
        default=0.0,
        ge=0,
        le=500,
        validation_alias=AliasChoices("fibre_g", "fiber_g", "fibre", "fiber", "dietary_fibre"),
    )
    sodium_mg: SodiumValue = Field(
        default=0.0,
        ge=0,
        le=50_000,
        validation_alias=AliasChoices("sodium_mg", "sodium", "salt_mg", "na_mg"),
    )
    confidence: ConfidenceValue = Field(
        default=None,
        validation_alias=AliasChoices("confidence", "confidence_score", "certainty"),
    )
    cooking_method: str | None = Field(
        default=None,
        max_length=60,
        validation_alias=AliasChoices(
            "cooking_method", "cooking", "method", "preparation", "cooking_style"
        ),
    )

    @model_validator(mode="after")
    def _check_energy_consistency(self) -> "FoodItem":
        # Catch the classic AI mistake of mixing up per-100 g values with a
        # much smaller weight. Only the physically impossible case is rejected;
        # everything else becomes a warning in the confirmation dialog.
        weight = self.estimated_weight_g
        if weight and self.calories_kcal > weight * 12.0:
            raise ValueError(
                f"calories ({self.calories_kcal:.0f} kcal) are impossible for "
                f"{weight:.0f} g of {self.name!r}"
            )
        return self

    def per_100g(self) -> dict[str, float]:
        """Values scaled to 100 g (empty dict when the weight is unknown)."""
        weight = self.estimated_weight_g or 0.0
        if weight <= 0:
            return {}
        factor = 100.0 / weight
        return {
            "calories_kcal": round(self.calories_kcal * factor, 1),
            "protein_g": round(self.protein_g * factor, 1),
            "carbs_g": round(self.carbs_g * factor, 1),
            "fat_g": round(self.fat_g * factor, 1),
            "sugar_g": round(self.sugar_g * factor, 1),
            "fibre_g": round(self.fibre_g * factor, 1),
            "sodium_mg": round(self.sodium_mg * factor, 1),
        }

    def to_record_fields(self) -> dict[str, Any]:
        """Flatten to the column names used by the ``food_items`` table."""
        return {
            "name": self.name,
            "amount": self.amount,
            "unit": self.unit,
            "estimated_weight_g": self.estimated_weight_g,
            "calories_kcal": self.calories_kcal,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "sugar_g": self.sugar_g,
            "fibre_g": self.fibre_g,
            "sodium_mg": self.sodium_mg,
            "confidence": self.confidence,
            "cooking_method": self.cooking_method,
        }


class FoodAnalysisResult(BaseModel):
    """Answer to "what did I just eat?"."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    foods: list[FoodItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("foods", "items", "results", "food_items"),
    )
    note: str = Field(
        default="",
        max_length=1000,
        validation_alias=AliasChoices("note", "notes", "message", "comment", "explanation"),
    )


class ShoppingItem(BaseModel):
    """推荐里「需要购买」的一项食材。"""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    name: str = Field(
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices(
            "name", "item", "food", "food_name", "ingredient", "product"
        ),
    )
    quantity: LooseText = Field(
        default="",
        max_length=80,
        validation_alias=AliasChoices(
            "quantity", "amount", "qty", "size", "estimated_quantity", "need"
        ),
    )
    difficulty: DifficultyValue = Field(
        default="",
        max_length=20,
        validation_alias=AliasChoices(
            "difficulty", "purchase_difficulty", "availability", "availability_level"
        ),
    )


class MealRecommendation(BaseModel):
    """Answer to "what should I eat next?"（按购买条件推荐）。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    title: str = Field(
        default="Suggested meal",
        max_length=120,
        validation_alias=AliasChoices("title", "meal", "meal_name", "suggestion"),
    )
    foods: list[FoodItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("foods", "items", "ingredients", "food_items"),
    )
    reason: str = Field(
        default="",
        max_length=2000,
        validation_alias=AliasChoices("reason", "why", "rationale", "explanation", "justification"),
    )
    tips: str = Field(
        default="",
        max_length=1000,
        validation_alias=AliasChoices("tips", "advice", "note", "notes"),
    )
    difficulty: DifficultyValue = Field(
        default="",
        max_length=20,
        validation_alias=AliasChoices(
            "difficulty", "purchase_difficulty", "availability", "shopping_difficulty"
        ),
    )
    shopping_list: Annotated[list[ShoppingItem], BeforeValidator(_shopping_items)] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "shopping_list", "shopping", "need_to_buy", "to_buy", "items_to_buy",
            "shopping_items", "purchase_list",
        ),
    )

    def shopping_names(self) -> list[str]:
        return [item.name for item in self.shopping_list]


# --------------------------------------------------------------------------
# Online reference (second opinion) - comparison only, never stored
# --------------------------------------------------------------------------

# ``nutrient_kind`` maps a field to the unit the app displays it in. The UI only
# ever shows these units; anything else the API sends is converted here.
KIND_KCAL = "kcal"
KIND_GRAM = "g"
KIND_MILLIGRAM = "mg"

KCAL_PER_KJ = 0.239
MG_PER_GRAM = 1000.0

#: Field name -> (display unit, English label, Simplified Chinese label).
ONLINE_NUTRIENT_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("calories_kcal", KIND_KCAL, "Calories", "热量"),
    ("protein_g", KIND_GRAM, "Protein", "蛋白质"),
    ("carbs_g", KIND_GRAM, "Carbohydrates", "碳水"),
    ("fat_g", KIND_GRAM, "Fat", "脂肪"),
    ("sugar_g", KIND_GRAM, "Sugar", "糖"),
    ("fibre_g", KIND_GRAM, "Dietary fibre", "膳食纤维"),
    ("sodium_mg", KIND_MILLIGRAM, "Sodium", "钠"),
)

_BASIS_PER_100G = "per_100g"
_PER_100G_ALIASES = frozenset(
    {
        "per_100g",
        "per100g",
        "100g",
        "per_100_grams",
        "per100grams",
        "per_100_g",
        "每100克",
        "每100g",
    }
)
_PER_KG_ALIASES = frozenset(
    {"per_kg", "perkg", "1000g", "per_1000g", "per_1000_g", "每千克", "每1000克"}
)

_FIELD_UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "calories_kcal": ("kj", "kilojoule", "kilojoules", "千焦"),
    "sodium_mg": ("g", "gram", "grams", "克"),
}

_DECIMAL_COMMA_RE = re.compile(r"^-?\d{1,3},\d{1,4}$")
_KILOGRAM_RE = re.compile(r"(?i)(kg|kilogram|公斤|千克)")
_GRAM_RE = re.compile(r"(?i)\b(g|gram|grams|克)\b")


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """How one online value is written in the answer."""

    name: str
    kind: str


_FIELD_SPECS: dict[str, _FieldSpec] = {
    name: _FieldSpec(name=name, kind=kind) for name, kind, _, _ in ONLINE_NUTRIENT_FIELDS
}

# Suffix -> value multiplier, per field. An explicit ``"0.5 g"`` for sodium and
# an explicit ``"1500 kJ"`` for calories are both converted during parsing, so
# the GUI can always print one unit per field.
_UNIT_CONVERSIONS: dict[str, dict[str, float]] = {
    "calories_kcal": {"kj": KCAL_PER_KJ},
    "sodium_mg": {"g": MG_PER_GRAM},
}

# Fields whose nutrient is a subset of another one: protein/carbs/fat/sugar are
# not all "parts of 100 g", so only these two are checked this way.
_SUBSET_FIELDS: tuple[tuple[str, str], ...] = (
    ("sugar_g", "carbs_g"),
    ("fibre_g", "carbs_g"),
)


def _basis_text(value: Any) -> Any:
    """Keep the basis a string, so the parser can reject a numeric alias safely.

    ``basis`` doubles as a nutrient alias for some answers (a bare ``{"per": 100}``
    style document), and a float would reach ``re.sub`` inside the conversions.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _reference_number(spec: _FieldSpec) -> BeforeValidator:
    """Coerce one online value, converting any non-standard unit on the way.

    ``before`` runs *before* Pydantic validates, so it also owns the null rule:
    an absent or unreadable value becomes ``None`` (unknown) and is never
    silently turned into ``0``, which would mean something different.
    """

    def _coerce(value: Any) -> Any:
        if value is None:
            return None
        factor = 1.0
        if isinstance(value, str):
            factor *= _unit_factor(value, spec)
        try:
            number = coerce_number(value, field=spec.name, allow_none=True)
        except ValidationError as exc:
            # Unreadable junk is "unknown"; the range checks below still see a
            # real number when the API did send one.
            raise ValueError(str(exc)) from exc
        if number is None:
            return None
        return float(number) * factor

    return BeforeValidator(_coerce)


def _unit_factor(text: Any, spec: _FieldSpec) -> float:
    """Multiplier for an explicitly stated unit (``"1500 kJ"``, ``"0.5 g"``)."""
    lowered = str(text).strip().lower()
    if "kj" in lowered:
        return _UNIT_CONVERSIONS[spec.name].get("kj", 1.0)
    # ``"0.5 g"`` must not be mistaken for the field's own unit, so only the
    # units registered for this field are honoured. A European decimal comma
    # ("0,5 g") is ambiguous and stays unconverted instead of risking a wrong number.
    if _DECIMAL_COMMA_RE.match(lowered.replace(" ", "")):
        return 1.0
    suffix = re.search(r"([a-z\u4e00-\u9fff]+)\s*$", lowered)
    if suffix:
        unit = suffix.group(1)
        for alias in _FIELD_UNIT_ALIASES.get(spec.name, ()):
            if unit == alias:
                return _UNIT_CONVERSIONS[spec.name].get(alias, 1.0)
    return 1.0


def _basis_factor(basis: Any) -> float:
    """Divide values that are stated per kg so everything is per 100 g."""
    text = re.sub(r"[\s_]+", "", str(basis or "").strip().lower())
    if text in _PER_KG_ALIASES:
        return 0.1
    if text in _PER_100G_ALIASES:
        return 1.0
    # A missing basis is treated as per 100 g (the prompt asks for that); a
    # kilogram that is spelled out ("per kg" / "每公斤") is still caught.
    if text and _KILOGRAM_RE.search(text) and not _GRAM_RE.search(text):
        return 0.1
    return 1.0


class OnlineNutritionReference(BaseModel):
    """Nutrition per 100 g of one food, as estimated online.

    This is a **second reference**, not a data source: the GUI shows it next to
    the local table in ``app/nutrition/food_data.py`` and nothing else consumes
    it. Missing fields stay ``None`` (= "unknown") instead of defaulting to
    ``0``, because a genuine zero (black coffee has no fibre) and an unanswered
    field mean different things in the comparison.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str = Field(
        default="",
        max_length=120,
        validation_alias=AliasChoices("name", "food", "food_name", "item", "query"),
    )
    basis: Annotated[str, BeforeValidator(_basis_text)] = Field(
        default=_BASIS_PER_100G,
        max_length=40,
        validation_alias=AliasChoices("basis", "per", "unit_basis", "normalised_to"),
    )
    calories_kcal: Annotated[float | None, _reference_number(_FIELD_SPECS["calories_kcal"])] = Field(
        default=None,
        ge=0,
        le=10_000,
        validation_alias=AliasChoices(
            "calories_kcal", "calories", "kcal", "energy_kcal", "energy"
        ),
    )
    protein_g: Annotated[float | None, _reference_number(_FIELD_SPECS["protein_g"])] = Field(
        default=None,
        ge=0,
        le=1_000,
        validation_alias=AliasChoices("protein_g", "protein", "protein_grams", "proteins"),
    )
    carbs_g: Annotated[float | None, _reference_number(_FIELD_SPECS["carbs_g"])] = Field(
        default=None,
        ge=0,
        le=1_500,
        validation_alias=AliasChoices(
            "carbs_g", "carbs", "carbohydrate_g", "carbohydrates", "carbohydrate"
        ),
    )
    fat_g: Annotated[float | None, _reference_number(_FIELD_SPECS["fat_g"])] = Field(
        default=None,
        ge=0,
        le=1_000,
        validation_alias=AliasChoices("fat_g", "fat", "fats", "total_fat_g"),
    )
    sugar_g: Annotated[float | None, _reference_number(_FIELD_SPECS["sugar_g"])] = Field(
        default=None,
        ge=0,
        le=1_500,
        validation_alias=AliasChoices("sugar_g", "sugar", "sugars", "total_sugar_g"),
    )
    fibre_g: Annotated[float | None, _reference_number(_FIELD_SPECS["fibre_g"])] = Field(
        default=None,
        ge=0,
        le=500,
        validation_alias=AliasChoices(
            "fibre_g", "fiber_g", "fibre", "fiber", "dietary_fibre", "dietary_fiber"
        ),
    )
    sodium_mg: Annotated[float | None, _reference_number(_FIELD_SPECS["sodium_mg"])] = Field(
        default=None,
        ge=0,
        le=50_000,
        validation_alias=AliasChoices("sodium_mg", "sodium", "salt_mg", "na_mg"),
    )
    confidence: ConfidenceValue = Field(
        default=None,
        validation_alias=AliasChoices("confidence", "confidence_score", "certainty"),
    )
    note: str = Field(
        default="",
        max_length=1000,
        validation_alias=AliasChoices("note", "notes", "comment", "message", "explanation"),
    )

    @model_validator(mode="after")
    def _check_values(self) -> "OnlineNutritionReference":
        factor = _basis_factor(self.basis)
        if factor != 1.0:
            # The parser already rejected a numeric ``basis`` alias, so a real
            # value here is a stated basis - never a nutrient.
            for name, _, _, _ in ONLINE_NUTRIENT_FIELDS:
                current = getattr(self, name)
                if current is not None:
                    object.__setattr__(self, name, round(float(current) * factor, 4))
        self._check_not_empty()
        self._check_subsets()
        return self

    def _check_not_empty(self) -> None:
        if all(getattr(self, name) is None for name, _, _, _ in ONLINE_NUTRIENT_FIELDS):
            # An answer without a single number is not a reference: the caller
            # hides the online section instead of showing a row of "unknown".
            raise ValueError("the online reference contained no nutrient values")

    def _check_subsets(self) -> None:
        # The classic AI mistake: sugar or fibre larger than the total carbs.
        for part, whole in _SUBSET_FIELDS:
            small = getattr(self, part)
            large = getattr(self, whole)
            if small is None or large is None:
                continue
            if large < 0.05:
                # Everything is ~zero (olive oil has no carbs at all), so the
                # subset relation says nothing.
                continue
            if small > large * 1.02 + 0.2:
                raise ValueError(
                    f"{part} ({small}) cannot exceed {whole} ({large}) per 100 g"
                )
        self._check_macros()

    def _check_macros(self) -> None:
        """Reject answers whose macros cannot physically fit in 100 g."""
        macros = [self.protein_g, self.carbs_g, self.fat_g]
        if not all(value is not None for value in macros):
            return
        protein, carbs, fat = (float(value) for value in macros)
        if protein + carbs + fat > 100.0 + 0.5:
            raise ValueError(
                f"protein + carbs + fat = {protein + carbs + fat:.1f} g per 100 g"
            )
        if self.calories_kcal is not None:
            energy = protein * 4.0 + carbs * 4.0 + fat * 9.0
            # 9.5 kcal/g is the physical ceiling (fat is 9 kcal/g); the same
            # tolerance the confirmation dialog uses for AI items.
            if self.calories_kcal > 9.5 * 100.0 + 1.0:
                raise ValueError(
                    f"{self.calories_kcal:.0f} kcal is impossible for 100 g"
                )
            if energy > 0 and self.calories_kcal > energy * 1.6 + 50:
                raise ValueError(
                    f"{self.calories_kcal:.0f} kcal exceeds the energy of its macros"
                )

    # ------------------------------------------------------------------ output
    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(name for name, _, _, _ in ONLINE_NUTRIENT_FIELDS)

    def per_100g(self) -> dict[str, float | None]:
        """``{field: value or None}`` - ``None`` means the answer had no value."""
        return {name: getattr(self, name) for name in self.field_names()}

    def known_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.field_names() if getattr(self, name) is not None)

    def unknown_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.field_names() if getattr(self, name) is None)

    def is_complete(self) -> bool:
        return not self.unknown_fields()

    def label(self, name: str, language: str | None = None) -> str:
        """Localised nutrient label (English by default, Chinese for ``zh*``)."""
        for field, _, english, chinese in ONLINE_NUTRIENT_FIELDS:
            if field == name:
                return chinese if _is_chinese(language) else english
        return name


def _is_chinese(language: str | None) -> bool:
    return bool(language) and str(language).strip().lower().startswith("zh")
