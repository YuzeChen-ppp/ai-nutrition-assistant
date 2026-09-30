"""Small, dependency-free validation helpers.

These functions are shared by:

* ``app/ai/schemas.py``  - validating (untrusted) JSON returned by the AI
* ``app/ui/...``         - validating numbers typed by the user

They avoid Pydantic-style exceptions so they can be unit tested without
importing the GUI stack.
"""

from __future__ import annotations

import re
from typing import Any

from app.i18n import tr

__all__ = [
    "ValidationError",
    "coerce_number",
    "coerce_confidence",
    "looks_like_api_key",
    "normalise_meal_type",
    "MEAL_TYPES",
]

# The number must stand on its own: no digit/letter/underscore directly before
# it (so "v1.5" and "0x10" are not numbers) and no digit or underscore directly
# after it. An exponent is part of the number, so "1e400" is read as a whole and
# then rejected as out of range, instead of being silently read as 1. A unit may
# still follow - "140 kcal" and "1500kJ" keep working - because a letter after
# the number is exactly how a unit is written.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\d_])")

# DeepSeek 的 Key 形如 ``sk-xxxxxxxx``。这只是一个"像不像"的判断：
# 代理服务可能用别的格式，所以调用方只据此提醒，不据此拒绝保存。
_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")

# 数字里的逗号/点号有三种常见写法，必须区分开，否则界面显示过的数字读回来会变样：
#   1. 英文千位分隔符   "4,200"、"1,234,567"        -> 4200 / 1234567
#   2. 欧洲小数逗号     "0,5"、"12,50"、"1,2345"     -> 0.5 / 12.5 / 1.2345
#   3. 欧洲千位点+小数逗号 "1.234,56"                -> 1234.56
# 其中 "4,200" 是唯一真正有歧义的写法（欧洲人可能读成 4.2）。本程序的界面就是用
# 逗号做千位分隔（例如钠 4,200 mg），所以这里按「千位分隔符」解释，保证
# 「显示 -> 读回」不会改变数值。
_THOUSANDS_COMMA_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")
_DECIMAL_COMMA_RE = re.compile(r"^-?\d{1,3},\d{1,4}$")
_EUROPEAN_FULL_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$")

MEAL_TYPES: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")

_MEAL_TYPE_ALIASES = {
    "breakfast": "breakfast",
    "brekkie": "breakfast",
    "morning": "breakfast",
    "早餐": "breakfast",
    "早饭": "breakfast",
    "lunch": "lunch",
    "midday": "lunch",
    "午餐": "lunch",
    "午饭": "lunch",
    "dinner": "dinner",
    "supper": "dinner",
    "evening": "dinner",
    "晚餐": "dinner",
    "晚饭": "dinner",
    "snack": "snack",
    "snacks": "snack",
    "dessert": "snack",
    "drink": "snack",
    "加餐": "snack",
    "零食": "snack",
    "夜宵": "snack",
}


class ValidationError(ValueError):
    """Raised when a value cannot be interpreted as a sensible number/choice."""


def looks_like_api_key(value: str | None) -> bool:
    """True when the value looks like a DeepSeek ``sk-...`` key.

    Only ever used to *warn* the user: the authoritative check is the
    connection test, because a proxy may accept a differently shaped key.
    """
    return bool(_API_KEY_RE.fullmatch((value or "").strip()))


def coerce_number(
    value: Any,
    *,
    field: str = "value",
    allow_none: bool = False,
) -> float | None:
    """Convert ``value`` to ``float``.

    Accepts numbers, numeric strings and strings that merely contain a number
    such as ``"140 kcal"`` or ``"1,200 mg"``. Booleans are rejected because
    ``True`` would otherwise silently become ``1``. A string that contains no
    standalone number at all (``"about a lot"``, ``"0x10"``) is rejected rather
    than guessed at, and an out-of-range value such as ``"1e400"`` comes back as
    ``inf`` so the caller's own range check can refuse it.
    """
    if value is None:
        if allow_none:
            return None
        raise ValidationError(tr("error.value_required", field=field))
    if isinstance(value, bool):
        raise ValidationError(tr("error.number_field", field=field))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace("\u3000", "")
        if not text:
            if allow_none:
                return None
            raise ValidationError(tr("error.value_required", field=field))
        if _EUROPEAN_FULL_RE.match(text):  # 1.234,56
            text = text.replace(".", "").replace(",", ".")
        elif _THOUSANDS_COMMA_RE.match(text):  # 4,200 / 1,234,567
            text = text.replace(",", "")
        elif _DECIMAL_COMMA_RE.match(text):  # 0,5 / 12,50
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        match = _NUMBER_RE.search(text)
        if not match:
            raise ValidationError(tr("error.value_not_number", value=value, field=field))
        return float(match.group(0))
    raise ValidationError(tr("error.number_field", field=field))


def coerce_confidence(value: Any, *, field: str = "confidence") -> float | None:
    """Normalise a confidence value to the range 0.0 - 1.0.

    ``0.9``, ``"0.9"``, ``90`` and ``"90%"`` all become ``0.9``.
    """
    number = coerce_number(value, field=field, allow_none=True)
    if number is None:
        return None
    if 1.0 < number <= 100.0:
        number = number / 100.0
    if number < 0.0 or number > 1.0:
        raise ValidationError(tr("error.confidence_range"))
    return number


def normalise_meal_type(value: Any, *, default: str = "snack") -> str:
    """Map free text ("Breakfast", "午饭") to one of :data:`MEAL_TYPES`."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _MEAL_TYPE_ALIASES:
        return _MEAL_TYPE_ALIASES[text]
    for alias, meal_type in _MEAL_TYPE_ALIASES.items():
        if alias and alias in text:
            return meal_type
    return default
