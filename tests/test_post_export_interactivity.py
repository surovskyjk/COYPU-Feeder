"""
Part 3A — restore map interactivity after export (Req 4).

Root cause (three-link chain, all in `gui/app.py` / `gui/worker.py`):

1. `App._on_alignment_ready` used to unconditionally call the NON-
   interactive `map_widget.show_alignment(...)` (`map.html`'s
   `showAlignment` — no hover/click/highlight bindings at all), relying on
   a SEPARATE `alignment_segments_ready` signal to supersede it.
2. That signal only fires `if per_element_segments`, built inside a bare
   `except Exception: pass` in `worker.py` — any hiccup left the map
   permanently non-interactive after export with no error anywhere.
3. `_goto_step` never re-rendered the alignment on navigation, so the dead
   state survived the trip back to Refine/Stations/etc.

`App()` itself can't be constructed offscreen (QWebEngineView segfault,
pre-existing/confirmed) — these tests exercise the unbound methods against
a lightweight stand-in for `self`, which is sufficient since neither method
touches anything but the attributes it reads/writes explicitly.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.app as app_mod  # noqa: E402
from gui.app import (  # noqa: E402
    App, S_DESIGN, S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT,
    S_CANDIDATES,
)


class _FakeApp:
    """Minimal stand-in for App carrying only what the methods under test
    read or write."""

    _ALIGNMENT_STEPS = App._ALIGNMENT_STEPS

    def __init__(self, elements=None, pi_model=None, js_aln_count=None):
        self._elements = elements or []
        self._pi_model = pi_model
        self.map_widget = MagicMock()
        self.map_widget._js_aln_count = js_aln_count
        self.render_calls = []
        self.overlay_calls = []

    def _render_elements_on_map(self, elements, fit_view=False, splice=None):
        self.render_calls.append((list(elements), fit_view))

    def _show_pi_overlay(self, pi_model, moved_only=None):
        self.overlay_calls.append(pi_model)


class TestEnsureInteractiveAlignmentShown:

    def test_rerenders_when_map_is_noninteractive_and_elements_exist(self):
        """The exact post-export bug: elements exist, but the map is
        showing the non-interactive draw (_js_aln_count == 0)."""
        fake = _FakeApp(elements=[{"type": "Line"}], js_aln_count=0)
        App._ensure_interactive_alignment_shown(fake, S_DESIGN)
        assert fake.render_calls, "expected a re-render"
        assert fake.overlay_calls

    def test_rerenders_when_nothing_ever_drawn(self):
        fake = _FakeApp(elements=[{"type": "Line"}], js_aln_count=None)
        App._ensure_interactive_alignment_shown(fake, S_STATIONS)
        assert fake.render_calls

    def test_noop_when_already_interactive(self):
        fake = _FakeApp(elements=[{"type": "Line"}], js_aln_count=42)
        App._ensure_interactive_alignment_shown(fake, S_DESIGN)
        assert not fake.render_calls

    def test_noop_when_no_elements(self):
        fake = _FakeApp(elements=[], js_aln_count=0)
        App._ensure_interactive_alignment_shown(fake, S_DESIGN)
        assert not fake.render_calls

    def test_noop_on_non_alignment_steps(self):
        """S_CANDIDATES shows track/candidate overlays, not the edited
        alignment — must never be force-rendered here."""
        fake = _FakeApp(elements=[{"type": "Line"}], js_aln_count=0)
        App._ensure_interactive_alignment_shown(fake, S_CANDIDATES)
        assert not fake.render_calls

    def test_covers_every_alignment_relevant_step(self):
        for idx in (S_DESIGN, S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT):
            fake = _FakeApp(elements=[{"type": "Line"}], js_aln_count=0)
            App._ensure_interactive_alignment_shown(fake, idx)
            assert fake.render_calls, f"step {idx} should trigger a re-render"


class TestOnAlignmentReadyPrefersInteractivePath:

    def test_uses_render_elements_on_map_when_elements_exist(self):
        fake = _FakeApp(elements=[{"type": "Line"}, {"type": "Arc"}])
        fake.statusBar = MagicMock(return_value=MagicMock())
        App._on_alignment_ready(fake, [[[50.0, 14.0], [50.1, 14.1]]])
        assert fake.render_calls, "must route through the interactive render path"
        assert fake.render_calls[0][1] is True   # fit_view=True on export

    def test_falls_back_to_merged_draw_when_no_elements(self):
        fake = _FakeApp(elements=[])
        fake.statusBar = MagicMock(return_value=MagicMock())
        alignments = [[[50.0, 14.0], [50.1, 14.1]]]
        App._on_alignment_ready(fake, alignments)
        assert not fake.render_calls
        fake.map_widget.show_alignment.assert_called_once()

    def test_reports_no_points_when_both_empty(self):
        fake = _FakeApp(elements=[])
        fake.statusBar = MagicMock(return_value=MagicMock())
        App._on_alignment_ready(fake, [])
        assert not fake.render_calls
        fake.map_widget.show_alignment.assert_not_called()


class TestOnAlignmentSegmentsReadyDoesNotRaceTheInteractiveRender:

    def test_skips_redraw_when_elements_already_rendered_interactively(self):
        fake = _FakeApp(elements=[{"type": "Line"}])
        fake.statusBar = MagicMock(return_value=MagicMock())
        App._on_alignment_segments_ready(fake, [{"type": "Line", "params": {}}])
        fake.map_widget.show_alignment_segmented.assert_not_called()

    def test_still_draws_when_no_elements_exist(self):
        fake = _FakeApp(elements=[])
        fake.statusBar = MagicMock(return_value=MagicMock())
        segments = [{"type": "Line", "params": {}}]
        App._on_alignment_segments_ready(fake, segments)
        fake.map_widget.show_alignment_segmented.assert_called_once_with(segments)
