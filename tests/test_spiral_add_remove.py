"""
Part 1C — manual transition-spiral add/remove (candidates.add_spirals /
candidates.remove_spirals).

The PI model already supports L-C-L <-> L-S-C-S-L natively via
PIData.spiral_len (0 = no spiral, >0 = requested length, <0 = auto) — these
two functions are thin, validated wrappers around that, following
merge_intermediate_line's rollback convention: never leave a partial edit,
report a clamp rather than silently accepting less than requested (see
Part 1B / PIData's docstring), and roll back byte-identical on total
infeasibility.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, build_model, fp_elements  # noqa: E402
from test_edit_history import _make_model  # noqa: E402


def _shape(model, k):
    return "".join(e["type"][0] for e in model.elements if e.get("_pi") == k)


def _pid(model, k):
    return next(p for p in model.pis if p.index == k)


def _first_curve_pi(model):
    return [p.index for p in model.pis if not p.omitted][1]


class TestRemoveSpirals:

    def test_removes_spirals_and_extends_arc(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        assert _shape(model, k) == "SAS"
        ok, msg = C.remove_spirals(model, k)
        assert ok, msg
        assert _shape(model, k) == "A"
        pid = _pid(model, k)
        assert pid.spiral_len == 0.0

    def test_exactly_c1_after_removal(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        assert C._max_heading_jump_rad(model.elements) <= 1e-9

    def test_refuses_when_no_spirals_present(self):
        model = build_model(RUN3, use_spirals=False)
        k = _first_curve_pi(model)
        assert _shape(model, k) == "A"
        ok, msg = C.remove_spirals(model, k)
        assert not ok
        assert "no transition spirals" in msg.lower()

    def test_refuses_for_pi_with_no_curve(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        pid = _pid(model, k)
        pid.omitted = True
        C.rebuild_from_pi_model(model)
        ok, msg = C.remove_spirals(model, k)
        assert not ok


class TestAddSpirals:

    def test_adds_default_length_spirals(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        assert _shape(model, k) == "A"
        ok, msg = C.add_spirals(model, k)
        assert ok, msg
        assert _shape(model, k) == "SAS"

    def test_adds_explicit_length(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        ok, msg = C.add_spirals(model, k, length=35.0)
        assert ok, msg
        pid = _pid(model, k)
        assert pid.spiral_len == 35.0
        assert pid.spiral_len_applied == pytest.approx(35.0, abs=0.5)

    def test_exactly_c1_after_add(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        C.add_spirals(model, k, length=25.0)
        assert C._max_heading_jump_rad(model.elements) <= 1e-9

    def test_refuses_on_level2_no_spirals_setting(self):
        model = build_model(RUN3, use_spirals=False)
        k = _first_curve_pi(model)
        ok, msg = C.add_spirals(model, k)
        assert not ok
        assert "level 2" in msg.lower() or "level 3" in msg.lower()

    def test_refuses_when_spirals_already_present(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        assert _shape(model, k) == "SAS"
        ok, msg = C.add_spirals(model, k)
        assert not ok
        assert "already" in msg.lower()

    def test_refuses_for_pi_with_no_curve(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        pid = _pid(model, k)
        pid.omitted = True
        C.rebuild_from_pi_model(model)
        ok, msg = C.add_spirals(model, k)
        assert not ok

    def test_infeasible_request_rolls_back_byte_identical(self):
        """An absurdly large spiral-length request can cascade into an
        infeasible radius too (the same override-radius clamp interaction
        every other edit shares) — either way, total infeasibility must
        leave the model exactly as it was, never a partial mutation."""
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        before = fp_elements(model)
        pid_before = (_pid(model, k).radius, _pid(model, k).spiral_len)
        ok, msg = C.add_spirals(model, k, length=100000.0)
        assert not ok, msg
        assert fp_elements(model) == before
        pid_after = _pid(model, k)
        assert (pid_after.radius, pid_after.spiral_len) == pid_before


class TestRoundTrip:

    def test_remove_then_add_back_is_byte_identical(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        original_sl = _pid(model, k).spiral_len
        before = fp_elements(model)

        ok1, msg1 = C.remove_spirals(model, k)
        assert ok1, msg1
        ok2, msg2 = C.add_spirals(model, k, length=original_sl)
        assert ok2, msg2

        after = fp_elements(model)
        assert before == after

    def test_add_then_remove_returns_to_bare_arc(self):
        model = build_model(RUN3, use_spirals=True)
        k = _first_curve_pi(model)
        C.remove_spirals(model, k)
        before = fp_elements(model)

        C.add_spirals(model, k, length=40.0)
        assert _shape(model, k) == "SAS"
        ok, msg = C.remove_spirals(model, k)
        assert ok, msg
        assert _shape(model, k) == "A"

        after = fp_elements(model)
        assert before == after


class TestSmallModelFallsBackToFullRebuild:
    """`rebuild_pi_span` returning None (too few PIs for a span-local
    rebuild, per its own `m <= 3` guard) means "fell back to a full
    rebuild" — a valid, successful path in its own right. A regression here
    once made add/remove_spirals report failure on every small alignment
    (the exact shape of a project fresh out of Step 4), even though the
    geometry was built correctly."""

    def test_remove_succeeds_on_a_tiny_single_pi_model(self):
        model = _make_model()
        k = next(p.index for p in model.pis if not p.omitted)
        assert _shape(model, k) == "SAS"
        ok, msg = C.remove_spirals(model, k)
        assert ok, msg
        assert _shape(model, k) == "A"

    def test_add_succeeds_on_a_tiny_single_pi_model(self):
        model = _make_model()
        k = next(p.index for p in model.pis if not p.omitted)
        C.remove_spirals(model, k)
        ok, msg = C.add_spirals(model, k, length=20.0)
        assert ok, msg
        assert _shape(model, k) == "SAS"
