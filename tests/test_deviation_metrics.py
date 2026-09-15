"""mean_deviation propagation across the deviation-stats pipeline (added for
the persistent metrics chip — Δmean must be correct and present everywhere
max_deviation/rmse already were), and the exact-C1 route-statistics fix:
`_heading_at_start`/`_heading_at_end` used to approximate a clothoid's
chord-to-tangent angle as α ≈ θ_s/3, which reads as a spurious "C1 mismatch"
in the metrics chip after editing the tangent polygon even though the
PI-model rebuild is exactly C1 by construction — see `_clothoid_chord_angle`
and `_c1_junction_report` in `geometry.candidates`."""

from __future__ import annotations

import math
import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, SOAK, build_model, fp_elements  # noqa: E402


def test_mean_deviation_matches_point_dist_mean():
    xy = np.array([[0.0, 0.0], [1.0, 0.5], [2.0, -0.5], [3.0, 0.0]])
    elements = [{
        "type": "Line", "start": [0.0, 0.0], "end": [3.0, 0.0],
        "length": 3.0,
    }]
    stats = C.compute_deviation_stats(elements, xy)
    assert "mean_deviation" in stats
    assert stats["mean_deviation"] == pytest.approx(stats["point_dist"].mean())
    # sanity: strictly between 0 and max for this asymmetric offset pattern
    assert 0.0 < stats["mean_deviation"] < stats["max_deviation"]


def test_mean_deviation_empty_inputs():
    assert C.compute_deviation_stats([], np.zeros((0, 2)))["mean_deviation"] == 0.0
    assert C.compute_deviation_stats(
        [{"type": "Line", "start": [0, 0], "end": [1, 0], "length": 1.0}],
        np.zeros((0, 2)))["mean_deviation"] == 0.0


def test_metrics_from_stats_carries_mean():
    stats = {"max_deviation": 1.0, "rmse": 0.5, "mean_deviation": 0.3}
    m = C.metrics_from_stats(stats, [])
    assert m["mean_deviation"] == 0.3


def test_evaluate_candidate_has_mean_deviation():
    xy, ch = build_model(RUN3).xy_ref, build_model(RUN3).chainages_ref
    elements = build_model(RUN3).elements
    m = C.evaluate_candidate(elements, xy, ch)
    assert "mean_deviation" in m
    assert 0.0 <= m["mean_deviation"] <= m["max_deviation"]


def test_candidate_alignment_accepts_mean_deviation_kwarg():
    """CandidateAlignment must accept every key evaluate_candidate() returns
    via **metrics — this used to crash generation once mean_deviation was
    added to the stats dict but not to the dataclass."""
    c = C.CandidateAlignment(
        "level3", "Level 3", [],
        max_deviation=1.0, rmse=0.5, mean_deviation=0.3,
        n_elements=0, max_heading_jump_deg=0.0,
    )
    assert c.mean_deviation == 0.3


def test_span_snapshot_preserves_mean_deviation():
    """A failed-merge rollback (_span_snapshot/restore_span_snapshot) must
    not silently drop mean_deviation back to 0."""
    model = build_model(RUN3)
    assert model.last_stats.get("mean_deviation", 0.0) > 0.0
    snap = C._span_snapshot(model)
    assert snap["stats"]["mean_deviation"] == pytest.approx(
        model.last_stats["mean_deviation"])


# ---------------------------------------------------------------------------
# Exact-C1 route statistics (the α ≈ θ_s/3 approximation fix)
# ---------------------------------------------------------------------------

def _max_exact_jump_rad(elements: list[dict]) -> float:
    """Independent re-derivation of the exact junction mismatch, straight
    from scipy's Fresnel integrals — deliberately NOT reusing
    `_clothoid_chord_angle`/`_heading_at_start`/`_heading_at_end`, so this
    test can't just be checking the implementation against itself."""
    from scipy.special import fresnel

    def heading(el, at_end):
        et = el.get("type")
        if et == "Line":
            return float(el["direction_rad"])
        if et == "Arc":
            cx, cy = el["center"]
            px, py = el["end"] if at_end else el["start"]
            sign = 1.0 if el.get("rot") == "ccw" else -1.0
            return math.atan2(py - cy, px - cx) + sign * math.pi / 2.0
        if et == "Spiral":
            sx, sy = el["start"]
            ex, ey = el["end"]
            chord = math.atan2(ey - sy, ex - sx)
            L = float(el["length"])
            A = float(el["clothoid_A"])
            R = A * A / L
            theta_s = L / (2.0 * R)
            sign = 1.0 if el.get("rot") == "ccw" else -1.0
            t = L / (A * math.sqrt(math.pi))
            s, c = fresnel(t)
            alpha = math.atan2(s, c)
            entry = math.isinf(float(el["radius_start"]))
            if at_end:
                return chord + sign * ((theta_s - alpha) if entry else alpha)
            return chord - sign * (alpha if entry else (theta_s - alpha))
        return None

    worst = 0.0
    for a, b in zip(elements[:-1], elements[1:]):
        ha, hb = heading(a, True), heading(b, False)
        if ha is None or hb is None:
            continue
        diff = abs((hb - ha + math.pi) % (2.0 * math.pi) - math.pi)
        worst = max(worst, diff)
    return worst


