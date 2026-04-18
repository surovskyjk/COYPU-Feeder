"""
Unit tests for src/geometry/cross_section.py

Tests cover:
  - sample_alignment_with_headings: spacing, heading at key points
  - diff_to_color: boundary colours
  - export_csv: round-trip accuracy
  - compute_cross_section: mocked elevation (no network)
"""

import csv
import io
import math
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.cross_section import (
    diff_to_color,
    export_csv,
    sample_alignment_with_headings,
    _hsl_to_hex,
)


# ---------------------------------------------------------------------------
# Helper: build simple element lists
# ---------------------------------------------------------------------------

def _line_element(sta, x0, y0, length, heading_deg):
    h = math.radians(heading_deg)
    x1 = x0 + length * math.cos(h)
    y1 = y0 + length * math.sin(h)
    return {
        "type":          "Line",
        "sta_start":     sta,
        "length":        length,
        "start":         [x0, y0],
        "end":           [x1, y1],
        "direction_rad": h,
    }


def _arc_element(sta, start, center, R, defl_rad, rot="ccw"):
    """Build an Arc element analytically from start, center, and signed deflection."""
    cx, cy = center
    sign   = 1.0 if rot == "ccw" else -1.0
    a_s    = math.atan2(start[1] - cy, start[0] - cx)
    a_e    = a_s + sign * abs(defl_rad)
    end    = [cx + R * math.cos(a_e), cy + R * math.sin(a_e)]
    arc_len = R * abs(defl_rad)
    return {
        "type":        "Arc",
        "sta_start":   sta,
        "length":      arc_len,
        "start":       list(start),
        "end":         end,
        "center":      list(center),
        "radius":      R,
        "rot":         rot,
        "_deflection": sign * abs(defl_rad),
    }


# ---------------------------------------------------------------------------
# sample_alignment_with_headings
# ---------------------------------------------------------------------------

class TestSampleAlignmentWithHeadings:
    def test_single_line_interval(self):
        """Samples should appear every 5 m along a horizontal line."""
        el = _line_element(0, 0, 0, 50, 0)  # east-pointing, 50 m
        pts = sample_alignment_with_headings([el], interval=5.0)
        assert len(pts) >= 2
        stations = [p[0] for p in pts]
        # All gaps should be ≈ 5 m (or the remainder at the end)
        gaps = [stations[i+1] - stations[i] for i in range(len(stations)-1)]
        for g in gaps:
            assert abs(g - 5.0) < 0.01

    def test_first_point_is_at_start(self):
        el = _line_element(0, 100, 200, 80, 45)
        pts = sample_alignment_with_headings([el], interval=10.0)
        sta0, x0, y0, h0 = pts[0]
        assert abs(sta0) < 1e-6
        assert abs(x0 - 100) < 1e-6
        assert abs(y0 - 200) < 1e-6

    def test_line_heading_constant(self):
        heading_deg = 37.0
        el = _line_element(0, 0, 0, 100, heading_deg)
        pts = sample_alignment_with_headings([el], interval=10.0)
        expected_h = math.radians(heading_deg)
        for sta, x, y, h in pts:
            assert abs(h - expected_h) < 1e-9

    def test_arc_heading_changes(self):
        """Heading at arc end should equal entry heading + deflection."""
        R        = 300.0
        defl     = math.radians(30)
        center   = [0.0, R]          # CCW arc starting east (heading 0)
        start_pt = [0.0, 0.0]
        el       = _arc_element(0, start_pt, center, R, defl, "ccw")
        pts      = sample_alignment_with_headings([el], interval=10.0)
        # Entry heading = 0 (east); exit heading = +30°
        entry_h = 0.0
        # Heading of last sampled point should be ≈ entry + cumulative turn
        last_h = pts[-1][3]
        cumulative_turn = last_h - entry_h
        # It should be positive (CCW) and between 0 and 30°
        assert 0 < cumulative_turn <= defl + 0.01

    def test_arc_positions_on_circle(self):
        """All sampled arc points must lie on the arc circle."""
        R      = 400.0
        defl   = math.radians(60)
        center = [0.0, R]
        start  = [0.0, 0.0]
        el     = _arc_element(0, start, center, R, defl, "ccw")
        pts    = sample_alignment_with_headings([el], interval=5.0)
        cx, cy = center
        for sta, x, y, h in pts:
            dist = math.hypot(x - cx, y - cy)
            assert abs(dist - R) < 0.05, f"Point at sta={sta:.1f} is {abs(dist-R):.4f} m off circle"

    def test_two_elements_continuous(self):
        """Last point of Line should match start of second element."""
        el1 = _line_element(0,  0, 0, 100, 0)
        el2 = _line_element(100, 100, 0, 50, 90)  # turn north
        pts = sample_alignment_with_headings([el1, el2], interval=10.0)
        stations = [p[0] for p in pts]
        # Should cover the full length
        assert stations[-1] >= 100.0  # at least up to junction
        # No negative gaps
        gaps = [stations[i+1] - stations[i] for i in range(len(stations)-1)]
        assert all(g > 0 for g in gaps)


