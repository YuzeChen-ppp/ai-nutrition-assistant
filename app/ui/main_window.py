"""主窗口：页面导航、后台任务、API Key 管理与所有数据库写入。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date as date_cls
from typing import Any, Callable

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.schemas import FoodAnalysisResult, FoodItem, MealRecommendation
from app.ai.model_catalog import normalise_effort
from app.context import AppContext
from app.database.database import DatabaseError
from app.i18n import language_label, set_language, tr
from app.nutrition.purchase import find_inconvenient_matches
from app.ui.api_key_form import WelcomePage
from app.ui.dashboard_page import DashboardPage
from app.ui.dialogs import (
    ConfirmFoodDialog,
    FoodItemEditDialog,
    ManualEntryDialog,
    MealEditDialog,
    PhotoEntryDialog,
    ask_confirmation,
    show_error,
    show_info,
)
from app.ui.history_page import HistoryPage
from app.ui.profile_form import ProfileSetupPage
from app.ui.settings_page import ApiStatus, SettingsPage
from app.ui.widgets import LoadingBar, meal_type_title
from app.ui.workers import ApiWorker, run_in_background
from app.utils.app_settings_store import StoredAiSettings, normalise_base_url
from app.utils.config import env_overrides_settings, log_dir
from app.utils.credentials import CredentialError
from app.utils.images import FILE_DIALOG_FILTER, prepare_image_for_api
from app.utils.logging_setup import get_logger
from app.utils.validation import MEAL_TYPES, ValidationError, looks_like_api_key

__all__ = ["MainWindow"]

_logger = get_logger("ui")

_LANGUAGE_SETTING = "language"
_SKIPPED_API_KEY_SETTING = "api_key_prompt_skipped"


class MainWindow(QMainWindow):
    """页面、后台线程与数据库写入都集中在这里。"""

    def __init__(self, context: AppContext, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.db = context.database
        self._worker: ApiWorker | None = None
        self._recommendation: MealRecommendation | None = None
        self._recommendation_remaining: float | None = None
        # 测试时替换成自动确认的对话框
        self.confirm_dialog_factory: Callable[..., Any] = self._default_confirm_dialog

        self.setWindowTitle(tr("common.app_name"))
        self.resize(1180, 820)
        self.setMinimumSize(960, 640)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.stack = QStackedWidget()
        self.welcome_page = WelcomePage()
        self.setup_page = ProfileSetupPage()
        self.dashboard = DashboardPage()
        self.history = HistoryPage()
        self.settings = SettingsPage()
        for page in (
            self.welcome_page,
            self.setup_page,
            self.dashboard,
            self.history,
            self.settings,
        ):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self._connect_signals()
        self.statusBar().showMessage(tr("common.ready"))
        self._apply_stored_language()
        self.refresh_all()

    # ---------------------------------------------------------------- 界面
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        header.setStyleSheet(
            "QWidget#Header { background: #ffffff; border-bottom: 1px solid #e2e8f0; }"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(14)

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel(tr("common.app_name"))
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        subtitle = QLabel(tr("common.app_subtitle"))
        subtitle.setObjectName("Muted")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        layout.addLayout(title_block)

        self.loading = LoadingBar()
        layout.addWidget(self.loading)
        layout.addStretch(1)

        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in (
            ("today", tr("nav.today")),
            ("history", tr("nav.history")),
            ("settings", tr("nav.settings")),
        ):
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, target=key: self.goto(target))
            layout.addWidget(button)
            self.nav_buttons[key] = button
        return header

    def _connect_signals(self) -> None:
        self.welcome_page.submit_requested.connect(self.on_welcome_submit)
        self.welcome_page.skip_requested.connect(self.on_welcome_skip)

        self.setup_page.saved.connect(self.save_profile)

        self.dashboard.upload_requested.connect(self.on_upload_photo)
        self.dashboard.manual_requested.connect(self.on_manual_entry)
        self.dashboard.recommend_requested.connect(self.on_recommend_next_meal)
        self.dashboard.log_recommendation_requested.connect(self.on_log_recommendation)
        self.dashboard.dismiss_recommendation_requested.connect(self.on_dismiss_recommendation)
        self.dashboard.edit_item_requested.connect(self.on_edit_food_item)
        self.dashboard.delete_item_requested.connect(self.on_delete_food_item)
        self.dashboard.edit_meal_requested.connect(self.on_edit_meal)
        self.dashboard.delete_meal_requested.connect(self.on_delete_meal)
        self.dashboard.date_changed.connect(self.refresh_day)
        self.dashboard.open_settings_requested.connect(lambda: self.goto("settings"))

        self.history.open_day_requested.connect(self.on_open_day)
        self.history.day_selected.connect(self.on_history_day_selected)
        self.history.edit_meal_requested.connect(self.on_edit_meal)
        self.history.delete_meal_requested.connect(self.on_delete_meal)
        self.history.edit_item_requested.connect(self.on_edit_food_item)
        self.history.delete_item_requested.connect(self.on_delete_food_item)
        self.history.refresh_requested.connect(self.refresh_all)

        self.settings.profile_saved.connect(self.save_profile)
        self.settings.api_key_save_requested.connect(self.on_api_key_save)
        self.settings.api_key_test_requested.connect(self.on_api_key_test)
        self.settings.api_key_delete_requested.connect(self.on_api_key_delete)
        self.settings.ai_settings_save_requested.connect(self.on_ai_settings_save)
        self.settings.ai_settings_test_requested.connect(self.on_ai_settings_test)
        self.settings.ai_settings_reset_requested.connect(self.on_ai_settings_reset)
        self.settings.language_changed.connect(self.on_language_changed)
        self.settings.purchase_saved.connect(self.on_save_purchase_preferences)

    # ------------------------------------------------------------ 页面导航
    def goto(self, target: str) -> None:
        if target in {"today", "history"} and not self.context.settings.has_api_key:
            if self.db.get_app_setting(_SKIPPED_API_KEY_SETTING) != "1":
                self._show_page("welcome")
                return
        if target in {"today", "history"} and self.db.get_profile() is None:
            target = "setup"
        page = {
            "welcome": self.welcome_page,
            "setup": self.setup_page,
            "today": self.dashboard,
            "history": self.history,
            "settings": self.settings,
        }[target]
        self._show_page(target)
        if page is self.dashboard:
            self.refresh_day(self.dashboard.current_date())
        elif page is self.history:
            self.refresh_history()

    def _show_page(self, target: str) -> None:
        page = {
            "welcome": self.welcome_page,
            "setup": self.setup_page,
            "today": self.dashboard,
            "history": self.history,
            "settings": self.settings,
        }[target]
        self.stack.setCurrentWidget(page)
        for key, button in self.nav_buttons.items():
            button.setChecked(key == target)
        can_browse = (
            self.context.settings.has_api_key
            or self.db.get_app_setting(_SKIPPED_API_KEY_SETTING) == "1"
        )
        for key in ("today", "history"):
            self.nav_buttons[key].setEnabled(can_browse or target == "settings")

    # ------------------------------------------------------------ 整体刷新
    def refresh_all(self) -> None:
        profile = self.db.get_profile()
        has_key = self.context.settings.has_api_key
        skipped = self.db.get_app_setting(_SKIPPED_API_KEY_SETTING) == "1"

        self.dashboard.set_api_key_state(has_key)
        self.settings.set_profile(profile)
        self.settings.set_status(self._api_status())
        self._refresh_purchase_preferences()

        if not has_key and not skipped:
            self._show_page("welcome")
            self.welcome_page.focus_input()
        elif profile is None:
            self._show_page("setup")
        else:
            if self.stack.currentWidget() in (self.welcome_page, self.setup_page):
                self._show_page("today")
            else:
                self._show_page(self._current_page_name())

        self.refresh_day(self.dashboard.current_date())
        self.refresh_history()

    def _current_page_name(self) -> str:
        current = self.stack.currentWidget()
        for name, page in (
            ("today", self.dashboard),
            ("history", self.history),
            ("settings", self.settings),
            ("setup", self.setup_page),
            ("welcome", self.welcome_page),
        ):
            if current is page:
                return name
        return "today"

    def refresh_day(self, date_iso: str) -> None:
        totals = self.db.daily_totals(date_iso)
        meals = self.db.meals_for_date(date_iso)
        targets = self.db.get_targets()
        self.dashboard.set_data(
            date_iso=date_iso,
            totals=totals,
            targets=targets,
            meals=meals,
            is_today=date_iso == date_cls.today().isoformat(),
        )
        if date_iso != date_cls.today().isoformat():
            self._recommendation = None
            self._recommendation_remaining = None
            self.dashboard.clear_recommendation()

    def refresh_history(self) -> None:
        self.history.set_summaries(self.db.recent_days(limit=365))
        selected = self.history.selected_date()
        if selected:
            self.history.set_day_details(selected, self.db.meals_for_date(selected))

    def _api_status(self) -> ApiStatus:
        settings = self.context.settings
        return ApiStatus(
            has_key=settings.has_api_key,
            masked_key=settings.masked_api_key,
            key_source=settings.api_key_source,
            model=settings.model,
            base_url=settings.base_url,
            timeout=settings.timeout,
            thinking=settings.thinking,
            reasoning_effort=settings.reasoning_effort,
            env_overrides=env_overrides_settings(),
            env_files=settings.env_files,
            database_path=str(self.db.path),
            log_directory=str(log_dir()),
        )

    # ------------------------------------------------------------ 购买条件
    def _refresh_purchase_preferences(self) -> None:
        preferences = self.db.get_purchase_preferences()
        self.settings.set_purchase_preferences(preferences)
        self.dashboard.set_purchase_summary(
            preferences.summary()
            if preferences.is_configured()
            else tr("purchase.using_defaults")
        )

    def on_save_purchase_preferences(self, preferences: Any) -> None:
        """保存「我方便买到什么」，之后的推荐都会带上这些条件。"""
        if preferences is None:
            return
        try:
            self.db.save_purchase_preferences(preferences)
        except DatabaseError as exc:
            self.settings.set_purchase_status(exc.user_message, ok=False)
            return
        # 旧的推荐是用旧条件算的，清掉避免误导
        self.on_dismiss_recommendation()
        self._refresh_purchase_preferences()
        self.settings.set_purchase_status(tr("purchase.saved"), ok=True)
        self.statusBar().showMessage(tr("purchase.saved"))

    # ------------------------------------------------------ API Key 管理
    def on_welcome_submit(self, key: str) -> None:
        """欢迎页的「测试并保存」：保存 → 立即测试连接 → 进入下一步。"""
        if not key.strip():
            self.welcome_page.set_status(tr("settings.key_required"), ok=False)
            return

        def job() -> tuple[Any, str]:
            stored = self.context.api_key_store.save(key)
            settings = self.context.reload_settings()
            message = self.context.client().check_connection()
            return stored, message

        def success(result: tuple[Any, str]) -> None:
            stored, message = result
            where = self.context.api_key_store.save_location_label(stored)
            self.welcome_page.set_status(tr("settings.key_saved", where=where), ok=True)
            self.welcome_page.clear_input()
            _logger.info("API key saved and verified")
            self._after_api_key_ready(message)

        self._start_job(
            job,
            label="save and test api key",
            busy_message=tr("settings.testing"),
            on_success=success,
            on_error=lambda message: self.welcome_page.set_status(
                tr("settings.test_failed", message=message), ok=False
            ),
            busy_targets=("welcome",),
        )

    def _after_api_key_ready(self, detail: str = "") -> None:
        self.db.set_app_setting(_SKIPPED_API_KEY_SETTING, "0")
        self.refresh_all()
        if self.db.get_profile() is None:
            self._show_page("setup")
        else:
            self._show_page("today")
        show_info(
            self,
            tr("nav.settings"),
            tr("settings.test_ok", detail=detail),
        )

    def on_welcome_skip(self) -> None:
        self.db.set_app_setting(_SKIPPED_API_KEY_SETTING, "1")
        self.refresh_all()
        if self.db.get_profile() is None:
            self._show_page("setup")
        else:
            self._show_page("today")

    def on_api_key_save(self, key: str) -> None:
        if not key.strip():
            self.settings.set_test_result(tr("settings.key_required"), ok=False)
            return

        def job() -> Any:
            stored = self.context.api_key_store.save(key)
            self.context.reload_settings()
            return stored

        def success(stored: Any) -> None:
            where = self.context.api_key_store.save_location_label(stored)
            self.settings.clear_key_input()
            self.refresh_all()
            # 形状不对也照样保存：代理服务可能用别的格式，真正的裁判是「测试 API Key」。
            if looks_like_api_key(key):
                self.settings.set_test_result(tr("settings.key_saved", where=where), ok=True)
            else:
                self.settings.set_test_result(tr("settings.key_saved_odd"), ok=False)
            self.statusBar().showMessage(tr("status.api_key_saved"))

        self._start_job(
            job,
            label="save api key",
            busy_message=tr("common.working"),
            on_success=success,
            on_error=lambda message: self.settings.set_test_result(
                tr("settings.test_failed", message=message), ok=False
            ),
            busy_targets=("settings",),
        )

    def on_api_key_test(self, key: str) -> None:
        """测试连通性。输入框里有内容就测这个，否则测已保存的 Key。"""
        settings = self.context.settings
        if key.strip():
            candidate = replace(settings, api_key=key.strip())
            client = self.context.api_client_factory(candidate)
        else:
            client = self.context.client()
            if not settings.has_api_key:
                self.settings.set_test_result(tr("settings.key_required"), ok=False)
                return

        def success(message: str) -> None:
            self.settings.set_test_result(
                tr("settings.test_ok", detail=message), ok=True
            )

        self._start_job(
            client.check_connection,
            label="test api key",
            busy_message=tr("settings.testing"),
            on_success=success,
            on_error=lambda message: self.settings.set_test_result(
                tr("settings.test_failed", message=message), ok=False
            ),
            busy_targets=("settings",),
        )

    def on_api_key_delete(self) -> None:
        if not ask_confirmation(
            self,
            tr("settings.delete_confirm_title"),
            tr("settings.delete_confirm"),
        ):
            return
        try:
            self.context.api_key_store.delete()
        except CredentialError as exc:
            show_error(self, tr("error.delete_failed"), exc.user_message)
            return
        self.context.reload_settings()
        self.db.set_app_setting(_SKIPPED_API_KEY_SETTING, "0")
        self.settings.clear_key_input()
        self.settings.set_test_result(tr("settings.key_deleted"), ok=True)
        self.refresh_all()
        self.statusBar().showMessage(tr("status.api_key_deleted"))

    # ------------------------------------------------- AI 模型设置（可编辑）
    def on_ai_settings_save(self, values: StoredAiSettings) -> None:
        """保存接口地址 / 模型 / 思考模式 / 思考强度。

        只写 SQLite 的 ``app_settings`` 表；API Key 仍然只归 Windows 凭据管理器管。
        保存后必须 ``reload_settings()``，否则下一次 AI 请求还会用旧参数。
        """
        try:
            self.context.ai_settings.save(
                base_url=values.base_url,
                model=values.model,
                thinking=values.thinking,
                reasoning_effort=values.reasoning_effort,
            )
        except ValidationError as exc:
            self.settings.model_form.set_status(str(exc), ok=False)
            return

        self.context.reload_settings()
        self.refresh_all()
        self.settings.model_form.set_status(tr("settings.ai_saved"), ok=True)
        self.statusBar().showMessage(tr("settings.ai_saved"))
        _logger.info(
            "AI settings saved (model=%s, thinking=%s, effort=%s)",
            self.context.settings.model,
            self.context.settings.thinking,
            self.context.settings.reasoning_effort,
        )

    def on_ai_settings_test(self, values: StoredAiSettings) -> None:
        """用表单里当前的值测一次连接（不改动已保存的设置）。"""
        try:
            base_url = normalise_base_url(values.base_url)
        except ValidationError as exc:
            self.settings.model_form.set_status(str(exc), ok=False)
            return

        candidate = replace(
            self.context.settings,
            base_url=base_url,
            model=(values.model or self.context.settings.model).strip(),
            thinking="enabled" if values.thinking == "enabled" else "disabled",
            reasoning_effort=normalise_effort(values.reasoning_effort),
        )
        client = self.context.api_client_factory(candidate)

        def success(message: str) -> None:
            self.settings.model_form.set_status(
                tr("settings.ai_test_ok", detail=message), ok=True
            )

        self._start_job(
            client.check_connection,
            label="test ai settings",
            busy_message=tr("settings.testing"),
            on_success=success,
            on_error=lambda message: self.settings.model_form.set_status(
                tr("settings.test_failed", message=message), ok=False
            ),
            busy_targets=("settings",),
        )

    def on_ai_settings_reset(self) -> None:
        """恢复默认设置；API Key 不在这次重置的范围内。"""
        self.context.ai_settings.clear()
        self.context.reload_settings()
        self.refresh_all()
        self.settings.set_test_result(tr("settings.ai_reset_done"), ok=True)
        self.statusBar().showMessage(tr("settings.ai_reset_done"))

    # ------------------------------------------------------------ 语言设置
    def _apply_stored_language(self) -> None:
        stored = self.db.get_app_setting(_LANGUAGE_SETTING)
        if stored:
            set_language(stored)
        self.settings.set_language_selection(stored or "")

    def on_language_changed(self, code: str) -> None:
        if not code:
            return
        set_language(code)
        self.db.set_app_setting(_LANGUAGE_SETTING, code)
        self.settings.set_language_hint(
            tr("status.language_changed", language=language_label(code))
        )

    # -------------------------------------------------------------- 个人资料
    def save_profile(self, profile: Any) -> None:
        try:
            targets = profile.calculate_targets()
            self.db.save_profile(profile, targets)
        except DatabaseError as exc:
            show_error(self, tr("error.profile_save_failed"), exc.user_message)
            return
        self.refresh_all()
        self._show_page("today")
        self.statusBar().showMessage(
            tr(
                "status.profile_saved",
                target=f"{targets.calorie_target:,.0f}",
                bmr=f"{targets.bmr:,.0f}",
                tdee=f"{targets.tdee:,.0f}",
            )
        )

    # ------------------------------------------------------ 添加食物：文字
    def on_manual_entry(self) -> None:
        dialog = ManualEntryDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.analyze_text(dialog.text(), dialog.meal_type())

    def analyze_text(self, text: str, meal_type: str) -> None:
        client = self.context.client()
        self._start_job(
            lambda: client.analyze_text(text),
            label="analyse text",
            busy_message=tr("dashboard.loading_text"),
            on_success=lambda result: self._handle_analysis(result, meal_type, "manual"),
            busy_targets=("dashboard",),
        )

    # ------------------------------------------------------ 添加食物：照片
    def on_upload_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("photo.window_title"), "", FILE_DIALOG_FILTER
        )
        if not path:
            return
        dialog = PhotoEntryDialog(path, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.analyze_photo(path, dialog.meal_type(), dialog.note())

    def analyze_photo(self, path: str, meal_type: str, note: str = "") -> None:
        client = self.context.client()

        def job() -> tuple[Any, FoodAnalysisResult]:
            prepared = prepare_image_for_api(path)
            result = client.analyze_image(prepared, note=note)
            return prepared, result

        def on_success(payload: tuple[Any, FoodAnalysisResult]) -> None:
            prepared, result = payload
            if prepared.resized:
                self.statusBar().showMessage(tr("photo.resized"))
            self._handle_analysis(result, meal_type, "image")

        self._start_job(
            job,
            label="analyse photo",
            busy_message=tr("dashboard.loading_image"),
            on_success=on_success,
            on_error=lambda message: show_error(self, tr("error.photo_title"), message),
            busy_targets=("dashboard",),
        )

    # ---------------------------------------------------------- 确认与保存
    def _default_confirm_dialog(
        self,
        items: list[FoodItem],
        source: str,
        note: str,
    ) -> ConfirmFoodDialog:
        return ConfirmFoodDialog(
            items,
            source=source,
            note=note,
            reference_job_factory=self.reference_job_factory(),
            parent=self,
        )

    def reference_job_factory(self) -> Any:
        """「查询在线参考」用的后台任务工厂（唯一一处把按钮接到 DeepSeek 上）。

        返回 ``None`` 表示没有 API Key：此时确认页/编辑对话框里的按钮是灰的，
        并说明原因。这个工厂只有用户点按钮时才会被调用。
        """
        if not self.context.settings.has_api_key:
            return None
        client = self.context.client()

        def factory(
            on_success: Callable[[Any], None],
            on_error: Callable[[str], None],
            food_name: str,
        ) -> ApiWorker:
            # 走和识别/推荐同一条后台线程机制：界面不会卡住。
            return run_in_background(
                lambda: client.fetch_nutrition_reference(food_name),
                label="query online nutrition reference",
                on_success=on_success,
                on_error=on_error,
                owner=self,
            )

        return factory

    def _handle_analysis(
        self,
        result: FoodAnalysisResult,
        meal_type: str,
        source: str,
    ) -> None:
        dialog = self.confirm_dialog_factory(list(result.foods), source, result.note)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(tr("status.nothing_saved"))
            return
        self.save_items(
            dialog.confirmed_items(),
            meal_type=meal_type,
            source=source,
            note=result.note.strip(),
        )

    def save_items(
        self,
        items: list[FoodItem],
        *,
        meal_type: str,
        source: str = "manual",
        note: str = "",
        date: str | None = None,
    ) -> int | None:
        """把一餐写入数据库（唯一的写入入口）。"""
        try:
            meal_id = self.db.add_meal(
                items, meal_type=meal_type, source=source, note=note, date=date
            )
        except DatabaseError as exc:
            show_error(self, tr("error.meal_save_failed"), exc.user_message)
            return None
        self.refresh_all()
        totals = self.db.meal_totals(meal_id)
        self.statusBar().showMessage(
            tr("status.saved_items", count=len(items), kcal=f"{totals.calories_kcal:,.0f}")
        )
        return meal_id

    # ------------------------------------------------------------ 历史记录
    def on_open_day(self, date_iso: str) -> None:
        self.dashboard.set_date(date_iso)
        self._show_page("today")
        self.refresh_day(date_iso)

    def on_history_day_selected(self, date_iso: str) -> None:
        self.history.set_day_details(date_iso, self.db.meals_for_date(date_iso))

    # ---------------------------------------------------------------- 编辑
    def on_edit_food_item(self, item_id: int) -> None:
        record = self.db.get_food_item(item_id)
        if record is None:
            show_error(self, tr("error.not_found_title"), tr("error.not_found"))
            return
        dialog = FoodItemEditDialog(
            record,
            reference_job_factory=self.reference_job_factory(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            item = dialog.item()
            self.db.update_food_item(item_id, **item.to_record_fields())
        except DatabaseError as exc:
            show_error(self, tr("error.save_failed"), exc.user_message)
            return
        except Exception as exc:  # noqa: BLE001 - Pydantic 校验错误
            show_error(self, tr("error.check_values_title"), str(exc))
            return
        self.refresh_all()
        self.statusBar().showMessage(tr("status.food_updated"))

    def on_delete_food_item(self, item_id: int) -> None:
        record = self.db.get_food_item(item_id)
        if record is None:
            return
        if not ask_confirmation(
            self,
            tr("delete.food_title"),
            tr("delete.food_message", name=record.name),
        ):
            return
        try:
            self.db.delete_food_item(item_id)
        except DatabaseError as exc:
            show_error(self, tr("error.delete_failed"), exc.user_message)
            return
        self.refresh_all()
        self.statusBar().showMessage(tr("status.food_deleted"))

    def on_edit_meal(self, meal_id: int) -> None:
        meal = self.db.get_meal(meal_id)
        if meal is None:
            return
        dialog = MealEditDialog(meal, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not _valid_date(values["date"]):
            show_error(self, tr("mealedit.bad_date_title"), tr("mealedit.bad_date"))
            return
        try:
            self.db.update_meal(meal_id, **values)
        except DatabaseError as exc:
            show_error(self, tr("error.save_failed"), exc.user_message)
            return
        self.refresh_all()
        self.statusBar().showMessage(tr("status.meal_updated"))

    def on_delete_meal(self, meal_id: int) -> None:
        meal = self.db.get_meal(meal_id)
        if meal is None:
            return
        if not ask_confirmation(
            self,
            tr("delete.meal_title"),
            tr(
                "delete.meal_message",
                date=meal.date,
                meal_type=meal_type_title(meal.meal_type),
                count=len(meal.items),
            ),
        ):
            return
        try:
            self.db.delete_meal(meal_id)
        except DatabaseError as exc:
            show_error(self, tr("error.delete_failed"), exc.user_message)
            return
        self.refresh_all()
        self.statusBar().showMessage(tr("status.meal_deleted"))

    # ------------------------------------------------------------ 下一餐推荐
    def on_recommend_next_meal(self) -> None:
        if self.db.get_profile() is None:
            self._show_page("setup")
            return
        if self.db.count_meals() == 0:
            show_info(self, tr("recommend.card_title"), tr("recommend.need_meals"))
            return
        date_iso = self.dashboard.current_date()
        context = self._recommendation_context(date_iso)
        preferences = self.db.get_purchase_preferences()
        client = self.context.client()

        def on_success(recommendation: MealRecommendation) -> None:
            self._recommendation = recommendation
            remaining = context["remaining"]["calories_kcal"]
            self._recommendation_remaining = remaining
            self.dashboard.set_recommendation(
                recommendation,
                remaining_kcal=remaining,
                purchase_context=self._purchase_context_text(preferences),
                inconvenient_warning=self._inconvenient_warning(recommendation, preferences),
            )
            self.statusBar().showMessage(tr("status.recommendation_ready"))

        self._start_job(
            lambda: client.recommend_next_meal(context),
            label="recommend next meal",
            busy_message=tr("dashboard.loading_recommend"),
            on_success=on_success,
            busy_targets=("dashboard",),
        )

    def _purchase_context_text(self, preferences: Any) -> str:
        stores = "、".join(preferences.stores) or tr("recommend.context_no_stores")
        return tr(
            "recommend.context_summary",
            country=preferences.country_label(),
            stores=stores,
            budget=preferences.budget_label(),
            time=preferences.cooking_time_label(),
        )

    def _inconvenient_warning(self, recommendation: MealRecommendation, preferences: Any) -> str:
        """AI 偶尔仍会带上用户不能买的食材，这里做一次确定性检查并提醒。"""
        hard = preferences.hard_ingredients()
        if not hard:
            return ""
        names = [food.name for food in recommendation.foods]
        names += recommendation.shopping_names()
        matches = find_inconvenient_matches(names, hard)
        if not matches:
            return ""
        items = "、".join(dict.fromkeys(raw for _, raw in matches))
        return tr("recommend.inconvenient_warning", items=items)

    def _recommendation_context(self, date_iso: str) -> dict[str, Any]:
        profile = self.db.get_profile()
        targets = self.db.get_targets()
        totals = self.db.daily_totals(date_iso)
        meals = self.db.meals_for_date(date_iso)
        preferences = self.db.get_purchase_preferences()
        targets_totals = targets.as_totals() if targets else None
        remaining = targets_totals - totals if targets_totals else totals

        def rounded(value: float) -> float:
            return round(float(value), 1)

        return {
            "date": date_iso,
            "goal": profile.goal if profile else "maintain",
            "activity_level": profile.activity_level if profile else "sedentary",
            # ---- 购买条件：推荐要优先使用「现在方便买到」的食材 ----
            "location": preferences.country,
            "preferred_stores": list(preferences.stores),
            "easy_to_buy_ingredients": preferences.effective_easy_ingredients(),
            "using_default_ingredient_list": preferences.uses_default_ingredients(),
            "inconvenient_ingredients": preferences.hard_ingredients(),
            "budget": preferences.budget,
            "diet_preferences": preferences.diet_preferences,
            "cooking_equipment": list(preferences.equipment),
            "max_cooking_minutes": int(preferences.max_cooking_minutes or 0),
            "daily_targets": {
                "calories_kcal": rounded(targets.calorie_target if targets else 0),
                "protein_g": rounded(targets.protein_g if targets else 0),
                "carbs_g": rounded(targets.carbs_g if targets else 0),
                "fat_g": rounded(targets.fat_g if targets else 0),
                "fibre_g": rounded(targets.fibre_g if targets else 0),
                "sugar_limit_g": rounded(targets.sugar_limit_g if targets else 0),
                "sodium_limit_mg": rounded(targets.sodium_limit_mg if targets else 0),
            },
            "consumed": {key: rounded(value) for key, value in totals.as_dict().items()},
            "remaining": {key: rounded(value) for key, value in remaining.as_dict().items()},
            "today_meals": [
                {
                    "meal_type": meal.meal_type,
                    "time": meal.time,
                    "items": [
                        {
                            "name": item.name,
                            "estimated_weight_g": item.estimated_weight_g,
                            "calories_kcal": rounded(item.calories_kcal),
                            "protein_g": rounded(item.protein_g),
                            "carbs_g": rounded(item.carbs_g),
                            "fat_g": rounded(item.fat_g),
                            "fibre_g": rounded(item.fibre_g),
                        }
                        for item in meal.items
                    ],
                }
                for meal in meals
            ],
        }

    def on_log_recommendation(self) -> None:
        if self._recommendation is None:
            return
        labels = [meal_type_title(meal_type) for meal_type in MEAL_TYPES]
        label, accepted = QInputDialog.getItem(
            self,
            tr("recommend.save_as"),
            tr("meal.type"),
            labels,
            _default_meal_index(),
            False,
        )
        if not accepted:
            return
        meal_type = MEAL_TYPES[labels.index(label)]
        note = tr("recommend.note_prefix", title=self._recommendation.title)
        self.save_items(
            list(self._recommendation.foods),
            meal_type=meal_type,
            source="manual",
            note=note,
        )
        self.statusBar().showMessage(
            tr("status.logged_recommendation", meal_type=meal_type_title(meal_type))
        )
        self.on_dismiss_recommendation()

    def on_dismiss_recommendation(self) -> None:
        self._recommendation = None
        self._recommendation_remaining = None
        self.dashboard.clear_recommendation()

    # --------------------------------------------------------------- 后台任务
    def _start_job(
        self,
        job: Callable[[], Any],
        *,
        label: str,
        busy_message: str,
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
        busy_targets: tuple[str, ...] = ("dashboard",),
    ) -> None:
        if self._worker is not None and self._worker.isRunning():
            show_info(self, tr("error.busy_title"), tr("error.busy"))
            return
        self._set_busy(True, busy_message, busy_targets)

        def success(value: Any) -> None:
            self._set_busy(False, "", busy_targets)
            on_success(value)

        def failure(message: str) -> None:
            self._set_busy(False, "", busy_targets)
            if on_error is not None:
                on_error(message)
            else:
                show_error(self, tr("error.ai_service_title"), message)
            self.statusBar().showMessage(tr("status.request_failed"))

        worker = run_in_background(
            job, label=label, on_success=success, on_error=failure, owner=self
        )
        self._worker = worker

    def _set_busy(
        self,
        busy: bool,
        message: str,
        busy_targets: tuple[str, ...] = ("dashboard", "settings"),
    ) -> None:
        if "dashboard" in busy_targets:
            self.dashboard.set_busy(busy, message)
        if "settings" in busy_targets:
            self.settings.set_busy(busy, message)
        if "welcome" in busy_targets:
            self.welcome_page.set_busy(busy, message)
        if busy:
            self.loading.start(message)
            self.statusBar().showMessage(message)
        else:
            self.loading.stop()

    def closeEvent(self, event: Any) -> None:
        if self._worker is not None and self._worker.isRunning():
            if not ask_confirmation(
                self,
                tr("error.request_running_title"),
                tr("error.request_running_close"),
            ):
                event.ignore()
                return
            self._worker.wait(3000)
        self.context.close()
        event.accept()

def _valid_date(value: str) -> bool:
    try:
        date_cls.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _default_meal_index() -> int:
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 10:
        return MEAL_TYPES.index("breakfast")
    if hour < 15:
        return MEAL_TYPES.index("lunch")
    if hour < 21:
        return MEAL_TYPES.index("dinner")
    return MEAL_TYPES.index("snack")
