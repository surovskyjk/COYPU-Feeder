"""
ProfileCanvas and Step8Vertical, driven offscreen.

Both are plain QWidgets (no QWebEngineView), so unlike the full `App` they
construct and paint under QT_QPA_PLATFORM=offscreen — the same precedent as
`test_radius_live_preview.py`.

The canvas is the repo's first `paintEvent`, so the emphasis is on it never
raising: an empty profile, a one-sample profile, mismatched array lengths and
an all-NaN series all have to paint something rather than take the app down.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.profile import (  # noqa: E402
    METHOD_WINDOW, DirectConnection, VerticalProfile, rebuild_profile,
)
from gui.profile_canvas import ProfileCanvas, _nice_step  # noqa: E402
from gui.steps.step8_vertical import Step8Vertical  # noqa: E402

app = QApplication.instance() or QApplication([])


def _profile(length=20000.0, ds=20.0, connections=None, seed=1):
    rng = np.random.default_rng(seed)
    s = np.arange(0.0, length, ds)
    z = 300.0 + 30.0 * np.sin(s / 2500.0) + rng.normal(0.0, 0.2, len(s))
    p = VerticalProfile(s=s, z_raw=z, connections=list(connections or []))
    return rebuild_profile(p)


# Qt widgets created without a parent are owned by Python; letting them be
# collected mid-session while Qt still holds connections and a pending debounce
# timer corrupts the event loop for whatever runs next in the same process.
# Every widget these tests build is kept alive for the module's lifetime.
_KEEP: list = []


def _canvas(profile=None, w=900, h=300):
    c = ProfileCanvas()
    _KEEP.append(c)
    c.resize(w, h)
    if profile is not None:
        c.set_profile(profile)
    return c


def _step(profile=None, canvas=None):
    s = Step8Vertical()
    _KEEP.append(s)
    if profile is not None:
        s.bind(profile, canvas if canvas is not None else _canvas())
    return s


def teardown_module(_module):
    for w in _KEEP:
        timer = getattr(w, "_timer", None)
        if timer is not None:
            timer.stop()
        w.deleteLater()
    app.processEvents()
    _KEEP.clear()


def _click(widget, x, y, kind=QMouseEvent.Type.MouseButtonPress,
           button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), widget.mapToGlobal(QPoint(int(x), int(y))),
                       button, button, Qt.KeyboardModifier.NoModifier)


# ---------------------------------------------------------------------------
# Painting
# ---------------------------------------------------------------------------

class TestPainting:

    def test_empty_canvas_paints_a_placeholder(self):
        c = _canvas()
        assert not c.grab().isNull()

    def test_profile_paints(self):
        assert not _canvas(_profile()).grab().isNull()

    def test_connections_paint(self):
        p = _profile(connections=[DirectConnection(4000.0, 5200.0, "tunnel")])
        assert not _canvas(p).grab().isNull()

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_degenerate_sample_counts(self, n):
        p = VerticalProfile(s=np.arange(float(n)), z_raw=np.arange(float(n)))
        rebuild_profile(p)
        assert not _canvas(p).grab().isNull()

    def test_mismatched_array_lengths(self):
        p = VerticalProfile(s=np.arange(100.0), z_raw=np.arange(10.0))
        assert not _canvas(p).grab().isNull()

    def test_all_nodata(self):
        s = np.arange(0.0, 1000.0, 20.0)
        p = VerticalProfile(s=s, z_raw=np.full(len(s), np.nan))
        rebuild_profile(p)
        assert not _canvas(p).grab().isNull()

    def test_flat_profile_does_not_divide_by_zero(self):
        s = np.arange(0.0, 1000.0, 20.0)
        p = VerticalProfile(s=s, z_raw=np.full(len(s), 250.0))
        rebuild_profile(p)
        assert not _canvas(p).grab().isNull()

    def test_tiny_widget(self):
        assert not _canvas(_profile(), w=20, h=20).grab().isNull()

    def test_hover_readout_paints(self):
        c = _canvas(_profile())
        c._hover_x = 6000.0
        assert not c.grab().isNull()

    def test_drag_band_paints(self):
        c = _canvas(_profile())
        c.set_pick_mode(True)
        c._drag_from, c._drag_to = 1000.0, 2000.0
        assert not c.grab().isNull()

    @pytest.mark.parametrize("dark", [True, False])
    def test_theme_switch(self, dark):
        c = _canvas(_profile())
        c.applyTheme(dark)
        assert not c.grab().isNull()

    def test_very_long_line_is_decimated(self):
        """135 km at 20 m is ~6 750 points per series; the polyline must be
        strided down to roughly the pixel width before it is built."""
        c = _canvas(_profile(length=135_000.0), w=800)
        assert c._stride(6750) > 1
        assert not c.grab().isNull()

    def test_short_line_is_not_decimated(self):
        c = _canvas(_profile(length=2000.0), w=900)
        assert c._stride(100) == 1


class TestNiceStep:

    @pytest.mark.parametrize("span", [1.0, 7.0, 55.0, 1234.0, 98765.0, 1e6])
    def test_produces_a_sane_tick_count(self, span):
        step = _nice_step(span, 5)
        assert step > 0
        assert 1 <= span / step <= 20

    def test_degenerate_spans(self):
        assert _nice_step(0.0, 5) == 1.0
        assert _nice_step(-5.0, 5) == 1.0
        assert _nice_step(100.0, 0) == 1.0


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

class TestInteraction:

    def test_x_maps_into_the_chainage_range(self):
        c = _canvas(_profile(length=10000.0))
        rect = c._plot_rect()
        assert c._x_to_s(rect.left()) == pytest.approx(0.0, abs=1.0)
        assert c._x_to_s(rect.right()) == pytest.approx(9980.0, abs=25.0)

    def test_x_outside_the_plot_is_clamped(self):
        c = _canvas(_profile(length=10000.0))
        assert c._x_to_s(-500.0) == pytest.approx(0.0, abs=1.0)
        assert c._x_to_s(99999.0) <= 9980.0 + 1e-6

    def test_pick_drag_emits_a_range(self):
        c = _canvas(_profile(length=10000.0))
        c.set_pick_mode(True)
        got = []
        c.range_picked.connect(lambda a, b: got.append((a, b)))
        rect = c._plot_rect()
        c.mousePressEvent(_click(c, rect.left() + 100, rect.center().y()))
        c.mouseMoveEvent(_click(c, rect.left() + 300, rect.center().y(),
                                QMouseEvent.Type.MouseMove,
                                Qt.MouseButton.NoButton))
        c.mouseReleaseEvent(_click(c, rect.left() + 300, rect.center().y(),
                                   QMouseEvent.Type.MouseButtonRelease))
        assert len(got) == 1
        a, b = got[0]
        assert 0.0 <= a < b <= 10000.0

    def test_a_bare_click_does_not_emit_a_range(self):
        c = _canvas(_profile(length=10000.0))
        c.set_pick_mode(True)
        got = []
        c.range_picked.connect(lambda a, b: got.append((a, b)))
        rect = c._plot_rect()
        c.mousePressEvent(_click(c, rect.left() + 100, rect.center().y()))
        c.mouseReleaseEvent(_click(c, rect.left() + 100, rect.center().y(),
                                   QMouseEvent.Type.MouseButtonRelease))
        assert got == []

    def test_pick_mode_off_does_not_emit(self):
        c = _canvas(_profile(length=10000.0))
        got = []
        c.range_picked.connect(lambda a, b: got.append((a, b)))
        rect = c._plot_rect()
        c.mousePressEvent(_click(c, rect.left() + 100, rect.center().y()))
        c.mouseReleaseEvent(_click(c, rect.left() + 300, rect.center().y(),
                                   QMouseEvent.Type.MouseButtonRelease))
        assert got == []

    def test_hover_emits_the_chainage_and_clears_on_leave(self):
        c = _canvas(_profile(length=10000.0))
        seen = []
        c.hover_changed.connect(seen.append)
        rect = c._plot_rect()
        c.mouseMoveEvent(_click(c, rect.center().x(), rect.center().y(),
                                QMouseEvent.Type.MouseMove,
                                Qt.MouseButton.NoButton))
        assert seen and seen[-1] > 0.0
        c.leaveEvent(None)
        assert seen[-1] == -1.0

    def test_clear_resets_everything(self):
        c = _canvas(_profile())
        c._hover_x = 100.0
        c._view = (0.0, 500.0)
        c.clear()
        assert c._profile is None and c._view is None and c._hover_x is None
        assert not c.grab().isNull()

    def test_view_survives_a_rebind_of_the_same_length(self):
        """The step rebinds on every parameter change; a zoomed-in user must
        not be yanked back to the whole line each time."""
        p = _profile()
        c = _canvas(p)
        c._view = (1000.0, 2000.0)
        c.set_profile(p)
        assert c._view == (1000.0, 2000.0)

    def test_view_resets_when_the_sample_count_changes(self):
        c = _canvas(_profile(length=10000.0))
        c._view = (1000.0, 2000.0)
        c.set_profile(_profile(length=20000.0))
        assert c._view is None

    def test_grade_readout_matches_the_pvi_tangents(self):
        s = np.arange(0.0, 4000.0, 20.0)
        p = VerticalProfile(s=s, z_raw=100.0 + 0.010 * s, max_grade_pm=15.0)
        rebuild_profile(p)
        c = _canvas(p)
        assert c._grade_at(2000.0) == pytest.approx(10.0, abs=0.5)

    def test_grade_readout_on_an_empty_profile(self):
        assert _canvas()._grade_at(100.0) is None


# ---------------------------------------------------------------------------
# The step widget
# ---------------------------------------------------------------------------

class TestStep8Vertical:

    def test_constructs_and_resets_without_a_profile(self):
        step = _step()
        step.reset()
        assert step.profile() is None

    def test_bind_seeds_the_controls_from_the_model(self):
        step = _step()
        p = _profile()
        p.method = METHOD_WINDOW
        p.max_grade_pm = 9.5
        p.z_tol = 1.25
        p.window_m = 220.0
        p.poly_order = 1
        p.vc_enabled = False
        p.vc_length = 300.0
        step.bind(p, _canvas())
        assert step._grade_spin.value() == pytest.approx(9.5)
        assert step._tol_spin.value() == pytest.approx(1.25)
        assert step._window_spin.value() == pytest.approx(220.0)
        assert step._order_spin.value() == 1
        assert step._vc_check.isChecked() is False
        assert step._vc_spin.value() == pytest.approx(300.0)

    def test_editing_a_control_rebuilds_the_bound_profile_in_place(self):
        step = _step()
        p = _profile()
        step.bind(p, _canvas())
        before = p.stats["max_grade_pm"]
        step._grade_spin.setValue(2.0)
        step._recompute()
        assert step.profile() is p              # mutated, never replaced
        assert p.stats["max_grade_pm"] <= 2.0 + 1e-6
        assert p.stats["max_grade_pm"] != before

    def test_recompute_is_debounced_not_immediate(self):
        step = _step()
        step.bind(_profile(), _canvas())
        step._arm_recompute()
        assert step._timer is not None and step._timer.isActive()

    def test_seeding_controls_does_not_arm_a_recompute(self):
        step = _step()
        step.bind(_profile(), _canvas())
        if step._timer is not None:
            step._timer.stop()
        step._seed_controls()
        assert step._timer is None or not step._timer.isActive()

    def test_window_controls_are_disabled_for_the_gradient_method(self):
        step = _step()
        step.bind(_profile(), _canvas())
        step._method_combo.setCurrentIndex(0)      # gradient
        assert not step._window_spin.isEnabled()
        step._method_combo.setCurrentIndex(1)      # window
        assert step._window_spin.isEnabled()
        assert step._order_spin.isEnabled()

    def test_add_and_delete_a_range(self):
        step = _step()
        p = _profile()
        step.bind(p, _canvas())
        step._on_add_range()
        assert len(p.connections) == 1
        assert step._table.rowCount() == 1
        step._on_delete(0)
        assert p.connections == []
        assert step._table.rowCount() == 0

    def test_editing_a_km_cell_updates_the_model(self):
        step = _step()
        p = _profile()
        step.bind(p, _canvas())
        step._on_add_range()
        step._table.item(0, 0).setText("1.500")
        step._table.item(0, 1).setText("2.500")
        assert p.connections[0].s_start == pytest.approx(1500.0)
        assert p.connections[0].s_end == pytest.approx(2500.0)

    def test_a_non_numeric_cell_is_rejected_not_fatal(self):
        step = _step()
        p = _profile()
        step.bind(p, _canvas())
        step._on_add_range()
        before = p.connections[0].s_start
        step._table.item(0, 0).setText("not a number")
        assert p.connections[0].s_start == pytest.approx(before)

    def test_picked_range_lands_in_the_table(self):
        step = _step()
        p = _profile()
        canvas = _canvas(p)
        step.bind(p, canvas)
        step._pick_btn.setChecked(True)
        canvas.range_picked.emit(3000.0, 4000.0)
        assert len(p.connections) == 1
        assert p.connections[0].s_start == pytest.approx(3000.0)
        assert not step._pick_btn.isChecked()       # disarms after one pick

    def test_confirm_emits_the_profile(self):
        step = _step()
        p = _profile()
        step.bind(p, _canvas())
        got = []
        step.profile_confirmed.connect(got.append)
        step._on_confirm()
        assert got == [p]

    def test_confirm_is_disabled_without_samples(self):
        step = _step()
        step.bind(VerticalProfile(), _canvas())
        assert not step._confirm_btn.isEnabled()
        got = []
        step.profile_confirmed.connect(got.append)
        step._on_confirm()
        assert got == []

    def test_fingerprint_mismatch_warns_but_keeps_the_samples(self):
        step = _step()
        p = _profile()
        p.geom_fingerprint = (3, 19980.0)
        warnings = []
        step.log_message.connect(lambda m, lvl: warnings.append((m, lvl)))
        step.prepare([{"length": 12345.0}], 5514, {}, p, _canvas())
        assert any(lvl == "warn" for _, lvl in warnings)
        assert len(p.s) > 0                        # never silently discarded

    def test_matching_fingerprint_does_not_warn(self):
        step = _step()
        p = _profile()
        elements = [{"length": 12345.0}]
        p.geom_fingerprint = (1, 12345.0)
        warnings = []
        step.log_message.connect(lambda m, lvl: warnings.append((m, lvl)))
        step.prepare(elements, 5514, {}, p, _canvas())
        assert not any(lvl == "warn" for _, lvl in warnings)

    def test_sample_results_become_nan_for_dem_misses(self):
        step = _step()
        p = VerticalProfile()
        step.bind(p, _canvas())
        step._elements = [{"length": 60.0}]
        step._on_sample_finished(np.array([0.0, 20.0, 40.0]),
                                 [100.0, None, 102.0])
        assert np.isnan(p.z_raw[1])
        assert p.stats["n_nodata"] == 1

    def test_theme_switch(self):
        step = _step()
        step.bind(_profile(), _canvas())
        step.applyTheme(True)
        step.applyTheme(False)
        assert not step.grab().isNull()
