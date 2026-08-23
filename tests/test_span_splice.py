"""
Correctness tests for `model.last_splice` — the element-list splice that
`rebuild_pi_span` now exposes so the GUI can ship only the changed span to
the map instead of resampling/reprojecting the whole alignment.

Each mutator is checked against two independent properties:

1. Soundness — the announced prefix `elements[:start]` and suffix
   `elements[start+added:]` are geometrically IDENTICAL to the
   corresponding regions of the pre-edit element list (untouched-outside-
   the-window contract).
2. Patch-reconstruction — splicing the pre-edit list with the announced
   window reproduces the post-edit list exactly (geometrically), the same
   operation `map.html`'s `patchAlignmentSegmented` performs client-side.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, build_model  # noqa: E402


def _snapshot(model) -> list[dict]:
    """Shallow-copy every element dict so later in-place sta_start/id
    write-backs on the live model don't retroactively mutate our 'before'
    reference (rebuild_pi_span reuses dict objects for the untouched
    regions, not just their values)."""
    return [dict(e) for e in model.elements]


def _geom_eq(a: dict, b: dict) -> bool:
    return C._element_geom_equal(a, b)


def _assert_geom_seq_equal(seq_a: list[dict], seq_b: list[dict], ctx: str):
    assert len(seq_a) == len(seq_b), f"{ctx}: length {len(seq_a)} != {len(seq_b)}"
    for i, (a, b) in enumerate(zip(seq_a, seq_b)):
        assert _geom_eq(a, b), f"{ctx}: element {i} geometry differs"


def _check_splice(old_elements: list[dict], model, ctx: str):
    """Soundness + patch-reconstruction for one successful edit."""
    splice = model.last_splice
    assert splice is not None, f"{ctx}: expected a splice, got None (fell back?)"
    start, removed, added = splice["start"], splice["removed"], splice["added"]
    new_elements = model.elements

    assert len(new_elements) == len(old_elements) - removed + added, (
        f"{ctx}: count mismatch — old {len(old_elements)}, new "
        f"{len(new_elements)}, splice {splice}")
    assert 0 <= start <= len(old_elements)
    assert start + removed <= len(old_elements)

    # 1. Soundness: prefix/suffix outside the window are untouched.
    _assert_geom_seq_equal(
        old_elements[:start], new_elements[:start], f"{ctx}: prefix")
    _assert_geom_seq_equal(
        old_elements[start + removed:], new_elements[start + added:],
        f"{ctx}: suffix")

    # 2. Patch reconstruction: old[:start] + new[start:start+added] +
    #    old[start+removed:] must equal the full new list, element-for-
    #    element — exactly what patchAlignmentSegmented does in map.html.
    patched = (old_elements[:start] + new_elements[start:start + added]
               + old_elements[start + removed:])
    _assert_geom_seq_equal(patched, new_elements, f"{ctx}: patch reconstruction")


class TestSpanSplice:

    def test_value_edit(self):
        model = build_model(RUN3)
        arc_pi = next(p for p in model.pis if not p.omitted)
        before = _snapshot(model)
        arc_pi.radius = max(50.0, arc_pi.radius * 0.7 if arc_pi.radius > 0 else 400.0)
        snap = C.rebuild_pi_span(model, arc_pi.index, arc_pi.index)
        assert snap is not None, "span rebuild fell back to a full rebuild"
        _check_splice(before, model, "value edit")

    def test_omit_restore(self):
        model = build_model(RUN3)
        pi = next(p for p in model.pis if not p.omitted)
        before = _snapshot(model)
        pi.omitted = True
        snap = C.rebuild_pi_span(model, pi.index, pi.index)
        assert snap is not None
        _check_splice(before, model, "omit")

        before2 = _snapshot(model)
        pi.omitted = False
        snap2 = C.rebuild_pi_span(model, pi.index, pi.index)
        assert snap2 is not None
        _check_splice(before2, model, "restore")

    def test_move_pi(self):
        model = build_model(RUN3)
        k = model.pis[0].index
        before = _snapshot(model)
        target = model.V[k].copy()
        target[0] += 3.0
        target[1] -= 2.0
        ok, msg = C.move_pi(model, k, target)
        assert ok, msg
        _check_splice(before, model, "move_pi")

    def test_merge_pi_range(self):
        model = build_model(RUN3)
        assert len(model.pis) >= 3
        before = _snapshot(model)
        ok, msg = C.merge_pi_range(model, 1, 2)
        assert ok, msg
        _check_splice(before, model, "merge_pi_range")

    def test_undo_merge(self):
        # A short intermediate straight the two curves can legally absorb
        # (RUN3's own gaps are too wide relative to their radii).
        segs = [("S", 400), ("A", 500, 30), ("S", 100), ("A", 480, 25), ("S", 400)]
        model = build_model(segs)
        two = [p.index for p in model.pis if not p.omitted][:2]
        if len(two) < 2 or two[1] != two[0] + 1:
            pytest.skip("extraction merged the pair — layout changed")
        pi_a, pi_b = two
        ok, msg = C.merge_intermediate_line(model, pi_a, pi_b)
        assert ok, msg

        before = _snapshot(model)
        ok2, msg2 = C.undo_merge(model, pi_a)
        assert ok2, msg2
        _check_splice(before, model, "undo_merge")

    def test_insert_delete_pi(self):
        model = build_model(RUN3)
        j_ref = int((model.idx[1] + model.idx[2]) // 2)
        before = _snapshot(model)
        ok, msg = C.insert_pi(model, j_ref)
        assert ok, msg
        _check_splice(before, model, "insert_pi")

        new_k = next(p.index for p in model.pis
                     if int(model.idx[p.index]) == j_ref
                     or abs(int(model.idx[p.index]) - j_ref) <= 1)
        before2 = _snapshot(model)
        ok2, msg2 = C.delete_pi(model, new_k)
        assert ok2, msg2
        _check_splice(before2, model, "delete_pi")

    def test_fallback_has_no_splice(self):
        """A full rebuild (rebuild_from_pi_model) must clear last_splice —
        the GUI must never patch against a stale window after a full
        reconstruction (e.g. trim_alignment, candidate extraction)."""
        model = build_model(RUN3)
        C.rebuild_pi_span(model, model.pis[0].index, model.pis[0].index)
        assert model.last_splice is not None
        C.rebuild_from_pi_model(model)
        assert model.last_splice is None
