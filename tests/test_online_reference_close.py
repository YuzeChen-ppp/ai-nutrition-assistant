"""Regression tests for the online reference around closing a dialog.

Three things must hold when the user closes the window while a request is in
flight:

* the process must not abort with "QThread: Destroyed while thread is still
  running" (a QThread may not outlive its owner);
* a late answer must not re-show a closed dialog or leave the card visible;
* nothing else in the dialog (local reference values, table contents) changes.

Every worker here is given a bounded sleep, so closing the dialog really does
wait for it - exactly like a real request that returns before the wait expires.
"""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database.models import FoodItemRecord  # noqa: E402
from app.ui.dialogs import ConfirmFoodDialog, FoodItemEditDialog  # noqa: E402
from app.ui.online_reference import WORKER_WAIT_MS  # noqa: E402
from app.ui.workers import ApiWorker  # noqa: E402
from tests.test_online_reference import food_item  # noqa: E402

_APP: QApplication | None = None

#: Comfortably shorter than the card's own wait, so the thread always finishes.
SLOW_JOB_SECONDS = 0.3


def setUpModule() -> None:  # noqa: N802
    global _APP
    _APP = QApplication.instance() or QApplication([])


def slow_factory(seconds: float = SLOW_JOB_SECONDS):
    """A job factory whose answer arrives after a short, bounded delay."""

    def factory(on_success, on_error, food_name: str) -> ApiWorker:
        def job():
            time.sleep(seconds)
            return "late answer"

        worker = ApiWorker(job, label=f"slow online reference for {food_name!r}")
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.start()
        return worker

    return factory


def pump(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


class ClosingDuringARequestTests(unittest.TestCase):
    def test_closing_the_dialog_while_the_request_runs_does_not_crash(self) -> None:
        dialog = ConfirmFoodDialog(
            [food_item()],
            "manual",
            "",
            reference_job_factory=slow_factory(),
            parent=None,
        )
        dialog.show()
        QApplication.processEvents()
        dialog.reference_card.button().click()

        # The user closes the dialog while the request is still in flight.
        dialog.close()
        # Give Qt a chance to complain about a destroyed-but-running thread.
        pump(lambda: False, timeout=0.2)
        for _ in range(3):
            QApplication.processEvents()
            time.sleep(0.05)

        card = dialog.reference_card
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())

    def test_a_late_answer_never_reopens_a_closed_dialog(self) -> None:
        dialog = ConfirmFoodDialog(
            [food_item()],
            "manual",
            "",
            reference_job_factory=slow_factory(),
            parent=None,
        )
        card = dialog.reference_card
        card.button().click()
        dialog.close()
        # Wait past the moment the answer arrives: it must be dropped.
        pump(lambda: False, timeout=SLOW_JOB_SECONDS + 0.3)

        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())
        self.assertEqual(card.online_text("calories_kcal"), "")
        self.assertEqual(card.online_text("sugar_g"), "")

    def test_closing_does_not_touch_the_local_values(self) -> None:
        dialog = ConfirmFoodDialog(
            [food_item()],
            "manual",
            "",
            reference_job_factory=slow_factory(),
            parent=None,
        )
        before = dialog.totals_label.text()
        rows_before = dialog.table.rowCount()
        card = dialog.reference_card
        card.button().click()
        dialog.close()

        self.assertEqual(dialog.totals_label.text(), before)
        self.assertEqual(dialog.table.rowCount(), rows_before)
        self.assertFalse(card.is_showing())

    def test_shutdown_is_safe_without_a_request(self) -> None:
        dialog = ConfirmFoodDialog([food_item()], "manual", "", parent=None)
        dialog.close()  # never queried: shutdown must be a no-op
        dialog.reference_card.shutdown()
        self.assertFalse(dialog.reference_card.is_showing())

    def test_shutdown_is_idempotent(self) -> None:
        dialog = ConfirmFoodDialog(
            [food_item()],
            "manual",
            "",
            reference_job_factory=slow_factory(),
            parent=None,
        )
        dialog.reference_card.button().click()
        dialog.reference_card.shutdown()
        dialog.reference_card.shutdown()
        dialog.close()
        self.assertFalse(dialog.reference_card.is_showing())

    def test_the_edit_dialog_closes_the_same_way(self) -> None:
        record = FoodItemRecord(
            id=1, meal_id=1, name="egg", estimated_weight_g=100, calories_kcal=143
        )
        dialog = FoodItemEditDialog(
            record, reference_job_factory=slow_factory(), parent=None
        )
        card = dialog.reference_card
        card.button().click()
        dialog.close()
        pump(lambda: False, timeout=SLOW_JOB_SECONDS + 0.3)

        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())
        # 本地参考那一行完全没被影响
        self.assertIn("本地参考", dialog.reference_label.text())
        self.assertAlmostEqual(dialog.spins["calories_kcal"].value(), 143.0)

    def test_the_wait_stays_bounded(self) -> None:
        """等待是有上限的：请求再慢也不能把窗口永久卡住。"""
        self.assertTrue(0 < WORKER_WAIT_MS <= 10_000)

    def test_a_request_slower_than_the_wait_still_never_crashes(self) -> None:
        """比等待上限还慢的请求：窗口照常关闭，线程自己收尾，进程不崩。

        这正是「线程不挂在对话框名下」的原因：对话框可以随时消失，而
        QThread 只要还在跑就不能被销毁（Qt 会直接 abort 进程）。
        """
        started = threading.Event()
        release = threading.Event()

        def factory(on_success, on_error, food_name: str) -> ApiWorker:
            def job():
                started.set()
                release.wait(10.0)  # much longer than WORKER_WAIT_MS
                return "very late answer"

            worker = ApiWorker(job, label="very slow online reference")
            worker.succeeded.connect(on_success)
            worker.failed.connect(on_error)
            worker.start()
            return worker

        dialog = ConfirmFoodDialog(
            [food_item()], "manual", "", reference_job_factory=factory, parent=None
        )
        card = dialog.reference_card
        card.button().click()
        self.assertTrue(pump(started.is_set, timeout=3.0), "the request never started")

        started_at = time.monotonic()
        dialog.close()  # waits at most WORKER_WAIT_MS
        waited = time.monotonic() - started_at
        self.assertLess(waited, WORKER_WAIT_MS / 1000 + 1.0)
        self.assertFalse(card.is_showing())

        # Let the slow answer land, then make sure the process is still sane and
        # the closed dialog stayed closed.
        release.set()
        pump(lambda: False, timeout=0.5)
        for _ in range(3):
            QApplication.processEvents()
            time.sleep(0.05)
        self.assertFalse(card.is_showing())
        self.assertFalse(card.has_result())
        # The answer was dropped without ever being rendered.
        self.assertEqual(card.online_text("calories_kcal"), "")


if __name__ == "__main__":
    unittest.main()
