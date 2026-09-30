"""「今日营养」主页面：营养统计、饮食记录与下一餐推荐。"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ai.schemas import MealRecommendation
from app.database.models import MealRecord
from app.i18n import format_date, tr
from app.nutrition.calculator import (
    DailyTotals,
    describe_remaining_calories,
    sum_food_items,
)
from app.nutrition.targets import NutritionTargets, estimate_note
from app.nutrition.purchase import difficulty_label
from app.ui.widgets import (
    MEAL_TYPE_ORDER,
    Card,
    Chip,
    LoadingBar,
    MealSection,
    NutritionBar,
    SectionTitle,
    meal_type_title,
    muted,
)

__all__ = ["DashboardPage"]


class DashboardPage(QWidget):
    """一天的全部信息，以及添加/修改记录的操作入口。"""

    upload_requested = Signal()
    manual_requested = Signal()
    recommend_requested = Signal()
    log_recommendation_requested = Signal()
    dismiss_recommendation_requested = Signal()
    edit_item_requested = Signal(int)
    delete_item_requested = Signal(int)
    edit_meal_requested = Signal(int)
    delete_meal_requested = Signal(int)
    date_changed = Signal(str)
    open_settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._date = QDate.currentDate()

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

        layout.addWidget(self._build_api_warning())
        layout.addWidget(self._build_summary_card())
        layout.addWidget(self._build_actions_card())
        layout.addWidget(self._build_meals_card())
        layout.addWidget(self._build_recommendation_card())
        layout.addStretch(1)

    # ---------------------------------------------------------------- 构建
    def _build_api_warning(self) -> QWidget:
        self.api_warning = QWidget()
        row = QHBoxLayout(self.api_warning)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        label = QLabel(tr("dashboard.no_api_key"))
        label.setObjectName("Warning")
        label.setWordWrap(True)
        row.addWidget(label, 1)
        button = QPushButton(tr("nav.settings"))
        button.clicked.connect(self.open_settings_requested.emit)
        row.addWidget(button)
        self.api_warning.setVisible(False)
        return self.api_warning

    def _build_summary_card(self) -> Card:
        card = Card(tr("dashboard.title_today"))
        self.card = card

        date_row = QHBoxLayout()
        date_row.addWidget(muted(tr("dashboard.showing_date")))
        self.date_edit = QDateEdit(self._date)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.dateChanged.connect(self._on_date_changed)
        date_row.addWidget(self.date_edit)
        today_button = QPushButton(tr("dashboard.today_button"))
        today_button.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        date_row.addWidget(today_button)
        date_row.addStretch(1)
        card.body().addLayout(date_row)

        numbers = QGridLayout()
        numbers.setHorizontalSpacing(28)
        self.consumed_value = _big_number("0")
        self.target_value = _big_number("0")
        self.remaining_value = _big_number("0")
        for column, (title, widget) in enumerate(
            (
                (tr("nutrient.consumed"), self.consumed_value),
                (tr("nutrient.target"), self.target_value),
                (tr("nutrient.remaining"), self.remaining_value),
            )
        ):
            numbers.addWidget(muted(title), 0, column)
            numbers.addWidget(widget, 1, column)
        card.body().addLayout(numbers)

        self.remaining_sentence = QLabel("")
        self.remaining_sentence.setObjectName("Muted")
        card.body().addWidget(self.remaining_sentence)

        self.bars: dict[str, NutritionBar] = {
            "calories": NutritionBar(tr("nutrient.calories"), unit=tr("nutrient.unit_kcal")),
            "protein": NutritionBar(tr("nutrient.protein"), unit=tr("nutrient.unit_gram")),
            "carbs": NutritionBar(tr("nutrient.carbs"), unit=tr("nutrient.unit_gram")),
            "fat": NutritionBar(tr("nutrient.fat"), unit=tr("nutrient.unit_gram")),
            "sugar": NutritionBar(tr("nutrient.sugar"), unit=tr("nutrient.unit_gram"), is_limit=True),
            "fibre": NutritionBar(tr("nutrient.fibre"), unit=tr("nutrient.unit_gram")),
            "sodium": NutritionBar(tr("nutrient.sodium"), unit=tr("nutrient.unit_mg"), is_limit=True),
        }
        for bar in self.bars.values():
            card.body().addWidget(bar)

        card.body().addWidget(muted(estimate_note()))
        return card

    def _build_actions_card(self) -> Card:
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.upload_button = QPushButton(tr("dashboard.upload_photo"))
        self.upload_button.setObjectName("Primary")
        self.upload_button.clicked.connect(self.upload_requested.emit)
        self.manual_button = QPushButton(tr("dashboard.manual_entry"))
        self.manual_button.setObjectName("Primary")
        self.manual_button.clicked.connect(self.manual_requested.emit)
        self.recommend_button = QPushButton(tr("dashboard.recommend"))
        self.recommend_button.clicked.connect(self.recommend_requested.emit)
        row.addWidget(self.upload_button)
        row.addWidget(self.manual_button)
        row.addWidget(self.recommend_button)
        row.addStretch(1)
        self.loading = LoadingBar()
        row.addWidget(self.loading)
        card.body().addLayout(row)
        self.purchase_summary = muted("")
        card.body().addWidget(self.purchase_summary)
        return card

    def _build_meals_card(self) -> Card:
        card = Card(tr("dashboard.meals_today"))
        self.meals_card = card
        self.meals_container = QWidget()
        self.meals_layout = QVBoxLayout(self.meals_container)
        self.meals_layout.setContentsMargins(0, 0, 0, 0)
        self.meals_layout.setSpacing(14)
        card.body().addWidget(self.meals_container)
        return card

    def _build_recommendation_card(self) -> Card:
        card = Card(tr("recommend.card_title"))
        self.recommendation_card = card
        card.setVisible(False)

        title_row = QHBoxLayout()
        self.recommendation_title = SectionTitle("")
        title_row.addWidget(self.recommendation_title)
        title_row.addStretch(1)
        self.recommendation_difficulty = Chip("")
        title_row.addWidget(self.recommendation_difficulty)
        card.body().addLayout(title_row)

        self.recommendation_ingredients_title = SectionTitle(
            tr("recommend.ingredients_title")
        )
        card.body().addWidget(self.recommendation_ingredients_title)
        self.recommendation_items = QLabel("")
        self.recommendation_items.setWordWrap(True)
        card.body().addWidget(self.recommendation_items)

        self.recommendation_estimate = QLabel("")
        self.recommendation_estimate.setObjectName("SectionTitle")
        self.recommendation_estimate.setWordWrap(True)
        card.body().addWidget(self.recommendation_estimate)

        self.recommendation_context = muted("")
        card.body().addWidget(self.recommendation_context)

        self.recommendation_shopping_title = SectionTitle(tr("recommend.shopping_title"))
        card.body().addWidget(self.recommendation_shopping_title)
        self.recommendation_shopping = QLabel("")
        self.recommendation_shopping.setWordWrap(True)
        card.body().addWidget(self.recommendation_shopping)
        self.recommendation_shopping_note = muted(tr("recommend.shopping_note"))
        card.body().addWidget(self.recommendation_shopping_note)

        self.recommendation_warning = QLabel("")
        self.recommendation_warning.setObjectName("Warning")
        self.recommendation_warning.setWordWrap(True)
        self.recommendation_warning.setVisible(False)
        card.body().addWidget(self.recommendation_warning)

        self.recommendation_reason_title = SectionTitle(tr("recommend.reason_title"))
        card.body().addWidget(self.recommendation_reason_title)
        self.recommendation_reason = muted("")
        card.body().addWidget(self.recommendation_reason)

        self.recommendation_tips = muted("")
        card.body().addWidget(self.recommendation_tips)

        row = QHBoxLayout()
        self.log_recommendation_button = QPushButton(tr("recommend.add_to_log"))
        self.log_recommendation_button.setObjectName("Primary")
        self.log_recommendation_button.clicked.connect(self.log_recommendation_requested.emit)
        dismiss = QPushButton(tr("recommend.dismiss"))
        dismiss.clicked.connect(self.dismiss_recommendation_requested.emit)
        row.addWidget(self.log_recommendation_button)
        row.addWidget(dismiss)
        row.addStretch(1)
        card.body().addLayout(row)
        return card

    # ------------------------------------------------------------------ 接口
    def current_date(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def _on_date_changed(self, date: QDate) -> None:
        self.date_changed.emit(date.toString("yyyy-MM-dd"))

    def set_date(self, date_iso: str) -> None:
        date = QDate.fromString(date_iso, "yyyy-MM-dd")
        if date.isValid():
            self.date_edit.setDate(date)

    def set_api_key_state(self, has_key: bool) -> None:
        self.api_warning.setVisible(not has_key)

    def set_busy(self, busy: bool, message: str = "") -> None:
        for button in (
            self.upload_button,
            self.manual_button,
            self.recommend_button,
            self.log_recommendation_button,
        ):
            button.setEnabled(not busy)
        if busy:
            self.loading.start(message or tr("dashboard.loading_working"))
            self.setCursor(Qt.CursorShape.BusyCursor)
        else:
            self.loading.stop()
            self.unsetCursor()

    def set_data(
        self,
        *,
        date_iso: str,
        totals: DailyTotals,
        targets: NutritionTargets | None,
        meals: Sequence[MealRecord],
        is_today: bool,
    ) -> None:
        pretty = format_date(date_iso)
        self.card.set_title(
            tr("dashboard.title_today") if is_today else tr("dashboard.title_date", date=pretty)
        )
        self.meals_card.set_title(
            tr("dashboard.meals_today") if is_today else tr("dashboard.meals_date", date=pretty)
        )

        target_calories = targets.calorie_target if targets else 0.0
        self.consumed_value.setText(f"{totals.calories_kcal:,.0f}")
        self.target_value.setText(f"{target_calories:,.0f}")
        remaining = target_calories - totals.calories_kcal
        self.remaining_value.setText(f"{remaining:,.0f}")
        self.remaining_sentence.setText(
            describe_remaining_calories(remaining)
            if targets
            else tr("dashboard.no_profile")
        )

        self.bars["calories"].set_values(totals.calories_kcal, target_calories)
        if targets:
            self.bars["protein"].set_values(totals.protein_g, targets.protein_g)
            self.bars["carbs"].set_values(totals.carbs_g, targets.carbs_g)
            self.bars["fat"].set_values(totals.fat_g, targets.fat_g)
            self.bars["sugar"].set_values(totals.sugar_g, targets.sugar_limit_g)
            self.bars["fibre"].set_values(totals.fibre_g, targets.fibre_g)
            self.bars["sodium"].set_values(totals.sodium_mg, targets.sodium_limit_mg)
        else:
            for key, value in (
                ("protein", totals.protein_g),
                ("carbs", totals.carbs_g),
                ("fat", totals.fat_g),
                ("sugar", totals.sugar_g),
                ("fibre", totals.fibre_g),
                ("sodium", totals.sodium_mg),
            ):
                self.bars[key].set_values(value, 0)

        self._rebuild_meals(meals)

    def _rebuild_meals(self, meals: Sequence[MealRecord]) -> None:
        while self.meals_layout.count():
            entry = self.meals_layout.takeAt(0)
            widget = entry.widget() if entry is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not meals:
            self.meals_layout.addWidget(muted(tr("dashboard.no_meals")))
            return

        for meal_type in MEAL_TYPE_ORDER:
            group_meals = [meal for meal in meals if meal.meal_type == meal_type]
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(6)

            header = QHBoxLayout()
            header.addWidget(SectionTitle(meal_type_title(meal_type)))
            header.addStretch(1)
            total_kcal = sum(meal.totals().calories_kcal for meal in group_meals)
            kcal_label = QLabel(f"{total_kcal:,.0f} kcal")
            kcal_label.setObjectName("Muted")
            header.addWidget(kcal_label)
            group_layout.addLayout(header)

            if not group_meals:
                group_layout.addWidget(muted(tr("dashboard.nothing_logged")))
            else:
                for meal in group_meals:
                    section = MealSection(meal)
                    section.edit_meal.connect(self.edit_meal_requested.emit)
                    section.delete_meal.connect(self.delete_meal_requested.emit)
                    section.edit_item.connect(self.edit_item_requested.emit)
                    section.delete_item.connect(self.delete_item_requested.emit)
                    group_layout.addWidget(section)

            self.meals_layout.addWidget(group)

    def set_recommendation(
        self,
        recommendation: MealRecommendation,
        *,
        remaining_kcal: float | None = None,
        purchase_context: str = "",
        inconvenient_warning: str = "",
    ) -> None:
        self.recommendation_card.setVisible(True)
        self.recommendation_title.setText(
            tr("recommend.title", title=recommendation.title)
        )
        difficulty = difficulty_label(recommendation.difficulty)
        self.recommendation_difficulty.setVisible(bool(difficulty))
        if difficulty:
            self.recommendation_difficulty.set_state(
                tr("recommend.difficulty_title") + "：" + difficulty,
                warning=recommendation.difficulty == "hard",
            )

        lines = []
        for food in recommendation.foods:
            portion = (
                f"{food.estimated_weight_g:g} g" if food.estimated_weight_g else ""
            )
            lines.append(f"・{food.name}" + (f" — {portion}" if portion else ""))
        self.recommendation_items.setText("\n".join(lines))

        self.recommendation_context.setText(purchase_context)
        self.recommendation_context.setVisible(bool(purchase_context))

        shopping_lines = []
        for item in recommendation.shopping_list:
            quantity = f" {item.quantity}" if item.quantity else ""
            difficulty_text = difficulty_label(item.difficulty)
            suffix = f"（{difficulty_text}）" if difficulty_text else ""
            shopping_lines.append(f"・{item.name}{quantity}{suffix}")
        has_shopping = bool(shopping_lines)
        self.recommendation_shopping_title.setVisible(has_shopping)
        self.recommendation_shopping.setText("\n".join(shopping_lines))
        self.recommendation_shopping.setVisible(has_shopping)
        self.recommendation_shopping_note.setVisible(has_shopping)

        self.recommendation_warning.setText(inconvenient_warning)
        self.recommendation_warning.setVisible(bool(inconvenient_warning))

        totals = sum_food_items(recommendation.foods)
        self.recommendation_estimate.setText(
            tr(
                "recommend.estimated",
                kcal=f"{totals.calories_kcal:,.0f}",
                protein=f"{totals.protein_g:,.1f}",
                carbs=f"{totals.carbs_g:,.1f}",
                fat=f"{totals.fat_g:,.1f}",
            )
            + (
                "　" + tr("recommend.remaining_before", kcal=f"{remaining_kcal:,.0f}")
                if remaining_kcal is not None
                else ""
            )
        )
        self.recommendation_reason.setText(recommendation.reason or "")
        self.recommendation_tips.setText(recommendation.tips or "")
        self.recommendation_tips.setVisible(bool(recommendation.tips))

    def set_purchase_summary(self, text: str) -> None:
        self.purchase_summary.setText(text)
        self.purchase_summary.setVisible(bool(text))

    def clear_recommendation(self) -> None:
        self.recommendation_card.setVisible(False)
        self.recommendation_title.setText("")
        self.recommendation_items.setText("")
        self.recommendation_estimate.setText("")
        self.recommendation_reason.setText("")
        self.recommendation_tips.setText("")
        self.recommendation_context.setText("")
        self.recommendation_shopping.setText("")
        self.recommendation_shopping_note.setVisible(False)
        self.recommendation_difficulty.setVisible(False)
        self.recommendation_warning.setVisible(False)


def _big_number(value: str) -> QLabel:
    label = QLabel(value)
    label.setObjectName("BigNumber")
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return label
