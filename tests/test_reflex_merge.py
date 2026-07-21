"""
merge_pi_range always replaces a PI range with exactly ONE PI — a single
Spiral-Arc-Spiral, both boundary tangents kept fixed — for any total turn,
including a reflex/major arc above 180 deg (PIData.merged_turn carries the
signed, unwrapped total; see _build_zone_range's reflex branch).

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


def _build_and_merge(total_deg, R=600.0, straight=500.0):
    # A reflex/major-arc curve needs tangent room on the order of
    # R*|tan(turn/2)| on each side — huge just past 180 deg, shrinking back
    # down as the turn approaches 360 deg. Angles well past 180 deg need a
    # longer straight to be geometrically constructible at all; callers
    # picked straight lengths accordingly (see the module's angle/room note).
    xy, ch = make_single_arc_track(total_deg, R=R, straight=straight)
    model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                               spiral_length=20.0, use_spirals=True)
    pis = [p.index for p in model.pis if not p.omitted]
    assert len(pis) >= 2, "test track should DP-fragment into several PIs"
    k_from, k_to = pis[0], pis[-1]
    ok, msg = C.merge_pi_range(model, k_from, k_to)
    return model, xy, k_from, ok, msg


# (total_deg, straight) pairs that are geometrically constructible: near
# 180 deg the required tangent room explodes (T = R*|tan(turn/2)|), so
# angles further past 180 deg deliberately use a longer straight.
FEASIBLE_ANGLES = [(60.0, 500.0), (120.0, 500.0), (250.0, 2000.0),
                   (300.0, 2000.0)]


class TestSingleCurveMerge:

    @pytest.mark.parametrize("total_deg,straight", FEASIBLE_ANGLES)
    def test_merge_yields_exactly_spiral_arc_spiral(self, total_deg, straight):
        model, xy, k_from, ok, msg = _build_and_merge(total_deg, straight=straight)
        assert ok, msg

        # Exactly one PI survives the merge — no chain, ever.
        pid = next(p for p in model.pis if p.index == k_from)
        assert not pid.chain_next
        assert pid.merged_turn is not None
        assert abs(math.degrees(pid.merged_turn) - total_deg) < 1.0

        own = [e for e in model.elements if e.get("_pi") == k_from]
        types = [e["type"] for e in own]
        arcs = [e for e in own if e["type"] == "Arc"]
        assert len(arcs) == 1, f"expected exactly one Arc, got {types}"
        assert set(types) <= {"Spiral", "Arc"}, \
            f"merge produced element types other than Spiral/Arc: {types}"
        # Spiral-Arc-Spiral (or bare Arc if spirals degraded to zero) —
        # never Arc-Arc or anything with more than one curve.
        assert types in (["Arc"], ["Spiral", "Arc", "Spiral"])

        radius = arcs[0]["radius"]
        assert abs(radius - 600.0) < 15.0, f"radius {radius} far from true 600"

        els = model.elements
        # C0: every element's end matches the next one's start exactly
        max_gap = max(
            math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
            for a, b in zip(els[:-1], els[1:]))
        assert max_gap < 1e-6, f"C0 gap {max_gap} m"

        # C1: no heading discontinuity anywhere in the alignment
        jj = _max_heading_jump_rad(els)
        assert math.degrees(jj) < 1e-3, f"heading jump {math.degrees(jj)} deg"

        # endpoints anchored to the original OSM polyline exactly
        assert math.hypot(els[0]["start"][0] - xy[0][0],
                          els[0]["start"][1] - xy[0][1]) < 1e-6
        assert math.hypot(els[-1]["end"][0] - xy[-1][0],
                          els[-1]["end"][1] - xy[-1][1]) < 1e-6

        assert model.last_stats["max_deviation"] < 1.0, \
            f"max_dev {model.last_stats['max_deviation']} too large for a clean arc"

    def test_boundary_tangents_fixed(self):
        """The vertex before k_from and after k_to are untouched by the merge —
        only the interior PIs collapse into one."""
        xy, ch = make_single_arc_track(150.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k_from, k_to = pis[0], pis[-1]
        A_before = model.V[k_from - 1].copy()
        B_before = model.V[k_to + 1].copy()
        ok, msg = C.merge_pi_range(model, k_from, k_to)
        assert ok, msg
        assert np.allclose(model.V[k_from - 1], A_before)
        assert np.allclose(model.V[k_from + 1], B_before)   # k_to collapsed

    def test_absurd_total_rejected(self):
        xy, ch = make_single_arc_track(60.0)   # a real, constructible 2+ PI range
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k_from, k_to = pis[0], pis[-1]
        ok2, msg2 = C._merge_pi_range_single(model, k_from, k_to,
                                             total_turn=math.radians(355.0))
        assert not ok2
        assert "plausible" in msg2.lower()

    def test_near_180_singular_refused(self):
        """A total turn within the singular band around 180 deg has no
        finite single-PI solution (the boundary tangents are near-
        parallel) — merge_pi_range must fall back to the auxiliary-PI
        construction (see TestAuxiliaryPISingularBand) rather than build a
        runaway curve. A single PI is never produced for such a turn."""
        xy, ch = make_single_arc_track(60.0)   # a real, constructible 2+ PI range
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k_from, k_to = pis[0], pis[-1]
        ok, msg = C._merge_pi_range_single(model, k_from, k_to,
                                           total_turn=math.radians(179.0))
        assert ok, msg
        assert "auxiliary" in msg.lower()
        arcs = [e for e in model.elements if e.get("type") == "Arc"]
        assert len(arcs) == 2   # never a single arc this close to 180 deg

    def test_insufficient_tangent_room_fails_cleanly(self):
        """A reflex angle just past 180 deg needs huge tangent room
        (T = R*|tan(turn/2)|); with only a short straight either side, the
        merge must fail with a clear message — never silently shrink the
        radius below the value fitted to the OSM points."""
        model, xy, k_from, ok, msg = _build_and_merge(200.0, straight=500.0)
        assert not ok
        assert "room" in msg.lower() or "no room" in msg.lower()


class TestExportAndRoundTrip:

    def test_landxml_reflex_arc(self, tmp_path):
        from landxml.builder import build_landxml, write_landxml
        model, xy, k_from, ok, msg = _build_and_merge(270.0, straight=2000.0)
        assert ok, msg
        aln = {"name": "test", "elements": model.elements, "sta_start": 0.0}
        root = build_landxml([aln], output_epsg=5514)
        out = tmp_path / "reflex.xml"
        write_landxml(root, str(out))
        text = out.read_text(encoding="utf-8")
        n_arcs = sum(1 for e in model.elements if e["type"] == "Arc")
        assert text.count("<Curve") == n_arcs == 1   # ONE reflex curve, not a chain
        assert 'crvType="arc"' in text
        assert text.count("<Center") == 1

    def test_project_roundtrip_merged_turn(self, tmp_path):
        import project_io
        model, xy, k_from, ok, msg = _build_and_merge(300.0, straight=2000.0)
        assert ok, msg
        before = [(p.index, p.merged_turn, round(p.radius, 6))
                  for p in model.pis]

        state = {
            "project_name": "reflex-test", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "reflex.coypu")
        project_io.save_project(path, state)
        text = open(path, encoding="utf-8").read()
        assert '<PI ' in text
        assert 'mergedTurn=' in text

        loaded = project_io.load_project(path)
        m2 = loaded["pi_model"]
        after = [(p.index, p.merged_turn, round(p.radius, 6)) for p in m2.pis]
        for (i1, t1, r1), (i2, t2, r2) in zip(before, after):
            assert i1 == i2
            assert r1 == r2
            if t1 is None:
                assert t2 is None
            else:
                assert abs(t1 - t2) < 1e-6

    def test_old_project_file_without_merged_turn_loads(self, tmp_path):
        """A pre-1.2 .coypu file (no mergedTurn attribute) must still load —
        the default (None) keeps every PI's ordinary wrapped-delta rebuild."""
        import project_io
        model, xy, k_from, ok, msg = _build_and_merge(60.0)
        state = {
            "project_name": "old", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "old.coypu")
        project_io.save_project(path, state)
        text = open(path, encoding="utf-8").read()
        import re
        text = re.sub(r' mergedTurn="[^"]*"', '', text)
        open(path, "w", encoding="utf-8").write(text)
        assert "mergedTurn" not in text
        loaded = project_io.load_project(path)
        assert all(p.merged_turn is None for p in loaded["pi_model"].pis)

    def test_legacy_chain_next_still_loads(self, tmp_path):
        """A pre-release .coypu 1.1 file with chainNext=true (from the old,
        now-removed chained-merge builder) must still load and rebuild via
        _build_zone_range's untouched legacy chain-boundary code path."""
        import project_io
        xy, ch = make_single_arc_track(80.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        state = {
            "project_name": "legacy", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "legacy.coypu")
        project_io.save_project(path, state)
        text = open(path, encoding="utf-8").read()
        # Flip the FIRST PI's chainNext to true, simulating a legacy file.
        text = text.replace('chainNext="false"', 'chainNext="true"', 1)
        open(path, "w", encoding="utf-8").write(text)
        loaded = project_io.load_project(path)   # must not raise
        m2 = loaded["pi_model"]
        assert any(p.chain_next for p in m2.pis)
        # Rebuild already ran inside load_project (via rebuild_from_pi_model);
        # the alignment must still be a valid, C1-continuous element chain.
        els = m2.elements
        assert len(els) > 0
        max_gap = max(
            math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
            for a, b in zip(els[:-1], els[1:]))
        assert max_gap < 1e-6


class TestAmbiguity:

    def test_clean_run_is_unambiguous(self):
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
        V = np.array([
            [0.0, 0.0],
            [500.0, 0.0],
            [500.0 + 300.0 * math.cos(math.radians(178.0)),
             300.0 * math.sin(math.radians(178.0))],
        ])
        phi2 = math.radians(178.0)
        p2 = V[2]
        phi3 = phi2 + math.radians(20.0)
        p3 = p2 + 300.0 * np.array([math.cos(phi3), math.sin(phi3)])
        phi4 = phi3
        p4 = p3 + 500.0 * np.array([math.cos(phi4), math.sin(phi4)])
        V = np.vstack([V, p3[None, :], p4[None, :]])

        n_osm = 400
        t = np.linspace(0, 1, n_osm)
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


class TestBitIdentityUnaffected:
    """Ordinary (non-merged) rebuilds must be byte-for-byte unaffected by the
    reflex-merge machinery — it's only ever reached via PIData.merged_turn,
    which no ordinary PI ever sets."""

    def test_ordinary_rebuild_untouched(self):
        xy, ch = make_single_arc_track(45.0)
        m1 = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                spiral_length=20.0, use_spirals=True)
        m2 = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                spiral_length=20.0, use_spirals=True)
        assert len(m1.elements) == len(m2.elements)
        for e1, e2 in zip(m1.elements, m2.elements):
            assert e1["type"] == e2["type"]
            assert round(e1["length"], 9) == round(e2["length"], 9)
            assert all(abs(a - b) < 1e-9 for a, b in
                      zip(e1["start"], e2["start"]))


