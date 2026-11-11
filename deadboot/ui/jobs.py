"""Background jobs: run a Python function in a QThread so the window never freezes.

Job functions call runner.run_blocking (and DeviceOps) freely. Results come
back to the GUI thread through signals.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class _Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)

    def __init__(self, fn: Callable, cancel: threading.Event):
        super().__init__()
        self.fn = fn
        self.cancel = cancel

    def run(self) -> None:
        try:
            result = self.fn(self)
        except Exception as exc:  # noqa: BLE001 - every failure is shown to the user
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=4)}")
            return
        self.done.emit(result)


class JobHandle:
    def __init__(self, worker: _Worker):
        self.worker = worker
        self.cancel = worker.cancel

    def stop(self) -> None:
        self.cancel.set()

    def running(self) -> bool:
        return self.worker.isRunning()


class Jobs(QObject):
    busy_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: set[_Worker] = set()

    def run(self, fn: Callable[[Any], Any], on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[str], None] | None = None,
            on_progress: Callable[[Any], None] | None = None) -> JobHandle:
        """fn receives the worker; use worker.cancel (Event) and worker.progress.emit(x)."""
        cancel = threading.Event()
        w = _Worker(fn, cancel)
        if on_done:
            w.done.connect(on_done)
        if on_error:
            w.failed.connect(on_error)
        if on_progress:
            w.progress.connect(on_progress)
        w.finished.connect(lambda: self._finished(w))
        self._workers.add(w)
        self.busy_changed.emit(len(self._workers))
        w.start()
        return JobHandle(w)

    def _finished(self, w: _Worker) -> None:
        self._workers.discard(w)
        w.deleteLater()
        self.busy_changed.emit(len(self._workers))

    def stop_all(self, wait_ms: int = 3000) -> None:
        for w in list(self._workers):
            w.cancel.set()
        for w in list(self._workers):
            w.wait(wait_ms)
