"""模型能力表与思考强度档位。

纯逻辑模块：不导入 Qt、不发网络请求，因此可以单独做单元测试。这里的
DeepSeek 事实来自官方文档 https://api-docs.deepseek.com/guides/thinking_mode

* ``thinking`` 开关........ ``{"type": "enabled"}`` / ``{"type": "disabled"}``
* ``reasoning_effort``..... 服务端接受 low/medium/high/... 但把 medium 映射成
  high、把 minimal 映射成 low。所以界面只提供三档，并且映射到三个**真实不同**
  的服务端强度 low / medium / max。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Capability",
    "DEFAULT_REASONING_EFFORT",
    "EFFORT_TO_API",
    "KNOWN_MODELS",
    "REASONING_EFFORTS",
    "THINKING_MAX_TOKENS_FLOOR",
    "api_effort",
    "capability_for",
    "normalise_effort",
]

# 界面上的三档标签 -> 真正发给 API 的值。
# DeepSeek 把 ``medium`` 映射为 high、``high`` 映射为 high，因此“高”档必须
# 用 ``max`` 才能和“中”档拉开差距。
DEFAULT_REASONING_EFFORT = "medium"
REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high")
EFFORT_TO_API: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "max",
}

KNOWN_MODELS: tuple[str, ...] = (
    "deepseek-flash",
    "deepseek-v4-pro",
)

# 开启思考后，推理 token 也算进 ``max_tokens``。默认的 2048 很容易被推理吃光、
# 导致最终正文为空，所以开启思考时把输出预算抬到不低于这个值。
THINKING_MAX_TOKENS_FLOOR = 4096


@dataclass(frozen=True, slots=True)
class Capability:
    """某个模型支持什么。"""

    supports_thinking: bool
    supports_vision: bool


_UNKNOWN_CAPABILITY = Capability(supports_thinking=False, supports_vision=False)

_MODEL_TABLE: dict[str, Capability] = {
    "deepseek-flash": Capability(supports_thinking=True, supports_vision=True),
    "deepseek-v4-pro": Capability(supports_thinking=True, supports_vision=False),
}

# DeepSeek 的模型家族都支持思考模式；前缀匹配让新模型不必改代码。
# 未知供应商的模型（gpt-4o、claude-*、...）保守地当作不支持。
_THINKING_MODEL_PREFIX = "deepseek-"


def capability_for(model: str) -> Capability:
    """返回 ``model`` 的能力；未知模型一律按“不支持”处理。"""
    name = (model or "").strip().lower()
    if not name:
        return _UNKNOWN_CAPABILITY
    known = _MODEL_TABLE.get(name)
    if known is not None:
        return known
    if name.startswith(_THINKING_MODEL_PREFIX):
        # 新的 DeepSeek 模型假定支持思考，但不假定支持图片。
        return Capability(supports_thinking=True, supports_vision=False)
    return _UNKNOWN_CAPABILITY


def normalise_effort(value: str | None) -> str:
    """把任意输入收敛到三档之一，无法识别时回落到默认档。"""
    text = (value or "").strip().lower()
    return text if text in EFFORT_TO_API else DEFAULT_REASONING_EFFORT


def api_effort(value: str | None) -> str:
    """返回应该放进请求体的 ``reasoning_effort`` 取值。"""
    return EFFORT_TO_API[normalise_effort(value)]