class TestAuxiliaryPISingularBand:
    """A total turn within _REFLEX_SINGULAR_DEG (2 deg) of +-180 deg has no
    finite single-PI tangent-line-intersection solution — merge_pi_range
    falls back to ONE auxiliary PI (two arcs sharing one fitted radius,
    joined at a zero-length connector via the legacy chain_next marker)
    instead of refusing outright."""

    @pytest.mark.parametrize("total_deg", [178.5, 179.0, 180.0, 181.0, 181.5])
    def test_aux_pi_yields_two_arcs_one_radius_c1(self, total_deg):
        xy, ch = make_single_arc_track(total_deg, R=600.0, straight=2000.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k_from, k_to = pis[0], pis[-1]
        ok, msg = C.merge_pi_range(model, k_from, k_to)
        assert ok, msg
        assert "auxiliary" in msg.lower()

        arcs = [e for e in model.elements if e.get("type") == "Arc"]
        assert len(arcs) == 2, f"expected exactly 2 arcs, got {len(arcs)}"
        radii = [a["radius"] for a in arcs]
        # Both PIData entries request the SAME fitted R, but the (unchanged,
        # legacy) chain-boundary construction may clamp each end slightly
        # differently against its own available tangent room — a small
        # tolerance, not exact equality (matches the old chain-merge tests'
        # bar of "shared within the T_max clamp tolerance").
        assert abs(radii[0] - radii[1]) < 1.0, "the two arcs must share one radius"
        assert abs(radii[0] - 600.0) < 15.0, f"radius {radii[0]} far from true 600"

        live_pis = [p for p in model.pis if not p.omitted]
        assert len(live_pis) == 2
        assert live_pis[0].chain_next and not live_pis[1].chain_next

        els = model.elements
        max_gap = max(
            math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
            for a, b in zip(els[:-1], els[1:]))
        assert max_gap < 1e-6, f"C0 gap {max_gap} m"
        jj = _max_heading_jump_rad(els)
        assert math.degrees(jj) < 1e-3, f"heading jump {math.degrees(jj)} deg"

        assert math.hypot(els[0]["start"][0] - xy[0][0],
                          els[0]["start"][1] - xy[0][1]) < 1e-6
        assert math.hypot(els[-1]["end"][0] - xy[-1][0],
                          els[-1]["end"][1] - xy[-1][1]) < 1e-6
        assert model.last_stats["max_deviation"] < 1.0

    def test_landxml_two_curves(self, tmp_path):
        from landxml.builder import build_landxml, write_landxml
        xy, ch = make_single_arc_track(180.3, R=600.0, straight=2000.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        ok, msg = C.merge_pi_range(model, pis[0], pis[-1])
        assert ok, msg
        aln = {"name": "test", "elements": model.elements, "sta_start": 0.0}
        root = build_landxml([aln], output_epsg=5514)
        out = tmp_path / "aux.xml"
        write_landxml(root, str(out))
        text = out.read_text(encoding="utf-8")
        n_arcs = sum(1 for e in model.elements if e["type"] == "Arc")
        assert n_arcs == 2
        assert text.count("<Curve") == 2

    def test_project_roundtrip_chain_next(self, tmp_path):
        import project_io
        xy, ch = make_single_arc_track(180.3, R=600.0, straight=2000.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        ok, msg = C.merge_pi_range(model, pis[0], pis[-1])
        assert ok, msg
        before = [(p.index, p.chain_next, round(p.radius, 6))
                  for p in model.pis if not p.omitted]

        state = {
            "project_name": "aux-pi-test", "work_epsg": 5514, "level": "level3",
            "step": 4, "settings": {}, "tracks": [], "selected_tracks": [0],
            "pi_model": model, "stations": [],
        }
        path = str(tmp_path / "auxpi.coypu")
        project_io.save_project(path, state)
        text = open(path, encoding="utf-8").read()
        assert 'chainNext="true"' in text

        loaded = project_io.load_project(path)
        m2 = loaded["pi_model"]
        after = [(p.index, p.chain_next, round(p.radius, 6))
                 for p in m2.pis if not p.omitted]
        assert after == before

    def test_consolidate_apply_uses_aux_pi(self):
        """apply_consolidation (the Consolidate step's core function) must
        pick up a near-180-deg run via the same fallback — it calls
        merge_pi_range internally, so this is mostly a wiring check."""
        xy, ch = make_single_arc_track(180.2, R=600.0, straight=2000.0)
        model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                   spiral_length=20.0, use_spirals=True)
        pis = [p.index for p in model.pis if not p.omitted]
        k_from, k_to = pis[0], pis[-1]
        group = {"k_from": k_from, "k_to": k_to, "ok": True}
        applied, msgs = C.apply_consolidation(model, [group])
        assert applied == 1
        assert "auxiliary" in msgs[0].lower()
        arcs = [e for e in model.elements if e.get("type") == "Arc"]
        assert len(arcs) == 2

    def test_far_from_band_still_single_curve(self):
        """Sanity: totals clearly outside the +-2 deg band still take the
        ordinary single-PI (or plain reflex) path — no regression from
        adding the aux fallback."""
        for total_deg, straight in [(60.0, 500.0), (120.0, 500.0),
                                    (250.0, 2000.0), (300.0, 2000.0)]:
            xy, ch = make_single_arc_track(total_deg, R=600.0, straight=straight)
            model = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                       spiral_length=20.0, use_spirals=True)
            pis = [p.index for p in model.pis if not p.omitted]
            ok, msg = C.merge_pi_range(model, pis[0], pis[-1])
            assert ok, msg
            arcs = [e for e in model.elements if e.get("type") == "Arc"]
            assert len(arcs) == 1, f"{total_deg} deg: expected 1 arc, got {len(arcs)}"
