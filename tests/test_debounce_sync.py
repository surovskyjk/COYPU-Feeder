"""
Part 2A/2B — radius/spiral-length input debouncing and table↔map↔metrics
sync (element_table.py).

2A: `setKeyboardTracking(False)` on the Radius/Spiral L cell spinboxes is
the actual fix for "typing 2 on the way to 200 destroys the spiral" — Qt
emits `valueChanged` per typed digit by default; with tracking off, typed
digits commit once (Enter/focus-out), while arrow-click/wheel stepping
(`stepBy()`, not the keyboard-typing path) still ticks immediately, so the
live-preview stays responsive. `editingFinished` flushes the debounced
commit immediately instead of waiting out the timer.

2B: the fast "same structure" table repaint path (`_update_in_place`) used
to return without re-emitting the selection, silently dropping the map
halo on every radius/spiral commit; and a new "⟳ Sync" button forces a full
table↔map↔metrics resync on demand.
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
        it = table._table.item(r, 0)
        meta = it.data(256) or {}
        if meta.get("etype") == "Arc":
            return r
    raise AssertionError("no Arc row in the table")


class TestKeyboardTrackingDisabled:

    def test_radius_and_spiral_spinboxes_have_keyboard_tracking_off(self):
        model = _make_model()
        table, _ = _make_table(model)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        assert isinstance(spin, QDoubleSpinBox)
        assert spin.keyboardTracking() is False

    def test_programmatic_setvalue_still_ticks_immediately(self):
        """setKeyboardTracking(False) must NOT affect programmatic setValue
        (arrow-click/wheel stepping goes through stepBy, not the keyboard-
        typing path) — only raw digit typing is deferred. The tick is armed
        synchronously (pending state + preview gesture), even before the
        zero-interval preview timer itself has fired."""
        model = _make_model()
        table, _ = _make_table(model)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        pi_index = int(spin.property("pi_index"))

        spin.setValue(spin.value() * 0.8)
        assert table._pending.get(pi_index, {}).get("radius") is not None
        assert table._preview_pending is not None
        assert table._preview_pending[0] == pi_index
        assert table._preview_pending[1] == "radius"


class TestDebounceInterval:

    def test_debounce_interval_is_800ms(self):
        model = _make_model()
        table, _ = _make_table(model)
        assert table._debounce.interval() == 800


class TestEditingFinishedFlushesImmediately:

    def test_editing_finished_flushes_without_waiting_for_the_timer(self):
        model = _make_model()
        table, calls = _make_table(model)
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        pi_index = int(spin.property("pi_index"))

        rebuilt_calls = []
        table.rebuilt.connect(lambda *a: rebuilt_calls.append(a))

        spin.setValue(spin.value() * 0.9)
        assert table._debounce.isActive()
        assert not rebuilt_calls

        spin.editingFinished.emit()
        assert not table._debounce.isActive()
        assert rebuilt_calls, "editingFinished must flush the pending commit immediately"
        pid = next(p for p in model.pis if p.index == pi_index)
        assert pid.radius > 0

    def test_editing_finished_with_no_pending_edit_is_a_no_op(self):
        model = _make_model()
        table, _ = _make_table(model)
        rebuilt_calls = []
        table.rebuilt.connect(lambda *a: rebuilt_calls.append(a))
        row = _arc_row(table)
        spin = table._table.cellWidget(row, COL_R)
        spin.editingFinished.emit()
        assert not rebuilt_calls


class TestSelectionSurvivesInPlaceCommit:

    def test_halo_reemitted_after_in_place_commit(self):
        """The exact Part 2B bug: `_update_in_place` (the common radius/
        spiral commit path — same element ids, just new values) used to
        return without re-emitting the selection, so the map halo silently
        vanished on every commit."""
        model = _make_model()
        table, _ = _make_table(model)
        row = _arc_row(table)
        table._table.selectRow(row)

        selections = []
        table.elements_selected.connect(lambda ids: selections.append(ids))

        spin = table._table.cellWidget(row, COL_R)
        spin.setValue(spin.value() * 0.9)
        table._apply_pending()   # simulate the debounce firing

        assert selections, "expected elements_selected to be re-emitted after commit"
        assert selections[-1], "the selection must not have been dropped"


class TestSyncButton:

    def test_sync_button_emits_sync_parameters_requested(self):
        model = _make_model()
        table, _ = _make_table(model)
        fired = []
        table.sync_parameters_requested.connect(lambda: fired.append(1))
        table._sync_btn.click()
        assert fired == [1]
