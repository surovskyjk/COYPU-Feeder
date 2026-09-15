"""
Part 1B — requested vs applied radius/spiral length.

Before this fix, `PIData.radius`/`PIData.spiral_len` were both the REQUEST
(what the user typed / what "auto" resolved to) AND, after every rebuild,
silently overwritten with the ACHIEVED value — including 0 when a transient
infeasibility (e.g. a mid-typing radius of "2" en route to "200") clamped a
transition spiral away entirely. Since `spiral_len == 0` is the model's own
encoding for "user explicitly wants no spiral" (see PIData's docstring), one
infeasible intermediate state became a permanent, sticky "no spiral" setting.

The fix: `radius`/`spiral_len` are now the request and are never overwritten
by a clamp — only the one-time auto (<=0 / <0) → concrete resolution locks
them in, mirroring `_reset_pi`'s "set back to the sentinel to re-resolve"
contract. `radius_applied`/`spiral_len_applied` always carry what the solver
actually built.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, SOAK, build_model, fp_elements  # noqa: E402


def _shape(model, k):
    return "".join(e["type"][0] for e in model.elements if e.get("_pi") == k)


def _pid(model, k):
    return next(p for p in model.pis if p.index == k)


# ---------------------------------------------------------------------------
# The literal reported reproduction
# ---------------------------------------------------------------------------

def test_typing_through_an_infeasible_radius_recovers_the_spiral():
    """typing "2" -> "20" -> "200" -> "600" in the radius box must NOT
    permanently strip the spirals — the request (spiral_len) is untouched
    throughout, and the achieved geometry (spiral_len_applied / shape)
    recovers as soon as the radius is feasible again."""
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    k = ks[1]
    pid = _pid(model, k)
    assert _shape(model, k) == "SAS"
    requested_sl = pid.spiral_len
    assert requested_sl > 0

    for r, expect_shape in ((2.0, "A"), (20.0, "SAS"), (200.0, "SAS"), (600.0, "SAS")):
        pid.radius = r
        C.rebuild_from_pi_model(model)
        pid = _pid(model, k)
        # The REQUEST never moves — this is the actual bug fix.
        assert pid.spiral_len == requested_sl, (
            f"R={r}: spiral_len request was clobbered to {pid.spiral_len!r}")
        assert _shape(model, k) == expect_shape, (
            f"R={r}: expected shape {expect_shape}, got {_shape(model, k)}")

    # Final state (R=600, feasible again) must be indistinguishable from a
    # model that was simply built with R=600 / spiral_len=20 from scratch —
    # no lingering scar from the R=2 excursion.
    ref = build_model(RUN3)
    ref_pid = _pid(ref, k)
    ref_pid.radius = 600.0
    C.rebuild_from_pi_model(ref)
    assert fp_elements(model) == fp_elements(ref)


def test_infeasible_radius_logs_a_warning_and_keeps_the_arc():
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    k = ks[1]
    pid = _pid(model, k)
    pid.radius = 2.0
    C.rebuild_from_pi_model(model)
    assert _shape(model, k) == "A"
    assert any(f"PI {k}" in note and "spiral" in note.lower()
              for note in model.log), model.log
    assert any(note.startswith("⚠") for note in model.log)


# ---------------------------------------------------------------------------
# Radius clamp round trip
# ---------------------------------------------------------------------------

def test_clamped_radius_request_is_preserved_and_recovers():
    """An explicit radius override too large for the available tangent room
    is clamped in the ACHIEVED geometry (radius_applied), but the REQUEST
    stays exactly what was typed — so widening the neighbouring geometry (or
    simply the clamp condition going away) lets the full request through."""
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    k = ks[1]
    pid = _pid(model, k)

    huge = 1e6
    pid.radius = huge
    C.rebuild_from_pi_model(model)
    pid = _pid(model, k)
    assert pid.radius == huge, "request must not be clobbered by the clamp"
    assert pid.radius_applied < huge, "the achieved radius must actually be clamped"
    assert any("clamp" in note.lower() or "does not fit" in note.lower()
              or "applied" in note.lower() for note in model.log), model.log

    # Reset back to a reasonable value — no lingering effect from the clamp.
    pid.radius = 300.0
    C.rebuild_from_pi_model(model)
    pid = _pid(model, k)
    assert pid.radius == 300.0
    assert pid.radius_applied == pytest.approx(300.0, abs=1.0)


# ---------------------------------------------------------------------------
# Auto resolves-and-locks (unchanged behaviour) — Reset semantics
# ---------------------------------------------------------------------------

def test_auto_radius_and_spiral_resolve_and_lock_on_first_build():
    """This is NOT new behaviour — extract_pi_model's multi-pass loop and
    _reset_pi (element_table.py) both depend on "auto" (<=0 / <0) resolving
    to a concrete value that then STAYS after one rebuild, only reset back to
    auto by explicitly setting the sentinel again."""
    model = build_model(RUN3)
    for pid in model.pis:
        if pid.omitted:
            continue
        assert pid.radius > 0, "auto radius must resolve after the first build"
        assert pid.spiral_len >= 0, "auto spiral length must resolve after the first build"
        assert pid.radius_applied == pytest.approx(pid.radius, abs=1e-6)
        assert pid.spiral_len_applied == pytest.approx(pid.spiral_len, abs=1e-6)


def test_reset_to_auto_sentinel_reresolves_cleanly():
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    k = ks[1]
    pid = _pid(model, k)
    locked_r, locked_sl = pid.radius, pid.spiral_len

    pid.radius = 999.0
    C.rebuild_from_pi_model(model)
    assert _pid(model, k).radius == 999.0

    # "Reset" (element_table._reset_pi's own mechanism): set back to auto.
    pid = _pid(model, k)
    pid.radius = -1.0
    pid.spiral_len = -1.0
    C.rebuild_from_pi_model(model)
    pid = _pid(model, k)
    assert pid.radius == pytest.approx(locked_r, abs=1e-6)
    assert pid.spiral_len == pytest.approx(locked_sl, abs=1e-6)


# ---------------------------------------------------------------------------
# rebuild_from_pi_model determinism (no drift across repeated no-op rebuilds)
# ---------------------------------------------------------------------------

def test_repeated_rebuild_is_bit_identical():
    model = build_model(SOAK)
    fp1 = fp_elements(model)
    C.rebuild_from_pi_model(model)
    fp2 = fp_elements(model)
    C.rebuild_from_pi_model(model)
    fp3 = fp_elements(model)
    assert fp1 == fp2 == fp3


# ---------------------------------------------------------------------------
# Snapshot / rollback carries the new fields
# ---------------------------------------------------------------------------

def test_span_snapshot_roundtrips_applied_fields():
    model = build_model(RUN3)
    ks = [p.index for p in model.pis if not p.omitted]
    k = ks[1]
    pid = _pid(model, k)
    before_r_applied = pid.radius_applied
    before_sl_applied = pid.spiral_len_applied

    snap = C._span_snapshot(model)
    pid.radius = 5.0
    C.rebuild_pi_span(model, k, k)
    assert _pid(model, k).radius_applied != before_r_applied or \
        _pid(model, k).spiral_len_applied != before_sl_applied

    C.restore_span_snapshot(model, snap)
    pid = _pid(model, k)
    assert pid.radius_applied == pytest.approx(before_r_applied, abs=1e-9)
    assert pid.spiral_len_applied == pytest.approx(before_sl_applied, abs=1e-9)


# ---------------------------------------------------------------------------
# merge_intermediate_line's clamp-detection must use *_applied
# ---------------------------------------------------------------------------

# Same two-curve-with-a-short-straight layout in both cases (only the
# approach/departure straight lengths differ) — one has enough outer tangent
# room to prolong the spirals and close the gap, the other doesn't.
_MERGE_OK_SEGS   = [("S", 400), ("A", 500, 30), ("S", 100), ("A", 480, 25), ("S", 400)]
_MERGE_FAIL_SEGS = [("S", 25),  ("A", 500, 30), ("S", 100), ("A", 480, 25), ("S", 25)]


def _adjacent_curve_pis(model):
    two = [p.index for p in model.pis if not p.omitted][:2]
    if len(two) < 2 or two[1] != two[0] + 1:
        pytest.skip("extraction merged/reshaped the pair — layout changed")
    return two


def test_merge_intermediate_line_success_uses_applied_field_correctly():
    """A genuinely feasible prolongation must be recognised as a success —
    proving the *_applied-based `ok` check isn't just permissive by
    construction (e.g. accidentally always true)."""
    model = build_model(_MERGE_OK_SEGS)
    pi_a, pi_b = _adjacent_curve_pis(model)
    ok, msg = C.merge_intermediate_line(model, pi_a, pi_b)
    assert ok, msg
    a, b = _pid(model, pi_a), _pid(model, pi_b)
    # The request and the achieved result must now agree exactly.
    assert a.spiral_len_applied == pytest.approx(a.spiral_len, abs=0.05)
    assert b.spiral_len_applied == pytest.approx(b.spiral_len, abs=0.05)


def test_merge_intermediate_line_rollback_on_infeasible_prolongation():
    """Construct a case where the prolonged spiral length cannot actually be
    built (rollback path) — the (ok/not-ok) decision must be driven by
    spiral_len_applied, not by spiral_len (which no longer reflects the
    achieved value under the fix, so a naive check would read back the
    REQUEST and false-positive as "succeeded")."""
    model = build_model(_MERGE_FAIL_SEGS)
    pi_a, pi_b = _adjacent_curve_pis(model)
    before = fp_elements(model)
    ok, msg = C.merge_intermediate_line(model, pi_a, pi_b)
    assert not ok, (
        "expected this tight-approach layout to be infeasible — if it now "
        f"succeeds ({msg!r}), the test fixture needs adjusting")
    assert fp_elements(model) == before, "failed merge must roll back byte-identical"
