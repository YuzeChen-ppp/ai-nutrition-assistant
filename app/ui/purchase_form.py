"""「购买与饮食」设置卡片：告诉程序你现在方便买到什么。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.database.models import PurchasePreferences
from app.i18n import tr
from app.nutrition.purchase import (
    BUDGETS,
    COUNTRIES,
    DEFAULT_EASY_INGREDIENTS,
    EQUIPMENT_OPTIONS,
    STORE_OPTIONS,
    split_ingredient_text,
)
from app.ui.widgets import Card, muted

__all__ = ["PurchaseForm"]


class PurchaseForm(Card):
    """国家 / 超市 / 方便购买与否 / 预算 / 厨具 / 时间 / 饮食偏好。"""

    saved = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("purchase.card_title"), parent=parent)
        body = self.body()
        body.addWidget(muted(tr("purchase.hint")))

        # ---------------------------------------------------------- 国家
        country_row = QHBoxLayout()
        country_row.addWidget(QLabel(tr("purchase.country")))
        self.country_combo = QComboBox()
        for code, label in COUNTRIES:
            self.country_combo.addItem(label, code)
        self.country_combo.setMinimumWidth(180)
        country_row.addWidget(self.country_combo)
        country_row.addStretch(1)
        body.addLayout(country_row)

        # ---------------------------------------------------------- 超市
        body.addWidget(_label(tr("purchase.stores")))
        self.store_boxes: dict[str, QCheckBox] = {}
        store_grid = QGridLayout()
        store_grid.setHorizontalSpacing(18)
        for index, name in enumerate(STORE_OPTIONS):
            box = QCheckBox(name)
            self.store_boxes[name] = box
            store_grid.addWidget(box, index // 3, index % 3)
        body.addLayout(store_grid)
        self.store_other = QLineEdit()
        self.store_other.setPlaceholderText(tr("purchase.store_other_placeholder"))
        other_row = QHBoxLayout()
        other_row.addWidget(QLabel(tr("purchase.store_other")))
        other_row.addWidget(self.store_other, 1)
        body.addLayout(other_row)
        body.addWidget(muted(tr("purchase.stores_hint")))

        # ------------------------------------------------- 方便购买与否
        body.addWidget(_label(tr("purchase.easy_label")))
        self.easy_edit = QPlainTextEdit()
        self.easy_edit.setPlaceholderText(tr("purchase.easy_placeholder"))
        self.easy_edit.setFixedHeight(72)
        body.addWidget(self.easy_edit)
        body.addWidget(muted(tr("purchase.easy_hint")))
        self.easy_preview = QLabel("")
        self.easy_preview.setObjectName("Muted")
        self.easy_preview.setWordWrap(True)
        body.addWidget(self.easy_preview)

        body.addWidget(_label(tr("purchase.hard_label")))
        self.hard_edit = QPlainTextEdit()
        self.hard_edit.setPlaceholderText(tr("purchase.hard_placeholder"))
        self.hard_edit.setFixedHeight(60)
        body.addWidget(self.hard_edit)
        body.addWidget(muted(tr("purchase.hard_hint")))
        self.hard_preview = QLabel("")
        self.hard_preview.setObjectName("Muted")
        self.hard_preview.setWordWrap(True)
        body.addWidget(self.hard_preview)

        # ---------------------------------------------------------- 预算
        budget_row = QHBoxLayout()
        budget_row.addWidget(QLabel(tr("purchase.budget")))
        self.budget_combo = QComboBox()
        for code, label in BUDGETS:
            self.budget_combo.addItem(label, code)
        self.budget_combo.setMinimumWidth(140)
        budget_row.addWidget(self.budget_combo)
        budget_row.addStretch(1)
        body.addLayout(budget_row)

        # ---------------------------------------------------------- 厨具
        body.addWidget(_label(tr("purchase.equipment")))
        self.equipment_boxes: dict[str, QCheckBox] = {}
        equipment_grid = QGridLayout()
        equipment_grid.setHorizontalSpacing(18)
        for index, name in enumerate(EQUIPMENT_OPTIONS):
            box = QCheckBox(name)
            self.equipment_boxes[name] = box
            equipment_grid.addWidget(box, index // 3, index % 3)
        body.addLayout(equipment_grid)
        self.equipment_other = QLineEdit()
        self.equipment_other.setPlaceholderText(tr("purchase.equipment_placeholder"))
        equipment_row = QHBoxLayout()
        equipment_row.addWidget(QLabel(tr("purchase.equipment_other")))
        equipment_row.addWidget(self.equipment_other, 1)
        body.addLayout(equipment_row)

        # ------------------------------------------------------ 时间 / 偏好
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel(tr("purchase.max_time")))
        self.time_spin = QSpinBox()
        self.time_spin.setRange(0, 240)
        self.time_spin.setSingleStep(5)
        self.time_spin.setSpecialValueText(tr("purchase.time_unlimited"))
        self.time_spin.setSuffix(" 分钟")
        self.time_spin.setMinimumWidth(120)
        time_row.addWidget(self.time_spin)
        time_row.addStretch(1)
        body.addLayout(time_row)

        diet_row = QHBoxLayout()
        diet_row.addWidget(QLabel(tr("purchase.diet")))
        self.diet_edit = QLineEdit()
        self.diet_edit.setPlaceholderText(tr("purchase.diet_placeholder"))
        diet_row.addWidget(self.diet_edit, 1)
        body.addLayout(diet_row)

        # ---------------------------------------------------------- 保存
        footer = QHBoxLayout()
        self.save_button = QPushButton(tr("purchase.save"))
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.saved.emit(self.preferences()))
        footer.addWidget(self.save_button)
        footer.addStretch(1)
        body.addLayout(footer)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Ok")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        body.addWidget(self.status_label)

        self.easy_edit.textChanged.connect(self._update_previews)
        self.hard_edit.textChanged.connect(self._update_previews)
        self._update_previews()

    # ---------------------------------------------------------------- 数据
    def preferences(self) -> PurchasePreferences:
        return PurchasePreferences(
            country=str(self.country_combo.currentData() or ""),
            stores=self._checked(self.store_boxes) + self._extra(self.store_other),
            easy_text=self.easy_edit.toPlainText().strip(),
            hard_text=self.hard_edit.toPlainText().strip(),
            budget=str(self.budget_combo.currentData() or "normal"),
            equipment=self._checked(self.equipment_boxes) + self._extra(self.equipment_other),
            max_cooking_minutes=int(self.time_spin.value()),
            diet_preferences=self.diet_edit.text().strip(),
        )

    def set_preferences(self, preferences: PurchasePreferences | None) -> None:
        if preferences is None:
            return
        index = self.country_combo.findData(preferences.country)
        self.country_combo.setCurrentIndex(max(0, index))

        known_stores = set(STORE_OPTIONS)
        for name, box in self.store_boxes.items():
            box.setChecked(name in preferences.stores)
        custom_stores = [name for name in preferences.stores if name not in known_stores]
        self.store_other.setText("、".join(custom_stores))

        self.easy_edit.setPlainText(preferences.easy_text)
        self.hard_edit.setPlainText(preferences.hard_text)

        budget_index = self.budget_combo.findData(preferences.budget)
        self.budget_combo.setCurrentIndex(max(0, budget_index))

        known_equipment = set(EQUIPMENT_OPTIONS)
        for name, box in self.equipment_boxes.items():
            box.setChecked(name in preferences.equipment)
        custom_equipment = [
            name for name in preferences.equipment if name not in known_equipment
        ]
        self.equipment_other.setText("、".join(custom_equipment))

        self.time_spin.setValue(int(preferences.max_cooking_minutes or 0))
        self.diet_edit.setText(preferences.diet_preferences)
        self._update_previews()

    def set_status(self, message: str, *, ok: bool = True) -> None:
        self.status_label.setText(message)
        self.status_label.setObjectName("Ok" if ok else "Warning")
        self.status_label.setVisible(bool(message))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def set_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)

    # -------------------------------------------------------------- 内部
    def _checked(self, boxes: dict[str, QCheckBox]) -> list[str]:
        return [name for name, box in boxes.items() if box.isChecked()]

    def _extra(self, line_edit: QLineEdit) -> list[str]:
        return split_ingredient_text(line_edit.text())

    def _update_previews(self) -> None:
        easy = split_ingredient_text(self.easy_edit.toPlainText())
        hard = split_ingredient_text(self.hard_edit.toPlainText())
        if easy:
            sample = "、".join(easy[:8]) + ("…" if len(easy) > 8 else "")
            self.easy_preview.setText(
                tr("purchase.parsed_easy", count=len(easy), sample=sample)
            )
        else:
            self.easy_preview.setText(
                tr("purchase.parsed_none")
                + "　"
                + tr(
                    "purchase.default_list",
                    items="、".join(DEFAULT_EASY_INGREDIENTS[:8]) + "…",
                )
            )
        if hard:
            sample = "、".join(hard[:8]) + ("…" if len(hard) > 8 else "")
            self.hard_preview.setText(
                tr("purchase.parsed_hard", count=len(hard), sample=sample)
            )
        else:
            self.hard_preview.setText(tr("purchase.parsed_none"))


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label
