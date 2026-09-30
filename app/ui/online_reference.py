"""「查询在线参考」区域：把 DeepSeek 的营养估算和本地参考表并排显示。

设计要点（这几条都是硬性约束）：

* **只有点按钮才会发请求**。这个控件自己不监听任何信号，创建它、显示它、
  刷新确认页都不会产生网络调用。
* **失败就是隐藏**。离线、超时、401/429/5xx、非法 JSON、空响应、校验失败……
  一律走同一条路：整块区域隐藏，只在 ``logs/app.log`` 记一条 warning。
  界面不弹窗、不显示错误文案，本地参考值的逻辑完全不受影响。
* **在线值只是第二参考**。它不会离开这个控件：不返回给调用方做计算，
  不写数据库，也不参与当日汇总。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ai.schemas import ONLINE_NUTRIENT_FIELDS
from app.i18n import tr, tr_text
from app.nutrition.reference_compare import ReferenceComparison, build_comparison
from app.ui.widgets import Card, Chip, muted
from app.utils.logging_setup import get_logger

__all__ = ["OnlineReferenceCard", "nutrient_label"]

_logger = get_logger("ui")

#: Field -> Simplified Chinese label, in the order the app displays nutrients.
_FIELD_LABELS: dict[str, str] = {
    name: chinese for name, _, _, chinese in ONLINE_NUTRIENT_FIELDS
}

#: ``job_factory(on_success, on_error)`` starts the request in a background
#: thread. The caller supplies it, so this widget never performs networking
#: itself and tests can hand in a fake that never touches the network.
JobFactory = Callable[[Callable[[Any], None], Callable[[str], None]], Any]


def nutrient_label(field: str) -> str:
    """Chinese label of one nutrient field (falls back to the field name)."""
    return _FIELD_LABELS.get(field, field)


#: How long the card waits for an in-flight request before letting the dialog
#: close anyway. A QThread must not outlive its owner, so the wait is what keeps
#: a closed dialog from taking the process down with it; the request itself is a
#: network call and cannot be cancelled.
WORKER_WAIT_MS = 3000


class OnlineReferenceCard(Card):
    """Side-by-side local/online reference for one food (hidden by default)."""

    def __init__(
        self,
        *,
        item_provider: Callable[[], Any],
        name_provider: Callable[[], str],
        job_factory: JobFactory | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tr("reference.card_title"), parent=parent)
        self._item_provider = item_provider
        self._name_provider = name_provider
        self._job_factory = job_factory
        self._worker: Any = None
        #: How many times a query was actually started (used by tests).
        self.query_count = 0
        #: Field -> the label showing the online value.
        self._value_labels: dict[str, QLabel] = {}
        #: Food name the in-flight request was started for.
        self._pending_name = ""
        #: True once the dialog is closing: late answers are dropped.
        self._closing = False

        self.query_button = QPushButton(tr("reference.query_button"))
        self.query_button.setObjectName("Link")
        self.query_button.clicked.connect(self.start_query)
        if job_factory is None:
            # No API key: the button stays visible but explains itself, instead
            # of silently doing nothing when it is pressed.
            self.query_button.setEnabled(False)
            self.query_button.setToolTip(tr("reference.needs_key"))
        self.add_header_widget(Chip(tr("reference.chip")))
        self.add_header_widget(self.query_button)

        self._result = QWidget()
        result_layout = QVBoxLayout(self._result)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(8)

        self.heading = QLabel("")
        self.heading.setObjectName("SectionTitle")
        result_layout.addWidget(self.heading)

        # Says which food the left column was matched against, so "same food,
        # same fields, same unit" is checkable instead of implied.
        self.source_label = muted("")
        result_layout.addWidget(self.source_label)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(6)
        for column, key in enumerate(
            ("reference.column_field", "reference.column_local", "reference.column_online")
        ):
            header = QLabel(tr(key))
            header.setObjectName("Muted")
            self._grid.addWidget(header, 0, column)
        result_layout.addLayout(self._grid)
        self._header_cells = 3

        self.difference_label = QLabel("")
        self.difference_label.setObjectName("Warning")
        self.difference_label.setWordWrap(True)
        self.difference_label.setVisible(False)
        result_layout.addWidget(self.difference_label)

        result_layout.addWidget(muted(tr("reference.disclaimer")))
        result_layout.addWidget(muted(tr("reference.same_source_note")))
        self.add(self._result)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        # The card itself - title, chip and the button - is ALWAYS on screen, so
        # the feature can be found without knowing it exists. Only the result
        # block below is hidden until there is something to show.
        self._result.setVisible(False)

    # ------------------------------------------------------------------ 状态
    def button(self) -> QPushButton:
        """The button the user has to press (exposed for tests)."""
        return self.query_button

    def has_result(self) -> bool:
        """True once a comparison table is on screen."""
        return bool(self._value_labels)

    def is_showing(self) -> bool:
        """False while the result block is hidden, which is the failure state."""
        return not self._result.isHidden()

    def clear_reference(self) -> None:
        """Hide the online section (used when the food being edited changes).

        Nothing else is touched: the local reference values in the dialog are
        unaffected by anything this card does.
        """
        self._clear_rows()
        self._pending_name = ""
        self.source_label.setText("")
        self.difference_label.setText("")
        self.difference_label.setVisible(False)
        self._hide_silently()

    def shutdown(self) -> None:
        """Stop caring about an in-flight answer; called when the dialog closes.

        A confirmation dialog lives for a few seconds, so this is a real path:
        press the button, then press Esc. The request itself cannot be cancelled
        (it is a network call), so the card waits for the thread to finish and
        then drops the answer: a late answer can neither re-show a closed dialog
        nor leave the card visible. Waiting matters because a QThread that is
        still running while its owner is destroyed aborts the process - Qt says
        so itself: "QThread: Destroyed while thread is still running".
        """
        self._closing = True
        self._pending_name = ""
        self.clear_reference()
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            worker.wait(WORKER_WAIT_MS)

    # ------------------------------------------------------------ 手动触发
    def start_query(self) -> None:
        """Fetch the online reference for the current food name.

        This is wired to the button only. Application start, changing the date,
        opening the history, analysing a food and saving numbers never call it,
        so they can never trigger a request.
        """
        if self._job_factory is None or not self.query_button.isEnabled():
            # The button is disabled in both cases; this is the defensive path.
            return

        self._clear_rows()
        self._result.setVisible(False)
        self._pending_name = self._current_name()
        self._set_busy(True)
        self.query_count += 1

        def on_success(reference: Any) -> None:
            if self._closing:
                # The dialog is gone: drop the answer instead of showing it.
                return
            self._set_busy(False)
            self._show_reference(reference)

        def on_error(message: str) -> None:
            if self._closing:
                return
            self._on_failure(message)

        try:
            self._worker = self._job_factory(on_success, on_error)
        except Exception:  # noqa: BLE001 - a broken factory must not crash the dialog
            _logger.exception("Online reference could not be started")
            self._on_failure("could not start the request")

    def _show_reference(self, reference: Any) -> None:
        if self._current_name() != self._pending_name:
            # The food was renamed (or another row was picked) while the request
            # was in flight, so these numbers describe something else. Showing
            # them would silently compare two different foods.
            self._hide_silently()
            return
        try:
            comparison = build_comparison(self._item_provider(), reference)
        except Exception:  # noqa: BLE001 - a display failure is not the user's problem
            _logger.exception("Online reference could not be displayed")
            self._hide_silently()
            return
        self._fill(comparison)
        self._result.setVisible(True)
        self.adjustSize()

    def _fill(self, comparison: ReferenceComparison) -> None:
        self._clear_rows()
        name = comparison.name or self._name_provider()
        self.heading.setText(tr("reference.heading", name=name))
        if comparison.has_local():
            self.source_label.setText(tr("reference.source_matched", name=comparison.local_source))
            self.source_label.setVisible(True)
        else:
            # The left column is then the record's own numbers, not the table's.
            self.source_label.setText(tr("reference.local_missing"))
            self.source_label.setVisible(True)
        for index, row in enumerate(comparison.rows):
            self._grid.addWidget(QLabel(nutrient_label(row.field)), index + 1, 0)
            self._grid.addWidget(QLabel(row.local_text()), index + 1, 1)
            online_label = QLabel(row.online_text())
            if row.online is None:
                # "未知" is a different statement from "0" and must look like it.
                online_label.setObjectName("Muted")
            self._grid.addWidget(online_label, index + 1, 2)
            self._value_labels[row.field] = online_label

        note = comparison.difference_note()
        self.difference_label.setText(note)
        self.difference_label.setVisible(bool(note))

    def _on_failure(self, message: str) -> None:
        """One warning in the log, and nothing at all in the interface."""
        _logger.warning(
            tr_text("reference.log_failed", name=self._pending_name or "-", error=message)
        )
        self._hide_silently()

    # ------------------------------------------------------------------ 取值
    def online_text(self, field: str) -> str:
        """Text shown for the online value of ``field`` (empty when hidden)."""
        label = self._value_labels.get(field)
        return label.text() if label is not None else ""

    # ---------------------------------------------------------------- 内部
    def _set_busy(self, busy: bool) -> None:
        self.query_button.setEnabled(not busy and self._job_factory is not None)
        self.query_button.setText(
            tr("reference.querying") if busy else tr("reference.query_button")
        )

    def _current_name(self) -> str:
        return str(self._name_provider() or "").strip()

    def _clear_rows(self) -> None:
        while self._grid.count() > self._header_cells:
            item = self._grid.takeAt(self._grid.count() - 1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._value_labels.clear()

    def _hide_silently(self) -> None:
        """Failure state: hide the results. Title, chip and button stay."""
        self._set_busy(False)
        self._result.setVisible(False)
        self.adjustSize()
