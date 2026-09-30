"""界面流程测试：首次启动的 API Key 引导、保存、测试与删除。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

from app.ai.errors import MissingAPIKeyError  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.database.models import UserProfile  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.ui import main_window as main_window_module  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.utils.config import Settings  # noqa: E402
from app.utils.credentials import ApiKeyStore  # noqa: E402
from tests.fakes import FakeDeepSeekClient  # noqa: E402
from tests.test_api_key import FakeCredentialStore  # noqa: E402

_APP: QApplication | None = None
NEW_KEY = "sk-brand-new-key-1234567890"


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


class ApiKeyOnboardingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "ui.db")
        self.db.initialize()
        self.credentials = FakeCredentialStore()
        self.store = ApiKeyStore(
            env_file=self.tmp / ".env", credential_store=self.credentials
        )
        self.fake = FakeDeepSeekClient(Settings(api_key=""))
        self.context = AppContext(
            settings=Settings(api_key="", model="deepseek-flash"),
            database=self.db,
            api_key_store=self.store,
            api_client_factory=lambda _settings: self.fake,
        )
        self._previous_env = os.environ.pop("DEEPSEEK_API_KEY", None)
        self.infos: list[str] = []
        self.errors: list[str] = []
        self._patch_messages()
        self.window = MainWindow(self.context)

    def tearDown(self) -> None:
        # 等后台线程结束，避免回调在数据库关闭之后再执行
        worker = getattr(self.window, "_worker", None)
        if worker is not None:
            wait_until(lambda: not worker.isRunning(), timeout=5.0)
        self.window.close()
        QApplication.processEvents()
        if self._previous_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def _patch_messages(self) -> None:
        self._original_info = main_window_module.show_info
        self._original_error = main_window_module.show_error
        main_window_module.show_info = (
            lambda parent, title, message: self.infos.append(message)
        )
        main_window_module.show_error = (
            lambda parent, title, message: self.errors.append(message)
        )
        self.addCleanup(self._restore_messages)

    def _restore_messages(self) -> None:
        main_window_module.show_info = self._original_info
        main_window_module.show_error = self._original_error

    # ------------------------------------------------------------ 首次启动
    def test_first_run_shows_welcome_page(self) -> None:
        self.assertIs(self.window.stack.currentWidget(), self.window.welcome_page)
        self.assertFalse(self.window.nav_buttons["today"].isEnabled())
        self.assertFalse(self.window.nav_buttons["history"].isEnabled())
        self.assertTrue(self.window.nav_buttons["settings"].isEnabled())
        self.assertEqual(
            self.window.welcome_page.form.input.placeholderText(),
            tr("apikey.placeholder"),
        )

    def test_welcome_text_is_chinese(self) -> None:
        texts = [
            label.text()
            for label in self.window.welcome_page.findChildren(
                type(self.window.welcome_page.form.status_label)
            )
        ]
        joined = " ".join(texts)
        self.assertIn("欢迎使用 AI 饮食助手", joined)
        self.assertIn("DeepSeek API Key", joined)

    def test_api_key_input_is_masked_by_default(self) -> None:
        field = self.window.welcome_page.form.input
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)
        self.window.welcome_page.form.toggle_button.setChecked(True)
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Normal)
        self.window.welcome_page.form.toggle_button.setChecked(False)
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)

    def test_empty_submit_shows_chinese_hint(self) -> None:
        self.window.on_welcome_submit("   ")
        self.assertIn("API Key", self.window.welcome_page.form.status_label.text())
        self.assertFalse(self.credentials.value)

    def test_submit_saves_key_and_moves_on(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: self.credentials.value == NEW_KEY),
            "API Key 没有被保存",
        )
        self.assertTrue(self.context.settings.has_api_key)
        self.assertEqual(self.context.settings.api_key, NEW_KEY)
        self.assertIn("凭据管理器", self.context.settings.api_key_source)
        # 还没有个人资料 → 进入个人资料设置页
        self.assertTrue(
            wait_until(lambda: self.window.stack.currentWidget() is self.window.setup_page),
            "保存成功后应该进入个人资料设置页",
        )
        self.assertTrue(self.infos, "保存成功后应该给出提示")
        self.assertIn("有效", self.infos[-1])
        self.assertEqual(self.window.welcome_page.form.key_text(), "")

    def test_submit_with_bad_key_reports_error_and_stays(self) -> None:
        self.fake.fail_with = MissingAPIKeyError(tr("error.api_key_invalid"))
        self.window.on_welcome_submit("sk-wrong-key-000000")
        self.assertTrue(
            wait_until(
                lambda: "测试失败" in self.window.welcome_page.form.status_label.text()
            ),
            "应该显示测试失败",
        )
        self.assertIn("API Key 无效", self.window.welcome_page.form.status_label.text())
        self.assertIs(self.window.stack.currentWidget(), self.window.welcome_page)

    def test_skip_is_remembered_for_next_launch(self) -> None:
        self.window.on_welcome_skip()
        self.assertEqual(self.db.get_app_setting("api_key_prompt_skipped"), "1")
        self.assertTrue(self.window.nav_buttons["today"].isEnabled())
        self.window.close()

        second = MainWindow(self.context)
        try:
            self.assertIsNot(second.stack.currentWidget(), second.welcome_page)
            self.assertIs(second.stack.currentWidget(), second.setup_page)
        finally:
            second.close()
        self.window = MainWindow(self.context)

    # -------------------------------------------------------------- 设置页
    def test_settings_page_is_chinese_and_hides_the_key(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        wait_until(lambda: self.context.settings.has_api_key)
        self.window.save_profile(
            UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
        )
        self.window.goto("settings")

        self.assertEqual(self.window.stack.currentWidget(), self.window.settings)
        masked = self.window.settings.current_key_label.text()
        self.assertNotIn(NEW_KEY, masked)
        self.assertIn("*", masked)
        self.assertEqual(self.window.settings.test_result.text(), "")
        self.assertIn("凭据管理器", self.window.settings.source_label.text())
        self.assertTrue(self.window.settings.delete_button.isEnabled())
        self.assertEqual(
            self.window.settings.key_form.input.placeholderText(),
            tr("apikey.placeholder"),
        )
        about_texts = [label.text() for label in self.window.settings.findChildren(QLabel)]
        self.assertTrue(any("仅供参考" in text for text in about_texts))

    def test_settings_save_key_updates_status(self) -> None:
        self.window.save_profile(
            UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
        )
        self.window.on_api_key_save(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: "已保存" in self.window.settings.test_result.text()),
            "设置页应该显示保存成功",
        )
        self.assertNotIn(NEW_KEY, self.window.settings.current_key_label.text())

    def test_settings_test_connection_reports_success(self) -> None:
        self.window.on_api_key_save(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: "已保存" in self.window.settings.test_result.text()),
            "保存成功后设置页应该有提示",
        )
        self.window.on_api_key_test("")
        self.assertTrue(
            wait_until(lambda: "有效" in self.window.settings.test_result.text()),
            "应该报告连接成功",
        )

    def test_settings_test_connection_reports_network_error(self) -> None:
        from app.ai.errors import AINetworkError

        self.fake.fail_with = AINetworkError()
        self.window.on_api_key_test(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: "测试失败" in self.window.settings.test_result.text())
        )
        self.assertIn("网络连接", self.window.settings.test_result.text())

    def test_delete_key_clears_everything(self) -> None:
        self.window.save_profile(
            UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
        )
        self.window.on_api_key_save(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: "已保存" in self.window.settings.test_result.text()),
            "保存成功后设置页应该有提示",
        )

        original_confirm = main_window_module.ask_confirmation
        main_window_module.ask_confirmation = lambda *args, **kwargs: True
        try:
            self.window.on_api_key_delete()
        finally:
            main_window_module.ask_confirmation = original_confirm

        self.assertIsNone(self.credentials.value)
        self.assertFalse(self.context.settings.has_api_key)
        self.assertEqual(
            self.window.settings.current_key_label.text(), tr("settings.key_not_set")
        )
        self.assertFalse(self.window.settings.delete_button.isEnabled())
        # 没有 Key 时主界面会给出中文提醒，并且再次启动会重新引导
        self.assertFalse(self.window.dashboard.api_warning.isHidden())
        self.assertIs(self.window.stack.currentWidget(), self.window.welcome_page)

    def test_key_is_usable_after_saving(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        self.assertTrue(
            wait_until(
                lambda: self.window.stack.currentWidget() is self.window.setup_page
            ),
            "保存成功后应该进入个人资料设置页",
        )
        self.window.save_profile(
            UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
        )
        self.assertIs(self.window.stack.currentWidget(), self.window.dashboard)
        self.assertTrue(self.window.nav_buttons["today"].isEnabled())
        self.assertTrue(self.window.dashboard.api_warning.isHidden())


class ApiKeyFallbackTest(unittest.TestCase):
    """凭据管理器不可用时：界面依然能保存，并写入 .env。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "ui.db")
        self.db.initialize()
        self.credentials = FakeCredentialStore(available=False)
        self.env_path = self.tmp / ".env"
        self.store = ApiKeyStore(
            env_file=self.env_path, credential_store=self.credentials
        )
        self.fake = FakeDeepSeekClient(Settings(api_key=""))
        self.context = AppContext(
            settings=Settings(api_key=""),
            database=self.db,
            api_key_store=self.store,
            api_client_factory=lambda _settings: self.fake,
        )
        self._previous_env = os.environ.pop("DEEPSEEK_API_KEY", None)
        self.infos: list[str] = []
        self.errors: list[str] = []
        self._patch_messages()
        self.window = MainWindow(self.context)

    def tearDown(self) -> None:
        # 顺序很重要：先等后台线程结束、让排队的主线程回调跑完（此时补丁仍然生效），
        # 再关闭窗口，最后才由 addCleanup 恢复真正的弹窗函数。
        worker = getattr(self.window, "_worker", None)
        if worker is not None:
            wait_until(lambda: not worker.isRunning(), timeout=5.0)
        QApplication.processEvents()
        self.window.close()
        QApplication.processEvents()
        if self._previous_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def _patch_messages(self) -> None:
        original_info = main_window_module.show_info
        original_error = main_window_module.show_error
        main_window_module.show_info = lambda parent, title, message: self.infos.append(message)
        main_window_module.show_error = lambda parent, title, message: self.errors.append(message)

        def restore() -> None:
            main_window_module.show_info = original_info
            main_window_module.show_error = original_error

        self.addCleanup(restore)

    def test_key_is_written_to_env_file(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        self.assertTrue(
            wait_until(lambda: self.env_path.is_file() and bool(self.infos)),
            ".env 后备文件应该被创建",
        )
        self.assertIn("DEEPSEEK_API_KEY=" + NEW_KEY, self.env_path.read_text(encoding="utf-8"))
        self.assertTrue(self.context.settings.has_api_key)
        self.assertIn(".env", self.context.settings.api_key_source)

    def test_settings_page_shows_env_source(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        wait_until(lambda: self.env_path.is_file() and bool(self.infos))
        self.window.save_profile(
            UserProfile(age=30, sex="male", height_cm=175, weight_kg=70)
        )
        self.window.goto("settings")
        self.assertIn(".env", self.window.settings.source_label.text())

    def test_delete_clears_the_env_file(self) -> None:
        self.window.on_welcome_submit(NEW_KEY)
        wait_until(lambda: self.env_path.is_file() and bool(self.infos))
        original_confirm = main_window_module.ask_confirmation
        main_window_module.ask_confirmation = lambda *args, **kwargs: True
        try:
            self.window.on_api_key_delete()
        finally:
            main_window_module.ask_confirmation = original_confirm
        self.assertFalse(self.context.settings.has_api_key)
        self.assertFalse(self.store.load().found)
        self.assertIsNone(self.store.env_file.get())


if __name__ == "__main__":
    unittest.main()
