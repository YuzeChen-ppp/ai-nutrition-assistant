"""设置页：AI 设置（API Key）、个人资料、界面语言与关于。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.database.models import UserProfile
from app.i18n import available_languages, tr
from app.ui.api_key_form import ApiKeyForm
from app.ui.model_settings_form import ModelSettingsForm
from app.ui.profile_form import ProfileForm
from app.ui.purchase_form import PurchaseForm
from app.ui.widgets import Card, SectionTitle, muted

__all__ = ["ApiStatus", "SettingsPage"]


@dataclass(frozen=True, slots=True)
class ApiStatus:
    """API 配置的只读摘要（永远不包含完整密钥）。"""

    has_key: bool
    masked_key: str
    key_source: str
    model: str
    base_url: str
    timeout: float
    thinking: str
    reasoning_effort: str
    env_overrides: bool
    env_files: tuple[str, ...]
    database_path: str
    log_directory: str


class SettingsPage(QWidget):
    """API Key 管理、个人资料编辑、语言与关于。"""

    profile_saved = Signal(object)
    api_key_save_requested = Signal(str)
    api_key_test_requested = Signal(str)
    api_key_delete_requested = Signal()
    language_changed = Signal(str)
    purchase_saved = Signal(object)
    ai_settings_save_requested = Signal(object)
    ai_settings_test_requested = Signal(object)
    ai_settings_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(16)
        layout.addWidget(SectionTitle(tr("settings.title")))

        layout.addWidget(self._build_ai_card())
        layout.addWidget(self._build_profile_card())
        layout.addWidget(self._build_purchase_card())
        layout.addWidget(self._build_interface_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)

    # ------------------------------------------------------------------ AI
    def _build_ai_card(self) -> Card:
        card = Card(tr("settings.ai_card"))

        self.current_row = QHBoxLayout()
        self.current_row_label = QLabel(tr("settings.api_key_current"))
        self.current_row_label.setObjectName("Muted")
        self.current_key_label = QLabel("-")
        self.current_key_label.setObjectName("SectionTitle")
        self.current_key_label.setTextInteractionFlags(
            self.current_key_label.textInteractionFlags().TextSelectableByMouse
        )
        self.delete_button = QPushButton(tr("settings.delete_key"))
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self.api_key_delete_requested.emit)
        self.current_row.addWidget(self.current_row_label)
        self.current_row.addWidget(self.current_key_label)
        self.current_row.addStretch(1)
        self.current_row.addWidget(self.delete_button)
        card.body().addLayout(self.current_row)

        self.key_form = ApiKeyForm(mode="settings")
        self.key_form.save_requested.connect(self.api_key_save_requested.emit)
        self.key_form.test_requested.connect(self.api_key_test_requested.emit)
        card.body().addWidget(self.key_form)

        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setVisible(False)
        card.body().addWidget(self.test_result)

        # 可编辑的模型设置（接口地址 / 模型 / 思考模式 / 思考强度）
        self.model_form = ModelSettingsForm()
        self.model_form.save_requested.connect(self.ai_settings_save_requested.emit)
        self.model_form.test_requested.connect(self.ai_settings_test_requested.emit)
        self.model_form.reset_requested.connect(self.ai_settings_reset_requested.emit)
        card.body().addWidget(self.model_form)

        self.env_wins_hint = muted("")
        self.env_wins_hint.setVisible(False)
        card.body().addWidget(self.env_wins_hint)

        form = QFormLayout()
        self.source_label = QLabel("-")
        self.timeout_label = QLabel("-")
        self.env_label = QLabel("-")
        self.db_label = QLabel("-")
        self.log_label = QLabel("-")
        for label in (
            self.source_label,
            self.timeout_label,
            self.env_label,
            self.db_label,
            self.log_label,
        ):
            label.setObjectName("Muted")
            label.setWordWrap(True)
            label.setTextInteractionFlags(label.textInteractionFlags().TextSelectableByMouse)
        form.addRow(tr("settings.key_source"), self.source_label)
        form.addRow(tr("settings.timeout"), self.timeout_label)
        form.addRow(tr("settings.env_files"), self.env_label)
        form.addRow(tr("settings.database"), self.db_label)
        form.addRow(tr("settings.logs"), self.log_label)
        card.body().addLayout(form)
        return card

    # ------------------------------------------------------------- 个人资料
    def _build_profile_card(self) -> Card:
        self.form = ProfileForm(title=tr("settings.profile_card"))
        row = QHBoxLayout()
        row.addStretch(1)
        self.save_button = QPushButton(tr("settings.save_profile"))
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.profile_saved.emit(self.form.profile()))
        row.addWidget(self.save_button)
        self.form.body().addLayout(row)
        return self.form

    # ------------------------------------------------------------- 界面设置
    def _build_purchase_card(self) -> Card:
        self.purchase_form = PurchaseForm()
        self.purchase_form.saved.connect(self.purchase_saved.emit)
        return self.purchase_form

    def _build_interface_card(self) -> Card:
        card = Card(tr("settings.interface_card"))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("settings.language")))
        self.language_combo = QComboBox()
        for code, label in available_languages().items():
            self.language_combo.addItem(label, code)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        row.addWidget(self.language_combo)
        row.addStretch(1)
        card.body().addLayout(row)
        self.language_hint = muted("")
        card.body().addWidget(self.language_hint)
        return card

    def _on_language_changed(self, _index: int) -> None:
        self.language_changed.emit(str(self.language_combo.currentData()))

    # ------------------------------------------------------------------ 关于
    def _build_about_card(self) -> Card:
        card = Card(tr("settings.about_card"))
        name = QLabel(tr("settings.about_name"))
        name.setObjectName("SectionTitle")
        version = QLabel(tr("settings.about_version", version=__version__))
        version.setObjectName("Muted")
        card.body().addWidget(name)
        card.body().addWidget(version)
        card.body().addWidget(muted(tr("settings.about_disclaimer")))
        card.body().addWidget(muted(tr("settings.about_full_disclaimer")))
        return card

    # ------------------------------------------------------------------ 状态
    def set_profile(self, profile: UserProfile | None) -> None:
        self.form.set_profile(profile)

    def set_purchase_preferences(self, preferences: object) -> None:
        self.purchase_form.set_preferences(preferences)  # type: ignore[arg-type]

    def set_purchase_status(self, message: str, *, ok: bool = True) -> None:
        self.purchase_form.set_status(message, ok=ok)

    def set_status(self, status: ApiStatus) -> None:
        self.current_key_label.setText(
            status.masked_key if status.has_key else tr("settings.key_not_set")
        )
        self.delete_button.setEnabled(status.has_key)
        self.source_label.setText(status.key_source)
        self.timeout_label.setText(tr("settings.timeout_value", seconds=status.timeout))
        self.model_form.set_values(
            base_url=status.base_url,
            model=status.model,
            thinking=status.thinking,
            reasoning_effort=status.reasoning_effort,
        )
        self.env_wins_hint.setText(tr("settings.env_wins_hint") if status.env_overrides else "")
        self.env_wins_hint.setVisible(status.env_overrides)
        self.env_label.setText(
            "\n".join(status.env_files) if status.env_files else tr("settings.env_files_none")
        )
        self.db_label.setText(status.database_path)
        self.log_label.setText(status.log_directory)

    def set_language_selection(self, code: str) -> None:
        index = self.language_combo.findData(code)
        if index >= 0 and index != self.language_combo.currentIndex():
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(index)
            self.language_combo.blockSignals(False)

    def set_language_hint(self, message: str) -> None:
        self.language_hint.setText(message)

    def set_test_result(self, message: str, *, ok: bool = True) -> None:
        self.test_result.setText(message)
        self.test_result.setObjectName("Ok" if ok else "Warning")
        self.test_result.setVisible(bool(message))
        self.test_result.style().unpolish(self.test_result)
        self.test_result.style().polish(self.test_result)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.key_form.set_busy(busy, message)
        self.model_form.set_busy(busy, message)
        self.delete_button.setEnabled(not busy and self.delete_button.isEnabled())
        self.save_button.setEnabled(not busy)
        self.purchase_form.set_busy(busy)

    def clear_key_input(self) -> None:
        self.key_form.clear_input()

    def focus_key_input(self) -> None:
        self.key_form.focus_input()
