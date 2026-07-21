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

import bisect
import copy
import math
import time
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class CandidateAlignment:
    algorithm_id:        str
    label:               str
    elements:            list
    max_deviation:       float = 0.0
    rmse:                float = 0.0
    n_elements:          int   = 0
    color_hex:           str   = "#ffffff"
    geo_wgs84:           list  = field(default_factory=list)
    # Per-element WGS84 segments for per-element coloured map rendering.
    # Each entry: {"type": str, "params": dict, "points": [[lat, lon], ...]}.
    geo_segments_wgs84:  list  = field(default_factory=list)
    # Maximum heading mismatch (degrees) across all junctions; sanity metric.
    max_heading_jump_deg: float = 0.0
    # Editable PI model (Level 2/3 only; None otherwise). Source of truth
    # for interactive editing in Step 5 — see PIAlignment.
    pi_model:            object = None


@dataclass
class PIData:
    """
    One editable Point of Intersection of the tangent polygon.

    Convention for the editable fields:
      radius     > 0  → user/applied value used verbatim (still guarded)
      radius    <= 0  → auto-estimate from the OSM points
      spiral_len > 0  → user/applied spiral length (still guarded)
      spiral_len == 0 → explicitly no spiral (plain arc)
      spiral_len  < 0 → auto (use the model's default spiral length)

    After every rebuild the *applied* values are written back into
    `radius` / `spiral_len`, so the table always shows real numbers and
    subsequent rebuilds are reproducible.
    """
    index:            int                 # vertex index into PIAlignment.V (stable per build)
    xy:               tuple               # PI coordinates (projected)
    deflection:       float               # polygon deflection (rad, signed, informational)
    radius:           float = -1.0
    radius_auto:      float = 0.0         # last auto-estimated radius (for reset)
    spiral_len:       float = -1.0
    spiral_len_auto:  float = 0.0
    omitted:          bool  = False
    merged_with_next: bool  = False       # spiral-merge state (feature: merge)
    merged_with_prev: bool  = False       # set on the partner PI
    merge_partner:    int   = -1          # PI index of the merge partner
    premerge_spiral_len: float = -1.0     # value to restore on undo-merge
    # High-angle chain: this PI's curve continues into the NEXT PI's curve
    # on the same circle (no exit spiral here, no entry spiral there, zero
    # connector). Legacy field: no longer SET by any current merge (which
    # always builds a single PI — see merged_turn below); kept read-only so
    # a pre-release .coypu 1.1 file with chainNext still loads and rebuilds.
    chain_next:       bool  = False
    # Set by merge_pi_range on the single PI it creates: the SIGNED,
    # UNWRAPPED total turn (radians) the merged curve must sweep — may
    # exceed +-180 deg (a reflex/major arc). None for an ordinary PI, where
    # the wrapped polygon deflection (always <= 180 deg) is authoritative.
    merged_turn:      float | None = None


@dataclass
class PIAlignment:
    """
    Editable PI model — the source of truth for Level 2/3 alignments.

    `rebuild_from_pi_model` regenerates `elements` (and `tangent_stubs`)
    from V + per-PI overrides; the OSM polyline is kept for radius
    auto-estimation and quality evaluation.
    """
    V:              np.ndarray            # tangent polygon vertices (incl. endpoints)
    idx:            list                  # original polyline index per vertex
    pis:            list                  # list[PIData], one per interior vertex
    xy_ref:         np.ndarray            # OSM polyline (projected)
    chainages_ref:  np.ndarray
    tol:            float                 # DP tolerance used
    min_radius:     float
    spiral_default: float                 # Step-3 spiral length setting
    use_spirals:    bool
    elements:       list = field(default_factory=list)
    # Per-PI virtual tangent stubs for map display:
    # {"pi": k, "pi_xy": [x, y], "tc": [x, y], "ct": [x, y]}
    tangent_stubs:  list = field(default_factory=list)
    # Human-readable notes from the last rebuild (radius clamps, skips, …),
    # surfaced in the GUI log panel.
    log:            list = field(default_factory=list)
    # Deviation stats from the last rebuild (compute_deviation_stats result);
    # reused by the GUI so a rebuild costs exactly one pass.
    last_stats:     dict = field(default_factory=dict)


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

def _dists_to_element_vec(xy: np.ndarray, el: dict) -> np.ndarray:
    """
    Vectorised twin of `_point_to_element_dist`: distance from EVERY point in
    `xy` (P, 2) to one element, returned as a (P,) array. Same maths, same
    tie-breaking — just without the per-point Python call.
    """
    etype = el.get("type", "Line")
    if etype == "Arc":
        c = np.asarray(el["center"], dtype=float)
        r = float(el.get("radius", 1.0))
        return np.abs(np.hypot(xy[:, 0] - c[0], xy[:, 1] - c[1]) - r)

    # Line / Spiral / unknown → clamped projection onto the chord
    start = np.asarray(el.get("start", [0.0, 0.0]), dtype=float)
    end   = np.asarray(el.get("end",   [0.0, 0.0]), dtype=float)
    seg   = end - start
    seg_len = float(np.hypot(seg[0], seg[1]))
    d0 = xy - start
    if seg_len < 1e-9:
        return np.hypot(d0[:, 0], d0[:, 1])
    # Elementwise on purpose (not d0 @ seg): BLAS picks size-dependent
    # kernels (FMA vs plain) so a windowed slice would round differently
    # from the full array — rebuild_pi_span needs bitwise-equal distances.
    t = (d0[:, 0] * seg[0] + d0[:, 1] * seg[1]) / (seg_len * seg_len)
    np.clip(t, 0.0, 1.0, out=t)
    px = start[0] + t * seg[0]
    py = start[1] + t * seg[1]
    return np.hypot(xy[:, 0] - px, xy[:, 1] - py)


def compute_deviation_stats(elements: list[dict], xy: np.ndarray) -> dict:
    """
    One exact, vectorised pass: for every OSM point find its nearest element
    and that distance. Replaces the two former O(points × elements) Python
    loops (`evaluate_candidate` and `annotate_element_deviations`), which
    together dominated every interactive rebuild.

    Returns
    -------
    {"max_deviation": float, "rmse": float,
     "per_element": [(max_dev, mean_dev, n_points), ...],   # one per element
     "point_dist": (P,) float array,   # per-OSM-point nearest distance
     "point_elem": (P,) int array}     # per-OSM-point nearest element index

    The per-point arrays are what makes `rebuild_pi_span` able to update the
    stats incrementally instead of re-running this whole E×P pass.
    """
    n_el = len(elements)
    empty = {"max_deviation": 0.0, "rmse": 0.0,
             "per_element": [(0.0, 0.0, 0)] * n_el}
    if n_el == 0 or xy is None or len(xy) == 0:
        return empty

    pts = np.asarray(xy, dtype=float)
    best   = np.full(len(pts), np.inf)
    best_i = np.zeros(len(pts), dtype=np.int64)
    for i, el in enumerate(elements):
        d = _dists_to_element_vec(pts, el)
        upd = d < best              # strict '<' → first element wins ties
        if upd.any():
            best[upd]   = d[upd]
            best_i[upd] = i

    ok = np.isfinite(best)
    if not ok.any():
        return empty
    stats = _stats_from_point_arrays(best, best_i, n_el)
    stats["point_dist"] = best
    stats["point_elem"] = best_i
    return stats


def _stats_from_point_arrays(point_dist: np.ndarray, point_elem: np.ndarray,
                             n_el: int) -> dict:
    """
    Aggregate max/rmse/per-element stats from the per-point nearest-distance
    arrays. O(P) numpy — this is what lets `rebuild_pi_span` refresh the
    global stats after recomputing only a window of points.
    """
    ok = np.isfinite(point_dist)
    if not ok.any() or n_el == 0:
        return {"max_deviation": 0.0, "rmse": 0.0,
                "per_element": [(0.0, 0.0, 0)] * n_el}
    b  = point_dist[ok]
    bi = point_elem[ok]

    sums   = np.bincount(bi, weights=b, minlength=n_el)
    counts = np.bincount(bi, minlength=n_el)
    maxs   = np.zeros(n_el)
    np.maximum.at(maxs, bi, b)

    per_element = [
        (float(maxs[i]),
         float(sums[i] / counts[i]) if counts[i] else 0.0,
         int(counts[i]))
        for i in range(n_el)
    ]
    return {
        "max_deviation": float(b.max()),
        "rmse":          float(math.sqrt(float(np.mean(b * b)))),
        "per_element":   per_element,
    }


def metrics_from_stats(stats: dict, elements: list[dict]) -> dict:
    """Assemble the public metrics dict from a `compute_deviation_stats` result."""
    try:
        jj_rad = _max_heading_jump_rad(elements)
    except Exception:
        jj_rad = 0.0
    return {
        "max_deviation":        float(stats.get("max_deviation", 0.0)),
        "rmse":                 float(stats.get("rmse", 0.0)),
        "n_elements":           len(elements),
        "max_heading_jump_deg": float(math.degrees(jj_rad)),
    }


def evaluate_candidate(
    elements:      list[dict],
    xy:            np.ndarray,
    chainages:     np.ndarray,
    check_interval: float = 5.0,
) -> dict:
    """
    Compute quality metrics for a list of fitted elements vs the OSM polyline.

    Per-OSM-point perpendicular distance to the *nearest* fitted element.
    Chainage-free matching tolerates spiral insertion (which shifts downstream
    stations relative to the original OSM chainages), hence `chainages` and
    `check_interval` are accepted for API compatibility but not needed.

    Returns dict with keys: max_deviation, rmse, n_elements, max_heading_jump_deg
    """
    if not elements or xy is None or len(xy) < 2:
        return {"max_deviation": 0.0, "rmse": 0.0, "n_elements": len(elements),
                "max_heading_jump_deg": 0.0}
    return metrics_from_stats(compute_deviation_stats(elements, xy), elements)


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
# Continuity helpers (used by spiral insertion + sweep scoring)
# ---------------------------------------------------------------------------

def _spiral_R_eff(el: dict) -> float:
    """
    The 'effective' arc-side radius of a Spiral element. For both entry
    (∞→R) and exit (R→∞) spirals this is the finite radius bound.
    """
    r_end = float(el.get("radius_end",   float("inf")))
    r_st  = float(el.get("radius_start", float("inf")))
    if not math.isinf(r_end) and r_end > 0:
        return r_end
    if not math.isinf(r_st)  and r_st  > 0:
        return r_st
    return float("inf")


def _heading_at_start(el: dict) -> float | None:
    """Tangent heading (radians) at the start of an element."""
    et = el.get("type")
    if et == "Line":
        return float(el.get("direction_rad", 0.0))
    if et == "Arc":
        try:
            cx, cy = el["center"][0], el["center"][1]
            sx, sy = el["start"][0],  el["start"][1]
            radial = math.atan2(sy - cy, sx - cx)
            sign   = +1.0 if el.get("rot") == "ccw" else -1.0
            return radial + sign * math.pi / 2.0
        except Exception:
            return None
    if et == "Spiral":
        # Chord-to-tangent relations for a clothoid (α ≈ θ_s/3 = L/(6R)):
        #   entry (∞→R): start = chord − sign·L/(6R), end = chord + sign·L/(3R)
        #   exit  (R→∞): start = chord − sign·L/(3R), end = chord + sign·L/(6R)
        try:
            sx, sy = el["start"][0], el["start"][1]
            ex, ey = el["end"][0],   el["end"][1]
            chord_dir = math.atan2(ey - sy, ex - sx)
            L     = float(el.get("length", 0.0))
            R_eff = _spiral_R_eff(el)
            if not math.isfinite(R_eff) or R_eff <= 0 or L <= 0:
                return chord_dir
            sign = +1.0 if el.get("rot") == "ccw" else -1.0
            r_st = float(el.get("radius_start", float("inf")))
            if math.isinf(r_st):                       # entry spiral
                return chord_dir - sign * (L / (6.0 * R_eff))
            return chord_dir - sign * (L / (3.0 * R_eff))   # exit spiral
        except Exception:
            return None
    return None


def _heading_at_end(el: dict) -> float | None:
    """Tangent heading (radians) at the end of an element."""
    et = el.get("type")
    if et == "Line":
        return float(el.get("direction_rad", 0.0))
    if et == "Arc":
        try:
            cx, cy = el["center"][0], el["center"][1]
            ex, ey = el["end"][0],    el["end"][1]
            radial = math.atan2(ey - cy, ex - cx)
            sign   = +1.0 if el.get("rot") == "ccw" else -1.0
            return radial + sign * math.pi / 2.0
        except Exception:
            return None
    if et == "Spiral":
        try:
            sx, sy = el["start"][0], el["start"][1]
            ex, ey = el["end"][0],   el["end"][1]
            chord_dir = math.atan2(ey - sy, ex - sx)
            L     = float(el.get("length", 0.0))
            R_eff = _spiral_R_eff(el)
            if not math.isfinite(R_eff) or R_eff <= 0 or L <= 0:
                return chord_dir
            sign = +1.0 if el.get("rot") == "ccw" else -1.0
            r_st = float(el.get("radius_start", float("inf")))
            if math.isinf(r_st):                       # entry spiral
                return chord_dir + sign * (L / (3.0 * R_eff))
            return chord_dir + sign * (L / (6.0 * R_eff))   # exit spiral
        except Exception:
            return None
    return None


def _max_heading_jump_rad(elements: list[dict]) -> float:
    """
    Maximum absolute heading mismatch (rad) between successive elements'
    tangent directions at their shared junction. Used as a sanity / quality
    metric and post-spiral C1 audit.
    """
    if len(elements) < 2:
        return 0.0
    worst = 0.0
    for a, b in zip(elements[:-1], elements[1:]):
        ha = _heading_at_end(a)
        hb = _heading_at_start(b)
        if ha is None or hb is None:
            continue
        diff = (hb - ha + math.pi) % (2.0 * math.pi) - math.pi
        worst = max(worst, abs(diff))
    return worst


# ---------------------------------------------------------------------------
# Candidate generator
# ---------------------------------------------------------------------------

