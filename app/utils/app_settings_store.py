"""用户在「设置 → AI 模型设置」里改的那几项，存在哪里、谁说了算。

存储位置是 SQLite 的 ``app_settings`` 键值表（和界面语言用的是同一张表），
不是 ``.env``：``.env`` 是给高级用户/CI 用的，界面改一次就重写它是很粗鲁的。
API Key 不在这里，它只进 Windows 凭据管理器。

优先级（高到低）::

    界面保存的值  >  环境变量  >  内置默认值

设置 ``NUTRITION_ENV_WINS=1`` 时翻转成“环境变量说了算”，界面保存的值被完全
忽略——给 CI 和脚本用，默认关闭。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlsplit

from app.ai.model_catalog import DEFAULT_REASONING_EFFORT, normalise_effort
from app.i18n import tr
from app.utils.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    Settings,
    env_overrides_settings,
    normalise_thinking,
)
from app.utils.validation import ValidationError

__all__ = [
    "AiSettingsStore",
    "StoredAiSettings",
    "normalise_base_url",
]

KEY_BASE_URL = "ai.base_url"
KEY_MODEL = "ai.model"
KEY_THINKING = "ai.thinking"
KEY_REASONING_EFFORT = "ai.reasoning_effort"

ALL_KEYS: tuple[str, ...] = (KEY_BASE_URL, KEY_MODEL, KEY_THINKING, KEY_REASONING_EFFORT)


class SettingsTable(Protocol):
    """``Database`` 里我们用到的那两个方法（方便测试注入假对象）。"""

    def get_app_setting(self, key: str, default: str | None = None) -> str | None: ...

    def set_app_setting(self, key: str, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredAiSettings:
    """已经归一化过的、用户保存的设置；``None`` 表示“没存过”。"""

    base_url: str | None = None
    model: str | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None


def normalise_base_url(value: str | None) -> str:
    """空值回落到官方地址；必须是 http(s) 且带主机名，否则报错。"""
    text = (value or "").strip()
    if not text:
        return DEFAULT_BASE_URL
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValidationError(tr("error.base_url_invalid"))
    return text.rstrip("/")


class AiSettingsStore:
    """读写「AI 模型设置」这一组键值。"""

    def __init__(self, database: SettingsTable) -> None:
        self._db = database

    # ------------------------------------------------------------------ 读
    def load(self) -> StoredAiSettings:
        def read(key: str) -> str | None:
            value = self._db.get_app_setting(key)
            text = (value or "").strip()
            return text or None

        return StoredAiSettings(
            base_url=read(KEY_BASE_URL),
            model=read(KEY_MODEL),
            thinking=read(KEY_THINKING),
            reasoning_effort=read(KEY_REASONING_EFFORT),
        )

    def apply(self, settings: Settings) -> Settings:
        """把已保存的值叠加到 ``settings`` 上（见模块开头的优先级）。"""
        if env_overrides_settings():
            return settings
        stored = self.load()
        return replace(
            settings,
            base_url=stored.base_url or settings.base_url,
            model=stored.model or settings.model,
            thinking=stored.thinking or settings.thinking,
            reasoning_effort=stored.reasoning_effort or settings.reasoning_effort,
        )

    # ------------------------------------------------------------------ 写
    def save(
        self,
        *,
        base_url: str | None,
        model: str | None,
        thinking: str | None,
        reasoning_effort: str | None,
    ) -> StoredAiSettings:
        """校验 + 归一化后写库；非法输入会抛 :class:`ValidationError`。"""
        values = StoredAiSettings(
            base_url=normalise_base_url(base_url),
            model=(model or "").strip() or DEFAULT_MODEL,
            thinking=normalise_thinking(thinking),
            reasoning_effort=normalise_effort(reasoning_effort or DEFAULT_REASONING_EFFORT),
        )
        for key, value in (
            (KEY_BASE_URL, values.base_url),
            (KEY_MODEL, values.model),
            (KEY_THINKING, values.thinking),
            (KEY_REASONING_EFFORT, values.reasoning_effort),
        ):
            self._db.set_app_setting(key, value or "")
        return values

    def clear(self) -> None:
        """重置为默认：删除已保存的值，环境变量与默认值随即生效。"""
        for key in ALL_KEYS:
            self._db.set_app_setting(key, "")
