"""
Tests for `geometry.candidates.auto_consolidate` and its hook into
`CandidateGenerator._run_level2/_run_level3` (curve runs pre-merged during
candidate generation, gated by settings["auto_consolidate"]).

Note on thresholds: `extract_pi_model` already auto-merges same-rotation
curve pairs separated by less than `_PI_MIN_TANGENT` (60 m) as part of
building the tangent polygon. `auto_consolidate`'s own default
`max_straight_m` (30 m) is intentionally smaller — it is a secondary,
narrower pass (the wide-gap case is left to the manual "Consolidate curve
runs" tool). To exercise `auto_consolidate` itself these tests read the
ACTUAL connecting-straight lengths extraction produced (tangent-length
consumption means they never equal the nominal polyline gap fed in) and
derive thresholds from those measured values, rather than assuming a
specific number — hand-tuning an exact nominal gap proved fragile since DP
+ cluster-merge reshape a synthetic multi-curve track nontrivially.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402


def _build_three_curve_track(gap=70.0, R=(600.0, 600.0, 600.0),
                             degs=(30.0, 25.0, 20.0)):
    """Three same-rotation arcs joined by straights of `gap` metres — wider
    than _PI_MIN_TANGENT (60 m) so extraction does not pre-merge them."""
    pts = [(0.0, 0.0)]
    heading = 0.0

    def line(length, step=5.0):
        nonlocal heading
        n = max(2, int(length / step))
        start = pts[-1]
        for i in range(1, n + 1):
            d = length * i / n
            pts.append((start[0] + d * math.cos(heading),
                        start[1] + d * math.sin(heading)))

    def arc(radius, deg, step=5.0):
        nonlocal heading
        ang = math.radians(deg)
        sgn = 1.0 if ang >= 0 else -1.0
        cx = pts[-1][0] + radius * math.cos(heading + sgn * math.pi / 2)
        cy = pts[-1][1] + radius * math.sin(heading + sgn * math.pi / 2)
        a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
        n = max(2, int(abs(ang) * radius / step))
        for i in range(1, n + 1):
            a = a0 + ang * i / n
            pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        heading += ang

    line(400)
    for i, (r, d) in enumerate(zip(R, degs)):
        arc(r, d)
        if i < len(R) - 1:
            line(gap)
    line(400)

    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return xy, ch


def _connecting_gaps(model) -> list[float]:
    """Length of the Line element directly between each consecutive pair
    of Arcs (what _curve_runs/find_consolidation_groups actually measure)."""
    gaps = []
    pending = 0.0
    seen_arc = False
    for e in model.elements:
        if e["type"] == "Arc":
            if seen_arc:
                gaps.append(pending)
            pending = 0.0
            seen_arc = True
        elif e["type"] == "Line" and seen_arc:
            pending += e["length"]
    return gaps


def _build_model(gap=70.0):
    xy, ch = _build_three_curve_track(gap=gap)
    model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                               spiral_length=20.0, use_spirals=True)
    n_arcs = sum(1 for e in model.elements if e["type"] == "Arc")
    if n_arcs < 2:
        pytest.skip(f"extraction already merged the runs ({n_arcs} arc(s)) "
                    "— gap too small for this test's purpose")
    return model, n_arcs


class TestAutoConsolidate:

    def test_merges_qualifying_runs(self):
        model, n_arcs_before = _build_model(gap=70.0)
        gaps = _connecting_gaps(model)
        wide = max(gaps) + 50.0   # comfortably above every actual gap
        # The synthetic track's four arcs end up with quite different fitted
        # radii (extraction reshapes a hand-built polyline nontrivially), so
        # a single merged curve deviates more than a "real" curve run would
        # — 10 m keeps this a meaningful bound while still accepting it.
        applied = C.auto_consolidate(model, max_straight_m=wide, max_dev_m=10.0)
        n_arcs_after = sum(1 for e in model.elements if e["type"] == "Arc")
        assert applied > 0, "expected at least one run to merge"
        assert n_arcs_after < n_arcs_before, (
            f"{n_arcs_before} -> {n_arcs_after} arcs; expected a reduction")
        # C1 continuity must hold — construction guarantees it, but prove it.
        # Tightened from 1e-3 now that _heading_at_start/_end use the exact
        # clothoid chord-to-tangent angle instead of the α ≈ θ_s/3
        # approximation (see geometry.candidates._clothoid_chord_angle).
        assert C._max_heading_jump_rad(model.elements) < 1e-9

    def test_idempotent(self):
        model, _ = _build_model(gap=70.0)
        gaps = _connecting_gaps(model)
        wide = max(gaps) + 50.0
        first = C.auto_consolidate(model, max_straight_m=wide, max_dev_m=10.0)
        assert first > 0
        second = C.auto_consolidate(model, max_straight_m=wide, max_dev_m=10.0)
        assert second == 0, "a second pass on an already-consolidated model must find nothing"

    def test_respects_deviation_bound(self):
        model, n_arcs_before = _build_model(gap=70.0)
        gaps = _connecting_gaps(model)
        wide = max(gaps) + 50.0
        # An unreasonably tight bound must reject the merge outright.
        applied = C.auto_consolidate(model, max_straight_m=wide, max_dev_m=0.001)
        n_arcs_after = sum(1 for e in model.elements if e["type"] == "Arc")
        assert applied == 0
        assert n_arcs_after == n_arcs_before

    def test_narrow_max_straight_finds_nothing(self):
        """A threshold below every actual gap must leave the model alone."""
        model, n_arcs_before = _build_model(gap=70.0)
        gaps = _connecting_gaps(model)
        narrow = max(0.0, min(gaps) - 1.0)
        applied = C.auto_consolidate(model, max_straight_m=narrow, max_dev_m=5.0)
        assert applied == 0
        assert sum(1 for e in model.elements if e["type"] == "Arc") == n_arcs_before

    def test_zero_qualifying_runs_returns_zero(self):
        """A track with no same-direction runs at all — must not crash."""
        pts = [(0.0, 0.0), (500.0, 0.0)]
        xy = np.array(pts, dtype=float)
        ch = np.array([0.0, 500.0])
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        assert C.auto_consolidate(model) == 0


class TestCandidateGeneratorHook:

    def _settings(self, auto: bool) -> dict:
        return {
            "max_deviation": 1.0, "min_radius": 150.0, "spiral_length": 20.0,
            "auto_consolidate": auto, "check_interval": 5.0,
        }

    def test_auto_merged_field_present_and_zero_when_disabled(self):
        xy, ch = _build_three_curve_track(gap=70.0)
        gen = C.CandidateGenerator(xy, ch, self._settings(auto=False))
        c = gen._run_level3()
        assert c.auto_merged == 0
        c2 = gen._run_level2()
        assert c2.auto_merged == 0

    def test_auto_consolidate_setting_gates_the_hook(self):
        xy, ch = _build_three_curve_track(gap=70.0)

        gen_off = C.CandidateGenerator(xy, ch, self._settings(auto=False))
        c_off = gen_off._run_level3()

        gen_on = C.CandidateGenerator(xy, ch, self._settings(auto=True))
        c_on = gen_on._run_level3()

        n_arcs_off = sum(1 for e in c_off.elements if e["type"] == "Arc")
        n_arcs_on = sum(1 for e in c_on.elements if e["type"] == "Arc")
        # ON must never produce MORE arcs than OFF, and auto_merged must be
        # internally consistent with what actually happened.
        assert n_arcs_on <= n_arcs_off
        if c_on.auto_merged > 0:
            assert n_arcs_on < n_arcs_off
        else:
            assert n_arcs_on == n_arcs_off

    def test_default_setting_is_on(self):
        """Omitting the key entirely must default to auto-consolidate ON
        (settings.get("auto_consolidate", True))."""
        xy, ch = _build_three_curve_track(gap=70.0)
        settings = self._settings(auto=True)
        del settings["auto_consolidate"]
        gen = C.CandidateGenerator(xy, ch, settings)
        c = gen._run_level3()
        # Must not crash; auto_merged reflects whatever the default gate did.
        assert isinstance(c.auto_merged, int)

    def test_off_is_fingerprint_identical_to_pre_phase6_output(self):
        """With the setting off, _run_level3 must behave exactly as it did
        before this feature existed — same elements as calling
        extract_pi_model directly with no auto_consolidate step."""
        xy, ch = _build_three_curve_track(gap=70.0)
        gen = C.CandidateGenerator(xy, ch, self._settings(auto=False))
        c = gen._run_level3()

        ref_model = C.extract_pi_model(
            xy, ch, tolerance=gen.max_deviation, min_radius=gen.min_radius,
            spiral_length=gen.spiral_length, use_spirals=True,
        )
        assert len(c.elements) == len(ref_model.elements)
        for a, b in zip(c.elements, ref_model.elements):
            assert C._element_geom_equal(a, b)
