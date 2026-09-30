"""Tiny translation engine.

Usage::

    from app.i18n import tr
    label.setText(tr("dashboard.upload_photo"))
    message = tr("status.saved_items", count=3, kcal="344")

Only Simplified Chinese is shipped, but the lookup is language-keyed so a new
language is a new dictionary in :mod:`app.translations`.
"""

from __future__ import annotations

from datetime import date as date_cls

from app.translations import DEFAULT_LANGUAGE, LANGUAGE_LABELS, TRANSLATIONS

__all__ = [
    "available_languages",
    "current_language",
    "format_date",
    "gettext_noop",
    "language_label",
    "set_language",
    "tr",
    "tr_text",
]

_current_language = DEFAULT_LANGUAGE


def available_languages() -> dict[str, str]:
    """Return ``{code: label}`` for every language that has a table."""
    return {code: LANGUAGE_LABELS.get(code, code) for code in TRANSLATIONS}


def current_language() -> str:
    return _current_language


def set_language(code: str | None) -> str:
    """Select the active language. Unknown codes keep the current language."""
    global _current_language
    if code and code in TRANSLATIONS:
        _current_language = code
    return _current_language


def language_label(code: str | None = None) -> str:
    code = code or _current_language
    return LANGUAGE_LABELS.get(code, code)


def gettext_noop(key: str) -> str:
    """Mark a string for translation without translating it now."""
    return key


def tr(key: str, **kwargs: object) -> str:
    """Translate ``key`` into the active language.

    Falls back to the default language and finally to the key itself, so a
    missing entry can never crash the GUI.
    """
    table = TRANSLATIONS.get(_current_language) or {}
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS.get(DEFAULT_LANGUAGE, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # A malformed placeholder must not break the interface.
            pass
    return text


def tr_text(key: str, **kwargs: object) -> str:
    """Translate a message that is *already* formatted, for use in the log.

    Needed because :mod:`app.translations` uses ``str.format`` placeholders
    while :mod:`logging` uses ``%``-style ones: passing an unformatted
    translation to ``logger.warning(text, *args)`` produces a logging error and
    loses the line, so callers format the text here first.
    """
    return tr(key, **kwargs)


def format_date(value: str | date_cls | None) -> str:
    """Format an ISO date for the active language (e.g. ``2026年9月14日``)."""
    if value is None:
        return ""
    if isinstance(value, date_cls):
        parsed = value
    else:
        try:
            parsed = date_cls.fromisoformat(str(value))
        except (TypeError, ValueError):
            return str(value)
    if _current_language == "zh_CN":
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"
    return parsed.strftime("%d %b %Y")
