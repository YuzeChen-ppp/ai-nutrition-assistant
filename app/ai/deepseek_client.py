"""DeepSeek API client.

Facts verified against the official DeepSeek API documentation
(https://api-docs.deepseek.com):

* Base URL ......... ``https://api.deepseek.com`` (OpenAI-compatible)
* Model identifier  ``deepseek-flash``  == "DeepSeek V4.1 Flash"
* Vision .......... supported natively for the flash model
* JSON output ..... ``response_format={"type": "json_object"}``
* Thinking mode ... enabled by default; we disable it for speed and cost.

DeepSeek documents the OpenAI Python SDK as the supported way to call the API,
so this module uses it (the SDK is only imported when a request is made, which
keeps the app importable without a key).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from app.ai.errors import (
    AIError,
    AINetworkError,
    AIResponseError,
    AIServiceError,
    AITimeoutError,
    MissingAPIKeyError,
    OnlineReferenceTooSmallError,
    OnlineReferenceUnavailableError,
)
from app.ai.model_catalog import (
    THINKING_MAX_TOKENS_FLOOR,
    api_effort,
    capability_for,
)
from app.ai.parser import (
    parse_food_analysis,
    parse_online_nutrition,
    parse_recommendation,
)
from app.ai.prompts import (
    FOOD_IMAGE_ANALYSIS_PROMPT,
    FOOD_TEXT_ANALYSIS_PROMPT,
    PURCHASABLE_INGREDIENT_MEAL_PROMPT,
    online_nutrition_reference_prompt,
)
from app.ai.schemas import FoodAnalysisResult, MealRecommendation, OnlineNutritionReference
from app.i18n import current_language, tr
from app.utils.config import Settings
from app.utils.images import PreparedImage
from app.utils.logging_setup import get_logger

__all__ = ["DeepSeekClient", "describe_api_error"]

_logger = get_logger("ai")

# 连接测试用的最小请求：只验证“这个模型名能不能用”，不产生有用输出。
_PING_MAX_TOKENS = 16

# 在线参考的答案只有 7 个数字和一个短备注，几百 token 足够。
_REFERENCE_MAX_TOKENS = 600

# 比这更短的名字无法指向一种食物（「a」、「1」），直接当作不可用处理。
_MIN_REFERENCE_NAME_LENGTH = 2


class DeepSeekClient:
    """Thin, well-behaved wrapper around the DeepSeek chat completions API."""

    def __init__(self, settings: Settings, *, sdk_client: Any | None = None) -> None:
        self.settings = settings
        self._client = sdk_client
        self._last_usage: dict[str, int] = {}

    # ------------------------------------------------------------------ setup
    @property
    def last_usage(self) -> dict[str, int]:
        """Token usage of the most recent request (empty when unavailable)."""
        return dict(self._last_usage)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.has_api_key:
            raise MissingAPIKeyError()
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is required
            raise AIServiceError(
                tr("error.openai_package_missing"),
                detail=str(exc),
            ) from exc
        self._client = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            max_retries=2,
        )
        return self._client

    # ---------------------------------------------------- 请求参数（思考模式）
    def _thinking_enabled(self) -> bool:
        """思考模式是否真的会生效：模型支持它，而且用户把它打开了。"""
        return (
            capability_for(self.settings.model).supports_thinking
            and self.settings.thinking == "enabled"
        )

    def _request_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """组装一次 chat 请求的参数，重点是思考参数的注入规则。

        DeepSeek 把“开不开思考”和“思考多用力”拆成了两个独立字段
        （见官方文档 /guides/thinking_mode）：

        * ``extra_body={"thinking": {"type": "enabled"|"disabled"}}`` —— 思考模式开关。
          服务端默认是**开启**，所以想省钱的客户端必须显式传 ``disabled``，
          不能靠“不传”来表达关闭。
        * 顶层 ``reasoning_effort`` —— 思考强度。它只在思考模式开启时才有意义；
          关掉思考还传强度是自相矛盾的（服务端会忽略），所以我们干脆不发，
          免得日志和计费都让人误解。

        模型不支持思考时（例如用户手输 ``gpt-4o``），这两个参数一个都不发：
        服务端不认识 ``thinking`` 字段时会直接 400，而我们没必要冒这个险。
        """
        capability = capability_for(self.settings.model)
        thinking_enabled = self._thinking_enabled()

        # 思考模式下推理 token 也计入 max_tokens，预算太小会导致正文被挤空。
        budget = max_tokens or self.settings.max_tokens
        if thinking_enabled:
            budget = max(budget, THINKING_MAX_TOKENS_FLOOR)

        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": budget,
            "timeout": self.settings.timeout,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if capability.supports_thinking:
            kwargs["extra_body"] = {"thinking": {"type": self.settings.thinking}}
            if thinking_enabled:
                # 界面上的 low/medium/high -> 服务端真实强度 low/medium/max。
                kwargs["reasoning_effort"] = api_effort(self.settings.reasoning_effort)
        return kwargs

    # --------------------------------------------------------------- requests
    def _chat_json(
        self,
        *,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
        max_tokens: int | None = None,
        attempts: int = 2,
    ) -> str:
        """Send one chat request and return the raw text answer."""
        client = self._get_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        capability = capability_for(self.settings.model)
        thinking_enabled = self._thinking_enabled()
        last_error: AIError | None = None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = client.chat.completions.create(
                    **self._request_kwargs(
                        messages=messages,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                    )
                )
            except Exception as exc:  # noqa: BLE001 - translated below
                raise _translate_error(exc) from exc

            elapsed = time.perf_counter() - started
            content = _message_content(response)
            self._last_usage = _usage_dict(response)
            _logger.info(
                "DeepSeek request ok in %.1fs (model=%s, thinking=%s, tokens=%s)",
                elapsed,
                self.settings.model,
                "on" if thinking_enabled else "off",
                self._last_usage or "n/a",
            )
            if content and content.strip():
                return content
            if _finish_reason(response) == "length":
                # 推理 token 也算输出：预算被推理吃光时正文是空的，重试只是再花一次钱。
                _logger.warning(
                    "Answer truncated at max_tokens (model=%s, thinking=%s)",
                    self.settings.model,
                    "on" if thinking_enabled else "off",
                )
                raise AIResponseError(
                    tr("error.truncated"),
                    detail="finish_reason=length and the message content was empty",
                )
            # JSON mode occasionally returns empty content; retry once.
            last_error = AIResponseError(
                detail="the model returned an empty answer",
            )
            _logger.warning("Empty answer from DeepSeek (attempt %s/%s)", attempt, attempts)

        raise last_error or AIResponseError(detail="empty answer")

    def analyze_text(self, description: str) -> FoodAnalysisResult:
        """Turn a text description into structured food data."""
        text = (description or "").strip()
        if not text:
            raise AIResponseError(tr("error.empty_input"))
        raw = self._chat_json(
            system_prompt=FOOD_TEXT_ANALYSIS_PROMPT,
            user_content=text,
            max_tokens=min(self.settings.max_tokens, 2048),
        )
        result = parse_food_analysis(raw)
        _logger.info("Text analysis returned %s food item(s)", len(result.foods))
        return result

    def analyze_image(
        self,
        prepared: PreparedImage,
        *,
        note: str = "",
    ) -> FoodAnalysisResult:
        """Analyse a prepared (resized) food photo."""
        prompt_text = (
            "Analyse this food photo"
            + (" and this extra information from the user: " + note.strip() if note.strip() else ".")
        )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt_text},
            {
                "type": "image_url",
                "image_url": {"url": prepared.data_url, "detail": "high"},
            },
        ]
        raw = self._chat_json(
            system_prompt=FOOD_IMAGE_ANALYSIS_PROMPT,
            user_content=content,
            max_tokens=min(self.settings.max_tokens, 2048),
        )
        result = parse_food_analysis(raw)
        _logger.info("Image analysis returned %s food item(s)", len(result.foods))
        return result

    def recommend_next_meal(self, context: Mapping[str, Any]) -> MealRecommendation:
        """按用户的购买条件推荐下一餐（同时兼顾当日营养目标）。"""
        payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        raw = self._chat_json(
            system_prompt=PURCHASABLE_INGREDIENT_MEAL_PROMPT,
            user_content=payload,
            max_tokens=min(self.settings.max_tokens, 3000),
        )
        recommendation = parse_recommendation(raw)
        _logger.info(
            "推荐结果：%s（%s 项食材，购买难度=%s，需要购买 %s 项）",
            recommendation.title,
            len(recommendation.foods),
            recommendation.difficulty or "-",
            len(recommendation.shopping_list),
        )
        return recommendation

    def fetch_nutrition_reference(
        self,
        food_name: str,
        *,
        language: str | None = None,
    ) -> OnlineNutritionReference:
        """Look up one food as a **second reference** for the confirmation page.

        This is the only method here whose result is never persisted: the answer
        is displayed next to the local reference table and then thrown away. It
        is called from exactly one place - the "查询在线参考" button - so an
        application start, a date change or opening the history never triggers a
        request.
        """
        name = (food_name or "").strip()
        if len(name) < _MIN_REFERENCE_NAME_LENGTH:
            # Nothing identifiable to look up: fail like an offline API instead
            # of spending a request on a single letter.
            raise OnlineReferenceTooSmallError(
                detail=f"food name {name!r} is too short to look up",
            )
        try:
            raw = self._chat_json(
                system_prompt=online_nutrition_reference_prompt(
                    language or current_language()
                ),
                user_content=name,
                max_tokens=_REFERENCE_MAX_TOKENS,
                attempts=1,
            )
        except AIResponseError as exc:
            # "The model answered nothing usable" is the same outcome as "the
            # request failed" for this feature, so callers see one error type.
            raise OnlineReferenceUnavailableError(detail=exc.detail) from exc
        reference = parse_online_nutrition(raw, name=name)
        _logger.info(
            "Online reference for %r: %s/%s nutrient fields present",
            name,
            len(reference.known_fields()),
            len(reference.field_names()),
        )
        return reference

    def check_connection(self) -> str:
        """Verify the key, the base URL and the selected model.

        ``models.list()`` 只能证明“Key + Base URL 通”，它**不校验模型名**，
        所以后面还要对当前模型发一条极小的 ping 请求。ping 强制关闭思考，
        避免连接测试本身又慢又贵。
        """
        client = self._get_client()
        try:
            models = client.models.list()
        except Exception as exc:  # noqa: BLE001 - translated below
            raise _translate_error(exc) from exc
        ids = [getattr(item, "id", "") for item in getattr(models, "data", []) or []]
        ids = [model_id for model_id in ids if model_id]
        self._ping_model(client)
        if not ids:
            return tr("settings.connection_no_models")
        note = ""
        if self.settings.model not in ids:
            note = tr("settings.connection_model_missing", model=self.settings.model)
        return tr("settings.connection_ok", models=", ".join(ids)) + note

    def _ping_model(self, client: Any) -> None:
        """对当前模型发一条最小请求；失败时抛出可直接展示的错误。"""
        request: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": _PING_MAX_TOKENS,
            "timeout": self.settings.timeout,
        }
        if capability_for(self.settings.model).supports_thinking:
            request["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001 - translated below
            raise _translate_error(exc) from exc


def describe_api_error(error: BaseException) -> str:
    """Return a human-readable message for any exception."""
    if isinstance(error, AIError):
        return error.user_message
    return AIServiceError.default_message()


def _message_content(response: Any) -> str:
    try:
        choice = response.choices[0]
        message = choice.message
    except (AttributeError, IndexError, TypeError):
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):  # Responses API style content parts
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "".join(parts)
    return ""


def _finish_reason(response: Any) -> str:
    """``"length"`` means the answer was cut off by ``max_tokens``."""
    try:
        choice = response.choices[0]
    except (AttributeError, IndexError, TypeError):
        return ""
    value = getattr(choice, "finish_reason", None)
    return value if isinstance(value, str) else ""


def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    data: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            data[key] = value
    return data


def _translate_error(exc: BaseException) -> AIError:
    """Turn an SDK exception into an AIError with a friendly message."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    detail = f"{name}: {exc}"

    if name in {"APITimeoutError", "Timeout", "TimeoutException", "ReadTimeout"}:
        return AITimeoutError(detail=detail)
    if name in {
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "RemoteProtocolError",
        "ProxyError",
    }:
        return AINetworkError(detail=detail)
    if name == "AuthenticationError" or status == 401:
        return MissingAPIKeyError(
            tr("error.api_key_invalid"),
            detail=detail,
        )
    if name == "PermissionDeniedError" or status == 403:
        return AIServiceError(
            tr("error.forbidden"),
            detail=detail,
        )
    if status == 402:
        return AIServiceError(
            tr("error.balance"),
            detail=detail,
        )
    if name == "RateLimitError" or status == 429:
        return AIServiceError(
            tr("error.rate_limit"),
            detail=detail,
        )
    if status in {400, 422}:
        return AIServiceError(
            tr("error.bad_request"),
            detail=detail,
        )
    if status is not None and status >= 500:
        return AIServiceError(
            tr("error.server"),
            detail=detail,
        )
    return AIServiceError(detail=detail)
