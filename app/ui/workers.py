"""Background workers so the window never freezes during an API call."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

from app.ai.errors import AIError
from app.database.database import DatabaseError
from app.utils.logging_setup import get_logger

__all__ = ["ApiWorker", "run_in_background"]

_logger = get_logger("worker")

#: Workers that are still running. A QThread must not be destroyed while it
#: runs - Qt aborts the process with "QThread: Destroyed while thread is still
#: running" - so an in-flight worker is kept alive here rather than depending on
#: a dialog that the user may close at any moment. ``_forget_worker`` drops the
#: entry once the thread has finished.
_LIVE_WORKERS: set["ApiWorker"] = set()


class ApiWorker(QThread):
    """Run one callable in a thread and report the result with signals.

    ``succeeded`` carries the return value, ``failed`` carries a message that
    is already safe to show to the user (no traceback).

    The worker **owns itself**: it is not parented to the window that started
    it, because that window (a short-lived dialog) can be closed while the
    request is still in flight. Signals from a worker that outlives its receiver
    are simply not delivered, which is what the online reference wants.
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[], Any], *, label: str = "task") -> None:
        super().__init__(None)
        self._job = job
        self._label = label
        _LIVE_WORKERS.add(self)
        self.finished.connect(lambda: _forget_worker(self))

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result = self._job()
        except (AIError, DatabaseError) as exc:
            _logger.warning(
                "%s failed: %s", self._label, getattr(exc, "detail", None) or exc
            )
            self.failed.emit(exc.user_message)
        except Exception as exc:  # noqa: BLE001 - never let a thread die silently
            _logger.exception("%s crashed", self._label)
            self.failed.emit(
                "Something went wrong while processing this request. "
                "Please try again - the details were written to the log file."
            )
            _ = exc
        else:
            self.succeeded.emit(result)


def _forget_worker(worker: "ApiWorker") -> None:
    """Called when a worker finishes: it no longer needs to be kept alive."""
    _LIVE_WORKERS.discard(worker)


def run_in_background(
    job: Callable[[], Any],
    *,
    label: str,
    on_success: Callable[[Any], None],
    on_error: Callable[[str], None],
    owner: Any = None,
) -> ApiWorker:
    """Start ``job`` in a thread and connect the two callbacks.

    ``owner`` is accepted for call-site clarity and signal lifetime, but it is
    deliberately *not* used as the Qt parent: see :class:`ApiWorker`.
    """
    worker = ApiWorker(job, label=label)
    worker.succeeded.connect(on_success)
    worker.failed.connect(on_error)
    _ = owner
    worker.start()
    return worker
