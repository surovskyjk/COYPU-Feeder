"""
Vertical alignment profile: terrain sampling, smoothing and PVI synthesis.

The horizontal side of the Feeder has the PI model as its single editable
source of truth; this module is its vertical counterpart.  ``VerticalProfile``
holds the DEM samples plus the user's smoothing configuration, and
``rebuild_profile`` regenerates everything derived from them — exactly the
``rebuild_from_pi_model`` contract.  Derived arrays (``z_override``,
``z_design``, ``pvis``, ``stats``) are never persisted; they are always
regenerated from ``s`` / ``z_raw`` / the config.

Two smoothing methods are offered, but both feed the *same* PVI back end: a
moving-window filter produces a dense series, and ``<ProfAlign>`` cannot take
thousands of PVIs, so the simplification + grade clamp always runs.  The
methods differ only in whether a pre-filter runs first.

No Qt, no network — everything here is pure numpy/scipy so it is testable
without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Defaults shared by the step widget, the project loader and the tests.
DEFAULT_SAMPLE_INTERVAL = 20.0      # m
DEFAULT_MAX_GRADE_PM    = 15.0      # per mille
MAX_GRADE_PM_LIMIT      = 40.0      # per mille — the spinbox ceiling
DEFAULT_Z_TOL           = 0.50      # m
DEFAULT_WINDOW_M        = 150.0     # m
WINDOW_M_RANGE          = (50.0, 300.0)
DEFAULT_POLY_ORDER      = 2
DEFAULT_VC_LENGTH       = 100.0     # m
DEFAULT_ELEVATION_SOURCE = "cuzk_dmr5g"

METHOD_GRADIENT = "gradient"
METHOD_WINDOW   = "window"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DirectConnection:
    """A manually bridged span (tunnel / viaduct) — the terrain inside is
    replaced by a straight line between the boundary elevations."""
    s_start: float
    s_end:   float
    label:   str = ""


@dataclass
class VerticalProfile:
    """Vertical alignment state.

    ``s`` / ``z_raw`` are the expensive part (one DEM query each) and are the
    only sampled data persisted in the project file.  Everything below the
    "derived" line is rebuilt by :func:`rebuild_profile`.
    """

    # -- persisted --------------------------------------------------------
    s:     np.ndarray = field(default_factory=lambda: np.zeros(0))
    z_raw: np.ndarray = field(default_factory=lambda: np.zeros(0))
    sample_interval:  float = DEFAULT_SAMPLE_INTERVAL
    elevation_source: str   = DEFAULT_ELEVATION_SOURCE
    method:           str   = METHOD_GRADIENT
    max_grade_pm:     float = DEFAULT_MAX_GRADE_PM
    z_tol:            float = DEFAULT_Z_TOL
    window_m:         float = DEFAULT_WINDOW_M
    poly_order:       int   = DEFAULT_POLY_ORDER
    vc_enabled:       bool  = True
    vc_length:        float = DEFAULT_VC_LENGTH
    connections: list = field(default_factory=list)
    # (n_elements, round(total_length, 3)) of the alignment these samples were
    # taken along — lets the step warn when the horizontal geometry moved
    # underneath an already-sampled profile.
    geom_fingerprint: tuple = (0, 0.0)

    # -- derived (rebuild_profile) — never persisted -----------------------
    z_override: np.ndarray = field(default_factory=lambda: np.zeros(0))
    z_design:   np.ndarray = field(default_factory=lambda: np.zeros(0))
    pvis:  list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def has_samples(self) -> bool:
        return len(self.s) >= 2 and len(self.z_raw) == len(self.s)


def empty_stats() -> dict:
    """The stats dict shape, all-zero. Every consumer unpacks these keys, so
    an empty profile must still carry all of them (same discipline as the
    deviation-metrics dicts — a missing key becomes a TypeError downstream)."""
    return {
        "max_grade_pm": 0.0, "z_min": 0.0, "z_max": 0.0,
        "max_cut": 0.0, "max_fill": 0.0,
        "n_pvi": 0, "n_nodata": 0, "n_samples": 0, "length_m": 0.0,
    }


def geom_fingerprint(elements: list | None) -> tuple:
    """(element count, total length) — cheap identity of a horizontal chain."""
    els = elements or []
    total = sum(float(e.get("length", 0.0)) for e in els)
    return (len(els), round(total, 3))


def make_default_profile(
    elements: list | None,
    settings: dict | None = None,
) -> VerticalProfile:
    """A fresh, unsampled profile seeded from the Step 3 settings.

    Used both for a brand-new project and for a legacy `.coypuf` that predates
    the vertical block, so the fallbacks mirror what the export worker used to
    read out of the settings dict.
    """
    s = settings or {}
    return VerticalProfile(
        sample_interval=float(s.get("sample_interval", DEFAULT_SAMPLE_INTERVAL)),
        elevation_source=str(s.get("elevation_source", DEFAULT_ELEVATION_SOURCE)),
        vc_length=float(s.get("vc_length", DEFAULT_VC_LENGTH)),
        geom_fingerprint=geom_fingerprint(elements),
    )


# ---------------------------------------------------------------------------
# Terrain sampling (along the FITTED alignment, not the OSM polyline)
# ---------------------------------------------------------------------------

def sample_points_along_elements(
    elements: list,
    work_epsg: int,
    interval: float = DEFAULT_SAMPLE_INTERVAL,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """
    ``(s, latlon)`` every *interval* metres along the fitted centreline.

    Sampling the element chain rather than the raw OSM polyline is what puts
    ``<Profile>`` and ``<CoordGeom>`` on the same station axis — they were
    previously fitted against different lengths.

    The projection is a single batched ``projected_to_wgs84`` call, never one
    call per sample (see the perf invariants in CLAUDE.md).
    """
    from geometry.cross_section import sample_alignment_with_headings
    from geometry.projection import projected_to_wgs84

    els = elements or []
    pts = sample_alignment_with_headings(els, max(float(interval), 1.0))
    if not pts:
        return np.zeros(0), []

    s  = [p[0] for p in pts]
    xy = [(p[1], p[2]) for p in pts]

    # The sampler walks a fixed grid, so unless the total length happens to be
    # an exact multiple of the interval it stops up to one interval short —
    # which would leave every exported <ProfAlign> ending before its
    # <Alignment>. Close the gap with the true end point.
    total = float(els[-1].get("sta_start", 0.0)) + float(els[-1].get("length", 0.0))
    if total - s[-1] > 1e-6:
        end = els[-1].get("end") or xy[-1]
        s.append(total)
        xy.append((float(end[0]), float(end[1])))

    latlon = projected_to_wgs84(xy, work_epsg)
    return (np.array(s, dtype=float),
            [(float(a), float(b)) for a, b in latlon])


# ---------------------------------------------------------------------------
# Direct connections (tunnels / bridges)
# ---------------------------------------------------------------------------

def normalise_connections(
    connections: list,
    s_lo: float,
    s_hi: float,
    min_span: float = 1e-6,
) -> list[DirectConnection]:
    """Clip to the alignment, drop degenerate spans, merge overlaps, sort.

    Merging rather than rejecting overlaps keeps the table forgiving: two
    ranges the user typed that happen to touch become one connector instead of
    fighting over the same samples.
    """
    clean: list[DirectConnection] = []
    for c in connections or []:
        a, b = float(c.s_start), float(c.s_end)
        if b < a:
            a, b = b, a
        a = max(a, float(s_lo))
        b = min(b, float(s_hi))
        if b - a <= min_span:
            continue
        clean.append(DirectConnection(a, b, getattr(c, "label", "") or ""))

    clean.sort(key=lambda c: c.s_start)
    merged: list[DirectConnection] = []
    for c in clean:
        if merged and c.s_start <= merged[-1].s_end:
            prev = merged[-1]
            prev.s_end = max(prev.s_end, c.s_end)
            if c.label and not prev.label:
                prev.label = c.label
        else:
            merged.append(DirectConnection(c.s_start, c.s_end, c.label))
    return merged


def apply_connections(
    s: np.ndarray,
    z: np.ndarray,
    connections: list,
) -> np.ndarray:
    """Replace each connection span with a straight line between its boundary
    elevations:  ``z(s) = z0 + (z1 - z0) / (s1 - s0) * (s - s0)``.

    Boundary elevations are interpolated from *z* itself, so the connector
    always meets the surrounding series exactly.
    """
    out = np.asarray(z, dtype=float).copy()
    s = np.asarray(s, dtype=float)
    if len(s) == 0 or len(out) != len(s):
        return out

    for c in connections or []:
        span = float(c.s_end) - float(c.s_start)
        if span <= 1e-9:
            continue
        z0 = float(np.interp(c.s_start, s, out))
        z1 = float(np.interp(c.s_end,   s, out))
        m = (s >= c.s_start) & (s <= c.s_end)
        if not m.any():
            continue
        out[m] = z0 + (z1 - z0) * (s[m] - float(c.s_start)) / span
    return out


def connection_sample_mask(s: np.ndarray, connections: list) -> np.ndarray:
    """Boolean mask of the samples covered by any connection — the canvas
    draws these as a separate highlighted series."""
    s = np.asarray(s, dtype=float)
    mask = np.zeros(len(s), dtype=bool)
    for c in connections or []:
        mask |= (s >= float(c.s_start)) & (s <= float(c.s_end))
    return mask


def _forced_indices(s: np.ndarray, connections: list) -> list[int]:
    """Sample indices nearest each connection boundary — simplification is run
    between them so a connector's ends are never smoothed away."""
    out: list[int] = []
    n = len(s)
    if n == 0:
        return out
    for c in connections or []:
        for v in (float(c.s_start), float(c.s_end)):
            i = int(np.clip(np.searchsorted(s, v), 0, n - 1))
            if i > 0 and abs(s[i - 1] - v) < abs(s[i] - v):
                i -= 1
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Smoothing primitives
# ---------------------------------------------------------------------------

