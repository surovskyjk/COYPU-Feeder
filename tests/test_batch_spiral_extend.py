"""
Part D — Batch "Extend Transition Spirals".

`batch_extend_spirals(model, target_length, mode=...)` lengthens every
eligible curve's spirals toward a target length, wherever geometrically
feasible, applying each PI through `rebuild_pi_span` (the same span-local
path every other edit uses) and rolling back a PI whose edit makes no real
progress.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from geometry.candidates import _max_heading_jump_rad  # noqa: E402


def make_track(segs, step=5.0):
    """segs: ('S', length) or ('A', radius, signed_degrees)."""
    pts = [(0.0, 0.0)]
    heading = 0.0
    for s in segs:
        if s[0] == "S":
            start = pts[-1]
            n = max(2, int(s[1] / step))
            for i in range(1, n + 1):
                d = s[1] * i / n
                pts.append((start[0] + d * math.cos(heading),
                            start[1] + d * math.sin(heading)))
        else:
            _, R, deg = s
            ang = math.radians(deg)
            sgn = 1.0 if ang >= 0 else -1.0
            cx = pts[-1][0] + R * math.cos(heading + sgn * math.pi / 2)
            cy = pts[-1][1] + R * math.sin(heading + sgn * math.pi / 2)
            a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
            n = max(2, int(abs(ang) * R / step))
            for i in range(1, n + 1):
                a = a0 + ang * i / n
                pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
            heading += ang
    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return xy, ch


# Generous straights (800 m) so every curve has real room to extend into,
# and one deliberately tight one (PI at the "TIGHT" arc) that cannot reach
# a large target — exercises "clamped"/"skipped" as well as "lengthened".
ROOMY = [("S", 800), ("A", 600, 30), ("S", 800), ("A", 550, -25), ("S", 800),
        ("A", 620, 20), ("S", 800)]

TIGHT = [("S", 800), ("A", 600, 30), ("S", 800), ("A", 1200, -40), ("S", 60),
        ("A", 550, 25), ("S", 800)]


def build_model(segs, spiral_length=20.0, min_radius=150.0):
    xy, ch = make_track(segs)
    return C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=min_radius,
                              spiral_length=spiral_length, use_spirals=True)


def eligible_pis(model):
    return [p for p in model.pis
            if not p.omitted and not p.merged_with_next
            and not p.merged_with_prev and not getattr(p, "chain_next", False)]


def fingerprint(model):
    out = []
    for e in model.elements:
        row = [e.get("type")]
        for key in ("sta_start", "length", "radius", "radius_start",
                    "radius_end", "clothoid_A"):
            v = e.get(key)
            row.append(round(float(v), 9) if v is not None else None)
        for key in ("start", "end"):
            v = e.get(key)
            row.append((round(float(v[0]), 9), round(float(v[1]), 9))
                       if v is not None else None)
        out.append(tuple(row))
    return tuple(out)


class TestFixedRadiusMode:

    def test_never_changes_radius(self):
        model = build_model(ROOMY)
        before = {p.index: p.radius for p in model.pis if not p.omitted}
        C.batch_extend_spirals(model, target_length=60.0, mode="fixed_radius")
        after = {p.index: p.radius for p in model.pis if not p.omitted}
        assert before == after

    def test_lengthens_toward_target_where_feasible(self):
        model = build_model(ROOMY)
        elig = eligible_pis(model)
        starting = {p.index: p.spiral_len for p in elig}
        result = C.batch_extend_spirals(model, target_length=60.0, mode="fixed_radius")
        assert result.lengthened >= 1
        for p in elig:
            if p.spiral_len > starting[p.index] + 1e-6:
                # Reached at least close to the target on the roomy track.
                assert p.spiral_len >= 59.0

    def test_report_counts_sum_to_eligible_count(self):
        model = build_model(TIGHT)
        elig = eligible_pis(model)
        result = C.batch_extend_spirals(model, target_length=80.0, mode="fixed_radius")
        assert result.lengthened + result.clamped + result.skipped == len(elig)
        assert len(result.notes) == len(elig)


class TestMinimizeDeviationMode:

    def test_radius_may_change_and_deviation_stays_reasonable(self):
        model = build_model(ROOMY)
        before_dev = C.compute_deviation_stats(model.elements, model.xy_ref)
        result = C.batch_extend_spirals(model, target_length=60.0,
                                        mode="minimize_deviation")
        assert result.lengthened + result.clamped >= 1
        after_dev = C.compute_deviation_stats(model.elements, model.xy_ref)
        # Not a strict improvement guarantee (spirals got much longer, which
        # trades some fit for a longer transition) but must stay bounded —
        # a broken search that wanders to absurd radii would blow this up.
        assert after_dev["max_deviation"] < before_dev["max_deviation"] * 5 + 5.0

    def test_min_radius_override_is_respected(self):
        """Every PI the function actually touched (lengthened or clamped —
        i.e. NOT skipped, since a "no radius >= floor fits here" skip is
        exactly how the floor gets honoured when it's unreachable) must end
        up at or above the floor. A PI that was never eligible, or that got
        skipped because even the floor itself didn't fit its tangent room,
        keeps its original (pre-call, possibly sub-floor) radius — the
        floor is a constraint on what this call may CHANGE a radius to,
        not a retroactive requirement on radii it never touched."""
        model = build_model(ROOMY)
        floor = 500.0
        result = C.batch_extend_spirals(model, target_length=60.0,
                                        mode="minimize_deviation", min_radius=floor)
        touched = {k for k, status, _ in result.notes if status != "skipped"}
        assert touched, "expected at least one PI to be lengthened/clamped"
        by_index = {p.index: p for p in model.pis}
        for k in touched:
            assert by_index[k].radius >= floor - 1.0

    def test_search_never_explores_wildly_infeasible_radii(self):
        """Regression guard: an earlier version's search bound was a bare
        generous constant (e.g. 50 km) with no feasibility probe, so Brent
        wandered a flat, clamped region and could report a "clamped" note
        with an absurd requested radius. The committed radius must always
        be a plausible, bounded value."""
        model = build_model(ROOMY)
        C.batch_extend_spirals(model, target_length=60.0, mode="minimize_deviation")
        for p in model.pis:
            if not p.omitted:
                assert p.radius < 20000.0


class TestNeverShrinksAnExistingSpiral:

    def test_target_below_current_length_is_a_noop_for_that_pi(self):
        model = build_model(ROOMY)
        elig = eligible_pis(model)
        # Force every eligible PI's ACTUAL applied length to a known value
        # above the target (extract_pi_model's own initial-fit clamping can
        # leave a PI's applied length below its requested default — reading
        # that back instead of setting it explicitly would make this test
        # depend on unrelated initial-fit tangent-room specifics). Not every
        # PI's tangent room can actually hold 80 m (e.g. a large-radius
        # curve on the same straight length) — restrict the check to the
        # ones where the forced value genuinely took hold.
        for p in elig:
            p.spiral_len = 80.0
        C.rebuild_from_pi_model(model)
        settled = [p.index for p in elig if p.spiral_len >= 79.0]
        assert settled, "setup didn't take for any PI"
        before = {p.index: p.spiral_len for p in elig if p.index in settled}

        result = C.batch_extend_spirals(model, target_length=20.0, mode="fixed_radius")
        after = {p.index: p.spiral_len for p in elig if p.index in settled}
        assert before == after
        touched_settled = {k for k, status, _ in result.notes
                           if k in settled and status != "skipped"}
        assert not touched_settled


class TestInfeasiblePiIsRolledBackExactly:

    def test_tight_pi_left_byte_identical_when_no_progress_possible(self):
        """A PI reported "skipped" must have its OWN PIData (radius/spiral
        fields) rolled back exactly — its ELEMENTS may still shift (station/
        position) as a side effect of a DIFFERENT PI elsewhere in the same
        batch successfully lengthening (each zone is built from the
        previous zone's exit point, by design), so only the PIData fields
        this function actually owns are checked here, not the whole
        downstream element chain."""
        model = build_model(TIGHT)
        elig = eligible_pis(model)
        before_vals = {p.index: (p.radius, p.radius_auto, p.spiral_len,
                                 p.spiral_len_auto) for p in elig}
        result = C.batch_extend_spirals(model, target_length=500.0, mode="fixed_radius")
        after_vals = {p.index: (p.radius, p.radius_auto, p.spiral_len,
                                p.spiral_len_auto) for p in elig}
        skipped_indices = {k for k, status, _ in result.notes if status == "skipped"}
        assert skipped_indices, "expected the TIGHT track to have at least one skip"
        for k in skipped_indices:
            assert before_vals[k] == after_vals[k], (
                f"PI {k} was reported skipped but its PIData changed")


class TestC1ContinuityPreserved:

    def test_fixed_radius_mode(self):
        model = build_model(ROOMY)
        C.batch_extend_spirals(model, target_length=60.0, mode="fixed_radius")
        jump = _max_heading_jump_rad(model.elements)
        assert jump < math.radians(0.01)

    def test_minimize_deviation_mode(self):
        model = build_model(ROOMY)
        C.batch_extend_spirals(model, target_length=60.0, mode="minimize_deviation")
        jump = _max_heading_jump_rad(model.elements)
        assert jump < math.radians(0.01)

    def test_matches_a_full_rebuild_from_the_same_pi_values(self):
        """End state must be exactly what rebuild_from_pi_model would give
        for the same PIData — this is a performance-path feature (goes
        through rebuild_pi_span), not a different geometry path."""
        model = build_model(ROOMY)
        C.batch_extend_spirals(model, target_length=60.0, mode="fixed_radius")
        reference = build_model(ROOMY)
        by_index = {p.index: p for p in model.pis}
        for p in reference.pis:
            src = by_index.get(p.index)
            if src is not None:
                p.radius, p.spiral_len = src.radius, src.spiral_len
        C.rebuild_from_pi_model(reference)
        assert fingerprint(model) == fingerprint(reference)


class TestProgressCallback:

    def test_called_once_per_eligible_pi_numeric(self):
        model = build_model(ROOMY)
        elig = eligible_pis(model)
        calls = []
        C.batch_extend_spirals(model, target_length=60.0, mode="fixed_radius",
                               progress_cb=lambda d, t: calls.append((d, t)))
        assert len(calls) == len(elig)
        assert calls[-1] == (len(elig), len(elig))
        assert all(isinstance(d, int) and isinstance(t, int) for d, t in calls)
