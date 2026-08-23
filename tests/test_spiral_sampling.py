"""Equivalence test for the vectorized `_sample_spiral_heading`.

The numpy rewrite must reproduce the old pure-Python midpoint-rule loop.
np.add.accumulate keeps the exact left-to-right accumulation order, so the
only permitted difference is a cos/sin ULP (numpy's SIMD trig vs libm),
bounded here at 1e-9 m — far below display or station-snap relevance.
"""

import math

import numpy as np
import pytest

from geometry.alignment import _sample_spiral_heading


def _sample_spiral_heading_ref(el, interval, prev_heading, start, end, length):
    """The pre-vectorization implementation, kept verbatim as the oracle."""
    r_s  = el.get("radius_start", float("inf"))
    r_e  = el.get("radius_end",   float("inf"))
    rot  = el.get("rot", "ccw")
    sign = 1.0 if rot == "ccw" else -1.0

    k_s = 0.0 if math.isinf(r_s) else 1.0 / float(r_s)
    k_e = 0.0 if math.isinf(r_e) else 1.0 / float(r_e)

    theta_0 = prev_heading if prev_heading is not None else math.atan2(
        float(end[1] - start[1]), float(end[0] - start[0])
    )

    n_int = max(50, min(200, int(length / max(interval * 0.1, 0.2))))
    ds    = length / n_int

    pts_raw = [tuple(start)]
    x, y = float(start[0]), float(start[1])

    for i in range(1, n_int + 1):
        s_mid    = (i - 0.5) * ds
        theta_m  = theta_0 + sign * (k_s * s_mid +
                                     (k_e - k_s) * s_mid ** 2 / (2.0 * length))
        x += ds * math.cos(theta_m)
        y += ds * math.sin(theta_m)
        pts_raw.append((x, y))

    pts_raw[-1] = (float(end[0]), float(end[1]))

    step = max(1, int(round(interval / ds)))
    pts  = pts_raw[::step]
    if len(pts) < 2 or pts[-1] != pts_raw[-1]:
        pts.append(pts_raw[-1])

    h_end = theta_0 + sign * (k_s * length + (k_e - k_s) * length / 2.0)
    return pts, h_end


CASES = [
    # (radius_start, radius_end, rot, length, interval, prev_heading)
    (float("inf"), 600.0,  "ccw",  80.0,  2.0,  0.3),        # entry spiral
    (600.0, float("inf"),  "cw",   80.0,  2.0, -1.2),        # exit spiral
    (float("inf"), 250.0,  "cw",   40.0,  5.0,  2.9),        # tight entry
    (900.0, 300.0,         "ccw", 120.0, 20.0,  0.0),        # compound
    (float("inf"), 1500.0, "ccw", 300.0,  2.0,  None),       # heading from chord
    (400.0, float("inf"),  "cw",   15.0, 10.0,  1.0),        # short, coarse
]


@pytest.mark.parametrize("r_s,r_e,rot,length,interval,heading", CASES)
def test_vectorized_matches_reference(r_s, r_e, rot, length, interval, heading):
    start = np.array([712345.678901, 1023456.789012])
    # end only matters as the snap target + chord-heading fallback
    end   = start + np.array([length * 0.98, length * 0.11])
    el = {"radius_start": r_s, "radius_end": r_e, "rot": rot}

    pts_new, h_new = _sample_spiral_heading(
        el, interval, heading, start, end, length)
    pts_ref, h_ref = _sample_spiral_heading_ref(
        el, interval, heading, start, end, length)

    assert h_new == h_ref
    assert len(pts_new) == len(pts_ref)
    diffs = [math.hypot(a[0] - b[0], a[1] - b[1])
             for a, b in zip(pts_new, pts_ref)]
    assert max(diffs) < 1e-9, f"max sample deviation {max(diffs):.3e} m"
    # Endpoint snap must be exact in both
    assert pts_new[-1] == (float(end[0]), float(end[1]))
