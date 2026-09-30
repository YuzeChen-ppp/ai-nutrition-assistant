"""API Key 存储：Windows 凭据管理器优先，.env 作为后备，并且绝不泄漏。"""

from __future__ import annotations

import io
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.utils.config import Settings
from app.utils.credentials import (
    SOURCE_CREDENTIAL,
    SOURCE_ENV_FILE,
    SOURCE_ENV_VAR,
    ApiKeyStore,
    CredentialError,
    EnvFileStore,
    WindowsCredentialStore,
    mask_secret,
)
from app.utils.logging_setup import RedactSecretsFilter

TEST_KEY = "sk-test-1234567890abcdefghij"


class FakeCredentialStore:
    """内存版「凭据管理器」，用于测试，不会碰真实系统凭据。"""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.value: str | None = None
        self.writes = 0
        self.deletes = 0

    def is_available(self) -> bool:
        return self.available

    def get(self) -> str | None:
        if not self.available:
            raise CredentialError("不可用")
        return self.value

    def set(self, secret: str) -> None:
        self.writes += 1
        self.value = secret

    def delete(self) -> None:
        self.deletes += 1
        self.value = None


class EnvFileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / ".env"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_has_no_key(self) -> None:
        self.assertIsNone(EnvFileStore(self.path).get())

    def test_write_and_read(self) -> None:
        store = EnvFileStore(self.path)
        store.set(TEST_KEY)
        self.assertEqual(store.get(), TEST_KEY)
        self.assertIn("DEEPSEEK_API_KEY", self.path.read_text(encoding="utf-8"))

    def test_delete_empties_the_value(self) -> None:
        store = EnvFileStore(self.path)
        store.set(TEST_KEY)
        store.delete()
        self.assertIsNone(store.get())

    def test_other_lines_are_preserved(self) -> None:
        self.path.write_text(
            "# 注释\nDEEPSEEK_MODEL=deepseek-flash\nDEEPSEEK_API_KEY=old\n",
            encoding="utf-8",
        )
        EnvFileStore(self.path).set(TEST_KEY)
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_MODEL=deepseek-flash", content)
        self.assertIn("# 注释", content)
        self.assertNotIn("old", content)

    def test_empty_value_counts_as_missing(self) -> None:
        self.path.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
        self.assertIsNone(EnvFileStore(self.path).get())


class ApiKeyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self._tmp.name) / ".env"
        self.credentials = FakeCredentialStore()
        self.store = ApiKeyStore(
            env_file=self.env_path, credential_store=self.credentials
        )
        self._previous_env = __import__("os").environ.pop("DEEPSEEK_API_KEY", None)

    def tearDown(self) -> None:
        if self._previous_env is not None:
            __import__("os").environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def test_save_uses_the_credential_manager_first(self) -> None:
        stored = self.store.save(TEST_KEY)
        self.assertEqual(stored.source, SOURCE_CREDENTIAL)
        self.assertEqual(self.credentials.value, TEST_KEY)
        self.assertFalse(self.env_path.exists())  # 不会把密钥重复写到磁盘
        self.assertEqual(self.store.load().value, TEST_KEY)
        self.assertEqual(self.store.load().source, SOURCE_CREDENTIAL)

    def test_falls_back_to_env_file_when_unavailable(self) -> None:
        store = ApiKeyStore(
            env_file=self.env_path,
            credential_store=FakeCredentialStore(available=False),
        )
        stored = store.save(TEST_KEY)
        self.assertEqual(stored.source, SOURCE_ENV_FILE)
        self.assertEqual(EnvFileStore(self.env_path).get(), TEST_KEY)
        loaded = store.load()
        self.assertEqual(loaded.value, TEST_KEY)
        self.assertEqual(loaded.source, SOURCE_ENV_FILE)
        self.assertEqual(Path(loaded.location), self.env_path)

    def test_environment_variable_wins(self) -> None:
        import os

        self.store.save(TEST_KEY)
        os.environ["DEEPSEEK_API_KEY"] = "sk-from-environment"
        loaded = self.store.load()
        self.assertEqual(loaded.value, "sk-from-environment")
        self.assertEqual(loaded.source, SOURCE_ENV_VAR)

    def test_delete_removes_everywhere(self) -> None:
        self.store.save(TEST_KEY)
        EnvFileStore(self.env_path).set("sk-second-copy")
        self.store.delete()
        self.assertIsNone(self.credentials.value)
        self.assertIsNone(EnvFileStore(self.env_path).get())
        self.assertEqual(self.store.load().value, "")

    def test_saving_an_empty_key_is_rejected(self) -> None:
        with self.assertRaises(CredentialError):
            self.store.save("   ")

    def test_source_labels_are_chinese(self) -> None:
        stored = self.store.save(TEST_KEY)
        self.assertIn("凭据管理器", stored.source_label())

    def test_mask_never_reveals_the_middle(self) -> None:
        masked = mask_secret(TEST_KEY)
        self.assertNotIn("1234567890", masked)
        self.assertTrue(masked.startswith("sk-t"))
        self.assertTrue(masked.endswith("ghij"))
        self.assertEqual(mask_secret(""), "尚未设置")

    def test_windows_store_reports_availability(self) -> None:
        # 在 Windows 上应该可用；在其它系统上返回 False（不会抛异常）。
        self.assertIsInstance(WindowsCredentialStore().is_available(), bool)


class SettingsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self._tmp.name) / ".env"
        self.credentials = FakeCredentialStore()
        self.store = ApiKeyStore(
            env_file=self.env_path, credential_store=self.credentials
        )
        import os

        self._previous_env = os.environ.pop("DEEPSEEK_API_KEY", None)

    def tearDown(self) -> None:
        import os

        if self._previous_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def test_settings_pick_up_a_saved_key(self) -> None:
        self.store.save(TEST_KEY)
        settings = Settings.from_env(key_store=self.store)
        self.assertTrue(settings.has_api_key)
        self.assertEqual(settings.api_key, TEST_KEY)
        self.assertIn("凭据管理器", settings.api_key_source)
        self.assertNotIn(TEST_KEY, settings.masked_api_key)

    def test_settings_without_a_key(self) -> None:
        settings = Settings.from_env(key_store=self.store)
        self.assertFalse(settings.has_api_key)
        self.assertEqual(settings.masked_api_key, "尚未设置")


class LeakTests(unittest.TestCase):
    def test_logs_never_contain_the_api_key(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactSecretsFilter())
        logger = logging.getLogger("nutrition.leak-test")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            logger.info("sending request with %s", TEST_KEY)
            logger.info('Authorization: Bearer %s', TEST_KEY)
        finally:
            logger.removeHandler(handler)
        output = stream.getvalue()
        self.assertNotIn(TEST_KEY, output)
        self.assertIn("***", output)

    def test_database_never_contains_the_api_key(self) -> None:
        from app.database.database import Database
        from app.database.models import UserProfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.db"
            db = Database(path)
            db.initialize()
            db.save_profile(
                UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
            )
            db.add_meal(
                [{"name": "鸡蛋", "calories_kcal": 143, "protein_g": 12.6}],
                meal_type="breakfast",
            )
            db.set_app_setting("language", "zh_CN")
            db.close()

            raw = path.read_bytes()
            self.assertNotIn(TEST_KEY.encode(), raw)
            connection = sqlite3.connect(path)
            try:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                    )
                ]
                for table in tables:
                    for row in connection.execute(f"SELECT * FROM {table}"):
                        for value in row:
                            if isinstance(value, str):
                                self.assertNotIn(TEST_KEY, value)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
