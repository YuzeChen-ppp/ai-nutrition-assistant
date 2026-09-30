"""Tolerant parsing of AI answers.

The model is asked for strict JSON, but in practice answers arrive wrapped in
markdown fences, with trailing prose, or as a bare list. This module normalises
all of that and then hands the data to Pydantic (see ``app.ai.schemas``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.ai.errors import (
    AIResponseError,
    NoFoodRecognisedError,
    OnlineReferenceUnavailableError,
)
from app.ai.schemas import FoodAnalysisResult, MealRecommendation, OnlineNutritionReference

__all__ = [
    "extract_json",
    "parse_food_analysis",
    "parse_online_nutrition",
    "parse_recommendation",
]

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    """Return the JSON value contained in ``text``.

    Handles ```json fences, leading/trailing prose and top-level arrays.
    """
    if text is None:
        raise AIResponseError(detail="empty response")
    raw = str(text).strip()
    if not raw:
        raise AIResponseError(detail="empty response")

    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fall back to the first complete JSON document inside the text.
    decoder = json.JSONDecoder()
    for opener in ("{", "["):
        start = raw.find(opener)
        while start != -1:
            try:
                value, _ = decoder.raw_decode(raw[start:])
                return value
            except json.JSONDecodeError:
                start = raw.find(opener, start + 1)
    raise AIResponseError(
        detail=f"could not find JSON in the answer: {raw[:200]!r}",
    )


def _normalise_food_payload(payload: Any) -> dict[str, Any]:
    """Coerce the many possible shapes into ``{"foods": [...]}``."""
    if payload is None:
        raise AIResponseError(detail="null payload")
    if isinstance(payload, list):
        return {"foods": payload}
    if isinstance(payload, dict):
        for key in ("foods", "items", "results", "food_items"):
            if key in payload and isinstance(payload[key], list):
                return payload
        if "name" in payload or "food" in payload or "food_name" in payload:
            return {"foods": [payload]}
        raise AIResponseError(
            detail=f"no food list in the answer: {list(payload)[:10]}",
        )
    raise AIResponseError(detail=f"unexpected payload type {type(payload).__name__}")


def parse_food_analysis(raw: str) -> FoodAnalysisResult:
    """Parse and validate a food-analysis answer."""
    payload = _normalise_food_payload(extract_json(raw))
    try:
        result = FoodAnalysisResult.model_validate(payload)
    except PydanticValidationError as exc:
        raise AIResponseError(
            detail=f"validation failed: {exc.errors()[:3]}",
        ) from exc
    if not result.foods:
        raise NoFoodRecognisedError(detail=result.note or "model returned no foods")
    return result


def parse_recommendation(raw: str) -> MealRecommendation:
    """Parse and validate a next-meal recommendation."""
    payload = extract_json(raw)
    if isinstance(payload, list):
        payload = {"foods": payload, "title": "Suggested meal"}
    if not isinstance(payload, dict):
        raise AIResponseError(detail=f"unexpected payload type {type(payload).__name__}")
    if "foods" not in payload and "items" not in payload:
        # Some models return the food list under a meal name key.
        for value in payload.values():
            if isinstance(value, list) and value:
                payload = {**payload, "foods": value}
                break
    try:
        result = MealRecommendation.model_validate(payload)
    except PydanticValidationError as exc:
        raise AIResponseError(
            detail=f"validation failed: {exc.errors()[:3]}",
        ) from exc
    if not result.foods:
        raise AIResponseError(detail="recommendation contained no foods")
    return result


def parse_online_nutrition(raw: str, *, name: str = "") -> OnlineNutritionReference:
    """Parse and validate an online (second-reference) nutrition answer.

    ``name`` is the food the user asked about; it is only used when the answer
    does not name the food itself. Every failure mode - empty answer, prose
    instead of JSON, a bare number, a negative value, a physically impossible
    value - becomes :class:`OnlineReferenceUnavailableError`. The caller hides
    the online section for all of them, so there is nothing to distinguish here.
    """
    try:
        payload = extract_json(raw)
    except AIResponseError as exc:
        raise OnlineReferenceUnavailableError(detail=exc.detail or "unreadable answer") from exc

    if isinstance(payload, list):
        # Some models wrap the single object in a one-element array.
        payload = payload[0] if payload else None
    if isinstance(payload, (int, float)):
        # A bare number cannot say which nutrient it belongs to.
        raise OnlineReferenceUnavailableError(
            detail=f"the answer was a bare number ({payload!r}), not an object"
        )
    if isinstance(payload, dict):
        for key in ("reference", "nutrition", "food", "data", "result", "per_100g"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                payload = {**payload, **inner}
                break
    if not isinstance(payload, dict):
        raise OnlineReferenceUnavailableError(
            detail=f"unexpected payload type {type(payload).__name__}"
        )

    if name.strip() and not str(payload.get("name") or "").strip():
        # Otherwise the Pydantic alias chain would happily read the food name
        # out of a "food" key, which is also a valid alias for the name field.
        payload = {**payload, "name": name.strip()}

    # "basis" is also a nutrient alias in some answers ({"per": 1430}): a number
    # there is a value, not a basis, and reading it as one would silently keep
    # values that were stated per kg.
    for key in ("basis", "per", "unit_basis", "normalised_to"):
        if isinstance(payload.get(key), (int, float)) and not isinstance(
            payload.get(key), bool
        ):
            raise OnlineReferenceUnavailableError(
                detail=f"{key!r} was a number ({payload[key]!r}), not a basis",
            )

    try:
        reference = OnlineNutritionReference.model_validate(payload)
    except PydanticValidationError as exc:
        raise OnlineReferenceUnavailableError(
            detail=f"validation failed: {exc.errors()[:3]}",
        ) from exc
    return reference
