"""
Horizontal geometry fitting.
Converts a projected 2D polyline into a sequence of geometric primitives:
  Line, Circular Arc, Clothoid (Euler) Spiral.
Output elements are dicts ready for LandXML serialisation.
"""

import numpy as np
from scipy.optimize import least_squares
from .curvature import (
    compute_curvature,
    smooth_curvature,
    compute_chainages,
    segment_curvature,
    ElementType,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fit_alignment(
    xy: np.ndarray,
    smooth_window: int = 21,
    line_tol: float = 0.001,
    arc_tol: float = 0.0002,
    min_element_length: float = 10.0,
) -> list[dict]:
    """
    Fit geometric elements to a 2D polyline.

    Parameters
    ----------
    xy : (N, 2) array of projected (x, y) coordinates in metres.
    smooth_window : Savitzky-Golay window size for curvature smoothing.
    line_tol : curvature threshold (1/m) below which a segment is a Line.
    arc_tol : max κ variation (1/m) for a segment to be an Arc.
    min_element_length : minimum element length in metres.

    Returns
    -------
    List of element dicts, each containing:
      type, start_station, end_station, length,
      and type-specific fields (radius, rot, spiral params, etc.)
    """
    kappa = compute_curvature(xy)
    kappa_smooth = smooth_curvature(kappa, window=smooth_window)
    chainages = compute_chainages(xy)
    segments = segment_curvature(
        kappa_smooth, chainages,
        line_tol=line_tol,
        arc_tol=arc_tol,
        min_length=min_element_length,
    )

    elements = []
    for seg in segments:
        el = _fit_element(seg, xy, kappa_smooth, chainages)
        if el is not None:
            elements.append(el)

    return elements


# ---------------------------------------------------------------------------
# Element fitting
# ---------------------------------------------------------------------------

def _fit_element(
    seg: dict,
    xy: np.ndarray,
    kappa: np.ndarray,
    chainages: np.ndarray,
) -> dict | None:
    i0 = seg["start_idx"]
    i1 = seg["end_idx"]
    pts = xy[i0:i1 + 1]
    sta_start = float(chainages[i0])
    sta_end = float(chainages[i1])
    length = sta_end - sta_start

    if length < 0.1 or len(pts) < 2:
        return None

    etype = seg["type"]

    if etype == ElementType.LINE:
        return _fit_line(pts, sta_start, length)
    elif etype == ElementType.ARC:
        return _fit_arc(pts, sta_start, length, seg["mean_kappa"])
    elif etype == ElementType.SPIRAL:
        return _fit_spiral(pts, sta_start, length, seg["kappa_start"], seg["kappa_end"])
    return None


def _fit_line(pts: np.ndarray, sta_start: float, length: float) -> dict:
    """
    Least-squares line fit via SVD/PCA so the element is a clean straight
    line through the point cloud, not just first-to-last raw OSM nodes.
    """
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    direction_vec = vt[0]  # unit vector along principal axis

    # Project all points onto the principal axis
    projections = (pts - centroid) @ direction_vec

    # Preserve traversal direction (match the sign of the original polyline)
    polyline_dir = pts[-1] - pts[0]
    if np.dot(polyline_dir, direction_vec) < 0:
        direction_vec = -direction_vec
        projections = -projections

    t_start = projections[0]
    t_end = projections[-1]
    start_pt = (centroid + t_start * direction_vec).tolist()
    end_pt = (centroid + t_end * direction_vec).tolist()
    actual_length = abs(t_end - t_start)
    direction = float(np.arctan2(direction_vec[1], direction_vec[0]))

    return {
        "type": "Line",
        "sta_start": sta_start,
        "length": actual_length if actual_length > 0.1 else length,
        "start": start_pt,
        "end": end_pt,
        "direction_rad": direction,
    }


def _fit_arc(
    pts: np.ndarray,
    sta_start: float,
    length: float,
    mean_kappa: float,
) -> dict:
    if abs(mean_kappa) < 1e-9:
        return _fit_line(pts, sta_start, length)

    radius = abs(1.0 / mean_kappa)
    rot = "ccw" if mean_kappa > 0 else "cw"

    # Least-squares circle fit (Kåsa method)
    cx, cy, r_fit = _fit_circle_kasa(pts)
    if r_fit is not None and 0 < r_fit < 1e6:
        radius = r_fit

    # Chord
    chord = float(np.linalg.norm(pts[-1] - pts[0]))

    return {
        "type": "Arc",
        "sta_start": sta_start,
        "length": length,
        "start": pts[0].tolist(),
        "end": pts[-1].tolist(),
        "center": [float(cx), float(cy)] if cx is not None else None,
        "radius": radius,
        "rot": rot,
        "chord": chord,
    }


def _fit_spiral(
    pts: np.ndarray,
    sta_start: float,
    length: float,
    kappa_start: float,
    kappa_end: float,
) -> dict:
    """
    Fit a clothoid (Euler spiral / Cornu spiral).
    Characterised by linearly varying curvature κ(s) = κ0 + (κ1-κ0)*s/L.
    """
    r_start = abs(1.0 / kappa_start) if abs(kappa_start) > 1e-9 else float("inf")
    r_end = abs(1.0 / kappa_end) if abs(kappa_end) > 1e-9 else float("inf")

    # Clothoid parameter A² = R * L
    # Use end radius (the tighter end)
    r_min = min(r_start, r_end)
    A2 = r_min * length
    A = float(np.sqrt(A2))

    # Rotation direction based on mean curvature
    mean_k = (kappa_start + kappa_end) / 2
    rot = "ccw" if mean_k > 0 else "cw"

    return {
        "type": "Spiral",
        "sta_start": sta_start,
        "length": length,
        "start": pts[0].tolist(),
        "end": pts[-1].tolist(),
        "radius_start": r_start,
        "radius_end": r_end,
        "clothoid_A": A,
        "rot": rot,
    }


# ---------------------------------------------------------------------------
# Circle fitting (Kåsa algebraic method)
# ---------------------------------------------------------------------------

def _fit_circle_kasa(
    pts: np.ndarray,
) -> tuple[float | None, float | None, float | None]:
    """Algebraic circle fit. Returns (cx, cy, radius) or (None, None, None)."""
    if len(pts) < 3:
        return None, None, None
    x = pts[:, 0]
    y = pts[:, 1]
    A = np.column_stack([x, y, np.ones(len(x))])
    b = x ** 2 + y ** 2
    try:
        result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None, None, None
    cx = result[0] / 2
    cy = result[1] / 2
    r = float(np.sqrt(result[2] + cx ** 2 + cy ** 2))
    return float(cx), float(cy), r
