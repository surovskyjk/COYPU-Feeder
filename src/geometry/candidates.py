"""
Multi-algorithm candidate alignment generator — redesigned.

Root cause of the old approach: ``_build_elements_c1`` propagated arc deflection
angles measured from noisy OSM points, producing cumulative position drift that
grew progressively along the alignment.

The new algorithms use **position-anchored, tangent-point junctions**:
each arc is fitted independently to its OSM point cluster; each line direction
is fitted independently; junctions are solved geometrically (O(1), no drift).

Algorithm IDs
-------------
``segment_fit``     Segment & Fit   — curvature segmentation + tangent-point assembly
``dp_segment``      DP Segmentation — Imai-Iri DP, globally optimal element count
``progressive_mc``  Progressive MC  — greedy insertion + simulated annealing
``raw``             OSM Polyline    — one Line per vertex pair (unchanged)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class CandidateAlignment:
    algorithm_id:  str
    label:         str
    elements:      list
    max_deviation: float = 0.0
    rmse:          float = 0.0
    n_elements:    int   = 0
    color_hex:     str   = "#ffffff"
    geo_wgs84:     list  = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal segment dataclass
# ---------------------------------------------------------------------------

@dataclass
class _Segment:
    seg_type:   str    # "Line" | "Arc"
    start_idx:  int    # index into xy (inclusive)
    end_idx:    int    # index into xy (inclusive)
    R_median:   float  # fitted radius; math.inf for Line
    rot:        str    # "ccw" | "cw"
    deflection: float  # signed total heading change (rad)


# Minimum arc deflection below which a curved segment is treated as a line
_MIN_ARC_DEFLECTION_RAD = math.radians(5.0)


# ---------------------------------------------------------------------------
# Candidate evaluation  (unchanged public API)
# ---------------------------------------------------------------------------

def evaluate_candidate(
    elements:      list[dict],
    xy:            np.ndarray,
    chainages:     np.ndarray,
    check_interval: float = 5.0,
) -> dict:
    """
    Compute quality metrics for a list of fitted elements vs the OSM polyline.

    Returns dict with keys: max_deviation (float), rmse (float), n_elements (int)
    """
    from geometry.alignment import max_deviation_element

    if not elements or len(xy) < 2:
        return {"max_deviation": 0.0, "rmse": 0.0, "n_elements": len(elements)}

    max_dev = 0.0
    sq_sum  = 0.0
    sq_cnt  = 0

    for el in elements:
        sta0   = el.get("sta_start", 0.0)
        sta1   = sta0 + el.get("length", 0.0)
        mask   = (chainages >= sta0 - 0.1) & (chainages <= sta1 + 0.1)
        xy_seg = xy[mask]
        if len(xy_seg) < 2:
            continue
        dev = max_deviation_element(el, xy_seg, check_interval)
        if dev > max_dev:
            max_dev = dev

    for i, pt in enumerate(xy):
        ch = float(chainages[i])
        min_dist = float("inf")
        for el in elements:
            sta0 = el.get("sta_start", 0.0)
            sta1 = sta0 + el.get("length", 0.0)
            if ch < sta0 - 1.0 or ch > sta1 + 1.0:
                continue
            d = _point_to_element_dist(pt, el)
            if d < min_dist:
                min_dist = d
        if math.isfinite(min_dist):
            sq_sum += min_dist * min_dist
            sq_cnt += 1

    rmse = math.sqrt(sq_sum / sq_cnt) if sq_cnt > 0 else 0.0
    return {
        "max_deviation": float(max_dev),
        "rmse":          float(rmse),
        "n_elements":    len(elements),
    }


def _point_to_element_dist(pt: np.ndarray, el: dict) -> float:
    """Perpendicular distance from point to the nearest point on a Line/Arc element."""
    etype = el.get("type", "Line")
    if etype == "Line":
        start = np.array(el["start"])
        end   = np.array(el["end"])
        seg   = end - start
        seg_len = float(np.linalg.norm(seg))
        if seg_len < 1e-9:
            return float(np.linalg.norm(pt - start))
        t = float(np.dot(pt - start, seg)) / (seg_len * seg_len)
        t = max(0.0, min(1.0, t))
        return float(np.linalg.norm(pt - (start + t * seg)))
    elif etype == "Arc":
        center = np.array(el["center"])
        r      = el.get("radius", 1.0)
        return abs(float(np.linalg.norm(pt - center)) - r)
    else:
        start = np.array(el.get("start", [0.0, 0.0]))
        end   = np.array(el.get("end",   [0.0, 0.0]))
        seg   = end - start
        seg_len = float(np.linalg.norm(seg))
        if seg_len < 1e-9:
            return float(np.linalg.norm(pt - start))
        t = float(np.dot(pt - start, seg)) / (seg_len * seg_len)
        t = max(0.0, min(1.0, t))
        return float(np.linalg.norm(pt - (start + t * seg)))


# ---------------------------------------------------------------------------
# Candidate generator
# ---------------------------------------------------------------------------

class CandidateGenerator:
    """Run all four candidate algorithms on a projected XY polyline."""

    COLORS = ["#ff9800", "#66bb6a", "#42a5f5", "#e040fb"]
    LABELS = {
        "segment_fit":    "Segment & Fit",
        "dp_segment":     "DP Segmentation",
        "progressive_mc": "Progressive MC",
        "raw":            "OSM Polyline",
    }

    def __init__(self, xy: np.ndarray, chainages: np.ndarray, settings: dict):
        self.xy             = xy
        self.chainages      = chainages
        self.settings       = settings
        self.min_radius     = settings.get("min_radius",       150.0)
        self.smooth_window  = settings.get("smooth_window",    21)
        self.max_deviation  = settings.get("max_deviation",    0.5)
        self.check_interval = settings.get("check_interval",   5.0)
        self.merge_pct      = settings.get("merge_radius_pct", 15.0)
        self.time_budget_s  = settings.get("time_budget_s",    60.0)

    def run_all(self) -> list[CandidateAlignment]:
        results = []
        algo_ids = ["segment_fit", "dp_segment", "progressive_mc", "raw"]
        for algo_id, color in zip(algo_ids, self.COLORS):
            try:
                c = self._run_one(algo_id)
                c.color_hex = color
            except Exception:
                c = CandidateAlignment(
                    algorithm_id=algo_id,
                    label=self.LABELS[algo_id],
                    elements=[],
                    color_hex=color,
                )
            results.append(c)
        return results

    def _run_one(self, algo_id: str, progress_cb=None, preview_cb=None) -> CandidateAlignment:
        if algo_id == "segment_fit":
            return self._run_segment_fit(progress_cb=progress_cb)
        elif algo_id == "dp_segment":
            return self._run_dp(progress_cb=progress_cb)
        elif algo_id == "progressive_mc":
            return self._run_progressive_mc(progress_cb=progress_cb, preview_cb=preview_cb)
        elif algo_id == "raw":
            return self._run_raw()
        else:
            raise ValueError(f"Unknown algorithm: {algo_id!r}")

    # ------------------------------------------------------------------
    # Algorithm 1 — Segment & Fit
    # ------------------------------------------------------------------

    def _run_segment_fit(self, progress_cb=None) -> CandidateAlignment:
        """
        Curvature segmentation + independent primitive fitting + tangent-point
        C0/C1 assembly.  Fast and reliable for clean OSM data.
        """
        from geometry.alignment import _fit_circle_kasa
        from geometry.curvature import compute_curvature, smooth_curvature

        def _p(msg):
            if progress_cb:
                progress_cb(msg)

        xy, chainages = self.xy, self.chainages
        N = len(xy)

        if N < 2:
            return CandidateAlignment("segment_fit", "Segment & Fit", [])
        if N == 2:
            elements = _two_point_line(xy, chainages)
            metrics  = evaluate_candidate(elements, xy, chainages, self.check_interval)
            return CandidateAlignment("segment_fit", "Segment & Fit", elements, **metrics)

        LINE_TOL = 0.001

        _p("Computing curvature profile…")
        kappa        = compute_curvature(xy)
        kappa_smooth = smooth_curvature(kappa, window=self.smooth_window)

        micro_types: list[str] = []
        micro_sign:  list[int] = []

        for i in range(1, N - 1):
            k = float(kappa_smooth[i])
            if abs(k) < LINE_TOL:
                micro_types.append("Line")
                micro_sign.append(+1)
            else:
                micro_types.append("Arc")
                micro_sign.append(+1 if k > 0.0 else -1)

        _p("Segmenting by curvature sign…")
        segments = _merge_segments_by_sign(micro_types, micro_sign, xy)
        if not segments:
            elements = _two_point_line(xy, chainages)
            metrics  = evaluate_candidate(elements, xy, chainages, self.check_interval)
            return CandidateAlignment("segment_fit", "Segment & Fit", elements, **metrics)

        _p("Fitting circle arcs (Kasa)…")
        # Kasa refinement
        for seg in segments:
            if seg.seg_type == "Arc":
                pts = xy[seg.start_idx: seg.end_idx + 1]
                if len(pts) >= 3:
                    cx, cy, r = _fit_circle_kasa(pts)
                    if cx is not None and r is not None and self.min_radius <= r < 1e6:
                        seg.R_median = float(r)

        # Sagitta filter
        changed = False
        for seg in segments:
            if seg.seg_type == "Arc":
                if _arc_sagitta(seg.R_median, seg.deflection) < 1.5:
                    seg.seg_type = "Line"
                    seg.R_median = math.inf
                    changed = True
        if changed:
            segments = _merge_consecutive_lines(segments, xy)

        # Stage-2 radius merge
        _p("Merging similar-radius arcs…")
        segments = _merge_arcs_by_radius(segments, xy, self.merge_pct)

        # Re-run Kasa after merge
        for seg in segments:
            if seg.seg_type == "Arc":
                pts = xy[seg.start_idx: seg.end_idx + 1]
                if len(pts) >= 3:
                    cx, cy, r = _fit_circle_kasa(pts)
                    if cx is not None and r is not None and self.min_radius <= r < 1e6:
                        seg.R_median = float(r)

        _p("Assembling elements with tangent junctions…")
        elements = _connect_segments_tangent(segments, xy, chainages, self.min_radius)
        _p("Evaluating quality…")
        metrics  = evaluate_candidate(elements, xy, chainages, self.check_interval)
        return CandidateAlignment("segment_fit", "Segment & Fit", elements, **metrics)

    # ------------------------------------------------------------------
    # Algorithm 2 — DP Segmentation
    # ------------------------------------------------------------------

    def _run_dp(self, progress_cb=None) -> CandidateAlignment:
        """
        Imai-Iri dynamic programming: finds the globally optimal segmentation
        (minimum SSE + regularisation × element count).
        O(N²) cost table; practical for N < 1000 OSM nodes.
        """
        def _p(msg):
            if progress_cb:
                progress_cb(msg)

        # lam scales with merge_pct: higher tolerance → fewer, longer elements
        lam = max(1.0, self.merge_pct) ** 2 * 0.5
        _p("Building cost table…")
        segments = _dp_segmentation(
            self.xy, self.chainages, lam, self.min_radius,
            progress_cb=progress_cb,
            time_budget_s=self.time_budget_s,
        )
        _p("Assembling elements with tangent junctions…")
        elements = _connect_segments_tangent(
            segments, self.xy, self.chainages, self.min_radius
        )
        _p("Evaluating quality…")
        metrics = evaluate_candidate(elements, self.xy, self.chainages, self.check_interval)
        return CandidateAlignment("dp_segment", "DP Segmentation", elements, **metrics)

    # ------------------------------------------------------------------
    # Algorithm 3 — Progressive MC
    # ------------------------------------------------------------------

    def _run_progressive_mc(self, progress_cb=None, preview_cb=None) -> CandidateAlignment:
        """
        Progressive element insertion with simulated annealing.
        Guaranteed to reach max_deviation if feasible.
        """
        elements = _progressive_mc_build(
            self.xy, self.chainages,
            max_deviation=self.max_deviation,
            min_radius=self.min_radius,
            merge_pct=self.merge_pct,
            time_budget_s=self.time_budget_s,
            progress_cb=progress_cb,
            preview_cb=preview_cb,
        )
        if progress_cb:
            progress_cb("Evaluating quality…")
        metrics = evaluate_candidate(elements, self.xy, self.chainages, self.check_interval)
        return CandidateAlignment("progressive_mc", "Progressive MC", elements, **metrics)

    # ------------------------------------------------------------------
    # Raw OSM polyline (unchanged)
    # ------------------------------------------------------------------

    def _run_raw(self) -> CandidateAlignment:
        """One Line element per consecutive OSM vertex pair. No fitting, no C1."""
        xy, chainages = self.xy, self.chainages
        elements: list[dict] = []
        sta = 0.0
        for i in range(len(xy) - 1):
            p0, p1  = xy[i], xy[i + 1]
            seg_len = float(chainages[i + 1] - chainages[i])
            if seg_len < 1e-9:
                continue
            elements.append({
                "type":          "Line",
                "sta_start":     sta,
                "length":        seg_len,
                "start":         p0.tolist(),
                "end":           p1.tolist(),
                "direction_rad": math.atan2(
                    float(p1[1] - p0[1]), float(p1[0] - p0[0])
                ),
            })
            sta += seg_len

        metrics = evaluate_candidate(elements, xy, chainages, self.check_interval)
        return CandidateAlignment("raw", "OSM Polyline", elements, **metrics)


# ---------------------------------------------------------------------------
# Shared geometry helpers (used by multiple algorithms)
# ---------------------------------------------------------------------------

def _two_point_line(xy: np.ndarray, chainages: np.ndarray) -> list[dict]:
    """Trivial single-Line element for degenerate 2-point inputs."""
    seg_len = float(chainages[-1] - chainages[0])
    heading = math.atan2(float(xy[-1, 1] - xy[0, 1]), float(xy[-1, 0] - xy[0, 0]))
    return [{
        "type": "Line", "sta_start": 0.0, "length": seg_len,
        "start": xy[0].tolist(), "end": xy[-1].tolist(),
        "direction_rad": heading,
    }]


def _segment_deflection(pts: np.ndarray) -> float:
    """Signed total heading change across a point sequence (sum of turning angles)."""
    if len(pts) < 3:
        return 0.0
    total = 0.0
    for i in range(len(pts) - 2):
        e1 = pts[i + 1] - pts[i]
        e2 = pts[i + 2] - pts[i + 1]
        cross = float(e1[0] * e2[1] - e1[1] * e2[0])
        dot   = float(e1[0] * e2[0] + e1[1] * e2[1])
        total += math.atan2(cross, dot)
    return total


def _arc_sagitta(R: float, defl_rad: float) -> float:
    """Maximum lateral deviation of a circular arc from its chord."""
    if not math.isfinite(R) or R <= 0:
        return 0.0
    return R * (1.0 - math.cos(abs(defl_rad) / 2.0))


# ---------------------------------------------------------------------------
# Segment-merge helpers (used by Algorithm 1)
# ---------------------------------------------------------------------------

def _merge_segments_by_sign(
    micro_types: list[str],
    micro_sign:  list[int],
    xy:          np.ndarray,
) -> list[_Segment]:
    """Stage-1: group by type + sign; demote shallow arcs; merge adjacent Lines."""
    n_micro = len(micro_types)
    if n_micro == 0:
        return []

    segments: list[_Segment] = []
    seg_start    = 0
    current_type = micro_types[0]
    current_sign = micro_sign[0]

    def _flush(seg_end_micro: int) -> None:
        xi0  = seg_start
        xi1  = min(seg_end_micro + 1, len(xy) - 1)
        pts  = xy[xi0: xi1 + 1]
        defl = _segment_deflection(pts)
        rot  = "ccw" if current_sign >= 0 else "cw"
        if current_type == "Line" or abs(defl) < _MIN_ARC_DEFLECTION_RAD:
            typ, R_med = "Line", math.inf
        else:
            typ, R_med = "Arc", math.inf
        segments.append(_Segment(
            seg_type=typ, start_idx=xi0, end_idx=xi1,
            R_median=R_med, rot=rot, deflection=defl,
        ))

    for m in range(1, n_micro):
        if (micro_types[m] != current_type
                or (micro_types[m] == "Arc" and micro_sign[m] != current_sign)):
            _flush(m - 1)
            seg_start    = m
            current_type = micro_types[m]
            current_sign = micro_sign[m]

    _flush(n_micro - 1)

    # Boundary corrections
    if segments:
        segments[0].start_idx = 0
        segments[-1].end_idx  = len(xy) - 1

    return _merge_consecutive_lines(segments, xy)


def _merge_consecutive_lines(segments: list[_Segment], xy: np.ndarray) -> list[_Segment]:
    """Merge adjacent Line segments into one."""
    merged: list[_Segment] = []
    for seg in segments:
        if merged and seg.seg_type == "Line" and merged[-1].seg_type == "Line":
            merged[-1].end_idx  = seg.end_idx
            pts = xy[merged[-1].start_idx: merged[-1].end_idx + 1]
            merged[-1].deflection = _segment_deflection(pts)
        else:
            merged.append(seg)
    return merged


def _merge_arcs_by_radius(
    segments:         list[_Segment],
    xy:               np.ndarray,
    merge_radius_pct: float,
) -> list[_Segment]:
    """Stage-2: merge adjacent same-sign Arcs with similar Kasa radii."""
    tol     = merge_radius_pct / 100.0
    changed = True
    while changed:
        changed = False
        merged: list[_Segment] = []
        i = 0
        while i < len(segments):
            seg = segments[i]
            if (i + 1 < len(segments)
                    and seg.seg_type == "Arc"
                    and segments[i + 1].seg_type == "Arc"
                    and seg.rot == segments[i + 1].rot):
                nxt = segments[i + 1]
                Ra, Rb = seg.R_median, nxt.R_median
                if (Ra > 0 and Rb > 0
                        and math.isfinite(Ra) and math.isfinite(Rb)
                        and abs(Ra - Rb) / max(Ra, Rb) <= tol):
                    La = float(np.linalg.norm(xy[seg.end_idx] - xy[seg.start_idx]))
                    Lb = float(np.linalg.norm(xy[nxt.end_idx] - xy[nxt.start_idx]))
                    den = La + Lb if (La + Lb) > 0 else 1.0
                    R_m = (Ra * La + Rb * Lb) / den
                    pts  = xy[seg.start_idx: nxt.end_idx + 1]
                    defl = _segment_deflection(pts)
                    merged.append(_Segment(
                        seg_type="Arc", start_idx=seg.start_idx, end_idx=nxt.end_idx,
                        R_median=R_m, rot=seg.rot, deflection=defl,
                    ))
                    i += 2
                    changed = True
                    continue
            merged.append(seg)
            i += 1
        segments = merged
    return segments


# ---------------------------------------------------------------------------
# Core: position-anchored tangent-point junction assembly
# ---------------------------------------------------------------------------

def _fit_line_direction(xy: np.ndarray, i0: int, i1: int) -> float:
    """
    Orthogonal regression via SVD. Returns dominant heading (rad) aligned with
    overall travel direction (xy[i0] → xy[i1]).
    """
    pts = xy[i0: i1 + 1]
    if len(pts) < 2:
        i0c = max(0, i0 - 1)
        i1c = min(len(xy) - 1, i1 + 1)
        d = xy[i1c] - xy[i0c]
        return math.atan2(float(d[1]), float(d[0]))
    mean    = pts.mean(axis=0)
    centred = pts - mean
    try:
        _, _, vt = np.linalg.svd(centred, full_matrices=False)
        d = vt[0]
    except np.linalg.LinAlgError:
        d = pts[-1] - pts[0]
    # Align sign with travel direction
    overall = pts[-1] - pts[0]
    if np.dot(d, overall) < 0:
        d = -d
    norm = math.hypot(float(d[0]), float(d[1]))
    if norm < 1e-9:
        return 0.0
    return math.atan2(float(d[1]) / norm, float(d[0]) / norm)


def _fit_arc_robust(
    xy: np.ndarray, i0: int, i1: int, min_radius: float
) -> tuple[float, float, float] | None:
    """
    Fit a circle to OSM[i0..i1] using Kasa algebraic method.
    Returns (cx, cy, R) or None if the fit is degenerate.
    """
    from geometry.alignment import _fit_circle_kasa

    pts = xy[i0: i1 + 1]
    if len(pts) >= 3:
        cx, cy, r = _fit_circle_kasa(pts)
        if cx is not None and r is not None and math.isfinite(r) and min_radius <= r < 1e6:
            return float(cx), float(cy), float(r)
    # Fallback: first / middle / last only
    if len(pts) >= 3:
        sub = np.array([pts[0], pts[len(pts) // 2], pts[-1]])
        cx, cy, r = _fit_circle_kasa(sub)
        if cx is not None and r is not None and math.isfinite(r) and min_radius <= r < 1e6:
            return float(cx), float(cy), float(r)
    return None


def _arc_line_tangent_junction(
    cx: float, cy: float, R: float, rot: str, heading_rad: float
) -> tuple[float, float]:
    """
    The unique point on circle (cx, cy, R) where the tangent direction equals
    heading_rad in the direction of arc travel.

    Derivation (CCW arc, increasing θ):
        tangent at θ = (−sin θ, cos θ).  Set equal to (cos φ, sin φ):
        ⟹  θ = φ − π/2  ⟹  J = (cx + R sin φ, cy − R cos φ)

    CW arc (decreasing θ):
        tangent at θ = (sin θ, −cos θ).  Set equal to (cos φ, sin φ):
        ⟹  θ = π/2 + φ  ⟹  J = (cx − R sin φ, cy + R cos φ)
    """
    sp = math.sin(heading_rad)
    cp = math.cos(heading_rad)
    if rot == "ccw":
        return cx + R * sp, cy - R * cp
    else:
        return cx - R * sp, cy + R * cp


def _connect_segments_tangent(
    segments:   list[_Segment],
    xy:         np.ndarray,
    chainages:  np.ndarray,
    min_radius: float,
) -> list[dict]:
    """
    Build a C0+C1 element list using tangent-point junctions.

    Each arc is independently Kasa-fitted to its OSM points; each line
    direction is independently PCA-fitted.  Junction points are solved
    analytically — no forward angle propagation, no accumulated drift.

    Anchors: first element starts at xy[0]; last element ends at xy[-1].
    """
    if not segments:
        return []

    n = len(segments)

    # ── Step 1: fit geometric primitives ─────────────────────────────────
    fitted: list[dict] = []
    for seg in segments:
        if seg.seg_type == "Arc":
            result = _fit_arc_robust(xy, seg.start_idx, seg.end_idx, min_radius)
            if result is None:
                # Degenerate — treat as line
                h = _fit_line_direction(xy, seg.start_idx, seg.end_idx)
                fitted.append({"type": "Line", "heading": h, "seg": seg})
            else:
                cx, cy, R = result
                fitted.append({
                    "type": "Arc", "cx": cx, "cy": cy, "R": R,
                    "rot": seg.rot, "seg": seg,
                })
        else:
            h = _fit_line_direction(xy, seg.start_idx, seg.end_idx)
            fitted.append({"type": "Line", "heading": h, "seg": seg})

    # ── Step 2: compute junction points ──────────────────────────────────
    junctions: list[np.ndarray | None] = [None] * (n + 1)
    junctions[0] = np.array(xy[0],  dtype=float)
    junctions[n] = np.array(xy[-1], dtype=float)

    for j in range(1, n):
        left  = fitted[j - 1]
        right = fitted[j]

        if left["type"] == "Line" and right["type"] == "Arc":
            # Junction = point on right arc where tangent = left line heading.
            # Refine heading iteratively (converges in 2-3 steps).
            phi = left["heading"]
            prev = junctions[j - 1]
            for _ in range(5):
                jx, jy = _arc_line_tangent_junction(
                    right["cx"], right["cy"], right["R"], right["rot"], phi
                )
                if prev is not None:
                    dx = jx - float(prev[0])
                    dy = jy - float(prev[1])
                    dist = math.hypot(dx, dy)
                    if dist > 1e-6:
                        new_phi = math.atan2(dy, dx)
                        if abs(new_phi - phi) < 1e-5:
                            break
                        phi = new_phi
                    else:
                        break
                else:
                    break
            junctions[j] = np.array([jx, jy])

        elif left["type"] == "Arc" and right["type"] == "Line":
            # Junction = point on left arc where tangent = right line heading.
            phi = right["heading"]
            jx, jy = _arc_line_tangent_junction(
                left["cx"], left["cy"], left["R"], left["rot"], phi
            )
            junctions[j] = np.array([jx, jy])

        else:
            # Arc-Arc or Line-Line: fall back to OSM boundary point
            bdy = fitted[j - 1]["seg"].end_idx
            junctions[j] = np.array(xy[bdy], dtype=float)

    # ── Step 3: validate junctions (bounding-box sanity check) ───────────
    for j in range(1, n):
        if junctions[j] is None:
            bdy = fitted[j - 1]["seg"].end_idx
            junctions[j] = np.array(xy[bdy], dtype=float)
            continue
        # Must lie within the OSM bounding box (with 500 m margin)
        seg_l = fitted[j - 1]["seg"]
        seg_r = fitted[j]["seg"]
        all_pts = xy[seg_l.start_idx: seg_r.end_idx + 1]
        if len(all_pts) == 0:
            continue
        margin = 500.0
        if not (all_pts[:, 0].min() - margin <= junctions[j][0] <= all_pts[:, 0].max() + margin
                and all_pts[:, 1].min() - margin <= junctions[j][1] <= all_pts[:, 1].max() + margin):
            bdy = fitted[j - 1]["seg"].end_idx
            junctions[j] = np.array(xy[bdy], dtype=float)

    # ── Step 4: build elements from junctions ────────────────────────────
    elements: list[dict] = []
    sta = 0.0

    for i, f in enumerate(fitted):
        start_pt = junctions[i]
        end_pt   = junctions[i + 1]

        if f["type"] == "Line":
            seg_len = float(np.linalg.norm(end_pt - start_pt))
            if seg_len < 1e-6:
                continue
            heading = math.atan2(
                float(end_pt[1] - start_pt[1]),
                float(end_pt[0] - start_pt[0]),
            )
            elements.append({
                "type":          "Line",
                "sta_start":     sta,
                "length":        seg_len,
                "start":         start_pt.tolist(),
                "end":           end_pt.tolist(),
                "direction_rad": heading,
            })
            sta += seg_len

        else:  # Arc
            cx, cy, R = f["cx"], f["cy"], f["R"]
            rot  = f["rot"]
            sign = 1.0 if rot == "ccw" else -1.0

            a_s = math.atan2(float(start_pt[1]) - cy, float(start_pt[0]) - cx)
            a_e = math.atan2(float(end_pt[1])   - cy, float(end_pt[0])   - cx)

            # Wrap arc angle to correct direction
            delta = a_e - a_s
            if rot == "ccw":
                while delta <= 0.0:
                    delta += 2.0 * math.pi
            else:
                while delta >= 0.0:
                    delta -= 2.0 * math.pi

            # Sanity: arc must be < 180° for railway geometry
            if abs(delta) > math.pi:
                # Wrong solution — fall back to line chord
                chord = float(np.linalg.norm(end_pt - start_pt))
                if chord > 1e-6:
                    heading = math.atan2(
                        float(end_pt[1] - start_pt[1]),
                        float(end_pt[0] - start_pt[0]),
                    )
                    elements.append({
                        "type": "Line", "sta_start": sta, "length": chord,
                        "start": start_pt.tolist(), "end": end_pt.tolist(),
                        "direction_rad": heading,
                    })
                    sta += chord
                continue

            arc_len = R * abs(delta)
            chord   = float(np.linalg.norm(end_pt - start_pt))

            elements.append({
                "type":        "Arc",
                "sta_start":   sta,
                "length":      arc_len,
                "start":       start_pt.tolist(),
                "end":         end_pt.tolist(),
                "center":      [cx, cy],
                "radius":      R,
                "rot":         rot,
                "chord":       chord,
                "_deflection": sign * abs(delta),
            })
            sta += arc_len

    return elements


# ---------------------------------------------------------------------------
# Algorithm 2 helpers — Dynamic Programming Segmentation
# ---------------------------------------------------------------------------

def _line_sse(xy: np.ndarray, i0: int, i1: int) -> float:
    """Sum of squared perpendicular distances from OSM[i0..i1] to the best-fit line."""
    pts = xy[i0: i1 + 1]
    if len(pts) < 2:
        return 0.0
    centred = pts - pts.mean(axis=0)
    try:
        _, s, _ = np.linalg.svd(centred, full_matrices=False)
        # Minor singular value² = sum of squared perp distances
        return float(s[1] ** 2) if len(s) > 1 else 0.0
    except np.linalg.LinAlgError:
        return float("inf")


def _arc_sse_and_fit(
    xy: np.ndarray, i0: int, i1: int, min_radius: float
) -> tuple[float, float, float, float]:
    """
    Fit a circle to OSM[i0..i1]; return (radial_SSE, cx, cy, R).
    Returns (inf, 0, 0, 0) if fewer than 3 points or fit fails.
    """
    from geometry.alignment import _fit_circle_kasa

    pts = xy[i0: i1 + 1]
    if len(pts) < 3:
        return float("inf"), 0.0, 0.0, 0.0
    cx, cy, R = _fit_circle_kasa(pts)
    if cx is None or R is None or not math.isfinite(R) or R < min_radius or R > 1e6:
        return float("inf"), 0.0, 0.0, 0.0
    dists = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    sse   = float(np.sum((dists - R) ** 2))
    return sse, float(cx), float(cy), float(R)


def _dp_segmentation(
    xy:           np.ndarray,
    chainages:    np.ndarray,
    lam:          float,
    min_radius:   float,
    progress_cb=None,
    time_budget_s: float = 60.0,
) -> list[_Segment]:
    """
    Imai-Iri DP: find the segmentation of OSM[0..N-1] minimising
        Σ SSE(segment) + λ × number_of_segments.

    lam controls the trade-off: larger → fewer, longer elements.
    To keep O(N²) tractable, spans longer than max_span points skip the arc fit.
    """
    t_dp_start = time.monotonic()

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    N = len(xy)
    if N < 2:
        return []

    # For very dense OSM data (< 2 m node spacing), subsample to ≤ 200 nodes
    # before building the O(N²) cost table.  The subsampled indices are later
    # mapped back when constructing _Segment objects.
    target_n = 200
    if N > target_n:
        step = max(1, N // target_n)
        sub_idx = list(range(0, N, step))
        if sub_idx[-1] != N - 1:
            sub_idx.append(N - 1)
        xy_dp  = xy[sub_idx]
        ch_dp  = chainages[sub_idx]
        # build a reverse map: subsampled index → original index
        orig_idx = sub_idx
        Ndp = len(xy_dp)
        _p(f"Subsampled {N} → {Ndp} nodes for cost table…")
    else:
        xy_dp    = xy
        ch_dp    = chainages
        orig_idx = list(range(N))
        Ndp      = N

    max_span = min(Ndp, 120)  # limit arc-fit window for performance

    # Pre-compute cost table on the (possibly subsampled) point set
    cost_val  = np.full((Ndp, Ndp), np.inf, dtype=float)
    cost_type = np.empty((Ndp, Ndp), dtype=object)
    arc_cx    = np.zeros((Ndp, Ndp), dtype=float)
    arc_cy    = np.zeros((Ndp, Ndp), dtype=float)
    arc_R_arr = np.zeros((Ndp, Ndp), dtype=float)
    arc_rot   = np.empty((Ndp, Ndp), dtype=object)

    _p(f"Building cost table (0/{Ndp} rows)…")
    last_report_i = -1
    report_every  = max(1, Ndp // 10)   # report ~10 times across the table

    for i in range(Ndp - 1):
        # Time-budget check (DP rarely exceeds it, but guard anyway)
        if time.monotonic() - t_dp_start > time_budget_s:
            _p(f"Time limit reached at row {i}/{Ndp}; using partial cost table.")
            break

        if i - last_report_i >= report_every:
            _p(f"Cost table: {i}/{Ndp} rows…")
            last_report_i = i

        for j in range(i + 1, min(i + max_span, Ndp)):
            l_sse = _line_sse(xy_dp, i, j)
            best_sse  = l_sse
            best_type = "Line"

            if j - i >= 3:
                defl = abs(_segment_deflection(xy_dp[i: j + 1]))
                if defl >= _MIN_ARC_DEFLECTION_RAD:
                    a_sse, cx, cy, R = _arc_sse_and_fit(xy_dp, i, j, min_radius)
                    sag = _arc_sagitta(R, defl) if R > 0 and math.isfinite(R) else 0.0
                    if a_sse < l_sse and sag >= 1.5:
                        best_sse  = a_sse
                        best_type = "Arc"
                        arc_cx[i, j] = cx
                        arc_cy[i, j] = cy
                        arc_R_arr[i, j] = R
                        raw_defl = _segment_deflection(xy_dp[i: j + 1])
                        arc_rot[i, j] = "ccw" if raw_defl >= 0 else "cw"

            cost_val[i, j]  = best_sse
            cost_type[i, j] = best_type

    _p("Running dynamic programming…")

    # Dynamic programming on the subsampled index space
    dp_cost = np.full(Ndp, np.inf, dtype=float)
    dp_prev = np.full(Ndp, -1,    dtype=int)
    dp_cost[0] = 0.0

    for j in range(1, Ndp):
        for i in range(max(0, j - max_span), j):
            if not math.isfinite(dp_cost[i]):
                continue
            c = dp_cost[i] + cost_val[i, j] + lam
            if c < dp_cost[j]:
                dp_cost[j] = c
                dp_prev[j] = i

    _p("Tracing optimal segmentation…")

    # Reconstruct breakpoints (in subsampled space)
    bp_sub: list[int] = []
    j = Ndp - 1
    while j > 0:
        bp_sub.append(j)
        p = dp_prev[j]
        if p < 0:
            break
        j = p
    bp_sub.reverse()

    if not bp_sub:
        bp_sub = [Ndp - 1]

    # Build a reverse map: original index → subsampled index
    orig_to_sub: dict[int, int] = {orig_idx[si]: si for si in range(Ndp)}

    # Map subsampled breakpoints back to original OSM indices, keeping pairs
    # (sub_j, orig_bp) so we can later look up cost_type / arc_R_arr.
    bp_pairs: list[tuple[int, int]] = [
        (sub_j, orig_idx[sub_j]) for sub_j in bp_sub
    ]

    # Build _Segment list using original xy / chainages
    segments: list[_Segment] = []
    prev_orig = 0
    prev_sub  = 0
    for sub_j, bp in bp_pairs:
        seg_type = cost_type[prev_sub, sub_j]
        if seg_type is None:
            seg_type = "Line"

        defl = _segment_deflection(xy[prev_orig: bp + 1])

        if seg_type == "Arc":
            R   = arc_R_arr[prev_sub, sub_j]
            rot = arc_rot[prev_sub, sub_j]
            if not isinstance(rot, str):
                rot = "ccw" if defl >= 0 else "cw"
            if _arc_sagitta(R, defl) < 1.5 or abs(defl) < _MIN_ARC_DEFLECTION_RAD:
                seg_type = "Line"
                R = math.inf
        else:
            R   = math.inf
            rot = "ccw" if defl >= 0 else "cw"

        segments.append(_Segment(
            seg_type=seg_type, start_idx=prev_orig, end_idx=bp,
            R_median=R, rot=rot, deflection=defl,
        ))
        prev_orig = bp
        prev_sub  = sub_j

    return segments


# ---------------------------------------------------------------------------
# Algorithm 3 helpers — Progressive MC
# ---------------------------------------------------------------------------

def _build_elements_from_boundaries(
    boundaries: list[int],
    types:      list[str],
    xy:         np.ndarray,
    chainages:  np.ndarray,
    min_radius: float,
) -> list[dict]:
    """
    Construct element list from boundary indices + type assignments.
    Arc elements are fitted to their OSM point range; endpoints anchored
    to xy[boundary].  Used during MC iterations (no tangent-point junctions
    here — those are applied in the final assembly pass).
    """
    elements: list[dict] = []
    sta = 0.0
    for k in range(len(boundaries) - 1):
        i0  = boundaries[k]
        i1  = boundaries[k + 1]
        typ = types[k]
        s_pt = np.array(xy[i0], dtype=float)
        e_pt = np.array(xy[i1], dtype=float)

        if typ == "Line" or i1 - i0 < 3:
            seg_len = float(chainages[i1] - chainages[i0])
            if seg_len < 1e-6:
                continue
            heading = math.atan2(
                float(e_pt[1] - s_pt[1]), float(e_pt[0] - s_pt[0])
            )
            elements.append({
                "type": "Line", "sta_start": sta, "length": seg_len,
                "start": s_pt.tolist(), "end": e_pt.tolist(),
                "direction_rad": heading,
            })
            sta += seg_len

        else:  # Arc
            result = _fit_arc_robust(xy, i0, i1, min_radius)
            if result is None:
                seg_len = float(chainages[i1] - chainages[i0])
                if seg_len < 1e-6:
                    continue
                heading = math.atan2(
                    float(e_pt[1] - s_pt[1]), float(e_pt[0] - s_pt[0])
                )
                elements.append({
                    "type": "Line", "sta_start": sta, "length": seg_len,
                    "start": s_pt.tolist(), "end": e_pt.tolist(),
                    "direction_rad": heading,
                })
                sta += seg_len
                continue

            cx, cy, R = result
            defl = _segment_deflection(xy[i0: i1 + 1])
            rot  = "ccw" if defl >= 0 else "cw"
            sign = 1.0 if rot == "ccw" else -1.0
            arc_len = R * abs(defl)
            chord   = float(np.linalg.norm(e_pt - s_pt))
            elements.append({
                "type":        "Arc",
                "sta_start":   sta,
                "length":      arc_len,
                "start":       s_pt.tolist(),
                "end":         e_pt.tolist(),
                "center":      [cx, cy],
                "radius":      R,
                "rot":         rot,
                "chord":       chord,
                "_deflection": sign * abs(defl),
            })
            sta += arc_len

    return elements


def _worst_osm_deviation(
    elements: list[dict], xy: np.ndarray, chainages: np.ndarray
) -> tuple[float, int]:
    """
    Return (max_deviation, worst_osm_idx) over all OSM points.

    Vectorised per-element: computes perpendicular distances for all points
    in an element's chainage window at once using numpy, which is significantly
    faster than the previous point-by-point scalar loop.
    """
    worst_dev = 0.0
    worst_idx = len(xy) // 2

    for el in elements:
        sta0 = el.get("sta_start", 0.0)
        sta1 = sta0 + el.get("length", 0.0)
        mask    = (chainages >= sta0 - 0.1) & (chainages <= sta1 + 0.1)
        idx_arr = np.where(mask)[0]
        if len(idx_arr) == 0:
            continue

        pts = xy[idx_arr]  # shape (K, 2)

        etype = el.get("type", "Line")
        if etype == "Arc":
            center = np.array(el["center"], dtype=float)
            r      = float(el.get("radius", 1.0))
            dists  = np.abs(np.linalg.norm(pts - center, axis=1) - r)
        else:
            # Line (or unknown — treat as Line)
            start  = np.array(el.get("start", [0.0, 0.0]), dtype=float)
            end    = np.array(el.get("end",   [0.0, 0.0]), dtype=float)
            seg    = end - start
            seg_sq = float(np.dot(seg, seg))
            if seg_sq < 1e-18:
                dists = np.linalg.norm(pts - start, axis=1)
            else:
                t    = np.dot(pts - start, seg) / seg_sq
                t    = np.clip(t, 0.0, 1.0)
                proj = start + t[:, np.newaxis] * seg
                dists = np.linalg.norm(pts - proj, axis=1)

        local_max = float(dists.max())
        if local_max > worst_dev:
            worst_dev = local_max
            worst_idx = int(idx_arr[int(dists.argmax())])

    return worst_dev, worst_idx


def _progressive_mc_build(
    xy:            np.ndarray,
    chainages:     np.ndarray,
    max_deviation: float,
    min_radius:    float,
    merge_pct:     float = 15.0,
    max_elements:  int   = 80,
    time_budget_s: float = 60.0,
    progress_cb=None,
    preview_cb=None,
    preview_interval_s: float = 7.0,
) -> list[dict]:
    """
    Progressive insertion with simulated annealing.

    Starts with a single Line element.  At each step, inserts a new boundary
    at the OSM point with the highest deviation; tries Line split, Arc
    conversion, and Line-to-Arc hybrid; keeps the option that most reduces
    max deviation.

    Every 10 insertions a SA perturbation randomly shifts boundary indices
    by ±3 points, accepting moves probabilistically to escape local minima.

    On completion, the final boundary/type assignment is assembled with
    tangent-point junctions (via _connect_segments_tangent).

    progress_cb(msg: str)        — called at each iteration with status text
    preview_cb(elements: list)   — called every ~preview_interval_s seconds with
                                   a preliminary element list for map visualisation
    """
    N = len(xy)
    if N < 2:
        return []

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    rng = np.random.default_rng(42)
    t_start        = time.monotonic()
    t_last_preview = t_start

    # Initial state: one Line covering everything
    boundaries: list[int] = [0, N - 1]
    types:      list[str] = ["Line"]

    T = max(max_deviation * 2.0, 0.5)   # SA initial temperature

    _p("Starting — 1 element, evaluating…")

    iteration = 0
    while True:
        # ── time / size budget ───────────────────────────────────────────
        elapsed = time.monotonic() - t_start
        if elapsed > time_budget_s:
            _p(f"Time limit reached ({time_budget_s:.0f} s). Finalising…")
            break
        if len(boundaries) >= max_elements + 1:
            _p(f"Element limit reached ({max_elements}). Finalising…")
            break

        # ── evaluate current state ───────────────────────────────────────
        elements = _build_elements_from_boundaries(
            boundaries, types, xy, chainages, min_radius
        )
        if not elements:
            break
        worst_dev, worst_idx = _worst_osm_deviation(elements, xy, chainages)

        n_el = len(boundaries) - 1
        _p(
            f"Iteration {iteration + 1}  |  {n_el} element{'s' if n_el != 1 else ''}"
            f"  |  max deviation {worst_dev:.3f} m"
            f"  |  {elapsed:.0f}/{time_budget_s:.0f} s"
        )

        # ── periodic preview for map visualisation ───────────────────────
        if preview_cb is not None:
            now = time.monotonic()
            if now - t_last_preview >= preview_interval_s:
                try:
                    # Build a quick tangent-junction version of current state
                    _preview_segs: list[_Segment] = []
                    for k in range(len(boundaries) - 1):
                        i0p, i1p = boundaries[k], boundaries[k + 1]
                        typ_p  = types[k]
                        defl_p = _segment_deflection(xy[i0p: i1p + 1])
                        rot_p  = "ccw" if defl_p >= 0 else "cw"
                        R_p    = math.inf
                        if typ_p == "Arc":
                            res = _fit_arc_robust(xy, i0p, i1p, min_radius)
                            if res:
                                R_p = res[2]
                            else:
                                typ_p = "Line"
                        _preview_segs.append(_Segment(
                            seg_type=typ_p, start_idx=i0p, end_idx=i1p,
                            R_median=R_p, rot=rot_p, deflection=defl_p,
                        ))
                    preview_elements = _connect_segments_tangent(
                        _preview_segs, xy, chainages, min_radius
                    )
                    preview_cb(preview_elements)
                except Exception:
                    pass
                t_last_preview = now

        if worst_dev <= max_deviation:
            _p(f"Converged! Max deviation {worst_dev:.3f} m within {max_deviation:.3f} m target")
            break

        # ── find which segment contains the worst point ──────────────────
        seg_k = None
        for k in range(len(boundaries) - 1):
            if boundaries[k] <= worst_idx <= boundaries[k + 1]:
                seg_k = k
                break
        if seg_k is None:
            break

        i0, i1 = boundaries[seg_k], boundaries[seg_k + 1]

        if i1 - i0 < 2:
            # Cannot split further; try SA perturbation instead
            if T < 1e-3:
                break
            _do_sa_perturbation(boundaries, types, xy, chainages, min_radius,
                                worst_dev, rng, T)
            T *= 0.90
            iteration += 1
            continue

        # ── generate candidate moves ─────────────────────────────────────
        mid = max(i0 + 1, min(i1 - 1, worst_idx))

        best_dev   = worst_dev
        best_bdry  = boundaries[:]
        best_types = types[:]

        # Move A: split into two Lines
        bdry_a = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
        typ_a  = types[:seg_k] + ["Line", "Line"] + types[seg_k + 1:]
        dev_a, _ = _worst_osm_deviation(
            _build_elements_from_boundaries(bdry_a, typ_a, xy, chainages, min_radius),
            xy, chainages,
        )
        if dev_a < best_dev:
            best_dev, best_bdry, best_types = dev_a, bdry_a, typ_a

        # Move B: convert entire segment to Arc
        defl_full = _segment_deflection(xy[i0: i1 + 1])
        if (i1 - i0 >= 3
                and abs(defl_full) >= _MIN_ARC_DEFLECTION_RAD
                and types[seg_k] != "Arc"):
            typ_b = types[:seg_k] + ["Arc"] + types[seg_k + 1:]
            dev_b, _ = _worst_osm_deviation(
                _build_elements_from_boundaries(boundaries, typ_b, xy, chainages, min_radius),
                xy, chainages,
            )
            if dev_b < best_dev:
                best_dev, best_bdry, best_types = dev_b, boundaries[:], typ_b

        # Move C: split into Line + Arc (left half as Line, right as Arc)
        if mid - i0 >= 3:
            defl_r = _segment_deflection(xy[mid: i1 + 1])
            if abs(defl_r) >= _MIN_ARC_DEFLECTION_RAD:
                bdry_c = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
                typ_c  = types[:seg_k] + ["Line", "Arc"] + types[seg_k + 1:]
                dev_c, _ = _worst_osm_deviation(
                    _build_elements_from_boundaries(bdry_c, typ_c, xy, chainages, min_radius),
                    xy, chainages,
                )
                if dev_c < best_dev:
                    best_dev, best_bdry, best_types = dev_c, bdry_c, typ_c

        # Move D: split into Arc + Line (left half as Arc, right as Line)
        if i1 - mid >= 3:
            defl_l = _segment_deflection(xy[i0: mid + 1])
            if abs(defl_l) >= _MIN_ARC_DEFLECTION_RAD:
                bdry_d = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
                typ_d  = types[:seg_k] + ["Arc", "Line"] + types[seg_k + 1:]
                dev_d, _ = _worst_osm_deviation(
                    _build_elements_from_boundaries(bdry_d, typ_d, xy, chainages, min_radius),
                    xy, chainages,
                )
                if dev_d < best_dev:
                    best_dev, best_bdry, best_types = dev_d, bdry_d, typ_d

        boundaries = best_bdry
        types      = best_types

        # ── periodic SA perturbation ─────────────────────────────────────
        if iteration % 10 == 9 and T > 1e-3:
            _do_sa_perturbation(boundaries, types, xy, chainages, min_radius,
                                best_dev, rng, T)
            T *= 0.95

        iteration += 1

    _p("Assembling final elements with tangent junctions…")

    # ── final assembly with tangent-point junctions ───────────────────────
    segments: list[_Segment] = []
    for k in range(len(boundaries) - 1):
        i0, i1 = boundaries[k], boundaries[k + 1]
        typ  = types[k]
        defl = _segment_deflection(xy[i0: i1 + 1])
        rot  = "ccw" if defl >= 0 else "cw"

        R = math.inf
        if typ == "Arc":
            result = _fit_arc_robust(xy, i0, i1, min_radius)
            if result:
                R = result[2]
            else:
                typ = "Line"
                R   = math.inf

        segments.append(_Segment(
            seg_type=typ, start_idx=i0, end_idx=i1,
            R_median=R, rot=rot, deflection=defl,
        ))

    return _connect_segments_tangent(segments, xy, chainages, min_radius)


def _do_sa_perturbation(
    boundaries: list[int],
    types:      list[str],
    xy:         np.ndarray,
    chainages:  np.ndarray,
    min_radius: float,
    current_dev: float,
    rng:        np.random.Generator,
    T:          float,
) -> None:
    """In-place SA: randomly shift one interior boundary by ±1–3 indices."""
    if len(boundaries) <= 2:
        return
    j    = int(rng.integers(1, len(boundaries) - 1))
    step = int(rng.integers(-3, 4))
    if step == 0:
        return
    new_b = int(boundaries[j]) + step
    new_b = max(int(boundaries[j - 1]) + 1, min(int(boundaries[j + 1]) - 1, new_b))
    if new_b == boundaries[j]:
        return
    old_b        = boundaries[j]
    boundaries[j] = new_b
    new_elements = _build_elements_from_boundaries(
        boundaries, types, xy, chainages, min_radius
    )
    new_dev, _ = _worst_osm_deviation(new_elements, xy, chainages)
    delta = new_dev - current_dev
    if delta > 0:
        # Worsening move: accept with SA probability
        if rng.random() >= math.exp(-delta / max(T, 1e-9)):
            boundaries[j] = old_b   # reject
