"""
Part D — Batch Extend Spirals UI/worker wiring.

`BatchSpiralExtendWorker` (src/gui/worker.py) runs `batch_extend_spirals`
off the GUI thread; `Step5Design`'s "Batch extend spirals" section drives it.
Both are plain QWidget/QThread (no QWebEngineView), so they construct fine
offscreen — unlike full `App()`.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import geometry.candidates as C  # noqa: E402
from test_batch_spiral_extend import build_model, ROOMY  # noqa: E402


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _run_worker(worker):
    """Start `worker`, pump the event loop until finished/failed fires or a
    10s watchdog trips — mirrors TestConsolidationWorkers in
    test_progress_workers.py."""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    progress_calls = []
    result = {"finished": None, "error": None}

    worker.progress.connect(lambda *a: progress_calls.append(a))
    worker.finished.connect(lambda *a: (result.__setitem__("finished", a), loop.quit()))
    worker.failed.connect(lambda e: (result.__setitem__("error", e), loop.quit()))

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    watchdog.start(10000)

    worker.start()
    loop.exec()
    worker.wait(2000)
    assert result["finished"] is not None or result["error"] is not None, \
        "worker neither finished nor failed within the watchdog timeout"
    return progress_calls, result["finished"], result["error"]


class TestBatchSpiralExtendWorker:

    def test_end_to_end(self):
        from gui.worker import BatchSpiralExtendWorker
        model = build_model(ROOMY)
        w = BatchSpiralExtendWorker(model, 60.0, "fixed_radius")
        progress_calls, finished, error = _run_worker(w)
        assert error is None, error
        assert finished is not None
        (result,) = finished
        assert result.lengthened + result.clamped + result.skipped > 0
        assert progress_calls, "expected at least one progress tick"
        assert progress_calls[-1][0] == progress_calls[-1][1]   # done == total on the last tick

    def test_minimize_deviation_mode_end_to_end(self):
        from gui.worker import BatchSpiralExtendWorker
        model = build_model(ROOMY)
        w = BatchSpiralExtendWorker(model, 60.0, "minimize_deviation", min_radius=150.0)
        _, finished, error = _run_worker(w)
        assert error is None, error
        (result,) = finished
        assert result.lengthened + result.clamped + result.skipped > 0

    def test_failure_path_emits_failed_not_a_crash(self):
        from gui.worker import BatchSpiralExtendWorker
        w = BatchSpiralExtendWorker(None, 60.0, "fixed_radius")
        _, finished, error = _run_worker(w)
        assert finished is None
        assert error is not None


class TestStep5DesignBatchSpiralSection:

    def _panel(self, model):
        from gui.steps.step5_design import Step5Design
        panel = Step5Design()
        panel.bind(model)
        return panel

    def test_run_button_disabled_without_a_model(self):
        panel = self._panel(None)
        assert not panel._batch_run_btn.isEnabled()

    def test_run_button_enabled_with_a_model(self):
        model = build_model(ROOMY)
        panel = self._panel(model)
        assert panel._batch_run_btn.isEnabled()

    def test_reset_disables_the_run_button_and_hides_progress(self):
        model = build_model(ROOMY)
        panel = self._panel(model)
        panel._batch_progress_bar.setVisible(True)
        panel.reset()
        assert not panel._batch_run_btn.isEnabled()
        assert not panel._batch_progress_bar.isVisible()

    def test_running_pushes_history_once_and_emits_model_changed_on_success(self, monkeypatch):
        """Exercises the full Step5Design -> BatchSpiralExtendWorker ->
        model_changed wiring deterministically: make .start() run
        synchronously in-process instead of spawning a real thread, so the
        test doesn't depend on event-loop timing."""
        from gui.worker import BatchSpiralExtendWorker
        monkeypatch.setattr(BatchSpiralExtendWorker, "start", BatchSpiralExtendWorker.run)

        model = build_model(ROOMY)
        panel = self._panel(model)
        pushed = []
        panel.on_before_edit = lambda label: pushed.append(label)
        panel.on_edit_failed = lambda: pushed.append("FAILED")
        changed = []
        panel.model_changed.connect(lambda: changed.append(True))

        # Avoid a real QMessageBox popping up (and blocking) during the test.
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        panel._target_len_spin.setValue(60.0)
        panel._batch_mode_combo.setCurrentIndex(
            panel._batch_mode_combo.findData("fixed_radius"))
        panel._on_batch_extend_run()
        assert pushed == ["batch extend spirals to 60 m"]

        assert changed == [True]
        assert pushed == ["batch extend spirals to 60 m"]   # no extra push/discard
