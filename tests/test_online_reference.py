"""在线参考（第二参考）：schema、触发时机、错误路径、隔离性与阈值。

全部离线：DeepSeek 的响应来自 ``tests/fakes.py``，网络调用一律被假的客户端
记录而不是真的发出去。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai.errors import (  # noqa: E402
    AINetworkError,
    AIResponseError,
    AIServiceError,
    AITimeoutError,
    MissingAPIKeyError,
    OnlineReferenceUnavailableError,
)
from app.ai.parser import parse_online_nutrition  # noqa: E402
from app.ai.schemas import OnlineNutritionReference  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.database.database import Database  # noqa: E402
from app.database.models import FoodItemRecord, UserProfile  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.nutrition.food_data import lookup  # noqa: E402
from app.nutrition.reference_compare import (  # noqa: E402
    DIFFERENCE_THRESHOLD,
    build_comparison,
    is_meaningful_difference,
    relative_difference,
)
from app.ui.dialogs import (  # noqa: E402
    ConfirmFoodDialog,
    FoodItemEditDialog,
    with_reference_sources,
)
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.online_reference import OnlineReferenceCard  # noqa: E402
from app.ui.styles import apply_app_style  # noqa: E402
from app.ui.workers import ApiWorker  # noqa: E402
from app.utils.config import Settings  # noqa: E402
from tests.fakes import (  # noqa: E402
    REFERENCE_PAYLOAD,
    REFERENCE_PAYLOAD_MATCHING_LOCAL,
    FakeDeepSeekClient,
)

_APP: QApplication | None = None


def setUpModule() -> None:  # noqa: N802
    global _APP
    _APP = QApplication.instance() or QApplication([])
    apply_app_style(_APP)


def pump(predicate, timeout: float = 5.0) -> bool:
    """Process Qt events until ``predicate`` is true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def food_item(**overrides):
    from app.ai.schemas import FoodItem

    values = {
        "name": "egg",
        "amount": 2,
        "unit": "pieces",
        "estimated_weight_g": 100,
        "calories_kcal": 143,
        "protein_g": 12.6,
        "carbs_g": 0.7,
        "fat_g": 9.5,
        "sugar_g": 0.4,
        "fibre_g": 0,
        "sodium_mg": 142,
        "confidence": 0.9,
    }
    values.update(overrides)
    return FoodItem(**values)


class AutoConfirmDialog(ConfirmFoodDialog):
    """A real confirmation dialog that confirms itself.

    The widget (and therefore the online-reference card) is the real one, so
    "分析 → 确认 → 保存 不会发请求" is tested against production code; only the
    modal event loop is skipped. ``_on_confirm`` runs exactly like a click on the
    confirm button, which is what fills ``confirmed_items()``.
    """

    def exec(self) -> int:
        self._on_confirm()
        return self.result()


def reference_job_factory(client):
    """The same shape ``MainWindow`` produces, but returning a controllable fake.

    ``start()`` is what actually performs the request: a factory that returns a
    worker without starting it looks identical to the card, but never runs.
    """

    def factory(on_success, on_error, food_name: str) -> ApiWorker:
        worker = ApiWorker(
            lambda: client.fetch_nutrition_reference(food_name),
            label="query online nutrition reference",
        )
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.start()
        return worker

    return factory


def card_factory(client, *, name: str = "egg"):
    """A 2-argument job factory for :class:`OnlineReferenceCard`.

    ``with_reference_sources`` is the same adapter ``MainWindow`` and the dialogs
    use, so the tests exercise the production wiring instead of a look-alike.
    """
    return with_reference_sources(
        reference_job_factory(client), food_item, lambda: name
    )


# ---------------------------------------------------------------------------
# 1. Schema：合法 / 边界 / 非法
# ---------------------------------------------------------------------------


