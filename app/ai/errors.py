"""Errors raised by the AI layer.

Every error carries a ``user_message`` that is safe (and useful) to show in the
GUI, plus an optional ``detail`` used for the log file. The GUI never shows a
raw traceback.

Messages are looked up in :mod:`app.translations`, so the whole interface -
including failures - is in the user's language.
"""

from __future__ import annotations

from app.i18n import tr

__all__ = [
    "AIError",
    "AINetworkError",
    "AIResponseError",
    "AIServiceError",
    "AITimeoutError",
    "MissingAPIKeyError",
    "NoFoodRecognisedError",
    "OnlineReferenceUnavailableError",
    "OnlineReferenceTooSmallError",
]


class AIError(Exception):
    """Base class for all AI-related failures."""

    message_key = "error.ai_generic"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        super().__init__(message or tr(self.message_key))
        self.user_message = message or tr(self.message_key)
        self.detail = detail

    @classmethod
    def default_message(cls) -> str:
        return tr(cls.message_key)


class MissingAPIKeyError(AIError):
    message_key = "error.no_api_key"


class AINetworkError(AIError):
    message_key = "error.network"


class AITimeoutError(AIError):
    message_key = "error.timeout"


class AIServiceError(AIError):
    message_key = "error.service"


class AIResponseError(AIError):
    message_key = "error.bad_json"


class NoFoodRecognisedError(AIError):
    message_key = "error.no_food"


class OnlineReferenceUnavailableError(AIResponseError):
    """The online (second-reference) answer was unusable.

    Raised for empty answers, unparsable JSON and schema violations alike. The
    "query online reference" button treats this exactly like an offline network
    failure: the caller hides the online section and logs one warning, so the
    user never has to read a validation message or parse a string to decide what
    to do.
    """

    message_key = "error.online_reference_unavailable"


class OnlineReferenceTooSmallError(OnlineReferenceUnavailableError):
    """The food name is too short to identify a food, so nothing was requested."""

    message_key = "error.online_reference_too_small"
