"""
The vertical profile ↔ map hover link, below the App wiring
(test_vertical_step_wiring.py covers that):

  * chainage_lookup / latlon_at_s put a profile station on the fitted track;
  * ProfileCanvas.set_cursor_station shows the crosshair for a station that
    comes from the map without echoing it back through hover_changed;
  * MapWidget coalesces the hover dot to one call per frame and drops it
    before the page is ready, and ships the lookup to the page.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.profile import (  # noqa: E402
    VerticalProfile, chainage_lookup, latlon_at_s, rebuild_profile,
)
from geometry.projection import projected_to_wgs84  # noqa: E402
from gui.profile_canvas import ProfileCanvas  # noqa: E402

app = QApplication.instance() or QApplication([])
_KEEP: list = []


def teardown_module(_module):
    for w in _KEEP:
        if isinstance(w, ProfileCanvas):
            w.deleteLater()
    app.processEvents()
    _KEEP.clear()


# S-JTSK (EPSG:5514) near Kolín: a 1 km straight heading east.
X0, Y0 = -680000.0, -1060000.0
LINE = [{"type": "Line", "sta_start": 0.0, "length": 1000.0,
         "start": [X0, Y0], "end": [X0 + 1000.0, Y0], "direction_rad": 0.0}]


class TestChainageLookup:

    @pytest.mark.parametrize("s", [0.0, 1.0, 333.3, 777.77, 1000.0])
    def test_positions_lie_on_the_track(self, s):
        lookup = chainage_lookup(LINE, 5514)
        lat, lon = latlon_at_s(lookup, s)
        exp_lat, exp_lon = projected_to_wgs84([(X0 + s, Y0)], 5514)[0]
        # 1e-7° ≈ 1 cm
        assert abs(lat - exp_lat) < 1e-7 and abs(lon - exp_lon) < 1e-7

    def test_clamped_beyond_the_ends(self):
        lookup = chainage_lookup(LINE, 5514)
        assert latlon_at_s(lookup, -50.0) == latlon_at_s(lookup, 0.0)
        assert latlon_at_s(lookup, 5000.0) == latlon_at_s(lookup, 1000.0)

    def test_density_is_bounded(self):
        s, _lat, _lon = chainage_lookup(LINE, 5514, max_points=10)
        assert np.diff(s).max() <= 25.0 + 1e-9     # never sparser than 25 m
        s, _lat, _lon = chainage_lookup(LINE, 5514)
        assert np.diff(s).min() >= 2.0 - 1e-9      # never denser than 2 m
        assert s[0] == 0.0 and s[-1] == 1000.0

    def test_empty(self):
        lookup = chainage_lookup([], 5514)
        assert latlon_at_s(lookup, 10.0) is None
        assert latlon_at_s(None, 10.0) is None


def _canvas():
    s = np.arange(0.0, 4000.0, 20.0)
    c = ProfileCanvas()
    _KEEP.append(c)
    c.resize(900, 300)
    c.set_profile(rebuild_profile(VerticalProfile(s=s, z_raw=200.0 + 0.006 * s)))
    return c


class TestCanvasCursorFromTheMap:

    def test_shows_the_readout_without_echoing(self):
        c = _canvas()
        seen = []
        c.hover_changed.connect(seen.append)
        c.set_cursor_station(1500.0)
        assert c._hover_x == 1500.0
        assert not c.grab().isNull()
        c.set_cursor_station(-1.0)
        assert c._hover_x is None
        assert seen == []

    def test_a_station_outside_the_zoom_window_is_not_drawn_at_the_edge(self):
        c = _canvas()
        c._view = (0.0, 1000.0)
        c.set_cursor_station(3000.0)
        painter = MagicMock()
        rect = c._plot_rect()
        c._draw_readout(painter, c._tok(), rect, 0.0, 1000.0,
                        lambda v: v, lambda v: v)
        painter.drawLine.assert_not_called()


def _map_widget(ready=True):
    import gui.map_widget as mw
    w = mw.MapWidget.__new__(mw.MapWidget)
    w._map_ready = ready
    w._cursor_pending = None
    w._cursor_timer = QTimer()
    w._cursor_timer.setSingleShot(True)
    w._run_js = MagicMock()
    w._run_js_call = MagicMock()
    _KEEP.append(w._cursor_timer)
    return w


class TestMapWidgetCursor:

    def test_ticks_are_coalesced_to_the_newest(self):
        w = _map_widget()
        for lat in (50.1, 50.2, 50.3):
            w.set_track_cursor(lat, 15.2)
        assert w._cursor_timer.isActive()
        w._cursor_timer.stop()
        w._flush_track_cursor()
        w._run_js.assert_called_once_with("setTrackCursor(50.3000000, 15.2000000)")

    def test_clear(self):
        w = _map_widget()
        w.set_track_cursor(50.1, 15.2)
        w.clear_track_cursor()
        w._cursor_timer.stop()
        w._flush_track_cursor()
        w._run_js.assert_called_once_with("clearTrackCursor()")

    def test_dropped_before_the_map_is_ready(self):
        w = _map_widget(ready=False)
        w.set_track_cursor(50.1, 15.2)
        assert not w._cursor_timer.isActive()
        assert w._cursor_pending is None
        w._run_js.assert_not_called()

    def test_profile_link_payload(self):
        w = _map_widget()
        w.set_profile_link(np.array([0.0, 12.34567]), np.array([50.123456789, 50.2]),
                           np.array([15.1, 15.987654321]))
        w._run_js_call.assert_called_once_with("setProfileLink", {
            "s": [0.0, 12.346], "lat": [50.1234568, 50.2], "lon": [15.1, 15.9876543]})
        w.clear_profile_link()
        w._run_js.assert_called_once_with("clearProfileLink()")
