"""
Element-level split / delete / trim: insert_pi, delete_pi, trim_alignment.

insert_pi / delete_pi follow the merge_pi_range template (vertex array
edit + PI reindex + span rebuild via rebuild_pi_span) — every result must
keep the alignment's C0/C1 guarantees and exact endpoint anchoring.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import build_model, RUN3, SOAK  # noqa: E402


def _check_c0_c1_endpoints(model, ctx=""):
    els = model.elements
    assert els, f"{ctx}: empty element chain"
    for a, b in zip(els[:-1], els[1:]):
        gap = math.hypot(a["end"][0] - b["start"][0], a["end"][1] - b["start"][1])
        assert gap < 1e-6, f"{ctx}: C0 gap {gap} m"
    jj = C._max_heading_jump_rad(els)
    assert math.degrees(jj) < 1e-3, f"{ctx}: heading jump {math.degrees(jj)} deg"
    assert math.hypot(els[0]["start"][0] - model.xy_ref[0][0],
                      els[0]["start"][1] - model.xy_ref[0][1]) < 1e-6, \
        f"{ctx}: start endpoint drift"
    assert math.hypot(els[-1]["end"][0] - model.xy_ref[-1][0],
                      els[-1]["end"][1] - model.xy_ref[-1][1]) < 1e-6, \
        f"{ctx}: end endpoint drift"


class TestInsertPi:

    def test_split_a_line(self):
        model = build_model(RUN3)
        n0 = len(model.pis)
        line = max((e for e in model.elements if e["type"] == "Line"),
                  key=lambda e: e["length"])
        mid = [(line["start"][0] + line["end"][0]) / 2,
               (line["start"][1] + line["end"][1]) / 2]
        j_ref = C.nearest_xy_ref_index(model, mid)
        ok, msg = C.insert_pi(model, j_ref)
        assert ok, msg
        assert len(model.pis) == n0 + 1
        _check_c0_c1_endpoints(model, "insert_pi")

    def test_reject_at_existing_vertex(self):
        model = build_model(RUN3)
        fp_before = [dict(e) for e in model.elements]
        ok, msg = C.insert_pi(model, model.idx[1])
        assert not ok
        assert model.elements == fp_before or all(
            a == b for a, b in zip(model.elements, fp_before))

    def test_reject_out_of_bounds(self):
        model = build_model(RUN3)
        ok, msg = C.insert_pi(model, -1)
        assert not ok
        ok, msg = C.insert_pi(model, 10_000_000)
        assert not ok
        ok, msg = C.insert_pi(model, model.idx[0])   # the very start
        assert not ok
        ok, msg = C.insert_pi(model, model.idx[-1])  # the very end
        assert not ok

    def test_new_pi_indices_shift_correctly(self):
        model = build_model(RUN3)
        old_indices = sorted(p.index for p in model.pis)
        line = model.elements[0]
        for e in model.elements:
            if e["type"] == "Line" and e["length"] > 50:
                line = e
                break
        j_ref = C.nearest_xy_ref_index(
            model, [(line["start"][0] + line["end"][0]) / 2,
                    (line["start"][1] + line["end"][1]) / 2])
        ok, msg = C.insert_pi(model, j_ref)
        assert ok, msg
        new_indices = sorted(p.index for p in model.pis)
        assert new_indices == list(range(1, len(new_indices) + 1)), \
            "PI indices must be contiguous 1..n after insert"


class TestDeletePi:

    def test_delete_interior_pi(self):
        model = build_model(RUN3)
        n0 = len(model.pis)
        k = model.pis[len(model.pis) // 2].index
        ok, msg = C.delete_pi(model, k)
        assert ok, msg
        assert len(model.pis) == n0 - 1
        _check_c0_c1_endpoints(model, "delete_pi")

    def test_delete_differs_from_omit(self):
        """delete_pi removes the vertex; omit keeps it (self-healing skip)."""
        m_del = build_model(RUN3)
        m_omit = build_model(RUN3)
        k = m_del.pis[1].index
        n_v_before = len(m_del.V)

        ok, _ = C.delete_pi(m_del, k)
        assert ok
        assert len(m_del.V) == n_v_before - 1

        pid = next(p for p in m_omit.pis if p.index == k)
        pid.omitted = True
        C.rebuild_pi_span(m_omit, k, k)
        assert len(m_omit.V) == n_v_before   # vertex still present, just skipped

    def test_reject_out_of_bounds(self):
        model = build_model(RUN3)
        ok, msg = C.delete_pi(model, 0)
        assert not ok
        ok, msg = C.delete_pi(model, len(model.V) - 1)
        assert not ok
        ok, msg = C.delete_pi(model, 9999)
        assert not ok

    def test_reindex_contiguous(self):
        model = build_model(RUN3)
        k = model.pis[2].index
        ok, msg = C.delete_pi(model, k)
        assert ok, msg
        new_indices = sorted(p.index for p in model.pis)
        assert new_indices == list(range(1, len(new_indices) + 1))

    def test_insert_then_delete_roundtrips_count(self):
        model = build_model(RUN3)
        n0 = len(model.pis)
        line = next(e for e in model.elements
                   if e["type"] == "Line" and e["length"] > 100)
        j_ref = C.nearest_xy_ref_index(
            model, [(line["start"][0] + line["end"][0]) / 2,
                    (line["start"][1] + line["end"][1]) / 2])
        ok, _ = C.insert_pi(model, j_ref)
        assert ok
        new_k = next(p.index for p in model.pis
                    if abs(p.xy[0] - model.xy_ref[j_ref][0]) < 1e-6)
        ok, _ = C.delete_pi(model, new_k)
        assert ok
        assert len(model.pis) == n0
        _check_c0_c1_endpoints(model, "insert+delete roundtrip")


class TestTrimAlignment:

    def test_trim_shrinks_and_reanchors(self):
        model = build_model(SOAK)
        n0 = len(model.xy_ref)
        j_start, j_end = n0 // 5, 4 * n0 // 5
        ok, msg = C.trim_alignment(model, j_start, j_end)
        assert ok, msg
        assert len(model.xy_ref) == j_end - j_start + 1
        _check_c0_c1_endpoints(model, "trim_alignment")

    def test_trim_drops_pis_outside_range(self):
        model = build_model(SOAK)
        n0 = len(model.xy_ref)
        n_pis_before = len(model.pis)
        ok, msg = C.trim_alignment(model, n0 // 5, 4 * n0 // 5)
        assert ok, msg
        assert len(model.pis) <= n_pis_before

    def test_reject_invalid_range(self):
        model = build_model(RUN3)
        ok, msg = C.trim_alignment(model, 100, 50)   # end before start
        assert not ok
        ok, msg = C.trim_alignment(model, -5, 50)
        assert not ok
        ok, msg = C.trim_alignment(model, 0, 10_000_000)
        assert not ok

    def test_reject_too_small_range(self):
        model = build_model(RUN3)
        ok, msg = C.trim_alignment(model, 0, 1)
        assert not ok
        assert "too small" in msg.lower() or "empty" in msg.lower()


class TestNearestXyRefIndex:

    def test_finds_closest_point(self):
        model = build_model(RUN3)
        j = 200
        target = model.xy_ref[j]
        found = C.nearest_xy_ref_index(model, target)
        assert found == j

    def test_finds_closest_for_offset_point(self):
        model = build_model(RUN3)
        j = 150
        target = model.xy_ref[j] + np.array([0.3, -0.2])
        found = C.nearest_xy_ref_index(model, target)
        assert abs(found - j) <= 2
