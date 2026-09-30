"""Dialog widgets are constructed offscreen so real bugs surface in tests.

Modal dialogs cannot be clicked in a headless test, but constructing them and
driving their non-modal logic catches the mistakes that would otherwise only
appear when a user opens the window.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402

from app.ai.schemas import FoodItem  # noqa: E402
from app.ai.parser import parse_recommendation  # noqa: E402
from app.database.models import FoodItemRecord, MealRecord, UserProfile  # noqa: E402
from app.ui.dialogs import (  # noqa: E402
    ConfirmFoodDialog,
    FoodItemEditDialog,
    ManualEntryDialog,
    MealEditDialog,
    PhotoEntryDialog,
    RecommendationDialog,
)
from app.ui.main_window import _default_meal_index, _valid_date  # noqa: E402
from app.ui.profile_form import ProfileForm  # noqa: E402
from tests.fakes import RECOMMENDATION_PAYLOAD  # noqa: E402

_APP: QApplication | None = None


def setUpModule() -> None:  # noqa: N802
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _food_item() -> FoodItem:
    return FoodItem(
        name="chicken breast",
        amount=150,
        unit="g",
        estimated_weight_g=150,
        calories_kcal=248,
        protein_g=46.5,
        carbs_g=0,
        fat_g=5.4,
        sugar_g=0,
        fibre_g=0,
        sodium_mg=111,
        cooking_method="grilled",
        confidence=0.8,
    )


class ManualEntryDialogTests(unittest.TestCase):
    def test_collects_text_and_meal_type(self) -> None:
        dialog = ManualEntryDialog()
        dialog.text_edit.setPlainText("两个鸡蛋、一片全麦吐司")
        dialog.meal_type_combo.setCurrentIndex(dialog.meal_type_combo.findData("dinner"))
        self.assertEqual(dialog.text(), "两个鸡蛋、一片全麦吐司")
        self.assertEqual(dialog.meal_type(), "dinner")
        dialog.close()

    def test_empty_text_is_rejected(self) -> None:
        dialog = ManualEntryDialog()
        messages: list[tuple[str, str]] = []
        import app.ui.dialogs as dialogs_module

        original = dialogs_module.show_error
        dialogs_module.show_error = lambda parent, title, message: messages.append((title, message))
        try:
            dialog._on_accept()
        finally:
            dialogs_module.show_error = original
        self.assertTrue(messages)
        self.assertEqual(dialog.result(), 0)  # not accepted
        dialog.close()


class PhotoEntryDialogTests(unittest.TestCase):
    def test_preview_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meal.jpg"
            Image.new("RGB", (800, 600), (120, 90, 60)).save(path)
            dialog = PhotoEntryDialog(path)
            dialog.note_edit.setText("with olive oil")
            dialog.meal_type_combo.setCurrentIndex(dialog.meal_type_combo.findData("lunch"))
            self.assertEqual(dialog.note(), "with olive oil")
            self.assertEqual(dialog.meal_type(), "lunch")
            dialog.close()

    def test_missing_preview_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-an-image.png"
            path.write_bytes(b"nope")
            dialog = PhotoEntryDialog(path)
            dialog.close()


class ConfirmDialogTests(unittest.TestCase):
    def test_total_equals_the_sum_of_the_rows(self) -> None:
        """回归测试：披萨 4,200 mg 钠 + 酱 180 mg 钠 的合计必须是 4,380。"""
        items = [
            FoodItem(
                name="意大利辣香肠披萨（额外加马苏里拉芝士）",
                amount=1,
                unit="整个",
                estimated_weight_g=700,
                calories_kcal=1800,
                protein_g=80,
                carbs_g=200,
                fat_g=78,
                sugar_g=14,
                fibre_g=9,
                sodium_mg=4200,
                confidence=0.5,
            ),
            FoodItem(
                name="蒜香香草酱",
                amount=1,
                unit="小盒",
                estimated_weight_g=25,
                calories_kcal=90,
                protein_g=0.5,
                carbs_g=2,
                fat_g=9,
                sugar_g=0.5,
                fibre_g=0,
                sodium_mg=180,
                confidence=0.6,
            ),
        ]
        dialog = ConfirmFoodDialog(items, source="image")
        # 表格里显示的是带千位分隔符的 "4,200"
        self.assertEqual(dialog.table.item(0, 10).text(), "4,200")
        parsed, problems = dialog.table.items()
        self.assertEqual(problems, [])
        self.assertAlmostEqual(parsed[0].sodium_mg, 4200.0)
        self.assertAlmostEqual(parsed[1].sodium_mg, 180.0)
        text = dialog.totals_label.text()
        self.assertIn("4,380", text)
        self.assertIn("1,890", text)  # 热量合计同样正确
        self.assertIn("80.5", text)   # 蛋白质合计

        # 用户自己输入带千位分隔符的数字，也必须按 4200 理解
        dialog.table.item(0, 10).setText("4,380")
        dialog._refresh_totals()
        self.assertIn("4,560", dialog.totals_label.text())  # 4380 + 180
        dialog.close()

    def test_total_and_warning_text(self) -> None:
        dialog = ConfirmFoodDialog([_food_item()], source="manual")
        self.assertIn("248", dialog.totals_label.text())
        self.assertEqual(dialog.confirmed_items(), [])
        dialog.table.item(0, 4).setText("600")
        dialog._refresh_totals()
        self.assertIn("600", dialog.totals_label.text())
        dialog._toggle_editing()
        self.assertIn("完成修改", dialog.edit_button.text())
        dialog._toggle_editing()
        self.assertIn("修改", dialog.edit_button.text())
        dialog.close()

    def test_confirm_requires_valid_rows(self) -> None:
        import app.ui.dialogs as dialogs_module

        dialog = ConfirmFoodDialog([_food_item()], source="manual")
        dialog.table.item(0, 4).setText("-100")  # negative calories
        messages: list[str] = []
        original = dialogs_module.show_error
        dialogs_module.show_error = lambda parent, title, message: messages.append(message)
        try:
            dialog._on_confirm()
        finally:
            dialogs_module.show_error = original
        self.assertTrue(messages)
        self.assertEqual(dialog.result(), 0)
        dialog.close()

    def test_removing_the_last_row_blocks_confirm(self) -> None:
        import app.ui.dialogs as dialogs_module

        dialog = ConfirmFoodDialog([_food_item()])
        messages: list[str] = []
        original = dialogs_module.show_error
        dialogs_module.show_error = lambda parent, title, message: messages.append(message)
        try:
            dialog.table.selectRow(0)
            dialog._remove_row()
            dialog._on_confirm()
        finally:
            dialogs_module.show_error = original
        self.assertTrue(messages)
        self.assertEqual(dialog.result(), 0)
        dialog.close()


class FoodItemEditDialogTests(unittest.TestCase):
    def _record(self) -> FoodItemRecord:
        return FoodItemRecord(
            id=1,
            meal_id=1,
            name="white rice",
            estimated_weight_g=200,
            calories_kcal=260,
            protein_g=5.4,
            carbs_g=56.4,
            fat_g=0.6,
        )

    def test_editing_fields_returns_valid_item(self) -> None:
        dialog = FoodItemEditDialog(self._record())
        dialog.name_edit.setText("brown rice")
        dialog.spins["calories_kcal"].setValue(185)
        item = dialog.item()
        self.assertEqual(item.name, "brown rice")
        self.assertAlmostEqual(item.calories_kcal, 185.0)
        self.assertEqual(item.estimated_weight_g, 200.0)
        dialog.close()

    def test_weight_change_rescales_the_nutrients(self) -> None:
        dialog = FoodItemEditDialog(self._record())
        dialog.spins["estimated_weight_g"].setValue(100)
        dialog._maybe_rescale()
        self.assertAlmostEqual(dialog.spins["calories_kcal"].value(), 130.0, places=1)
        self.assertAlmostEqual(dialog.spins["protein_g"].value(), 2.7, places=1)
        dialog.close()

    def test_rescaling_can_be_switched_off(self) -> None:
        dialog = FoodItemEditDialog(self._record())
        dialog.rescale_checkbox.setChecked(False)
        dialog.spins["estimated_weight_g"].setValue(100)
        dialog._maybe_rescale()
        self.assertAlmostEqual(dialog.spins["calories_kcal"].value(), 260.0, places=1)
        dialog.close()

    def test_local_reference_values_can_be_applied(self) -> None:
        dialog = FoodItemEditDialog(self._record())
        self.assertIn("本地参考", dialog.reference_label.text())
        dialog._apply_reference()
        self.assertAlmostEqual(dialog.spins["calories_kcal"].value(), 260.0, places=1)
        dialog.close()

    def test_unknown_food_has_no_reference(self) -> None:
        record = FoodItemRecord(id=2, meal_id=1, name="grandma's secret stew", calories_kcal=400)
        dialog = FoodItemEditDialog(record)
        self.assertIn("没有匹配", dialog.reference_label.text())
        dialog.close()

    def test_empty_name_is_rejected(self) -> None:
        import app.ui.dialogs as dialogs_module

        dialog = FoodItemEditDialog(self._record())
        dialog.name_edit.setText("   ")
        messages: list[str] = []
        original = dialogs_module.show_error
        dialogs_module.show_error = lambda parent, title, message: messages.append(message)
        try:
            dialog._on_accept()
        finally:
            dialogs_module.show_error = original
        self.assertTrue(messages)
        self.assertEqual(dialog.result(), 0)
        dialog.close()


class MealEditDialogTests(unittest.TestCase):
    def test_values_round_trip(self) -> None:
        meal = MealRecord(
            id=5, date="2026-09-14", time="12:30", meal_type="lunch", source="manual",
            note="leftovers",
        )
        dialog = MealEditDialog(meal)
        values = dialog.values()
        self.assertEqual(values["meal_type"], "lunch")
        self.assertEqual(values["date"], "2026-09-14")
        self.assertEqual(values["time"], "12:30")
        self.assertEqual(values["note"], "leftovers")
        dialog.close()


class RecommendationDialogTests(unittest.TestCase):
    def test_recommendation_is_displayed(self) -> None:
        recommendation = parse_recommendation(RECOMMENDATION_PAYLOAD)
        dialog = RecommendationDialog(recommendation, remaining_kcal=800)
        self.assertEqual(dialog.title_text(), recommendation.title)
        self.assertEqual(len(dialog.foods()), len(recommendation.foods))
        self.assertIn(dialog.meal_type(), ("breakfast", "lunch", "dinner", "snack"))
        dialog.close()


class ProfileFormTests(unittest.TestCase):
    def test_set_profile_then_read_back(self) -> None:
        form = ProfileForm()
        profile = UserProfile(
            age=41, sex="female", height_cm=162.5, weight_kg=58.2,
            activity_level="very_active", goal="lose",
        )
        form.set_profile(profile)
        self.assertEqual(form.profile(), profile)
        self.assertIn("kcal", form.targets_label.text())
        form.close()

    def test_helper_functions(self) -> None:
        self.assertTrue(_valid_date("2026-09-14"))
        self.assertFalse(_valid_date("14/09/2026"))
        self.assertFalse(_valid_date(""))
        self.assertIn(_default_meal_index(), range(4))


if __name__ == "__main__":
    unittest.main()
