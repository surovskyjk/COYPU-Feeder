"""
Part E — engineering-defaults preferences (SettingsDialog, Step3Configure,
extract_pi_model's new `pi_min_tangent` parameter).

Scope, per the confirmed decision: only genuine railway-engineering
parameters are exposed here (radius/length floors, tolerances) — internal
numerical-stability constants (span-rebuild tolerances, the ~180° singular-
band threshold, etc.) stay hardcoded, since CLAUDE.md's "bit-identical"
proof obligation makes those correctness knobs, not design knobs.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import geometry.candidates as C  # noqa: E402


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


ENGINEERING_KEYS = [
    "default_max_deviation", "default_min_radius", "default_spiral_length",
    "default_pi_min_tangent", "default_min_tangent_length",
    "default_min_line_length", "default_min_arc_length",
    "default_min_spiral_length",
]


class TestSettingsDialogEngineeringDefaults:

    def test_round_trips_every_key(self):
        from gui.dialogs import SettingsDialog
        current = {
            "theme_mode": "dark", "font_pt": 11, "show_log": False,
            "confirm_start_over": False,
            "default_max_deviation": 2.5, "default_min_radius": 275.0,
            "default_spiral_length": 45.0, "default_pi_min_tangent": 80.0,
            "default_min_tangent_length": 35.0, "default_min_line_length": 12.0,
            "default_min_arc_length": 14.0, "default_min_spiral_length": 16.0,
        }
        dlg = SettingsDialog(current)
        out = dlg.values()
        for key in ENGINEERING_KEYS:
            assert out[key] == pytest.approx(current[key]), key

    def test_missing_keys_fall_back_to_the_documented_defaults(self):
        from gui.dialogs import SettingsDialog
        dlg = SettingsDialog({})   # a bare/legacy prefs dict
        out = dlg.values()
        fallbacks = {k: v for k, _, _, _, _, v, _ in SettingsDialog.ENGINEERING_DEFAULTS}
        for key, expected in fallbacks.items():
            assert out[key] == pytest.approx(expected), key

    def test_never_exposes_internal_tuning_constants(self):
        """Scope guard: none of the internal numerical-stability constants
        (span-rebuild tolerance/extend bound, singular-band threshold, …)
        should ever appear as an editable field here."""
        from gui.dialogs import SettingsDialog
        keys = {k for k, *_ in SettingsDialog.ENGINEERING_DEFAULTS}
        forbidden = {"span_eq_tol", "span_max_extend", "reflex_singular_deg",
                    "pi_eps_ang", "merge_max_total_deg"}
        assert keys.isdisjoint(forbidden)


class TestStep3ConfigureUsesDefaults:

    def test_spinboxes_prefilled_from_defaults(self):
        from gui.steps.step3_configure import Step3Configure
        defaults = {
            "default_max_deviation": 3.0, "default_min_radius": 400.0,
            "default_spiral_length": 55.0,
        }
        step3 = Step3Configure(defaults=defaults)
        assert step3._max_dev_spin.value() == pytest.approx(3.0)
        assert step3._min_radius_spin.value() == pytest.approx(400.0)
        assert step3._spiral_length_spin.value() == pytest.approx(55.0)

    def test_no_defaults_reproduces_the_historical_hardcoded_values(self):
        from gui.steps.step3_configure import Step3Configure
        step3 = Step3Configure()
        assert step3._max_dev_spin.value() == pytest.approx(1.0)
        assert step3._min_radius_spin.value() == pytest.approx(150.0)
        assert step3._spiral_length_spin.value() == pytest.approx(20.0)

    def test_emitted_settings_carry_the_no_widget_defaults(self):
        from gui.steps.step3_configure import Step3Configure
        defaults = {
            "default_pi_min_tangent": 90.0, "default_min_tangent_length": 40.0,
            "default_min_line_length": 15.0, "default_min_arc_length": 17.0,
            "default_min_spiral_length": 18.0,
        }
        step3 = Step3Configure(defaults=defaults)
        emitted = []
        step3.config_confirmed.connect(lambda d: emitted.append(d))
        step3._on_next()
        assert len(emitted) == 1
        cfg = emitted[0]
        assert cfg["pi_min_tangent"] == pytest.approx(90.0)
        assert cfg["min_tangent_length"] == pytest.approx(40.0)
        assert cfg["min_line_length"] == pytest.approx(15.0)
        assert cfg["min_arc_length"] == pytest.approx(17.0)
        assert cfg["min_spiral_length"] == pytest.approx(18.0)


def make_split_curve_track(gap_m: float, step=5.0):
    """Two same-rotation 30° arcs separated by a short straight `gap_m` —
    exactly the "Douglas-Peucker split one physical curve in two" shape
    `_find_split_curve_pair`/`pi_min_tangent` governs."""
    pts = [(0.0, 0.0)]
    heading = 0.0

    def add_straight(length):
        start = pts[-1]
        n = max(2, int(length / step))
        for i in range(1, n + 1):
            d = length * i / n
            pts.append((start[0] + d * math.cos(heading),
                        start[1] + d * math.sin(heading)))

    def add_arc(R, deg):
        nonlocal heading
        ang = math.radians(deg)
        cx = pts[-1][0] + R * math.cos(heading + math.pi / 2)
        cy = pts[-1][1] + R * math.sin(heading + math.pi / 2)
        a0 = math.atan2(pts[-1][1] - cy, pts[-1][0] - cx)
        n = max(2, int(abs(ang) * R / step))
        for i in range(1, n + 1):
            a = a0 + ang * i / n
            pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
        heading += ang

    add_straight(500)
    add_arc(600, 15)
    add_straight(gap_m)
    add_arc(600, 15)
    add_straight(500)
    xy = np.array(pts, dtype=float)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    ch = np.concatenate([[0.0], np.cumsum(d)])
    return xy, ch


class TestExtractPiModelPiMinTangent:

    def test_default_reproduces_historical_60m_gate(self):
        """Default (no override) must match calling extract_pi_model with
        no pi_min_tangent at all — the exact pre-Part-E signature/behavior,
        proving this is additive, not a behavior change."""
        xy, ch = make_split_curve_track(gap_m=40.0)   # < the 60 m default gate
        a = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                               spiral_length=20.0, use_spirals=True)
        b = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                               spiral_length=20.0, use_spirals=True,
                               pi_min_tangent=60.0)
        n_curves_a = sum(1 for p in a.pis if not p.omitted)
        n_curves_b = sum(1 for p in b.pis if not p.omitted)
        assert n_curves_a == n_curves_b == 1   # merged into one curve either way

    def test_smaller_gate_leaves_the_two_curves_split(self):
        xy, ch = make_split_curve_track(gap_m=40.0)
        wide_gate = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                       spiral_length=20.0, use_spirals=True,
                                       pi_min_tangent=60.0)
        narrow_gate = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                         spiral_length=20.0, use_spirals=True,
                                         pi_min_tangent=10.0)
        n_wide = sum(1 for p in wide_gate.pis if not p.omitted)
        n_narrow = sum(1 for p in narrow_gate.pis if not p.omitted)
        assert n_wide == 1      # 40 m gap < 60 m gate -> merged
        assert n_narrow == 2    # 40 m gap > 10 m gate -> stays split

    def test_larger_gate_merges_a_gap_the_default_would_leave_split(self):
        xy, ch = make_split_curve_track(gap_m=80.0)   # > the 60 m default gate
        default_gate = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                          spiral_length=20.0, use_spirals=True)
        wider_gate = C.extract_pi_model(xy, ch, tolerance=1.0, min_radius=150.0,
                                        spiral_length=20.0, use_spirals=True,
                                        pi_min_tangent=100.0)
        n_default = sum(1 for p in default_gate.pis if not p.omitted)
        n_wider = sum(1 for p in wider_gate.pis if not p.omitted)
        assert n_default == 2
        assert n_wider == 1
