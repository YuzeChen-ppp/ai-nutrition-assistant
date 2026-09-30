"""「AI 模型设置」表单：接口地址、模型、思考模式、思考强度。

表单只负责收集与展示，不碰网络也不写数据库：它把用户填的内容打包成
:class:`StoredAiSettings` 通过信号发给主窗口，由主窗口去保存或测试。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QWidget,
)

from app.ai.model_catalog import (
    DEFAULT_REASONING_EFFORT,
    KNOWN_MODELS,
    REASONING_EFFORTS,
    capability_for,
    normalise_effort,
)
from app.i18n import tr
from app.ui.widgets import Card, LoadingBar, muted
from app.utils.app_settings_store import StoredAiSettings
from app.utils.config import DEFAULT_BASE_URL, normalise_thinking

__all__ = ["ModelSettingsForm"]

_EFFORT_LABEL_KEYS = {
    "low": "settings.effort_low",
    "medium": "settings.effort_medium",
    "high": "settings.effort_high",
}


class ModelSettingsForm(Card):
    """可编辑的 AI 设置卡片。"""

    save_requested = Signal(object)  # StoredAiSettings
    test_requested = Signal(object)  # StoredAiSettings
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("settings.ai_model_card"), parent=parent)
        # 用户“想不想要思考”和“想要多用力”，与当前模型是否支持无关：
        # 切到不支持的模型时只是灰掉，切回来要能恢复。
        self._thinking_preference = "disabled"
        self._effort_preference = DEFAULT_REASONING_EFFORT
        self._updating = False

        form = QFormLayout()
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(DEFAULT_BASE_URL)
        self.base_url_edit.setClearButtonEnabled(True)
        form.addRow(tr("settings.base_url"), self.base_url_edit)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # 允许手输，兼容将来新增的模型
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.addItems(KNOWN_MODELS)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        if self.model_combo.lineEdit() is not None:
            self.model_combo.lineEdit().setPlaceholderText(tr("settings.model_placeholder"))
        form.addRow(tr("settings.model"), self.model_combo)
        self.body().addLayout(form)

        self.thinking_checkbox = QCheckBox(tr("settings.thinking_toggle"))
        self.thinking_checkbox.toggled.connect(self._on_thinking_toggled)
        self.body().addWidget(self.thinking_checkbox)

        effort_row = QHBoxLayout()
        effort_row.addWidget(QLabel(tr("settings.reasoning_effort")))
        self.effort_slider = QSlider(Qt.Orientation.Horizontal)
        self.effort_slider.setRange(0, len(REASONING_EFFORTS) - 1)
        self.effort_slider.setSingleStep(1)
        self.effort_slider.setPageStep(1)
        self.effort_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.effort_slider.setTickInterval(1)
        self.effort_slider.setMinimumWidth(180)
        self.effort_slider.valueChanged.connect(self._on_effort_changed)
        effort_row.addWidget(self.effort_slider, 1)
        self.effort_label = QLabel("")
        effort_row.addWidget(self.effort_label)
        self.body().addLayout(effort_row)

        self.effort_hint = muted(tr("settings.reasoning_effort_hint"))
        self.body().addWidget(self.effort_hint)

        self.hint_label = muted("")
        self.body().addWidget(self.hint_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = QPushButton(tr("settings.ai_save"))
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.values()))
        self.test_button = QPushButton(tr("settings.ai_test"))
        self.test_button.clicked.connect(lambda: self.test_requested.emit(self.values()))
        self.reset_button = QPushButton(tr("settings.ai_reset"))
        self.reset_button.clicked.connect(self.reset_requested.emit)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        self.loading = LoadingBar()
        buttons.addWidget(self.loading)
        self.body().addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        self.body().addWidget(self.status_label)

        self._refresh_enabled_state()

    # -------------------------------------------------------------- 读取状态
    def model_text(self) -> str:
        return self.model_combo.currentText().strip()

    def effort_level(self) -> str:
        return REASONING_EFFORTS[self.effort_slider.value()]

    def set_effort_level(self, level: str) -> None:
        index = REASONING_EFFORTS.index(normalise_effort(level))
        self.effort_slider.blockSignals(True)
        self.effort_slider.setValue(index)
        self.effort_slider.blockSignals(False)
        self._update_effort_label()

    def values(self) -> StoredAiSettings:
        """当前表单内容；模型不支持思考时，思考一律按“关闭”上报。"""
        capability = capability_for(self.model_text())
        thinking = (
            "enabled"
            if capability.supports_thinking and self.thinking_checkbox.isChecked()
            else "disabled"
        )
        return StoredAiSettings(
            base_url=self.base_url_edit.text().strip(),
            model=self.model_text(),
            thinking=thinking,
            reasoning_effort=self.effort_level(),
        )

    # -------------------------------------------------------------- 写入状态
    def set_values(
        self,
        *,
        base_url: str | None,
        model: str | None,
        thinking: str | None,
        reasoning_effort: str | None,
    ) -> None:
        """用已生效的设置刷新表单（保存/重置/刷新后调用）。"""
        self._updating = True
        try:
            self.base_url_edit.setText((base_url or "").strip())
            self._thinking_preference = normalise_thinking(thinking)
            self._effort_preference = normalise_effort(reasoning_effort)
            if model and self.model_text() != model:
                self.model_combo.setCurrentText(model)
            self.thinking_checkbox.setChecked(self._thinking_preference == "enabled")
            self.set_effort_level(self._effort_preference)
        finally:
            self._updating = False
        self._refresh_enabled_state()

    def set_status(self, message: str, *, ok: bool = True) -> None:
        self.status_label.setText(message)
        self.status_label.setObjectName("Ok" if ok else "Warning")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setVisible(bool(message))

    def set_busy(self, busy: bool, message: str = "") -> None:
        for widget in (
            self.base_url_edit,
            self.model_combo,
            self.thinking_checkbox,
            self.effort_slider,
            self.save_button,
            self.test_button,
            self.reset_button,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.loading.start(message or tr("settings.testing"))
        else:
            self.loading.stop()
            # 重新按模型能力决定哪些控件可用。
            self._refresh_enabled_state()

    # ------------------------------------------------------------------ 联动
    def _refresh_enabled_state(self) -> None:
        """模型不支持思考 -> 灰掉开关并说明原因（不静默修改用户的偏好）。"""
        supported = capability_for(self.model_text()).supports_thinking
        # 先设 enabled 再 setChecked：这样程序化的 setChecked 不会被当成用户操作。
        self.thinking_checkbox.setEnabled(supported)
        self.thinking_checkbox.setChecked(supported and self._thinking_preference == "enabled")
        self.hint_label.setText("" if supported else tr("settings.model_no_thinking"))
        self._refresh_effort_enabled()

    def _refresh_effort_enabled(self) -> None:
        thinking_on = self.thinking_checkbox.isEnabled() and self.thinking_checkbox.isChecked()
        for widget in (self.effort_slider, self.effort_label, self.effort_hint):
            widget.setEnabled(thinking_on)
        self._update_effort_label()

    def _update_effort_label(self) -> None:
        self.effort_label.setText(tr(_EFFORT_LABEL_KEYS[self.effort_level()]))

    def _on_model_changed(self, _text: str) -> None:
        if self._updating:
            return
        self._refresh_enabled_state()

    def _on_thinking_toggled(self, checked: bool) -> None:
        if self._updating or not self.thinking_checkbox.isEnabled():
            return  # 程序化降级，不是用户的选择
        self._thinking_preference = "enabled" if checked else "disabled"
        self._refresh_effort_enabled()

    def _on_effort_changed(self, _value: int) -> None:
        self._update_effort_label()
