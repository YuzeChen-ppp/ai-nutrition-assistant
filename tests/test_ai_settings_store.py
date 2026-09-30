"""Persisting the editable AI settings and applying the right precedence.

Rule: what the user saved in the GUI wins, then the environment variables, then
the built-in defaults - unless ``NUTRITION_ENV_WINS`` is set (CI / scripts).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.context import AppContext
from app.database.database import Database
from app.utils.app_settings_store import AiSettingsStore
from app.utils.config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings
from app.utils.credentials import ApiKeyStore
from app.utils.validation import ValidationError
from tests.test_api_key import FakeCredentialStore


class AiSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "settings.db")
        self.db.initialize()
        self.store = AiSettingsStore(self.db)
        self._previous_flag = os.environ.pop("NUTRITION_ENV_WINS", None)

    def tearDown(self) -> None:
        if self._previous_flag is None:
            os.environ.pop("NUTRITION_ENV_WINS", None)
        else:
            os.environ["NUTRITION_ENV_WINS"] = self._previous_flag
        self.db.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------- defaults
    def test_defaults_are_kept_when_nothing_was_saved(self) -> None:
        settings = self.store.apply(Settings(api_key="sk-test"))
        self.assertEqual(settings.model, DEFAULT_MODEL)
        self.assertEqual(settings.base_url, DEFAULT_BASE_URL)

    def test_saved_values_win_over_the_environment_defaults(self) -> None:
        from_environment = Settings(api_key="sk-test", model="deepseek-v4-pro")

        self.store.save(
            base_url="https://proxy.example.com/v1",
            model="deepseek-flash",
            thinking="enabled",
            reasoning_effort="high",
        )
        settings = self.store.apply(from_environment)

        self.assertEqual(settings.model, "deepseek-flash")
        self.assertEqual(settings.base_url, "https://proxy.example.com/v1")
        self.assertEqual(settings.thinking, "enabled")
        self.assertEqual(settings.reasoning_effort, "high")

    def test_api_key_is_never_touched(self) -> None:
        self.store.save(
            base_url=DEFAULT_BASE_URL,
            model="deepseek-flash",
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertEqual(self.store.apply(Settings(api_key="sk-test")).api_key, "sk-test")

    def test_clear_restores_the_defaults(self) -> None:
        self.store.save(
            base_url="https://proxy.example.com",
            model="deepseek-v4-pro",
            thinking="enabled",
            reasoning_effort="high",
        )
        self.store.clear()

        settings = self.store.apply(Settings(api_key="sk-test"))
        self.assertEqual(settings.model, DEFAULT_MODEL)
        self.assertEqual(settings.base_url, DEFAULT_BASE_URL)
        self.assertEqual(settings.thinking, "disabled")
        self.assertEqual(settings.reasoning_effort, "medium")

    # ---------------------------------------------------- environment override
    def test_environment_wins_when_the_override_flag_is_on(self) -> None:
        os.environ["NUTRITION_ENV_WINS"] = "1"
        from_environment = Settings(
            api_key="sk-test",
            model="deepseek-v4-pro",
            base_url="https://env.example.com",
        )
        self.store.save(
            base_url="https://gui.example.com",
            model="deepseek-flash",
            thinking="enabled",
            reasoning_effort="high",
        )

        settings = self.store.apply(from_environment)

        self.assertEqual(settings.model, "deepseek-v4-pro")
        self.assertEqual(settings.base_url, "https://env.example.com")
        self.assertEqual(settings.thinking, "disabled")

    def test_flag_accepts_the_usual_truthy_spellings(self) -> None:
        for value in ("1", "true", "TRUE", "yes"):
            os.environ["NUTRITION_ENV_WINS"] = value
            self.store.save(
                base_url="https://gui.example.com",
                model="deepseek-flash",
                thinking="disabled",
                reasoning_effort="medium",
            )
            with self.subTest(value=value):
                settings = self.store.apply(Settings(api_key="sk-test", model="deepseek-v4-pro"))
                self.assertEqual(settings.model, "deepseek-v4-pro")

    def test_flag_is_off_by_default(self) -> None:
        self.store.save(
            base_url="https://gui.example.com",
            model="deepseek-flash",
            thinking="disabled",
            reasoning_effort="medium",
        )
        settings = self.store.apply(Settings(api_key="sk-test", model="deepseek-v4-pro"))
        self.assertEqual(settings.model, "deepseek-flash")

    # ----------------------------------------------------------- validation
    def test_blank_base_url_falls_back_to_the_default(self) -> None:
        self.store.save(
            base_url="   ",
            model="deepseek-flash",
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertEqual(self.store.apply(Settings()).base_url, DEFAULT_BASE_URL)

    def test_base_url_without_an_http_scheme_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.save(
                base_url="ftp://example.com",
                model="deepseek-flash",
                thinking="disabled",
                reasoning_effort="medium",
            )

    def test_trailing_slashes_are_removed_from_the_base_url(self) -> None:
        self.store.save(
            base_url="https://proxy.example.com/v1//",
            model="deepseek-flash",
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertEqual(self.store.apply(Settings()).base_url, "https://proxy.example.com/v1")

    def test_blank_model_falls_back_to_the_default(self) -> None:
        self.store.save(
            base_url=DEFAULT_BASE_URL,
            model="  ",
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertEqual(self.store.apply(Settings()).model, DEFAULT_MODEL)

    def test_unknown_effort_is_normalised_instead_of_stored_verbatim(self) -> None:
        self.store.save(
            base_url=DEFAULT_BASE_URL,
            model="deepseek-flash",
            thinking="disabled",
            reasoning_effort="ultra",
        )
        self.assertEqual(self.store.apply(Settings()).reasoning_effort, "medium")

    def test_unknown_thinking_value_falls_back_to_disabled(self) -> None:
        self.store.save(
            base_url=DEFAULT_BASE_URL,
            model="deepseek-flash",
            thinking="maybe",
            reasoning_effort="medium",
        )
        self.assertEqual(self.store.apply(Settings()).thinking, "disabled")

    def test_saved_values_survive_a_new_store_instance(self) -> None:
        # Same database file, new objects: this is what a restart looks like.
        self.store.save(
            base_url="https://proxy.example.com",
            model="deepseek-v4-pro",
            thinking="enabled",
            reasoning_effort="high",
        )
        reopened_db = Database(Path(self._tmp.name) / "settings.db")
        try:
            reopened = AiSettingsStore(reopened_db)
            settings = reopened.apply(Settings(api_key="sk-test"))
            self.assertEqual(settings.model, "deepseek-v4-pro")
            self.assertEqual(settings.reasoning_effort, "high")
        finally:
            reopened_db.close()


class RestartTests(unittest.TestCase):
    """重启程序后界面里保存的值必须生效（用户唯一能感知的那条路径）。"""

    def test_a_restart_keeps_the_saved_model_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "app.db"
            key_store = ApiKeyStore(
                env_file=Path(tmp) / ".env", credential_store=FakeCredentialStore()
            )
            key_store.save("sk-test-key-1234567890")

            first = AppContext.create(database_path=database_path, api_key_store=key_store)
            try:
                first.ai_settings.save(
                    base_url="https://proxy.example.com",
                    model="deepseek-v4-pro",
                    thinking="enabled",
                    reasoning_effort="high",
                )
            finally:
                first.close()

            second = AppContext.create(database_path=database_path, api_key_store=key_store)
            try:
                self.assertEqual(second.settings.model, "deepseek-v4-pro")
                self.assertEqual(second.settings.base_url, "https://proxy.example.com")
                self.assertEqual(second.settings.thinking, "enabled")
                self.assertEqual(second.settings.reasoning_effort, "high")
                self.assertEqual(second.settings.api_key, "sk-test-key-1234567890")
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
