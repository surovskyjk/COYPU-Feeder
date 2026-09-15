"""
Part A5 — radius/spiral-length live preview.

Mirrors the PI-drag live-preview pattern (gesture-start pushes ONE history
entry, coalesced ticks refresh only the map, the debounced commit does the
full table/metrics/log/staleness catch-up) — but for the Radius/Spiral L
columns' embedded QDoubleSpinBox cells (element_table.py) instead of a map
marker drag. `ElementTableDock` is a plain QWidget (no QWebEngineView), so
it constructs fine offscreen, unlike full `App()`.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDoubleSpinBox  # noqa: E402

import geometry.candidates as C  # noqa: E402
from gui.element_table import ElementTableDock, COL_R, COL_SL  # noqa: E402
from test_edit_history import _make_model  # noqa: E402


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_table(model):
    table = ElementTableDock()
    calls = []
    table.on_before_edit = lambda label: calls.append(("push", label))
    table.on_edit_failed = lambda: calls.append(("discard",))
    table.prepare(model, model.elements)
    return table, calls


def _arc_row(table) -> int:
    for r in range(table._table.rowCount()):
        it = table._table.item(r, 0)   # COL_ID
        meta = it.data(256) or {}      # Qt.ItemDataRole.UserRole == 256
        if meta.get("etype") == "Arc":
            return r
    raise AssertionError("no Arc row in the table")


class TestRadiusCellIsASpinbox:

    def test_arc_radius_cell_is_a_spinbox_when_editable(self):
        model = _make_model()
        table, _ = _make_table(model)
        row = _arc_row(table)
        w = table._table.cellWidget(row, COL_R)
        assert isinstance(w, QDoubleSpinBox)

    def test_read_only_mode_uses_plain_items_not_spinboxes(self):
        model = _make_model()
        table = ElementTableDock()
        table.prepare(None, model.elements)   # no PI model -> read-only
        row = _arc_row(table)
        assert table._table.cellWidget(row, COL_R) is None
        assert table._table.item(row, COL_R) is not None


class TestLivePreviewGesture:

    def test_single_tick_pushes_history_once_and_emits_preview(self):
        model = _make_model()
        table, calls = _make_table(model)
        table.set_live_preview(True)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        pi_index = int(spin.property("pi_index"))

        previews = []
        table.preview_changed.connect(lambda els, splice: previews.append((els, splice)))
        rebuilt_calls = []
        table.rebuilt.connect(lambda *a: rebuilt_calls.append(a))

        spin.setValue(spin.value() * 0.8)   # one tick
        new_value = spin.value()   # QDoubleSpinBox rounds to its `decimals`

        assert calls == [("push", "radius/spiral change")]
        assert table._preview_pending == (pi_index, "radius", new_value)
        assert not rebuilt_calls   # preview tick must NOT trigger the full path

        table._apply_preview_tick()   # simulate the zero-interval timer firing
        assert len(previews) == 1
        assert not rebuilt_calls
        # The model itself already reflects the preview (span-local rebuild).
        pid = next(p for p in model.pis if p.index == pi_index)
        assert pid.radius == new_value

    def test_multiple_ticks_in_one_gesture_push_history_only_once(self):
        model = _make_model()
        table, calls = _make_table(model)
        table.set_live_preview(True)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        base = spin.value()

        for f in (0.95, 0.9, 0.85, 0.8):
            spin.setValue(base * f)
            table._apply_preview_tick()

        assert calls == [("push", "radius/spiral change")], (
            "a multi-tick gesture on the SAME cell must push exactly one "
            "history entry, not one per tick")

    def test_commit_does_not_push_history_again_after_a_preview_gesture(self):
        model = _make_model()
        table, calls = _make_table(model)
        table.set_live_preview(True)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        new_value = spin.value() * 0.8
        spin.setValue(new_value)
        table._apply_preview_tick()
        assert calls == [("push", "radius/spiral change")]

        rebuilt_calls = []
        table.rebuilt.connect(lambda *a: rebuilt_calls.append(a))
        table._apply_pending()   # simulate the debounce firing (the full commit)

        assert calls == [("push", "radius/spiral change")], (
            "the debounced commit must not push a SECOND history entry for "
            "a gesture that already pushed one at its first tick")
        assert len(rebuilt_calls) == 1
        assert table._preview_gesture is None

    def test_live_preview_disabled_behaves_like_before_this_feature(self):
        """Off: no preview_changed signal, no early history push — the
        debounced commit is the only thing that happens, exactly as before
        radius editing got a live-preview mode."""
        model = _make_model()
        table, calls = _make_table(model)
        table.set_live_preview(False)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)

        previews = []
        table.preview_changed.connect(lambda els, splice: previews.append((els, splice)))

        spin.setValue(spin.value() * 0.8)
        assert previews == []
        assert calls == []   # no early push — only _apply_pending pushes

        table._apply_pending()
        assert calls == [("push", "radius/spiral change")]

    def test_spiral_length_column_also_gets_live_preview(self):
        model = _make_model()
        table, calls = _make_table(model)
        table.set_live_preview(True)
        row = None
        for r in range(table._table.rowCount()):
            it = table._table.item(r, 0)
            if (it.data(256) or {}).get("etype") == "Spiral":
                row = r
                break
        assert row is not None, "model has no Spiral rows"
        spin = table._table.cellWidget(row, COL_SL)
        assert isinstance(spin, QDoubleSpinBox)
        assert spin.property("field") == "spiral_len"

        previews = []
        table.preview_changed.connect(lambda els, splice: previews.append((els, splice)))
        spin.setValue(min(spin.maximum(), spin.value() + 5.0))
        table._apply_preview_tick()
        assert len(previews) == 1


class TestPreviewCommitFingerprintMatchesFullRebuild:

    def test_committed_radius_edit_matches_rebuild_from_pi_model(self):
        """The end state after a live-preview gesture + debounced commit
        must be bit-identical to what a full rebuild would produce —
        Part A is a performance fix, not a behavior change."""
        model = _make_model()
        table, _ = _make_table(model)
        table.set_live_preview(True)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        pi_index = int(spin.property("pi_index"))

        spin.setValue(spin.value() * 0.8)
        new_value = spin.value()   # QDoubleSpinBox rounds to its `decimals`
        table._apply_preview_tick()
        table._apply_pending()

        pid = next(p for p in model.pis if p.index == pi_index)
        assert pid.radius == new_value

        reference = _make_model()
        ref_pid = next(p for p in reference.pis if p.index == pi_index)
        ref_pid.radius = new_value
        C.rebuild_from_pi_model(reference)

        def fp(m):
            return [(e.get("type"), round(float(e.get("length", 0.0)), 6),
                     round(float(e.get("radius", 0.0) or 0.0), 6),
                     e.get("element_id"))
                    for e in m.elements]

        assert fp(model) == fp(reference)
