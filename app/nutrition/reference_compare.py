"""Compare the local reference table with the online (second) reference.

Two independent estimates of the same thing, shown side by side:

* the local table in :mod:`app.nutrition.food_data` - the app's primary
  reference, unchanged and still the only one that feeds calculations;
* an optional online figure, fetched only when the user presses the button on
  the confirmation page.

This module is pure logic: no Qt, no network, no database. The one rule that
matters is that **neither side is authoritative**. A large difference produces a
neutral sentence asking the user to check the portion or the cooking method, and
never suggests that the online value is the correct one - both are estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.schemas import (
    KIND_KCAL,
    KIND_MILLIGRAM,
    ONLINE_NUTRIENT_FIELDS,
)
from app.i18n import tr
from app.nutrition.food_data import FoodReference, lookup, per_100g_values

__all__ = [
    "DIFFERENCE_THRESHOLD",
    "MIN_MEANINGFUL_VALUE",
    "NutrientComparison",
    "ReferenceComparison",
    "build_comparison",
    "is_meaningful_difference",
    "relative_difference",
]

#: Relative difference (|online - local| / local) above which the neutral hint
#: is shown. One constant, easy to tune.
DIFFERENCE_THRESHOLD = 0.30

#: Below this value a relative difference is noise, not a disagreement: two
#: estimates of "fibre 0.2 g vs 0.4 g" must not trigger a warning.
MIN_MEANINGFUL_VALUE = 0.5

#: Field -> display unit. Everything else is grams.
UNIT_LABELS: dict[str, str] = {
    "calories_kcal": KIND_KCAL,
    "sodium_mg": KIND_MILLIGRAM,
}
_DEFAULT_UNIT = "g"


def unit_for(field: str) -> str:
    """Display unit of ``field`` (``kcal`` / ``mg`` / ``g``)."""
    return UNIT_LABELS.get(field, _DEFAULT_UNIT)


def format_value(field: str, value: float | None) -> str:
    """Format one per-100 g value; ``None`` becomes the word "unknown".

    A missing value is *not* printed as ``0``: "unknown" and "none" are
    different statements and the user has to be able to tell them apart.
    """
    if value is None:
        return tr("reference.unknown")
    number = float(value)
    text = f"{number:,.0f}" if field in {"calories_kcal", "sodium_mg"} else f"{number:,.1f}"
    return f"{text} {unit_for(field)}"


def relative_difference(online: float | None, local: float | None) -> float | None:
    """``|online - local| / local``, or ``None`` when it cannot be computed."""
    if online is None or local is None:
        return None
    try:
        online_value = float(online)
        local_value = float(local)
    except (TypeError, ValueError):
        return None
    if local_value <= 0:
        return None
    return abs(online_value - local_value) / local_value


def is_meaningful_difference(
    online: float | None,
    local: float | None,
    *,
    threshold: float = DIFFERENCE_THRESHOLD,
    minimum: float = MIN_MEANINGFUL_VALUE,
) -> bool:
    """True when the two estimates differ enough to be worth mentioning."""
    if online is None or local is None:
        return False
    if max(float(online), float(local)) < minimum:
        return False
    difference = relative_difference(online, local)
    return difference is not None and difference > threshold


@dataclass(frozen=True, slots=True)
class NutrientComparison:
    """One row of the side-by-side table (values are per 100 g)."""

    field: str
    local: float | None
    online: float | None
    difference: float | None

    @property
    def unit(self) -> str:
        return unit_for(self.field)

    def local_text(self) -> str:
        return format_value(self.field, self.local)

    def online_text(self) -> str:
        return format_value(self.field, self.online)

    def exceeds(self, *, threshold: float = DIFFERENCE_THRESHOLD) -> bool:
        return is_meaningful_difference(self.online, self.local, threshold=threshold)


@dataclass(frozen=True, slots=True)
class ReferenceComparison:
    """The whole side-by-side view for one food."""

    name: str
    local_source: str | None
    rows: tuple[NutrientComparison, ...]

    def local_value(self, field: str) -> float | None:
        return self._value(field, "local")

    def online_value(self, field: str) -> float | None:
        return self._value(field, "online")

    def _value(self, field: str, side: str) -> float | None:
        for row in self.rows:
            if row.field == field:
                return getattr(row, side)
        return None

    def has_local(self) -> bool:
        return self.local_source is not None

    def has_online(self) -> bool:
        return any(row.online is not None for row in self.rows)

    def significant_rows(self) -> tuple[NutrientComparison, ...]:
        return tuple(row for row in self.rows if row.exceeds())

    def exceeds_threshold(self) -> bool:
        """True when at least one nutrient differs by more than the threshold."""
        return bool(self.significant_rows())

    def difference_note(self) -> str:
        """Neutral Chinese sentence; suggests no winner, only a re-check."""
        if not self.exceeds_threshold():
            return ""
        return tr("reference.difference")


def build_comparison(
    item: Any,
    online: Any,
    *,
    threshold: float = DIFFERENCE_THRESHOLD,
) -> ReferenceComparison:
    """Build the side-by-side view for ``item`` (an AI item, a DB row or a dict).

    Both sides are normalised to **per 100 g**: the left column is the item's own
    values scaled by its weight (or the local table's typical values when the
    weight is unknown), and the right column is what the API returned for 100 g.
    Nothing is invented: without a weight only the local table can be shown, and
    the caller says so instead of guessing.
    """
    name = _text(_read(item, "name")) or _text(_read(online, "name"))
    local_reference = lookup(name) if name else None
    local_values = _local_per_100g(item, local_reference)
    online_values = _online_per_100g(online)

    rows = tuple(
        NutrientComparison(
            field=field,
            local=local_values.get(field),
            online=online_values.get(field),
            difference=relative_difference(
                online_values.get(field), local_values.get(field)
            ),
        )
        for field, _, _, _ in ONLINE_NUTRIENT_FIELDS
    )
    _ = threshold  # rows carry raw values; callers pass a threshold to exceeds()
    return ReferenceComparison(
        name=name,
        local_source=local_reference.name if local_reference else None,
        rows=rows,
    )


def _local_per_100g(
    item: Any,
    reference: FoodReference | None = None,
) -> dict[str, float | None]:
    """The left column: the item's own per-100 g values, else the local table."""
    scaled = per_100g_values(item)
    if scaled is not None:
        return {field: scaled.get(field) for field, _, _, _ in ONLINE_NUTRIENT_FIELDS}
    if reference is None:
        return {field: None for field, _, _, _ in ONLINE_NUTRIENT_FIELDS}
    return {
        field: float(reference.per_100g[field]) for field, _, _, _ in ONLINE_NUTRIENT_FIELDS
    }


def _online_per_100g(online: Any) -> dict[str, float | None]:
    if online is None:
        return {field: None for field, _, _, _ in ONLINE_NUTRIENT_FIELDS}
    per_100g = getattr(online, "per_100g", None)
    if callable(per_100g):
        values = per_100g()
        return {
            field: _number(values.get(field)) for field, _, _, _ in ONLINE_NUTRIENT_FIELDS
        }
    return {
        field: _number(_read(online, field)) for field, _, _, _ in ONLINE_NUTRIENT_FIELDS
    }


def _scale(value: float | None, factor: float) -> float | None:
    return None if value is None else round(float(value) * factor, 2)


def _read(source: Any, field: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(field)
    return getattr(source, field, None)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()
