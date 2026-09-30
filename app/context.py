"""Application context: one small object shared by every page.

Holding the database, the settings and the AI client factory in one place
keeps the pages free of global state and makes the whole app testable: tests
can hand in a fake AI client.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.deepseek_client import DeepSeekClient
from app.database.database import Database
from app.utils.app_settings_store import AiSettingsStore
from app.utils.config import Settings, db_path
from app.utils.credentials import ApiKeyStore, default_api_key_store

__all__ = ["AppContext"]


@dataclass
class AppContext:
    """Everything the UI needs to do its job."""

    settings: Settings
    database: Database
    api_key_store: ApiKeyStore = field(default_factory=default_api_key_store)
    api_client_factory: Callable[[Settings], DeepSeekClient] = DeepSeekClient
    ai_settings: AiSettingsStore = field(init=False, repr=False)
    _client: DeepSeekClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.ai_settings = AiSettingsStore(self.database)

    @classmethod
    def create(
        cls,
        *,
        database_path: str | Path | None = None,
        api_key_store: ApiKeyStore | None = None,
    ) -> "AppContext":
        store = api_key_store or default_api_key_store()
        settings = Settings.from_env(key_store=store)
        database = Database(database_path or db_path())
        database.initialize()
        # 界面里保存过的值优先于环境变量（除非开了 NUTRITION_ENV_WINS）。
        settings = AiSettingsStore(database).apply(settings)
        return cls(settings=settings, database=database, api_key_store=store)

    def reload_settings(self) -> Settings:
        """Re-read the settings (API key, model, base URL, thinking...)."""
        self.settings = self.ai_settings.apply(
            Settings.from_env(key_store=self.api_key_store)
        )
        self._client = None  # new settings need a new API client
        return self.settings

    def client(self) -> DeepSeekClient:
        """Return the (lazily created) AI client."""
        if self._client is None:
            self._client = self.api_client_factory(self.settings)
        return self._client

    @property
    def has_api_key(self) -> bool:
        return self.settings.has_api_key

    def close(self) -> None:
        self.database.close()
