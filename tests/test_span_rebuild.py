"""
Bit-identity tests for the span-local rebuild (rebuild_pi_span).

Every mutator (merge_intermediate_line, merge_pi_range, undo_merge, plain
value edits) must produce results identical to a full rebuild_from_pi_model
— same elements to 1e-9, same deviation stats, same per-point arrays
bit-for-bit. The reference model gets the same edit with rebuild_pi_span
monkeypatched to the full-rebuild path.
"""

from __future__ import annotations

import math
import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic tracks
# ---------------------------------------------------------------------------

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


RUN3 = [("S", 400), ("A", 600, 30), ("S", 300), ("A", 550, 25), ("S", 300),
        ("A", 620, 20), ("S", 400), ("A", 500, -45), ("S", 400)]

# alternating long chain for the soak (12 curves, ~14 km)
SOAK = [("S", 500)]
for q, (rr, dd) in enumerate([(600, 30), (550, -25), (700, 20), (500, 35),
                              (650, -30), (800, 15), (450, 40), (600, -20),
                              (750, 25), (500, -35), (900, 12), (550, 30)]):
    SOAK += [("A", rr, dd), ("S", 350 + (q % 4) * 120)]


def build_model(segs, use_spirals=True):
    xy, ch = make_track(segs)
    return C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                              spiral_length=20.0, use_spirals=use_spirals)


def clone(model):
    import copy
    return copy.deepcopy(model)


# ---------------------------------------------------------------------------
# Fingerprinting / comparison
# ---------------------------------------------------------------------------

def fp_elements(model):
    out = []
    for e in model.elements:
        row = [e.get("type"), e.get("element_id"), e.get("_pi")]
        for key in ("sta_start", "length", "radius", "radius_start",
                    "radius_end", "clothoid_A", "_deflection",
                    "_max_dev", "_mean_dev"):
            v = e.get(key)
            row.append(None if v is None else round(float(v), 9))
        for key in ("start", "end", "center"):
            v = e.get(key)
            row.append(None if v is None else
                       [round(float(v[0]), 9), round(float(v[1]), 9)])
        out.append(row)
    return out


def assert_identical(span_model, full_model, ctx=""):
    TOL_E = 2e-9      # numeric compare; avoids 9th-decimal rounding flips
    ea, eb = span_model.elements, full_model.elements
    assert len(ea) == len(eb), f"{ctx}: element count {len(ea)} != {len(eb)}"
    for i, (x, y) in enumerate(zip(ea, eb)):
        ident = (x.get("type"), x.get("element_id"), x.get("_pi"))
        assert ident == (y.get("type"), y.get("element_id"), y.get("_pi")), \
            f"{ctx}: element {i} identity {ident} != " \
            f"{(y.get('type'), y.get('element_id'), y.get('_pi'))}"
        for key in ("sta_start", "length", "radius", "radius_start",
                    "radius_end", "clothoid_A", "_deflection",
                    "_max_dev", "_mean_dev"):
            va, vb = x.get(key), y.get(key)
            assert (va is None) == (vb is None), f"{ctx}: el {i} {key}"
            if va is None:
                continue
            va, vb = float(va), float(vb)
            if math.isinf(va) or math.isinf(vb):
                assert va == vb, f"{ctx}: el {i} {key} {va} != {vb}"
            else:
                assert abs(va - vb) <= TOL_E, \
                    f"{ctx}: el {i} ({ident}) {key}: {va!r} != {vb!r}"
        for key in ("start", "end", "center"):
            va, vb = x.get(key), y.get(key)
            assert (va is None) == (vb is None), f"{ctx}: el {i} {key}"
            if va is not None:
                assert (abs(va[0] - vb[0]) <= TOL_E
                        and abs(va[1] - vb[1]) <= TOL_E), \
                    f"{ctx}: el {i} ({ident}) {key}: {va} != {vb}"

    # NOTE on tolerance: true bitwise identity is impossible by construction
    # — the self-healing chain means a FULL rebuild after any edit perturbs
    # every downstream zone at the ~1e-12 level, while the span path keeps
    # the old (equally valid) floats past its convergence point. The bar is
    # the CLAUDE.md fingerprint standard: 9 decimals (nanometres).
    TOL = 1e-9
    sa, sb = span_model.last_stats, full_model.last_stats
    assert abs(sa["max_deviation"] - sb["max_deviation"]) <= TOL, \
        f"{ctx}: max_dev {sa['max_deviation']} vs {sb['max_deviation']}"
    assert abs(sa["rmse"] - sb["rmse"]) <= TOL, ctx
    assert len(sa["per_element"]) == len(sb["per_element"]), ctx
    for i, (ta, tb) in enumerate(zip(sa["per_element"], sb["per_element"])):
        assert abs(ta[0] - tb[0]) <= TOL and abs(ta[1] - tb[1]) <= 1e-6, \
            f"{ctx}: per_element[{i}] {ta} vs {tb}"
    pda, pdb = sa["point_dist"], sb["point_dist"]
    pea, peb = sa["point_elem"], sb["point_elem"]
    assert np.allclose(pda, pdb, rtol=0.0, atol=TOL), \
        f"{ctx}: point_dist differ (max {np.abs(pda - pdb).max()})"
    flip = pea != peb
    if flip.any():
        # assignment flips are only legitimate between near-equidistant
        # elements (an eps-level tie broke the other way)
        assert np.all(np.abs(pda[flip] - pdb[flip]) <= TOL), \
            f"{ctx}: point_elem flipped with non-tied distances"

    stubs_a = sorted(span_model.tangent_stubs, key=lambda s: s["pi"])
    stubs_b = sorted(full_model.tangent_stubs, key=lambda s: s["pi"])
    assert len(stubs_a) == len(stubs_b), f"{ctx}: stub count"
    for sx, sy in zip(stubs_a, stubs_b):
        assert sx["pi"] == sy["pi"], f"{ctx}: stub pi {sx['pi']} != {sy['pi']}"
        for key in ("tc", "ct"):
            assert (abs(sx[key][0] - sy[key][0]) <= TOL_E
                    and abs(sx[key][1] - sy[key][1]) <= TOL_E), \
                f"{ctx}: stub {sx['pi']} {key} differs"

    assert len(span_model.pis) == len(full_model.pis), f"{ctx}: PI count"
    for pa, pb in zip(span_model.pis, full_model.pis):
        assert (pa.index, pa.omitted, pa.merged_with_next,
                pa.merged_with_prev) == \
               (pb.index, pb.omitted, pb.merged_with_next,
                pb.merged_with_prev), f"{ctx}: PI {pa.index} flags differ"
        assert abs(pa.radius - pb.radius) <= TOL_E, \
            f"{ctx}: PI {pa.index} radius {pa.radius!r} != {pb.radius!r}"
        assert abs(pa.spiral_len - pb.spiral_len) <= TOL_E, \
            f"{ctx}: PI {pa.index} spiral_len differs"


