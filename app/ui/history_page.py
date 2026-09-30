"""历史记录页：按天列出统计，并可查看当天详细饮食。"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database.models import DailySummary, MealRecord
from app.i18n import format_date, tr
from app.ui.widgets import Card, MealSection, SectionTitle, muted

__all__ = ["HistoryPage"]

_FILTERS = (
    ("all", "history.filter_all"),
    ("today", "history.filter_today"),
    ("yesterday", "history.filter_yesterday"),
    ("7days", "history.filter_7days"),
    ("30days", "history.filter_30days"),
)

_COLUMN_KEYS = (
    "history.col_date",
    "history.col_calories",
    "history.col_protein",
    "history.col_carbs",
    "history.col_fat",
    "history.col_meals",
)


class HistoryPage(QWidget):
    """过去每一天的汇总，以及选中那天的详细记录。"""

    day_selected = Signal(str)
    open_day_requested = Signal(str)
    edit_meal_requested = Signal(int)
    delete_meal_requested = Signal(int)
    edit_item_requested = Signal(int)
    delete_item_requested = Signal(int)
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(SectionTitle(tr("history.title")))
        header.addStretch(1)
        self.filter_combo = QComboBox()
        for value, key in _FILTERS:
            self.filter_combo.addItem(tr(key), value)
        self.filter_combo.currentIndexChanged.connect(lambda _: self._apply_filter())
        header.addWidget(QLabel(tr("history.filter_label")))
        header.addWidget(self.filter_combo)
        refresh = QPushButton(tr("history.refresh"))
        refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(refresh)
        layout.addLayout(header)
        layout.addWidget(muted(tr("history.hint")))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        table_card = Card()
        self.table = QTableWidget(0, len(_COLUMN_KEYS))
        self.table.setHorizontalHeaderLabels([tr(key) for key in _COLUMN_KEYS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, len(_COLUMN_KEYS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        table_card.add(self.table)
        splitter.addWidget(table_card)

        details_card = Card(tr("history.details_placeholder"))
        self.details_card = details_card
        open_button = QPushButton(tr("history.open_today"))
        open_button.setObjectName("Link")
        open_button.clicked.connect(self._open_selected_day)
        details_card.add_header_widget(open_button)

        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_container = QWidget()
        self.details_layout = QVBoxLayout(self.details_container)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(10)
        self.details_scroll.setWidget(self.details_container)
        details_card.body().addWidget(self.details_scroll)
        splitter.addWidget(details_card)
        splitter.setSizes([340, 760])

        self._all_summaries: list[DailySummary] = []
        self._selected_date: str | None = None
        self._show_placeholder(tr("history.select_hint"))

    # ------------------------------------------------------------------ 数据
    def set_summaries(self, summaries: Sequence[DailySummary]) -> None:
        self._all_summaries = list(summaries)
        self._apply_filter()

    def _apply_filter(self) -> None:
        mode = str(self.filter_combo.currentData() or "all")
        today = date_cls.today()
        visible = self._all_summaries
        if mode != "all":
            if mode == "today":
                cutoff, exact = today, True
            elif mode == "yesterday":
                cutoff, exact = today - timedelta(days=1), True
            elif mode == "7days":
                cutoff, exact = today - timedelta(days=6), False
            else:
                cutoff, exact = today - timedelta(days=29), False
            threshold = cutoff.isoformat()
            visible = [
                summary
                for summary in self._all_summaries
                if (summary.date == threshold if exact else summary.date >= threshold)
            ]
        self._fill_table(visible)

    def _fill_table(self, summaries: Sequence[DailySummary]) -> None:
        self.table.setRowCount(0)
        for summary in summaries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                format_date(summary.date),
                f"{summary.totals.calories_kcal:,.0f} kcal",
                f"{summary.totals.protein_g:,.1f} g",
                f"{summary.totals.carbs_g:,.1f} g",
                f"{summary.totals.fat_g:,.1f} g",
                f"{summary.meal_count}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary.date)
                self.table.setItem(row, column, item)
        if not summaries:
            self._show_placeholder(tr("history.empty"))

    def set_day_details(self, date_iso: str, meals: Sequence[MealRecord]) -> None:
        self._selected_date = date_iso
        self.details_card.set_title(
            tr("history.details_title", date=format_date(date_iso))
        )
        self._clear_details()
        if not meals:
            self._show_placeholder(tr("history.day_empty"))
            return
        for meal in meals:
            section = MealSection(meal)
            section.edit_meal.connect(self.edit_meal_requested.emit)
            section.delete_meal.connect(self.delete_meal_requested.emit)
            section.edit_item.connect(self.edit_item_requested.emit)
            section.delete_item.connect(self.delete_item_requested.emit)
            self.details_layout.addWidget(section)
        self.details_layout.addStretch(1)

    def selected_date(self) -> str | None:
        return self._selected_date

    def select_date(self, date_iso: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == date_iso:
                self.table.selectRow(row)
                return

    # ------------------------------------------------------------- 内部逻辑
    def _on_selection_changed(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return
        item = self.table.item(min(rows), 0)
        if item is None:
            return
        date_iso = item.data(Qt.ItemDataRole.UserRole)
        if date_iso:
            self.day_selected.emit(str(date_iso))

    def _open_selected_day(self) -> None:
        if self._selected_date:
            self.open_day_requested.emit(self._selected_date)

    def _clear_details(self) -> None:
        while self.details_layout.count():
            entry = self.details_layout.takeAt(0)
            widget = entry.widget() if entry is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _show_placeholder(self, text: str) -> None:
        self._clear_details()
        self.details_layout.addWidget(muted(text))
        self.details_layout.addStretch(1)