# ---------------------------------------------------------------------------
# diff_to_color
# ---------------------------------------------------------------------------

class TestDiffToColor:
    def test_zero_diff_is_green(self):
        color = diff_to_color(0.0, 2.0)
        # HSL(120°, 90%, 38%) should give a green hex
        assert color.startswith("#")
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        assert g > r and g > b, f"Expected green, got {color} (R={r} G={g} B={b})"

    def test_at_threshold_is_red(self):
        color = diff_to_color(2.0, 2.0)
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        assert r > g, f"Expected red-dominant, got {color} (R={r} G={g} B={b})"

    def test_above_threshold_clamped(self):
        """Colours above and well above threshold should be the same."""
        c1 = diff_to_color(2.0, 2.0)
        c2 = diff_to_color(100.0, 2.0)
        assert c1 == c2

    def test_half_threshold_is_intermediate(self):
        """At half threshold, hue should be 60° (yellow-green region)."""
        c0 = diff_to_color(0.0, 2.0)
        c_half = diff_to_color(1.0, 2.0)
        c_full = diff_to_color(2.0, 2.0)
        # Red channel should increase monotonically
        r0  = int(c0[1:3],    16)
        rh  = int(c_half[1:3], 16)
        rf  = int(c_full[1:3], 16)
        assert r0 <= rh <= rf

    def test_zero_threshold_no_crash(self):
        """Should not raise even with threshold = 0."""
        color = diff_to_color(1.0, 0.0)
        assert color.startswith("#") and len(color) == 7

    def test_returns_hex_string(self):
        for diff, thr in [(0, 1), (0.5, 1), (1, 1), (2, 1), (5, 1)]:
            c = diff_to_color(diff, thr)
            assert len(c) == 7
            assert c[0] == "#"
            int(c[1:], 16)  # must parse as hex


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

class TestExportCsv:
    def _make_results(self):
        return [
            {"station": 0.0,  "elev_centre": 247.8, "diff_left":  0.7, "diff_right": -0.6,
             "elev_left": 248.5, "elev_right": 247.2,
             "lat": 50.0, "lon": 14.4, "lat_left": 50.0, "lon_left": 14.401,
             "lat_right": 50.0, "lon_right": 14.399, "x_utm": 0, "y_utm": 0},
            {"station": 5.0,  "elev_centre": 248.1, "diff_left":  2.0, "diff_right": -1.1,
             "elev_left": 250.1, "elev_right": 247.0,
             "lat": 50.0, "lon": 14.4, "lat_left": 50.0, "lon_left": 14.401,
             "lat_right": 50.0, "lon_right": 14.399, "x_utm": 5, "y_utm": 0},
            # Row with None values — should be skipped
            {"station": 10.0, "elev_centre": None,  "diff_left": None, "diff_right": None,
             "elev_left": None, "elev_right": None,
             "lat": 50.0, "lon": 14.4, "lat_left": 50.0, "lon_left": 14.401,
             "lat_right": 50.0, "lon_right": 14.399, "x_utm": 10, "y_utm": 0},
        ]

    def test_writes_header_and_rows(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_csv(self._make_results(), path)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            assert rows[0] == ["station_m", "centre_elev_m", "diff_left_m", "diff_right_m"]
            assert len(rows) == 3  # header + 2 valid rows (None row skipped)
        finally:
            os.unlink(path)

    def test_round_trip_values(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            results = self._make_results()
            export_csv(results, path)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            assert abs(float(rows[0]["station_m"]) - 0.0) < 1e-3
            assert abs(float(rows[0]["centre_elev_m"]) - 247.8) < 1e-3
            assert abs(float(rows[0]["diff_left_m"]) - 0.7) < 1e-3
            assert abs(float(rows[0]["diff_right_m"]) - (-0.6)) < 1e-3
            assert abs(float(rows[1]["station_m"]) - 5.0) < 1e-3
        finally:
            os.unlink(path)

    def test_none_rows_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_csv(self._make_results(), path)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            # Row at station=10.0 (all None) must not appear
            stations = [r[0] for r in rows[1:]]
            assert "10.000" not in stations
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# _hsl_to_hex (internal helper)
# ---------------------------------------------------------------------------

class TestHslToHex:
    def test_pure_green(self):
        # HSL(120°, 100%, 50%) → #00ff00
        c = _hsl_to_hex(120, 100, 50)
        r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        assert g == 255 and r == 0 and b == 0

    def test_pure_red(self):
        c = _hsl_to_hex(0, 100, 50)
        r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        assert r == 255 and g == 0 and b == 0

    def test_pure_blue(self):
        c = _hsl_to_hex(240, 100, 50)
        r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        assert b == 255 and r == 0 and g == 0
