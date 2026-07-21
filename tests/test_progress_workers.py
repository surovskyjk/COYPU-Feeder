"""
Part L — progress bars + ETA for Consolidate's Scan/Apply.

Three layers:
  1. progress_util.format_eta / ElapsedTimer — pure Python, no Qt.
  2. find_consolidation_groups / apply_consolidation's progress_cb(done,
     total) numeric contract (widened from a pre-formatted string to match
     every other progress callback in this codebase, e.g.
     geometry.cross_section.compute_cross_section).
  3. ConsolidationScanWorker / ConsolidationApplyWorker (QThread) deliver
     progress/finished/failed signals correctly off the GUI thread.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import geometry.candidates as C  # noqa: E402
from gui.progress_util import format_eta, ElapsedTimer  # noqa: E402
from test_reflex_merge import make_single_arc_track  # noqa: E402


class TestFormatEta:

    def test_zero_total_returns_empty(self):
        assert format_eta(1.0, 0, 0) == ""

    def test_no_progress_yet_shows_bare_counter(self):
        assert format_eta(0.0, 0, 10) == "0/10"
        assert format_eta(5.0, 0, 10) == "0/10"

    def test_partial_progress_shows_eta(self):
        # 5 done in 5s -> rate 1/s -> 5 remaining -> ~5s left
        s = format_eta(5.0, 5, 10)
        assert s.startswith("5/10")
        assert "left" in s

    def test_near_complete_shows_under_a_second(self):
        s = format_eta(10.0, 999, 1000)
        assert "<1s left" in s

    def test_slow_rate_shows_minutes(self):
        # 1 done in 120s -> rate very slow -> remaining should read in minutes
        s = format_eta(120.0, 1, 100)
        assert "m left" in s


class TestElapsedTimer:

    def test_elapsed_is_nonnegative_and_monotonic(self):
        t = ElapsedTimer()
        e1 = t.elapsed()
        e2 = t.elapsed()
        assert e1 >= 0.0
        assert e2 >= e1


class TestProgressCallbackContract:
    """find_consolidation_groups / apply_consolidation call progress_cb with
    NUMERIC (done, total) args, not a pre-formatted string — the caller
    (a QThread worker) just re-emits these as a Qt signal."""

    def _model_with_one_group(self):
        xy, ch = make_single_arc_track(180.2, R=600.0, straight=2000.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        return model

    def test_find_consolidation_groups_progress_numeric(self):
        model = self._model_with_one_group()
        calls = []
        groups = C.find_consolidation_groups(
            model, max_straight_m=30.0, max_dev_m=2.0,
            progress_cb=lambda done, total: calls.append((done, total)))
        assert len(groups) >= 1
        assert len(calls) == len(groups) == 1   # one run found -> one callback
        done, total = calls[0]
        assert isinstance(done, int) and isinstance(total, int)
        assert done == 1 and total == 1

    def test_apply_consolidation_progress_numeric(self):
        model = self._model_with_one_group()
        groups = C.find_consolidation_groups(model, max_straight_m=30.0, max_dev_m=2.0)
        assert groups
        calls = []
        applied, msgs = C.apply_consolidation(
            model, groups,
            progress_cb=lambda done, total: calls.append((done, total)))
        assert applied == 1
        assert calls == [(1, 1)]

    def test_apply_consolidation_without_progress_cb_still_works(self):
        """progress_cb is optional — existing callers (and the earlier
        test_reflex_merge.py Consolidate test) don't pass it."""
        model = self._model_with_one_group()
        groups = C.find_consolidation_groups(model, max_straight_m=30.0, max_dev_m=2.0)
        applied, msgs = C.apply_consolidation(model, groups)
        assert applied == 1


class TestConsolidationWorkers:
    """QThread-based workers — run the Qt event loop with a hard timeout so
    a hang becomes a clear test failure, not an indefinite stall."""

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def _run_worker(self, worker):
        """Start `worker`, pump the event loop until finished/failed fires
        or a 10s watchdog trips, return (progress_calls, finished_args, error)."""
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

    def _model_with_one_group(self):
        xy, ch = make_single_arc_track(180.2, R=600.0, straight=2000.0)
        return C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                  spiral_length=20.0, use_spirals=True)

    def test_scan_worker_end_to_end(self):
        from gui.worker import ConsolidationScanWorker
        model = self._model_with_one_group()
        w = ConsolidationScanWorker(model, 30.0, 2.0)
        progress_calls, finished, error = self._run_worker(w)
        assert error is None, error
        assert finished is not None
        (groups,) = finished
        assert len(groups) == 1
        assert progress_calls == [(1, 1)]

    def test_apply_worker_end_to_end(self):
        from gui.worker import ConsolidationApplyWorker
        model = self._model_with_one_group()
        groups = C.find_consolidation_groups(model, max_straight_m=30.0, max_dev_m=2.0)
        assert groups
        w = ConsolidationApplyWorker(model, groups)
        progress_calls, finished, error = self._run_worker(w)
        assert error is None, error
        applied, msgs = finished
        assert applied == 1
        assert progress_calls == [(1, 1)]

    def test_scan_worker_failure_path(self):
        """A None model makes find_consolidation_groups raise — the worker
        must emit failed(), not crash or hang."""
        from gui.worker import ConsolidationScanWorker
        w = ConsolidationScanWorker(None, 30.0, 2.0)
        progress_calls, finished, error = self._run_worker(w)
        assert finished is None
        assert error is not None
