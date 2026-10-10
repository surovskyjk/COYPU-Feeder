"""
App-level wiring of the Vertical Profile step.

`App()` itself cannot be constructed offscreen (QWebEngineView segfault —
pre-existing, see `test_post_export_interactivity.py`), so these exercise the
unbound methods against a lightweight stand-in for `self`. That is sufficient:
none of them touches anything but the attributes it reads or writes explicitly.

What is being protected:

  * the export receives the *confirmed* profile and therefore skips the DEM
    query — the whole point of moving sampling out of the export worker;
  * a project with no vertical block (new, or pre-2.0) still gets a bound
    profile rather than an empty panel;
  * the bottom panel never shows the element table and the profile canvas at
    once, since they share the same splitter slot.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from geometry.profile import VerticalProfile, rebuild_profile  # noqa: E402
from gui.app import (  # noqa: E402
    App, N_STEPS, S_CROSSSEC, S_DESIGN, S_EXPORT, S_STATIONS, S_VERTICAL,
)


def _sampled_profile():
    s = np.arange(0.0, 4000.0, 20.0)
    return rebuild_profile(VerticalProfile(s=s, z_raw=200.0 + 0.006 * s))


class _FakeApp:
    _DOWNSTREAM = App._DOWNSTREAM
    _ALIGNMENT_STEPS = App._ALIGNMENT_STEPS
    _STEP_TIPS = App._STEP_TIPS

    def __init__(self, profile=None, elements=None, pi_model=None):
        self._profile = profile
        self._elements = elements if elements is not None else [
            {"type": "Line", "length": 3980.0}]
        self._pi_model = pi_model
        self._settings = {"vc_length": 120.0, "sample_interval": 25.0}
        self._selected_tracks = []
        self._tracks = []
        self._stations = []
        self._xy_list = []
        self._chainages_list = []
        self._work_epsg = 5514
        self._level = ""
        self._stale = set()
        self._done_steps = set()
        self._current_step = S_CROSSSEC
        self._suggested_step = None
        self.step8_vertical = MagicMock()
        self.step7_export = MagicMock()
        self.step6_stations = MagicMock()
        self.step6_crosssec = MagicMock()
        self.profile_canvas = MagicMock()
        self.element_table = MagicMock()
        self.map_widget = MagicMock()
        self.log_panel = MagicMock()
        self.sidebar = MagicMock()
        self.stack = MagicMock()
        self._step_label = MagicMock()
        self.dirty_calls = []
        self.goto_calls = []
        self.status_calls = []

    # collaborators the methods under test call back into
    def _set_dirty(self, on):
        self.dirty_calls.append(on)

    def _goto_step(self, idx):
        self.goto_calls.append(idx)

    def statusBar(self):
        bar = MagicMock()
        bar.showMessage.side_effect = self.status_calls.append
        return bar

    _prepare_vertical = App._prepare_vertical
    _prepare_export = App._prepare_export
    _on_profile_changed = App._on_profile_changed
    _on_profile_confirmed = App._on_profile_confirmed
    _collect_state = App._collect_state
    _refresh_downstream = App._refresh_downstream


# ---------------------------------------------------------------------------

class TestPrepareVertical:

    def test_creates_a_default_profile_when_there_is_none(self):
        """A new project, or one loaded from a pre-2.0 file."""
        fake = _FakeApp(profile=None)
        fake._prepare_vertical()
        assert fake._profile is not None
        assert fake._profile.vc_length == 120.0        # seeded from settings
        assert fake._profile.sample_interval == 25.0
        assert fake._profile.geom_fingerprint == (1, 3980.0)

    def test_keeps_an_existing_profile(self):
        p = _sampled_profile()
        fake = _FakeApp(profile=p)
        fake._prepare_vertical()
        assert fake._profile is p

    def test_binds_the_shared_canvas_to_the_step(self):
        fake = _FakeApp(profile=_sampled_profile())
        fake._prepare_vertical()
        args = fake.step8_vertical.prepare.call_args[0]
        assert args[0] is fake._elements
        assert args[1] == 5514
        assert args[3] is fake._profile
        assert args[4] is fake.profile_canvas


class TestPrepareExport:

    def test_confirmed_profile_is_handed_to_the_export(self):
        p = _sampled_profile()
        fake = _FakeApp(profile=p)
        fake._prepare_export()
        kwargs = fake.step7_export.prepare.call_args[1]
        assert kwargs["vertical_list"] == [p.pvis]

    def test_no_profile_means_the_worker_falls_back_to_the_dem(self):
        fake = _FakeApp(profile=None)
        fake._prepare_export()
        assert fake.step7_export.prepare.call_args[1]["vertical_list"] is None

    def test_unsampled_profile_also_falls_back(self):
        fake = _FakeApp(profile=VerticalProfile())
        fake._prepare_export()
        assert fake.step7_export.prepare.call_args[1]["vertical_list"] is None

    def test_stations_are_still_forwarded(self):
        fake = _FakeApp(profile=_sampled_profile())
        fake._stations = ["a", "b"]
        fake._prepare_export()
        assert fake.step7_export.prepare.call_args[1]["stations"] == ["a", "b"]


class TestProfileEdits:

    def test_an_edit_makes_only_the_export_stale(self):
        """The step mutates the shared profile in place, so the vertical step
        itself is already current — re-preparing it would just discard the
        user's scroll position for nothing."""
        fake = _FakeApp(profile=_sampled_profile())
        fake._on_profile_changed()
        assert fake._stale == {S_EXPORT}
        assert fake.dirty_calls == [True]

    def test_confirm_prepares_the_export_and_advances(self):
        p = _sampled_profile()
        fake = _FakeApp(profile=None)
        fake._stale = {S_EXPORT}
        fake._on_profile_confirmed(p)
        assert fake._profile is p
        assert fake.step7_export.prepare.called
        assert S_EXPORT not in fake._stale
        assert fake.goto_calls == [S_EXPORT]

    def test_confirm_reports_the_numbers_the_user_was_shown(self):
        p = _sampled_profile()
        fake = _FakeApp()
        fake._on_profile_confirmed(p)
        msg = fake.status_calls[-1]
        assert str(p.stats["n_pvi"]) in msg
        assert "max gradient" in msg