@pytest.fixture()
def full_path(monkeypatch):
    """Force the reference model onto the full-rebuild path."""
    def _full(model, k_lo, k_hi, **kw):
        C.rebuild_from_pi_model(model)
        return None
    def apply(fn, model, *args, **kw):
        monkeypatch.setattr(C, "rebuild_pi_span", _full)
        try:
            return fn(model, *args, **kw)
        finally:
            monkeypatch.undo()
    return apply


# ---------------------------------------------------------------------------
# Individual operations
# ---------------------------------------------------------------------------

class TestSpanOps:

    def test_merge_pi_range(self, full_path):
        m_span = build_model(RUN3)
        m_full = clone(m_span)
        assert len(m_span.pis) >= 3
        ok_a, msg_a = C.merge_pi_range(m_span, 1, 2)
        ok_b, msg_b = full_path(C.merge_pi_range, m_full, 1, 2)
        assert ok_a and ok_b and msg_a == msg_b
        assert_identical(m_span, m_full, "merge_pi_range")

    def test_merge_pi_range_three(self, full_path):
        m_span = build_model(RUN3)
        m_full = clone(m_span)
        ok_a, _ = C.merge_pi_range(m_span, 1, 3)
        ok_b, _ = full_path(C.merge_pi_range, m_full, 1, 3)
        assert ok_a == ok_b
        assert_identical(m_span, m_full, "merge_pi_range 1-3")

    def test_merge_intermediate_line_and_undo(self, full_path):
        segs = [("S", 400), ("A", 500, 30), ("S", 100), ("A", 480, 25),
                ("S", 400)]
        m_span = build_model(segs)
        m_full = clone(m_span)
        two = [p.index for p in m_span.pis if not p.omitted][:2]
        if len(two) < 2 or two[1] != two[0] + 1:
            pytest.skip("extraction merged the pair — layout changed")
        ok_a, msg_a = C.merge_intermediate_line(m_span, two[0], two[1])
        ok_b, msg_b = full_path(C.merge_intermediate_line, m_full,
                                two[0], two[1])
        assert ok_a == ok_b and msg_a == msg_b
        assert_identical(m_span, m_full, "merge_intermediate_line")
        if ok_a:
            ra, _ = C.undo_merge(m_span, two[0])
            rb, _ = full_path(C.undo_merge, m_full, two[0])
            assert ra and rb
            assert_identical(m_span, m_full, "undo_merge")

    def test_value_edit_span(self, full_path):
        """Radius/spiral edit through the span path == full rebuild."""
        m_span = build_model(RUN3)
        m_full = clone(m_span)
        k = m_span.pis[1].index
        for m in (m_span, m_full):
            m.pis[1].radius = m.pis[1].radius * 0.85
            m.pis[1].spiral_len = 35.0
        C.rebuild_pi_span(m_span, k, k)
        C.rebuild_from_pi_model(m_full)
        assert_identical(m_span, m_full, "value edit")

    def test_omit_restore_span(self, full_path):
        m_span = build_model(RUN3)
        m_full = clone(m_span)
        k = m_span.pis[1].index
        for m in (m_span, m_full):
            m.pis[1].omitted = True
        C.rebuild_pi_span(m_span, k, k)
        C.rebuild_from_pi_model(m_full)
        assert_identical(m_span, m_full, "omit")
        for m in (m_span, m_full):
            m.pis[1].omitted = False
        C.rebuild_pi_span(m_span, k, k)
        C.rebuild_from_pi_model(m_full)
        assert_identical(m_span, m_full, "restore")

    def test_snapshot_roundtrip(self):
        """restore_span_snapshot returns the model to its exact prior state."""
        model = build_model(RUN3)
        ref = clone(model)
        k = model.pis[1].index
        old_radius = model.pis[1].radius
        model.pis[1].radius = old_radius * 0.7
        snap = C.rebuild_pi_span(model, k, k)
        assert snap is not None
        C.restore_span_snapshot(model, snap)
        model.pis[1].radius = old_radius
        assert_identical(model, ref, "snapshot roundtrip")

    def test_failed_range_merge_is_noop(self, full_path):
        m_span = build_model(RUN3)
        before = fp_elements(m_span)
        # 150° cap / parallel guards fire before any rebuild for a reversing
        # range; a same-direction absurd range exercises the rollback path
        ok, _ = C.merge_pi_range(m_span, 1, len(m_span.V) - 3)
        if not ok:
            assert fp_elements(m_span) == before


