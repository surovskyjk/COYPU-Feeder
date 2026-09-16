"""
Vertical-profile smoothing algorithms (`geometry.profile`).

Pure numpy — no Qt, no network. The guarantees under test are the ones the
step's read-out promises the user:

  * the simplification stays within Δz tolerance of the terrain,
  * the gradient clamp is *hard* — never exceeded, whatever the terrain does,
  * the clamp is idempotent and end-symmetric, so nudging a slider twice does
    not drift the profile,
  * the filter controls cannot be driven into an exception.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.profile import (  # noqa: E402
    METHOD_GRADIENT, METHOD_WINDOW, VerticalProfile, bridge_nodata,
    build_vertical_elements, clamp_grades, douglas_peucker, empty_stats,
    geom_fingerprint, make_default_profile, rebuild_profile, smooth_window,
    window_samples,
)


def _terrain(grade=0.030, length=10000.0, ds=20.0, noise=0.15, seed=0):
    """A constant-grade hillside with DEM-like noise."""
    rng = np.random.default_rng(seed)
    s = np.arange(0.0, length, ds)
    z = 200.0 + grade * s + rng.normal(0.0, noise, len(s))
    return s, z


def _rolling(s, z):
    """Rolling terrain with several genuine grade breaks."""
    return 300.0 + 40.0 * np.sin(s / 2500.0) + 8.0 * np.sin(s / 430.0)


# ---------------------------------------------------------------------------
# Douglas-Peucker simplification
# ---------------------------------------------------------------------------

class TestDouglasPeucker:

    @pytest.mark.parametrize("tol", [0.05, 0.25, 1.0, 3.0])
    def test_stays_within_tolerance(self, tol):
        s, z = _terrain()
        s_pvi, z_pvi = douglas_peucker(s, z, tol)
        approx = np.interp(s, s_pvi, z_pvi)
        assert np.max(np.abs(approx - z)) <= tol + 1e-9

    def test_looser_tolerance_never_adds_pvis(self):
        s, z = _terrain()
        counts = [len(douglas_peucker(s, z, t)[0])
                  for t in (0.05, 0.25, 1.0, 3.0)]
        assert counts == sorted(counts, reverse=True)

    def test_endpoints_are_always_kept(self):
        s, z = _terrain()
        s_pvi, z_pvi = douglas_peucker(s, z, 5.0)
        assert s_pvi[0] == s[0] and s_pvi[-1] == s[-1]
        assert z_pvi[0] == z[0] and z_pvi[-1] == z[-1]

    def test_exact_line_collapses_to_two_points(self):
        s = np.arange(0.0, 5000.0, 20.0)
        s_pvi, _ = douglas_peucker(s, 100.0 + 0.01 * s, 0.001)
        assert len(s_pvi) == 2

    def test_forced_indices_survive(self):
        s = np.arange(0.0, 5000.0, 20.0)
        z = 100.0 + 0.01 * s                    # a line: nothing else survives
        forced = [50, 120]
        s_pvi, _ = douglas_peucker(s, z, 0.5, forced)
        for i in forced:
            assert np.any(np.isclose(s_pvi, s[i]))

    def test_deep_input_does_not_recurse(self):
        """A 135 km line at 20 m is ~6 750 samples — a recursive RDP on a
        monotonic worst case would blow the interpreter's stack."""
        s = np.arange(0.0, 135_000.0, 20.0)
        z = np.cumsum(np.full(len(s), 0.2))      # strictly convex: max depth
        s_pvi, _ = douglas_peucker(s, z, 0.01)
        assert len(s_pvi) >= 2

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_degenerate_lengths(self, n):
        s = np.arange(float(n))
        s_pvi, z_pvi = douglas_peucker(s, s.copy(), 0.5)
        assert len(s_pvi) == n and len(z_pvi) == n


# ---------------------------------------------------------------------------
# Gradient clamp
# ---------------------------------------------------------------------------