def _random_edit_soak(model, rng, n_ops=40):
    """Randomised PI drags / radius edits / spiral-length edits / merges —
    same style as `TestSoak.test_random_edit_soak` in test_span_rebuild.py,
    but driving `rebuild_from_pi_model` after every op (this test cares
    about the resulting statistic, not the span/full equivalence proof)."""
    for _ in range(n_ops):
        pis = [p for p in model.pis if not p.omitted]
        if len(pis) < 3:
            break
        op = rng.choice(["drag", "radius", "spiral", "merge"])
        if op == "drag":
            pd = rng.choice(pis)
            v = model.V[pd.index]
            C.move_pi(model, pd.index,
                      (float(v[0]) + rng.uniform(-80, 80),
                       float(v[1]) + rng.uniform(-80, 80)))
        elif op == "radius":
            pd = rng.choice(pis)
            pd.radius = max(150.0, pd.radius * rng.uniform(0.6, 1.6))
            C.rebuild_from_pi_model(model)
        elif op == "spiral":
            pd = rng.choice(pis)
            pd.spiral_len = rng.choice([0.0, 15.0, 30.0, 60.0, -1.0])
            C.rebuild_from_pi_model(model)
        else:  # merge
            if len(pis) < 4:
                continue
            i = rng.randrange(len(pis) - 1)
            k1, k2 = pis[i].index, pis[i + 1].index
            if k2 == k1 + 1:
                C.merge_intermediate_line(model, k1, k2)


def test_exact_c1_after_random_edit_soak():
    """The reported C1 number must track the EXACT junction mismatch, not
    the old θ_s/3 approximation — which used to wander up to ~0.06° across a
    session of PI drags even though the geometry itself never left C1."""
    rng = random.Random(7)
    model = build_model(SOAK)
    _random_edit_soak(model, rng, n_ops=40)
    exact = _max_exact_jump_rad(model.elements)
    assert exact <= 1e-9, f"exact junction mismatch {exact!r} rad"
    reported = C._max_heading_jump_rad(model.elements)
    assert reported <= 1e-9, f"reported (module) mismatch {reported!r} rad"
    assert reported == pytest.approx(exact, abs=1e-12)
    assert C._c1_junction_report(model.elements) == []


def test_exact_c1_survives_tight_radius_and_long_spiral():
    """A regime where the old α ≈ θ_s/3 approximation had its worst error
    (small R, long L → large θ_s) must still read exactly C1."""
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    pd = next(p for p in model.pis if p.index == ks[1])
    pd.radius = 210.0
    pd.spiral_len = 200.0
    C.rebuild_from_pi_model(model)
    assert any(e.get("_pi") == ks[1] and e["type"] == "Spiral"
              for e in model.elements), "expected the spiral to survive"
    exact = _max_exact_jump_rad(model.elements)
    assert exact <= 1e-9, f"exact junction mismatch {exact!r} rad"
    assert C._max_heading_jump_rad(model.elements) <= 1e-9


def test_c1_junction_report_empty_on_healthy_model():
    model = build_model(RUN3)
    assert C._c1_junction_report(model.elements) == []


def test_c1_junction_report_flags_synthetic_break():
    """A deliberately broken junction (independent of any solver path) IS
    reported — proves the report isn't just always empty."""
    elements = [
        {"type": "Line", "direction_rad": 0.0, "start": [0.0, 0.0],
         "end": [10.0, 0.0], "length": 10.0, "element_id": "L1"},
        {"type": "Line", "direction_rad": math.radians(5.0),
         "start": [10.0, 0.0], "end": [20.0, 0.0], "length": 10.0,
         "element_id": "L2"},
    ]
    report = C._c1_junction_report(elements, threshold_deg=0.05)
    assert len(report) == 1
    assert report[0]["jump_deg"] == pytest.approx(5.0, abs=1e-6)
    assert report[0]["a_id"] == "L1" and report[0]["b_id"] == "L2"


def test_metrics_from_stats_reports_near_zero_c1_after_edit():
    """End-to-end: metrics_from_stats (what the GUI actually reads) must
    report ~0.000° after a realistic edit, and carry an empty c1_junctions
    list — this is the number the user was seeing wander before the fix."""
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    v = model.V[ks[1]]
    C.move_pi(model, ks[1], (float(v[0]) + 40.0, float(v[1]) - 25.0))
    metrics = C.metrics_from_stats(model.last_stats, model.elements)
    assert metrics["max_heading_jump_deg"] < 1e-6
    assert metrics["c1_junctions"] == []


def test_c1_fix_does_not_alter_element_geometry():
    """Fingerprint proof: computing the (now-exact) C1 metrics is a pure
    read — it must not perturb element geometry at all."""
    model = build_model(RUN3)
    before = fp_elements(model)
    C.metrics_from_stats(model.last_stats, model.elements)
    C._max_heading_jump_rad(model.elements)
    C._c1_junction_report(model.elements)
    after = fp_elements(model)
    assert before == after
