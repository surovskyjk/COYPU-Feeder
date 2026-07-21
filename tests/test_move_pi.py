"""
move_pi (src/geometry/candidates.py) — the model-level operation behind
Edit mode's PI-drag interaction (Part C). Dragging never changes the vertex
count; it moves V[k] and re-snaps idx[k], clamped to stay strictly between
the neighbouring PIs' OSM anchors so the deviation-window code's monotonic
idx assumption is never violated.
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


def _make_model(defl_deg=60.0, R=600.0, straight=500.0, step=4.0):
    pts = [(0.0, 0.0)]
    heading = 0.0
    n = max(2, int(straight / step))
    for i in range(1, n + 1):
        d = straight * i / n
        pts.append((d * math.cos(heading), d * math.sin(heading)))
    ang = math.radians(defl_deg)
    cx = pts[-1][0] + R * math.cos(heading + math.pi / 2)
    cy = pts[-1][1] + R * math.sin(heading + math.pi / 2)
    a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
    n2 = max(2, int(abs(ang) * R / step))
    for i in range(1, n2 + 1):
        a = a0 + ang * i / n2
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    heading += ang
    start = pts[-1]
    for i in range(1, n + 1):
        d = straight * i / n
        pts.append((start[0] + d * math.cos(heading),
                    start[1] + d * math.sin(heading)))
    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=100.0,
                              spiral_length=20.0, use_spirals=True)


class TestMovePI:

    def test_moves_vertex_and_rebuilds_c1(self):
        model = _make_model()
        pis = [p.index for p in model.pis if not p.omitted]
        k = pis[len(pis) // 2]
        old_xy = model.V[k].copy()
        new_xy = old_xy + np.array([15.0, -8.0])

        ok, msg = C.move_pi(model, k, new_xy)
        assert ok, msg
        assert np.allclose(model.V[k], new_xy)

        els = model.elements
        assert len(els) > 0
        max_gap = max(
            math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
            for a, b in zip(els[:-1], els[1:]))
        assert max_gap < 1e-6, f"C0 gap {max_gap} m"
        jj = _max_heading_jump_rad(els)
        assert math.degrees(jj) < 1e-3, f"heading jump {math.degrees(jj)} deg"

    def test_neighbours_untouched(self):
        model = _make_model()
        pis = [p.index for p in model.pis if not p.omitted]
        k = pis[len(pis) // 2]
        before_prev = model.V[k - 1].copy()
        before_next = model.V[k + 1].copy()
        ok, msg = C.move_pi(model, k, model.V[k] + np.array([10.0, 5.0]))
        assert ok, msg
        assert np.allclose(model.V[k - 1], before_prev)
        assert np.allclose(model.V[k + 1], before_next)

    def test_idx_clamped_between_neighbours(self):
        model = _make_model()
        pis = [p.index for p in model.pis if not p.omitted]
        k = pis[len(pis) // 2]
        # Drag far past the NEXT PI's own OSM anchor — idx[k] must still
        # end up strictly inside (idx[k-1], idx[k+1]), never crossing it.
        far_xy = model.V[k + 1] + (model.V[k + 1] - model.V[k]) * 5.0
        ok, msg = C.move_pi(model, k, far_xy)
        assert ok, msg
        assert model.idx[k - 1] < model.idx[k] < model.idx[k + 1]

    def test_out_of_bounds_index_rejected(self):
        model = _make_model()
        m = len(model.V)
        ok, msg = C.move_pi(model, 0, model.V[1])          # endpoint, not a PI
        assert not ok
        ok, msg = C.move_pi(model, m - 1, model.V[1])       # endpoint
        assert not ok

    def test_move_to_collinear_drops_curve_not_crash(self):
        """Dragging a PI onto the straight line through its neighbours makes
        it collinear — the curve should simply vanish (like any tiny-
        deflection PI), not error out."""
        model = _make_model()
        pis = [p.index for p in model.pis if not p.omitted]
        k = pis[0]
        A, B = model.V[k - 1], model.V[k + 1]
        midpoint = (A + B) / 2.0
        ok, msg = C.move_pi(model, k, midpoint)
        assert ok, msg   # must not raise/fail — a vanished curve is valid

    def test_span_rebuild_matches_full_rebuild(self):
        """move_pi's span-local rebuild (rebuild_pi_span) must reproduce
        exactly what a full rebuild_from_pi_model would, per CLAUDE.md's
        behaviour-preservation requirement — fingerprint both paths on an
        identical edit."""
        m1 = _make_model(defl_deg=60.0, straight=500.0)
        m2 = _make_model(defl_deg=60.0, straight=500.0)
        pis = [p.index for p in m1.pis if not p.omitted]
        k = pis[0]
        new_xy = m1.V[k] + np.array([12.0, -6.0])

        ok, msg = C.move_pi(m1, k, new_xy)   # span-local path
        assert ok, msg

        m2.V[k] = new_xy
        j = C.nearest_xy_ref_index(m2, new_xy)
        lo, hi = m2.idx[k - 1], m2.idx[k + 1]
        m2.idx[k] = max(lo + 1, min(hi - 1, j)) if hi - lo > 1 else lo
        C.rebuild_from_pi_model(m2)          # full-rebuild reference

        assert len(m1.elements) == len(m2.elements)
        for e1, e2 in zip(m1.elements, m2.elements):
            assert e1["type"] == e2["type"]
            assert round(e1["length"], 9) == round(e2["length"], 9)
            assert all(abs(a - b) < 1e-9 for a, b in
                      zip(e1["start"], e2["start"]))
            assert all(abs(a - b) < 1e-9 for a, b in
                      zip(e1["end"], e2["end"]))