class TestDownstreamRefresh:

    def test_vertical_is_a_downstream_step(self):
        """It consumes the alignment, so a horizontal edit must invalidate it."""
        assert S_VERTICAL in App._DOWNSTREAM

    def test_refresh_dispatches_to_the_vertical_branch(self):
        fake = _FakeApp(profile=None)
        fake._stale = {S_VERTICAL}
        fake._refresh_downstream(S_VERTICAL)
        assert fake.step8_vertical.prepare.called
        assert S_VERTICAL not in fake._stale

    def test_refresh_export_uses_the_vertical_aware_path(self):
        p = _sampled_profile()
        fake = _FakeApp(profile=p)
        fake._stale = {S_EXPORT}
        fake._refresh_downstream(S_EXPORT)
        assert fake.step7_export.prepare.call_args[1]["vertical_list"] == [p.pvis]


class TestStateAndOrdering:

    def test_profile_is_part_of_the_saved_state(self):
        p = _sampled_profile()
        fake = _FakeApp(profile=p)
        assert fake._collect_state()["profile"] is p

    def test_step_order_puts_vertical_between_crosssection_and_export(self):
        assert S_CROSSSEC < S_VERTICAL < S_EXPORT
        assert S_EXPORT == N_STEPS - 1

    def test_vertical_expects_the_interactive_alignment_view(self):
        assert S_VERTICAL in App._ALIGNMENT_STEPS

    def test_back_routing_threads_through_the_new_step(self):
        """Built in _build_toolbar, so read it off a real instance's method
        source rather than constructing App."""
        import inspect
        src = inspect.getsource(App._build_toolbar)
        assert "S_VERTICAL:    lambda: self._goto_step(S_CROSSSEC)" in src
        assert "S_EXPORT:      lambda: self._goto_step(S_VERTICAL)" in src

    def test_every_downstream_step_has_a_refresh_branch(self):
        import inspect
        src = inspect.getsource(App._refresh_downstream)
        for name in ("S_STATIONS", "S_CROSSSEC", "S_VERTICAL", "S_EXPORT"):
            assert name in src

    def test_table_and_canvas_never_share_the_bottom_panel(self):
        import inspect
        src = inspect.getsource(App._goto_step)
        assert "idx != S_VERTICAL" in src
        assert "self.profile_canvas.setVisible(idx == S_VERTICAL)" in src

    def test_cross_section_hands_off_to_vertical_not_export(self):
        import inspect
        src = inspect.getsource(App._on_analysis_done)
        assert "self._goto_step(S_VERTICAL)" in src
        assert "S_EXPORT" not in src
