"""「AI 模型设置」表单 + 主窗口写库行为（offscreen，不联网）。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.ui import main_window as main_window_module  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.model_settings_form import ModelSettingsForm  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.utils.app_settings_store import StoredAiSettings  # noqa: E402
from app.utils.config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings  # noqa: E402
from app.utils.credentials import ApiKeyStore  # noqa: E402
from tests.fakes import FakeDeepSeekClient  # noqa: E402
from tests.test_api_key import FakeCredentialStore  # noqa: E402

_APP: QApplication | None = None
TEST_KEY = "sk-ui-model-settings-key"


def setUpModule() -> None:  # noqa: N802
    global _APP
    _APP = QApplication.instance() or QApplication([])
    apply_app_style(_APP)


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return predicate()


class ModelSettingsFormTests(unittest.TestCase):
    """表单控件本身：联动、降级、以及它吐出来的值。"""

    def setUp(self) -> None:
        self.form = ModelSettingsForm()
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.saved: list[StoredAiSettings] = []
        self.tested: list[StoredAiSettings] = []
        self.resets: list[int] = []
        self.form.save_requested.connect(self.saved.append)
        self.form.test_requested.connect(self.tested.append)
        self.form.reset_requested.connect(lambda: self.resets.append(1))

    def tearDown(self) -> None:
        self.form.close()

    def test_effort_is_disabled_while_thinking_is_off(self) -> None:
        self.assertFalse(self.form.thinking_checkbox.isChecked())
        self.assertFalse(self.form.effort_slider.isEnabled())

    def test_effort_is_enabled_while_thinking_is_on(self) -> None:
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            thinking="enabled",
            reasoning_effort="medium",
        )
        self.assertTrue(self.form.thinking_checkbox.isChecked())
        self.assertTrue(self.form.effort_slider.isEnabled())

    def test_turning_thinking_off_greys_out_the_effort_slider(self) -> None:
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            thinking="enabled",
            reasoning_effort="medium",
        )
        self.form.thinking_checkbox.setChecked(False)
        self.assertFalse(self.form.effort_slider.isEnabled())

    def test_unsupported_model_greys_out_thinking_and_explains_why(self) -> None:
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model="gpt-4o",
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertFalse(self.form.thinking_checkbox.isEnabled())
        self.assertFalse(self.form.effort_slider.isEnabled())
        self.assertIn("不支持", self.form.hint_label.text())

    def test_supported_model_hides_the_unsupported_hint(self) -> None:
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            thinking="disabled",
            reasoning_effort="medium",
        )
        self.assertEqual(self.form.hint_label.text(), "")

    def test_switching_models_degrades_and_restores_the_preference(self) -> None:
        self.form.set_values(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            thinking="enabled",
            reasoning_effort="high",
        )

        self.form.model_combo.setCurrentText("gpt-4o")
        self.assertFalse(self.form.thinking_checkbox.isEnabled())
        self.assertEqual(self.form.values().thinking, "disabled")

        self.form.model_combo.setCurrentText(DEFAULT_MODEL)
        self.assertTrue(self.form.thinking_checkbox.isChecked())
        self.assertEqual(self.form.values().reasoning_effort, "high")

    def test_save_payload_carries_every_edited_field(self) -> None:
        self.form.set_values(
            base_url="https://proxy.example.com/v1",
            model="deepseek-v4-pro",
            thinking="enabled",
            reasoning_effort="high",
        )
        self.form.save_button.click()

        self.assertEqual(len(self.saved), 1)
        values = self.saved[0]
        self.assertEqual(values.base_url, "https://proxy.example.com/v1")
        self.assertEqual(values.model, "deepseek-v4-pro")
        self.assertEqual(values.thinking, "enabled")
        self.assertEqual(values.reasoning_effort, "high")

    def test_a_typed_model_name_is_used(self) -> None:
        self.form.model_combo.setCurrentText("deepseek-v9-preview")
        self.form.save_button.click()
        self.assertEqual(self.saved[0].model, "deepseek-v9-preview")

    def test_test_button_sends_the_current_form_values(self) -> None:
        self.form.base_url_edit.setText("https://proxy.example.com")
        self.form.test_button.click()
        self.assertEqual(self.tested[0].base_url, "https://proxy.example.com")

    def test_reset_button_emits_a_reset_request(self) -> None:
        self.form.reset_button.click()
        self.assertEqual(self.resets, [1])

    def test_status_and_busy_are_visible_feedback(self) -> None:
        self.form.set_status("saved", ok=True)
        self.assertEqual(self.form.status_label.text(), "saved")
        self.form.set_busy(True, "testing")
        self.assertFalse(self.form.save_button.isEnabled())
        self.form.set_busy(False)
        self.assertTrue(self.form.save_button.isEnabled())


class AiSettingsWindowTests(unittest.TestCase):
    """主窗口：保存 / 测试 / 重置分别做了什么。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "ui.db")
        self.db.initialize()
        self._previous_env = os.environ.pop("DEEPSEEK_API_KEY", None)
        self.fake = FakeDeepSeekClient(Settings(api_key=TEST_KEY))
        self.store = ApiKeyStore(
            env_file=self.tmp / ".env", credential_store=FakeCredentialStore()
        )
        self.store.save(TEST_KEY)  # 真实的 Key 存在凭据管理器里，重载设置时才拿得到
        self.context = AppContext(
            settings=Settings(api_key=TEST_KEY, model=DEFAULT_MODEL),
            database=self.db,
            api_key_store=self.store,
            api_client_factory=lambda _settings: self.fake,
        )
        self.infos: list[str] = []
        self.errors: list[str] = []
        self._original_info = main_window_module.show_info
        self._original_error = main_window_module.show_error
        main_window_module.show_info = lambda parent, title, message: self.infos.append(message)
        main_window_module.show_error = lambda parent, title, message: self.errors.append(message)
        self.addCleanup(self._restore_messages)
        self.window = MainWindow(self.context)

    def _restore_messages(self) -> None:
        main_window_module.show_info = self._original_info
        main_window_module.show_error = self._original_error

    def tearDown(self) -> None:
        worker = getattr(self.window, "_worker", None)
        if worker is not None:
            wait_until(lambda: not worker.isRunning(), timeout=5.0)
        self.window.close()
        QApplication.processEvents()
        if self._previous_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def _values(self, **overrides) -> StoredAiSettings:
        values = {
            "base_url": "https://proxy.example.com/v1",
            "model": "deepseek-v4-pro",
            "thinking": "enabled",
            "reasoning_effort": "high",
        }
        values.update(overrides)
        return StoredAiSettings(**values)

    def test_saving_persists_and_applies_the_new_settings(self) -> None:
        self.window.on_ai_settings_save(self._values())

        self.assertEqual(self.db.get_app_setting("ai.model"), "deepseek-v4-pro")
        self.assertEqual(self.db.get_app_setting("ai.reasoning_effort"), "high")
        self.assertEqual(self.context.settings.model, "deepseek-v4-pro")
        self.assertEqual(self.context.settings.thinking, "enabled")
        self.assertEqual(self.context.settings.base_url, "https://proxy.example.com/v1")
        self.assertEqual(
            self.window.settings.model_form.model_combo.currentText(), "deepseek-v4-pro"
        )

    def test_saved_settings_are_used_by_the_next_ai_client(self) -> None:
        seen: list[Settings] = []
        self.context.api_client_factory = lambda settings: (
            seen.append(settings) or FakeDeepSeekClient(settings)
        )
        self.window.on_ai_settings_save(self._values())
        self.context.client()
        self.assertEqual(seen[-1].model, "deepseek-v4-pro")

    def test_invalid_base_url_is_reported_and_nothing_is_saved(self) -> None:
        self.window.on_ai_settings_save(self._values(base_url="example.com/api"))

        self.assertIsNone(self.db.get_app_setting("ai.model"))
        self.assertIn("http", self.window.settings.model_form.status_label.text())

    def test_reset_restores_the_defaults_and_keeps_the_api_key(self) -> None:
        self.window.on_ai_settings_save(self._values())

        self.window.on_ai_settings_reset()

        self.assertEqual(self.context.settings.model, DEFAULT_MODEL)
        self.assertEqual(self.context.settings.base_url, DEFAULT_BASE_URL)
        self.assertEqual(self.context.settings.thinking, "disabled")
        self.assertEqual(self.context.settings.api_key, TEST_KEY)
        self.assertEqual(
            self.window.settings.model_form.model_combo.currentText(), DEFAULT_MODEL
        )

    def test_connection_test_uses_the_values_currently_in_the_form(self) -> None:
        seen: list[Settings] = []

        def factory(settings: Settings) -> FakeDeepSeekClient:
            seen.append(settings)
            return FakeDeepSeekClient(settings)

        self.context.api_client_factory = factory  # type: ignore[assignment]

        self.window.on_ai_settings_test(self._values(model="deepseek-flash"))

        self.assertTrue(wait_until(lambda: bool(seen)), "connection test never ran")
        self.assertEqual(seen[0].model, "deepseek-flash")
        self.assertEqual(seen[0].thinking, "enabled")

    def test_connection_test_reports_a_bad_base_url_without_network_access(self) -> None:
        self.window.on_ai_settings_test(self._values(base_url="not-a-url"))
        self.assertIn("http", self.window.settings.model_form.status_label.text())

    # ------------------------------------------------------- API Key 格式提示
    def test_a_key_that_does_not_look_like_a_deepseek_key_is_saved_with_a_warning(
        self,
    ) -> None:
        self.window.on_api_key_save("totally-wrong-key")

        self.assertTrue(wait_until(lambda: bool(self.window.settings.test_result.text())))
        self.assertEqual(self.context.api_key_store.load().value, "totally-wrong-key")
        self.assertEqual(self.window.settings.test_result.text(), tr("settings.key_saved_odd"))

    def test_a_well_formed_key_is_saved_without_a_warning(self) -> None:
        self.window.on_api_key_save("sk-1234567890abcdefghij")

        self.assertTrue(wait_until(lambda: bool(self.window.settings.test_result.text())))
        self.assertEqual(self.context.api_key_store.load().value, "sk-1234567890abcdefghij")
        text = self.window.settings.test_result.text()
        self.assertNotEqual(text, tr("settings.key_saved_odd"))
        self.assertIn(tr("settings.key_saved_credential"), text)


if __name__ == "__main__":
    unittest.main()