class TestClampGrades:

    @pytest.mark.parametrize("limit_pm", [4.0, 15.0, 25.0, 40.0])
    def test_hard_limit_is_never_exceeded(self, limit_pm):
        s, z = _terrain(grade=0.030)             # 30 permille terrain
        s_pvi, z_pvi = douglas_peucker(s, z, 0.5)
        out = clamp_grades(s_pvi, z_pvi, limit_pm / 1000.0)
        grades = np.abs(np.diff(out) / np.diff(s_pvi))
        assert np.max(grades) <= limit_pm / 1000.0 + 1e-9

    def test_idempotent(self):
        s, z = _terrain(grade=0.030)
        s_pvi, z_pvi = douglas_peucker(s, z, 0.5)
        once = clamp_grades(s_pvi, z_pvi, 0.015)
        twice = clamp_grades(s_pvi, once, 0.015)
        np.testing.assert_allclose(twice, once, atol=1e-9)

    def test_compliant_profile_is_untouched(self):
        s = np.arange(0.0, 5000.0, 100.0)
        z = 100.0 + 0.005 * s                    # 5 permille, well under 15
        np.testing.assert_allclose(clamp_grades(s, z, 0.015), z, atol=1e-9)

    def test_error_is_shared_between_both_ends(self):
        """A forward-only sweep anchors the first PVI and dumps the whole
        accumulated error at the far end. Averaging the two anchored sweeps
        must spread it, so neither endpoint carries all of it."""
        s = np.arange(0.0, 6000.0, 200.0)
        z = 100.0 + 0.030 * s
        out = clamp_grades(s, z, 0.010)
        assert abs(out[0] - z[0]) > 1e-6
        assert abs(out[-1] - z[-1]) > 1e-6

    def test_symmetric_under_reversal(self):
        """Clamping a profile driven from the far end must give the mirror of
        clamping it from the near end — i.e. the result does not depend on
        which way along the line you happen to walk."""
        s, z = _terrain(grade=0.030, length=6000.0, seed=3)
        s_rev = s[-1] - s[::-1]
        z_rev = z[::-1]
        fwd = clamp_grades(s, z, 0.010)
        rev = clamp_grades(s_rev, z_rev, 0.010)
        np.testing.assert_allclose(rev[::-1], fwd, atol=1e-9)

    def test_zero_or_negative_limit_is_a_no_op(self):
        s = np.arange(0.0, 1000.0, 100.0)
        z = np.linspace(0.0, 50.0, len(s))
        np.testing.assert_allclose(clamp_grades(s, z, 0.0), z)
        np.testing.assert_allclose(clamp_grades(s, z, -1.0), z)

    def test_single_point(self):
        out = clamp_grades(np.array([0.0]), np.array([5.0]), 0.015)
        np.testing.assert_allclose(out, [5.0])


# ---------------------------------------------------------------------------
# Window filter
# ---------------------------------------------------------------------------

class TestSmoothWindow:

    def test_removes_a_spike(self):
        s = np.arange(0.0, 2000.0, 20.0)
        z = np.full(len(s), 100.0)
        z[50] = 140.0
        out = smooth_window(z, 150.0, 20.0, poly_order=0)
        assert abs(out[50] - 100.0) < abs(z[50] - 100.0)

    def test_moving_average_at_order_zero(self):
        z = np.array([0.0, 3.0, 0.0, 3.0, 0.0, 3.0, 0.0])
        out = smooth_window(z, 60.0, 20.0, poly_order=0)
        assert len(out) == len(z)
        assert np.all(out >= 0.0) and np.all(out <= 3.0)

    @pytest.mark.parametrize("window_m,ds,n,expect_odd", [
        (150.0, 20.0, 500, True),
        (50.0, 20.0, 500, True),
        (300.0, 5.0, 500, True),
        (10_000.0, 20.0, 51, True),     # window longer than the line
        (150.0, 20.0, 4, True),
    ])
    def test_window_sizing_is_odd_and_bounded(self, window_m, ds, n, expect_odd):
        w = window_samples(window_m, ds, n)
        assert w % 2 == 1 and w >= 3
        assert w <= max(3, n if n % 2 else n - 1)

    @pytest.mark.parametrize("order", [0, 1, 2, 3, 4, 9])
    def test_any_order_returns_a_finite_series(self, order):
        s, z = _terrain(length=1000.0)
        out = smooth_window(z, 150.0, 20.0, poly_order=order)
        assert len(out) == len(z) and np.all(np.isfinite(out))

    @pytest.mark.parametrize("n", [0, 1, 2, 3])
    def test_short_series_survive(self, n):
        z = np.arange(float(n))
        out = smooth_window(z, 150.0, 20.0, poly_order=2)
        assert len(out) == n

    def test_window_longer_than_series_is_a_no_op_or_flat(self):
        z = np.array([1.0, 5.0, 2.0, 8.0, 3.0])
        out = smooth_window(z, 100_000.0, 20.0, poly_order=0)
        assert len(out) == len(z) and np.all(np.isfinite(out))


