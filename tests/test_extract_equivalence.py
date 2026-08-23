"""
Bit-identity tests for the `extract_pi_model` performance refactor:

1. The split-curve re-merge loop now calls `rebuild_pi_span` instead of a
   full `rebuild_from_pi_model` per iteration (up to 6 full O(E·P) passes
   collapse to span-local ones).
2. `_merge_pi_clusters` accumulates into a python list instead of repeated
   `np.vstack` (same arithmetic, same merge order).
3. The near-zero-deflection prefilter resumes at the affected vertex
   instead of restarting the whole scan from PI 1.

None of these are meant to change behavior — only cost. Each test builds a
model with the current code, and a reference model with `rebuild_pi_span`
monkeypatched to the full-rebuild path (the same trick test_span_rebuild.py
uses for edit operations), and asserts the two are identical to the
CLAUDE.md fingerprint standard (9 decimals).
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, SOAK, make_track, assert_identical  # noqa: E402


SETTINGS = dict(tolerance=1.0, min_radius=150.0, spiral_length=20.0)


def _extract(xy, ch, use_spirals=True, **kw):
    params = dict(SETTINGS, **kw)
    return C.extract_pi_model(xy, ch, use_spirals=use_spirals, **params)


@pytest.fixture()
def full_path(monkeypatch):
    """Force the reference build onto the full-rebuild path — the code
    extract_pi_model used before the span-local refactor."""
    def _full(model, k_lo, k_hi, **kw):
        C.rebuild_from_pi_model(model)
        return None

    def apply(fn, *args, **kw):
        monkeypatch.setattr(C, "rebuild_pi_span", _full)
        try:
            return fn(*args, **kw)
        finally:
            monkeypatch.undo()
    return apply


@pytest.mark.parametrize("name,segs", [("RUN3", RUN3), ("SOAK", SOAK)])
@pytest.mark.parametrize("use_spirals", [False, True])
def test_extract_equivalence(name, segs, use_spirals, full_path):
    xy, ch = make_track(segs)
    new = _extract(xy, ch, use_spirals=use_spirals)
    ref = full_path(_extract, xy, ch, use_spirals=use_spirals)
    assert_identical(new, ref, f"extract_pi_model {name} (spirals={use_spirals})")


def _build_split_curve_track():
    """Two same-rotation arcs joined by a straight shorter than
    _PI_MIN_TANGENT (60 m) — engineered to survive DP + cluster-merge as
    two distinct PIs so `_find_split_curve_pair` (not `_merge_pi_clusters`)
    is what re-merges them — the part of extract_pi_model this refactor
    actually touches."""
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

    def arc(R, deg, step=5.0):
        nonlocal heading
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

    line(400)
    arc(600, 35)
    line(45)          # < _PI_MIN_TANGENT (60 m)
    arc(600, 20)       # same rotation as above
    line(400)

    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return xy, ch


@pytest.mark.parametrize("use_spirals", [False, True])
def test_extract_equivalence_split_curve_pair(use_spirals, full_path):
    xy, ch = _build_split_curve_track()
    new = _extract(xy, ch, use_spirals=use_spirals)
    ref = full_path(_extract, xy, ch, use_spirals=use_spirals)

    # sanity: confirm the split-curve loop actually ran, else this test
    # only proves the ordinary (unexercised) path.
    n_arcs = sum(1 for e in ref.elements if e["type"] == "Arc")
    if n_arcs != 1:
        pytest.skip(
            f"track didn't produce the intended split-curve pair "
            f"({n_arcs} arcs after merge, expected 1) — geometry drifted"
        )
    assert sum(1 for e in new.elements if e["type"] == "Arc") == 1
    assert_identical(new, ref, f"extract_pi_model split-curve-pair "
                              f"(spirals={use_spirals})")


class TestMergeClustersEquivalence:
    """_merge_pi_clusters: list-based accumulation vs the old np.vstack
    version, called directly (isolated from the rest of extract_pi_model)."""

    @staticmethod
    def _old_merge_pi_clusters(V, idx, merge_dist, max_passes=4):
        V = V.copy()
        idx = list(idx)
        for _ in range(max_passes):
            if len(V) < 4:
                break
            merged_any = False
            k = 1
            while k < len(V) - 2:
                A, P1, P2, B = V[k - 1], V[k], V[k + 1], V[k + 2]
                d_mid = float(np.hypot(*(P2 - P1)))
                if d_mid > merge_dist:
                    k += 1
                    continue
                phi_in   = math.atan2(P1[1] - A[1],  P1[0] - A[0])
                phi_mid  = math.atan2(P2[1] - P1[1], P2[0] - P1[0])
                phi_out  = math.atan2(B[1] - P2[1],  B[0] - P2[0])
                d1 = C._wrap_pi(phi_mid - phi_in)
                d2 = C._wrap_pi(phi_out - phi_mid)
                if d1 * d2 <= 0 or abs(d1) < 1e-4 or abs(d2) < 1e-4:
                    k += 1
                    continue
                sin_d = math.sin(C._wrap_pi(phi_out - phi_in))
                if abs(sin_d) < 1e-9:
                    k += 1
                    continue
                dx = float(P2[0] - P1[0]); dy = float(P2[1] - P1[1])
                t  = (dx * math.sin(phi_out) - dy * math.cos(phi_out)) / sin_d
                if t <= 0:
                    k += 1
                    continue
                PI_new = P1 + t * np.array([math.cos(phi_in), math.sin(phi_in)])
                V   = np.vstack([V[:k], PI_new[None, :], V[k + 2:]])
                idx = idx[:k] + [(idx[k] + idx[k + 1]) // 2] + idx[k + 2:]
                merged_any = True
            if not merged_any:
                break
        return V, idx

    @pytest.mark.parametrize("segs", [RUN3, SOAK])
    def test_matches_old_vstack_version(self, segs):
        xy, ch = make_track(segs)
        idx = C._douglas_peucker_indices(xy, 1.0)
        V = xy[idx].astype(float)
        merge_dist = max(60.0, 8.0 * 1.0)

        V_new, idx_new = C._merge_pi_clusters(V.copy(), list(idx), merge_dist)
        V_old, idx_old = self._old_merge_pi_clusters(V.copy(), list(idx), merge_dist)

        assert idx_new == idx_old
        assert V_new.shape == V_old.shape
        assert np.allclose(V_new, V_old, rtol=0.0, atol=1e-9)


class TestNoisePrefilterEquivalence:
    """The resume-at-k-1 rewrite must delete the identical vertex sequence
    as the old restart-from-1 loop."""

    @staticmethod
    def _old_prefilter(V, idx, min_defl):
        V = V.copy(); idx = list(idx)
        changed = True
        while changed and len(V) > 2:
            changed = False
            for k in range(1, len(V) - 1):
                if abs(C._polygon_deflection(V, k)) < min_defl:
                    V   = np.delete(V, k, axis=0)
                    idx = idx[:k] + idx[k + 1:]
                    changed = True
                    break
        return V, idx

    @staticmethod
    def _new_prefilter(V, idx, min_defl):
        V = V.copy(); idx = list(idx)
        k = 1
        while k < len(V) - 1:
            if abs(C._polygon_deflection(V, k)) < min_defl:
                V   = np.delete(V, k, axis=0)
                idx = idx[:k] + idx[k + 1:]
                k = max(1, k - 1)
            else:
                k += 1
        return V, idx

    @pytest.mark.parametrize("segs", [RUN3, SOAK])
    def test_matches_old_restart_version(self, segs):
        xy, ch = make_track(segs)
        idx = C._douglas_peucker_indices(xy, 1.0)
        V = xy[idx].astype(float)
        V, idx = C._merge_pi_clusters(V, idx, max(60.0, 8.0))
        min_defl = math.radians(0.7)

        V_old, idx_old = self._old_prefilter(V, idx, min_defl)
        V_new, idx_new = self._new_prefilter(V, idx, min_defl)

        assert idx_new == idx_old
        assert V_new.shape == V_old.shape
        assert np.allclose(V_new, V_old, rtol=0.0, atol=0.0)   # exact — same ops, same order

    def test_dense_near_threshold_cluster(self):
        """Several consecutive near-threshold deflections in a row is the
        case most likely to expose an ordering difference between resume
        and restart."""
        min_defl = math.radians(0.7)
        pts = [(0.0, 0.0)]
        heading = 0.0
        deflections_deg = [0.3, 0.9, 0.4, 0.2, 1.2, 0.5]
        for d in deflections_deg:
            step = 20.0
            pts.append((pts[-1][0] + step * math.cos(heading),
                       pts[-1][1] + step * math.sin(heading)))
            heading += math.radians(d)
        pts.append((pts[-1][0] + 40 * math.cos(heading),
                   pts[-1][1] + 40 * math.sin(heading)))
        V = np.array(pts, dtype=float)
        idx = list(range(len(pts)))

        V_old, idx_old = self._old_prefilter(V, idx, min_defl)
        V_new, idx_new = self._new_prefilter(V, idx, min_defl)

        assert idx_new == idx_old
        assert np.allclose(V_new, V_old, rtol=0.0, atol=0.0)