class CandidateGenerator:
    """Run all four candidate algorithms on a projected XY polyline."""

    COLORS = ["#e040fb", "#ff9800", "#26c6da"]
    LABELS = {
        # Level-based algorithms (current UI)
        "level1":              "Level 1 — OSM Polyline",
        "level2":              "Level 2 — Lines + Arcs",
        "level3":              "Level 3 — Lines + Spirals + Arcs",
        # Legacy algorithms (kept callable, not shown in the UI)
        "segment_fit":         "Segment & Fit",
        "segment_fit_spirals": "Segment & Fit (Spirals)",
        "dp_segment":          "DP Segmentation",
        "progressive_mc":      "Progressive MC",
        "raw":                 "OSM Polyline",
    }

    def __init__(self, xy: np.ndarray, chainages: np.ndarray, settings: dict):
        self.xy             = xy
        self.chainages      = chainages
        self.settings       = settings
        self.min_radius     = settings.get("min_radius",       150.0)
        self.smooth_window  = settings.get("smooth_window",    21)
        self.max_deviation  = settings.get("max_deviation",    0.5)
        self.check_interval = settings.get("check_interval",   5.0)
        self.merge_pct         = settings.get("merge_radius_pct",  15.0)
        self.time_budget_s     = settings.get("time_budget_s",     60.0)
        self.division_length    = settings.get("division_length",    500.0)
        self.min_tangent_length = settings.get("min_tangent_length",  30.0)
        self.min_kappa_radius   = settings.get("min_kappa_radius",     0.0)
        self.min_kappa_length   = settings.get("min_kappa_length",   200.0)
        self.spiral_length      = settings.get("spiral_length",       20.0)

        # Precompute forced-line chainage ranges once (shared by all algorithms)
        self._forced_ch_ranges: list[tuple[float, float]] = _compute_forced_line_ranges(
            self.xy, self.chainages,
            smooth_window    = self.smooth_window,
            min_kappa_radius = self.min_kappa_radius,
            min_kappa_length = self.min_kappa_length,
        )

    def run_all(self) -> list[CandidateAlignment]:
        results = []
        algo_ids = ["level1", "level2", "level3"]
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
        if algo_id == "level1":
            return self._run_level1(progress_cb=progress_cb)
        elif algo_id == "level2":
            return self._run_level2(progress_cb=progress_cb)
        elif algo_id == "level3":
            return self._run_level3(progress_cb=progress_cb)
        elif algo_id == "segment_fit":
            return self._run_segment_fit(progress_cb=progress_cb)
        elif algo_id == "segment_fit_spirals":
            return self._run_segment_fit_spirals(progress_cb=progress_cb)
        elif algo_id == "dp_segment":
            return self._run_dp(progress_cb=progress_cb)
        elif algo_id == "progressive_mc":
            return self._run_progressive_mc(progress_cb=progress_cb, preview_cb=preview_cb)
        elif algo_id == "raw":
            return self._run_raw()
        else:
            raise ValueError(f"Unknown algorithm: {algo_id!r}")

    # ------------------------------------------------------------------
    # Multi-run parameter sweep (best-of-N)
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_score(c: CandidateAlignment) -> float:
        """
        Lower-is-better scalar for picking the best candidate of a sweep.

        Penalises (1) max_deviation strongly, (2) RMSE moderately,
        (3) heading discontinuity sharply (as a hard sanity gate),
        (4) element density (elements per km) to reward parsimonious,
        design-intent alignments over noise-chasing over-segmentation, and
        (5) catastrophic single-Line degeneracy.
        """
        if not c.elements:
            return float("inf")
        # Catastrophic-degeneracy guard: a single Line element with large
        # deviation ranks worse than any reasonable multi-element fit.
        if len(c.elements) == 1 and c.max_deviation > 5.0:
            return 1e9 + c.max_deviation
        total_len = sum(e.get("length", 0.0) for e in c.elements)
        elems_per_km = c.n_elements / max(0.1, total_len / 1000.0)
        return (c.max_deviation
                + 0.5 * c.rmse
                + 0.05 * c.max_heading_jump_deg
                + 0.5 * elems_per_km)

    def _sweep_variations(self, algo_id: str, n: int) -> list[dict]:
        """
        Build a list of `n` parameter overrides for the given algorithm.
        Each override is applied via `_apply_overrides` for one run.

        Empty dict = use defaults (= the user's Step 3 settings).
        """
        n = max(1, int(n))
        variations: list[dict] = []

        if algo_id == "level1":
            # Deterministic; no sweep.
            variations.append({})

        elif algo_id in ("level2", "level3"):
            # Sweep the Douglas-Peucker tolerance (== max_deviation): a
            # coarser polygon gives fewer, longer curves; a finer one hugs
            # the OSM data more closely. Best-of-N picked by score.
            base = self.max_deviation
            for factor in (1.0, 0.5, 2.5):
                variations.append({"max_deviation": max(0.1, base * factor)})

        elif algo_id == "segment_fit":
            base_sw = self.smooth_window
            for delta in (0, -6, +10):
                sw = max(5, min(51, base_sw + delta))
                if sw % 2 == 0:
                    sw += 1
                variations.append({"smooth_window": sw})

        elif algo_id == "segment_fit_spirals":
            # Inherit smooth_window sweep from segment_fit *and* vary the
            # spiral-length scaling (so inflection points get a chance to
            # accept different spiral lengths).
            base_sw  = self.smooth_window
            base_sl  = self.spiral_length
            params = [
                {"smooth_window": base_sw,                        "spiral_length": base_sl},
                {"smooth_window": max(5, min(51, base_sw - 6)),   "spiral_length": base_sl * 0.7},
                {"smooth_window": max(5, min(51, base_sw + 10)),  "spiral_length": base_sl},
            ]
            for v in params:
                if v["smooth_window"] % 2 == 0:
                    v["smooth_window"] += 1
                variations.append(v)

        elif algo_id == "dp_segment":
            base = self.merge_pct
            for factor in (1.0, 0.5, 1.5):
                variations.append({"merge_radius_pct": max(2.0, min(40.0, base * factor))})

        elif algo_id == "progressive_mc":
            base_div = self.division_length
            base_tb  = self.time_budget_s
            per_run_tb = max(8.0, base_tb / n)
            # Each window in piecewise MC uses seed=(42 + window_idx), so
            # changing division_length changes the seed pattern naturally —
            # giving us genuinely different MC trajectories across runs.
            divs = [base_div, base_div * 1.5, base_div * 0.7]
            for i in range(n):
                variations.append({
                    "division_length": float(divs[i % len(divs)]),
                    "time_budget_s":   float(per_run_tb),
                })

        elif algo_id == "raw":
            # Deterministic; sweep makes no sense.
            variations.append({})

        # Trim/pad to `n` runs
        if len(variations) > n:
            variations = variations[:n]
        while len(variations) < n and variations:
            variations.append(dict(variations[0]))    # repeat first as filler
        if not variations:
            variations.append({})
        return variations

    def _apply_overrides(self, overrides: dict):
        """Snapshot current values for the keys in `overrides`, apply, return snapshot."""
        snapshot = {}
        for k, v in overrides.items():
            attr = k
            if k == "merge_radius_pct":
                attr = "merge_pct"
            if hasattr(self, attr):
                snapshot[attr] = getattr(self, attr)
                setattr(self, attr, v)
        return snapshot

    def _restore_overrides(self, snapshot: dict):
        for k, v in snapshot.items():
            setattr(self, k, v)

    def run_one_with_sweep(
        self,
        algo_id: str,
        n: int = 3,
        progress_cb=None,
        preview_cb=None,
    ) -> CandidateAlignment:
        """
        Run `algo_id` up to `n` times with parameter variations and return the
        best (lowest `_candidate_score`) result.

        For deterministic algorithms (`raw`, `dp_segment` with no perturbation)
        a single run is sufficient even if `n>1`; the sweep generator returns
        a single variation in those cases.
        """
        if algo_id in ("raw", "level1"):
            # Deterministic — no sweep needed.
            return self._run_one(algo_id, progress_cb=progress_cb, preview_cb=preview_cb)

        variations = self._sweep_variations(algo_id, n)
        if len(variations) <= 1:
            # Single variation collapses to a regular _run_one call.
            return self._run_one(algo_id,
                                 progress_cb=progress_cb,
                                 preview_cb=preview_cb)

        results: list[CandidateAlignment] = []
        for i, overrides in enumerate(variations, start=1):
            tag = f"[run {i}/{len(variations)}]"
            wrapped_pcb = (lambda msg, _t=tag: progress_cb(f"{_t} {msg}")) if progress_cb else None
            snap = self._apply_overrides(overrides)
            try:
                c = self._run_one(algo_id, progress_cb=wrapped_pcb, preview_cb=preview_cb)
            except Exception:
                c = CandidateAlignment(
                    algorithm_id=algo_id,
                    label=self.LABELS.get(algo_id, algo_id),
                    elements=[],
                )
            finally:
                self._restore_overrides(snap)
            results.append(c)

            # Early-exit: if a result is already very high quality, stop.
            if (results[-1].elements
                    and results[-1].max_deviation < 0.5 * self.max_deviation
                    and results[-1].max_heading_jump_deg < 0.1):
                if progress_cb:
                    progress_cb(f"{tag} ✓ early-accept (max dev "
                                f"{results[-1].max_deviation:.2f} m)")
                break

        # Pick best
        best = min(results, key=self._candidate_score)
        if progress_cb:
            n_done = len(results)
            progress_cb(f"Best of {n_done}: max dev {best.max_deviation:.2f} m, "
                        f"RMSE {best.rmse:.2f} m, "
                        f"jumps {best.max_heading_jump_deg:.3f}°, "
                        f"{best.n_elements} elements")
        return best

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
        _p("Post-processing (C1 enforcement)…")
        elements = _post_process_elements(
            elements, self._forced_ch_ranges, self.min_radius
        )
        _p("Evaluating quality…")
        metrics  = evaluate_candidate(elements, xy, chainages, self.check_interval)
        return CandidateAlignment("segment_fit", "Segment & Fit", elements, **metrics)

    # ------------------------------------------------------------------
    # Algorithm 2 — Segment & Fit with Spirals
    # ------------------------------------------------------------------

    def _run_segment_fit_spirals(self, progress_cb=None) -> CandidateAlignment:
        """
        Segment & Fit with clothoid transition curves (Euler spirals).

        Runs the identical curvature-segmentation and fitting pipeline as
        _run_segment_fit, then inserts entry/exit spirals of length
        `spiral_length` around every Arc element that sits between two Lines
        using the **textbook tangent-fixed convention** (PI and tangent
        directions preserved; the arc keeps its original radius R; its centre
        shifts perpendicular to the bisector by p = L²/(24R)).

        After insertion, runs an extra C1 enforcement pass on the
        spiral-augmented element list to catch any numerical drift at TC/CT
        junctions (the spiral endpoints should match the adjacent tangent
        directions exactly by Fresnel construction, but a small drift can
        accumulate from the L_eff cap or skipped insertions).
        """
        def _p(msg):
            if progress_cb:
                progress_cb(msg)

        _p("Running Segment & Fit base…")
        base     = self._run_segment_fit(progress_cb=_p)
        elements = base.elements

        if not elements:
            return CandidateAlignment(
                "segment_fit_spirals", "Segment & Fit (Spirals)", []
            )

        L = self.spiral_length
        if L > 0:
            _p(f"Inserting clothoid spirals (L = {L:.0f} m)…")
            elements = _insert_spirals_into_elements(elements, L, self.min_radius)

            # Post-insertion C1 audit — log warning if any junction drifts
            jj = _max_heading_jump_rad(elements)
            if jj > 1e-3:
                _p(f"⚠ C1 audit: max heading jump = {math.degrees(jj):.4f}° after spiral insertion")

            # Re-enforce C1 (post-process was applied to the no-spiral output;
            # spiral insertion is a different element list and benefits from
            # one more pass to clean up tangent-line junctions outside the
            # inserted L–S–A–S–L zones).
            try:
                elements = _enforce_c1_junctions(elements, self.min_radius)
            except Exception:
                pass   # defensive — never let continuity-pass crash the pipeline

        _p("Evaluating quality…")
        xy, chainages = self.xy, self.chainages
        metrics = evaluate_candidate(elements, xy, chainages, self.check_interval)
        return CandidateAlignment(
            "segment_fit_spirals", "Segment & Fit (Spirals)", elements, **metrics
        )

    # ------------------------------------------------------------------
    # Algorithm 3 — DP Segmentation
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
        _p("Post-processing (C1 enforcement)…")
        elements = _post_process_elements(
            elements, self._forced_ch_ranges, self.min_radius
        )
        _p("Evaluating quality…")
        metrics = evaluate_candidate(elements, self.xy, self.chainages, self.check_interval)
        return CandidateAlignment("dp_segment", "DP Segmentation", elements, **metrics)

    # ------------------------------------------------------------------
    # Algorithm 3 — Progressive MC
    # ------------------------------------------------------------------

    def _run_progressive_mc(self, progress_cb=None, preview_cb=None) -> CandidateAlignment:
        """
        Piecewise MC with boundary constraints + Arc-Line-Arc consolidation.
        """
        # ── Phase 1: Piecewise MC ───────────────────────────────────────────
        elements = _progressive_mc_build_piecewise(
            self.xy, self.chainages,
            max_deviation   = self.max_deviation,
            min_radius      = self.min_radius,
            merge_pct       = self.merge_pct,
            max_elements    = 80,
            time_budget_s   = self.time_budget_s,
            division_length = self.division_length,
            progress_cb     = progress_cb,
            preview_cb      = preview_cb,
        )

        # ── Phase 2: Arc-Line-Arc consolidation ────────────────────────────
        if progress_cb:
            progress_cb("Consolidating Arc\u2013Line\u2013Arc patterns\u2026")
        elements = _consolidate_arc_line_arc(
            elements, self.xy, self.chainages,
            min_tangent_length = self.min_tangent_length,
            min_radius         = self.min_radius,
        )

        # ── Phase 3: C1 post-processing ────────────────────────────────────
        if progress_cb:
            progress_cb("Post-processing (C1 enforcement)\u2026")
        elements = _post_process_elements(
            elements, self._forced_ch_ranges, self.min_radius
        )

        # ── Quality evaluation ─────────────────────────────────────────────
        if progress_cb:
            progress_cb("Evaluating quality\u2026")
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

    # ------------------------------------------------------------------
    # Level-based algorithms (PI / tangent-polygon construction)
    # ------------------------------------------------------------------

    def _run_level1(self, progress_cb=None) -> CandidateAlignment:
        """Level 1 — the raw OSM polyline, re-tagged."""
        c = self._run_raw()
        c.algorithm_id = "level1"
        c.label        = self.LABELS["level1"]
        return c

    def _run_level2(self, progress_cb=None) -> CandidateAlignment:
        """
        Level 2 — Lines + circular Arcs from the tangent polygon.

        The OSM polyline is simplified (Douglas-Peucker) into a tangent
        polygon whose interior vertices are the Points of Intersection (PIs).
        At every PI a circular arc tangent to both adjacent tangents is
        inserted (T = R·tan(|δ|/2)); C1 continuity holds by construction.
        The alignment starts and ends exactly at the OSM endpoints.
        """
        def _p(msg):
            if progress_cb:
                progress_cb(msg)
        _p("Extracting tangent polygon (PIs)…")
        model = extract_pi_model(
            self.xy, self.chainages,
            tolerance     = self.max_deviation,
            min_radius    = self.min_radius,
            spiral_length = 0.0,
            use_spirals   = False,
            progress_cb   = _p,
        )
        elements = model.elements
        _p("Evaluating quality…")
        metrics = evaluate_candidate(elements, self.xy, self.chainages, self.check_interval)
        return CandidateAlignment("level2", self.LABELS["level2"], elements,
                                  pi_model=model, **metrics)

    def _run_level3(self, progress_cb=None) -> CandidateAlignment:
        """
        Level 3 — Lines + clothoid Spirals + circular Arcs.

        Same tangent polygon as Level 2, but every arc gets entry/exit
        clothoids. The spiral tangent length uses the exact Fresnel-based
        shift p and abscissa k:  T_s = (R + p)·tan(|δ|/2) + k, so the curve
        closes on the outgoing tangent by construction. Where a spiral
        cannot fit (short tangents, tiny deflection) the PI falls back to a
        plain arc — C1 continuity is never sacrificed.
        """
        def _p(msg):
            if progress_cb:
                progress_cb(msg)
        _p("Extracting tangent polygon (PIs)…")
        model = extract_pi_model(
            self.xy, self.chainages,
            tolerance     = self.max_deviation,
            min_radius    = self.min_radius,
            spiral_length = self.spiral_length,
            use_spirals   = True,
            progress_cb   = _p,
        )
        elements = model.elements
        _p("Evaluating quality…")
        metrics = evaluate_candidate(elements, self.xy, self.chainages, self.check_interval)
        return CandidateAlignment("level3", self.LABELS["level3"], elements,
                                  pi_model=model, **metrics)


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


# ---------------------------------------------------------------------------
# Piecewise MC helpers
# ---------------------------------------------------------------------------

def _place_anchors(chainages: np.ndarray, division_length: float) -> list[int]:
    """
    Return OSM node indices used as window boundaries.

    Always includes 0 and N-1.  Between them, selects the node closest
    to each successive multiple of division_length (< total length).
    Never adds two consecutive identical indices.
    """
    N = len(chainages)
    if N < 2 or division_length <= 0:
        return [0, N - 1]
    ch0    = float(chainages[0])
    ch_end = float(chainages[-1])
    if ch_end - ch0 <= division_length:
        return [0, N - 1]

    anchors = [0]
    k_mult  = 1
    while True:
        target = ch0 + k_mult * division_length
        if target >= ch_end:
            break
        j = int(np.searchsorted(chainages, target))
        j = max(1, min(N - 2, j))
        # Pick j-1 if it's closer to the target
        if j > 0 and abs(float(chainages[j - 1]) - target) < abs(float(chainages[j]) - target):
            j -= 1
        if j != anchors[-1]:
            anchors.append(j)
        k_mult += 1

    if anchors[-1] != N - 1:
        anchors.append(N - 1)
    return anchors


def _anchor_tangent(xy: np.ndarray, anchor_idx: int, half_window: int = 5) -> float:
    """
    Estimate OSM travel-direction heading at anchor_idx via local SVD fit
    over the ±half_window neighbouring nodes.
    """
    i0 = max(0, anchor_idx - half_window)
    i1 = min(len(xy) - 1, anchor_idx + half_window)
    return _fit_line_direction(xy, i0, i1)