# ---------------------------------------------------------------------------
# No-data bridging
# ---------------------------------------------------------------------------

class TestBridgeNodata:

    def test_interior_gap_is_linear(self):
        s = np.arange(0.0, 100.0, 10.0)
        z = s.copy()
        z[3:6] = np.nan
        out = bridge_nodata(s, z)
        np.testing.assert_allclose(out, s, atol=1e-9)

    def test_leading_and_trailing_gaps_clamp(self):
        s = np.arange(0.0, 50.0, 10.0)
        z = np.array([np.nan, 10.0, 20.0, 30.0, np.nan])
        out = bridge_nodata(s, z)
        assert np.all(np.isfinite(out))
        assert out[0] == 10.0 and out[-1] == 30.0

    def test_all_nan_becomes_zero_rather_than_nan(self):
        s = np.arange(5.0)
        out = bridge_nodata(s, np.full(5, np.nan))
        assert np.all(out == 0.0)

    def test_clean_series_is_copied_not_aliased(self):
        s = np.arange(5.0)
        z = np.arange(5.0)
        out = bridge_nodata(s, z)
        out[0] = 99.0
        assert z[0] == 0.0


# ---------------------------------------------------------------------------
# Vertical elements
# ---------------------------------------------------------------------------

class TestBuildVerticalElements:

    def test_paracurve_at_every_interior_pvi(self):
        s = np.array([0.0, 1000.0, 2000.0, 3000.0])
        z = np.array([100.0, 110.0, 105.0, 120.0])
        els = build_vertical_elements(s, z, True, 100.0)
        assert sum(1 for e in els if e["type"] == "PVI") == 4
        assert sum(1 for e in els if e["type"] == "ParaCurve") == 2

    def test_disabled_emits_bare_pvis(self):
        s = np.array([0.0, 1000.0, 2000.0])
        z = np.array([100.0, 110.0, 105.0])
        els = build_vertical_elements(s, z, False, 100.0)
        assert all(e["type"] == "PVI" for e in els)

    def test_curve_is_clamped_to_the_shorter_neighbour(self):
        """A ParaCurve straddles its PVI by L/2 each way, so on a 40 m tangent
        a nominal 100 m curve would overlap the next one."""
        s = np.array([0.0, 40.0, 500.0])
        z = np.array([0.0, 1.0, 2.0])
        pc = [e for e in build_vertical_elements(s, z, True, 100.0)
              if e["type"] == "ParaCurve"]
        assert len(pc) == 1 and pc[0]["length"] == 40.0

    def test_stations_are_monotonic(self):
        s = np.array([0.0, 500.0, 1200.0, 1800.0])
        z = np.array([10.0, 20.0, 15.0, 25.0])
        els = build_vertical_elements(s, z, True, 100.0)
        stations = [e["station"] for e in els]
        assert stations == sorted(stations)

    def test_empty(self):
        assert build_vertical_elements(np.zeros(0), np.zeros(0)) == []


# ---------------------------------------------------------------------------
# rebuild_profile end to end
# ---------------------------------------------------------------------------