class OnlineReferenceSchemaTests(unittest.TestCase):
    def test_valid_answer_is_accepted(self) -> None:
        reference = parse_online_nutrition(REFERENCE_PAYLOAD, name="egg")
        self.assertEqual(reference.name, "egg")
        self.assertAlmostEqual(reference.calories_kcal, 160.0)
        self.assertAlmostEqual(reference.sodium_mg, 124.0)
        self.assertEqual(reference.unknown_fields(), ("sugar_g",))

    def test_boundary_values_are_accepted(self) -> None:
        reference = OnlineNutritionReference.model_validate(
            {
                "name": "oil",
                "calories_kcal": 884,
                "protein_g": 0,
                "carbs_g": 0,
                "fat_g": 100,
                "sugar_g": 0,
                "fibre_g": 0,
                "sodium_mg": 0,
            }
        )
        self.assertEqual(reference.known_fields(), reference.field_names())
        self.assertTrue(reference.is_complete())

    def test_zero_is_kept_and_is_not_unknown(self) -> None:
        reference = OnlineNutritionReference.model_validate(
            {"name": "water", "calories_kcal": 0, "sugar_g": None}
        )
        self.assertEqual(reference.calories_kcal, 0.0)
        self.assertIsNone(reference.sugar_g)
        # 0 与「未知」是两件事，必须能区分
        self.assertIn("calories_kcal", reference.known_fields())
        self.assertIn("sugar_g", reference.unknown_fields())

    def test_negative_value_is_rejected(self) -> None:
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"egg","calories_kcal":-5}')

    def test_absurd_value_is_rejected(self) -> None:
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"egg","calories_kcal":50000}')
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"egg","protein_g":999999}')

    def test_kcal_string_is_accepted_but_junk_is_rejected(self) -> None:
        reference = parse_online_nutrition('{"name":"egg","calories_kcal":"140 kcal"}')
        self.assertAlmostEqual(reference.calories_kcal, 140.0)
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"egg","calories_kcal":"about a lot"}')

    def test_out_of_range_numbers_are_rejected_not_truncated(self) -> None:
        """1e400 / 无限大必须是「非法响应」，而不是被截断成一个小数字。"""
        for payload in (
            '{"name":"egg","calories_kcal":"1e400"}',
            '{"name":"egg","calories_kcal":1e308}',
            '{"name":"egg","calories_kcal":"inf"}',
            '{"name":"egg","calories_kcal":"nan"}',
            '{"name":"egg","sodium_mg":"1e9"}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(OnlineReferenceUnavailableError):
                    parse_online_nutrition(payload)
        # 正常范围的科学计数法仍然可用
        ok = parse_online_nutrition('{"name":"egg","calories_kcal":"1.43e2"}')
        self.assertAlmostEqual(ok.calories_kcal, 143.0)

    def test_impossible_macros_are_rejected(self) -> None:
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition(
                '{"name":"egg","protein_g":60,"carbs_g":60,"fat_g":60}'
            )
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition(
                '{"name":"rice","calories_kcal":900,"protein_g":2,"carbs_g":28,"fat_g":1}'
            )
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"juice","carbs_g":5,"sugar_g":40}')

    def test_missing_fields_stay_null_and_never_become_zero(self) -> None:
        reference = parse_online_nutrition('{"name":"egg","calories_kcal":143}')
        self.assertAlmostEqual(reference.calories_kcal, 143.0)
        for field in reference.field_names():
            if field == "calories_kcal":
                continue
            self.assertIsNone(
                getattr(reference, field),
                f"{field} 缺失时必须是 null，不能用 0 冒充未知",
            )

    def test_an_answer_without_any_value_is_rejected(self) -> None:
        with self.assertRaises(OnlineReferenceUnavailableError):
            parse_online_nutrition('{"name":"egg","note":"not sure"}')

    def test_units_are_converted_during_parsing(self) -> None:
        reference = parse_online_nutrition(
            '{"name":"egg","calories":"1500 kJ","sodium":"0.5 g"}'
        )
        self.assertAlmostEqual(reference.calories_kcal, 1500 * 0.239, places=3)
        self.assertAlmostEqual(reference.sodium_mg, 500.0)

    def test_values_stated_per_kg_are_scaled_to_100_g(self) -> None:
        with self.assertRaises(
            OnlineReferenceUnavailableError
        ):  # 数值型 basis 必须被拒绝，而不是当成营养素
            parse_online_nutrition('{"name":"egg","basis":100,"calories_kcal":143}')
        for basis in ("per kg", "per_kg", "每千克", " PER  KG "):
            with self.subTest(basis=basis):
                reference = parse_online_nutrition(
                    json.dumps(
                        {"name": "egg", "basis": basis, "calories_kcal": 1430}
                    )
                )
                self.assertAlmostEqual(reference.calories_kcal, 143.0)
        per_100g = parse_online_nutrition(
            '{"name":"egg","basis":"per 100 g","calories_kcal":143}'
        )
        self.assertAlmostEqual(per_100g.calories_kcal, 143.0)

    def test_field_names_match_the_local_table(self) -> None:
        from app.nutrition.food_data import LOCAL_FOODS

        self.assertEqual(
            OnlineNutritionReference.field_names(),
            tuple(LOCAL_FOODS[0].per_100g),
        )

    def test_unparsable_answers_raise_the_hide_signal(self) -> None:
        for raw in ("", "   ", "I think it is about 140 kcal", "null", "42", "[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(OnlineReferenceUnavailableError):
                    parse_online_nutrition(raw)


# ---------------------------------------------------------------------------
# 2. 对比逻辑与阈值
# ---------------------------------------------------------------------------


class ReferenceComparisonTests(unittest.TestCase):
    def _comparison(self, payload: str):
        reference = parse_online_nutrition(payload, name="egg")
        return build_comparison(food_item(), reference)

    def test_both_sides_are_per_100g_and_side_by_side(self) -> None:
        comparison = self._comparison(REFERENCE_PAYLOAD)
        # 本地：143 kcal / 100 g；在线：160 kcal / 100 g
        self.assertAlmostEqual(comparison.local_value("calories_kcal"), 143.0)
        self.assertAlmostEqual(comparison.online_value("calories_kcal"), 160.0)
        self.assertAlmostEqual(comparison.local_value("sodium_mg"), 142.0)
        self.assertAlmostEqual(comparison.online_value("sodium_mg"), 124.0)
        self.assertEqual(comparison.local_source, "Egg, whole, cooked")

    def test_unknown_online_field_is_none_not_zero(self) -> None:
        comparison = self._comparison(REFERENCE_PAYLOAD)
        self.assertIsNone(comparison.online_value("sugar_g"))
        row = [r for r in comparison.rows if r.field == "sugar_g"][0]
        self.assertEqual(row.online_text(), tr("reference.unknown"))
        # 在线是 0 的字段要显示 0，而不是「未知」
        fibre = [r for r in comparison.rows if r.field == "fibre_g"][0]
        self.assertEqual(fibre.online, 0.0)
        self.assertNotEqual(fibre.online_text(), tr("reference.unknown"))

    def test_difference_above_threshold_triggers_the_hint(self) -> None:
        # 320 kcal 全部来自脂肪（29 g ≈ 261 kcal，仍在合理范围内）=> 与本地 143 差 124%
        comparison = self._comparison(
            '{"name":"egg","calories_kcal":320,"protein_g":5,"fat_g":33,"carbs_g":1}'
        )
        self.assertGreater(relative_difference(320.0, 143.0), DIFFERENCE_THRESHOLD)
        self.assertTrue(comparison.exceeds_threshold())
        self.assertEqual(comparison.difference_note(), tr("reference.difference"))
        # 语气中立：不暗示任何一方是对的
        self.assertNotIn("AI 正确", comparison.difference_note())

    def test_difference_below_threshold_shows_no_hint(self) -> None:
        comparison = self._comparison(REFERENCE_PAYLOAD_MATCHING_LOCAL)
        self.assertFalse(comparison.exceeds_threshold())
        self.assertEqual(comparison.difference_note(), "")

    def test_threshold_helper_exact_behaviour(self) -> None:
        self.assertFalse(is_meaningful_difference(143.0, 143.0))
        self.assertFalse(is_meaningful_difference(143.0 * 1.29, 143.0))  # 29% < 30%
        self.assertTrue(is_meaningful_difference(143.0 * 1.31, 143.0))  # 31% > 30%
        # 缺失的一侧无法比较，不能提示
        self.assertFalse(is_meaningful_difference(None, 143.0))
        self.assertFalse(is_meaningful_difference(143.0, None))
        # 小数值的相对差异是噪声
        self.assertFalse(is_meaningful_difference(0.2, 0.4))

    def test_unknown_weight_falls_back_to_the_local_table(self) -> None:
        item = food_item(estimated_weight_g=None, calories_kcal=143)
        comparison = build_comparison(item, parse_online_nutrition(REFERENCE_PAYLOAD))
        self.assertAlmostEqual(comparison.local_value("calories_kcal"), 143.0)

    def test_unlisted_food_still_compares_the_item_itself(self) -> None:
        # 本地参考表里没有这道菜，所以只在「这条记录」和在线值之间比较
        item = food_item(
            name="zzz unknown dish", calories_kcal=200, estimated_weight_g=100
        )
        self.assertIsNone(
            lookup("zzz unknown dish"), "the test needs a food the local table lacks"
        )
        comparison = build_comparison(item, parse_online_nutrition('{"calories_kcal":270}'))
        self.assertFalse(comparison.has_local())
        self.assertAlmostEqual(comparison.local_value("calories_kcal"), 200.0)
        self.assertTrue(comparison.exceeds_threshold())


# ---------------------------------------------------------------------------
# 3. 触发路径：不点按钮就不发请求
# ---------------------------------------------------------------------------


class NoRequestWithoutAClickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ui.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.fake = FakeDeepSeekClient(Settings(api_key="test-key"))
        self.context = AppContext(
            settings=Settings(api_key="test-key"),
            database=self.db,
            api_client_factory=lambda _settings: self.fake,
        )
        self.window = MainWindow(self.context)
        self.window.confirm_dialog_factory = self._confirm_dialog
        self.dialogs: list[ConfirmFoodDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
        self.window.close()
        self.tmp.cleanup()

    def _confirm_dialog(self, items, source: str = "manual", note: str = "") -> ConfirmFoodDialog:
        dialog = AutoConfirmDialog(
            items,
            source=source,
            note=note,
            reference_job_factory=reference_job_factory(self.fake),
        )
        self.dialogs.append(dialog)
        return dialog

    def _with_profile(self) -> None:
        self.window.save_profile(
            UserProfile(
                age=30, sex="male", height_cm=180, weight_kg=80,
                activity_level="moderate", goal="maintain",
            )
        )

    def test_analysis_and_save_never_query_the_online_reference(self) -> None:
        """分析 → 确认 → 保存 的整条流程里，一次在线请求都不能发生。"""
        self._with_profile()
        self.window.analyze_text("两个鸡蛋", "breakfast")
        self.assertTrue(pump(lambda: self.db.count_meals() == 1), "meal was not saved")

        self.assertEqual(len(self.fake.text_calls), 1)
        self.assertEqual(self.fake.reference_calls, [])
        self.assertEqual(self.fake.recommendation_calls, [])
        self.assertEqual(len(self.dialogs), 1)
        # 对话框建好了，结果区还没有内容，也没有任何请求
        self.assertFalse(self.dialogs[0].reference_card.is_showing())
        self.assertEqual(self.dialogs[0].reference_card.query_count, 0)

    def test_the_button_is_visible_before_it_is_ever_used(self) -> None:
        """回归测试：按钮必须一开始就在窗口里，否则用户根本不知道有这个功能。

        以前的实现把整个卡片（连同按钮）一起藏起来，结果按钮只有点过之后才
        出现 —— 一个永远点不到的功能。
        """
        self._with_profile()
        self.window.analyze_text("两个鸡蛋", "breakfast")
        self.assertTrue(pump(lambda: self.db.count_meals() == 1))

        card = self.dialogs[0].reference_card
        card.show()  # 对话框在真实使用中会显示；测试里对话框不显示，故只影响可见性判断
        QApplication.processEvents()

        # 按钮在这里，而且可以点
        self.assertTrue(card.button().isVisibleTo(card))
        self.assertTrue(card.button().isEnabled())
        self.assertEqual(card.button().text(), tr("reference.query_button"))
        # 但结果区还没有内容
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())

    def test_the_edit_dialog_also_shows_the_button_right_away(self) -> None:
        record = FoodItemRecord(
            id=1, meal_id=1, name="鸡蛋", estimated_weight_g=100, calories_kcal=143
        )
        dialog = FoodItemEditDialog(
            record, reference_job_factory=reference_job_factory(self.fake), parent=None
        )
        self.dialogs.append(dialog)
        dialog.show()
        QApplication.processEvents()

        card = dialog.reference_card
        self.assertTrue(card.button().isVisibleTo(card))
        self.assertTrue(card.button().isEnabled())
        self.assertFalse(card.is_showing())
        self.assertEqual(self.fake.reference_calls, [])

    def test_switching_dates_and_history_never_query_the_online_reference(self) -> None:
        self._with_profile()
        self.window.refresh_all()
        self.window.goto("history")
        self.window.on_history_day_selected("2026-09-13")
        self.window.on_open_day("2026-09-13")
        self.window.goto("today")
        self.assertEqual(self.fake.reference_calls, [])

    def test_editing_numbers_never_queries_the_online_reference(self) -> None:
        self._with_profile()
        meal_id = self.window.save_items([food_item()], meal_type="lunch")
        item_id = self.db.food_items_for_meal(meal_id)[0].id
        record = self.db.get_food_item(item_id)
        dialog = FoodItemEditDialog(
            record, reference_job_factory=reference_job_factory(self.fake)
        )
        self.dialogs.append(dialog)
        dialog.spins["calories_kcal"].setValue(200)
        dialog.spins["estimated_weight_g"].setValue(120)
        dialog._maybe_rescale()
        dialog._apply_reference()
        dialog.item()
        self.assertEqual(self.fake.reference_calls, [])
        self.assertEqual(dialog.reference_card.query_count, 0)

    def test_clicking_the_button_sends_exactly_one_request(self) -> None:
        self._with_profile()
        self.window.analyze_text("两个鸡蛋", "breakfast")
        self.assertTrue(pump(lambda: self.db.count_meals() == 1))
        dialog = self.dialogs[0]

        dialog.reference_card.button().click()
        self.assertTrue(
            pump(lambda: dialog.reference_card.has_result()), "no online reference appeared"
        )
        self.assertEqual(self.fake.reference_calls, ["egg"])
        self.assertEqual(dialog.reference_card.query_count, 1)
        self.assertTrue(dialog.reference_card.is_showing())


# ---------------------------------------------------------------------------
# 4. 错误路径：在线区静默隐藏，界面不崩，本地值不受影响
# ---------------------------------------------------------------------------


class SilentFailureTests(unittest.TestCase):
    def _card(self, client) -> OnlineReferenceCard:
        return OnlineReferenceCard(
            item_provider=food_item,
            name_provider=lambda: "egg",
            job_factory=card_factory(client),
        )

    def _query(self, client) -> OnlineReferenceCard:
        card = self._card(client)
        card.button().click()
        self.assertTrue(
            pump(lambda: card.button().isEnabled()), "the button never came back"
        )
        return card

    def test_401_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = MissingAPIKeyError()
        card = self._query(client)
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())

    def test_429_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AIServiceError(tr("error.rate_limit"))
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_5xx_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AIServiceError(tr("error.server"))
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_timeout_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AITimeoutError()
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_network_error_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AINetworkError()
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_invalid_json_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.reference_payload = "I could not find this food, sorry"
        card = self._query(client)
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())

    def test_empty_answer_hides_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.reference_payload = ""
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_illegal_values_hide_the_online_section(self) -> None:
        client = FakeDeepSeekClient()
        client.reference_payload = '{"name":"egg","calories_kcal":-500}'
        card = self._query(client)
        self.assertFalse(card.is_showing())

    def test_failure_logs_one_warning_and_shows_no_dialog(self) -> None:
        import app.ui.online_reference as module

        logged: list[str] = []
        originals = (module._logger, module.tr_text)
        module._logger = _RecordingLogger(logged)
        module.tr_text = lambda key, **kwargs: tr(key, **kwargs)
        try:
            client = FakeDeepSeekClient()
            client.fail_with = AINetworkError()  # a plain offline failure
            card = OnlineReferenceCard(
                item_provider=food_item,
                name_provider=lambda: "egg",
                job_factory=card_factory(client),
            )
            card.button().click()
            self.assertTrue(pump(lambda: card.button().isEnabled()))
        finally:
            module._logger, module.tr_text = originals
        self.assertFalse(card.is_showing())
        self.assertEqual(len(logged), 1, f"expected exactly one warning, got {logged}")
        self.assertIn("egg", logged[0])
        self.assertNotIn("{name}", logged[0])
        self.assertNotIn("{error}", logged[0])

    def test_a_broken_job_factory_still_hides_instead_of_raising(self) -> None:
        """工厂自己出错时也必须走「静默隐藏」这条路，而不是让界面崩掉。"""
        import app.ui.online_reference as module

        logged: list[str] = []
        originals = (module._logger, module.tr_text)
        module._logger = _RecordingLogger(logged)
        module.tr_text = lambda key, **kwargs: tr(key, **kwargs)
        try:
            client = FakeDeepSeekClient()

            def broken_factory(on_success, on_error):  # pragma: no cover - raises
                raise RuntimeError("boom")

            card = OnlineReferenceCard(
                item_provider=food_item,
                name_provider=lambda: "egg",
                job_factory=broken_factory,
            )
            card.button().click()
        finally:
            module._logger, module.tr_text = originals
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())
        self.assertEqual(client.reference_calls, [])
        self.assertTrue(logged, "a broken factory must leave a trace in the log")

    def test_offline_failure_leaves_the_dialog_untouched(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AINetworkError()
        dialog = ConfirmFoodDialog(
            [food_item()],
            source="manual",
            reference_job_factory=reference_job_factory(client),
        )
        before_totals = dialog.totals_label.text()
        before_items, before_problems = dialog.table.items()

        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.button().isEnabled()))

        self.assertFalse(dialog.reference_card.is_showing())
        self.assertEqual(dialog.totals_label.text(), before_totals)
        after_items, after_problems = dialog.table.items()
        self.assertEqual(before_problems, after_problems)
        self.assertEqual(
            [item.to_record_fields() for item in before_items],
            [item.to_record_fields() for item in after_items],
        )
        # 确认按钮仍然可以正常确认（界面没被卡住）
        dialog._on_confirm()
        self.assertEqual(dialog.result(), 1)
        dialog.close()


