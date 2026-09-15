"""
Part 3C — deviation heatmap ("Colour by baseline deviation", Req 9b),
per-element scope for this round.

`palette.deviation_color` is the green -> yellow -> red ramp; `App` threads
a `color_mode` through `_sample_segments`/`_render_elements_on_map` that
stamps a `"color"` on each segment/meta from its `_max_dev`, which
`map.html`'s `_addSegmentLayer` prefers over the type-based lookup
(`seg.color || ELEMENT_COLOR[seg.type] || ...`) — hover/click/highlight are
untouched, they key off element_id, never colour.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.palette import deviation_color, DEVIATION_RAMP_STOPS  # noqa: E402


class TestDeviationColorRamp:

    def test_zero_deviation_is_the_good_stop(self):
        assert deviation_color(0.0, 1.0) == DEVIATION_RAMP_STOPS[0][1]

    def test_at_threshold_is_the_worst_stop(self):
        assert deviation_color(1.0, 1.0) == DEVIATION_RAMP_STOPS[-1][1]

    def test_beyond_threshold_clamps_to_the_worst_stop_not_a_new_colour(self):
        assert deviation_color(50.0, 1.0) == deviation_color(1.0, 1.0)

    def test_midpoint_is_the_middle_stop(self):
        assert deviation_color(0.5, 1.0) == DEVIATION_RAMP_STOPS[1][1]

    def test_interpolates_between_stops(self):
        c_quarter = deviation_color(0.25, 1.0)
        c_good = DEVIATION_RAMP_STOPS[0][1]
        c_mid = DEVIATION_RAMP_STOPS[1][1]
        assert c_quarter not in (c_good, c_mid)

    def test_worst_stop_has_the_highest_red_channel(self):
        """Sanity: the ramp must actually end up "reddest" at the worst end,
        not just a differently-hued colour."""
        def red(hexcol):
            return int(hexcol.lstrip("#")[0:2], 16)
        vals = [deviation_color(x, 1.0) for x in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
        reds = [red(v) for v in vals]
        assert red(vals[-1]) == max(reds)

    def test_zero_threshold_does_not_divide_by_zero(self):
        # threshold is clamped to a tiny positive floor internally
        assert deviation_color(0.0, 0.0) == DEVIATION_RAMP_STOPS[0][1]
        assert deviation_color(1.0, 0.0) == DEVIATION_RAMP_STOPS[-1][1]


class TestSampleSegmentsColorMode:

    def _make_app_stub(self, elements):
        from gui.app import App
        fake = MagicMock()
        fake._work_epsg = 32633
        fake._settings = {"max_deviation": 1.0}
        return fake

    def test_type_mode_has_no_color_key(self):
        from gui.app import App
        el = {"type": "Line", "start": [0.0, 0.0], "end": [10.0, 0.0],
              "length": 10.0, "direction_rad": 0.0, "_max_dev": 0.3}
        fake = self._make_app_stub([el])
        payload = App._sample_segments(fake, [el], 5.0, "type")
        assert payload
        assert "color" not in payload[0]

    def test_deviation_mode_stamps_color(self):
        from gui.app import App
        el = {"type": "Line", "start": [0.0, 0.0], "end": [10.0, 0.0],
              "length": 10.0, "direction_rad": 0.0, "_max_dev": 0.3}
        fake = self._make_app_stub([el])
        payload = App._sample_segments(fake, [el], 5.0, "deviation")
        assert payload
        assert payload[0]["color"] == deviation_color(0.3, 1.0)

    def test_deviation_mode_uses_project_threshold(self):
        from gui.app import App
        el = {"type": "Line", "start": [0.0, 0.0], "end": [10.0, 0.0],
              "length": 10.0, "direction_rad": 0.0, "_max_dev": 0.8}
        fake = self._make_app_stub([el])
        fake._settings = {"max_deviation": 2.0}
        payload = App._sample_segments(fake, [el], 5.0, "deviation")
        assert payload[0]["color"] == deviation_color(0.8, 2.0)

    def test_missing_max_dev_defaults_to_zero_not_a_crash(self):
        from gui.app import App
        el = {"type": "Line", "start": [0.0, 0.0], "end": [10.0, 0.0],
              "length": 10.0, "direction_rad": 0.0}
        fake = self._make_app_stub([el])
        payload = App._sample_segments(fake, [el], 5.0, "deviation")
        assert payload[0]["color"] == deviation_color(0.0, 1.0)


class TestColorModePreferenceRouting:

    def test_render_elements_on_map_defaults_to_pref(self):
        from gui.app import App
        fake = MagicMock()
        fake._prefs = {"map_color_mode": "deviation"}
        calls = []
        fake._sample_segments = lambda elements, interval, color_mode: (
            calls.append(color_mode) or [{"type": "Line", "params": {}, "points": [[0, 0], [1, 1]]}])
        fake.map_widget.show_alignment_segmented = MagicMock()
        el = {"type": "Line", "length": 10.0}
        App._render_elements_on_map(fake, [el], fit_view=True)
        assert calls == ["deviation"]

    def test_explicit_color_mode_overrides_pref(self):
        from gui.app import App
        fake = MagicMock()
        fake._prefs = {"map_color_mode": "deviation"}
        calls = []
        fake._sample_segments = lambda elements, interval, color_mode: (
            calls.append(color_mode) or [{"type": "Line", "params": {}, "points": [[0, 0], [1, 1]]}])
        fake.map_widget.show_alignment_segmented = MagicMock()
        el = {"type": "Line", "length": 10.0}
        App._render_elements_on_map(fake, [el], fit_view=True, color_mode="type")
        assert calls == ["type"]


class TestColorModeToggleHandler:

    def test_on_color_mode_changed_persists_and_rerenders(self):
        from gui.app import App
        fake = MagicMock()
        fake._prefs = {}
        fake._elements = [{"type": "Line", "length": 10.0}]
        fake._color_mode_combo.currentData.return_value = "deviation"
        App._on_color_mode_changed(fake, 1)
        assert fake._prefs["map_color_mode"] == "deviation"
        fake._save_prefs.assert_called_once()
        fake._render_elements_on_map.assert_called_once_with(
            fake._elements, color_mode="deviation")

    def test_no_rerender_when_no_elements_yet(self):
        from gui.app import App
        fake = MagicMock()
        fake._prefs = {}
        fake._elements = []
        fake._color_mode_combo.currentData.return_value = "type"
        App._on_color_mode_changed(fake, 0)
        fake._render_elements_on_map.assert_not_called()
