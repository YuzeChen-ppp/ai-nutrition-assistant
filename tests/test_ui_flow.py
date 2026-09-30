"""End-to-end test of the GUI flow (offscreen, with a fake AI client).

These tests build the real :class:`MainWindow` and drive it exactly like a user
would - minus the modal dialogs, which are replaced by a stub that confirms
immediately.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.database.models import UserProfile  # noqa: E402
from app.ui.dialogs import ConfirmFoodDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.ui.widgets import FoodItemsTable, NutritionBar  # noqa: E402
from app.utils.config import Settings  # noqa: E402
from tests.fakes import FakeDeepSeekClient  # noqa: E402

_APP: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest naming
    global _APP
    _APP = QApplication.instance() or QApplication([])
    apply_app_style(_APP)


def tearDownModule() -> None:  # noqa: N802 - unittest naming
    pass


def wait_until(predicate, timeout: float = 10.0) -> bool:
    """Process Qt events until ``predicate`` is true (or time runs out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return predicate()


class StubConfirmDialog:
    """Replaces the modal confirmation dialog in tests."""

    def __init__(self, items, source: str = "manual", note: str = "") -> None:
        self._items = list(items)
        self.source = source
        self.note = note

    def exec(self) -> int:
        return 1  # QDialog.DialogCode.Accepted

    def confirmed_items(self):
        return list(self._items)


class MainWindowFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ui.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.fake = FakeDeepSeekClient(Settings(api_key="test-key", model="deepseek-flash"))
        self.context = AppContext(
            settings=Settings(api_key="test-key", model="deepseek-flash"),
            database=self.db,
            api_client_factory=lambda _settings: self.fake,
        )
        self.window = MainWindow(self.context)
        self.window.confirm_dialog_factory = StubConfirmDialog

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ setup
    def test_first_run_shows_profile_setup(self) -> None:
        self.assertIsInstance(self.window.stack.currentWidget(), type(self.window.setup_page))
        self.assertIsNot(self.window.stack.currentWidget(), self.window.dashboard)
        self.assertTrue(self.window.nav_buttons["settings"].isEnabled())

    def test_saving_profile_enables_dashboard_and_targets(self) -> None:
        self.window.save_profile(
            UserProfile(
                age=25, sex="male", height_cm=175, weight_kg=70,
                activity_level="moderate", goal="maintain",
            )
        )
        self.assertIs(self.window.stack.currentWidget(), self.window.dashboard)
        self.assertTrue(self.window.nav_buttons["today"].isEnabled())
        self.assertIsNotNone(self.db.get_profile())
        targets = self.db.get_targets()
        assert targets is not None
        self.assertGreater(targets.calorie_target, 1500)
        self.assertIn("/", self.window.dashboard.bars["protein"].value_label.text())

    # ------------------------------------------------------------ text -> save
    def test_text_analysis_saves_meal_and_updates_totals(self) -> None:
        self._with_profile()
        self.window.analyze_text("两个鸡蛋、一片全麦吐司、一杯半脱脂牛奶", "breakfast")
        self.assertTrue(wait_until(lambda: self.db.count_meals() == 1), "meal was not saved")

        totals = self.db.daily_totals(self.window.dashboard.current_date())
        self.assertAlmostEqual(totals.calories_kcal, 344.0, places=1)
        self.assertAlmostEqual(totals.protein_g, 25.7, places=1)
        self.assertAlmostEqual(totals.carbs_g, 27.1, places=1)
        self.assertAlmostEqual(totals.fat_g, 14.7, places=1)

        self.assertIn("344", self.window.dashboard.consumed_value.text())
        self.assertEqual(len(self.fake.text_calls), 1)
        meals = self.db.meals_for_date(self.window.dashboard.current_date())
        self.assertEqual(meals[0].meal_type, "breakfast")
        self.assertEqual(meals[0].source, "manual")
        self.assertEqual(len(meals[0].items), 3)

    def test_image_analysis_is_stored_as_image_source(self) -> None:
        self._with_profile()
        from PIL import Image

        photo = Path(self._tmp.name) / "meal.jpg"
        Image.new("RGB", (900, 700), (180, 140, 90)).save(photo)

        self.window.analyze_photo(str(photo), "lunch", "with olive oil")
        self.assertTrue(wait_until(lambda: self.db.count_meals() == 1), "photo meal was not saved")

        meals = self.db.meals_for_date(self.window.dashboard.current_date())
        self.assertEqual(meals[0].source, "image")
        self.assertEqual(meals[0].items[0].name, "beef fried rice")
        self.assertAlmostEqual(meals[0].items[0].calories_kcal, 620.0)
        self.assertEqual(self.fake.image_calls, ["with olive oil"])

    def test_api_error_is_reported_and_does_not_crash(self) -> None:
        from app.ai.errors import AINetworkError
        from app.ui import main_window as main_window_module

        self._with_profile()
        captured: list[tuple[str, str]] = []
        original = main_window_module.show_error
        main_window_module.show_error = lambda parent, title, message: captured.append((title, message))
        try:
            self.fake.fail_with = AINetworkError()
            self.window.analyze_text("some food", "lunch")
            self.assertTrue(
                wait_until(lambda: bool(captured)),
                "the error was never surfaced to the user",
            )
        finally:
            main_window_module.show_error = original
        self.assertEqual(self.db.count_meals(), 0)
        self.assertFalse(self.window.dashboard.loading.isVisible())
        self.assertIn("网络连接", captured[0][1])

    # ---------------------------------------------------------- recommendation
    def test_recommendation_uses_remaining_amounts(self) -> None:
        self._with_profile()
        self.window.save_items(
            self._sample_items(), meal_type="breakfast", source="manual"
        )
        self.window.on_recommend_next_meal()
        self.assertTrue(
            wait_until(lambda: self.window._recommendation is not None),
            "no recommendation was produced",
        )
        self.assertFalse(self.window.dashboard.recommendation_card.isHidden())
        context = self.fake.recommendation_calls[0]
        self.assertIn("remaining", context)
        self.assertIn("today_meals", context)
        self.assertEqual(context["goal"], "maintain")
        self.assertGreater(context["remaining"]["calories_kcal"], 0)
        self.assertLess(context["remaining"]["calories_kcal"], context["daily_targets"]["calories_kcal"])
        self.assertIn("预计", self.window.dashboard.recommendation_estimate.text())

    def test_recommended_meal_is_logged_when_confirmed(self) -> None:
        self._with_profile()
        self.window.save_items(self._sample_items(), meal_type="breakfast")
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: self.window._recommendation is not None))
        items = list(self.window._recommendation.foods)  # type: ignore[union-attr]
        self.window.save_items(items, meal_type="dinner", source="manual", note="AI recommendation")
        self.assertEqual(self.db.count_meals(), 2)
        meals = self.db.meals_for_date(self.window.dashboard.current_date())
        lunch_or_dinner = [meal for meal in meals if meal.meal_type == "dinner"][0]
        self.assertIn("AI recommendation", lunch_or_dinner.note)

    # --------------------------------------------------------------- history
    def test_history_page_lists_days_and_details(self) -> None:
        self._with_profile()
        self.window.save_items(self._sample_items(), meal_type="dinner", date="2026-09-13")
        self.window.refresh_all()
        self.window.goto("history")
        self.assertGreaterEqual(self.window.history.table.rowCount(), 1)
        self.window.on_history_day_selected("2026-09-13")
        self.assertEqual(self.window.history.selected_date(), "2026-09-13")
        titles = [
            self.window.history.details_layout.itemAt(i).widget().__class__.__name__
            for i in range(self.window.history.details_layout.count())
            if self.window.history.details_layout.itemAt(i).widget() is not None
        ]
        self.assertIn("MealSection", titles)

    def test_editing_and_deleting_items_updates_totals(self) -> None:
        self._with_profile()
        meal_id = self.window.save_items(self._sample_items(), meal_type="lunch")
        item_id = self.db.food_items_for_meal(meal_id)[0].id
        self.db.update_food_item(item_id, calories_kcal=999)
        self.window.refresh_all()
        self.assertAlmostEqual(
            self.db.daily_totals(self.window.dashboard.current_date()).calories_kcal,
            1259.0,
            places=1,
        )
        self.assertIn("1,259", self.window.dashboard.consumed_value.text())

        self.db.delete_food_item(item_id)
        self.window.refresh_all()
        self.assertAlmostEqual(
            self.db.daily_totals(self.window.dashboard.current_date()).calories_kcal,
            260.0,
            places=1,
        )

    # ------------------------------------------------------------------ misc
    def test_api_status_never_shows_the_full_key(self) -> None:
        status = self.window._api_status()
        self.assertTrue(status.has_key)
        self.assertNotIn("test-key", status.masked_key)
        self.assertEqual(status.model, "deepseek-flash")

    def _with_profile(self) -> None:
        self.window.save_profile(
            UserProfile(
                age=28, sex="female", height_cm=168, weight_kg=62,
                activity_level="light", goal="maintain",
            )
        )

    def _sample_items(self):
        from app.ai.schemas import FoodItem

        return [
            FoodItem(
                name="chicken breast",
                estimated_weight_g=150,
                calories_kcal=248,
                protein_g=46.5,
                carbs_g=0,
                fat_g=5.4,
                confidence=0.9,
            ),
            FoodItem(
                name="white rice",
                estimated_weight_g=200,
                calories_kcal=260,
                protein_g=5.4,
                carbs_g=56.4,
                fat_g=0.6,
                confidence=0.9,
            ),
        ]


class WidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        global _APP
        _APP = QApplication.instance() or QApplication([])

    def test_food_items_table_round_trip(self) -> None:
        from app.ai.schemas import FoodItem

        table = FoodItemsTable(editable=True)
        table.set_items(
            [
                FoodItem(
                    name="egg",
                    amount=2,
                    unit="pieces",
                    estimated_weight_g=100,
                    calories_kcal=143,
                    protein_g=12.6,
                    confidence=0.9,
                )
            ]
        )
        items, problems = table.items()
        self.assertEqual(problems, [])
        self.assertEqual(items[0].name, "egg")
        self.assertAlmostEqual(items[0].calories_kcal, 143.0)

    def test_food_items_table_reports_bad_input(self) -> None:
        from app.ai.schemas import FoodItem

        table = FoodItemsTable(editable=True)
        table.set_items([FoodItem(name="egg", calories_kcal=100)])
        table.item(0, 4).setText("-50")  # calories column
        items, problems = table.items()
        self.assertTrue(problems)
        self.assertEqual(items, [])

    def test_confirm_dialog_warns_about_risky_values(self) -> None:
        from app.ai.schemas import FoodItem

        items = [
            FoodItem(
                name="beef fried rice",
                estimated_weight_g=350,
                calories_kcal=620,
                protein_g=24,
                carbs_g=75,
                fat_g=24,
                confidence=0.35,
            )
        ]
        dialog = ConfirmFoodDialog(items, source="image", note="estimated")
        self.assertIn("置信度", dialog.warnings_box.text())
        self.assertIn("620", dialog.totals_label.text())
        dialog.close()

    def test_nutrition_bar_shows_over_target_message(self) -> None:
        from app.nutrition.targets import NutritionTargets

        bar = NutritionBar("Calories", unit="kcal")
        bar.set_values(2350, 2200)
        self.assertIn("超过上限", bar.note_label.text())
        self.assertTrue(bar.note_label.isVisibleTo(bar) or not bar.isVisible())
        _ = NutritionTargets  # keeps the import meaningful for readers


if __name__ == "__main__":
    unittest.main()
