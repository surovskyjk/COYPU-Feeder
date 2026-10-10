"""
Manual direct connections (tunnels / viaducts) in the vertical profile.

The contract the step promises: inside a range the profile is exactly the
straight line between the boundary elevations, and no smoothing method or
gradient clamp may put a kink back into it.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.profile import (  # noqa: E402
    METHOD_GRADIENT, METHOD_WINDOW, DirectConnection, VerticalProfile,
    apply_connections, connection_sample_mask, normalise_connections,
    rebuild_profile,
)


def _series(length=10000.0, ds=20.0, seed=0):
    rng = np.random.default_rng(seed)
    s = np.arange(0.0, length, ds)
    z = 300.0 + 25.0 * np.sin(s / 1800.0) + rng.normal(0.0, 0.4, len(s))
    return s, z


def _max_second_difference(s, z, mask):
    """0 for a perfectly straight span on an even grid."""
    sub_s, sub_z = s[mask], z[mask]
    if len(sub_s) < 3:
        return 0.0
    grades = np.diff(sub_z) / np.diff(sub_s)
    return float(np.max(np.abs(np.diff(grades))))


# ---------------------------------------------------------------------------
# normalise_connections
# ---------------------------------------------------------------------------

class TestNormalise:

    def test_sorts_by_start(self):
        out = normalise_connections(
            [DirectConnection(800.0, 900.0), DirectConnection(100.0, 200.0)],
            0.0, 1000.0)
        assert [c.s_start for c in out] == [100.0, 800.0]

    def test_reversed_range_is_swapped(self):
        out = normalise_connections([DirectConnection(900.0, 400.0)], 0.0, 1000.0)
        assert (out[0].s_start, out[0].s_end) == (400.0, 900.0)

    def test_clipped_to_the_alignment(self):
        out = normalise_connections(
            [DirectConnection(-500.0, 5000.0)], 0.0, 1000.0)
        assert (out[0].s_start, out[0].s_end) == (0.0, 1000.0)

    def test_fully_outside_is_dropped(self):
        assert normalise_connections(
            [DirectConnection(2000.0, 3000.0)], 0.0, 1000.0) == []

    @pytest.mark.parametrize("a,b", [(500.0, 500.0), (500.0, 500.0000001)])
    def test_degenerate_span_is_dropped(self, a, b):
        """Zero-length (or sub-micron) only. A *reversed* range is not
        degenerate — it is swapped; see test_reversed_range_is_swapped."""
        assert normalise_connections(
            [DirectConnection(a, b)], 0.0, 1000.0) == []

    def test_overlapping_ranges_merge(self):
        out = normalise_connections(
            [DirectConnection(100.0, 400.0), DirectConnection(300.0, 700.0)],
            0.0, 1000.0)
        assert len(out) == 1
        assert (out[0].s_start, out[0].s_end) == (100.0, 700.0)

    def test_touching_ranges_merge(self):
        out = normalise_connections(
            [DirectConnection(100.0, 400.0), DirectConnection(400.0, 700.0)],
            0.0, 1000.0)
        assert len(out) == 1 and out[0].s_end == 700.0

    def test_disjoint_ranges_are_kept_apart(self):
        out = normalise_connections(
            [DirectConnection(100.0, 200.0), DirectConnection(400.0, 500.0)],
            0.0, 1000.0)
        assert len(out) == 2

    def test_merge_keeps_a_label(self):
        out = normalise_connections(
            [DirectConnection(100.0, 400.0, ""),
             DirectConnection(300.0, 700.0, "tunnel")], 0.0, 1000.0)
        assert out[0].label == "tunnel"

    def test_input_list_is_not_mutated(self):
        src = [DirectConnection(900.0, 400.0, "x")]
        normalise_connections(src, 0.0, 1000.0)
        assert (src[0].s_start, src[0].s_end) == (900.0, 400.0)

    def test_empty_and_none(self):
        assert normalise_connections([], 0.0, 100.0) == []
        assert normalise_connections(None, 0.0, 100.0) == []


# ---------------------------------------------------------------------------
# apply_connections
# ---------------------------------------------------------------------------

class TestApplyConnections:

    def test_span_becomes_exactly_linear(self):
        s, z = _series()
        conns = [DirectConnection(2000.0, 3000.0)]
        out = apply_connections(s, z, conns)
        mask = connection_sample_mask(s, conns)
        assert _max_second_difference(s, out, mask) < 1e-9

    def test_interpolates_between_the_boundary_elevations(self):
        s = np.arange(0.0, 1000.0, 10.0)
        z = np.zeros(len(s))
        z[20] = 10.0      # s = 200
        z[60] = 30.0      # s = 600
        out = apply_connections(s, z, [DirectConnection(200.0, 600.0)])
        mid = np.searchsorted(s, 400.0)
        assert out[20] == pytest.approx(10.0)
        assert out[60] == pytest.approx(30.0)
        assert out[mid] == pytest.approx(20.0)

    def test_outside_the_span_is_untouched(self):
        s, z = _series()
        out = apply_connections(s, z, [DirectConnection(2000.0, 3000.0)])
        outside = ~connection_sample_mask(s, [DirectConnection(2000.0, 3000.0)])
        np.testing.assert_allclose(out[outside], z[outside])

    def test_multiple_disjoint_spans(self):
        s, z = _series()
        conns = [DirectConnection(1000.0, 2000.0),
                 DirectConnection(6000.0, 7000.0)]
        out = apply_connections(s, z, conns)
        for c in conns:
            m = connection_sample_mask(s, [c])
            assert _max_second_difference(s, out, m) < 1e-9

    def test_does_not_mutate_the_input(self):
        s, z = _series()
        original = z.copy()
        apply_connections(s, z, [DirectConnection(2000.0, 3000.0)])
        np.testing.assert_allclose(z, original)

    def test_no_connections_is_a_copy(self):
        s, z = _series()
        out = apply_connections(s, z, [])
        np.testing.assert_allclose(out, z)
        out[0] = 1e6
        assert z[0] != 1e6

    def test_empty_series(self):
        out = apply_connections(np.zeros(0), np.zeros(0),
                                [DirectConnection(0.0, 10.0)])
        assert len(out) == 0


# ---------------------------------------------------------------------------
# Survival through the full pipeline
# ---------------------------------------------------------------------------

class TestThroughRebuild:

    @pytest.mark.parametrize("method", [METHOD_GRADIENT, METHOD_WINDOW])
    def test_connector_stays_straight_in_the_override_series(self, method):
        s, z = _series()
        p = VerticalProfile(s=s, z_raw=z, method=method,
                            connections=[DirectConnection(2000.0, 3500.0)])
        rebuild_profile(p)
        mask = connection_sample_mask(s, p.connections)
        assert _max_second_difference(s, p.z_override, mask) < 1e-9

    @pytest.mark.parametrize("method", [METHOD_GRADIENT, METHOD_WINDOW])
    def test_boundaries_become_pvis(self, method):
        s, z = _series()
        p = VerticalProfile(s=s, z_raw=z, method=method, z_tol=3.0,
                            connections=[DirectConnection(2000.0, 3500.0)])
        rebuild_profile(p)
        stations = np.array([e["station"] for e in p.pvis
                             if e["type"] == "PVI"])
        for boundary in (2000.0, 3500.0):
            # The forced vertex is the nearest sample, within half a step.
            assert np.min(np.abs(stations - boundary)) <= p.sample_interval

    def test_design_span_has_no_interior_kink(self):
        """With a loose tolerance the connector must survive as a single
        tangent in the design profile, not be resampled into a curve."""
        s, z = _series()
        p = VerticalProfile(s=s, z_raw=z, z_tol=2.0, max_grade_pm=40.0,
                            connections=[DirectConnection(2000.0, 3500.0)])
        rebuild_profile(p)
        interior = (s > 2000.0 + p.sample_interval) & (s < 3500.0 - p.sample_interval)
        assert _max_second_difference(s, p.z_design, interior) < 1e-6

    def test_a_tunnel_still_obeys_the_gradient_limit(self):
        """A connector is just a two-PVI span, so the clamp reaches it — you
        cannot escape the grade limit by declaring a tunnel."""
        s = np.arange(0.0, 4000.0, 20.0)
        z = 100.0 + 0.050 * s                        # 50 permille
        p = VerticalProfile(s=s, z_raw=z, max_grade_pm=15.0,
                            connections=[DirectConnection(500.0, 3500.0)])
        rebuild_profile(p)
        assert p.stats["max_grade_pm"] <= 15.0 + 1e-6

    def test_connection_spanning_the_whole_line(self):
        s, z = _series(length=2000.0)
        p = VerticalProfile(s=s, z_raw=z, max_grade_pm=40.0, z_tol=1.0,
                            connections=[DirectConnection(0.0, 1980.0)])
        rebuild_profile(p)
        assert _max_second_difference(s, p.z_override,
                                      np.ones(len(s), bool)) < 1e-9

    def test_out_of_range_connection_is_ignored_not_fatal(self):
        s, z = _series(length=2000.0)
        p = VerticalProfile(s=s, z_raw=z,
                            connections=[DirectConnection(50_000.0, 60_000.0)])
        rebuild_profile(p)
        assert p.stats["n_pvi"] >= 2
        np.testing.assert_allclose(p.z_override, z, atol=1e-9)

    def test_connection_over_a_nodata_gap(self):
        s, z = _series()
        z[110:140] = np.nan                          # inside the tunnel
        p = VerticalProfile(s=s, z_raw=z,
                            connections=[DirectConnection(2000.0, 3500.0)])
        rebuild_profile(p)
        assert np.all(np.isfinite(p.z_design))
        mask = connection_sample_mask(s, p.connections)
        assert _max_second_difference(s, p.z_override, mask) < 1e-9


class TestSampleMask:

    def test_marks_only_the_covered_samples(self):
        s = np.arange(0.0, 100.0, 10.0)
        mask = connection_sample_mask(s, [DirectConnection(30.0, 60.0)])
        assert list(np.flatnonzero(mask)) == [3, 4, 5, 6]

    def test_empty(self):
        s = np.arange(0.0, 100.0, 10.0)
        assert not connection_sample_mask(s, []).any()