def bridge_nodata(s: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Linearly bridge NaN gaps (DEM misses outside coverage / failed queries)
    so downstream maths never has to special-case them."""
    z = np.asarray(z, dtype=float)
    mask = np.isfinite(z)
    if mask.all():
        return z.copy()
    out = z.copy()
    if not mask.any():
        return np.zeros_like(out)
    s = np.asarray(s, dtype=float)
    out[~mask] = np.interp(s[~mask], s[mask], z[mask])
    return out


def window_samples(window_m: float, ds: float, n: int) -> int:
    """Window length in metres -> an odd sample count in [3, n]."""
    ds = max(float(ds), 1e-6)
    w = int(round(float(window_m) / ds))
    if w % 2 == 0:
        w += 1
    n_odd = n if n % 2 == 1 else n - 1
    return max(3, min(w, max(3, n_odd)))


def smooth_window(
    z: np.ndarray,
    window_m: float,
    ds: float,
    poly_order: int = DEFAULT_POLY_ORDER,
) -> np.ndarray:
    """Savitzky-Golay (poly_order >= 1) or plain moving average (poly_order 0).

    Falls back to the moving average whenever the window cannot support the
    requested polynomial order, so the controls can never produce an exception.
    """
    z = np.asarray(z, dtype=float)
    n = len(z)
    if n < 3:
        return z.copy()

    w = window_samples(window_m, ds, n)
    if w > n:
        return z.copy()

    order = int(poly_order)
    if order >= 1 and w > order:
        try:
            from scipy.signal import savgol_filter
            return np.asarray(savgol_filter(z, w, order, mode="interp"),
                              dtype=float)
        except Exception:
            pass    # degenerate window / scipy unavailable -> moving average

    kernel = np.ones(w) / w
    padded = np.pad(z, w // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")[:n]


def douglas_peucker(
    s: np.ndarray,
    z: np.ndarray,
    tol: float,
    forced_idx: list | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Simplify ``(s, z)`` into constant-grade tangents joined at PVIs.

    Ramer-Douglas-Peucker with a *vertical* deviation measure (not
    perpendicular — the axes have different units), so the guarantee is
    ``max |z - piecewise_linear(PVIs)| <= tol``.  Iterative rather than
    recursive: a 135 km line at 20 m is ~6 750 samples, deep enough to hit the
    interpreter's recursion limit.

    Indices in *forced_idx* are always kept and simplification runs
    independently between them, which is how a connection's boundaries survive.
    """
    s = np.asarray(s, dtype=float)
    z = np.asarray(z, dtype=float)
    n = len(s)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    if n <= 2:
        return s.copy(), z.copy()

    anchors = sorted({0, n - 1} | {int(i) for i in (forced_idx or ()) if 0 <= i < n})
    keep: set = set(anchors)
    tol = max(float(tol), 0.0)

    for a, b in zip(anchors[:-1], anchors[1:]):
        stack = [(a, b)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            ds = s[j] - s[i]
            if ds <= 1e-12:
                continue
            t = (s[i + 1:j] - s[i]) / ds
            dev = np.abs(z[i + 1:j] - (z[i] + t * (z[j] - z[i])))
            k = int(np.argmax(dev))
            if dev[k] > tol:
                m = i + 1 + k
                keep.add(m)
                stack.append((i, m))
                stack.append((m, j))

    idx = np.array(sorted(keep), dtype=int)
    return s[idx], z[idx]


def _sweep(z: np.ndarray, cap: np.ndarray, forward: bool) -> np.ndarray:
    """One greedy clamp pass. Anchored at the first (forward) or last
    (backward) PVI; the result always satisfies every grade constraint."""
    out = z.copy()
    n = len(out)
    rng = range(n - 1) if forward else range(n - 2, -1, -1)
    for i in rng:
        d = out[i + 1] - out[i]
        if d > cap[i]:
            if forward:
                out[i + 1] = out[i] + cap[i]
            else:
                out[i] = out[i + 1] - cap[i]
        elif d < -cap[i]:
            if forward:
                out[i + 1] = out[i] - cap[i]
            else:
                out[i] = out[i + 1] + cap[i]
    return out


def clamp_grades(
    s_pvi: np.ndarray,
    z_pvi: np.ndarray,
    max_grade: float,
) -> np.ndarray:
    """
    Enforce ``|dz/ds| <= max_grade`` (a decimal fraction, not per mille).

    A single forward sweep is already feasible, but it anchors on the first PVI
    and dumps the whole accumulated error at the far end.  The feasible set
    ``{ |z[i+1] - z[i]| <= max_grade * ds[i] }`` is convex, so the *average* of
    the forward- and backward-anchored sweeps is also feasible — and balanced.
    That makes the result symmetric, idempotent (clamping a compliant profile
    is a no-op) and independent of which end you start from.
    """
    z = np.asarray(z_pvi, dtype=float).copy()
    s = np.asarray(s_pvi, dtype=float)
    n = len(z)
    if n < 2 or max_grade <= 0:
        return z

    cap = np.maximum(np.diff(s), 0.0) * float(max_grade)
    for _ in range(4):      # converges on the first pass; bounded for safety
        z_new = 0.5 * (_sweep(z, cap, True) + _sweep(z, cap, False))
        if np.max(np.abs(z_new - z)) < 1e-9:
            return z_new
        z = z_new
    return z


# ---------------------------------------------------------------------------
# PVI -> LandXML vertical elements
# ---------------------------------------------------------------------------

def build_vertical_elements(
    s_pvi: np.ndarray,
    z_pvi: np.ndarray,
    vc_enabled: bool = True,
    vc_length: float = DEFAULT_VC_LENGTH,
) -> list[dict]:
    """
    ``[{"type": "PVI", …}, {"type": "ParaCurve", …}, …]`` — exactly the shape
    ``landxml.builder._add_vertical_elements`` consumes.

    A ParaCurve of length L straddles its PVI by L/2 each way, so L is clamped
    to the shorter neighbouring tangent.  (``elevation.fit_vertical_geometry``
    applies ``vc_length`` unconditionally, which lets adjacent curves overlap
    on closely-spaced grade breaks.)
    """
    s_pvi = np.asarray(s_pvi, dtype=float)
    z_pvi = np.asarray(z_pvi, dtype=float)
    n = len(s_pvi)
    out: list[dict] = []
    if n == 0:
        return out

    for i in range(n):
        out.append({
            "type": "PVI",
            "station": float(s_pvi[i]),
            "elevation": float(z_pvi[i]),
        })
        if vc_enabled and 0 < i < n - 1:
            span = min(s_pvi[i] - s_pvi[i - 1], s_pvi[i + 1] - s_pvi[i])
            length = min(float(vc_length), float(span))
            if length > 1e-6:
                out.append({
                    "type": "ParaCurve",
                    "station": float(s_pvi[i]),
                    "length": length,
                    "elevation": float(z_pvi[i]),
                })
    return out


def profile_stats(
    s: np.ndarray,
    z_terrain: np.ndarray,
    z_design: np.ndarray,
    s_pvi: np.ndarray,
    z_pvi: np.ndarray,
    n_nodata: int = 0,
) -> dict:
    """Live read-out values. Cut = design below terrain, fill = design above."""
    st = empty_stats()
    if len(s) == 0:
        return st

    diff = np.asarray(z_design, float) - np.asarray(z_terrain, float)
    st["n_samples"] = int(len(s))
    st["n_nodata"]  = int(n_nodata)
    st["length_m"]  = float(s[-1] - s[0])
    st["z_min"]     = float(np.min(z_design)) if len(z_design) else 0.0
    st["z_max"]     = float(np.max(z_design)) if len(z_design) else 0.0
    st["max_cut"]   = float(max(0.0, -np.min(diff))) if len(diff) else 0.0
    st["max_fill"]  = float(max(0.0,  np.max(diff))) if len(diff) else 0.0
    st["n_pvi"]     = int(len(s_pvi))

    if len(s_pvi) >= 2:
        ds = np.diff(np.asarray(s_pvi, float))
        dz = np.diff(np.asarray(z_pvi, float))
        grades = dz / np.maximum(ds, 1e-9)
        st["max_grade_pm"] = float(np.max(np.abs(grades)) * 1000.0)
    return st


# ---------------------------------------------------------------------------
# The rebuild entry point
# ---------------------------------------------------------------------------

def rebuild_profile(p: VerticalProfile) -> VerticalProfile:
    """
    Regenerate every derived field of *p* in place and return it.

    The vertical counterpart of ``rebuild_from_pi_model``: nothing downstream
    ever caches ``z_design`` / ``pvis`` / ``stats``, they are recomputed from
    the samples and the configuration on every parameter change and on load.
    """
    s = np.asarray(p.s, dtype=float)
    z = np.asarray(p.z_raw, dtype=float)

    if len(s) < 2 or len(z) != len(s):
        p.z_override = np.zeros(0)
        p.z_design   = np.zeros(0)
        p.pvis       = []
        p.stats      = empty_stats()
        return p

    n_nodata = int(np.count_nonzero(~np.isfinite(z)))
    z_filled = bridge_nodata(s, z)

    conns = normalise_connections(p.connections, float(s[0]), float(s[-1]))
    z_override = apply_connections(s, z_filled, conns)

    if p.method == METHOD_WINDOW:
        ds = float(np.median(np.diff(s))) if len(s) > 1 else p.sample_interval
        z_work = smooth_window(z_override, p.window_m, ds, p.poly_order)
        # Re-stamp the connectors using the *filtered* boundary values so a
        # tunnel blends into its smoothed neighbourhood rather than snapping
        # back to raw DEM noise at each end.
        z_work = apply_connections(s, z_work, conns)
    else:
        z_work = z_override

    s_pvi, z_pvi = douglas_peucker(s, z_work, p.z_tol, _forced_indices(s, conns))
    z_pvi = clamp_grades(s_pvi, z_pvi, float(p.max_grade_pm) / 1000.0)

    p.z_override = z_override
    p.z_design   = (np.interp(s, s_pvi, z_pvi) if len(s_pvi) >= 2
                    else np.full(len(s), z_pvi[0] if len(z_pvi) else 0.0))
    p.pvis  = build_vertical_elements(s_pvi, z_pvi, p.vc_enabled, p.vc_length)
    p.stats = profile_stats(s, z_filled, p.z_design, s_pvi, z_pvi, n_nodata)
    return p
