"""可复用界面组件（全部文案来自 app/translations.py）。"""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ai.schemas import FoodItem
from app.database.models import MealRecord
from app.i18n import tr
from app.nutrition.calculator import progress_fraction
from app.utils.validation import ValidationError, coerce_number

__all__ = [
    "Card",
    "Chip",
    "FoodItemsTable",
    "LoadingBar",
    "MealSection",
    "NutritionBar",
    "SectionTitle",
    "meal_type_title",
    "muted",
]

MEAL_TYPE_ORDER: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")


def meal_type_title(meal_type: str) -> str:
    """「早餐」/「午餐」/「晚餐」/「加餐」."""
    if meal_type in MEAL_TYPE_ORDER:
        return tr(f"meal.{meal_type}")
    return meal_type


def muted(text: str, *, parent: QWidget | None = None) -> QLabel:
    """灰色小字说明。"""
    label = QLabel(text, parent)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def SectionTitle(text: str, *, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("SectionTitle")
    return label


class Card(QFrame):
    """白色圆角面板，可带标题。"""

    def __init__(self, title: str | None = None, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(10)
        self.title_label: QLabel | None = None
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("CardTitle")
            self.header.addWidget(self.title_label)
            self.header.addStretch(1)
            self._layout.addLayout(self.header)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget)

    def set_title(self, text: str) -> None:
        if self.title_label is not None:
            self.title_label.setText(text)


class Chip(QLabel):
    """小徽标（例如 AI 置信度）。"""

    def __init__(
        self,
        text: str = "",
        *,
        warning: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("ChipWarn" if warning else "Chip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_state(self, text: str, *, warning: bool = False) -> None:
        self.setText(text)
        self.setObjectName("ChipWarn" if warning else "Chip")
        self.style().unpolish(self)
        self.style().polish(self)


class NutritionBar(QWidget):
    """一行营养素：名称、已摄入/目标、进度条、提示。"""

    def __init__(
        self,
        title: str,
        *,
        unit: str = "g",
        is_limit: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.unit = unit
        self.is_limit = is_limit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")
        self.value_label = QLabel("-")
        self.value_label.setObjectName("Muted")
        self.value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.value_label)
        layout.addLayout(header)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        layout.addWidget(self.bar)

        self.note_label = QLabel("")
        self.note_label.setObjectName("Muted")
        self.note_label.setVisible(False)
        layout.addWidget(self.note_label)

    def set_values(self, consumed: float, target: float) -> None:
        consumed = float(consumed or 0.0)
        target = float(target or 0.0)
        self.bar.setValue(int(round(progress_fraction(consumed, target) * 1000)))

        digits = 0 if self.unit == tr("nutrient.unit_mg") else 1
        if target > 0:
            self.value_label.setText(
                f"{consumed:,.{digits}f} / {target:,.{digits}f} {self.unit}"
            )
        else:
            self.value_label.setText(f"{consumed:,.{digits}f} {self.unit}")

        over = target > 0 and consumed > target
        state = "Over" if over else ("Ok" if target > 0 and consumed / target >= 0.9 else "")
        if self.bar.objectName() != state:
            self.bar.setObjectName(state)
            self.bar.style().unpolish(self.bar)
            self.bar.style().polish(self.bar)

        if over and target > 0:
            self.note_label.setText(
                tr(
                    "calc.above_limit",
                    value=f"{consumed - target:,.{digits}f}",
                    unit=self.unit,
                )
            )
            self.note_label.setVisible(True)
            self.note_label.setObjectName("Warning")
        elif self.is_limit and target > 0 and consumed > target * 0.9:
            self.note_label.setText(tr("calc.near_limit"))
            self.note_label.setVisible(True)
            self.note_label.setObjectName("Muted")
        else:
            self.note_label.setVisible(False)
        self.note_label.style().unpolish(self.note_label)
        self.note_label.style().polish(self.note_label)


class LoadingBar(QWidget):
    """等待提示（不确定进度条），避免用户以为程序卡死。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.label = QLabel("")
        self.label.setObjectName("Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 不确定进度
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(160)
        self.bar.setFixedHeight(10)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addStretch(1)
        self.setVisible(False)

    def start(self, message: str) -> None:
        self.label.setText(message)
        self.setVisible(True)

    def stop(self) -> None:
        self.setVisible(False)
        self.label.setText("")


class FoodItemsTable(QTableWidget):
    """食物表格：用于确认页与推荐页。"""

    COLUMNS: tuple[tuple[str, str], ...] = (
        ("name", "table.food"),
        ("amount", "table.amount"),
        ("unit", "table.unit"),
        ("estimated_weight_g", "table.weight_g"),
        ("calories_kcal", "table.calories"),
        ("protein_g", "table.protein_g"),
        ("carbs_g", "table.carbs_g"),
        ("fat_g", "table.fat_g"),
        ("sugar_g", "table.sugar_g"),
        ("fibre_g", "table.fibre_g"),
        ("sodium_mg", "table.sodium_mg"),
        ("confidence", "table.confidence"),
    )

    _NUMERIC_FIELDS = (
        "amount",
        "estimated_weight_g",
        "calories_kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sugar_g",
        "fibre_g",
        "sodium_mg",
    )

    def __init__(self, *, editable: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(0, len(self.COLUMNS), parent)
        self.setHorizontalHeaderLabels([tr(key) for _, key in self.COLUMNS])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        triggers = QTableWidget.EditTrigger.NoEditTriggers
        if editable:
            triggers = (
                QTableWidget.EditTrigger.DoubleClicked
                | QTableWidget.EditTrigger.SelectedClicked
                | QTableWidget.EditTrigger.EditKeyPressed
            )
        self.setEditTriggers(triggers)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(60)
        self.setMinimumHeight(180)

    # ------------------------------------------------------------------ data
    def set_items(self, items: Sequence[Any]) -> None:
        self.setRowCount(0)
        for item in items:
            self._append(item)

    def _append(self, item: Any) -> None:
        row = self.rowCount()
        self.insertRow(row)
        for column, (field, _) in enumerate(self.COLUMNS):
            value = _read_field(item, field)
            self.setItem(row, column, QTableWidgetItem(_format_cell(field, value)))

    def items(self) -> tuple[list[FoodItem], list[str]]:
        """把表格内容读回成经过校验的 :class:`FoodItem`。

        返回 ``(items, problems)``，``problems`` 是给用户看的中文提示。
        """
        items: list[FoodItem] = []
        problems: list[str] = []
        labels = {field: tr(key) for field, key in self.COLUMNS}
        for row in range(self.rowCount()):
            payload: dict[str, Any] = {}
            for column, (field, _) in enumerate(self.COLUMNS):
                cell = self.item(row, column)
                text = cell.text().strip() if cell is not None else ""
                if field == "confidence":
                    cleaned = text.replace("%", "").strip()
                    payload[field] = cleaned if cleaned else None
                elif field in self._NUMERIC_FIELDS:
                    try:
                        payload[field] = coerce_number(
                            text, field=labels.get(field, field), allow_none=True
                        )
                    except ValidationError as exc:
                        problems.append(f"第 {row + 1} 行：{exc}")
                        payload[field] = None
                else:
                    payload[field] = text or None
            try:
                items.append(FoodItem.model_validate(payload))
            except Exception as exc:  # noqa: BLE001 - Pydantic 校验错误
                label = payload.get("name") or tr("table.food")
                problems.append(f"第 {row + 1} 行（{label}）：{exc}")
        return items, problems

    def remove_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self.removeRow(row)


class MealSection(QFrame):
    """一条饮食记录：标题、食物列表、编辑/删除按钮。"""

    edit_meal = Signal(int)
    delete_meal = Signal(int)
    edit_item = Signal(int)
    delete_item = Signal(int)

    def __init__(self, meal: MealRecord, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.meal = meal
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        totals = meal.totals()
        header = QHBoxLayout()
        header.setSpacing(8)
        time_label = QLabel(meal.time or "--:--")
        time_label.setObjectName("Muted")
        header.addWidget(time_label)
        header.addStretch(1)
        summary = QLabel(
            f"{totals.calories_kcal:,.0f} kcal ・ "
            f"{tr('nutrient.protein')} {totals.protein_g:,.1f} ・ "
            f"{tr('nutrient.carbs')} {totals.carbs_g:,.1f} ・ "
            f"{tr('nutrient.fat')} {totals.fat_g:,.1f}"
        )
        summary.setObjectName("Muted")
        header.addWidget(summary)
        edit_button = QPushButton(tr("meal.edit"))
        edit_button.setObjectName("Link")
        edit_button.setToolTip(tr("meal.edit_tip"))
        edit_button.clicked.connect(lambda: self.edit_meal.emit(self.meal.id))
        delete_button = QPushButton(tr("meal.delete"))
        delete_button.setObjectName("Link")
        delete_button.clicked.connect(lambda: self.delete_meal.emit(self.meal.id))
        header.addWidget(edit_button)
        header.addWidget(delete_button)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for row, item in enumerate(meal.items):
            name = QLabel(item.name)
            name.setMinimumWidth(110)
            grid.addWidget(name, row, 0)
            portion = QLabel(item.portion_label())
            portion.setObjectName("Muted")
            grid.addWidget(portion, row, 1)
            kcal = QLabel(f"{item.calories_kcal:,.0f} kcal")
            grid.addWidget(kcal, row, 2)
            macros = QLabel(
                f"{tr('nutrient.protein')} {item.protein_g:,.1f} ・ "
                f"{tr('nutrient.carbs')} {item.carbs_g:,.1f} ・ "
                f"{tr('nutrient.fat')} {item.fat_g:,.1f}"
            )
            macros.setObjectName("Muted")
            grid.addWidget(macros, row, 3)
            grid.addWidget(
                Chip(
                    _confidence_text(item.confidence),
                    warning=_confidence_is_low(item.confidence),
                ),
                row,
                4,
            )
            edit_item = QPushButton(tr("meal.edit_item"))
            edit_item.setObjectName("Link")
            edit_item.clicked.connect(
                lambda _=False, item_id=item.id: self.edit_item.emit(item_id)
            )
            grid.addWidget(edit_item, row, 5)
            delete_item = QPushButton(tr("meal.delete_item"))
            delete_item.setObjectName("Link")
            delete_item.clicked.connect(
                lambda _=False, item_id=item.id: self.delete_item.emit(item_id)
            )
            grid.addWidget(delete_item, row, 6)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(3, 3)
        layout.addLayout(grid)

        if meal.source == "image":
            layout.addWidget(muted(tr("mealsource.image")))
        elif meal.source == "local_db":
            layout.addWidget(muted(tr("mealsource.local_db")))
        if meal.note:
            layout.addWidget(muted(tr("meal.note", note=meal.note)))


def _read_field(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _format_cell(field: str, value: Any) -> str:
    if value is None:
        return ""
    if field == "confidence":
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (int, float)):
        if field in {"amount", "estimated_weight_g"}:
            return f"{float(value):g}"
        if field == "sodium_mg":
            return f"{float(value):,.0f}"
        return f"{float(value):,.1f}"
    return str(value)


def _confidence_text(confidence: float | None) -> str:
    if confidence is None:
        return tr("table.confidence_unknown")
    return tr("table.confidence_value", percent=f"{confidence * 100:.0f}")


def _confidence_is_low(confidence: float | None) -> bool:
    return confidence is not None and confidence < 0.5
