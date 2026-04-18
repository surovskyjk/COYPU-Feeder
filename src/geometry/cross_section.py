"""
Cross-section elevation analysis.

For each station along a fitted alignment, samples terrain elevation at the
central point and at left/right perpendicular offsets, then computes the
elevation difference (cross-slope).  Uses the same OpenTopoData API already
used by the vertical-profile pipeline.

Public API
----------
sample_alignment_with_headings(elements, interval) -> list of (sta, x, y, h)
compute_cross_section(elements, work_epsg, offset_m, interval_m, progress_cb)
    -> list of result dicts
diff_to_color(diff_abs, threshold) -> "#rrggbb" string
export_csv(results, filepath)
"""

from __future__ import annotations

import csv
import math
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# HSL → hex helper
# ---------------------------------------------------------------------------

def _hsl_to_hex(h: float, s: float, l: float) -> str:
    """
    Convert HSL (h in degrees 0-360, s/l in percent 0-100) to "#rrggbb".
    Standard algorithm — no external dependency.
    """
    s /= 100.0
    l /= 100.0
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = l - c / 2.0

    if   0   <= h < 60:  r1, g1, b1 = c, x, 0
    elif 60  <= h < 120: r1, g1, b1 = x, c, 0
    elif 120 <= h < 180: r1, g1, b1 = 0, c, x
    elif 180 <= h < 240: r1, g1, b1 = 0, x, c
    elif 240 <= h < 300: r1, g1, b1 = x, 0, c
    else:                r1, g1, b1 = c, 0, x

    r = int(round((r1 + m) * 255))
    g = int(round((g1 + m) * 255))
    b = int(round((b1 + m) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Clothoid (Euler spiral) position at arbitrary arc length
# ---------------------------------------------------------------------------

def _clothoid_xy_at(s: float, A: float) -> tuple[float, float]:
    """
    Local (x, y) of a clothoid at arc-length *s* from the spiral origin.
    A = sqrt(L_total × R_end) is the clothoid parameter.

    Uses the Fresnel-series approximation; accurate to < 1 mm for s < A (
    which is always the case for railway spirals where L ≤ 2πR).
    """
    if A < 1e-9:
        return s, 0.0
    A2 = A * A
    A4 = A2 * A2
    A6 = A4 * A2
    A8 = A6 * A2
    x = s - s**5 / (40.0 * A4) + s**9 / (3456.0 * A8)
    y = s**3 / (6.0 * A2) - s**7 / (336.0 * A6) + s**11 / (42240.0 * A4 * A6)
    return x, y


# ---------------------------------------------------------------------------
# Heading at a point inside a Spiral element
# ---------------------------------------------------------------------------

def _spiral_entry_heading(el: dict, prev_heading: float | None = None) -> float:
    """Reconstruct the entry heading of a Spiral element from geometry."""
    start = el.get("start", [0.0, 0.0])
    end   = el.get("end",   [0.0, 0.0])
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord_heading = math.atan2(dy, dx)
    # The spiral chord heading ≈ entry heading + half the total tangent sweep,
    # but we approximate by deriving from the element's own start/end geometry.
    # For the purposes of offset-point placement, use the chord direction as a
    # conservative fallback; if caller passes prev_heading, prefer that.
    return prev_heading if prev_heading is not None else chord_heading


# ---------------------------------------------------------------------------
# Core: sample alignment at regular intervals
# ---------------------------------------------------------------------------

def sample_alignment_with_headings(
    elements: list[dict],
    interval: float = 5.0,
) -> list[tuple[float, float, float, float]]:
    """
    Return ``[(station, x, y, heading_rad), …]`` at every *interval* metres
    along the alignment, including the very first point.

    *heading_rad* is the tangent direction (positive = east), identical to the
    direction used by the constructive forward build.

    Handles Line, Arc, and Spiral elements correctly.
    """
    if not elements:
        return []

    result: list[tuple[float, float, float, float]] = []
    prev_heading: float | None = None

    for el in elements:
        etype    = el.get("type", "Line")
        sta0     = float(el.get("sta_start", 0.0))
        length   = float(el.get("length",   0.0))
        if length < 1e-9:
            continue

        start = el.get("start", [0.0, 0.0])
        end   = el.get("end",   [0.0, 0.0])

        # ── LINE ────────────────────────────────────────────────────────
        if etype == "Line":
            heading = float(el.get("direction_rad",
                                   math.atan2(end[1] - start[1],
                                              end[0] - start[0])))
            cos_h, sin_h = math.cos(heading), math.sin(heading)

            # First station of the alignment (sta=0 → first element only)
            t_start = 0.0 if not result else interval
            # Snap to interval grid relative to sta0
            t_grid = math.ceil(
                (sta0 if not result else sta0 + interval) / interval
            ) * interval - sta0
            if not result:
                # Emit the very first point
                result.append((sta0, float(start[0]), float(start[1]), heading))
                t_grid = interval

            t = t_grid
            while t <= length + 1e-9:
                x = float(start[0]) + t * cos_h
                y = float(start[1]) + t * sin_h
                result.append((sta0 + t, x, y, heading))
                t += interval

            prev_heading = heading

        # ── ARC ─────────────────────────────────────────────────────────
        elif etype == "Arc":
            R      = float(el.get("radius", 300.0))
            rot    = el.get("rot", "ccw")
            sign   = 1.0 if rot == "ccw" else -1.0
            center = el.get("center", [0.0, 0.0])
            cx, cy = float(center[0]), float(center[1])

            a_start = math.atan2(float(start[1]) - cy, float(start[0]) - cx)
            # Entry heading = tangent at start = radius angle + sign*pi/2
            entry_h = a_start + sign * math.pi / 2.0

            if not result:
                result.append((sta0, float(start[0]), float(start[1]), entry_h))

            # Grid start inside element
            t_grid = math.ceil((sta0 + 1e-9) / interval) * interval - sta0
            if t_grid <= 0:
                t_grid = interval

            t = t_grid
            while t <= length + 1e-9:
                alpha   = t / R                          # angular progress
                angle   = a_start + sign * alpha
                x       = cx + R * math.cos(angle)
                y       = cy + R * math.sin(angle)
                heading = angle + sign * math.pi / 2.0
                result.append((sta0 + t, x, y, heading))
                t += interval

            # Exit heading
            a_end        = a_start + sign * (length / R)
            prev_heading = a_end + sign * math.pi / 2.0

        # ── SPIRAL ──────────────────────────────────────────────────────
        elif etype == "Spiral":
            rot      = el.get("rot", "ccw")
            sign     = 1.0 if rot == "ccw" else -1.0
            r_start  = el.get("radius_start", float("inf"))
            r_end    = el.get("radius_end",   float("inf"))
            A        = float(el.get("clothoid_A", 0.0))

            # Determine which end is the finite radius
            if math.isfinite(r_end) and not math.isfinite(r_start):
                # Entry spiral (∞ → R): curvature increases from 0
                R_finite  = float(r_end)
                entry_h   = _spiral_entry_heading(el, prev_heading)
                cos_h, sin_h = math.cos(entry_h), math.sin(entry_h)

                if not result:
                    result.append((sta0, float(start[0]), float(start[1]), entry_h))

                t_grid = math.ceil((sta0 + 1e-9) / interval) * interval - sta0
                if t_grid <= 0:
                    t_grid = interval

                t = t_grid
                while t <= length + 1e-9:
                    x_loc, y_loc = _clothoid_xy_at(t, A)
                    x = float(start[0]) + cos_h * x_loc - sign * sin_h * y_loc
                    y = float(start[1]) + sin_h * x_loc + sign * cos_h * y_loc
                    A2 = A * A
                    heading = entry_h + sign * t * t / (2.0 * A2)
                    result.append((sta0 + t, x, y, heading))
                    t += interval

                A2 = A * A
                prev_heading = entry_h + sign * length * length / (2.0 * A2)

            elif math.isfinite(r_start) and not math.isfinite(r_end):
                # Exit spiral (R → ∞): curvature decreases from R
                R_finite  = float(r_start)
                # For exit spiral, reparametrize from arc-end backward:
                # The clothoid is run "in reverse" — position at t from exit
                # start means s_eff = L - t within the full clothoid.
                entry_h   = _spiral_entry_heading(el, prev_heading)
                cos_h, sin_h = math.cos(entry_h), math.sin(entry_h)

                if not result:
                    result.append((sta0, float(start[0]), float(start[1]), entry_h))

                t_grid = math.ceil((sta0 + 1e-9) / interval) * interval - sta0
                if t_grid <= 0:
                    t_grid = interval

                t = t_grid
                while t <= length + 1e-9:
                    # s_eff = distance from the clothoid origin (at exit end)
                    s_eff  = length - t
                    x_loc, y_loc = _clothoid_xy_at(s_eff, A)
                    # Mirror x_loc in the exit direction: we travel backwards
                    # through the clothoid, so use (length - x_loc_at_L, ...)
                    x_end_loc, y_end_loc = _clothoid_xy_at(length, A)
                    dx_loc = x_end_loc - x_loc
                    dy_loc = sign * (y_loc - 0.0)      # mirror y
                    # Rotate into world frame
                    x = float(start[0]) + cos_h * (x_end_loc - x_loc) + sign * sin_h * (y_end_loc - y_loc)
                    y = float(start[1]) + sin_h * (x_end_loc - x_loc) - sign * cos_h * (y_end_loc - y_loc)
                    A2      = A * A
                    heading = entry_h + sign * s_eff * s_eff / (2.0 * A2)
                    result.append((sta0 + t, x, y, heading))
                    t += interval

                prev_heading = entry_h   # exit heading at spiral end ≈ entry heading of next element

            else:
                # Fallback: treat as line between start and end
                dx = float(end[0]) - float(start[0])
                dy = float(end[1]) - float(start[1])
                heading = math.atan2(dy, dx)
                cos_h, sin_h = math.cos(heading), math.sin(heading)

                if not result:
                    result.append((sta0, float(start[0]), float(start[1]), heading))

                t_grid = math.ceil((sta0 + 1e-9) / interval) * interval - sta0
                if t_grid <= 0:
                    t_grid = interval
                t = t_grid
                while t <= length + 1e-9:
                    frac = t / length
                    x    = float(start[0]) + frac * dx
                    y    = float(start[1]) + frac * dy
                    result.append((sta0 + t, x, y, heading))
                    t += interval
                prev_heading = heading

    return result


# ---------------------------------------------------------------------------
# Color mapping
# ---------------------------------------------------------------------------

def diff_to_color(diff_abs: float, threshold: float) -> str:
    """
    Map |elevation difference| to an HTML hex colour.

    * ``diff_abs = 0``          → bright green  (#00c853)
    * ``diff_abs = threshold``  → red-orange    (#ff6d00)
    * ``diff_abs > threshold``  → deep red      (#b71c1c)

    Uses HSL colour space: hue 120° (green) → 0° (red), saturation 90%,
    lightness 38%.
    """
    if threshold < 1e-9:
        threshold = 1e-9
    ratio = min(diff_abs / threshold, 1.0)   # 0 → 1
    hue   = 120.0 * (1.0 - ratio)            # 120° → 0°
    return _hsl_to_hex(hue, 90.0, 38.0)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_csv(results: list[dict], filepath: str) -> None:
    """
    Write cross-section results to a CSV file.

    Columns: station_m, centre_elev_m, diff_left_m, diff_right_m
    Rows with any None value are skipped.
    """
    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "station_m", "centre_elev_m",
            "diff_left_m", "diff_right_m",
        ])
        for r in results:
            ec   = r.get("elev_centre")
            dl   = r.get("diff_left")
            dr   = r.get("diff_right")
            if ec is None or dl is None or dr is None:
                continue
            writer.writerow([
                f"{r['station']:.3f}",
                f"{ec:.3f}",
                f"{dl:.3f}",
                f"{dr:.3f}",
            ])


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_cross_section(
    elements:    list[dict],
    work_epsg:   int,
    offset_m:    float,
    interval_m:  float = 5.0,
    progress_cb  = None,
) -> list[dict]:
    """
    Compute cross-section elevation data for a fitted alignment.

    Parameters
    ----------
    elements   : fitted alignment elements in *work_epsg* (UTM metres)
    work_epsg  : EPSG code of the metric working CRS
    offset_m   : perpendicular offset distance in metres
    interval_m : sampling interval along the alignment (default 5 m)
    progress_cb: callable(current: int, total: int) for progress reporting

    Returns
    -------
    list of dicts, one per station:
        station, x_utm, y_utm, lat, lon,
        elev_centre,
        lat_left, lon_left, elev_left, diff_left,
        lat_right, lon_right, elev_right, diff_right
    """
    from geometry.elevation   import sample_elevations
    from geometry.projection  import projected_to_wgs84

    stations = sample_alignment_with_headings(elements, interval_m)
    if not stations:
        return []

    # Build flat UTM list: [centre₀, left₀, right₀, centre₁, …]
    all_utm: list[tuple[float, float]] = []
    for sta, x, y, h in stations:
        sin_h, cos_h = math.sin(h), math.cos(h)
        all_utm.append((x,                   y                  ))  # centre
        all_utm.append((x - offset_m * sin_h, y + offset_m * cos_h))  # left
        all_utm.append((x + offset_m * sin_h, y - offset_m * cos_h))  # right

    # Reproject entire batch to WGS84 in one call
    all_wgs84 = projected_to_wgs84(all_utm, work_epsg)

    # Sample elevations for all 3N points (batched by elevation.py)
    all_elevs: list[Optional[float]] = sample_elevations(all_wgs84)

    # Unpack into result dicts
    results: list[dict] = []
    n = len(stations)
    for i, (sta, x, y, h) in enumerate(stations):
        ec = all_elevs[3 * i]
        el = all_elevs[3 * i + 1]
        er = all_elevs[3 * i + 2]
        diff_l = (el - ec) if (el is not None and ec is not None) else None
        diff_r = (er - ec) if (er is not None and ec is not None) else None
        results.append({
            "station":    sta,
            "x_utm":      x,
            "y_utm":      y,
            "lat":        all_wgs84[3 * i][0],
            "lon":        all_wgs84[3 * i][1],
            "elev_centre": ec,
            "lat_left":   all_wgs84[3 * i + 1][0],
            "lon_left":   all_wgs84[3 * i + 1][1],
            "elev_left":  el,
            "diff_left":  diff_l,
            "lat_right":  all_wgs84[3 * i + 2][0],
            "lon_right":  all_wgs84[3 * i + 2][1],
            "elev_right": er,
            "diff_right": diff_r,
        })
        if progress_cb:
            progress_cb(i + 1, n)

    return results