class TestRebuildProfile:

    @pytest.mark.parametrize("method", [METHOD_GRADIENT, METHOD_WINDOW])
    def test_both_methods_respect_the_grade_limit(self, method):
        s, z = _terrain(grade=0.030)
        p = VerticalProfile(s=s, z_raw=z, method=method, max_grade_pm=15.0)
        rebuild_profile(p)
        assert p.stats["max_grade_pm"] <= 15.0 + 1e-6

    @pytest.mark.parametrize("method", [METHOD_GRADIENT, METHOD_WINDOW])
    def test_both_methods_produce_pvis(self, method):
        s = np.arange(0.0, 20000.0, 20.0)
        p = VerticalProfile(s=s, z_raw=_rolling(s, None), method=method)
        rebuild_profile(p)
        assert p.stats["n_pvi"] >= 2
        assert sum(1 for e in p.pvis if e["type"] == "PVI") == p.stats["n_pvi"]

    def test_cut_and_fill_are_reported_when_terrain_is_too_steep(self):
        s, z = _terrain(grade=0.030, length=10000.0, noise=0.0)
        p = VerticalProfile(s=s, z_raw=z, max_grade_pm=15.0)
        rebuild_profile(p)
        assert p.stats["max_cut"] > 10.0 and p.stats["max_fill"] > 10.0

    def test_gentle_terrain_needs_no_earthwork(self):
        s, z = _terrain(grade=0.004, noise=0.0)
        p = VerticalProfile(s=s, z_raw=z, max_grade_pm=15.0, z_tol=0.5)
        rebuild_profile(p)
        assert p.stats["max_cut"] < 0.5 and p.stats["max_fill"] < 0.5

    def test_repeated_rebuild_is_stable(self):
        s, z = _terrain()
        p = VerticalProfile(s=s, z_raw=z)
        rebuild_profile(p)
        first = p.z_design.copy()
        rebuild_profile(p)
        np.testing.assert_allclose(p.z_design, first, atol=1e-12)

    def test_nodata_is_counted_and_bridged(self):
        s, z = _terrain()
        z[100:150] = np.nan
        p = VerticalProfile(s=s, z_raw=z)
        rebuild_profile(p)
        assert p.stats["n_nodata"] == 50
        assert np.all(np.isfinite(p.z_design))

    @pytest.mark.parametrize("n", [0, 1])
    def test_too_few_samples_yields_empty_but_complete_stats(self, n):
        p = VerticalProfile(s=np.arange(float(n)), z_raw=np.arange(float(n)))
        rebuild_profile(p)
        assert p.pvis == []
        assert set(p.stats) == set(empty_stats())

    def test_mismatched_array_lengths_do_not_raise(self):
        p = VerticalProfile(s=np.arange(10.0), z_raw=np.arange(3.0))
        rebuild_profile(p)
        assert p.pvis == []

    def test_derived_fields_are_not_aliased_to_the_raw_samples(self):
        s, z = _terrain(length=2000.0)
        p = VerticalProfile(s=s, z_raw=z)
        rebuild_profile(p)
        p.z_design[0] = 99999.0
        assert p.z_raw[0] != 99999.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_geom_fingerprint(self):
        els = [{"length": 100.0}, {"length": 50.5}]
        assert geom_fingerprint(els) == (2, 150.5)
        assert geom_fingerprint(None) == (0, 0.0)

    def test_make_default_profile_reads_the_step3_settings(self):
        p = make_default_profile(
            [{"length": 10.0}],
            {"sample_interval": 35.0, "vc_length": 220.0,
             "elevation_source": "opentopodata"},
        )
        assert p.sample_interval == 35.0
        assert p.vc_length == 220.0
        assert p.elevation_source == "opentopodata"
        assert p.geom_fingerprint == (1, 10.0)
        assert p.connections == []
        assert p.method == METHOD_GRADIENT

    def test_make_default_profile_falls_back_like_the_export_worker(self):
        p = make_default_profile([], {})
        assert (p.sample_interval, p.vc_length, p.elevation_source) == (
            20.0, 100.0, "cuzk_dmr5g")

    def test_has_samples(self):
        assert not VerticalProfile().has_samples
        assert VerticalProfile(s=np.arange(3.0), z_raw=np.arange(3.0)).has_samples
        assert not VerticalProfile(s=np.arange(3.0), z_raw=np.arange(2.0)).has_samples
