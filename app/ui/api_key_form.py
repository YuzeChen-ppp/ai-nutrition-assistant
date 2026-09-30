"""API Key 输入组件与「首次启动」欢迎页。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ui.widgets import Card, LoadingBar, SectionTitle, muted

__all__ = ["ApiKeyForm", "WelcomePage"]


class ApiKeyForm(Card):
    """API Key 输入框：默认打码显示，可切换「显示 / 隐藏」。"""

    save_requested = Signal(str)
    test_requested = Signal(str)
    test_and_save_requested = Signal(str)

    def __init__(self, *, mode: str = "settings", parent: QWidget | None = None) -> None:
        super().__init__(None, parent=parent)
        self.mode = mode

        label = QLabel(tr("apikey.label"))
        label.setObjectName("SectionTitle")
        self.body().addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.setPlaceholderText(tr("apikey.placeholder"))
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self._on_primary)
        row.addWidget(self.input, 1)

        self.toggle_button = QPushButton(tr("apikey.show"))
        self.toggle_button.setCheckable(True)
        self.toggle_button.setFixedWidth(72)
        self.toggle_button.toggled.connect(self._toggle_visibility)
        row.addWidget(self.toggle_button)
        self.body().addLayout(row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if mode == "welcome":
            self.submit_button = QPushButton(tr("apikey.test_and_save"))
            self.submit_button.setObjectName("Primary")
            self.submit_button.clicked.connect(self._on_primary)
            buttons.addWidget(self.submit_button)
        else:
            self.save_button = QPushButton(tr("apikey.save"))
            self.save_button.setObjectName("Primary")
            self.save_button.clicked.connect(
                lambda: self.save_requested.emit(self.key_text())
            )
            self.test_button = QPushButton(tr("apikey.test"))
            self.test_button.clicked.connect(
                lambda: self.test_requested.emit(self.key_text())
            )
            buttons.addWidget(self.save_button)
            buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        self.loading = LoadingBar()
        buttons.addWidget(self.loading)
        self.body().addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.body().addWidget(self.status_label)

        self.security_label = muted(tr("settings.security_hint"))
        self.body().addWidget(self.security_label)

    # ---------------------------------------------------------------- 行为
    def _on_primary(self) -> None:
        key = self.key_text()
        if self.mode == "welcome":
            self.test_and_save_requested.emit(key)
        else:
            self.save_requested.emit(key)

    def _toggle_visibility(self, visible: bool) -> None:
        self.input.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.toggle_button.setText(tr("apikey.hide") if visible else tr("apikey.show"))
        if not visible:
            self.input.setFocus()

    # ---------------------------------------------------------------- 状态
    def key_text(self) -> str:
        return self.input.text().strip()

    def clear_input(self) -> None:
        self.input.clear()

    def focus_input(self) -> None:
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_status(self, message: str, *, ok: bool = True) -> None:
        self.status_label.setText(message)
        self.status_label.setObjectName("Ok" if ok else "Warning")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setVisible(bool(message))

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.input.setEnabled(not busy)
        for button in self.findChildren(QPushButton):
            button.setEnabled(not busy)
        if busy:
            self.loading.start(message or tr("apikey.testing"))
        else:
            self.loading.stop()

    def hide_security_hint(self) -> None:
        self.security_label.setVisible(False)


class WelcomePage(QWidget):
    """首次启动：还没有 API Key 时显示的引导页。"""

    submit_requested = Signal(str)
    skip_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)

        title = SectionTitle(tr("welcome.title"))
        title.setStyleSheet("font-size: 22px;")
        layout.addWidget(title)
        layout.addWidget(muted(tr("welcome.subtitle")))
        layout.addWidget(muted(tr("welcome.where_to_get")))

        self.form = ApiKeyForm(mode="welcome")
        self.form.test_and_save_requested.connect(self.submit_requested.emit)
        layout.addWidget(self.form)

        layout.addWidget(muted(tr("welcome.privacy")))
        layout.addWidget(muted(tr("welcome.skip_hint")))

        buttons = QHBoxLayout()
        self.skip_button = QPushButton(tr("welcome.skip"))
        self.skip_button.clicked.connect(self.skip_requested.emit)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    # ---------------------------------------------------------------- 状态
    def set_busy(self, busy: bool, message: str = "") -> None:
        self.form.set_busy(busy, message)
        self.skip_button.setEnabled(not busy)

    def set_status(self, message: str, *, ok: bool = True) -> None:
        self.form.set_status(message, ok=ok)

    def clear_input(self) -> None:
        self.form.clear_input()

    def focus_input(self) -> None:
        self.form.focus_input()
