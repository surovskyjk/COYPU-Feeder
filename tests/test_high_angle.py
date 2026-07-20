"""
High-angle curve merging: merge_pi_range dispatches to a chained construction
(multiple PIs sharing one fitted circle) when the total deflection of the
selected range exceeds _CHAIN_DISPATCH_DEG (90 deg) — a single PI cannot
represent |delta| >= 180 deg (deflections are wrapped to (-pi, pi]).

Track generator: ONE continuous physical arc (Douglas-Peucker naturally
fragments it into many small same-rotation PIs — that's the realistic
scenario this feature targets, not several genuinely separate curves
joined by straights).
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


def make_single_arc_track(total_deg, R=600.0, straight=500.0, step=4.0):
    pts = [(0.0, 0.0)]
    heading = 0.0
    for length in (straight,):
        start = pts[-1]
        n = max(2, int(length / step))
        for i in range(1, n + 1):
            d = length * i / n
            pts.append((start[0] + d * math.cos(heading),
                        start[1] + d * math.sin(heading)))
    ang = math.radians(total_deg)
    sgn = 1.0 if ang >= 0 else -1.0
    cx = pts[-1][0] + R * math.cos(heading + sgn * math.pi / 2)
    cy = pts[-1][1] + R * math.sin(heading + sgn * math.pi / 2)
    a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
    n = max(2, int(abs(ang) * R / step))
    for i in range(1, n + 1):
        a = a0 + ang * i / n
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    heading += ang
    start = pts[-1]
    n = max(2, int(straight / step))
    for i in range(1, n + 1):
        d = straight * i / n
        pts.append((start[0] + d * math.cos(heading),
                    start[1] + d * math.sin(heading)))
    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return xy, ch


def _build_and_merge(total_deg, R=600.0):
    xy, ch = make_single_arc_track(total_deg, R=R)
    model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                               spiral_length=20.0, use_spirals=True)
    pis = [p.index for p in model.pis if not p.omitted]
    assert len(pis) >= 2, "test track should DP-fragment into several PIs"
    k_from, k_to = pis[0], pis[-1]
    ok, msg = C.merge_pi_range(model, k_from, k_to)
    return model, xy, k_from, ok, msg


class TestChainConstruction:

    @pytest.mark.parametrize("total_deg", [120.0, 200.0, 270.0])
    def test_chain_merge_geometry(self, total_deg):
        model, xy, k_from, ok, msg = _build_and_merge(total_deg)
        assert ok, msg

        by_idx = {p.index: p for p in model.pis}
        grp = []
        idx = k_from
        while idx in by_idx:
            grp.append(by_idx[idx])
            if not by_idx[idx].chain_next:
                break
            idx += 1
        expected_n = math.ceil(total_deg / C._CHAIN_SUBTEND_DEG)
        assert len(grp) == max(2, expected_n), \
            f"chain length {len(grp)} != expected {max(2, expected_n)}"

        # shared radius (within the small T_max clamp tolerance)
        radii = [p.radius for p in grp]
        assert max(radii) - min(radii) < 5.0, f"radii not shared: {radii}"
        assert abs(radii[0] - 600.0) < 5.0, f"radius {radii[0]} far from true 600"

        # sum of member deflections reproduces the requested total
        total_built = sum(p.deflection for p in grp)
        assert abs(math.degrees(total_built) - total_deg) < 0.5

        els = model.elements
        # C0: every element's end matches the next one's start exactly
        max_gap = max(
            math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
            for a, b in zip(els[:-1], els[1:]))
        assert max_gap < 1e-6, f"C0 gap {max_gap} m"

        # C1: no heading discontinuity anywhere in the chain
        jj = _max_heading_jump_rad(els)
        assert math.degrees(jj) < 1e-3, f"heading jump {math.degrees(jj)} deg"

        # endpoints anchored to the original OSM polyline exactly
        assert math.hypot(els[0]["start"][0] - xy[0][0],
                          els[0]["start"][1] - xy[0][1]) < 1e-6
        assert math.hypot(els[-1]["end"][0] - xy[-1][0],
                          els[-1]["end"][1] - xy[-1][1]) < 1e-6

        # the whole chain tracks the original OSM polyline closely — this is
        # the real proof the fitted circle/construction is correct, not just
        # internally consistent
        assert model.last_stats["max_deviation"] < 1.0, \
            f"max_dev {model.last_stats['max_deviation']} too large for a clean arc"

    def test_dispatch_threshold(self):
        """<= 90 deg total uses the ordinary single-PI path (no chain_next)."""
        model, xy, k_from, ok, msg = _build_and_merge(60.0)
        assert ok, msg
        pid = next(p for p in model.pis if p.index == k_from)
        assert not pid.chain_next
        arcs = [e for e in model.elements if e.get("type") == "Arc"
               and e.get("_pi") == k_from]
        assert len(arcs) == 1

    def test_absurd_total_rejected(self):
        xy, ch = make_single_arc_track(20.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k = pis[0]
        ok2, msg2 = C._merge_pi_range_chained(
            model, k, k, math.radians(355.0))
        assert not ok2
        assert "plausible" in msg2.lower()


class TestExportAndRoundTrip:

    def test_landxml_reflex_arc(self, tmp_path):
        from landxml.builder import build_landxml, write_landxml
        model, xy, k_from, ok, msg = _build_and_merge(270.0)
        assert ok, msg
        aln = {"name": "test", "elements": model.elements, "sta_start": 0.0}
        root = build_landxml([aln], output_epsg=5514)
        out = tmp_path / "reflex.xml"
        write_landxml(root, str(out))
        text = out.read_text(encoding="utf-8")
        assert text.count("<Curve") >= 3     # the chain's arcs all exported
        assert 'crvType="arc"' in text
        # every exported curve carries Start/Center/End — angle-agnostic,
        # so a >180 deg member is written the same way as any other arc
        assert text.count("<Center") >= 3

    def test_project_roundtrip_chain_next(self, tmp_path):
        import project_io
        model, xy, k_from, ok, msg = _build_and_merge(200.0)
        assert ok, msg
        chain_before = [(p.index, p.chain_next, round(p.radius, 6))
                        for p in model.pis]

        state = {
            "project_name": "chain-test", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "chain.coypu")
        project_io.save_project(path, state)
        assert '<PI ' in open(path, encoding="utf-8").read()
        assert 'chainNext="true"' in open(path, encoding="utf-8").read()

        loaded = project_io.load_project(path)
        m2 = loaded["pi_model"]
        chain_after = [(p.index, p.chain_next, round(p.radius, 6))
                       for p in m2.pis]
        assert chain_after == chain_before

    def test_old_project_file_without_chain_next_loads(self, tmp_path):
        """A v1.0 .coypu file (no chainNext attribute) must still load —
        the default (False) keeps every PI an ordinary single curve."""
        import project_io
        model, xy, k_from, ok, msg = _build_and_merge(60.0)  # no chain here
        state = {
            "project_name": "old", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "old.coypu")
        project_io.save_project(path, state)
        text = open(path, encoding="utf-8").read().replace(
            ' chainNext="false"', '').replace(' chainNext="true"', '')
        open(path, "w", encoding="utf-8").write(text)
        assert "chainNext" not in text
        loaded = project_io.load_project(path)
        assert all(not p.chain_next for p in loaded["pi_model"].pis)


class TestAmbiguity:

    def test_clean_run_is_unambiguous(self):
        model, xy, k_from, ok, msg = _build_and_merge(200.0)
        pis = [p.index for p in model.pis if not p.omitted]
        # re-extract fresh (merge already collapsed it) for the ambiguity check
        xy2, ch2 = make_single_arc_track(200.0)
        m2 = C.extract_pi_model(xy2, ch2, tolerance=1.0, min_radius=150.0,
                                spiral_length=20.0, use_spirals=True)
        p2 = [p.index for p in m2.pis if not p.omitted]
        choice = C.merge_range_ambiguity(m2, p2[0], p2[-1])
        assert choice is None

    def test_near_180_singleton_flagged(self):
        """
        A range containing one PI whose OWN polygon deflection is itself
        near +-180 deg is individually wrap-ambiguous — hand-build a tiny
        model to hit this deterministically (DP rarely produces such a
        vertex on its own).
        """
        # Tangent polygon: a near-U-turn single vertex (178 deg) followed by
        # a second, smaller same-direction vertex — total ~= 200 or ~ -160
        # depending on which way the near-180 reading is taken.
        V = np.array([
            [0.0, 0.0],
            [500.0, 0.0],
            [500.0 + 300.0 * math.cos(math.radians(178.0)),
             300.0 * math.sin(math.radians(178.0))],
        ])
        # extend with a third vertex turning another +20 deg from the 178 leg
        phi2 = math.radians(178.0)
        p2 = V[2]
        phi3 = phi2 + math.radians(20.0)
        p3 = p2 + 300.0 * np.array([math.cos(phi3), math.sin(phi3)])
        phi4 = phi3
        p4 = p3 + 500.0 * np.array([math.cos(phi4), math.sin(phi4)])
        V = np.vstack([V, p3[None, :], p4[None, :]])

        n_osm = 400
        t = np.linspace(0, 1, n_osm)
        # crude polyline sample along the polygon legs (doesn't need to be a
        # real curve — only _polygon_deflection / V are used by the
        # ambiguity check, so a coarse reference polyline is enough)
        xy_ref = np.vstack([
            V[0] + (V[1] - V[0]) * np.linspace(0, 1, 50)[:, None],
            V[1] + (V[2] - V[1]) * np.linspace(0, 1, 50)[:, None],
            V[2] + (V[3] - V[2]) * np.linspace(0, 1, 50)[:, None],
            V[3] + (V[4] - V[3]) * np.linspace(0, 1, 50)[:, None],
        ])
        ch = np.concatenate([[0.0], np.cumsum(
            np.hypot(np.diff(xy_ref[:, 0]), np.diff(xy_ref[:, 1])))])
        idx = [0, 49, 99, 149, 199]

        model = C.PIAlignment(
            V=V, idx=idx,
            pis=[C.PIData(index=k, xy=(float(V[k][0]), float(V[k][1])),
                          deflection=C._polygon_deflection(V, k))
                 for k in (1, 2, 3)],
            xy_ref=xy_ref, chainages_ref=ch, tol=1.0, min_radius=150.0,
            spiral_default=20.0, use_spirals=True,
        )
        total, same_sign, near_180 = C._range_total_deflection(model, 1, 3)
        assert same_sign
        assert near_180, "the 178 deg leg should trip the near-180 guard"

        choice = C.merge_range_ambiguity(model, 1, 3)
        if choice is not None:
            assert len(choice.candidates) == 2
            totals = sorted(c.total_deg for c in choice.candidates)
            assert totals[1] - totals[0] > 300.0   # the two readings differ by ~360 deg
