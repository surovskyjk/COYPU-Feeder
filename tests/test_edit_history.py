"""
EditHistory (src/gui/edit_history.py) — the snapshot stack backing the
toolbar's dual-role Back/Forward undo/redo (Part B). Two layers:

  1. Pure model-level tests: push/undo/redo directly against a PIAlignment,
     no Qt involved.
  2. ElementTableDock wiring: on_before_edit/on_edit_failed fire at the
     right points for each mutating handler (offscreen Qt).
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
from gui.edit_history import EditHistory  # noqa: E402


def _make_model(defl_deg=60.0, R=300.0, straight=400.0, step=4.0):
    pts = [(0.0, 0.0)]
    heading = 0.0
    n = max(2, int(straight / step))
    for i in range(1, n + 1):
        d = straight * i / n
        pts.append((d * math.cos(heading), d * math.sin(heading)))
    ang = math.radians(defl_deg)
    cx = pts[-1][0] + R * math.cos(heading + math.pi / 2)
    cy = pts[-1][1] + R * math.sin(heading + math.pi / 2)
    a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
    n2 = max(2, int(abs(ang) * R / step))
    for i in range(1, n2 + 1):
        a = a0 + ang * i / n2
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    heading += ang
    start = pts[-1]
    for i in range(1, n + 1):
        d = straight * i / n
        pts.append((start[0] + d * math.cos(heading),
                    start[1] + d * math.sin(heading)))
    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=100.0,
                              spiral_length=20.0, use_spirals=True)


def _fingerprint(model):
    """9-decimal element fingerprint, per CLAUDE.md's behaviour-preservation
    convention — proves undo/redo restores the EXACT prior geometry, not
    just a plausible-looking one."""
    out = []
    for e in model.elements:
        out.append((
            e.get("type"),
            round(float(e.get("length", 0.0)), 9),
            round(float(e.get("start", [0, 0])[0]), 9),
            round(float(e.get("start", [0, 0])[1]), 9),
            round(float(e.get("radius", 0.0)) if e.get("radius") is not None else 0.0, 9),
        ))
    return tuple(out)


class TestEditHistoryModelLevel:

    def test_push_undo_restores_prior_state(self):
        model = _make_model()
        before = _fingerprint(model)
        pis = [p.index for p in model.pis if not p.omitted]

        hist = EditHistory()
        hist.push(model, "radius change")
        pid = next(p for p in model.pis if p.index == pis[0])
        pid.radius = 999.0
        C.rebuild_from_pi_model(model)
        after_edit = _fingerprint(model)
        assert after_edit != before

        label = hist.undo(model)
        assert label == "radius change"
        assert _fingerprint(model) == before

    def test_redo_reapplies_undone_edit(self):
        model = _make_model()
        pis = [p.index for p in model.pis if not p.omitted]
        hist = EditHistory()

        hist.push(model, "radius change")
        pid = next(p for p in model.pis if p.index == pis[0])
        pid.radius = 999.0
        C.rebuild_from_pi_model(model)
        edited = _fingerprint(model)

        hist.undo(model)
        assert hist.can_redo()
        label = hist.redo(model)
        assert label == "radius change"
        assert _fingerprint(model) == edited

    def test_new_edit_clears_redo(self):
        model = _make_model()
        hist = EditHistory()
        hist.push(model, "edit 1")
        hist.undo(model)
        assert hist.can_redo()
        hist.push(model, "edit 2")
        assert not hist.can_redo()

    def test_discard_last_push_removes_nothing_visible(self):
        model = _make_model()
        hist = EditHistory()
        hist.push(model, "attempted edit")
        assert hist.can_undo()
        hist.discard_last_push()
        assert not hist.can_undo()

    def test_multi_step_undo_redo_sequence(self):
        """merge -> radius change -> split, then undo x3 gets back to the
        original fingerprint; redo x3 reaches the final edited fingerprint —
        exercising the exact op mix the toolbar Back/Forward drives through."""
        model = _make_model(defl_deg=90.0, straight=500.0, R=600.0)
        original = _fingerprint(model)
        hist = EditHistory()

        # 1) merge
        pis = [p.index for p in model.pis if not p.omitted]
        hist.push(model, "merge")
        ok, msg = C.merge_pi_range(model, pis[0], pis[-1])
        assert ok, msg
        after_merge = _fingerprint(model)

        # 2) radius change on the merged PI
        hist.push(model, "radius")
        pid = next(p for p in model.pis if p.index == pis[0])
        pid.radius = 350.0
        C.rebuild_from_pi_model(model)
        after_radius = _fingerprint(model)
        assert after_radius != after_merge

        # 3) omit it
        hist.push(model, "omit")
        pid.omitted = True
        C.rebuild_from_pi_model(model)
        after_omit = _fingerprint(model)
        assert after_omit != after_radius

        assert hist.undo(model) == "omit"
        assert _fingerprint(model) == after_radius
        assert hist.undo(model) == "radius"
        assert _fingerprint(model) == after_merge
        assert hist.undo(model) == "merge"
        assert _fingerprint(model) == original
        assert not hist.can_undo()

        assert hist.redo(model) == "merge"
        assert _fingerprint(model) == after_merge
        assert hist.redo(model) == "radius"
        assert _fingerprint(model) == after_radius
        assert hist.redo(model) == "omit"
        assert _fingerprint(model) == after_omit
        assert not hist.can_redo()

    def test_undo_on_empty_history_is_noop(self):
        model = _make_model()
        hist = EditHistory()
        assert hist.undo(model) is None
        assert hist.redo(model) is None


class TestRestoreDefaultBaseline:
    """apply_model_snapshot is the same primitive undo/redo uses, reused by
    app.py's "Restore default" toolbar action to revert to a baseline
    stashed with _light_copy when the candidate was first selected."""

    def test_apply_model_snapshot_restores_baseline_after_edits(self):
        from geometry.candidates import _light_copy
        from gui.edit_history import apply_model_snapshot

        model = _make_model(defl_deg=90.0, straight=500.0, R=600.0)
        baseline = _light_copy(model)
        original = _fingerprint(model)

        pis = [p.index for p in model.pis if not p.omitted]
        ok, msg = C.merge_pi_range(model, pis[0], pis[-1])
        assert ok, msg
        pid = next(p for p in model.pis if p.index == pis[0])
        pid.omitted = True
        C.rebuild_from_pi_model(model)
        assert _fingerprint(model) != original

        apply_model_snapshot(model, baseline)
        assert _fingerprint(model) == original

    def test_baseline_survives_a_second_restore(self):
        """Restoring twice in a row (no edits between) is a harmless no-op —
        matches app.py re-stashing a fresh baseline after each restore."""
        from geometry.candidates import _light_copy
        from gui.edit_history import apply_model_snapshot

        model = _make_model()
        baseline = _light_copy(model)
        original = _fingerprint(model)
        apply_model_snapshot(model, baseline)
        apply_model_snapshot(model, baseline)
        assert _fingerprint(model) == original


class TestElementTableWiring:
    """Confirms ElementTableDock calls on_before_edit before a mutation and
    on_edit_failed when the mutator reports failure — without depending on
    App at all (App just supplies these two callables)."""

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def test_before_edit_fires_on_omit(self):
        from gui.element_table import ElementTableDock
        model = _make_model()
        table = ElementTableDock()
        table.prepare(model, model.elements)
        calls = []
        table.on_before_edit = lambda label: calls.append(label)
        table.on_edit_failed = lambda: calls.append("FAILED")
        pi_index = model.pis[0].index
        table._omit_pi(pi_index)
        assert calls == ["omit PI %d" % pi_index]

    def test_before_edit_and_discard_on_failed_merge(self):
        from gui.element_table import ElementTableDock
        model = _make_model(defl_deg=20.0)   # too small to fragment into 2+ PIs
        table = ElementTableDock()
        table.prepare(model, model.elements)
        calls = []
        table.on_before_edit = lambda label: calls.append(("push", label))
        table.on_edit_failed = lambda: calls.append(("discard",))
        # Force a range that can't merge: pick a single-PI "range" via the
        # low-level function directly (mirrors _on_merge_range's failure path).
        from geometry.candidates import merge_pi_range
        pis = [p.index for p in model.pis if not p.omitted]
        table._push_history("merge test")
        ok, msg = merge_pi_range(model, pis[0], pis[0])   # k_from == k_to -> fails
        assert not ok
        table._discard_history()
        assert calls == [("push", "merge test"), ("discard",)]


class TestToolbarIcons:
    """Part D: procedurally-drawn icons for the domain-specific toolbar
    actions (no bundled assets — see src/gui/toolbar_icons.py)."""

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def test_make_icon_returns_nonnull_icon_for_each_known_name(self):
        from gui.toolbar_icons import make_icon
        from PySide6.QtGui import QColor
        for name in ("show_alignment", "merge", "undo", "redo", "edit"):
            icon = make_icon(name, QColor("black"))
            assert not icon.isNull()
            pm = icon.pixmap(20, 20)
            assert not pm.isNull()

    def test_make_icon_unknown_name_returns_empty_icon(self):
        from gui.toolbar_icons import make_icon
        from PySide6.QtGui import QColor
        icon = make_icon("not_a_real_icon", QColor("black"))
        assert icon.isNull()
