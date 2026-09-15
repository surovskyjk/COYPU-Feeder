"""
Part 3B — interactive metric-to-element linking (Req 9a).

`metrics_from_stats` now reports `worst_element_id` (the element carrying
the largest per-element deviation from the OSM baseline); `MetricsChip`
becomes clickable whenever one is known and emits `metric_clicked`, which
`App._on_metric_clicked` routes to the same highlight+select round trip a
table-row click already does.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, build_model  # noqa: E402


class TestWorstElementId:

    def test_metrics_from_stats_reports_worst_element_id(self):
        model = build_model(RUN3)
        metrics = C.metrics_from_stats(model.last_stats, model.elements)
        assert metrics["worst_element_id"] is not None
        ids = {e.get("element_id") for e in model.elements}
        assert metrics["worst_element_id"] in ids

    def test_worst_element_actually_has_the_max_per_element_deviation(self):
        model = build_model(RUN3)
        stats = model.last_stats
        per_element = stats["per_element"]
        metrics = C.metrics_from_stats(stats, model.elements)
        i_worst = max(range(len(per_element)), key=lambda i: per_element[i][0])
        assert metrics["worst_element_id"] == model.elements[i_worst].get("element_id")

    def test_none_on_empty_elements(self):
        metrics = C.metrics_from_stats({"per_element": []}, [])
        assert metrics["worst_element_id"] is None

    def test_none_on_stats_element_count_mismatch(self):
        """Defensive: stale/mismatched stats must not point at the wrong
        element (or crash)."""
        model = build_model(RUN3)
        stale_stats = dict(model.last_stats)
        stale_stats["per_element"] = list(stale_stats["per_element"]) + [(99.0, 1.0, 1)]
        metrics = C.metrics_from_stats(stale_stats, model.elements)
        assert metrics["worst_element_id"] is None

    def test_candidate_alignment_accepts_the_new_kwargs(self):
        """**metrics unpacked into CandidateAlignment must not crash — the
        exact regression CLAUDE.md's mean_deviation note warns about."""
        model = build_model(RUN3)
        metrics = C.metrics_from_stats(model.last_stats, model.elements)
        c = C.CandidateAlignment("level3", "Level 3", model.elements, **metrics)
        assert c.worst_element_id == metrics["worst_element_id"]
        assert c.c1_junctions == metrics["c1_junctions"]

    def test_evaluate_candidate_empty_case_carries_the_key(self):
        m = C.evaluate_candidate([], None, None)
        assert m["worst_element_id"] is None
        assert "c1_junctions" in m


class TestMetricsChipClickable:

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def _chip(self):
        from gui.metrics_chip import MetricsChip
        return MetricsChip()

    def test_not_clickable_without_a_worst_element(self):
        chip = self._chip()
        chip.set_metrics({"mean_deviation": 0.1, "rmse": 0.2,
                          "max_deviation": 0.3, "max_heading_jump_deg": 0.0,
                          "n_elements": 3, "worst_element_id": None})
        fired = []
        chip.metric_clicked.connect(lambda eid: fired.append(eid))
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, Qt
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(1, 1),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        chip.mousePressEvent(ev)
        assert fired == []

    def test_clickable_emits_worst_element_id(self):
        chip = self._chip()
        chip.set_metrics({"mean_deviation": 0.1, "rmse": 0.2,
                          "max_deviation": 0.3, "max_heading_jump_deg": 0.0,
                          "n_elements": 3, "worst_element_id": "A2"})
        fired = []
        chip.metric_clicked.connect(lambda eid: fired.append(eid))
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, Qt
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(1, 1),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        chip.mousePressEvent(ev)
        assert fired == ["A2"]

    def test_clear_resets_clickability(self):
        chip = self._chip()
        chip.set_metrics({"worst_element_id": "A2", "n_elements": 1})
        assert chip._worst_element_id == "A2"
        chip.clear()
        assert chip._worst_element_id is None


class TestAppRoutesMetricClick:

    def test_on_metric_clicked_highlights_and_selects(self):
        from unittest.mock import MagicMock
        from gui.app import App

        fake = MagicMock()
        App._on_metric_clicked(fake, "A2")
        fake.map_widget.highlight_elements.assert_called_once_with(["A2"])
        fake.element_table.select_element.assert_called_once_with("A2")
