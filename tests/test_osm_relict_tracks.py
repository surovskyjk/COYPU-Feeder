"""
Part 3D — the red "relict" OSM polyline (reported alongside the stationPane
click-blocking bug last round; still live).

Root cause: `show_tracks` draws the raw OSM track(s) into `tracksPane` with
`TRACK_COLORS[0]` (opaque red) — NOT `MAP_COLORS.osmReference`, and NOT
governed by `set_osm_reference_style`'s "Original appearance" popover.
Nothing ever cleared that layer on step advance (only `clearAll()` did), so
once the styled dashed OSM reference took over for editing, the raw track
layer stayed underneath, uncontrollable and looking like "the original
polyline stuck red and won't change colour."

`App._clear_osm_relict_tracks`, called from `_goto_step` alongside the Req 4
fix, clears the raw layer and (re)pushes the properly-styled dashed
reference once navigation reaches a step where an alignment is actually
being designed.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.app import (  # noqa: E402
    App, S_DESIGN, S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT,
    S_CANDIDATES,
)


class _FakeTrack:
    def __init__(self, nodes):
        self.nodes = nodes


def _fake_app(selected_tracks):
    fake = MagicMock()
    fake._ALIGNMENT_STEPS = App._ALIGNMENT_STEPS
    fake._selected_tracks = selected_tracks
    return fake


class TestClearOsmRelictTracks:

    def test_clears_tracks_and_pushes_reference_on_alignment_steps(self):
        fake = _fake_app([_FakeTrack([(50.0, 14.0), (50.1, 14.1)])])
        App._clear_osm_relict_tracks(fake, S_DESIGN)
        fake.map_widget.clear_tracks.assert_called_once()
        fake.map_widget.show_osm_reference.assert_called_once()

    def test_noop_outside_alignment_steps(self):
        fake = _fake_app([_FakeTrack([(50.0, 14.0), (50.1, 14.1)])])
        App._clear_osm_relict_tracks(fake, S_CANDIDATES)
        fake.map_widget.clear_tracks.assert_not_called()
        fake.map_widget.show_osm_reference.assert_not_called()

    def test_clears_but_does_not_push_reference_without_tracks(self):
        """No selected tracks (shouldn't normally happen once elements
        exist) — still clear the relict, just don't push an empty ref."""
        fake = _fake_app([])
        App._clear_osm_relict_tracks(fake, S_DESIGN)
        fake.map_widget.clear_tracks.assert_called_once()
        fake.map_widget.show_osm_reference.assert_not_called()

    def test_covers_every_alignment_relevant_step(self):
        for idx in (S_DESIGN, S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT):
            fake = _fake_app([_FakeTrack([(50.0, 14.0), (50.1, 14.1)])])
            App._clear_osm_relict_tracks(fake, idx)
            fake.map_widget.clear_tracks.assert_called_once()


class TestMapWidgetClearTracks:

    def _map_widget(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        import gui.map_widget as mw
        widget = mw.MapWidget.__new__(mw.MapWidget)
        widget._run_js_call = MagicMock()
        return widget

    def test_clear_tracks_sends_empty_track_list(self):
        widget = self._map_widget()
        widget.clear_tracks()
        widget._run_js_call.assert_called_once_with("showTracks", [], False)