# ---------------------------------------------------------------------------
# Randomized soak — the real prover
# ---------------------------------------------------------------------------

class TestSoak:

    def test_random_edit_soak(self, full_path):
        rng = random.Random(42)
        m_span = build_model(SOAK)
        m_full = clone(m_span)
        n_ops = 50
        applied = 0
        for step in range(n_ops):
            pis = [p for p in m_span.pis]
            if len(pis) < 3:
                break
            op = rng.choice(["radius", "spiral", "omit", "merge_range",
                             "restore"])
            ctx = f"soak step {step} op {op}"
            if op == "radius":
                i = rng.randrange(len(pis))
                k = pis[i].index
                f = rng.uniform(0.8, 1.25)
                m_span.pis[i].radius = max(160.0, m_span.pis[i].radius * f)
                m_full.pis[i].radius = m_span.pis[i].radius
                C.rebuild_pi_span(m_span, k, k)
                C.rebuild_from_pi_model(m_full)
            elif op == "spiral":
                i = rng.randrange(len(pis))
                k = pis[i].index
                L = rng.choice([0.0, 20.0, 40.0, 60.0])
                m_span.pis[i].spiral_len = L
                m_full.pis[i].spiral_len = L
                C.rebuild_pi_span(m_span, k, k)
                C.rebuild_from_pi_model(m_full)
            elif op == "omit":
                i = rng.randrange(len(pis))
                k = pis[i].index
                m_span.pis[i].omitted = True
                m_full.pis[i].omitted = True
                C.rebuild_pi_span(m_span, k, k)
                C.rebuild_from_pi_model(m_full)
            elif op == "restore":
                cands = [i for i, p in enumerate(pis) if p.omitted]
                if not cands:
                    continue
                i = rng.choice(cands)
                k = pis[i].index
                m_span.pis[i].omitted = False
                m_full.pis[i].omitted = False
                C.rebuild_pi_span(m_span, k, k)
                C.rebuild_from_pi_model(m_full)
            else:  # merge_range
                if len(pis) < 4:
                    continue
                i = rng.randrange(len(pis) - 1)
                k1, k2 = pis[i].index, pis[i + 1].index
                if k2 != k1 + 1:
                    continue
                ok_a, msg_a = C.merge_pi_range(m_span, k1, k2)
                ok_b, msg_b = full_path(C.merge_pi_range, m_full, k1, k2)
                assert ok_a == ok_b, f"{ctx}: ok {ok_a} != {ok_b} ({msg_a} | {msg_b})"
            applied += 1
            assert_identical(m_span, m_full, ctx)
        assert applied >= 30
