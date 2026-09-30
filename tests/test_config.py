"""Configuration helpers: data folders, .env lookup, key masking."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.utils.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    Settings,
    data_dir,
    db_path,
    env_file_candidates,
    log_dir,
    resource_path,
)


class DataFolderTests(unittest.TestCase):
    def test_override_folder_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import os

            previous = os.environ.get("NUTRITION_APP_DATA_DIR")
            os.environ["NUTRITION_APP_DATA_DIR"] = tmp
            try:
                self.assertEqual(data_dir(), Path(tmp))
                self.assertEqual(db_path().name, "nutrition.db")
            finally:
                if previous is None:
                    os.environ.pop("NUTRITION_APP_DATA_DIR", None)
                else:
                    os.environ["NUTRITION_APP_DATA_DIR"] = previous

    def test_unusable_override_falls_back_instead_of_crashing(self) -> None:
        # A file cannot be used as a folder: the app must pick another location
        # rather than refusing to start.
        import os

        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "not-a-folder.txt"
            blocker.write_text("x", encoding="utf-8")
            previous = os.environ.get("NUTRITION_APP_DATA_DIR")
            os.environ["NUTRITION_APP_DATA_DIR"] = str(blocker / "data")
            try:
                folder = data_dir()
            finally:
                if previous is None:
                    os.environ.pop("NUTRITION_APP_DATA_DIR", None)
                else:
                    os.environ["NUTRITION_APP_DATA_DIR"] = previous
        self.assertTrue(folder.is_dir())
        self.assertNotIn("not-a-folder.txt", str(folder))

    def test_log_dir_is_writable(self) -> None:
        folder = log_dir()
        self.assertTrue(folder.is_dir())

    def test_resource_path_finds_the_icon(self) -> None:
        self.assertTrue(resource_path("assets/icon.ico").is_file())


class EnvironmentTests(unittest.TestCase):
    def test_env_candidates_are_paths(self) -> None:
        candidates = env_file_candidates()
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertIsInstance(candidate, Path)

    def test_settings_defaults(self) -> None:
        settings = Settings.from_env()
        self.assertEqual(settings.model, DEFAULT_MODEL)
        self.assertEqual(settings.base_url, DEFAULT_BASE_URL)
        self.assertEqual(settings.thinking, "disabled")
        self.assertGreater(settings.timeout, 0)
        self.assertGreater(settings.max_tokens, 0)

    def test_masked_key_never_reveals_the_key(self) -> None:
        settings = Settings(api_key="sk-abcdefghijklmnop")
        self.assertTrue(settings.has_api_key)
        masked = settings.masked_api_key
        self.assertNotIn("abcdefghij", masked)
        self.assertTrue(masked.startswith("sk-a"))

    def test_empty_key_is_reported_as_missing(self) -> None:
        settings = Settings(api_key="")
        self.assertFalse(settings.has_api_key)
        self.assertEqual(settings.masked_api_key, "尚未设置")


if __name__ == "__main__":
    unittest.main()
