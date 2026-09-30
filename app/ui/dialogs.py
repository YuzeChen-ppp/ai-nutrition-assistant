"""对话框：手动输入、照片预览、确认、编辑与推荐（全部中文）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ai.schemas import FoodItem, MealRecommendation
from app.database.models import FoodItemRecord, MealRecord
from app.i18n import tr
from app.nutrition.calculator import rescale_values, sanity_warnings, sum_food_items
from app.nutrition.food_data import compare_with_reference, lookup
from app.nutrition.targets import estimate_note
from app.ui.online_reference import OnlineReferenceCard
from app.ui.widgets import Card, Chip, FoodItemsTable, SectionTitle, meal_type_title, muted
from app.utils.validation import MEAL_TYPES, coerce_number

__all__ = [
    "ConfirmFoodDialog",
    "FoodItemEditDialog",
    "ManualEntryDialog",
    "MealEditDialog",
    "PhotoEntryDialog",
    "RecommendationDialog",
    "ask_confirmation",
    "show_error",
    "show_info",
]


def _meal_type_combo(current: str = "snack") -> QComboBox:
    combo = QComboBox()
    for meal_type in MEAL_TYPES:
        combo.addItem(meal_type_title(meal_type), meal_type)
    index = combo.findData(current if current in MEAL_TYPES else "snack")
    combo.setCurrentIndex(max(0, index))
    return combo


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText(tr("common.ok"))
    box.exec()


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText(tr("common.ok"))
    box.exec()


def ask_confirmation(parent: QWidget | None, title: str, message: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.button(QMessageBox.StandardButton.Yes).setText(tr("common.yes"))
    box.button(QMessageBox.StandardButton.No).setText(tr("common.no"))
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class ManualEntryDialog(QDialog):
    """手动输入这一餐吃了什么。"""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("manual.window_title"))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(SectionTitle(tr("manual.heading")))
        layout.addWidget(muted(tr("manual.hint")))

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(tr("manual.placeholder"))
        self.text_edit.setMinimumHeight(120)
        layout.addWidget(self.text_edit)

        form = QFormLayout()
        self.meal_type_combo = _meal_type_combo(_suggest_meal_type())
        form.addRow(tr("manual.meal_label"), self.meal_type_combo)
        layout.addLayout(form)

        layout.addWidget(muted(tr("manual.note")))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        analyse = buttons.button(QDialogButtonBox.StandardButton.Ok)
        analyse.setText(tr("manual.start"))
        analyse.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.text_edit.setFocus()

    def _on_accept(self) -> None:
        if not self.text().strip():
            show_error(self, tr("manual.empty_title"), tr("manual.empty_message"))
            return
        self.accept()

    def text(self) -> str:
        return self.text_edit.toPlainText().strip()

    def meal_type(self) -> str:
        return str(self.meal_type_combo.currentData())


class PhotoEntryDialog(QDialog):
    """照片预览 + 可选备注。"""

    def __init__(self, image_path: Path, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image_path = image_path
        self.setWindowTitle(tr("photo.window_title"))
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(SectionTitle(tr("photo.heading")))

        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            preview.setText(tr("photo.no_preview"))
        else:
            preview.setPixmap(
                pixmap.scaled(
                    520,
                    380,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        preview.setFrameShape(QFrame.Shape.StyledPanel)
        layout.addWidget(preview)

        form = QFormLayout()
        self.meal_type_combo = _meal_type_combo(_suggest_meal_type())
        form.addRow(tr("photo.meal_label"), self.meal_type_combo)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(tr("photo.note_placeholder"))
        form.addRow(tr("photo.note_label"), self.note_edit)
        layout.addLayout(form)

        layout.addWidget(muted(tr("photo.hint")))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        analyse = buttons.button(QDialogButtonBox.StandardButton.Ok)
        analyse.setText(tr("photo.start"))
        analyse.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def meal_type(self) -> str:
        return str(self.meal_type_combo.currentData())

    def note(self) -> str:
        return self.note_edit.text().strip()


class ConfirmFoodDialog(QDialog):
    """确认 AI 的识别结果，确认后才写入数据库。"""

    def __init__(
        self,
        items: Sequence[FoodItem],
        source: str = "manual",
        note: str = "",
        *,
        reference_job_factory: ReferenceJobFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("confirm.window_title"))
        self.setMinimumSize(1080, 700)
        self._source = source
        self._reference_job_factory = reference_job_factory

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.addWidget(SectionTitle(tr("confirm.heading")))
        heading.addStretch(1)
        heading.addWidget(Chip(tr("confirm.chip"), warning=True))
        layout.addLayout(heading)

        if note.strip():
            layout.addWidget(muted(tr("confirm.ai_note", note=note.strip())))
        layout.addWidget(muted(tr("confirm.hint") + " " + estimate_note()))

        self.table = FoodItemsTable(editable=False)
        self.table.set_items(items)
        self.table.itemChanged.connect(self._refresh_totals)
        layout.addWidget(self.table, 1)

        table_buttons = QHBoxLayout()
        self.edit_button = QPushButton(tr("confirm.edit_values"))
        self.edit_button.clicked.connect(self._toggle_editing)
        remove_button = QPushButton(tr("confirm.remove_row"))
        remove_button.clicked.connect(self._remove_row)
        table_buttons.addWidget(self.edit_button)
        table_buttons.addWidget(remove_button)
        table_buttons.addStretch(1)
        layout.addLayout(table_buttons)

        self.totals_label = QLabel("")
        self.totals_label.setObjectName("SectionTitle")
        self.totals_label.setWordWrap(True)
        layout.addWidget(self.totals_label)

        self.warnings_box = QLabel("")
        self.warnings_box.setObjectName("Warning")
        self.warnings_box.setWordWrap(True)
        layout.addWidget(self.warnings_box)

        self.reference_card = self._build_reference_card()
        layout.addWidget(self.reference_card)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        confirm.setText(tr("confirm.save"))
        confirm.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self._on_confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._items: list[FoodItem] = []
        self._refresh_totals()

    # ----------------------------------------------------- 在线参考（第二参考）
    def _build_reference_card(self) -> OnlineReferenceCard:
        """构造「查询在线参考」区域；这一块永远不会自动发起请求。"""
        self.reference_selector = QComboBox()
        items, _ = self.table.items()
        for index, item in enumerate(items):
            self.reference_selector.addItem(_food_row_label(item), index)
        self.reference_selector.setVisible(len(items) > 1)
        self.reference_selector.currentIndexChanged.connect(
            self._on_reference_row_changed
        )
        card = OnlineReferenceCard(
            item_provider=self._reference_item,
            name_provider=self._reference_name,
            job_factory=with_reference_sources(
                self._reference_job_factory, self._reference_item, self._reference_name
            ),
            parent=self,
        )
        if items:
            card.add_header_widget(self.reference_selector)
        return card

    def _on_reference_row_changed(self) -> None:
        # A new food means the old online numbers describe a different food:
        # hide them instead of showing a stale comparison.
        self.reference_card.clear_reference()

    def _refresh_reference_selection(self) -> None:
        """Keep the row picker in step with the (editable) table."""
        selector = getattr(self, "reference_selector", None)
        if selector is None:
            return
        current = selector.currentData()
        items, _ = self.table.items()
        selector.blockSignals(True)
        selector.clear()
        for index, item in enumerate(items):
            selector.addItem(_food_row_label(item), index)
        if isinstance(current, int) and 0 <= current < len(items):
            selector.setCurrentIndex(current)
        selector.blockSignals(False)
        selector.setVisible(len(items) > 1)

    def _reference_item(self) -> FoodItem | None:
        items, _ = self.table.items()
        selector = getattr(self, "reference_selector", None)
        index = selector.currentData() if selector is not None else 0
        if isinstance(index, int) and 0 <= index < len(items):
            return items[index]
        return items[0] if items else None

    def _reference_name(self) -> str:
        item = self._reference_item()
        return item.name if item is not None else ""

    # --------------------------------------------------------------- 行为
    def _toggle_editing(self) -> None:
        editable = self.table.editTriggers() == FoodItemsTable.EditTrigger.NoEditTriggers
        self.table.setEditTriggers(
            FoodItemsTable.EditTrigger.DoubleClicked
            | FoodItemsTable.EditTrigger.SelectedClicked
            | FoodItemsTable.EditTrigger.EditKeyPressed
            if editable
            else FoodItemsTable.EditTrigger.NoEditTriggers
        )
        self.edit_button.setText(
            tr("confirm.lock_values") if editable else tr("confirm.edit_values")
        )
        if editable:
            self.table.setFocus()

    def _remove_row(self) -> None:
        self.table.remove_selected_rows()
        self._refresh_totals()

    def _refresh_totals(self) -> None:
        items, problems = self.table.items()
        if hasattr(self, "_items"):
            # The table may have changed, so any online numbers on screen could
            # now belong to a different food: drop them instead of showing a
            # comparison that no longer matches the row.
            self.reference_card.clear_reference()
            self._refresh_reference_selection()
        totals = sum_food_items(items)
        self.totals_label.setText(
            tr(
                "confirm.estimated_total",
                kcal=f"{totals.calories_kcal:,.0f}",
                protein=f"{totals.protein_g:,.1f}",
                carbs=f"{totals.carbs_g:,.1f}",
                fat=f"{totals.fat_g:,.1f}",
                sugar=f"{totals.sugar_g:,.1f}",
                fibre=f"{totals.fibre_g:,.1f}",
                sodium=f"{totals.sodium_mg:,.0f}",
            )
        )

        messages = list(problems)
        messages.extend(sanity_warnings(items))
        for item in items:
            messages.extend(compare_with_reference(item.to_record_fields()))
        unique = list(dict.fromkeys(messages))
        if unique:
            self.warnings_box.setText(
                tr("confirm.warnings_header")
                + "\n".join(f"・{message}" for message in unique[:6])
            )
        else:
            self.warnings_box.setText("")

    def _on_confirm(self) -> None:
        items, problems = self.table.items()
        if problems:
            show_error(self, tr("confirm.check_values_title"), "\n".join(problems[:8]))
            return
        if not items:
            show_error(self, tr("confirm.check_values_title"), tr("confirm.need_item"))
            return
        self._items = items
        self.accept()

    def confirmed_items(self) -> list[FoodItem]:
        return list(self._items)

    def source(self) -> str:
        return self._source

    def closeEvent(self, event: Any) -> None:
        # The dialog can be closed while the online reference is still in
        # flight; the card then stops waiting for that answer.
        self.reference_card.shutdown()
        super().closeEvent(event)


class FoodItemEditDialog(QDialog):
    """编辑单项食物（不会调用 AI）。"""

    _FIELDS = (
        ("estimated_weight_g", "table.weight_g", 0.0, 20_000.0, 1.0),
        ("calories_kcal", "table.calories", 0.0, 10_000.0, 1.0),
        ("protein_g", "table.protein_g", 0.0, 1_000.0, 0.1),
        ("carbs_g", "table.carbs_g", 0.0, 1_500.0, 0.1),
        ("fat_g", "table.fat_g", 0.0, 1_000.0, 0.1),
        ("sugar_g", "table.sugar_g", 0.0, 1_500.0, 0.1),
        ("fibre_g", "table.fibre_g", 0.0, 500.0, 0.1),
        ("sodium_mg", "table.sodium_mg", 0.0, 50_000.0, 1.0),
    )

    def __init__(
        self,
        item: Any,
        *,
        reference_job_factory: ReferenceJobFactory | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("foodedit.window_title"))
        self.setMinimumWidth(470)
        self._original = item
        self._original_weight = _read(item, "estimated_weight_g")
        self._reference_job_factory = reference_job_factory

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_edit = QLineEdit(str(_read(item, "name") or ""))
        form.addRow(tr("foodedit.name"), self.name_edit)

        self.amount_spin = _spin(0.0, 100_000.0, 1.0, _read(item, "amount"))
        form.addRow(tr("foodedit.amount"), self.amount_spin)

        self.unit_edit = QLineEdit(str(_read(item, "unit") or ""))
        form.addRow(tr("foodedit.unit"), self.unit_edit)

        self.spins: dict[str, QDoubleSpinBox] = {}
        self.spins["estimated_weight_g"] = _spin(
            0.0, 20_000.0, 1.0, self._original_weight
        )
        form.addRow(tr("table.weight_g"), self.spins["estimated_weight_g"])

        self.rescale_checkbox = QCheckBox(tr("foodedit.rescale"))
        self.rescale_checkbox.setChecked(True)
        form.addRow("", self.rescale_checkbox)

        for field, label_key, minimum, maximum, step in self._FIELDS[1:]:
            spin = _spin(minimum, maximum, step, _read(item, field))
            self.spins[field] = spin
            form.addRow(tr(label_key), spin)

        self.cooking_edit = QLineEdit(str(_read(item, "cooking_method") or ""))
        form.addRow(tr("foodedit.cooking"), self.cooking_edit)

        confidence = _read(item, "confidence")
        self.confidence_spin = _spin(
            0.0, 1.0, 0.05, confidence if confidence is not None else 0.5
        )
        form.addRow(tr("foodedit.confidence"), self.confidence_spin)

        layout.addLayout(form)

        self.reference_label = muted("")
        layout.addWidget(self.reference_label)
        self._update_reference_hint()

        use_reference = QPushButton(tr("foodedit.use_reference"))
        use_reference.setObjectName("Link")
        use_reference.clicked.connect(self._apply_reference)
        layout.addWidget(use_reference)

        self.reference_card = OnlineReferenceCard(
            item_provider=self._current_item,
            name_provider=lambda: self.name_edit.text().strip(),
            job_factory=with_reference_sources(
                self._reference_job_factory,
                self._current_item,
                lambda: self.name_edit.text().strip(),
            ),
            parent=self,
        )
        layout.addWidget(self.reference_card)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("foodedit.save"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._last_weight = float(self._original_weight or 0.0)
        self.spins["estimated_weight_g"].editingFinished.connect(self._maybe_rescale)

    # --------------------------------------------------------------- 行为
    def _maybe_rescale(self) -> None:
        new_weight = float(self.spins["estimated_weight_g"].value())
        if not self.rescale_checkbox.isChecked() or self._last_weight <= 0:
            self._last_weight = new_weight
            return
        if abs(new_weight - self._last_weight) < 1e-6 or new_weight <= 0:
            self._last_weight = new_weight
            return
        values = rescale_values(
            {field: spin.value() for field, spin in self.spins.items()},
            original_weight_g=self._last_weight,
            new_weight_g=new_weight,
        )
        for field, value in values.items():
            if field in self.spins:
                self.spins[field].setValue(float(value))
        self._last_weight = new_weight
        self._update_reference_hint()

    def _reference(self) -> tuple[Any, dict[str, float] | None]:
        reference = lookup(self.name_edit.text())
        if reference is None:
            return None, None
        weight = float(self.spins["estimated_weight_g"].value())
        if weight <= 0:
            return reference, None
        return reference, reference.for_weight(weight)

    def _update_reference_hint(self) -> None:
        reference, values = self._reference()
        if reference is None:
            self.reference_label.setText(tr("foodedit.reference_none"))
            return
        weight = float(self.spins["estimated_weight_g"].value())
        if values is None:
            self.reference_label.setText(
                tr(
                    "foodedit.reference_per_100g",
                    name=reference.name,
                    kcal=f"{reference.per_100g['calories_kcal']:.0f}",
                )
            )
            return
        self.reference_label.setText(
            tr(
                "foodedit.reference_for_weight",
                weight=f"{weight:g}",
                name=reference.name,
                kcal=f"{values['calories_kcal']:.0f}",
                protein=f"{values['protein_g']:.1f}",
                carbs=f"{values['carbs_g']:.1f}",
                fat=f"{values['fat_g']:.1f}",
            )
        )

    def _apply_reference(self) -> None:
        reference, values = self._reference()
        if reference is None or values is None:
            show_error(
                self, tr("foodedit.no_reference_title"), tr("foodedit.no_reference")
            )
            return
        for field, value in values.items():
            if field in self.spins:
                self.spins[field].setValue(float(value))
        self._update_reference_hint()

    def _current_item(self) -> FoodItem:
        """The item as it is on screen right now (used for the online reference)."""
        return self.item()

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            show_error(
                self, tr("foodedit.name_required_title"), tr("foodedit.name_required")
            )
            return
        self.accept()

    def closeEvent(self, event: Any) -> None:
        # Same as the confirmation dialog: a late answer must not touch a closed
        # window. Closing the dialog also cancels the pending reference.
        self.reference_card.shutdown()
        super().closeEvent(event)

    def item(self) -> FoodItem:
        payload: dict[str, Any] = {
            "name": self.name_edit.text().strip(),
            "amount": self.amount_spin.value() or None,
            "unit": self.unit_edit.text().strip() or None,
            "cooking_method": self.cooking_edit.text().strip() or None,
            "confidence": self.confidence_spin.value(),
        }
        for field, spin in self.spins.items():
            payload[field] = spin.value()
        return FoodItem.model_validate(payload)


class MealEditDialog(QDialog):
    """修改一条饮食记录的餐次、日期、时间或备注。"""

    def __init__(self, meal: MealRecord, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("mealedit.window_title"))
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.meal_type_combo = _meal_type_combo(meal.meal_type)
        form.addRow(tr("mealedit.meal_label"), self.meal_type_combo)
        self.date_edit = QLineEdit(meal.date)
        self.date_edit.setPlaceholderText("YYYY-MM-DD")
        form.addRow(tr("mealedit.date"), self.date_edit)
        self.time_edit = QLineEdit(meal.time)
        self.time_edit.setPlaceholderText("HH:MM")
        form.addRow(tr("mealedit.time"), self.time_edit)
        self.note_edit = QLineEdit(meal.note)
        form.addRow(tr("mealedit.note"), self.note_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("mealedit.save"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {
            "meal_type": str(self.meal_type_combo.currentData()),
            "date": self.date_edit.text().strip(),
            "time": self.time_edit.text().strip(),
            "note": self.note_edit.text().strip(),
        }


class RecommendationDialog(QDialog):
    """展示 AI 给出的下一餐建议。"""

    def __init__(
        self,
        recommendation: MealRecommendation,
        *,
        remaining_kcal: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("recommend.card_title"))
        self.setMinimumSize(780, 540)
        self._recommendation = recommendation

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(SectionTitle(tr("recommend.title", title=recommendation.title)))
        title_row.addStretch(1)
        title_row.addWidget(Chip(tr("recommend.chip")))
        layout.addLayout(title_row)

        card = Card()
        table = FoodItemsTable(editable=False)
        table.set_items(recommendation.foods)
        table.setMinimumHeight(160)
        card.add(table)
        layout.addWidget(card)

        totals = sum_food_items(recommendation.foods)
        estimate = QLabel(
            tr(
                "recommend.estimated",
                kcal=f"{totals.calories_kcal:,.0f}",
                protein=f"{totals.protein_g:,.1f}",
                carbs=f"{totals.carbs_g:,.1f}",
                fat=f"{totals.fat_g:,.1f}",
            )
        )
        estimate.setObjectName("SectionTitle")
        estimate.setWordWrap(True)
        layout.addWidget(estimate)

        if remaining_kcal is not None:
            layout.addWidget(
                muted(tr("recommend.remaining_before", kcal=f"{remaining_kcal:,.0f}"))
            )

        if recommendation.reason:
            layout.addWidget(SectionTitle(tr("recommend.reason_title")))
            layout.addWidget(muted(recommendation.reason))
        if recommendation.tips:
            layout.addWidget(SectionTitle(tr("recommend.tips_title")))
            layout.addWidget(muted(recommendation.tips))

        layout.addWidget(muted(tr("settings.about_disclaimer")))
        layout.addStretch(1)

        buttons = QDialogButtonBox()
        add_button = buttons.addButton(
            tr("recommend.add_to_log"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        add_button.setObjectName("Primary")
        buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(tr("recommend.save_as")))
        self.meal_type_combo = _meal_type_combo(_suggest_meal_type())
        bottom.addWidget(self.meal_type_combo)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

    def meal_type(self) -> str:
        return str(self.meal_type_combo.currentData())

    def foods(self) -> list[FoodItem]:
        return list(self._recommendation.foods)

    def title_text(self) -> str:
        return self._recommendation.title


#: ``job_factory(on_success, on_error)`` runs one online-reference request in a
#: background thread. ``MainWindow`` builds it from the shared DeepSeek client;
#: tests pass a factory that returns a fake.
ReferenceJobFactory = Callable[
    [Callable[[Any], None], Callable[[str], None]], Any
]


def with_reference_sources(
    factory: "ReferenceJobFactory | None",
    item_provider: Callable[[], Any],
    name_provider: Callable[[], str],
) -> "ReferenceJobFactory | None":
    """Bind the live food name to the shared client call.

    Returning ``None`` is meaningful: the online-reference card then shows a
    disabled button with an explanation instead of trying to call the API.
    """
    if factory is None:
        return None

    def job(
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None],
    ) -> Any:
        _ = item_provider  # kept so callers pass both sources symmetrically
        return factory(on_success, on_error, str(name_provider() or "").strip())

    return job


def _food_row_label(item: Any) -> str:
    """Name (+ weight) for the row picker above the online reference."""
    name = str(_read(item, "name") or "").strip()
    weight = _read(item, "estimated_weight_g")
    try:
        grams = float(weight or 0.0)
    except (TypeError, ValueError):
        grams = 0.0
    if not name:
        return tr("table.food")
    if grams > 0:
        return f"{name}（{grams:g} g）"
    return name


def _spin(
    minimum: float,
    maximum: float,
    step: float,
    value: Any,
    *,
    decimals: int | None = None,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals if decimals is not None else (0 if step >= 1 else 1))
    try:
        numeric = coerce_number(value, allow_none=True) or 0.0
    except Exception:  # noqa: BLE001 - 错误数值不应该阻塞对话框
        numeric = 0.0
    spin.setValue(float(numeric))
    return spin


def _read(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    if isinstance(item, FoodItemRecord):
        return getattr(item, field, None)
    return getattr(item, field, None)


def _suggest_meal_type() -> str:
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 10:
        return "breakfast"
    if hour < 15:
        return "lunch"
    if hour < 21:
        return "dinner"
    return "snack"
