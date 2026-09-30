"""DeepSeek client behaviour: request payload, error translation, key handling.

No network access happens here: a fake SDK object records the keyword arguments
that would have been sent to the API.
"""

from __future__ import annotations

import json
import types
import unittest

from app.ai.deepseek_client import DeepSeekClient, describe_api_error
from app.ai.model_catalog import THINKING_MAX_TOKENS_FLOOR
from app.ai.errors import (
    AINetworkError,
    AIResponseError,
    AIServiceError,
    AITimeoutError,
    MissingAPIKeyError,
    OnlineReferenceTooSmallError,
    OnlineReferenceUnavailableError,
)
from app.i18n import tr
from app.utils.config import Settings
from app.utils.images import prepare_image_for_api


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 120
    completion_tokens = 80
    total_tokens = 200


class _Response:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage()


class _Completions:
    def __init__(self, owner: "FakeSdk") -> None:
        self.owner = owner

    def create(self, **kwargs):
        self.owner.requests.append(kwargs)
        if self.owner.raise_error is not None:
            raise self.owner.raise_error
        return _Response(self.owner.reply, self.owner.finish_reason)


class _Models:
    def __init__(self, owner: "FakeSdk") -> None:
        self.owner = owner

    def list(self):
        if self.owner.raise_error is not None:
            raise self.owner.raise_error
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(id="deepseek-flash"), types.SimpleNamespace(id="deepseek-v4-pro")]
        )


class FakeSdk:
    """Minimal stand-in for the ``openai.OpenAI`` client."""

    def __init__(self, reply: str = '{"foods":[{"name":"egg","calories_kcal":143}]}') -> None:
        self.requests: list[dict] = []
        self.reply = reply
        self.finish_reason = "stop"
        self.raise_error: Exception | None = None
        self.chat = types.SimpleNamespace(completions=_Completions(self))
        self.models = _Models(self)


TEXT_REPLY = (
    '{"foods":[{"name":"egg","amount":2,"unit":"pieces","estimated_weight_g":100,'
    '"calories_kcal":143,"protein_g":12.6,"carbs_g":0.7,"fat_g":9.5,"sugar_g":0.4,'
    '"fibre_g":0,"sodium_mg":142,"confidence":0.9}],"note":""}'
)


class ClientRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk(TEXT_REPLY)
        self.settings = Settings(api_key="sk-test", model="deepseek-flash", thinking="disabled")
        self.client = DeepSeekClient(self.settings, sdk_client=self.sdk)

    def test_text_analysis_request_shape(self) -> None:
        result = self.client.analyze_text("两个鸡蛋")
        self.assertEqual(len(result.foods), 1)

        request = self.sdk.requests[0]
        self.assertEqual(request["model"], "deepseek-flash")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertGreater(request["max_tokens"], 0)
        self.assertEqual(request["messages"][1], {"role": "user", "content": "两个鸡蛋"})
        system_prompt = request["messages"][0]["content"]
        self.assertIn("json", system_prompt.lower())
        self.assertIn("estimated_weight_g", system_prompt)
        self.assertEqual(self.client.last_usage["total_tokens"], 200)

    def test_empty_description_is_rejected_without_calling_the_api(self) -> None:
        with self.assertRaises(AIResponseError):
            self.client.analyze_text("   ")
        self.assertEqual(self.sdk.requests, [])

    def test_image_request_uses_a_data_url_content_block(self) -> None:
        from PIL import Image
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meal.jpg"
            Image.new("RGB", (400, 300), (10, 10, 10)).save(path)
            prepared = prepare_image_for_api(path)

        self.sdk.reply = (
            '{"foods":[{"name":"beef fried rice","estimated_weight_g":350,'
            '"calories_kcal":620,"protein_g":24,"carbs_g":75,"fat_g":24,"sugar_g":3,'
            '"fibre_g":3,"sodium_mg":900,"confidence":0.65}],"note":"estimates"}'
        )
        result = self.client.analyze_image(prepared, note="with olive oil")
        self.assertEqual(result.foods[0].name, "beef fried rice")

        content = self.sdk.requests[0]["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("olive oil", content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[1]["image_url"]["detail"], "high")

    def test_recommendation_sends_json_context(self) -> None:
        self.sdk.reply = (
            '{"title":"Chicken bowl","foods":[{"name":"chicken breast","weight_g":150,'
            '"calories_kcal":248,"protein_g":46.5,"carbs_g":0,"fat_g":5.4}],'
            '"shopping_list":[{"name":"chicken breast","quantity":"500 g","difficulty":"easy"}],'
            '"difficulty":"easy",'
            '"reason":"protein is behind target","tips":"add vegetables"}'
        )
        context = {"goal": "lose", "remaining": {"calories_kcal": 700.0}}
        recommendation = self.client.recommend_next_meal(context)
        self.assertEqual(recommendation.title, "Chicken bowl")

        payload = self.sdk.requests[0]["messages"][1]["content"]
        self.assertEqual(json.loads(payload), context)
        system_prompt = self.sdk.requests[0]["messages"][0]["content"]
        self.assertIn("json", system_prompt.lower())
        # 推荐必须使用「按购买条件」的 prompt
        self.assertIn("easy_to_buy_ingredients", system_prompt)
        self.assertIn("inconvenient_ingredients", system_prompt)
        self.assertIn("PRIORITY ORDER", system_prompt)
        self.assertEqual(recommendation.difficulty, "easy")
        self.assertEqual(recommendation.shopping_names(), ["chicken breast"])

    def test_missing_key_raises_before_any_request(self) -> None:
        client = DeepSeekClient(Settings(api_key=""), sdk_client=None)
        with self.assertRaises(MissingAPIKeyError):
            client.check_connection()

    def test_connection_check_lists_models(self) -> None:
        message = self.client.check_connection()
        self.assertIn("deepseek-flash", message)
        self.assertIn("已连接", message)

    def test_empty_answer_is_retried_then_reported(self) -> None:
        self.sdk.reply = ""
        with self.assertRaises(AIResponseError):
            self.client.analyze_text("两个鸡蛋")
        self.assertEqual(len(self.sdk.requests), 2)  # one retry


class ReasoningEffortPayloadTests(unittest.TestCase):
    """思考开关与思考强度是怎么进到请求体里的。

    DeepSeek 的服务端字段：``extra_body={"thinking": {"type": ...}}`` 负责开关，
    顶层 ``reasoning_effort`` 负责强度。两者必须配套：关掉思考还发强度是自相矛盾的。
    """

    def setUp(self) -> None:
        self.sdk = FakeSdk(TEXT_REPLY)

    def _client(self, **overrides) -> DeepSeekClient:
        values = {
            "api_key": "sk-test",
            "model": "deepseek-flash",
            "thinking": "disabled",
            "reasoning_effort": "medium",
        }
        values.update(overrides)
        return DeepSeekClient(Settings(**values), sdk_client=self.sdk)

    def _request(self, client: DeepSeekClient) -> dict:
        client.analyze_text("rice")
        self.assertEqual(len(self.sdk.requests), 1)
        return self.sdk.requests[0]

    def test_thinking_off_sends_no_effort(self) -> None:
        request = self._request(self._client(thinking="disabled", reasoning_effort="high"))
        self.assertNotIn("reasoning_effort", request)
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})

    def test_thinking_on_sends_the_mapped_effort(self) -> None:
        request = self._request(self._client(thinking="enabled", reasoning_effort="high"))
        self.assertEqual(request["reasoning_effort"], "max")
        self.assertEqual(request["extra_body"], {"thinking": {"type": "enabled"}})

    def test_medium_effort_is_passed_through(self) -> None:
        request = self._request(self._client(thinking="enabled", reasoning_effort="medium"))
        self.assertEqual(request["reasoning_effort"], "medium")

    def test_unknown_effort_falls_back_to_the_default(self) -> None:
        request = self._request(self._client(thinking="enabled", reasoning_effort="ultra"))
        self.assertEqual(request["reasoning_effort"], "medium")

    def test_model_without_thinking_support_sends_neither_parameter(self) -> None:
        # 例如用户把模型名改成 gpt-4o：宁可什么都不发，也不要发一对无效参数让服务端报 400。
        request = self._request(self._client(model="gpt-4o", thinking="enabled"))
        self.assertNotIn("reasoning_effort", request)
        self.assertNotIn("extra_body", request)

    def test_thinking_on_raises_the_output_budget(self) -> None:
        request = self._request(self._client(thinking="enabled", max_tokens=2048))
        self.assertGreaterEqual(request["max_tokens"], THINKING_MAX_TOKENS_FLOOR)

    def test_thinking_off_keeps_the_configured_budget(self) -> None:
        request = self._request(self._client(thinking="disabled", max_tokens=2048))
        self.assertEqual(request["max_tokens"], 2048)

    def test_truncated_answer_is_reported_without_a_retry(self) -> None:
        # 推理占满输出预算时正文可能为空；重试只会再烧一次钱，必须直接报错。
        self.sdk.reply = ""
        self.sdk.finish_reason = "length"
        client = self._client(thinking="enabled", reasoning_effort="high")

        with self.assertRaises(AIResponseError) as ctx:
            client.analyze_text("rice")

        self.assertEqual(len(self.sdk.requests), 1)
        self.assertEqual(ctx.exception.user_message, tr("error.truncated"))

    def test_connection_check_pings_the_selected_model(self) -> None:
        # models.list() 不校验模型名，所以要再发一条极小的 chat 请求。
        message = self._client().check_connection()

        self.assertEqual(len(self.sdk.requests), 1)
        ping = self.sdk.requests[0]
        self.assertEqual(ping["model"], "deepseek-flash")
        self.assertLessEqual(ping["max_tokens"], 64)
        self.assertEqual(ping["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertIn("deepseek-flash", message)


class OnlineReferenceRequestTests(unittest.TestCase):
    """在线参考的请求形状与失败路径（第二参考，不写库）。"""

    REFERENCE_REPLY = (
        '{"name":"cooked white rice","basis":"per_100g","calories_kcal":130,'
        '"protein_g":2.7,"carbs_g":28.2,"fat_g":0.3,"sugar_g":null,"fibre_g":0.4,'
        '"sodium_mg":1,"confidence":0.8,"note":""}'
    )

    def setUp(self) -> None:
        self.sdk = FakeSdk(self.REFERENCE_REPLY)
        self.client = DeepSeekClient(
            Settings(api_key="sk-test", model="deepseek-flash", thinking="disabled"),
            sdk_client=self.sdk,
        )

    def test_request_shape(self) -> None:
        reference = self.client.fetch_nutrition_reference("cooked white rice")

        self.assertEqual(len(self.sdk.requests), 1)
        request = self.sdk.requests[0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["messages"][1], {"role": "user", "content": "cooked white rice"})
        self.assertLessEqual(request["max_tokens"], 1024)
        system_prompt = request["messages"][0]["content"]
        self.assertIn("json", system_prompt.lower())
        self.assertIn("per 100 g", system_prompt.lower())
        self.assertIn("null", system_prompt)
        # 在线值永远是每 100 g
        self.assertEqual(reference.basis, "per_100g")
        self.assertAlmostEqual(reference.calories_kcal, 130.0)
        self.assertEqual(reference.unknown_fields(), ("sugar_g",))

    def test_output_language_is_a_parameter(self) -> None:
        self.client.fetch_nutrition_reference("rice", language="en")
        english = self.sdk.requests[0]["messages"][0]["content"]
        self.client.fetch_nutrition_reference("rice", language="zh_CN")
        chinese = self.sdk.requests[1]["messages"][0]["content"]

        self.assertIn("in English.", english)
        self.assertIn("简体中文", chinese)
        self.assertNotEqual(english, chinese)

    def test_too_short_names_are_rejected_without_a_request(self) -> None:
        for name in ("", " ", "a", "1"):
            with self.subTest(name=name):
                with self.assertRaises(OnlineReferenceTooSmallError):
                    self.client.fetch_nutrition_reference(name)
        self.assertEqual(self.sdk.requests, [])

    def test_unusable_answers_raise_the_hide_signal(self) -> None:
        for reply in ("", "not json at all", "null", "42", "{}"):
            with self.subTest(reply=reply):
                self.sdk.reply = reply
                self.sdk.requests.clear()
                with self.assertRaises(OnlineReferenceUnavailableError):
                    self.client.fetch_nutrition_reference("rice")
                # 非法响应不重试：再问一次只会再花一次钱
                self.assertEqual(len(self.sdk.requests), 1)
        self.sdk.reply = self.REFERENCE_REPLY

    def test_transport_errors_are_translated(self) -> None:
        class APITimeoutError(Exception):
            pass

        self.sdk.raise_error = APITimeoutError("slow")
        with self.assertRaises(AITimeoutError):
            self.client.fetch_nutrition_reference("rice")


class ErrorTranslationTests(unittest.TestCase):
    def _client_with_error(self, error: Exception) -> DeepSeekClient:
        sdk = FakeSdk()
        sdk.raise_error = error
        return DeepSeekClient(Settings(api_key="sk-test"), sdk_client=sdk)

    def test_timeout(self) -> None:
        class APITimeoutError(Exception):
            pass

        with self.assertRaises(AITimeoutError):
            self._client_with_error(APITimeoutError("slow")).check_connection()

    def test_connection_error(self) -> None:
        class APIConnectionError(Exception):
            pass

        with self.assertRaises(AINetworkError) as ctx:
            self._client_with_error(APIConnectionError("offline")).check_connection()
        self.assertIn("网络连接", ctx.exception.user_message)

    def test_authentication_error(self) -> None:
        class AuthenticationError(Exception):
            status_code = 401

        with self.assertRaises(MissingAPIKeyError):
            self._client_with_error(AuthenticationError("bad key")).check_connection()

    def test_rate_limit_and_insufficient_balance(self) -> None:
        class RateLimitError(Exception):
            status_code = 429

        class BalanceError(Exception):
            status_code = 402

        with self.assertRaises(AIServiceError) as ctx:
            self._client_with_error(RateLimitError("slow down")).check_connection()
        self.assertIn("频繁", ctx.exception.user_message)

        with self.assertRaises(AIServiceError) as ctx:
            self._client_with_error(BalanceError("no money")).check_connection()
        self.assertIn("余额", ctx.exception.user_message)

    def test_permission_denied(self) -> None:
        class PermissionDeniedError(Exception):
            status_code = 403

        with self.assertRaises(AIServiceError) as ctx:
            self._client_with_error(PermissionDeniedError("denied")).check_connection()
        self.assertIn("权限", ctx.exception.user_message)

    def test_server_error(self) -> None:
        class InternalServerError(Exception):
            status_code = 503

        with self.assertRaises(AIServiceError) as ctx:
            self._client_with_error(InternalServerError("down")).check_connection()
        self.assertIn("不可用", ctx.exception.user_message)

    def test_describe_api_error_never_leaks_a_traceback(self) -> None:
        message = describe_api_error(ValueError("some internal detail"))
        self.assertNotIn("ValueError", message)
        self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
