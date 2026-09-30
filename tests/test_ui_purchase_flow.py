"""界面流程测试：「按我的购买条件推荐」。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.ai.schemas import FoodItem  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.database.models import PurchasePreferences, UserProfile  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.nutrition.purchase import DEFAULT_EASY_INGREDIENTS  # noqa: E402
from app.ui import main_window as main_window_module  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.utils.config import Settings  # noqa: E402
from app.utils.credentials import ApiKeyStore  # noqa: E402
from tests.fakes import (  # noqa: E402
    RECOMMENDATION_WITH_AVOCADO,
    FakeDeepSeekClient,
)
from tests.test_api_key import FakeCredentialStore  # noqa: E402

_APP: QApplication | None = None

EASY_SENTENCE = "我附近超市可以买到鸡胸肉、鸡蛋、牛奶、西兰花、土豆和米饭。"


def setUpModule() -> None:  # noqa: N802
    global _APP
    _APP = QApplication.instance() or QApplication([])
    apply_app_style(_APP)


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return predicate()


class PurchaseFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "ui.db")
        self.db.initialize()
        self.store = ApiKeyStore(
            env_file=self.tmp / ".env",
            credential_store=FakeCredentialStore(available=False),
        )
        self.store.env_file.set("sk-test-key-1234567890")
        self.fake = FakeDeepSeekClient(Settings(api_key="sk-test-key-1234567890"))
        self.context = AppContext(
            settings=Settings(api_key="sk-test-key-1234567890", model="deepseek-flash"),
            database=self.db,
            api_key_store=self.store,
            api_client_factory=lambda _settings: self.fake,
        )
        self._previous_env = os.environ.pop("DEEPSEEK_API_KEY", None)
        self.infos: list[str] = []
        self.errors: list[str] = []
        self._patch_messages()
        self.window = MainWindow(self.context)
        self.window.save_profile(
            UserProfile(
                age=25, sex="male", height_cm=178, weight_kg=80,
                activity_level="moderate", goal="lose",
            )
        )
        self.window.save_items(self._sample_items(), meal_type="lunch")

    def tearDown(self) -> None:
        worker = getattr(self.window, "_worker", None)
        if worker is not None:
            wait_until(lambda: not worker.isRunning(), timeout=5.0)
        QApplication.processEvents()
        self.window.close()
        QApplication.processEvents()
        if self._previous_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._previous_env
        self._tmp.cleanup()

    def _patch_messages(self) -> None:
        original_info = main_window_module.show_info
        original_error = main_window_module.show_error
        main_window_module.show_info = lambda parent, title, message: self.infos.append(message)
        main_window_module.show_error = lambda parent, title, message: self.errors.append(message)

        def restore() -> None:
            main_window_module.show_info = original_info
            main_window_module.show_error = original_error

        self.addCleanup(restore)

    # ------------------------------------------------------------------ 设置
    def _fill_purchase_form(self) -> None:
        form = self.window.settings.purchase_form
        form.country_combo.setCurrentIndex(form.country_combo.findData("United Kingdom"))
        for name in ("Tesco", "Lidl"):
            form.store_boxes[name].setChecked(True)
        form.store_other.setText("韩国超市")
        form.easy_edit.setPlainText(EASY_SENTENCE)
        form.hard_edit.setPlainText("牛油果、藜麦、羽衣甘蓝")
        form.budget_combo.setCurrentIndex(form.budget_combo.findData("low"))
        for name in ("微波炉", "电饭锅"):
            form.equipment_boxes[name].setChecked(True)
        form.time_spin.setValue(15)
        form.diet_edit.setText("不吃猪肉")

    def test_saving_purchase_preferences_persists_everything(self) -> None:
        self.window.goto("settings")
        self._fill_purchase_form()
        self.window.settings.purchase_form.save_button.click()

        stored = self.db.get_purchase_preferences()
        self.assertEqual(stored.country, "United Kingdom")
        self.assertEqual(stored.stores, ["Tesco", "Lidl", "韩国超市"])
        self.assertEqual(
            stored.easy_ingredients(),
            ["鸡胸肉", "鸡蛋", "牛奶", "西兰花", "土豆", "米饭"],
        )
        self.assertEqual(stored.hard_ingredients(), ["牛油果", "藜麦", "羽衣甘蓝"])
        self.assertEqual(stored.budget, "low")
        self.assertEqual(stored.equipment, ["微波炉", "电饭锅"])
        self.assertEqual(stored.max_cooking_minutes, 15)
        self.assertEqual(stored.diet_preferences, "不吃猪肉")
        self.assertIn("已保存", self.window.settings.purchase_form.status_label.text())

    def test_form_reloads_saved_values(self) -> None:
        self.window.goto("settings")
        self._fill_purchase_form()
        self.window.settings.purchase_form.save_button.click()
        self.window.goto("today")
        self.window.goto("settings")
        form = self.window.settings.purchase_form
        self.assertEqual(form.country_combo.currentData(), "United Kingdom")
        self.assertTrue(form.store_boxes["Tesco"].isChecked())
        self.assertEqual(form.store_other.text(), "韩国超市")
        self.assertIn("鸡胸肉", form.easy_edit.toPlainText())
        self.assertEqual(form.time_spin.value(), 15)
        self.assertEqual(form.diet_edit.text(), "不吃猪肉")

    def test_parsed_preview_counts_the_ingredients(self) -> None:
        self.window.goto("settings")
        form = self.window.settings.purchase_form
        form.easy_edit.setPlainText(EASY_SENTENCE)
        form.hard_edit.setPlainText("牛油果、藜麦")
        self.assertIn("6", form.easy_preview.text())
        self.assertIn("鸡胸肉", form.easy_preview.text())
        self.assertIn("2", form.hard_preview.text())
        self.assertIn("牛油果", form.hard_preview.text())

    def test_empty_easy_list_shows_the_default_hint(self) -> None:
        self.window.goto("settings")
        self.window.settings.purchase_form.easy_edit.setPlainText("")
        self.assertIn(
            DEFAULT_EASY_INGREDIENTS[0],
            self.window.settings.purchase_form.easy_preview.text(),
        )

    # ------------------------------------------------------------------ 推荐
    def test_recommendation_payload_carries_the_purchase_conditions(self) -> None:
        self.window.goto("settings")
        self._fill_purchase_form()
        self.window.settings.purchase_form.save_button.click()

        self.window.on_recommend_next_meal()
        self.assertTrue(
            wait_until(lambda: bool(self.fake.recommendation_calls)),
            "没有发出推荐请求",
        )
        context = self.fake.recommendation_calls[0]
        self.assertEqual(context["location"], "United Kingdom")
        self.assertEqual(context["preferred_stores"], ["Tesco", "Lidl", "韩国超市"])
        self.assertEqual(
            context["easy_to_buy_ingredients"],
            ["鸡胸肉", "鸡蛋", "牛奶", "西兰花", "土豆", "米饭"],
        )
        self.assertFalse(context["using_default_ingredient_list"])
        self.assertEqual(
            context["inconvenient_ingredients"], ["牛油果", "藜麦", "羽衣甘蓝"]
        )
        self.assertEqual(context["budget"], "low")
        self.assertEqual(context["cooking_equipment"], ["微波炉", "电饭锅"])
        self.assertEqual(context["max_cooking_minutes"], 15)
        self.assertEqual(context["diet_preferences"], "不吃猪肉")
        # 营养条件也依然带着
        self.assertIn("remaining", context)
        self.assertIn("daily_targets", context)
        self.assertEqual(context["goal"], "lose")
        self.assertGreater(context["remaining"]["calories_kcal"], 0)

    def test_without_settings_the_default_common_ingredients_are_sent(self) -> None:
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: bool(self.fake.recommendation_calls)))
        context = self.fake.recommendation_calls[0]
        self.assertTrue(context["using_default_ingredient_list"])
        self.assertEqual(context["easy_to_buy_ingredients"], list(DEFAULT_EASY_INGREDIENTS))
        self.assertEqual(context["inconvenient_ingredients"], [])
        self.assertIn("鸡胸肉", context["easy_to_buy_ingredients"])
        self.assertNotIn("藜麦", context["easy_to_buy_ingredients"])

    def test_recommendation_card_shows_difficulty_and_shopping_list(self) -> None:
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: self.window._recommendation is not None))
        dashboard = self.window.dashboard
        self.assertFalse(dashboard.recommendation_card.isHidden())
        self.assertIn("购买难度", dashboard.recommendation_difficulty.text())
        self.assertIn("容易", dashboard.recommendation_difficulty.text())
        self.assertIn("鸡胸肉", dashboard.recommendation_shopping.text())
        self.assertIn("约 500 g", dashboard.recommendation_shopping.text())
        self.assertFalse(dashboard.recommendation_shopping_note.isHidden())
        self.assertIn("所需食材", dashboard.recommendation_ingredients_title.text())
        self.assertTrue(dashboard.recommendation_warning.isHidden())

    def test_inconvenient_ingredient_in_the_answer_is_flagged(self) -> None:
        self.window.goto("settings")
        self._fill_purchase_form()
        self.window.settings.purchase_form.save_button.click()

        self.fake.recommendation_payload = RECOMMENDATION_WITH_AVOCADO
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: self.window._recommendation is not None))

        dashboard = self.window.dashboard
        self.assertFalse(dashboard.recommendation_warning.isHidden())
        warning = dashboard.recommendation_warning.text()
        self.assertIn("不方便购买", warning)
        self.assertTrue("牛油果" in warning or "藜麦" in warning)
        self.assertIn("不容易", dashboard.recommendation_difficulty.text())

    def test_saving_preferences_clears_an_old_recommendation(self) -> None:
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: self.window._recommendation is not None))
        self.assertFalse(self.window.dashboard.recommendation_card.isHidden())

        self.window.goto("settings")
        self.window.settings.purchase_form.easy_edit.setPlainText("鸡蛋、米饭")
        self.window.settings.purchase_form.save_button.click()
        self.assertIsNone(self.window._recommendation)
        self.assertTrue(self.window.dashboard.recommendation_card.isHidden())

    def test_dashboard_shows_the_purchase_summary(self) -> None:
        self.window.goto("settings")
        self._fill_purchase_form()
        self.window.settings.purchase_form.save_button.click()
        summary = self.window.dashboard.purchase_summary.text()
        self.assertIn("英国", summary)
        self.assertIn("Tesco", summary)
        self.assertIn("低预算", summary)

    def test_logging_a_recommended_meal_still_works(self) -> None:
        self.window.on_recommend_next_meal()
        self.assertTrue(wait_until(lambda: self.window._recommendation is not None))
        items = list(self.window._recommendation.foods)  # type: ignore[union-attr]
        self.window.save_items(items, meal_type="dinner", source="manual", note="AI 推荐")
        meals = self.db.meals_for_date(self.window.dashboard.current_date())
        self.assertTrue(any(meal.meal_type == "dinner" for meal in meals))

    # -------------------------------------------------------------- 界面文本
    def test_ui_text_is_chinese(self) -> None:
        self.window.goto("settings")
        form = self.window.settings.purchase_form
        texts = [label.text() for label in form.findChildren(QLabel)]
        joined = " ".join(texts)
        self.assertIn(tr("purchase.card_title"), joined)
        self.assertIn(tr("purchase.easy_label"), joined)
        self.assertIn(tr("purchase.hard_label"), joined)
        self.assertIn(tr("purchase.budget"), joined)
        self.assertEqual(self.window.dashboard.recommend_button.text(), tr("dashboard.recommend"))
        self.assertIn("购买条件", self.window.dashboard.recommend_button.text())

    def test_forbidden_english_words_are_absent(self) -> None:
        self.window.goto("settings")
        form = self.window.settings.purchase_form
        blob = " ".join(
            [label.text() for label in form.findChildren(QLabel)]
            + [box.text() for box in form.store_boxes.values()]
            + [box.text() for box in form.equipment_boxes.values()]
            + [form.save_button.text()]
        )
        for forbidden in ("Easy", "Moderate", "Hard", "Save", "Purchasable", "Budget"):
            self.assertNotIn(forbidden, blob)

    def _sample_items(self) -> list[FoodItem]:
        return [
            FoodItem(
                name="鸡胸肉",
                estimated_weight_g=150,
                calories_kcal=248,
                protein_g=46.5,
                carbs_g=0,
                fat_g=5.4,
                confidence=0.9,
            ),
            FoodItem(
                name="米饭",
                estimated_weight_g=200,
                calories_kcal=260,
                protein_g=5.4,
                carbs_g=56.4,
                fat_g=0.6,
                confidence=0.9,
            ),
        ]


class PurchasePreferencesDatabaseTest(unittest.TestCase):
    """数据库层：保存 / 读取购买条件。"""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "p.db")
            db.initialize()
            self.assertFalse(db.get_purchase_preferences().is_configured())

            db.save_purchase_preferences(
                PurchasePreferences(
                    country="China",
                    stores=["中国超市"],
                    easy_text="鸡蛋、牛奶",
                    hard_text="牛油果",
                    budget="unlimited",
                    equipment=["微波炉"],
                    max_cooking_minutes=20,
                    diet_preferences="不吃辣",
                )
            )
            stored = db.get_purchase_preferences()
            self.assertEqual(stored.country, "China")
            self.assertEqual(stored.stores, ["中国超市"])
            self.assertEqual(stored.easy_ingredients(), ["鸡蛋", "牛奶"])
            self.assertEqual(stored.hard_ingredients(), ["牛油果"])
            self.assertEqual(stored.budget, "unlimited")
            self.assertEqual(stored.equipment, ["微波炉"])
            self.assertEqual(stored.max_cooking_minutes, 20)
            self.assertEqual(stored.diet_preferences, "不吃辣")
            self.assertTrue(stored.is_configured())

            # 再保存一次是覆盖，不会产生第二行
            db.save_purchase_preferences(PurchasePreferences(easy_text="米饭"))
            row = db._query_one("SELECT COUNT(*) AS n FROM purchase_preferences")
            assert row is not None
            self.assertEqual(int(row["n"]), 1)
            self.assertEqual(db.get_purchase_preferences().easy_ingredients(), ["米饭"])
            db.close()


if __name__ == "__main__":
    unittest.main()