def _connect_segments_tangent_constrained(
    segments:      list[_Segment],
    xy:            np.ndarray,
    chainages:     np.ndarray,
    min_radius:    float,
    entry_heading: float | None = None,
    exit_heading:  float | None = None,
) -> list[dict]:
    """
    Like _connect_segments_tangent but forces the heading of the first / last
    primitive to match entry_heading / exit_heading (C1 at window edges).

    For Line primitives: the fitted heading is simply overridden.
    For Arc primitives:  the Kasa-fitted radius is kept; the center is
    relocated so that the tangent at the boundary OSM point equals the
    required heading:
        CCW: center = P + R*(sin φ, −cos φ)
        CW:  center = P + R*(−sin φ, +cos φ)
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

    # ── Apply entry_heading constraint ───────────────────────────────────
    if entry_heading is not None and fitted:
        f0 = fitted[0]
        if f0["type"] == "Line":
            f0["heading"] = entry_heading
        else:
            R   = f0["R"]
            rot = f0["rot"]
            sp  = math.sin(entry_heading)
            cp  = math.cos(entry_heading)
            p   = xy[segments[0].start_idx]
            if rot == "ccw":
                f0["cx"] = float(p[0]) + R * sp
                f0["cy"] = float(p[1]) - R * cp
            else:
                f0["cx"] = float(p[0]) - R * sp
                f0["cy"] = float(p[1]) + R * cp

    # ── Apply exit_heading constraint ────────────────────────────────────
    if exit_heading is not None and fitted:
        fm = fitted[-1]
        if fm["type"] == "Line":
            fm["heading"] = exit_heading
        else:
            R   = fm["R"]
            rot = fm["rot"]
            sp  = math.sin(exit_heading)
            cp  = math.cos(exit_heading)
            p   = xy[segments[-1].end_idx]
            if rot == "ccw":
                fm["cx"] = float(p[0]) + R * sp
                fm["cy"] = float(p[1]) - R * cp
            else:
                fm["cx"] = float(p[0]) - R * sp
                fm["cy"] = float(p[1]) + R * cp

    # ── Step 2: compute junction points ──────────────────────────────────
    junctions: list[np.ndarray | None] = [None] * (n + 1)
    junctions[0] = np.array(xy[segments[0].start_idx], dtype=float)
    junctions[n] = np.array(xy[segments[-1].end_idx],  dtype=float)

    for j in range(1, n):
        left  = fitted[j - 1]
        right = fitted[j]

        if left["type"] == "Line" and right["type"] == "Arc":
            phi  = left["heading"]
            prev = junctions[j - 1]
            for _ in range(5):
                jx, jy = _arc_line_tangent_junction(
                    right["cx"], right["cy"], right["R"], right["rot"], phi
                )
                if prev is not None:
                    dx   = jx - float(prev[0])
                    dy   = jy - float(prev[1])
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
            phi = right["heading"]
            jx, jy = _arc_line_tangent_junction(
                left["cx"], left["cy"], left["R"], left["rot"], phi
            )
            junctions[j] = np.array([jx, jy])

        else:
            bdy = fitted[j - 1]["seg"].end_idx
            junctions[j] = np.array(xy[bdy], dtype=float)

    # ── Step 3: validate junctions ───────────────────────────────────────
    for j in range(1, n):
        if junctions[j] is None:
            bdy = fitted[j - 1]["seg"].end_idx
            junctions[j] = np.array(xy[bdy], dtype=float)
            continue
        seg_l   = fitted[j - 1]["seg"]
        seg_r   = fitted[j]["seg"]
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
            delta = a_e - a_s
            if rot == "ccw":
                while delta <= 0.0:
                    delta += 2.0 * math.pi
            else:
                while delta >= 0.0:
                    delta -= 2.0 * math.pi

            if abs(delta) > math.pi:
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


def _mc_window_build(
    xy_win:        np.ndarray,
    chainages_win: np.ndarray,
    entry_heading: float,
    exit_heading:  float,
    max_deviation: float,
    min_radius:    float,
    merge_pct:     float,
    time_budget_s: float,
    seed:          int  = 42,
    max_elements:  int  = 80,
    progress_cb=None,
) -> list[dict]:
    """
    Run the greedy MC insertion loop on a single window.

    Identical mechanics to _progressive_mc_build but operates on local
    xy_win / chainages_win so all boundary indices are 0-relative.
    Final assembly calls _connect_segments_tangent_constrained to enforce
    C1 at both window edges.  Returns elements whose sta_start values
    start from 0.0 (the caller adds the chainage offset when stitching).
    """
    N_win = len(xy_win)
    if N_win < 2:
        return []

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    rng = np.random.default_rng(seed)
    t_start = time.monotonic()

    boundaries: list[int] = [0, N_win - 1]
    types:      list[str] = ["Line"]
    T = max(max_deviation * 2.0, 0.5)

    iteration = 0
    while True:
        elapsed = time.monotonic() - t_start
        if elapsed > time_budget_s:
            break
        if len(boundaries) >= max_elements + 1:
            break

        elements = _build_elements_from_boundaries(
            boundaries, types, xy_win, chainages_win, min_radius
        )
        if not elements:
            break
        worst_dev, worst_idx = _worst_osm_deviation(elements, xy_win, chainages_win)

        n_el = len(boundaries) - 1
        _p(
            f"Iter {iteration + 1}  |  {n_el} element{'s' if n_el != 1 else ''}"
            f"  |  dev {worst_dev:.3f} m"
            f"  |  {elapsed:.0f}/{time_budget_s:.0f} s"
        )

        if worst_dev <= max_deviation:
            break

        seg_k = None
        for k in range(len(boundaries) - 1):
            if boundaries[k] <= worst_idx <= boundaries[k + 1]:
                seg_k = k
                break
        if seg_k is None:
            break

        i0, i1 = boundaries[seg_k], boundaries[seg_k + 1]

        if i1 - i0 < 2:
            if T < 1e-3:
                break
            _do_sa_perturbation(boundaries, types, xy_win, chainages_win, min_radius,
                                worst_dev, rng, T)
            T *= 0.90
            iteration += 1
            continue

        mid = max(i0 + 1, min(i1 - 1, worst_idx))

        best_dev   = worst_dev
        best_bdry  = boundaries[:]
        best_types = types[:]

        # Move A: split into two Lines
        bdry_a = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
        typ_a  = types[:seg_k] + ["Line", "Line"] + types[seg_k + 1:]
        dev_a, _ = _worst_osm_deviation(
            _build_elements_from_boundaries(bdry_a, typ_a, xy_win, chainages_win, min_radius),
            xy_win, chainages_win,
        )
        if dev_a < best_dev:
            best_dev, best_bdry, best_types = dev_a, bdry_a, typ_a

        # Move B: convert segment to Arc
        defl_full = _segment_deflection(xy_win[i0: i1 + 1])
        if (i1 - i0 >= 3
                and abs(defl_full) >= _MIN_ARC_DEFLECTION_RAD
                and types[seg_k] != "Arc"):
            typ_b = types[:seg_k] + ["Arc"] + types[seg_k + 1:]
            dev_b, _ = _worst_osm_deviation(
                _build_elements_from_boundaries(boundaries, typ_b, xy_win, chainages_win, min_radius),
                xy_win, chainages_win,
            )
            if dev_b < best_dev:
                best_dev, best_bdry, best_types = dev_b, boundaries[:], typ_b

        # Move C: Line + Arc
        if mid - i0 >= 3:
            defl_r = _segment_deflection(xy_win[mid: i1 + 1])
            if abs(defl_r) >= _MIN_ARC_DEFLECTION_RAD:
                bdry_c = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
                typ_c  = types[:seg_k] + ["Line", "Arc"] + types[seg_k + 1:]
                dev_c, _ = _worst_osm_deviation(
                    _build_elements_from_boundaries(bdry_c, typ_c, xy_win, chainages_win, min_radius),
                    xy_win, chainages_win,
                )
                if dev_c < best_dev:
                    best_dev, best_bdry, best_types = dev_c, bdry_c, typ_c

        # Move D: Arc + Line
        if i1 - mid >= 3:
            defl_l = _segment_deflection(xy_win[i0: mid + 1])
            if abs(defl_l) >= _MIN_ARC_DEFLECTION_RAD:
                bdry_d = boundaries[:seg_k + 1] + [mid] + boundaries[seg_k + 1:]
                typ_d  = types[:seg_k] + ["Arc", "Line"] + types[seg_k + 1:]
                dev_d, _ = _worst_osm_deviation(
                    _build_elements_from_boundaries(bdry_d, typ_d, xy_win, chainages_win, min_radius),
                    xy_win, chainages_win,
                )
                if dev_d < best_dev:
                    best_dev, best_bdry, best_types = dev_d, bdry_d, typ_d

        boundaries = best_bdry
        types      = best_types

        if iteration % 10 == 9 and T > 1e-3:
            _do_sa_perturbation(boundaries, types, xy_win, chainages_win, min_radius,
                                best_dev, rng, T)
            T *= 0.95

        iteration += 1

    # Final assembly with constrained tangent-point junctions
    segments: list[_Segment] = []
    for k in range(len(boundaries) - 1):
        i0, i1 = boundaries[k], boundaries[k + 1]
        typ  = types[k]
        defl = _segment_deflection(xy_win[i0: i1 + 1])
        rot  = "ccw" if defl >= 0 else "cw"
        R    = math.inf
        if typ == "Arc":
            result = _fit_arc_robust(xy_win, i0, i1, min_radius)
            if result:
                R = result[2]
            else:
                typ = "Line"
        segments.append(_Segment(
            seg_type=typ, start_idx=i0, end_idx=i1,
            R_median=R, rot=rot, deflection=defl,
        ))

    return _connect_segments_tangent_constrained(
        segments, xy_win, chainages_win, min_radius,
        entry_heading=entry_heading,
        exit_heading=exit_heading,
    )


def _progressive_mc_build_piecewise(
    xy:              np.ndarray,
    chainages:       np.ndarray,
    max_deviation:   float,
    min_radius:      float,
    merge_pct:       float = 15.0,
    max_elements:    int   = 80,
    time_budget_s:   float = 60.0,
    division_length: float = 500.0,
    progress_cb=None,
    preview_cb=None,
    preview_interval_s: float = 7.0,
) -> list[dict]:
    """
    Piecewise MC with anchor boundary constraints.

    Divides the OSM polyline into ~division_length windows at the OSM nodes
    closest to each multiple of division_length.  Runs _mc_window_build
    independently in each window with the time budget split evenly.
    C0 + C1 continuity at every inter-window boundary is enforced via
    _connect_segments_tangent_constrained.
    """
    N = len(xy)
    if N < 2:
        return []

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    anchors   = _place_anchors(chainages, division_length)
    n_windows = len(anchors) - 1

    # Compute OSM heading at every anchor node
    tangents = [_anchor_tangent(xy, anchors[k]) for k in range(len(anchors))]

    budget_per_window = time_budget_s / max(n_windows, 1)

    _p(
        f"Piecewise MC: {n_windows} window{'s' if n_windows != 1 else ''},"
        f" {budget_per_window:.0f} s each\u2026"
    )

    all_elements:    list[dict] = []
    t_last_preview = time.monotonic()

    for i in range(n_windows):
        a0, a1 = anchors[i], anchors[i + 1]
        xy_win        = xy[a0: a1 + 1]
        chainages_win = chainages[a0: a1 + 1] - chainages[a0]

        sta0_str = f"{float(chainages[a0]):.0f}"
        sta1_str = f"{float(chainages[a1]):.0f}"
        _p(f"Window {i + 1}/{n_windows}  ({sta0_str}\u2013{sta1_str} m)\u2026")

        def _win_pcb(msg, _wi=i, _nw=n_windows):
            _p(f"  [{_wi + 1}/{_nw}] {msg}")

        win_elements = _mc_window_build(
            xy_win, chainages_win,
            entry_heading = tangents[i],
            exit_heading  = tangents[i + 1],
            max_deviation = max_deviation,
            min_radius    = min_radius,
            merge_pct     = merge_pct,
            time_budget_s = budget_per_window,
            seed          = 42 + i,
            max_elements  = max_elements,
            progress_cb   = _win_pcb,
        )

        # Shift sta_start by the accumulated chainage offset
        sta_off = float(chainages[a0])
        for el in win_elements:
            el["sta_start"] += sta_off

        all_elements.extend(win_elements)

        # Emit preview after each window (or when interval elapsed)
        if preview_cb is not None:
            now = time.monotonic()
            if now - t_last_preview >= preview_interval_s or i == n_windows - 1:
                try:
                    preview_cb(all_elements[:])
                except Exception:
                    pass
                t_last_preview = now

    return all_elements


# ---------------------------------------------------------------------------
# Post-processing: Arc-Line-Arc consolidation
# ---------------------------------------------------------------------------

def _consolidate_arc_line_arc(
    elements:           list[dict],
    xy:                 np.ndarray,
    chainages:          np.ndarray,
    min_tangent_length: float,
    min_radius:         float,
) -> list[dict]:
    """
    Iteratively scan elements for [Arc][short Line][Arc] (same sense only)
    and merge the triple into a single Arc.

    Skips opposite-sense pairs (S-curves) to preserve their return tangent.
    Stops when no further merges are possible or min_tangent_length <= 0.
    """
    if min_tangent_length <= 0:
        return elements

    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(elements) - 2:
            A = elements[i]
            L = elements[i + 1]
            B = elements[i + 2]
            if (A.get("type") == "Arc"
                    and L.get("type") == "Line"
                    and B.get("type") == "Arc"
                    and L.get("length", 0.0) < min_tangent_length
                    and A.get("rot") == B.get("rot")):
                merged = _try_merge_arc_line_arc(elements, i, xy, chainages, min_radius)
                if merged is not None:
                    elements = merged
                    changed  = True
                    i        = max(0, i - 1)
                    continue
            i += 1
    return elements


def _try_merge_arc_line_arc(
    elements:   list[dict],
    idx:        int,
    xy:         np.ndarray,
    chainages:  np.ndarray,
    min_radius: float,
) -> list[dict] | None:
    """
    Try to replace elements[idx], elements[idx+1], elements[idx+2] with a
    single merged Arc.  Returns the updated list on success, None otherwise.
    """
    from geometry.alignment import _fit_circle_kasa

    A = elements[idx]
    B = elements[idx + 2]

    sta_start_A = A.get("sta_start", 0.0)
    sta_end_B   = B.get("sta_start", 0.0) + B.get("length", 0.0)

    # Collect OSM points under the triple
    mask    = (chainages >= sta_start_A - 0.1) & (chainages <= sta_end_B + 0.1)
    all_pts = xy[mask]
    if len(all_pts) < 3:
        return None

    # Fit a single circle to the combined point set
    cx, cy, R_fit = _fit_circle_kasa(all_pts)
    if cx is None or R_fit is None or not math.isfinite(float(R_fit)):
        return None
    R_fit = float(R_fit)
    cx, cy = float(cx), float(cy)
    if R_fit < min_radius or R_fit > 1e6:
        return None

    # Determine rotation from total deflection (must be consistent with Arc_A)
    defl = _segment_deflection(all_pts)
    rot  = A.get("rot", "ccw" if defl >= 0 else "cw")

    # Compute left junction (entry into merged arc)
    if idx > 0 and elements[idx - 1].get("type") == "Line":
        left_heading = elements[idx - 1].get("direction_rad", 0.0)
        jx, jy = _arc_line_tangent_junction(cx, cy, R_fit, rot, left_heading)
    else:
        sp = A.get("start", [0.0, 0.0])
        jx, jy = float(sp[0]), float(sp[1])

    # Compute right junction (exit from merged arc)
    if idx + 3 < len(elements) and elements[idx + 3].get("type") == "Line":
        right_heading = elements[idx + 3].get("direction_rad", 0.0)
        jx2, jy2 = _arc_line_tangent_junction(cx, cy, R_fit, rot, right_heading)
    else:
        ep = B.get("end", [0.0, 0.0])
        jx2, jy2 = float(ep[0]), float(ep[1])

    start_pt = np.array([jx,  jy],  dtype=float)
    end_pt   = np.array([jx2, jy2], dtype=float)

    # Build the merged arc's angular span
    a_s   = math.atan2(float(start_pt[1]) - cy, float(start_pt[0]) - cx)
    a_e   = math.atan2(float(end_pt[1])   - cy, float(end_pt[0])   - cx)
    delta = a_e - a_s
    if rot == "ccw":
        while delta <= 0.0:
            delta += 2.0 * math.pi
    else:
        while delta >= 0.0:
            delta -= 2.0 * math.pi

    if abs(delta) > math.pi:
        return None   # pathological geometry — skip

    sign    = 1.0 if rot == "ccw" else -1.0
    arc_len = R_fit * abs(delta)
    chord   = float(np.linalg.norm(end_pt - start_pt))

    merged_el: dict = {
        "type":        "Arc",
        "sta_start":   sta_start_A,
        "length":      arc_len,
        "start":       start_pt.tolist(),
        "end":         end_pt.tolist(),
        "center":      [cx, cy],
        "radius":      R_fit,
        "rot":         rot,
        "chord":       chord,
        "_deflection": sign * abs(delta),
    }

    # Assemble the new element list
    new_elements: list[dict] = elements[:idx] + [merged_el] + elements[idx + 3:]

    # Update the element immediately before if it is a Line
    if idx > 0 and new_elements[idx - 1].get("type") == "Line":
        prev_el = dict(new_elements[idx - 1])
        sp_prev = np.array(prev_el["start"], dtype=float)
        prev_el["end"]    = start_pt.tolist()
        prev_el["length"] = float(np.linalg.norm(start_pt - sp_prev))
        new_elements[idx - 1] = prev_el

    # Update the element immediately after if it is a Line
    after_idx = idx + 1   # merged_el sits at idx; the element after is idx+1
    if after_idx < len(new_elements) and new_elements[after_idx].get("type") == "Line":
        next_el = dict(new_elements[after_idx])
        ep_next = np.array(next_el["end"], dtype=float)
        next_el["start"]  = end_pt.tolist()
        next_el["length"] = float(np.linalg.norm(ep_next - end_pt))
        new_elements[after_idx] = next_el

    # Recompute sta_start for every element from idx onward
    if idx > 0:
        sta_running = (new_elements[idx - 1].get("sta_start", 0.0)
                       + new_elements[idx - 1].get("length",   0.0))
    else:
        sta_running = 0.0
    for k in range(idx, len(new_elements)):
        new_elements[k] = dict(new_elements[k])
        new_elements[k]["sta_start"] = sta_running
        sta_running += new_elements[k].get("length", 0.0)

    return new_elements


# ---------------------------------------------------------------------------
# C1 continuity post-processing
# ---------------------------------------------------------------------------

def _arc_tangent_heading(cx: float, cy: float, px: float, py: float, rot: str) -> float:
    """
    Tangent heading (rad) at point (px, py) on an arc with center (cx, cy).

    CCW arc:  tangent direction = (-sin θ, cos θ)  where θ = atan2(py-cy, px-cx)
              ⟹  atan2(dx, -dy)
    CW  arc:  tangent direction = ( sin θ, -cos θ)
              ⟹  atan2(-dx, dy)
    """
    dx = px - cx
    dy = py - cy
    if rot == "ccw":
        return math.atan2(dx, -dy)
    else:
        return math.atan2(-dx, dy)


def _compute_forced_line_ranges(
    xy:               np.ndarray,
    chainages:        np.ndarray,
    smooth_window:    int,
    min_kappa_radius: float,
    min_kappa_length: float,
) -> list[tuple[float, float]]:
    """
    Return list of (sta_start, sta_end) chainage pairs where smoothed |κ|
    is below 1/min_kappa_radius for at least min_kappa_length metres.

    Returns [] if min_kappa_radius <= 0 or min_kappa_length <= 0.
    """
    from geometry.curvature import compute_curvature, smooth_curvature

    if min_kappa_radius <= 0 or min_kappa_length <= 0:
        return []
    N = len(xy)
    if N < 3:
        return []

    threshold = 1.0 / min_kappa_radius
    kappa        = compute_curvature(xy)
    kappa_smooth = smooth_curvature(kappa, window=smooth_window)

    forced: list[tuple[float, float]] = []
    in_run    = False
    run_start = 0

    for i in range(N):
        if abs(float(kappa_smooth[i])) < threshold:
            if not in_run:
                in_run    = True
                run_start = i
        else:
            if in_run:
                in_run = False
                span = float(chainages[i - 1]) - float(chainages[run_start])
                if span >= min_kappa_length:
                    forced.append((
                        float(chainages[run_start]),
                        float(chainages[i - 1]),
                    ))

    if in_run:
        span = float(chainages[N - 1]) - float(chainages[run_start])
        if span >= min_kappa_length:
            forced.append((
                float(chainages[run_start]),
                float(chainages[N - 1]),
            ))

    return forced


def _merge_adjacent_line_elements(elements: list[dict]) -> list[dict]:
    """
    Merge any two or more consecutive Line elements into a single Line.
    Recomputes length, heading, and sta_start chain after merging.
    """
    if not elements:
        return elements

    merged: list[dict] = []
    for el in elements:
        if (merged
                and el.get("type") == "Line"
                and merged[-1].get("type") == "Line"):
            prev    = merged[-1]
            new_end = el["end"]
            sp      = np.array(prev["start"], dtype=float)
            ep      = np.array(new_end,       dtype=float)
            prev["end"]           = new_end
            prev["length"]        = float(np.linalg.norm(ep - sp))
            prev["direction_rad"] = math.atan2(
                float(ep[1] - sp[1]), float(ep[0] - sp[0])
            )
        else:
            merged.append(dict(el))

    # Recompute sta_start chain
    sta = 0.0
    for el in merged:
        el["sta_start"] = sta
        sta += el.get("length", 0.0)

    return merged


def _insert_line_line_connector_arc(
    elements:   list[dict],
    idx:        int,
    min_radius: float,
) -> list[dict] | None:
    """
    Insert a small circular arc of radius min_radius between the two consecutive
    Line elements at elements[idx] and elements[idx+1] to achieve C1 continuity.

    Returns the modified element list, or None if the arc cannot be fitted
    (collinear lines, or the tangent runout does not fit inside both elements).
    """
    A     = elements[idx]
    B     = elements[idx + 1]
    phi_A = A.get("direction_rad", 0.0)
    phi_B = B.get("direction_rad", 0.0)

    d_phi = phi_B - phi_A
    while d_phi >  math.pi: d_phi -= 2.0 * math.pi
    while d_phi < -math.pi: d_phi += 2.0 * math.pi

    if abs(d_phi) < 1e-4:
        return None   # effectively collinear — no arc needed

    T = min_radius * math.tan(abs(d_phi) / 2.0)
    len_A = A.get("length", 0.0)
    len_B = B.get("length", 0.0)
    if T <= 0 or T >= len_A * 0.45 or T >= len_B * 0.45:
        return None   # arc does not fit within the adjacent elements

    kink    = np.array(A["end"], dtype=float)
    cos_a   = math.cos(phi_A)
    sin_a   = math.sin(phi_A)
    cos_b   = math.cos(phi_B)
    sin_b   = math.sin(phi_B)

    tp_a = kink - T * np.array([cos_a, sin_a])   # tangent point on Line A
    tp_b = kink + T * np.array([cos_b, sin_b])   # tangent point on Line B

    rot = "ccw" if d_phi > 0 else "cw"
    R   = min_radius
    if rot == "ccw":
        center = tp_a + R * np.array([-sin_a,  cos_a])
    else:
        center = tp_a + R * np.array([ sin_a, -cos_a])

    arc_len = R * abs(d_phi)
    chord   = float(np.linalg.norm(tp_b - tp_a))
    sign    = 1.0 if rot == "ccw" else -1.0

    new_A = dict(A)
    new_A["end"]    = tp_a.tolist()
    new_A["length"] = float(np.linalg.norm(tp_a - np.array(A["start"], dtype=float)))

    b_end   = np.array(B["end"], dtype=float)
    new_B   = dict(B)
    new_B["start"]        = tp_b.tolist()
    new_B["length"]       = float(np.linalg.norm(b_end - tp_b))
    new_B["direction_rad"] = math.atan2(
        float(b_end[1] - tp_b[1]), float(b_end[0] - tp_b[0])
    )

    arc_el: dict = {
        "type":        "Arc",
        "sta_start":   new_A["sta_start"] + new_A["length"],
        "length":      arc_len,
        "start":       tp_a.tolist(),
        "end":         tp_b.tolist(),
        "center":      center.tolist(),
        "radius":      R,
        "rot":         rot,
        "chord":       chord,
        "_deflection": sign * abs(d_phi),
    }

    new_elements = elements[:idx] + [new_A, arc_el, new_B] + elements[idx + 2:]

    # Recompute sta_start chain from idx onward
    sta = new_A["sta_start"]
    for k in range(idx, len(new_elements)):
        new_elements[k] = dict(new_elements[k])
        new_elements[k]["sta_start"] = sta
        sta += new_elements[k].get("length", 0.0)

    return new_elements


def _enforce_c1_junctions(
    elements:           list[dict],
    min_radius:         float,
    max_junction_shift: float = 25.0,
    min_kink_rad:       float = 0.004,
) -> list[dict]:
    """
    Multi-pass junction correction to enforce C1 continuity.

    Junction types handled:
      Line→Arc  : move junction to tangent point on arc (tangent = line heading)
      Arc→Line  : same, symmetric
      Arc→Arc   : compute Arc_A tangent at junction; find tangent point on Arc_B
                  with that heading; update if shift < max_junction_shift

    Line→Line kinks are handled by _insert_line_line_connector_arc (called earlier
    in _post_process_elements).

    Runs up to 4 passes; stops early if no junction changed by more than 1e-4 m.
    Recomputes sta_start chain after all passes.
    """
    MAX_PASSES = 4

    def _recompute_arc_length(el: dict) -> float:
        cx   = el["center"][0];  cy  = el["center"][1]
        R    = el["radius"];     rot = el["rot"]
        sp   = el["start"];      ep  = el["end"]
        a_s  = math.atan2(sp[1] - cy, sp[0] - cx)
        a_e  = math.atan2(ep[1] - cy, ep[0] - cx)
        delta = a_e - a_s
        if rot == "ccw":
            while delta <= 0.0: delta += 2.0 * math.pi
        else:
            while delta >= 0.0: delta -= 2.0 * math.pi
        if abs(delta) > math.pi:
            return el.get("length", 0.0)   # pathological — keep old length
        return R * abs(delta)

    for _pass in range(MAX_PASSES):
        any_change = False
        elements   = [dict(e) for e in elements]

        for i in range(len(elements) - 1):
            L    = elements[i]
            R_el = elements[i + 1]
            lt   = L.get("type",   "Line")
            rt   = R_el.get("type","Line")

            if lt == "Line" and rt == "Arc":
                phi  = L.get("direction_rad", 0.0)
                cx   = R_el["center"][0];  cy = R_el["center"][1]
                R_v  = R_el["radius"];     rot = R_el["rot"]
                jx, jy = _arc_line_tangent_junction(cx, cy, R_v, rot, phi)
                cur    = np.array(L["end"], dtype=float)
                shift  = math.hypot(jx - cur[0], jy - cur[1])
                if 1e-4 < shift <= max_junction_shift:
                    new_j = [jx, jy]
                    elements[i]["end"]           = new_j
                    elements[i]["length"]         = float(np.linalg.norm(
                        np.array(new_j) - np.array(L["start"], dtype=float)
                    ))
                    elements[i]["direction_rad"]  = math.atan2(
                        new_j[1] - L["start"][1], new_j[0] - L["start"][0]
                    )
                    elements[i + 1]["start"]      = new_j
                    elements[i + 1]["length"]     = _recompute_arc_length(elements[i + 1])
                    any_change = True

            elif lt == "Arc" and rt == "Line":
                phi  = R_el.get("direction_rad", 0.0)
                cx   = L["center"][0];  cy = L["center"][1]
                R_v  = L["radius"];     rot = L["rot"]
                jx, jy = _arc_line_tangent_junction(cx, cy, R_v, rot, phi)
                cur    = np.array(L["end"], dtype=float)
                shift  = math.hypot(jx - cur[0], jy - cur[1])
                if 1e-4 < shift <= max_junction_shift:
                    new_j = [jx, jy]
                    elements[i]["end"]            = new_j
                    elements[i]["length"]          = _recompute_arc_length(elements[i])
                    elements[i + 1]["start"]       = new_j
                    elements[i + 1]["length"]      = float(np.linalg.norm(
                        np.array(R_el["end"], dtype=float) - np.array(new_j)
                    ))
                    elements[i + 1]["direction_rad"] = math.atan2(
                        R_el["end"][1] - new_j[1], R_el["end"][0] - new_j[0]
                    )
                    any_change = True

            elif lt == "Arc" and rt == "Arc":
                cur_j   = np.array(L["end"], dtype=float)
                cx_a    = L["center"][0];   cy_a = L["center"][1]
                heading_a = _arc_tangent_heading(cx_a, cy_a,
                                                  float(cur_j[0]), float(cur_j[1]),
                                                  L["rot"])
                cx_b   = R_el["center"][0]; cy_b = R_el["center"][1]
                R_b    = R_el["radius"];    rot_b = R_el["rot"]
                jx, jy = _arc_line_tangent_junction(cx_b, cy_b, R_b, rot_b, heading_a)
                shift  = math.hypot(jx - float(cur_j[0]), jy - float(cur_j[1]))
                if 1e-4 < shift <= max_junction_shift:
                    new_j = [jx, jy]
                    elements[i]["end"]        = new_j
                    elements[i]["length"]      = _recompute_arc_length(elements[i])
                    elements[i + 1]["start"]   = new_j
                    elements[i + 1]["length"]  = _recompute_arc_length(elements[i + 1])
                    any_change = True

        if not any_change:
            break

    # Recompute sta_start chain
    if elements:
        sta = elements[0].get("sta_start", 0.0)
        for el in elements:
            el["sta_start"] = sta
            sta += el.get("length", 0.0)

    return elements


def _post_process_elements(
    elements:         list[dict],
    forced_ch_ranges: list[tuple[float, float]],
    min_radius:       float,
    min_kink_rad:     float = 0.004,
) -> list[dict]:
    """
    Apply all C1 post-processing steps uniformly to any algorithm's output:

    1. Demote Arc elements that overlap a forced-line chainage range → Line
    2. Merge adjacent Line elements
    3. Insert connector arcs at Line→Line kinks (heading diff > min_kink_rad)
    4. Enforce C1 at Line→Arc, Arc→Line, Arc→Arc junctions
    5. Final merge of adjacent Lines (forced-line demotion may create new adjacencies)

    Returns a new element list.  Input is not modified.
    """
    if not elements:
        return elements

    # ── Step 1: demote Arcs that overlap forced-line chainage ranges ──────
    if forced_ch_ranges:
        out: list[dict] = []
        for el in elements:
            if el.get("type") == "Arc":
                sta0 = el.get("sta_start", 0.0)
                sta1 = sta0 + el.get("length", 0.0)
                overlap = any(
                    sta0 < r_end and sta1 > r_start
                    for r_start, r_end in forced_ch_ranges
                )
                if overlap:
                    sp = np.array(el["start"], dtype=float)
                    ep = np.array(el["end"],   dtype=float)
                    out.append({
                        "type":          "Line",
                        "sta_start":     sta0,
                        "length":        float(np.linalg.norm(ep - sp)),
                        "start":         el["start"],
                        "end":           el["end"],
                        "direction_rad": math.atan2(
                            float(ep[1] - sp[1]), float(ep[0] - sp[0])
                        ),
                    })
                    continue
            out.append(dict(el))
        elements = out

    # ── Step 2: merge adjacent Lines ──────────────────────────────────────
    elements = _merge_adjacent_line_elements(elements)

    # ── Step 3: insert connector arcs at Line→Line kinks ─────────────────
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(elements) - 1:
            if (elements[i].get("type") == "Line"
                    and elements[i + 1].get("type") == "Line"):
                phi_a = elements[i].get("direction_rad", 0.0)
                phi_b = elements[i + 1].get("direction_rad", 0.0)
                d_phi = phi_b - phi_a
                while d_phi >  math.pi: d_phi -= 2.0 * math.pi
                while d_phi < -math.pi: d_phi += 2.0 * math.pi
                if abs(d_phi) >= min_kink_rad:
                    new_els = _insert_line_line_connector_arc(elements, i, min_radius)
                    if new_els is not None:
                        elements = new_els
                        changed  = True
                        i        = max(0, i - 1)
                        continue
            i += 1

    # ── Step 4: enforce C1 at remaining junctions ────────────────────────
    elements = _enforce_c1_junctions(elements, min_radius, min_kink_rad=min_kink_rad)

    # ── Step 5: final Line merge (cleanup) ────────────────────────────────
    elements = _merge_adjacent_line_elements(elements)

    return elements


# ---------------------------------------------------------------------------
# PI / tangent-polygon alignment builder (Level 2 & Level 3)
# ---------------------------------------------------------------------------

def _douglas_peucker_indices(xy: np.ndarray, tol: float) -> list[int]:
    """
    Iterative Douglas-Peucker simplification. Returns sorted indices of the
    kept vertices (always includes 0 and len-1).
    """
    n = len(xy)
    if n <= 2:
        return list(range(n))
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        p0, p1 = xy[i0], xy[i1]
        seg = p1 - p0
        seg_len = float(np.hypot(seg[0], seg[1]))
        pts = xy[i0 + 1:i1]
        if seg_len < 1e-9:
            d = np.hypot(pts[:, 0] - p0[0], pts[:, 1] - p0[1])
        else:
            # Perpendicular distance to the chord
            d = np.abs((pts[:, 0] - p0[0]) * seg[1] - (pts[:, 1] - p0[1]) * seg[0]) / seg_len
        k = int(np.argmax(d))
        if float(d[k]) > tol:
            mid = i0 + 1 + k
            keep[mid] = True
            stack.append((i0, mid))
            stack.append((mid, i1))
    return [int(i) for i in np.nonzero(keep)[0]]


def _wrap_pi(a: float) -> float:
    """Wrap angle to (−π, π]."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _merge_pi_clusters(
    V: np.ndarray,
    idx: list[int],
    merge_dist: float,
    max_passes: int = 4,
) -> tuple[np.ndarray, list[int]]:
    """
    Merge runs of consecutive PIs that belong to one physical curve.

    Douglas-Peucker splits a long circular curve into several nearby vertices
    with same-sign deflections. Replacing such a pair with the intersection
    of the two *outer* tangents restores a single PI per curve, giving one
    long arc instead of a chain of short ones.

    V   : (m, 2) tangent-polygon vertices (V[0], V[-1] are the endpoints)
    idx : original polyline index of each vertex (for radius estimation)
    """
    V   = V.copy()
    idx = list(idx)
    for _ in range(max_passes):
        if len(V) < 4:
            break
        merged_any = False
        k = 1
        while k < len(V) - 2:
            A, P1, P2, B = V[k - 1], V[k], V[k + 1], V[k + 2]
            d_mid = float(np.hypot(*(P2 - P1)))
            if d_mid > merge_dist:
                k += 1
                continue
            phi_in   = math.atan2(P1[1] - A[1],  P1[0] - A[0])
            phi_mid  = math.atan2(P2[1] - P1[1], P2[0] - P1[0])
            phi_out  = math.atan2(B[1] - P2[1],  B[0] - P2[0])
            d1 = _wrap_pi(phi_mid - phi_in)
            d2 = _wrap_pi(phi_out - phi_mid)
            if d1 * d2 <= 0 or abs(d1) < 1e-4 or abs(d2) < 1e-4:
                k += 1
                continue
            # Intersect outer tangents: A→P1 ray with P2→B ray (backwards)
            sin_d = math.sin(_wrap_pi(phi_out - phi_in))
            if abs(sin_d) < 1e-9:
                k += 1
                continue
            dx = float(P2[0] - P1[0]); dy = float(P2[1] - P1[1])
            t  = (dx * math.sin(phi_out) - dy * math.cos(phi_out)) / sin_d
            if t <= 0:
                k += 1
                continue
            PI_new = P1 + t * np.array([math.cos(phi_in), math.sin(phi_in)])
            V   = np.vstack([V[:k], PI_new[None, :], V[k + 2:]])
            idx = idx[:k] + [(idx[k] + idx[k + 1]) // 2] + idx[k + 2:]
            merged_any = True
        if not merged_any:
            break
    return V, idx


# Construction constants shared by extract / rebuild
_PI_L_MIN       = 2.0     # minimum useful spiral length
_PI_EPS_ANG     = 1e-4    # deflections below this are treated as straight
_PI_MIN_TANGENT = 60.0    # Lines shorter than this between same-sense arcs
                          # trigger a PI merge (one physical curve)
_REFLEX_SINGULAR_DEG = 2.0   # a merged turn this close to +-180 deg is
                              # geometrically singular for ANY single circular
                              # PI (the two boundary tangents are near-
                              # parallel and never meet at a finite point) —
                              # refuse cleanly instead of building a runaway
                              # curve.


def _polygon_deflection(V: np.ndarray, k: int) -> float:
    """Signed deflection of the tangent polygon at interior vertex k."""
    phi_a = math.atan2(V[k, 1] - V[k - 1, 1], V[k, 0] - V[k - 1, 0])
    phi_b = math.atan2(V[k + 1, 1] - V[k, 1], V[k + 1, 0] - V[k, 0])
    return _wrap_pi(phi_b - phi_a)


def _fresh_pis(V: np.ndarray) -> list:
    """One auto-everything PIData per interior polygon vertex."""
    return [
        PIData(index=k, xy=(float(V[k, 0]), float(V[k, 1])),
               deflection=_polygon_deflection(V, k))
        for k in range(1, len(V) - 1)
    ]


def _required_tangent(R: float, half_tan: float, L: float) -> float:
    """Tangent length from the PI to TC for radius R and spiral length L."""
    from geometry.alignment import _compute_clothoid_shift
    if L >= _PI_L_MIN and R > 0:
        x_sp, y_sp = _compute_clothoid_shift(L, R)
        theta_s = L / (2.0 * R)
        p_sh = y_sp - R * (1.0 - math.cos(theta_s))
        k_ab = x_sp - R * math.sin(theta_s)
        return (R + p_sh) * half_tan + k_ab
    return R * half_tan


def _max_radius_for_tangent(T_allow: float, half_tan: float, L: float) -> float:
    """
    Largest radius whose tangent length fits within T_allow (monotonic in R;
    binary search). L is the intended spiral length (0 = plain arc).
    """
    if half_tan < 1e-9 or T_allow <= 0:
        return 1.0
    hi = T_allow / half_tan          # plain-arc upper bound (spiral only adds)
    lo = 1.0
    if _required_tangent(hi, half_tan, L) <= T_allow:
        return hi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if _required_tangent(mid, half_tan, L) <= T_allow:
            lo = mid
        else:
            hi = mid
    return lo


def _line_dict(p0, p1, direction=None) -> dict | None:
    """Connector Line element between two points; None if degenerate (<1e-6 m)."""
    length = float(np.hypot(*(p1 - p0)))
    if length < 1e-6:
        return None
    if direction is None:
        direction = math.atan2(float(p1[1] - p0[1]), float(p1[0] - p0[0]))
    return {
        "type": "Line", "sta_start": 0.0, "length": length,
        "start": p0.tolist(), "end": p1.tolist(),
        "direction_rad": direction,
    }


def _build_zone_range(model: "PIAlignment", k_lo: int, k_hi: int,
                      cur_pt) -> tuple:
    """
    Build the element chain for the interior PIs k_lo..k_hi (inclusive,
    vertex indices into model.V), starting from `cur_pt`.

    This is the single zone constructor: `rebuild_from_pi_model` runs it
    over all PIs, `rebuild_pi_span` over a window. Side effects match the
    pre-factor-out behaviour exactly: log notes are appended to model.log
    and the applied radius / spiral_len are written back into each PIData.

    Returns (elements, tangent_stubs, exit_point). The leading connector
    Line into each zone is included; the trailing connector after the last
    zone is NOT (the caller appends it — final tangent or splice connector).
    """
    from geometry.alignment import (
        _compute_zone_geometry, _compute_clothoid_shift, _fit_circle_kasa,
    )

    V, idx, xy = model.V, model.idx, model.xy_ref
    tol           = model.tol
    min_radius    = model.min_radius
    use_spirals   = model.use_spirals
    m = len(V)

    elements: list[dict] = []
    stubs:    list[dict] = []
    cur_pt = np.asarray(cur_pt, dtype=float).copy()

    def _append_line(p0, p1, direction=None):
        e = _line_dict(p0, p1, direction)
        if e is not None:
            elements.append(e)

    for k in range(max(1, k_lo), min(k_hi, m - 2) + 1):
        pi_data = model.pis[k - 1]
        if pi_data.omitted:
            continue          # self-healing: next curve absorbs the deflection

        # High-angle chain flags: a chained side carries no spiral, shares
        # no tangent margin, and joins the neighbour's arc on the same
        # circle at the leg's tangency point (zero connector).
        chained_next = bool(getattr(pi_data, "chain_next", False))
        chained_prev = bool(k >= 2
                            and getattr(model.pis[k - 2], "chain_next", False)
                            and not model.pis[k - 2].omitted)

        PI, B = V[k], V[k + 1]
        # Incoming direction from the ACTUAL current position (not the
        # polygon segment): this makes the construction self-healing —
        # any deflection lost at a skipped/omitted PI, or the mm-level
        # lateral offset of a previous CT, is absorbed by the next curve,
        # and tangency at the connecting Line is exact by definition.
        u_in_vec = PI - cur_pt
        d_in = float(np.hypot(*u_in_vec))
        if d_in < 1e-6:
            continue
        u_in  = u_in_vec / d_in
        u_out = B - PI;  u_out = u_out / max(1e-12, float(np.hypot(*u_out)))
        phi_in  = math.atan2(float(u_in[1]),  float(u_in[0]))
        phi_out = math.atan2(float(u_out[1]), float(u_out[0]))
        delta   = _wrap_pi(phi_out - phi_in)

        # A merge stores the SIGNED, UNWRAPPED total turn on merged_turn —
        # for |total| <= 180 deg this is numerically identical to the
        # wrapped delta above (both derived from the same PI/A/B), so the
        # override is a no-op there; for a reflex/major-arc merge (>180 deg)
        # it carries information the wrapped reading cannot (see
        # _build_pi_zone module docs / merge_pi_range).
        is_reflex = False
        if pi_data.merged_turn is not None:
            mt = float(pi_data.merged_turn)
            if abs(abs(mt) - math.pi) < math.radians(_REFLEX_SINGULAR_DEG):
                model.log.append(
                    f"⚠ PI {k}: merged turn {math.degrees(mt):+.1f}° is too "
                    "close to 180° — the boundary tangents are nearly "
                    "parallel and never meet at a finite point; curve "
                    "dropped.")
                continue
            delta = mt
            is_reflex = abs(mt) > math.pi

        if abs(delta) < _PI_EPS_ANG:
            continue    # collinear: tangents merge into one line

        if is_reflex:
            # Dedicated construction for turns > 180 deg: the ordinary
            # tangent-sharing / T_max clamping below assumes
            # tan(|delta|/2) > 0, which inverts sign past 180 deg. Radius
            # comes from a run-wide circle fit (or an explicit override);
            # entry/exit spirals attach via the same asymmetric
            # _compute_zone_geometry used everywhere else — it needs only a
            # start point/heading and the (unrestricted-magnitude) signed
            # delta, so it generalises to a reflex sweep with no changes.
            sign      = 1.0 if delta >= 0 else -1.0
            abs_delta = abs(delta)
            half_tan  = math.tan(abs_delta / 2.0)   # negative — expected

            d_out_room = float(np.hypot(*(B - PI)))
            d_in_room  = d_in

            if pi_data.radius > 0:
                R = float(pi_data.radius)
            else:
                fit = _fit_run_circle(model, k, k)
                if fit is None:
                    model.log.append(
                        f"⚠ PI {k}: could not fit a radius for the "
                        f"{math.degrees(delta):+.1f}° merged curve — "
                        "dropped.")
                    continue
                R = max(min_radius, float(fit[2]))
                pi_data.radius_auto = R

            L_target = 0.0
            if use_spirals:
                L_target = (float(model.spiral_default)
                           if pi_data.spiral_len < 0
                           else float(pi_data.spiral_len))
            L = L_target if L_target >= _PI_L_MIN else 0.0

            def _reflex_tangent(R_try, L_try):
                ht = math.tan(abs_delta / 2.0)
                if L_try > 0.0:
                    theta_s = L_try / (2.0 * R_try)
                    if abs_delta - 2.0 * theta_s < 0.01:
                        return None
                    x_sp, y_sp = _compute_clothoid_shift(L_try, R_try)
                    p_sh = y_sp - R_try * (1.0 - math.cos(theta_s))
                    k_ab = x_sp - R_try * math.sin(theta_s)
                    T_try = (R_try + p_sh) * ht + k_ab
                else:
                    T_try = R_try * ht
                if abs(T_try) <= min(d_in_room, d_out_room) - 0.5:
                    return T_try
                return None

            T_s = _reflex_tangent(R, L)
            if T_s is None and L > 0.0:
                L = 0.0
                T_s = _reflex_tangent(R, L)
            if T_s is None:
                # Do NOT silently shrink R to make it fit — that would
                # build a curve that no longer matches the OSM points the
                # radius was fitted to. A reflex/major-arc curve genuinely
                # needs tangent room on the order of R*|tan(turn/2)|; if
                # the fixed boundary tangents are too short for the fitted
                # radius, that's a real infeasibility to report, not paper
                # over.
                need = abs(R * math.tan(abs_delta / 2.0))
                model.log.append(
                    f"⚠ PI {k}: a {math.degrees(delta):+.1f}° curve at "
                    f"R={R:.0f} m needs about {need:.0f} m of straight "
                    f"tangent on each side; only "
                    f"{min(d_in_room, d_out_room):.0f} m is available here "
                    "— dropped.")
                continue

            TC   = PI - T_s * u_in
            zone = _compute_zone_geometry(TC, phi_in, delta, R,
                                          L_entry=L, L_exit=L)
            if zone is None:
                model.log.append(
                    f"⚠ PI {k}: reflex curve construction failed — "
                    "dropped.")
                continue
            _append_line(cur_pt, TC, direction=phi_in)
            zas, zac, zae = (zone["arc_start"], zone["arc_center"],
                             zone["arc_end"])
            CT, z_arc_len = zone["CT"], zone["arc_len"]
            rot = "ccw" if delta >= 0 else "cw"
            if L > 0.0:
                A_cl = math.sqrt(R * L)
                elements.append({
                    "type": "Spiral", "sta_start": 0.0, "length": L,
                    "start": TC.tolist(), "end": zas.tolist(),
                    "radius_start": float("inf"), "radius_end": R,
                    "clothoid_A": A_cl, "rot": rot, "_pi": k,
                })
            elements.append({
                "type": "Arc", "sta_start": 0.0, "length": z_arc_len,
                "start": zas.tolist(), "end": zae.tolist(),
                "center": zac.tolist(), "radius": R, "rot": rot,
                "chord": float(np.hypot(*(zae - zas))),
                "_deflection": zone["arc_angle"] * sign, "_pi": k,
            })
            if L > 0.0:
                elements.append({
                    "type": "Spiral", "sta_start": 0.0, "length": L,
                    "start": zae.tolist(), "end": CT.tolist(),
                    "radius_start": R, "radius_end": float("inf"),
                    "clothoid_A": A_cl, "rot": rot, "_pi": k,
                })
            stubs.append({
                "pi": k, "pi_xy": [float(PI[0]), float(PI[1])],
                "tc": TC.tolist(), "ct": np.array(CT, dtype=float).tolist(),
            })
            cur_pt = np.array(CT, dtype=float)
            pi_data.radius = R
            if use_spirals:
                pi_data.spiral_len = L
                if pi_data.spiral_len_auto <= 0.0 and L > 0.0:
                    pi_data.spiral_len_auto = L
            continue

        # Available beyond the PI: share the next segment with the next PI.
        # Merged PI pairs are exempt from the sharing margins: their spiral
        # tangent lengths are solved to meet exactly (T_s,a + T_s,b = D).
        d_seg_out = float(np.hypot(*(B - PI)))
        if pi_data.merged_with_next or chained_next:
            d_out = d_seg_out
        else:
            d_out = d_seg_out * (0.5 if k < m - 2 else 1.0) - 0.1
        d_in_eff = d_in if (pi_data.merged_with_prev or chained_prev) \
            else d_in - 0.1
        T_max = min(d_in_eff, d_out)
        if T_max <= 0.5:
            continue    # no room at all — drop the curve (stay on tangent)

        half_tan = math.tan(abs(delta) / 2.0)
        if half_tan < 1e-9:
            continue

        # ── Spiral length target (auto / override / none) ────────────────
        L_target = 0.0
        if use_spirals:
            if pi_data.spiral_len < 0:          # auto
                L_target = float(model.spiral_default)
            else:                                # override (may be 0 = none)
                L_target = float(pi_data.spiral_len)
        if chained_prev and chained_next:
            L_target = 0.0        # chain interior: pure arc on the circle
        L_res = L_target if L_target >= _PI_L_MIN else 0.0
        is_merged = (pi_data.merged_with_next or pi_data.merged_with_prev
                     or chained_prev or chained_next)

        # ── Radius: override or auto-estimate from the OSM points ────────
        if is_merged:
            # Merged pairs use exactly solved spiral lengths; no reserve or
            # safety factor — otherwise the radius clamp would shorten T_s
            # and re-open the gap.
            R_fit_max = max(1.0, T_max / half_tan)
        else:
            R_fit_max = max(1.0, (T_max - 0.55 * L_res) * 0.98 / half_tan)
            if R_fit_max < min_radius and L_res > 0.0:
                R_fit_max = max(1.0, (T_max * 0.98) / half_tan)

        if pi_data.radius > 0:
            R_req = float(pi_data.radius)
            if is_merged:
                # Merged pairs manage their own solved lengths.
                R = min(R_req, R_fit_max)
                if R_req > R + 1.0:
                    model.log.append(
                        f"⚠ PI {k}: radius {R_req:.0f} m clamped to {R:.0f} m "
                        f"(merged-curve tangent limit).")
            else:
                # Honour an explicit override generously: reserve only a small
                # part of the OUTGOING straight for the following curve (not the
                # fair half-share used for auto radii), so larger radii that are
                # still geometrically valid are accepted. Relax T_max to match
                # so the downstream spiral guard does not shrink R back.
                if k < m - 2:
                    reserve  = min(0.30 * d_seg_out, 60.0)
                    d_out_ov = max(0.5, d_seg_out - reserve)
                else:
                    d_out_ov = d_seg_out - 0.1
                T_allow = max(0.5, min(d_in_eff, d_out_ov))
                R_cap   = _max_radius_for_tangent(T_allow, half_tan, L_res)
                if R_req <= R_cap + 1e-6:
                    R = R_req
                    T_max = max(T_max, T_allow)   # let downstream honour it
                    if R_req > R_fit_max + 1.0:
                        model.log.append(
                            f"PI {k}: radius {R_req:.0f} m applied (uses more "
                            f"than half of the adjacent straight).")
                else:
                    R = R_cap
                    T_max = max(T_max, T_allow)
                    model.log.append(
                        f"⚠ PI {k}: radius {R_req:.0f} m does not fit — the "
                        f"tangent point would overrun the neighbouring curve. "
                        f"Largest that fits here is {R_cap:.0f} m; applied that.")
        else:
            # Auto: Kasa circle fit restricted to the *curved* points only —
            # points close to either tangent line belong to the straights and
            # bias the fit towards a larger radius, so they are masked out.
            i_lo = (idx[k - 1] + idx[k]) // 2
            i_hi = (idx[k] + idx[k + 1]) // 2
            R_est = None
            if i_hi - i_lo >= 3:
                seg_pts = xy[i_lo:i_hi + 1]
                rel     = seg_pts - PI
                d_in_l  = np.abs(rel[:, 0] * u_in[1]  - rel[:, 1] * u_in[0])
                d_out_l = np.abs(rel[:, 0] * u_out[1] - rel[:, 1] * u_out[0])
                lim     = max(2.0 * tol, 1.0)
                mask    = (d_in_l > lim) & (d_out_l > lim)
                fit_pts = seg_pts[mask] if int(mask.sum()) >= 5 else seg_pts
                _, _, r = _fit_circle_kasa(fit_pts)
                if r is not None and 10.0 < r < 1e6:
                    R_est = float(r)
                # Cross-check with the external-distance formula for
                # pronounced curves: E = R·(sec(δ/2) − 1), where E is the
                # closest approach of the polyline to the PI.
                if abs(delta) > math.radians(12.0):
                    E = float(np.min(np.hypot(rel[:, 0], rel[:, 1])))
                    sec_m1 = 1.0 / math.cos(abs(delta) / 2.0) - 1.0
                    if sec_m1 > 1e-9 and E > 0.05:
                        R_E = E / sec_m1
                        if 10.0 < R_E < 1e6:
                            R_est = R_E if R_est is None else 0.5 * (R_est + R_E)
            if R_est is None:
                R = min(max(min_radius, R_fit_max * 0.5), R_fit_max)
            else:
                R = min(max(R_est, min_radius), R_fit_max)
            pi_data.radius_auto = R
        if R < 1.0:
            continue
        # Guard: if even R_fit_max < min_radius we accept the smaller
        # radius — C1 continuity is prioritised over the radius floor.

        sign = 1.0 if delta >= 0 else -1.0
        rot  = "ccw" if delta >= 0 else "cw"

        # ── Spiral length with graceful degradation ──────────────────────
        chain_boundary = chained_prev != chained_next
        L = 0.0
        if L_target >= _PI_L_MIN:
            L = L_target
            # Arc must retain some angle: |δ| − L/R > EPS
            L = min(L, 0.9 * R * (abs(delta) - 0.01))
            if not chain_boundary:
                # Tangent must fit: T_s ≈ R·tan + L/2  ≤ T_max
                # (merged pairs use the exact solved length — no safety factor)
                slack = 1.0 if is_merged else 0.9
                L = min(L, 2.0 * (T_max - R * half_tan) * slack)
            if L < _PI_L_MIN:
                L = 0.0     # no spiral — fall back to plain arc

        zone_ok = False
        if L > 0.0 and chain_boundary:
            # ── One-sided spiral at a chain end ──────────────────────────
            # The chained side carries no spiral (the arc continues on the
            # same circle at the leg's tangency point); the outer side gets
            # the full transition. Unequal-tangent solution: the arc center
            # sits at distance R+p from the spiral-side leg and R from the
            # chained leg, giving
            #   Ts(spiral side)  = k + (R − (R+p)·cosδ)/sinδ
            #   Ts(chained side) =     (R + p − R·cosδ)/sinδ
            x_sp, y_sp = _compute_clothoid_shift(L, R)
            theta_s = L / (2.0 * R)
            p_sh = y_sp - R * (1.0 - math.cos(theta_s))
            k_ab = x_sp - R * math.sin(theta_s)
            sin_d = math.sin(abs(delta))
            cos_d = math.cos(abs(delta))
            if sin_d > 1e-9 and abs(delta) - theta_s > 0.01:
                if chained_next:      # spiral on the ENTRY side
                    Ts1 = k_ab + (R - (R + p_sh) * cos_d) / sin_d
                    Ts2 = (R + p_sh - R * cos_d) / sin_d
                    L_in, L_out = L, 0.0
                else:                 # spiral on the EXIT side
                    Ts1 = (R + p_sh - R * cos_d) / sin_d
                    Ts2 = k_ab + (R - (R + p_sh) * cos_d) / sin_d
                    L_in, L_out = 0.0, L
                if 0.0 < Ts1 <= d_in_eff + 1e-6 and 0.0 < Ts2 <= d_out + 1e-6:
                    TC = PI - Ts1 * u_in
                    zone = _compute_zone_geometry(TC, phi_in, delta, R,
                                                  L_entry=L_in, L_exit=L_out)
                    if zone is not None:
                        zone_ok = True
                        _append_line(cur_pt, TC, direction=phi_in)
                        zas, zac, zae = (zone["arc_start"],
                                         zone["arc_center"], zone["arc_end"])
                        CT, z_arc_len = zone["CT"], zone["arc_len"]
                        A_cl = math.sqrt(R * L)
                        if L_in > 0.0:
                            elements.append({
                                "type": "Spiral", "sta_start": 0.0, "length": L,
                                "start": TC.tolist(), "end": zas.tolist(),
                                "radius_start": float("inf"), "radius_end": R,
                                "clothoid_A": A_cl, "rot": rot, "_pi": k,
                            })
                        elements.append({
                            "type": "Arc", "sta_start": 0.0, "length": z_arc_len,
                            "start": zas.tolist(), "end": zae.tolist(),
                            "center": zac.tolist(), "radius": R, "rot": rot,
                            "chord": float(np.hypot(*(zae - zas))),
                            "_deflection": zone["arc_angle"] * sign, "_pi": k,
                        })
                        if L_out > 0.0:
                            elements.append({
                                "type": "Spiral", "sta_start": 0.0, "length": L,
                                "start": zae.tolist(), "end": CT.tolist(),
                                "radius_start": R, "radius_end": float("inf"),
                                "clothoid_A": A_cl, "rot": rot, "_pi": k,
                            })
                        stubs.append({
                            "pi": k, "pi_xy": [float(PI[0]), float(PI[1])],
                            "tc": TC.tolist(),
                            "ct": np.array(CT, dtype=float).tolist(),
                        })
                        cur_pt = np.array(CT, dtype=float)
            if not zone_ok:
                L = 0.0               # degrade to a pure arc (C1 kept)

        # Chain-boundary zone already built (or fell back to L=0 above) —
        # the symmetric branches below are for the ordinary (non-chained)
        # case only, and must not re-run against the chain result.
        if L > 0.0 and not chain_boundary:
            # Exact Fresnel-based shift p and abscissa k
            x_sp, y_sp = _compute_clothoid_shift(L, R)
            theta_s = L / (2.0 * R)
            p_sh = y_sp - R * (1.0 - math.cos(theta_s))
            k_ab = x_sp - R * math.sin(theta_s)
            T_s  = (R + p_sh) * half_tan + k_ab
            if T_s > T_max + 1e-6:
                # One shrink pass on R, then give up the spiral
                R_new = max(1.0, (T_max - k_ab) / half_tan - p_sh)
                if R_new >= min(min_radius, R):
                    R = R_new
                    x_sp, y_sp = _compute_clothoid_shift(L, R)
                    theta_s = L / (2.0 * R)
                    p_sh = y_sp - R * (1.0 - math.cos(theta_s))
                    k_ab = x_sp - R * math.sin(theta_s)
                    T_s  = (R + p_sh) * half_tan + k_ab
                if T_s > T_max + 1e-6 or abs(delta) - L / R < 0.01:
                    L = 0.0

        if L > 0.0 and not chain_boundary:
            TC   = PI - T_s * u_in
            zone = _compute_zone_geometry(TC, phi_in, delta, R,
                                          L_entry=L, L_exit=L)
            if zone is not None:
                zone_ok = True
                _append_line(cur_pt, TC, direction=phi_in)
                zas, zac, zae = zone["arc_start"], zone["arc_center"], zone["arc_end"]
                CT, z_arc_len = zone["CT"], zone["arc_len"]
                A_cl = math.sqrt(R * L)
                elements.append({
                    "type": "Spiral", "sta_start": 0.0, "length": L,
                    "start": TC.tolist(), "end": zas.tolist(),
                    "radius_start": float("inf"), "radius_end": R,
                    "clothoid_A": A_cl, "rot": rot, "_pi": k,
                })
                elements.append({
                    "type": "Arc", "sta_start": 0.0, "length": z_arc_len,
                    "start": zas.tolist(), "end": zae.tolist(),
                    "center": zac.tolist(), "radius": R, "rot": rot,
                    "chord": float(np.hypot(*(zae - zas))),
                    "_deflection": zone["arc_angle"] * sign, "_pi": k,
                })
                elements.append({
                    "type": "Spiral", "sta_start": 0.0, "length": L,
                    "start": zae.tolist(), "end": CT.tolist(),
                    "radius_start": R, "radius_end": float("inf"),
                    "clothoid_A": A_cl, "rot": rot, "_pi": k,
                })
                stubs.append({
                    "pi": k, "pi_xy": [float(PI[0]), float(PI[1])],
                    "tc": TC.tolist(), "ct": np.array(CT, dtype=float).tolist(),
                })
                cur_pt = np.array(CT, dtype=float)
            # zone failed → fall through to plain arc

        if not zone_ok:
            if L > 0.0:
                L = 0.0
            # ── Plain circular arc (Level 2, or Level-3 fallback) ────────
            T  = R * half_tan
            TS = PI - T * u_in
            ST = PI + T * u_out
            perp   = phi_in + sign * math.pi / 2.0
            center = TS + R * np.array([math.cos(perp), math.sin(perp)])
            _append_line(cur_pt, TS, direction=phi_in)
            elements.append({
                "type": "Arc", "sta_start": 0.0, "length": R * abs(delta),
                "start": TS.tolist(), "end": ST.tolist(),
                "center": center.tolist(), "radius": R, "rot": rot,
                "chord": float(np.hypot(*(ST - TS))),
                "_deflection": delta, "_pi": k,
            })
            stubs.append({
                "pi": k, "pi_xy": [float(PI[0]), float(PI[1])],
                "tc": TS.tolist(), "ct": ST.tolist(),
            })
            cur_pt = ST.copy()

        # Write back the *applied* values so the table shows reality and
        # the next rebuild reproduces this result exactly.
        pi_data.radius = R
        if use_spirals:
            pi_data.spiral_len = L
            if pi_data.spiral_len_auto <= 0.0 and L > 0.0:
                pi_data.spiral_len_auto = L

    return elements, stubs, cur_pt


def rebuild_from_pi_model(model: "PIAlignment", progress_cb=None) -> list[dict]:
    """
    (Re)construct the C1-continuous element chain from the PI model,
    honouring per-PI overrides (radius, spiral_len, omitted).

    Level 2 (model.use_spirals=False):  Line – Arc – Line …
    Level 3 (model.use_spirals=True):   Line – Spiral – Arc – Spiral – Line …

    Guarantees
    ----------
    • The first element starts exactly at xy_ref[0]; the last element ends
      exactly at xy_ref[-1] (the original OSM endpoints).
    • Every junction is tangent (C1): arcs meet their tangents at
      T = R·tan(|δ|/2); spirals use the exact Fresnel shift
      p = y_sp − R(1−cos θ_s), k = x_sp − R·sin θ_s,
      T_s = (R+p)·tan(|δ|/2) + k, so the L–S–A–S–L zone closes on the
      outgoing tangent by construction.
    • Spiral radius continuity: radius at the arc side equals the arc
      radius exactly; radius at the line side is ∞.
    • Where geometry does not fit (short tangents, tiny deflection) the
      builder degrades gracefully: shorter spiral → no spiral (plain arc)
      → smaller radius, in that order. C1 is never abandoned. Overridden
      values are clamped by the same guards and the *applied* value is
      written back into the PIData (visible in the element table).
    • Omitted PIs are skipped; the self-healing incoming direction absorbs
      their deflection into the next curve.

    Side effects: fills model.elements, model.tangent_stubs, and writes the
    applied radius / spiral_len (+ *_auto fields when auto-estimated) back
    into each PIData. Elements carry stable "_pi" and "element_id" keys.
    """
    from geometry.alignment import (
        _compute_zone_geometry, _compute_clothoid_shift, _fit_circle_kasa,
    )

    V, idx, xy = model.V, model.idx, model.xy_ref
    tol           = model.tol
    min_radius    = model.min_radius
    use_spirals   = model.use_spirals
    m = len(V)

    model.tangent_stubs = []
    model.log = []

    if m < 2:
        model.elements = []
        return model.elements
    if m == 2:
        model.elements = _two_point_line(xy, model.chainages_ref)
        _assign_element_ids(model.elements)
        return model.elements

    elements, stubs, cur_pt = _build_zone_range(model, 1, m - 2, V[0])
    model.tangent_stubs = stubs

    # Final tangent to the exact OSM end point
    _tail = _line_dict(cur_pt, V[-1].copy())
    if _tail is not None:
        elements.append(_tail)

    # ── Station chain + stable element ids + deviation stats ─────────────
    sta = 0.0
    for e in elements:
        e["sta_start"] = sta
        sta += e.get("length", 0.0)
    _assign_element_ids(elements)
    # Single deviation pass; kept on the model so the GUI can reuse it
    # instead of running a second (identical) evaluation.
    model.last_stats = annotate_element_deviations(elements, xy) or {}

    model.elements = elements
    return elements


def annotate_element_deviations(elements: list[dict], xy: np.ndarray,
                                stats: dict | None = None) -> dict | None:
    """
    Attach per-element deviation statistics (_max_dev / _mean_dev, metres) by
    assigning every OSM point to its nearest element. Used by the map hover
    popup and by the Consolidate step's tolerance check.

    Pass a precomputed `stats` (from `compute_deviation_stats`) to reuse a
    pass that has already been made; otherwise one is computed here. Returns
    the stats so callers can reuse them again.
    """
    if not elements or xy is None or len(xy) == 0:
        return stats
    if stats is None:
        stats = compute_deviation_stats(elements, xy)
    per = stats.get("per_element") or []
    for i, el in enumerate(elements):
        if i < len(per):
            el["_max_dev"], el["_mean_dev"] = per[i][0], per[i][1]
        else:
            el["_max_dev"] = el["_mean_dev"] = 0.0
    return stats


def _assign_element_ids(elements: list[dict]) -> None:
    """
    Stable, human-readable ids: Lines numbered sequentially (L1, L2, …);
    Arcs/Spirals tied to their PI index (A3, S3-in, S3-out).
    """
    line_no = 0
    for i, e in enumerate(elements):
        et = e.get("type")
        if et == "Line":
            line_no += 1
            e["element_id"] = f"L{line_no}"
        elif et == "Arc":
            k = e.get("_pi")
            e["element_id"] = f"A{k}" if k is not None else f"A?{i}"
        elif et == "Spiral":
            k = e.get("_pi")
            if k is None:
                e["element_id"] = f"S?{i}"
            else:
                r_st = float(e.get("radius_start", float("inf")))
                e["element_id"] = f"S{k}-in" if math.isinf(r_st) else f"S{k}-out"


# ---------------------------------------------------------------------------
# Span-local rebuild — the merge-click fast path
# ---------------------------------------------------------------------------
# A zone's geometry depends only on its two polygon legs, its PIData and the
# entry point carried in from the previous zone. Editing PIs [k_lo, k_hi]
# can therefore only change elements from zone k_lo-1 (its outgoing leg
# moved) up to the first downstream zone that rebuilds identically to its
# old self — everything beyond is provably unchanged. rebuild_pi_span
# exploits this: it rebuilds a handful of zones instead of thousands and
# refreshes the deviation stats from the stored per-point arrays instead of
# re-running the full E×P pass.

_SPAN_EQ_TOL = 1e-9
_SPAN_MAX_EXTEND = 6      # zones to try past k_hi+1 before giving up

# Diagnostic: why span rebuilds fell back to the full path (reason → count)
span_fallback_counts: dict = {}


def _span_fallback(reason: str) -> None:
    span_fallback_counts[reason] = span_fallback_counts.get(reason, 0) + 1


def _element_geom_equal(a: dict, b: dict, tol: float = _SPAN_EQ_TOL) -> bool:
    """Geometric equality of two element dicts (type + key numbers to 1e-9)."""
    if a.get("type") != b.get("type"):
        return False
    for key in ("length", "radius", "radius_start", "radius_end",
                "clothoid_A", "_deflection"):
        va, vb = a.get(key), b.get(key)
        if (va is None) != (vb is None):
            return False
        if va is not None:
            va, vb = float(va), float(vb)
            if math.isinf(va) or math.isinf(vb):
                if va != vb:
                    return False
            elif abs(va - vb) > tol:
                return False
    for key in ("start", "end", "center"):
        va, vb = a.get(key), b.get(key)
        if (va is None) != (vb is None):
            return False
        if va is not None and (abs(va[0] - vb[0]) > tol
                               or abs(va[1] - vb[1]) > tol):
            return False
    return True


def _zone_spans(elements: list[dict]) -> dict:
    """_pi tag → (first_index, last_index) of that zone's tagged elements."""
    spans: dict = {}
    for i, e in enumerate(elements):
        k = e.get("_pi")
        if k is None:
            continue
        if k in spans:
            spans[k] = (spans[k][0], i)
        else:
            spans[k] = (i, i)
    return spans


def _span_snapshot(model: "PIAlignment") -> dict:
    """Everything a splice mutates, for O(span) rollback without a rebuild."""
    st = model.last_stats or {}
    stats_copy: dict = {
        "max_deviation": st.get("max_deviation", 0.0),
        "rmse":          st.get("rmse", 0.0),
        "per_element":   list(st.get("per_element") or []),
    }
    if st.get("point_dist") is not None:
        stats_copy["point_dist"] = st["point_dist"].copy()
    if st.get("point_elem") is not None:
        stats_copy["point_elem"] = st["point_elem"].copy()
    return {
        "elements": model.elements,
        "stubs":    model.tangent_stubs,
        "stats":    stats_copy,
        # write-back fields by PIData *reference*: shared objects survive
        # any list swap the caller does during its own rollback
        "pi_vals":  [(p, p.radius, p.radius_auto, p.spiral_len,
                      p.spiral_len_auto) for p in model.pis],
    }


def restore_span_snapshot(model: "PIAlignment", snap: dict) -> None:
    """Rollback of a rebuild_pi_span splice (call BEFORE restoring caller-
    managed PIData fields — this rewrites the rebuild write-backs)."""
    for p, r, ra, sl, sla in snap["pi_vals"]:
        p.radius, p.radius_auto = r, ra
        p.spiral_len, p.spiral_len_auto = sl, sla
    model.elements = snap["elements"]
    model.tangent_stubs = snap["stubs"]
    model.last_stats = snap["stats"]
    # The splice may have overwritten _max_dev / ids / sta on the kept
    # prefix dicts — rewrite them from the restored stats (all O(E)-cheap).
    annotate_element_deviations(model.elements, model.xy_ref,
                                stats=model.last_stats)
    sta = 0.0
    for e in model.elements:
        e["sta_start"] = sta
        sta += e.get("length", 0.0)
    _assign_element_ids(model.elements)


def rebuild_pi_span(model: "PIAlignment", k_lo: int, k_hi: int, *,
                    old_lo: int | None = None, old_hi: int | None = None,
                    k_shift: int = 0) -> dict | None:
    """
    Span-local alternative to `rebuild_from_pi_model`.

    Call AFTER mutating model.V/idx/pis. Rebuilds zones [k_lo-1 … ] (new
    indexing) until the chain re-converges with the old elements, splices
    the result into model.elements and updates model.last_stats from the
    per-point arrays. `old_lo`/`old_hi` give the edited range in the OLD
    `_pi` tagging (default k_lo/k_hi); `k_shift` = new_tag − old_tag for
    zones after old_hi (−removed for a range merge, +1 for an insert).

    Returns a rollback snapshot (see `restore_span_snapshot`) on success or
    None when it fell back to a full `rebuild_from_pi_model` — the model is
    valid either way, and the result is bit-identical to a full rebuild by
    construction (same zone constructor, convergence checked to 1e-9,
    window deviation recomputed against ALL elements with the exact
    semantics of the full pass).
    """
    if old_lo is None:
        old_lo = k_lo
    if old_hi is None:
        old_hi = k_hi

    V, idx, xy = model.V, model.idx, model.xy_ref
    m = len(V)
    old_elements = model.elements
    st = model.last_stats or {}
    pd, pe = st.get("point_dist"), st.get("point_elem")
    if (m <= 3 or not old_elements or pd is None or pe is None
            or len(pd) != len(xy) or xy is None or len(xy) == 0):
        _span_fallback("no-point-arrays")
        rebuild_from_pi_model(model)
        return None

    snap = _span_snapshot(model)
    old_zones = _zone_spans(old_elements)

    # ── upstream boundary: zone k_lo-1 must be rebuilt (its outgoing leg
    # may have moved); zone k_lo-2 is provably unchanged ────────────────────
    a = max(1, k_lo - 1)
    prev_tags = [t for t in old_zones if t < a]     # a < old_lo ⇒ same tags
    if prev_tags:
        i_prev_last = old_zones[max(prev_tags)][1]
        i0 = i_prev_last + 1
        cur_pt = np.asarray(old_elements[i_prev_last]["end"], dtype=float)
    else:
        i0 = 0
        cur_pt = V[0].copy()

    # ── build forward until a rebuilt zone matches its old self exactly ─────
    built: list = []
    built_stubs: list = []
    j = a
    converged_at = None
    i1_end = len(old_elements)          # slice end (exclusive) in old list
    hard_stop = min(m - 2, k_hi + 1 + _SPAN_MAX_EXTEND)
    while j <= m - 2:
        els_j, stubs_j, cur_pt = _build_zone_range(model, j, j, cur_pt)
        if j > k_hi:
            osp = old_zones.get(j - k_shift)
            new_zone_els = [e for e in els_j if e.get("_pi") is not None]
            if osp is not None and new_zone_els:
                old_zone_els = old_elements[osp[0]:osp[1] + 1]
                if (len(old_zone_els) == len(new_zone_els)
                        and all(_element_geom_equal(x, y) for x, y
                                in zip(old_zone_els, new_zone_els))):
                    # keep the (possibly moved) connector, drop the
                    # identical rebuilt zone, keep old from here on
                    first_tagged = next(i for i, e in enumerate(els_j)
                                        if e.get("_pi") is not None)
                    built.extend(els_j[:first_tagged])
                    converged_at = j
                    i1_end = osp[0]
                    break
            if j >= hard_stop and osp is not None:
                # chain refuses to re-converge — bail out to a full rebuild
                _span_fallback("no-convergence")
                restore_span_snapshot(model, snap)
                rebuild_from_pi_model(model)
                return None
        built.extend(els_j)
        built_stubs.extend(stubs_j)
        j += 1
    else:
        # rebuilt to the last PI — replace through the end + final tangent
        tail = _line_dict(cur_pt, V[-1].copy())
        if tail is not None:
            built.append(tail)

    # ── OSM point window (new idx space; ±2 PI windows of margin) ───────────
    j_end = converged_at if converged_at is not None else (m - 2)
    # Low bound: the slice's leading connector reaches back to the previous
    # BUILT zone, which can be far behind a-2 when intervening PIs are
    # omitted or their curves were dropped — the window must cover it.
    prev_tag = max(prev_tags) if prev_tags else 0
    lo_pt = int(idx[max(0, min(a - 2, prev_tag - 1))])
    hi_pt = int(idx[min(m - 1, j_end + 2)])

    # The window must contain every point currently assigned into the
    # replaced slice (in dense PI clusters ownership wanders far beyond any
    # fixed PI margin), so extend it by the actual ownership range.
    owned = np.nonzero((pe >= i0) & (pe < i1_end))[0]
    if owned.size:
        lo_pt = min(lo_pt, int(owned[0]))
        hi_pt = max(hi_pt, int(owned[-1]))

    # ── assemble the new element list (tail dicts copied for rollback) ──────
    delta_el = len(built) - (i1_end - i0)
    new_tail = []
    for e in old_elements[i1_end:]:
        c = dict(e)
        if k_shift and c.get("_pi") is not None:
            c["_pi"] = c["_pi"] + k_shift
        new_tail.append(c)
    new_list = old_elements[:i0] + built + new_tail

    # ── incremental deviation update ─────────────────────────────────────────
    if delta_el:
        tail_mask = pe >= i1_end
        pe[tail_mask] += delta_el
    # Candidate elements: everything from two zones before the window's
    # first zone (a−2) to two zones after its last (j_end+2) — window edge
    # points live two zones out, and THEIR nearest element can be another
    # zone further. Zone-tag based (element counts vary; zones can be
    # omitted): the largest tag ≤ a−4 opens the range, the smallest tag
    # ≥ j_end+4 closes it.
    pts = xy[lo_pt:hi_pt + 1]
    new_zones = _zone_spans(new_list)
    lo_tags = [t for t in new_zones if t <= a - 4]
    hi_tags = [t for t in new_zones if t >= j_end + 4]
    c0 = new_zones[max(lo_tags)][0] if lo_tags else 0
    c1 = (new_zones[min(hi_tags)][1] if hi_tags else len(new_list) - 1)
    best = np.full(len(pts), np.inf)
    best_i = np.zeros(len(pts), dtype=np.int64)
    for ci in range(c0, c1 + 1):
        d = _dists_to_element_vec(pts, new_list[ci])
        upd = d < best                  # strict '<': full-pass tie semantics
        if upd.any():
            best[upd] = d[upd]
            best_i[upd] = ci

    # Exact completion: when local deviations are large (omitted zones
    # strand OSM points metres away), a window point's true nearest can be
    # a kept element arbitrarily far outside the candidate range. Sweep
    # every element whose bounding box lies within the preliminary worst
    # window distance — a lower-bound prune, so the result matches the
    # full pass exactly (even across hairpins).
    if len(pts) and len(new_list) > (c1 - c0 + 1):
        bound = float(best.max())
        if math.isfinite(bound):
            wx0 = float(pts[:, 0].min()); wx1 = float(pts[:, 0].max())
            wy0 = float(pts[:, 1].min()); wy1 = float(pts[:, 1].max())
            for ci, el in enumerate(new_list):
                if c0 <= ci <= c1:
                    continue
                if el.get("type") == "Arc":
                    cx, cy = el["center"]
                    r = float(el.get("radius", 0.0))
                    bx0, by0, bx1, by1 = cx - r, cy - r, cx + r, cy + r
                else:
                    sx, sy = el["start"]; ex, ey = el["end"]
                    bx0, bx1 = min(sx, ex), max(sx, ex)
                    by0, by1 = min(sy, ey), max(sy, ey)
                gx = max(bx0 - wx1, wx0 - bx1, 0.0)
                gy = max(by0 - wy1, wy0 - by1, 0.0)
                if math.hypot(gx, gy) > bound:
                    continue
                d = _dists_to_element_vec(pts, el)
                upd = d < best
                if upd.any():
                    best[upd] = d[upd]
                    best_i[upd] = ci
    pd[lo_pt:hi_pt + 1] = best
    pe[lo_pt:hi_pt + 1] = best_i

    # Points OUTSIDE the window keep valid distances to their (unchanged)
    # elements, but a rebuilt span element may have moved closer to them —
    # e.g. an omit swings the absorbing curve by metres. One cheap pass:
    # all outside points vs only the built elements (E_span ≪ E).
    if built:
        out_mask = np.ones(len(pd), dtype=bool)
        out_mask[lo_pt:hi_pt + 1] = False
        if out_mask.any():
            pts_out = xy[out_mask]
            d_min = np.full(pts_out.shape[0], np.inf)
            d_arg = np.zeros(pts_out.shape[0], dtype=np.int64)
            for off, el in enumerate(built):
                d = _dists_to_element_vec(pts_out, el)
                upd = d < d_min
                if upd.any():
                    d_min[upd] = d[upd]
                    d_arg[upd] = i0 + off          # absolute new index
            take = d_min < pd[out_mask]            # strictly closer wins
            if take.any():
                rows = np.nonzero(out_mask)[0][take]
                pd[rows] = d_min[take]
                pe[rows] = d_arg[take]

    stats = _stats_from_point_arrays(pd, pe, len(new_list))
    stats["point_dist"] = pd
    stats["point_elem"] = pe

    # ── finalize: stations, ids, annotations, stubs ──────────────────────────
    sta = 0.0
    for e in new_list:
        e["sta_start"] = sta
        sta += e.get("length", 0.0)
    _assign_element_ids(new_list)
    annotate_element_deviations(new_list, xy, stats=stats)

    kept_before = [s for s in snap["stubs"] if s["pi"] < a]
    kept_after = []
    if converged_at is not None:
        for s in snap["stubs"]:
            if s["pi"] >= converged_at - k_shift:
                s2 = dict(s)
                s2["pi"] = s["pi"] + k_shift
                kept_after.append(s2)
    model.tangent_stubs = kept_before + built_stubs + kept_after
    model.elements = new_list
    model.last_stats = stats
    return snap


def _light_copy(model: "PIAlignment") -> "PIAlignment":
    """
    Trial copy for consolidation scans: shares the big read-only arrays
    (xy_ref, chainages_ref), copies everything a trial merge mutates.
    Replaces copy.deepcopy, which cloned the whole OSM polyline per group.
    """
    import dataclasses as _dc
    c = PIAlignment(
        V=model.V.copy(),
        idx=list(model.idx),
        pis=[_dc.replace(p) for p in model.pis],
        xy_ref=model.xy_ref,
        chainages_ref=model.chainages_ref,
        tol=model.tol,
        min_radius=model.min_radius,
        spiral_default=model.spiral_default,
        use_spirals=model.use_spirals,
        elements=[dict(e) for e in model.elements],
        tangent_stubs=[dict(s) for s in model.tangent_stubs],
        log=[],
    )
    st = model.last_stats or {}
    ls = dict(st)
    if st.get("point_dist") is not None:
        ls["point_dist"] = st["point_dist"].copy()
    if st.get("point_elem") is not None:
        ls["point_elem"] = st["point_elem"].copy()
    if st.get("per_element") is not None:
        ls["per_element"] = list(st["per_element"])
    c.last_stats = ls
    return c


def _find_split_curve_pair(elements: list[dict]) -> tuple[int, int] | None:
    """
    Find the first pair of adjacent PIs whose curves are separated by a
    Line shorter than _PI_MIN_TANGENT and turn in the same direction —
    i.e. Douglas-Peucker split one physical curve in two.
    """
    curve_of: list[tuple[int, str]] = []   # (pi_index, rot) per curve group
    gap_after: dict[int, float] = {}       # pi_index → connector Line length
    last_pi = None
    for e in elements:
        if e.get("_pi") is not None and e["type"] == "Arc":
            curve_of.append((e["_pi"], e.get("rot", "")))
            last_pi = e["_pi"]
        elif e["type"] == "Line" and last_pi is not None:
            gap_after[last_pi] = gap_after.get(last_pi, 0.0) + e["length"]
    for (p1, r1), (p2, r2) in zip(curve_of[:-1], curve_of[1:]):
        if p2 == p1 + 1 and r1 == r2 and gap_after.get(p1, 1e9) < _PI_MIN_TANGENT:
            return (p1, p2)
    return None


def _merge_polygon_pair(V, idx, k):
    """Replace PIs k and k+1 with the intersection of the outer tangents."""
    A, P1, P2, B = V[k - 1], V[k], V[k + 1], V[k + 2]
    phi_in  = math.atan2(P1[1] - A[1],  P1[0] - A[0])
    phi_out = math.atan2(B[1] - P2[1],  B[0] - P2[0])
    sin_d = math.sin(_wrap_pi(phi_out - phi_in))
    if abs(sin_d) < 1e-9:
        return None
    dx = float(P2[0] - P1[0]); dy = float(P2[1] - P1[1])
    t  = (dx * math.sin(phi_out) - dy * math.cos(phi_out)) / sin_d
    if t <= 0:
        return None
    PI_new = P1 + t * np.array([math.cos(phi_in), math.sin(phi_in)])
    V2   = np.vstack([V[:k], PI_new[None, :], V[k + 2:]])
    idx2 = idx[:k] + [(idx[k] + idx[k + 1]) // 2] + idx[k + 2:]
    return V2, idx2


def extract_pi_model(
    xy:            np.ndarray,
    chainages:     np.ndarray,
    tolerance:     float,
    min_radius:    float,
    spiral_length: float,
    use_spirals:   bool,
    progress_cb=None,
) -> "PIAlignment":
    """
    Extract the editable PI model from the OSM polyline:
    Douglas-Peucker tangent polygon → cluster merge → noise-PI prefilter →
    construct → iterative split-curve re-merge. Returns a PIAlignment with
    the built element chain and auto values written into each PIData.
    """
    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    n = len(xy)
    tol = max(0.25, float(tolerance))

    if n < 2:
        V = np.zeros((0, 2)); idx = []
    elif n == 2 or float(chainages[-1]) < 1.0:
        V = xy[[0, -1]].astype(float); idx = [0, n - 1]
    else:
        # ── Tangent polygon via Douglas-Peucker + curve-cluster merge ────
        idx = _douglas_peucker_indices(xy, tol)
        V   = xy[idx].astype(float)
        merge_dist = max(60.0, 8.0 * tol)
        V, idx = _merge_pi_clusters(V, idx, merge_dist)

        # Drop near-zero-deflection vertices (noise artefacts of the DP
        # pass). Their tiny heading change is absorbed by the neighbouring
        # curves via the self-healing construction, so C1 continuity is
        # unaffected — but a spurious PI would clip the radius-estimation
        # window of its neighbour and bias the fitted radius.
        MIN_PI_DEFL = math.radians(0.7)
        changed = True
        while changed and len(V) > 2:
            changed = False
            for k in range(1, len(V) - 1):
                if abs(_polygon_deflection(V, k)) < MIN_PI_DEFL:
                    V   = np.delete(V, k, axis=0)
                    idx = idx[:k] + idx[k + 1:]
                    changed = True
                    break
        _p(f"{len(V) - 2} PIs found")

    model = PIAlignment(
        V=V, idx=list(idx), pis=_fresh_pis(V),
        xy_ref=xy, chainages_ref=chainages,
        tol=tol, min_radius=min_radius,
        spiral_default=float(spiral_length), use_spirals=use_spirals,
    )

    if len(V) < 2:
        model.elements = []
        return model

    # ── Construct; merge split curves; reconstruct (bounded loop) ────────
    elements = rebuild_from_pi_model(model, progress_cb)
    for _ in range(6):
        if len(model.V) < 4:
            break
        pair = _find_split_curve_pair(elements)
        if pair is None:
            break
        merged = _merge_polygon_pair(model.V, model.idx, pair[0])
        if merged is None:
            break
        model.V, model.idx = merged
        model.pis = _fresh_pis(model.V)
        _p(f"Merged split curve at PI {pair[0]} — {len(model.V) - 2} PIs")
        elements = rebuild_from_pi_model(model, progress_cb)

    return model


def _build_pi_alignment(
    xy:            np.ndarray,
    chainages:     np.ndarray,
    tolerance:     float,
    min_radius:    float,
    spiral_length: float,
    use_spirals:   bool,
    progress_cb=None,
) -> list[dict]:
    """Backwards-compatible wrapper: extract the PI model, return elements."""
    model = extract_pi_model(
        xy, chainages, tolerance, min_radius, spiral_length,
        use_spirals, progress_cb=progress_cb,
    )
    return model.elements


# ---------------------------------------------------------------------------
# Spiral merge — close a short intermediate straight between two curves
# ---------------------------------------------------------------------------

def _spiral_tangent_length(L: float, R: float, abs_delta: float) -> float:
    """Exact Fresnel-based tangent length T_s = (R+p)·tan(|δ|/2) + k."""
    from geometry.alignment import _compute_clothoid_shift
    if L <= 0.0:
        return R * math.tan(abs_delta / 2.0)
    x_sp, y_sp = _compute_clothoid_shift(L, R)
    theta_s = L / (2.0 * R)
    p_sh = y_sp - R * (1.0 - math.cos(theta_s))
    k_ab = x_sp - R * math.sin(theta_s)
    return (R + p_sh) * math.tan(abs_delta / 2.0) + k_ab


def _pi_zone_info(model: "PIAlignment", pi_index: int):
    """(arc_element, entry_spiral, exit_spiral, |δ_full|) for a spiral zone."""
    arc = entry = exit_ = None
    for e in model.elements:
        if e.get("_pi") != pi_index:
            continue
        if e["type"] == "Arc":
            arc = e
        elif e["type"] == "Spiral":
            if math.isinf(float(e.get("radius_start", float("inf")))):
                entry = e
            else:
                exit_ = e
    if arc is None:
        return None
    R = float(arc["radius"])
    abs_delta = abs(float(arc.get("_deflection", 0.0)))
    if entry is not None:
        abs_delta += float(entry["length"]) / (2.0 * R)
    if exit_ is not None:
        abs_delta += float(exit_["length"]) / (2.0 * R)
    return arc, entry, exit_, abs_delta


def merge_intermediate_line(model: "PIAlignment", pi_a: int, pi_b: int
                            ) -> tuple[bool, str]:
    """
    Remove the short intermediate straight between the curves at PI a and
    PI b by prolonging their transition spirals. The prolongation ΔL is the
    same on both curves, and because the model stores ONE spiral length per
    PI (used for both the entry and exit spiral of that curve), the spirals
    on the far side of each circular curve prolong identically — each curve
    stays symmetrical.

    Solves  gap(ΔL) = D − T_s,a(L_a+ΔL) − T_s,b(L_b+ΔL) = 0  by Newton
    iteration with the exact Fresnel tangent length, where D is the fixed
    distance between the two PIs along their common tangent.

    Returns (ok, message). On failure the model is left unchanged.
    """
    pids = {p.index: p for p in model.pis}
    a, b = pids.get(pi_a), pids.get(pi_b)
    if a is None or b is None:
        return False, "PI not found in the model."
    if a.omitted or b.omitted:
        return False, "One of the PIs is omitted."

    za = _pi_zone_info(model, pi_a)
    zb = _pi_zone_info(model, pi_b)
    if za is None or zb is None:
        return False, "Both PIs must currently carry a curve."
    arc_a, _, exit_a, delta_a = za
    arc_b, entry_b, _, delta_b = zb
    if exit_a is None or entry_b is None:
        return False, "Both curves need transition spirals before merging."

    R_a, R_b = float(arc_a["radius"]), float(arc_b["radius"])
    L_a, L_b = float(exit_a["length"]), float(entry_b["length"])

    # Current connector gap: the Line(s) between exit_a and entry_b
    i_exit  = model.elements.index(exit_a)
    i_entry = model.elements.index(entry_b)
    gap = 0.0
    for e in model.elements[i_exit + 1:i_entry]:
        if e["type"] != "Line":
            return False, "Elements other than a straight sit between the curves."
        gap += float(e["length"])
    if gap < 1e-6:
        return False, "The curves are already joined."

    # Fixed PI-to-PI distance along the common tangent
    D = gap + _spiral_tangent_length(L_a, R_a, delta_a) \
            + _spiral_tangent_length(L_b, R_b, delta_b)

    # Newton on f(Δ) = D − T_a(L_a+Δ) − T_b(L_b+Δ);  dT/dL ≈ 1/2 each → f' ≈ −1
    dL = gap                       # good initial guess (ΔL/2 + ΔL/2 = gap)
    for _ in range(20):
        f = D - _spiral_tangent_length(L_a + dL, R_a, delta_a) \
              - _spiral_tangent_length(L_b + dL, R_b, delta_b)
        if abs(f) < 1e-6:
            break
        h = 0.01
        f2 = D - _spiral_tangent_length(L_a + dL + h, R_a, delta_a) \
               - _spiral_tangent_length(L_b + dL + h, R_b, delta_b)
        deriv = (f2 - f) / h
        if abs(deriv) < 1e-9:
            break
        dL -= f / deriv
        if dL < 0:
            return False, "No prolongation closes the gap (negative solution)."

    L_a_new, L_b_new = L_a + dL, L_b + dL

    # Guards: each curve must keep a positive arc angle
    if delta_a - L_a_new / R_a < 0.02:
        return False, (f"Curve at PI {pi_a} is too short: prolonging its "
                       f"spirals to {L_a_new:.1f} m would consume the arc.")
    if delta_b - L_b_new / R_b < 0.02:
        return False, (f"Curve at PI {pi_b} is too short: prolonging its "
                       f"spirals to {L_b_new:.1f} m would consume the arc.")

    # Apply with rollback: the rebuild's own T_max guards may clamp the
    # lengths (outer tangents too short) — detect and roll back.
    snap = [(p.spiral_len, p.premerge_spiral_len,
             p.merged_with_next, p.merged_with_prev) for p in (a, b)]
    a.premerge_spiral_len = L_a
    b.premerge_spiral_len = L_b
    a.spiral_len = L_a_new
    b.spiral_len = L_b_new
    a.merged_with_next = True
    a.merge_partner = pi_b
    b.merged_with_prev = True

    span_snap = rebuild_pi_span(model, pi_a, pi_b)
    ok = (abs(a.spiral_len - L_a_new) < 0.05
          and abs(b.spiral_len - L_b_new) < 0.05)
    if ok:
        # Verify the connector is actually gone
        za2 = _pi_zone_info(model, pi_a)
        zb2 = _pi_zone_info(model, pi_b)
        if za2 and zb2 and za2[2] is not None and zb2[1] is not None:
            i1 = model.elements.index(za2[2])
            i2 = model.elements.index(zb2[1])
            residual = sum(float(e["length"]) for e in model.elements[i1 + 1:i2])
            ok = residual < 0.05
        else:
            ok = False
    if not ok:
        if span_snap is not None:
            restore_span_snapshot(model, span_snap)   # splice-back, no rebuild
        (a.spiral_len, a.premerge_spiral_len,
         a.merged_with_next, a.merged_with_prev) = snap[0]
        (b.spiral_len, b.premerge_spiral_len,
         b.merged_with_next, b.merged_with_prev) = snap[1]
        a.merge_partner = -1
        if span_snap is None:
            rebuild_from_pi_model(model)
        return False, ("Merging failed: the outer tangents are too short for "
                       "the prolonged spirals (rolled back).")
    return True, f"Merged: spirals prolonged to {L_a_new:.1f} m / {L_b_new:.1f} m."


# ---------------------------------------------------------------------------
# High-angle curve merging — always a single Spiral-Arc-Spiral PI
# ---------------------------------------------------------------------------
# A merge keeps BOTH boundary tangents fixed and replaces everything between
# them with exactly one Line-Spiral-Arc-Spiral-Line — never a chain of
# curves. The new PI's turn is the range's own unwrapped total (the sum of
# its member deflections), which may exceed +-180 deg (a reflex/major arc);
# `PIData.merged_turn` carries that signed value and `_build_zone_range`'s
# reflex branch constructs it directly from a run-wide circle fit (see
# _fit_run_circle below) instead of the ordinary tan(delta/2) tangent-length
# formula, which is only valid up to +-180 deg.

_MERGE_MAX_TOTAL_DEG = 350.0   # reject beyond this — not a real transition
_CHAIN_NEAR_180_DEG  = 5.0     # per-vertex reading this close to 180° is
                               # itself wrap-ambiguous (ask, don't guess)


@dataclass
class MergeCandidate:
    total_deg: float
    max_dev:   float | None      # None if the trial build failed
    ok:        bool
    reason:    str


@dataclass
class MergeChoice:
    """Two plausible readings of an ambiguous range; GUI asks the user."""
    candidates: list             # list[MergeCandidate], length 2
    needs_choice: bool


def _range_total_deflection(model: "PIAlignment", k_from: int, k_to: int
                            ) -> tuple[float, bool, bool]:
    """
    Sum of the tangent-polygon's own per-vertex deflections across
    [k_from, k_to] — the unwrapped truth for a same-rotation run (a single
    _wrap_pi'd reading of the outer tangents alone loses turns >= 180°).

    Returns (total_rad, same_sign, near_180_singleton):
      same_sign          — every component shares the sign of the total
                            (a mixed-sign range is a zigzag, not a single
                            curve — callers fall back to the ordinary path)
      near_180_singleton — some component is itself within
                            _CHAIN_NEAR_180_DEG of +-180°, i.e. individually
                            wrap-ambiguous
    """
    V = model.V
    comps = [_polygon_deflection(V, k) for k in range(k_from, k_to + 1)]
    total = sum(comps)
    if abs(total) < 1e-9:
        return total, True, False
    sgn = 1.0 if total >= 0 else -1.0
    same_sign = all(abs(d) < 1e-6 or (d >= 0) == (sgn >= 0) for d in comps)
    near_180 = any(abs(abs(d) - math.pi) < math.radians(_CHAIN_NEAR_180_DEG)
                   for d in comps)
    return total, same_sign, near_180


def _line_intersect(pa: np.ndarray, phi_a: float,
                    pb: np.ndarray, phi_b: float) -> np.ndarray | None:
    """Intersection of two lines given as (point, heading); None if parallel."""
    sin_d = math.sin(phi_b - phi_a)
    if abs(sin_d) < 1e-9:
        return None
    dx = float(pb[0] - pa[0]); dy = float(pb[1] - pa[1])
    t = (dx * math.sin(phi_b) - dy * math.cos(phi_b)) / sin_d
    return pa + t * np.array([math.cos(phi_a), math.sin(phi_a)])


def _fit_run_circle(model: "PIAlignment", k_from: int, k_to: int
                    ) -> tuple[float, float, float] | None:
    """
    Circle (cx, cy, R) for the whole [k_from, k_to] run: one Kasa fit on the
    OSM window, masking out points close to the two OUTER tangent lines
    (the entry/exit straights — everything between two consecutive small
    same-rotation PIs of one physical curve is already close to the true
    circle, so no interior masking is needed). Verified against synthetic
    120/200/270 degree arcs: exact to the millimetre.

    Do NOT average the run's existing per-PI arc radii instead — those come
    from DP-fragment-sized windows (a curve run is typically many small
    ~5-30 degree PIs after extraction) and are individually noisy; the
    averaged radius was off by 10-25% in testing, enough to add
    metres of deviation over a long sweep. Falls back to that average only
    when the fit itself is degenerate (too few points).
    """
    from geometry.alignment import _fit_circle_kasa
    V, idx, xy = model.V, model.idx, model.xy_ref
    A, P1 = V[k_from - 1], V[k_from]
    P2, B = V[k_to], V[k_to + 1]
    phi_in  = math.atan2(P1[1] - A[1], P1[0] - A[0])
    phi_out = math.atan2(B[1] - P2[1], B[0] - P2[0])

    i_lo, i_hi = idx[k_from - 1], idx[k_to + 1]
    if i_hi - i_lo >= 3:
        seg = xy[i_lo:i_hi + 1]
        rel_in  = seg - A
        rel_out = seg - B
        d_in  = np.abs(rel_in[:, 0]  * math.sin(phi_in)  - rel_in[:, 1]  * math.cos(phi_in))
        d_out = np.abs(rel_out[:, 0] * math.sin(phi_out) - rel_out[:, 1] * math.cos(phi_out))
        lim = max(2.0 * model.tol, 1.0)
        mask = (d_in > lim) & (d_out > lim)
        fit_pts = seg[mask] if int(mask.sum()) >= 5 else seg
        cx, cy, r = _fit_circle_kasa(fit_pts)
        if cx is not None and 10.0 < r < 1e6:
            return cx, cy, r

    radii = [float(e["radius"]) for e in model.elements
             if e.get("type") == "Arc" and k_from <= (e.get("_pi") or -1) <= k_to]
    if radii:
        R = sum(radii) / len(radii)
        sign = 1.0 if _wrap_pi(phi_out - phi_in) >= 0 else -1.0
        perp = phi_in + sign * math.pi / 2.0
        center = P1 + R * np.array([math.cos(perp), math.sin(perp)])
        return float(center[0]), float(center[1]), R
    return None


def _merge_pi_range_singular_aux(model: "PIAlignment", k_from: int,
                                 k_to: int, total_turn: float
                                 ) -> tuple[bool, str]:
    """
    Fallback for a total turn within `_REFLEX_SINGULAR_DEG` of +-180° —
    the single-PI tangent-line intersection genuinely has no finite
    solution there (the two boundary tangents are near-parallel). Instead
    of refusing, insert ONE auxiliary PI so the range still reads as a
    single continuous "curve" experience: two arcs sharing one fitted
    radius, meeting at a zero-length connector on the shared circle.

    This reuses the pre-single-PI chain-merge construction (fit one circle
    to the run's OSM points, take tangent points at evenly-spaced headings
    around it, intersect consecutive tangent lines for the new vertices),
    constrained to exactly 2 sub-curves — always enough, since the total
    is never more than ~2 deg past 180°, so each half is a comfortable
    ~90 deg, nowhere near singular. `PIData.chain_next` (legacy field,
    still fully honoured by `_build_zone_range`'s chain-boundary branch)
    marks the join so the existing zero-connector construction picks it
    up with no further changes needed there.

    Returns (ok, message); on failure the model is unchanged and the
    caller falls back to its own "too close to 180°" refusal message.
    """
    m = len(model.V)
    if not (1 <= k_from <= k_to <= m - 2) or k_from == k_to:
        return False, "PI range out of bounds."

    fit = _fit_run_circle(model, k_from, k_to)
    if fit is None:
        return False, "Could not fit a radius to this range's OSM points."
    cx, cy, R = fit
    center = np.array([cx, cy])

    A, P1 = model.V[k_from - 1], model.V[k_from]
    phi_in = math.atan2(P1[1] - A[1], P1[0] - A[0])
    sign = 1.0 if total_turn >= 0 else -1.0
    n = 2
    sub_delta = total_turn / n

    headings = [phi_in + i * sub_delta for i in range(n + 1)]
    tpts = []
    for phi in headings:
        perp = phi + sign * math.pi / 2.0
        tpts.append(center - R * np.array([math.cos(perp), math.sin(perp)]))
    new_V = []
    for i in range(n):
        v = _line_intersect(tpts[i], headings[i], tpts[i + 1], headings[i + 1])
        if v is None:
            return False, "Auxiliary-PI construction degenerated (near-parallel legs)."
        new_V.append(v)

    old_V, old_idx, old_pis = model.V.copy(), list(model.idx), model.pis
    old_indices = [p.index for p in old_pis]

    removed_old = k_to - k_from + 1
    k_shift = n - removed_old
    model.V = np.vstack([model.V[:k_from]] + [v[None, :] for v in new_V]
                        + [model.V[k_to + 1:]])
    new_idx_vals = [int(v) for v in np.linspace(
        model.idx[k_from - 1], model.idx[k_to + 1], n + 2, dtype=int)[1:-1]]
    model.idx = model.idx[:k_from] + new_idx_vals + model.idx[k_to + 1:]

    new_pis: list = []
    for p in old_pis:
        if p.index < k_from:
            new_pis.append(p)
        elif p.index > k_to:
            p.index += k_shift
            new_pis.append(p)
    for i, v in enumerate(new_V):
        new_pis.insert(k_from - 1 + i, PIData(
            index=k_from + i, xy=(float(v[0]), float(v[1])),
            deflection=sub_delta, radius=float(R), chain_next=(i < n - 1)))
    model.pis = new_pis

    span_snap = rebuild_pi_span(model, k_from, k_from + n - 1,
                                old_lo=k_from, old_hi=k_to, k_shift=k_shift)
    elements = model.elements
    n_arcs = sum(1 for e in elements if e.get("type") == "Arc"
                and k_from <= (e.get("_pi") or -1) <= k_from + n - 1)
    if n_arcs < n:
        model.V, model.idx, model.pis = old_V, old_idx, old_pis
        for p, i in zip(model.pis, old_indices):
            p.index = i
        if span_snap is not None:
            restore_span_snapshot(model, span_snap)
        else:
            rebuild_from_pi_model(model)
        return False, (f"The auxiliary-PI curve could not be constructed "
                       f"({n_arcs}/{n} arcs fit).")
    return True, (f"PIs {k_from}–{k_to} replaced by two curves sharing one "
                  f"radius via an auxiliary PI (δ={math.degrees(total_turn):+.1f}°, "
                  f"R≈{R:.0f} m) — the total turn is too close to 180° for "
                  "a single PI.")


def merge_range_ambiguity(model: "PIAlignment", k_from: int, k_to: int
                          ) -> MergeChoice | None:
    """
    Read-only pre-check for the GUI: None if the range is unambiguous (the
    overwhelmingly common case — just call merge_pi_range directly); a
    MergeChoice with two trial-evaluated candidates when a per-vertex
    reading is itself wrap-ambiguous (near +-180°) and the two
    interpretations are not clearly distinguished by fit quality.
    """
    m = len(model.V)
    if not (1 <= k_from <= k_to <= m - 2) or k_from == k_to:
        return None
    total, same_sign, near_180 = _range_total_deflection(model, k_from, k_to)
    if not same_sign or not near_180:
        return None

    alt = total - (2.0 * math.pi if total >= 0 else -2.0 * math.pi)

    def _trial(delta):
        t = _light_copy(model)
        ok, msg = merge_pi_range(t, k_from, k_to, prefer=delta)
        dev = _group_max_dev(t, k_from) if ok else None
        return MergeCandidate(math.degrees(delta), dev, ok, msg)

    c1, c2 = _trial(total), _trial(alt)
    if c1.ok and c2.ok and c1.max_dev is not None and c2.max_dev is not None:
        lo, hi = sorted((c1.max_dev, c2.max_dev))
        if hi > 3.0 * max(lo, 1e-6) and hi > model.tol:
            return None       # one candidate is decisively worse — no ask
    elif c1.ok != c2.ok:
        return None            # only one candidate is even constructible
    return MergeChoice(candidates=[c1, c2], needs_choice=True)


def merge_pi_range(model: "PIAlignment", k_from: int, k_to: int,
                   prefer: float | None = None) -> tuple[bool, str]:
    """
    Replace the PIs in [k_from, k_to] with ONE PI — always a single
    Spiral-Arc-Spiral, both boundary tangents kept fixed — via
    `_merge_pi_range_single`. The new PI's turn is the range's own
    unwrapped total deflection (the sum of its member deflections), which
    may exceed +-180 deg; that's a reflex/major arc, not a chain of curves
    (see `PIData.merged_turn` / `_build_zone_range`'s reflex branch).

    `prefer` overrides the total deflection (radians) used for the
    construction; only meaningful for genuinely ambiguous ranges (see
    `merge_range_ambiguity`) — leave it None for the ordinary case.
    """
    m = len(model.V)
    if not (1 <= k_from <= k_to <= m - 2):
        return False, "PI range out of bounds."
    if k_from == k_to:
        return False, "Select a range spanning at least two PIs."

    if prefer is not None:
        total = prefer
    else:
        total, same_sign, _ = _range_total_deflection(model, k_from, k_to)
        if not same_sign:
            total = None   # zigzag — let the construction fall back to its
                           # own outer-tangent-intersection (wrapped) reading

    if total is not None and abs(math.degrees(total)) > _MERGE_MAX_TOTAL_DEG:
        return False, (f"Total deflection {math.degrees(total):.0f}° is not "
                       "a plausible single transition.")
    return _merge_pi_range_single(model, k_from, k_to, total_turn=total)


def _merge_pi_range_single(model: "PIAlignment", k_from: int, k_to: int,
                           total_turn: float | None = None
                           ) -> tuple[bool, str]:
    """
    Replace ALL PIs in the vertex range [k_from … k_to] with a single PI —
    the intersection of the outer tangent LINES (both kept fixed). The
    tangent before the first PI and the tangent after the last PI are kept;
    everything between becomes one Spiral–Arc–Spiral (Level 3) or one Arc
    (Level 2), for any total turn — including a reflex/major arc >180 deg.

    `total_turn` is the signed, UNWRAPPED total (radians) the curve must
    sweep; None falls back to the wrapped outer-tangent reading (a mixed-
    sign/zigzag range, where "total across members" isn't meaningful).
    Note the outer-tangent LINE intersection used for the new PI's position
    is the same point regardless of interpretation — only the arc's radius/
    sweep differ — so this is computed once, unaffected by total_turn.

    Edits on PIs outside the range are preserved (indices remapped).
    Returns (ok, message); on failure the model is unchanged.
    """
    m = len(model.V)
    if not (1 <= k_from <= k_to <= m - 2):
        return False, "PI range out of bounds."
    if k_from == k_to:
        return False, "Select a range spanning at least two PIs."

    A  = model.V[k_from - 1]   # start of incoming tangent
    P1 = model.V[k_from]       # first PI
    P2 = model.V[k_to]         # last PI
    B  = model.V[k_to + 1]     # end of outgoing tangent

    phi_in  = math.atan2(P1[1] - A[1],  P1[0] - A[0])
    phi_out = math.atan2(B[1] - P2[1],  B[0] - P2[0])
    delta   = _wrap_pi(phi_out - phi_in)
    sin_d   = math.sin(delta)
    if abs(sin_d) < 1e-9:
        return False, ("The outer tangents are parallel — no single curve "
                       "can connect them.")

    # Intersection: P1 + t·û_in = P2 − s·û_out  (t ahead of P1, s behind P2).
    # This is the unique intersection of the two tangent LINES — it does not
    # depend on which arc (minor or reflex) will be swept through it, so the
    # feasibility check below (which side of P1/P2 a valid PI must fall on)
    # is regime-dependent — see the reflex branch below.
    dx = float(P2[0] - P1[0]); dy = float(P2[1] - P1[1])
    t  = (dx * math.sin(phi_out) - dy * math.cos(phi_out)) / sin_d
    s  = (dy * math.cos(phi_in)  - dx * math.sin(phi_in))  / sin_d

    turn = delta if total_turn is None else total_turn
    if abs(abs(turn) - math.pi) < math.radians(_REFLEX_SINGULAR_DEG):
        # No single PI has a finite solution this close to 180 deg — fall
        # back to one auxiliary PI (two arcs, one shared radius, C1 join)
        # instead of refusing outright.
        ok, msg = _merge_pi_range_singular_aux(model, k_from, k_to, turn)
        if ok:
            return True, msg
        return False, (f"Total turn {math.degrees(turn):+.1f}° is too close "
                       "to 180° — the boundary tangents are nearly parallel "
                       "and never meet at a finite curve.")
    if abs(math.degrees(turn)) > _MERGE_MAX_TOTAL_DEG:
        return False, (f"Total turn {math.degrees(turn):.0f}° is not a "
                       "plausible single transition.")

    # For a minor arc (|turn| <= 180 deg) the crossing point must be ahead
    # of P1 and before P2 — the ordinary, intuitive configuration. Past
    # 180 deg the SAME two lines cross on the far side instead (t and s
    # both flip negative together — verified against tan(delta/2)'s own
    # sign flip through the singularity): that is the CORRECT reflex
    # configuration, not a failure.
    if abs(turn) <= math.pi:
        if t <= 0 or s <= 0:
            return False, ("The tangents do not intersect ahead of the "
                           "selected range — these PIs cannot be replaced "
                           "by one curve (check the total deflection).")
    else:
        if t >= 0 or s >= 0:
            return False, ("The tangents intersect on the wrong side for a "
                           "reflex/major-arc curve here — this range's "
                           "total turn is not achievable as a single curve "
                           "with the available boundary tangents.")
    PI_new = P1 + t * np.array([math.cos(phi_in), math.sin(phi_in)])

    # Snapshot for rollback (indices get remapped in place below)
    old_V, old_idx = model.V.copy(), list(model.idx)
    old_pis = model.pis
    old_indices = [p.index for p in old_pis]

    removed = k_to - k_from          # vertices removed (range collapses to 1)
    model.V   = np.vstack([model.V[:k_from], PI_new[None, :],
                           model.V[k_to + 1:]])
    model.idx = (model.idx[:k_from]
                 + [(model.idx[k_from] + model.idx[k_to]) // 2]
                 + model.idx[k_to + 1:])

    # Rebuild the PIData list: keep edits outside the range, fresh auto PI
    # inside, and remap indices after the range.
    new_pis: list = []
    for p in old_pis:
        if p.index < k_from:
            new_pis.append(p)
        elif p.index > k_to:
            p.index -= removed
            new_pis.append(p)
        # PIs inside the range are dropped
    merged_pi = PIData(index=k_from,
                       xy=(float(PI_new[0]), float(PI_new[1])),
                       deflection=delta,
                       merged_turn=(total_turn if total_turn is not None
                                    else delta))
    new_pis.insert(k_from - 1, merged_pi)
    model.pis = new_pis

    span_snap = rebuild_pi_span(model, k_from, k_from,
                                old_lo=k_from, old_hi=k_to,
                                k_shift=-removed)
    elements = model.elements
    # Sanity: the merged curve must actually have been constructed
    if not any(e.get("_pi") == k_from and e["type"] == "Arc" for e in elements):
        model.V, model.idx, model.pis = old_V, old_idx, old_pis
        for p, i in zip(model.pis, old_indices):
            p.index = i
        if span_snap is not None:
            restore_span_snapshot(model, span_snap)   # splice-back, no rebuild
        else:
            rebuild_from_pi_model(model)
        return False, ("The merged curve could not be constructed (no room "
                       "for a tangent-fitting radius) — rolled back.")
    return True, (f"PIs {k_from}–{k_to} replaced by a single curve "
                  f"(δ={math.degrees(turn):+.1f}°).")


def undo_merge(model: "PIAlignment", pi_a: int) -> tuple[bool, str]:
    """Restore the pre-merge spiral lengths of the pair merged at pi_a."""
    pids = {p.index: p for p in model.pis}
    a = pids.get(pi_a)
    if a is None or not a.merged_with_next:
        return False, "This PI has no merge to undo."
    b = pids.get(a.merge_partner)
    k_hi = a.merge_partner if (b is not None
                               and a.merge_partner > pi_a) else pi_a
    a.spiral_len = a.premerge_spiral_len if a.premerge_spiral_len >= 0 else -1.0
    a.merged_with_next = False
    a.merge_partner = -1
    a.premerge_spiral_len = -1.0
    if b is not None:
        b.spiral_len = b.premerge_spiral_len if b.premerge_spiral_len >= 0 else -1.0
        b.premerge_spiral_len = -1.0
        b.merged_with_prev = False
    rebuild_pi_span(model, pi_a, k_hi)
    return True, "Merge undone."


# ---------------------------------------------------------------------------
# Split / delete / trim — direct edits to the tangent polygon
# ---------------------------------------------------------------------------
# insert_pi / delete_pi follow the merge_pi_range template (vertex array
# edit + PI reindex + span rebuild); trim_alignment is rare enough that a
# full rebuild is fine.

def nearest_xy_ref_index(model: "PIAlignment", point_xy) -> int:
    """Index into model.xy_ref of the OSM point closest to `point_xy`."""
    xy = model.xy_ref
    d2 = (xy[:, 0] - point_xy[0]) ** 2 + (xy[:, 1] - point_xy[1]) ** 2
    return int(np.argmin(d2))


def insert_pi(model: "PIAlignment", j_ref: int) -> tuple[bool, str]:
    """
    Insert a new PI at OSM point `xy_ref[j_ref]`, splitting whichever
    tangent-polygon leg currently spans it. The new PI gets an auto radius
    and spiral length — this is how a curve Douglas-Peucker smoothed away
    inside a Line can be picked back up.

    Returns (ok, message); on failure the model is unchanged.
    """
    m = len(model.V)
    idx = model.idx
    if not (0 <= j_ref < len(model.xy_ref)):
        return False, "Point index out of bounds."
    if j_ref <= idx[0] or j_ref >= idx[-1]:
        return False, "Pick a point strictly between the alignment's endpoints."

    # Locate the leg V[k] .. V[k+1] whose OSM range contains j_ref.
    k = bisect.bisect_right(idx, j_ref) - 1
    k = max(0, min(k, m - 2))
    if idx[k] == j_ref or idx[k + 1] == j_ref:
        return False, "That point already is a PI."

    new_xy = model.xy_ref[j_ref]
    old_pis = model.pis

    model.V = np.vstack([model.V[:k + 1], new_xy[None, :], model.V[k + 1:]])
    model.idx = model.idx[:k + 1] + [j_ref] + model.idx[k + 1:]

    new_pis: list = []
    for p in old_pis:
        if p.index <= k:
            new_pis.append(p)
        else:
            p.index += 1
            new_pis.append(p)
    new_pi = PIData(index=k + 1, xy=(float(new_xy[0]), float(new_xy[1])),
                    deflection=_polygon_deflection(model.V, k + 1))
    new_pis.insert(k, new_pi)
    model.pis = new_pis

    rebuild_pi_span(model, k + 1, k + 1, k_shift=1)
    return True, f"PI {k + 1} inserted."


def move_pi(model: "PIAlignment", k: int, xy_proj) -> tuple[bool, str]:
    """
    Drag PI `k` to a new position (projected coords) — the "edit mode" map
    interaction (Part C). Unlike merge/insert/delete this never changes the
    vertex count: only `V[k]` and its OSM anchor `idx[k]` move.

    `idx[k]` is re-snapped to the nearest OSM point, then clamped to stay
    strictly between `idx[k-1]` and `idx[k+1]` — dragging past a neighbour's
    anchor would invert the monotonic OSM-index ordering the deviation-
    window code (`_build_zone_range`'s masked Kasa fit, span rebuild) relies
    on. A PI dragged onto (near-)collinear with its neighbours simply loses
    its curve, like any other tiny-deflection PI — not a failure.

    Returns (ok, message); on failure the model is unchanged.
    """
    m = len(model.V)
    if not (1 <= k <= m - 2):
        return False, "PI index out of bounds."

    model.V[k] = np.asarray(xy_proj, dtype=float)

    j = nearest_xy_ref_index(model, xy_proj)
    lo, hi = model.idx[k - 1], model.idx[k + 1]
    j = max(lo + 1, min(hi - 1, j)) if hi - lo > 1 else lo
    model.idx[k] = j

    rebuild_pi_span(model, k, k)
    return True, f"PI {k} moved."


def delete_pi(model: "PIAlignment", k: int) -> tuple[bool, str]:
    """
    Physically remove PI `k` — unlike `omit` (which keeps the vertex and
    just skips its curve), the tangent polygon loses the vertex entirely
    and its neighbours connect directly.

    Returns (ok, message); on failure the model is unchanged.
    """
    m = len(model.V)
    if not (1 <= k <= m - 2):
        return False, "PI index out of bounds."
    if m <= 3:
        return False, "Cannot delete the only remaining PI."

    old_pis = model.pis

    model.V = np.vstack([model.V[:k], model.V[k + 1:]])
    model.idx = model.idx[:k] + model.idx[k + 1:]

    new_pis: list = []
    for p in old_pis:
        if p.index == k:
            continue
        if p.index > k:
            p.index -= 1
        new_pis.append(p)
    model.pis = new_pis

    rebuild_pi_span(model, k, k - 1, k_shift=-1)
    return True, f"PI {k} deleted."


def trim_alignment(model: "PIAlignment", j_start: int, j_end: int
                   ) -> tuple[bool, str]:
    """
    Discard the alignment outside OSM points [j_start, j_end]: re-anchors
    xy_ref/chainages_ref to the trimmed span and drops PIs outside it. Full
    rebuild (trimming is a rare, deliberate action — no span path needed).

    Returns (ok, message); on failure the model is unchanged.
    """
    from geometry.curvature import compute_chainages
    import dataclasses as _dc

    n = len(model.xy_ref)
    if not (0 <= j_start < j_end < n):
        return False, "Invalid trim range."

    keep = [k for k in range(len(model.V))
            if j_start <= model.idx[k] <= j_end]
    if len(keep) < 2:
        return False, "Trim range too small — no vertices remain."

    old_V, old_idx, old_pis = model.V, model.idx, model.pis
    old_xy, old_chain = model.xy_ref, model.chainages_ref

    new_xy_ref = old_xy[j_start:j_end + 1].copy()
    new_V = old_V[keep].copy()
    new_idx = [old_idx[k] - j_start for k in keep]
    new_V[0], new_V[-1] = new_xy_ref[0], new_xy_ref[-1]
    new_idx[0], new_idx[-1] = 0, len(new_xy_ref) - 1

    by_old_index = {p.index: p for p in old_pis}
    new_pis: list = []
    for new_k, old_k in enumerate(keep):
        if new_k == 0 or new_k == len(new_V) - 1:
            continue     # became an endpoint — no PIData
        p = by_old_index.get(old_k)
        if p is not None:
            new_pis.append(_dc.replace(p, index=new_k))

    model.xy_ref = new_xy_ref
    model.chainages_ref = compute_chainages(new_xy_ref)
    model.V = new_V
    model.idx = new_idx
    model.pis = new_pis

    els = rebuild_from_pi_model(model)
    if not els:
        model.xy_ref, model.chainages_ref = old_xy, old_chain
        model.V, model.idx, model.pis = old_V, old_idx, old_pis
        rebuild_from_pi_model(model)
        return False, "Trim produced an empty alignment — rolled back."
    return True, (f"Trimmed to {len(new_xy_ref)} OSM points "
                  f"(was {n}); {len(new_pis)} PIs remain.")


# ---------------------------------------------------------------------------
# Curve consolidation — merge runs of same-direction curves (Step "Consolidate")
# ---------------------------------------------------------------------------

def _curve_runs(model: "PIAlignment", max_straight_m: float) -> list[list[int]]:
    """
    Maximal runs of ≥2 consecutive curves that turn the SAME way and are
    connected either directly (spiral→spiral) or by straights whose total
    length is below `max_straight_m`.

    Returns a list of PI-index lists, e.g. [[3, 4, 5], [9, 10]].
    Generalises `_find_split_curve_pair` (adjacent pair, hardcoded 60 m).
    """
    # Walk the element chain: record each curve (its PI + rotation) and the
    # straight length accumulated between consecutive curves.
    seq: list[tuple[int, str]] = []          # (pi_index, rot) in order
    gap_after: dict[int, float] = {}         # pi_index → straight length after it
    last_pi: int | None = None
    for e in model.elements:
        pi = e.get("_pi")
        if e.get("type") == "Arc" and pi is not None:
            if not seq or seq[-1][0] != pi:
                seq.append((pi, e.get("rot", "")))
            last_pi = pi
        elif e.get("type") == "Line" and last_pi is not None:
            gap_after[last_pi] = gap_after.get(last_pi, 0.0) + float(e.get("length", 0.0))

    runs: list[list[int]] = []
    cur: list[int] = []
    for i, (pi, rot) in enumerate(seq):
        if not cur:
            cur = [pi]
            cur_rot = rot
            continue
        prev_pi = cur[-1]
        same_dir = (rot == cur_rot)
        close    = gap_after.get(prev_pi, 0.0) <= max_straight_m
        adjacent = (pi == prev_pi + 1)
        if same_dir and close and adjacent:
            cur.append(pi)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [pi]
            cur_rot = rot
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _merged_group_range(model: "PIAlignment", k_from: int) -> tuple[int, int]:
    """
    PI-index span [k_from, k_hi] of the curve built at `k_from` — a single
    PI, or (after a chained high-angle merge) the whole chain, found by
    walking chain_next links.
    """
    by_index = {p.index: p for p in model.pis}
    k_hi = k_from
    while by_index.get(k_hi) is not None and by_index[k_hi].chain_next:
        k_hi += 1
    return k_from, k_hi


def _range_max_dev(model: "PIAlignment", k_lo: int, k_hi: int) -> float:
    """Largest OSM deviation of the curve(s) tagged _pi in [k_lo, k_hi]."""
    devs = [float(e.get("_max_dev", 0.0)) for e in model.elements
            if e.get("_pi") is not None and k_lo <= e["_pi"] <= k_hi]
    return max(devs) if devs else float("inf")


def _group_max_dev(model: "PIAlignment", pi_index: int) -> float:
    """Largest OSM deviation of the curve built at `pi_index` (after a rebuild)."""
    return _range_max_dev(model, pi_index, pi_index)


def find_consolidation_groups(
    model:          "PIAlignment",
    max_straight_m: float = 30.0,
    max_dev_m:      float = 2.0,
    progress_cb=None,
) -> list[dict]:
    """
    Propose runs of same-direction curves that can be replaced by ONE curve.

    Each run is trial-merged on a deep copy via `merge_pi_range` (which keeps
    the outer tangents, puts the new PI at their intersection and reindexes);
    the trial is accepted only if the resulting single curve stays within
    `max_dev_m` of the OSM polyline.

    Returns one dict per run:
      {k_from, k_to, n_curves, radii_before, radius_after, max_dev, ok, reason}
    Rejected runs are returned too (ok=False + reason) so the UI can show why.

    `progress_cb(n_done, total)` (numeric, matching the convention used by
    every other progress callback in this codebase — e.g.
    `geometry.cross_section.compute_cross_section`) is called once per run
    evaluated; the caller formats its own status text / ETA from the two
    numbers.
    """
    runs = _curve_runs(model, max_straight_m)
    out: list[dict] = []
    for n_done, run in enumerate(runs, start=1):
        if progress_cb:
            progress_cb(n_done, len(runs))
        k_from, k_to = run[0], run[-1]
        radii_before = [
            round(float(e["radius"]), 1)
            for e in model.elements
            if e.get("type") == "Arc" and e.get("_pi") in run
        ]
        rec = {
            "k_from": k_from, "k_to": k_to, "n_curves": len(run),
            "radii_before": radii_before, "radius_after": None,
            "max_dev": None, "ok": False, "reason": "",
        }
        trial = _light_copy(model)      # shares xy_ref; O(span) trial merge
        ok, msg = merge_pi_range(trial, k_from, k_to)
        if not ok:
            rec["reason"] = msg
            out.append(rec)
            continue
        arcs = [e for e in trial.elements
                if e.get("type") == "Arc" and e.get("_pi") == k_from]
        if not arcs:
            rec["reason"] = "Merged curve could not be constructed."
            out.append(rec)
            continue
        rec["radius_after"] = round(float(arcs[0]["radius"]), 1)
        # Every merge — including high-angle/reflex ones — now builds a
        # single PI; _merged_group_range degenerates to (k_from, k_from)
        # unless a legacy .coypu 1.1 file's chain_next links are present.
        _, k_hi_built = _merged_group_range(trial, k_from)
        dev = _range_max_dev(trial, k_from, k_hi_built)
        rec["max_dev"] = round(dev, 3)
        if dev <= max_dev_m:
            rec["ok"] = True
            rec["reason"] = "within tolerance"
        else:
            rec["reason"] = (f"merged curve deviates {dev:.2f} m "
                             f"(limit {max_dev_m:.2f} m)")
        out.append(rec)
    return out


def apply_consolidation(model: "PIAlignment", groups: list[dict],
                        progress_cb=None) -> tuple[int, list[str]]:
    """
    Apply the accepted `groups` to `model`.

    Applied **back-to-front**: `merge_pi_range` reindexes every PI after the
    merged range, so working from the last group backwards keeps the earlier
    groups' indices valid.

    `progress_cb(n_done, total)` (numeric — see `find_consolidation_groups`)
    is called once per group applied.

    Returns (n_applied, messages).
    """
    msgs: list[str] = []
    todo = sorted([g for g in groups if g.get("ok")],
                  key=lambda g: g["k_from"], reverse=True)
    applied = 0
    for n_done, g in enumerate(todo, start=1):
        ok, msg = merge_pi_range(model, g["k_from"], g["k_to"])
        msgs.append(f"PIs {g['k_from']}–{g['k_to']}: {msg}")
        if ok:
            applied += 1
        if progress_cb:
            progress_cb(n_done, len(todo))
    return applied, msgs


# ---------------------------------------------------------------------------
# Spiral insertion helper (used by _run_segment_fit_spirals)
# ---------------------------------------------------------------------------

def _insert_spirals_into_elements(
    elements:      list[dict],
    spiral_length: float,
    min_radius:    float,
) -> list[dict]:
    """
    Insert entry and exit clothoid spirals around every Arc element that sits
    between two Line elements — **textbook tangent-fixed convention**.

    The original tangent polygon (PI position and tangent directions) stays
    fixed; the circular arc keeps its original radius `R` but its centre
    shifts perpendicular to the bisector by ``p = L²/(24R)``. The spiral
    sits half on the (now shortened) tangent and half "into" the original
    arc location.

    For each Line→Arc→Line triplet:

    1. PI — Point of Intersection of the two adjacent Lines (tangent rays).
    2. ``L_eff = min(spiral_length, 0.85·2·prev_len, 0.85·2·next_len)`` so the
       spirals fit inside the available tangent lengths. At inflection points
       the requested spiral_length is automatically shortened.
       Skip if ``L_eff < 2 m``.
    3. ``R = el["radius"]`` — original arc radius (NOT inflated).
    4. ``p = L_eff² / (24·R)`` — perpendicular offset of the arc.
    5. ``k = L_eff/2 − L_eff³/(240·R²)`` — spiral tangent projection.
    6. ``T_s = (R + p) · tan(|δ|/2) + k`` — tangent distance from PI.
    7. ``TC = PI − T_s · (cos φ_in,  sin φ_in)``  (on incoming tangent)
       ``CT_target = PI + T_s · (cos φ_out, sin φ_out)``  (on outgoing tangent)
    8. ``_compute_zone_geometry(TC, φ_in, δ, R, L_eff, L_eff)`` returns
       Fresnel-accurate L–S–A–S–L coordinates that close on CT_target by
       construction (within ~mm numerical error).

    Returns a new list; input is not modified.

    Spiral element dict fields (consumed by ``_add_spiral`` in builder.py):
        type, sta_start, length, start, end,
        radius_start (inf for entry), radius_end (R for entry),
        clothoid_A = sqrt(R · L_eff), rot
    Exit spiral: radius_start = R, radius_end = inf.
    """
    from geometry.alignment import _compute_zone_geometry

    _L_MIN = 2.0   # spirals shorter than this are not worth inserting

    if spiral_length <= 0 or not elements:
        return list(elements)

    work = [dict(e) for e in elements]
    out: list[dict] = []
    skip_next = False

    for i in range(len(work)):
        if skip_next:
            skip_next = False
            continue

        el = work[i]

        # Only handle Arc flanked by Line on both sides
        if (el.get("type") != "Arc"
                or i == 0 or i == len(work) - 1
                or work[i - 1].get("type") != "Line"
                or work[i + 1].get("type") != "Line"):
            out.append(el)
            continue

        prev_el = work[i - 1]
        next_el = work[i + 1]
        R       = el.get("radius", 0.0)        # KEEP original; do NOT inflate
        delta   = el.get("_deflection", 0.0)   # signed total arc deflection (rad)

        if R <= 0 or math.isinf(R) or abs(delta) < 1e-6:
            out.append(el)
            continue

        phi_in  = prev_el.get("direction_rad", 0.0)
        phi_out = next_el.get("direction_rad", 0.0)

        # ── Find PI (Point of Intersection of tangent lines) ─────────────
        # Solve: TS_orig + t*(cos φ_in, sin φ_in) = ST_orig + s*(cos φ_out, sin φ_out)
        TS_orig = np.array(el["start"], dtype=float)
        ST_orig = np.array(el["end"],   dtype=float)
        dx = float(ST_orig[0] - TS_orig[0])
        dy = float(ST_orig[1] - TS_orig[1])
        sin_d = math.sin(delta)
        if abs(sin_d) < 1e-9:
            out.append(el)
            continue
        # Cramer's rule: t_pi = (dx·sin φ_out − dy·cos φ_out) / sin(δ)
        t_pi = (dx * math.sin(phi_out) - dy * math.cos(phi_out)) / sin_d
        PI = TS_orig + t_pi * np.array([math.cos(phi_in), math.sin(phi_in)])

        # Effective spiral length, capped so TC and CT remain on adjacent Lines
        avail_prev = prev_el.get("length", 0.0)
        avail_next = next_el.get("length", 0.0)
        L_eff = min(spiral_length, 0.85 * 2.0 * avail_prev, 0.85 * 2.0 * avail_next)

        if L_eff < _L_MIN:
            out.append(el)
            continue

        # ── Textbook tangent-fixed geometry ───────────────────────────────
        # R kept; arc centre will shift perpendicular to the bisector by p.
        p   = L_eff * L_eff / (24.0 * R)
        k   = L_eff / 2.0 - L_eff ** 3 / (240.0 * R ** 2)
        T_s = (R + p) * math.tan(abs(delta) / 2.0) + k

        # Remaining arc angle after the two spirals consume L/R rad each
        arc_angle_rem = abs(delta) - 2.0 * (L_eff / (2.0 * R))   # = |δ| − L/R
        if arc_angle_rem < 0.02:
            out.append(el)
            continue

        TC = PI - T_s * np.array([math.cos(phi_in),  math.sin(phi_in)])

        # Guard: TC must stay between prev_start and TS_orig (on Line_before).
        # Distance from prev_start to PI = avail_prev + t_pi; TC at distance
        # (avail_prev + t_pi − T_s) from prev_start. Must be in (0, avail_prev).
        prev_start  = np.array(prev_el["start"], dtype=float)
        dist_tc_from_prev_start = float(np.linalg.norm(TC - prev_start))
        if dist_tc_from_prev_start < 1e-3 or dist_tc_from_prev_start >= avail_prev + t_pi - 0.01:
            out.append(el)
            continue

        # ── Full L–S–A–S–L geometry (Fresnel-accurate) ───────────────────
        # Pass R (not R+p) so the arc retains its original radius.
        zone = _compute_zone_geometry(TC, phi_in, delta, R,
                                      L_entry=L_eff, L_exit=L_eff)
        if zone is None:
            out.append(el)
            continue

        CT        = zone["CT"]
        zas       = zone["arc_start"]
        zac       = zone["arc_center"]
        zae       = zone["arc_end"]
        z_arc_len = zone["arc_len"]
        rot       = zone["rot"]
        clothoid_A = math.sqrt(R * L_eff)
        next_end_pt = np.array(next_el["end"], dtype=float)

        # Guard: CT must not overshoot the end of Line_after
        if float(np.linalg.norm(next_end_pt - CT)) < 1e-3:
            out.append(el)
            continue

        # ── 1. Truncate Line_before to TC ─────────────────────────────────
        tc_list  = TC.tolist()
        new_prev = dict(prev_el)
        new_prev["end"]    = tc_list
        new_prev["length"] = float(np.linalg.norm(TC - prev_start))
        # Replace the last element in `out` (which is prev_el, from prev iteration)
        if out and out[-1] is prev_el:
            out[-1] = new_prev
        else:
            out.append(new_prev)

        # ── 2. Entry spiral ──────────────────────────────────────────────
        sta_tc = new_prev.get("sta_start", 0.0) + new_prev["length"]
        entry_sp: dict = {
            "type":         "Spiral",
            "sta_start":    sta_tc,
            "length":       L_eff,
            "start":        tc_list,
            "end":          zas.tolist(),
            "radius_start": float("inf"),
            "radius_end":   R,
            "clothoid_A":   clothoid_A,
            "rot":          rot,
        }
        out.append(entry_sp)

        # ── 3. Circular arc (radius preserved at R) ───────────────────────
        chord   = float(np.linalg.norm(zae - zas))
        new_arc: dict = {
            "type":        "Arc",
            "sta_start":   sta_tc + L_eff,
            "length":      z_arc_len,
            "start":       zas.tolist(),
            "end":         zae.tolist(),
            "center":      zac.tolist(),
            "radius":      R,
            "rot":         rot,
            "chord":       chord,
            "_deflection": zone["arc_angle"] * (1 if delta >= 0 else -1),
        }
        out.append(new_arc)

        # ── 4. Exit spiral ────────────────────────────────────────────────
        ct_list = CT.tolist()
        exit_sp: dict = {
            "type":         "Spiral",
            "sta_start":    sta_tc + L_eff + z_arc_len,
            "length":       L_eff,
            "start":        zae.tolist(),
            "end":          ct_list,
            "radius_start": R,
            "radius_end":   float("inf"),
            "clothoid_A":   clothoid_A,
            "rot":          rot,
        }
        out.append(exit_sp)

        # ── 5. Truncated Line_after from CT, locked to phi_out ───────────
        # The Line's direction MUST stay phi_out (tangent-fixed convention).
        # CT may drift slightly off the outgoing tangent ray due to the
        # exit-spiral Fresnel construction in `_compute_zone_geometry`; we
        # therefore keep direction = phi_out and project the original next-end
        # onto the (CT, phi_out) ray to derive the length, ensuring exact C1.
        dir_vec   = np.array([math.cos(phi_out), math.sin(phi_out)])
        proj_len  = float(np.dot(next_end_pt - CT, dir_vec))
        new_next  = dict(next_el)
        new_next["start"]         = ct_list
        new_next["direction_rad"] = phi_out
        new_next["length"]        = max(0.0, proj_len)
        new_next["end"]           = (CT + max(0.0, proj_len) * dir_vec).tolist()
        out.append(new_next)
        skip_next = True   # work[i+1] already replaced above

    # ── Recompute sta_start chain ─────────────────────────────────────────
    sta = 0.0
    for e in out:
        e["sta_start"] = sta
        sta += e.get("length", 0.0)

    return out