class _RecordingLogger:
    """Minimal logger stand-in that records the formatted message."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def warning(self, message: str, *args: object) -> None:
        self._sink.append(str(message))

    def info(self, message: str, *args: object) -> None:  # pragma: no cover
        self._sink.append(str(message))

    def exception(self, message: str, *args: object) -> None:  # pragma: no cover
        self._sink.append(str(message))


# ---------------------------------------------------------------------------
# 5. null 与 0 在界面上的区别
# ---------------------------------------------------------------------------


class UnknownVersusZeroTests(unittest.TestCase):
    def _card(self, payload: str) -> OnlineReferenceCard:
        client = FakeDeepSeekClient()
        client.reference_payload = payload
        card = OnlineReferenceCard(
            item_provider=food_item,
            name_provider=lambda: "egg",
            job_factory=card_factory(client),
        )
        card.button().click()
        self.assertTrue(pump(lambda: card.has_result()))
        return card

    def test_missing_field_shows_unknown(self) -> None:
        card = self._card(REFERENCE_PAYLOAD)  # sugar_g is null
        self.assertEqual(card.online_text("sugar_g"), tr("reference.unknown"))

    def test_missing_field_never_shows_zero(self) -> None:
        card = self._card(REFERENCE_PAYLOAD)
        self.assertNotEqual(card.online_text("sugar_g"), "0 g")
        self.assertNotIn("0 g", card.online_text("sugar_g"))

    def test_real_zero_shows_zero(self) -> None:
        card = self._card(REFERENCE_PAYLOAD)  # fibre_g is a real 0
        self.assertEqual(card.online_text("fibre_g"), "0.0 g")
        self.assertNotEqual(card.online_text("fibre_g"), tr("reference.unknown"))

    def test_kcal_and_sodium_use_their_own_units(self) -> None:
        card = self._card(REFERENCE_PAYLOAD)
        self.assertTrue(card.online_text("calories_kcal").endswith("kcal"))
        self.assertTrue(card.online_text("sodium_mg").endswith("mg"))
        self.assertTrue(card.online_text("protein_g").endswith("g"))

    def test_the_card_says_which_side_is_which(self) -> None:
        """两边口径必须写清楚：左边是记录换算出的每 100 g，右边是在线值。"""
        card = self._card(REFERENCE_PAYLOAD)
        self.assertIn("每 100 g", card.heading.text())
        # 本地参考表匹配到具体条目时，卡片会说出是哪一个
        self.assertIn("Egg, whole, cooked", card.source_label.text())

    def test_an_unlisted_food_says_the_left_side_is_the_record(self) -> None:
        client = FakeDeepSeekClient()
        client.reference_payload = '{"calories_kcal":100}'
        card = OnlineReferenceCard(
            item_provider=lambda: food_item(name="zzz unknown dish"),
            name_provider=lambda: "zzz unknown dish",
            job_factory=card_factory(client, name="zzz unknown dish"),
        )
        card.button().click()
        self.assertTrue(pump(lambda: card.has_result()))
        self.assertEqual(card.source_label.text(), tr("reference.local_missing"))


# ---------------------------------------------------------------------------
# 6. 隔离性：在线值不写库、不参与汇总
# ---------------------------------------------------------------------------


class IsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "iso.db")
        self.db.initialize()
        self.fake = FakeDeepSeekClient(Settings(api_key="test-key"))
        self.window = MainWindow(
            AppContext(
                settings=Settings(api_key="test-key"),
                database=self.db,
                api_client_factory=lambda _settings: self.fake,
            )
        )
        self.window.save_profile(
            UserProfile(
                age=30, sex="male", height_cm=180, weight_kg=80,
                activity_level="moderate", goal="maintain",
            )
        )
        self.meal_id = self.window.save_items([food_item()], meal_type="lunch")
        self.dialogs: list[ConfirmFoodDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
        self.window.close()
        self.tmp.cleanup()

    def _dialog(self, items=None) -> ConfirmFoodDialog:
        dialog = ConfirmFoodDialog(
            items or [food_item()],
            source="manual",
            reference_job_factory=reference_job_factory(self.fake),
        )
        self.dialogs.append(dialog)
        return dialog

    def test_online_values_are_not_written_to_the_database(self) -> None:
        dialog = self._dialog()
        # 在线参考故意给一组完全不同的数值（320 kcal / 900 mg 钠）
        self.fake.reference_payload = (
            '{"name":"egg","calories_kcal":320,"protein_g":20,"fat_g":27,"carbs_g":3}'
        )
        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.has_result()))
        self.assertEqual(dialog.reference_card.online_text("calories_kcal"), "320 kcal")
        self.assertEqual(dialog.reference_card.online_text("sodium_mg"), "未知")

        # 数据库里仍然是原来的 143 kcal —— 在线值一个字都没进去
        items = self.db.food_items_for_meal(self.meal_id)
        self.assertAlmostEqual(items[0].calories_kcal, 143.0)
        self.assertAlmostEqual(items[0].protein_g, 12.6)
        self.assertAlmostEqual(items[0].sodium_mg, 142.0)
        self.assertEqual(len(items), 1)

    def test_online_values_do_not_change_the_daily_totals(self) -> None:
        from datetime import date

        today = date.today().isoformat()
        before = self.db.daily_totals(today)
        dialog = self._dialog()
        self.fake.reference_payload = '{"name":"egg","calories_kcal":320,"protein_g":30}'
        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.has_result()))
        after = self.db.daily_totals(today)
        self.assertEqual(before, after)
        self.assertAlmostEqual(after.calories_kcal, 143.0)

    def test_online_values_do_not_reach_the_confirmed_items(self) -> None:
        dialog = self._dialog()
        self.fake.reference_payload = '{"name":"egg","calories_kcal":320}'
        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.has_result()))
        dialog._on_confirm()
        confirmed = dialog.confirmed_items()
        self.assertEqual(dialog.result(), 1)
        self.assertAlmostEqual(confirmed[0].calories_kcal, 143.0)
        # 确认后写库的也必须是本地/AI 的那个值
        self.window.save_items(confirmed, meal_type="dinner")
        meals = self.db.meals_for_date(date_today())
        dinner = [meal for meal in meals if meal.meal_type == "dinner"][0]
        self.assertAlmostEqual(dinner.items[0].calories_kcal, 143.0)

    def test_no_database_write_happens_while_querying(self) -> None:
        from datetime import date

        today = date.today().isoformat()
        dialog = self._dialog()
        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.has_result()))
        self.assertEqual(self.db.count_meals(), 1)
        self.assertAlmostEqual(self.db.daily_totals(today).calories_kcal, 143.0)
        self.assertEqual(self.db.recent_days(limit=10)[0].meal_count, 1)

    def test_the_window_never_exposes_the_online_value_for_calculation(self) -> None:
        """窗口/对话框不提供任何把在线值取出来做计算的接口。"""
        dialog = self._dialog()
        self.fake.reference_payload = '{"name":"egg","calories_kcal":320}'
        dialog.reference_card.button().click()
        self.assertTrue(pump(lambda: dialog.reference_card.has_result()))
        self.assertFalse(hasattr(dialog, "online_items"))
        self.assertEqual(
            dialog.confirmed_items(), [], "确认之前不应该有任何可保存的条目"
        )


def date_today() -> str:
    from datetime import date

    return date.today().isoformat()


# ---------------------------------------------------------------------------
# 7. 没有 API Key 时按钮的表现
# ---------------------------------------------------------------------------


class WithoutApiKeyTests(unittest.TestCase):
    def test_button_is_disabled_and_explains_itself(self) -> None:
        card = OnlineReferenceCard(
            item_provider=food_item,
            name_provider=lambda: "egg",
            job_factory=None,
        )
        self.assertFalse(card.button().isEnabled())
        self.assertEqual(card.button().toolTip(), tr("reference.needs_key"))
        card.button().click()  # 点了也不会有任何请求
        self.assertEqual(card.query_count, 0)
        self.assertFalse(card.is_showing())

    def test_main_window_returns_no_factory_without_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "nokey.db")
            db.initialize()
            window = MainWindow(
                AppContext(
                    settings=Settings(api_key=""),
                    database=db,
                    api_client_factory=lambda _settings: FakeDeepSeekClient(),
                )
            )
            self.assertIsNone(window.reference_job_factory())
            window.close()

    def test_main_window_returns_a_factory_with_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "key.db")
            db.initialize()
            fake = FakeDeepSeekClient(Settings(api_key="test-key"))
            window = MainWindow(
                AppContext(
                    settings=Settings(api_key="test-key"),
                    database=db,
                    api_client_factory=lambda _settings: fake,
                )
            )
            factory = window.reference_job_factory()
            self.assertIsNotNone(factory)
            worker = factory(lambda value: None, lambda message: None, "egg")
            self.assertTrue(pump(lambda: not worker.isRunning()), "the worker never ran")
            self.assertEqual(fake.reference_calls, ["egg"])
            window.close()


# ---------------------------------------------------------------------------
# 8. 请求失败时的对话返回值（不让 worker 的错误文案泄漏到界面上）
# ---------------------------------------------------------------------------


class WorkerFailureMessageTests(unittest.TestCase):
    def test_api_errors_arrive_as_user_messages(self) -> None:
        client = FakeDeepSeekClient()
        client.fail_with = AIResponseError()
        collected: list[str] = []
        worker = reference_job_factory(client)(lambda value: None, collected.append, "egg")
        self.assertTrue(pump(lambda: bool(collected)))
        self.assertEqual(collected, [AIResponseError().user_message])


if __name__ == "__main__":
    unittest.main()
