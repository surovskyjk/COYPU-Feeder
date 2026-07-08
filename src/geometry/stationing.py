"""
Station / stop chainage estimation and CSV export.

Chainages are measured along the *fitted* element chain (the same chain
written to LandXML), so the CSV stationing is consistent with the LandXML
internal stationing by construction (staStart = 0 at the alignment start).

CSV format (one row per unique station name, sorted by chainage):

    Station,Dwell Time,Name
    1.234,30,Praha hl.n.

`Station` is the chainage in km with 3 decimals; `Dwell Time` in seconds.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field

import numpy as np


DEFAULT_DWELL_S = 30


@dataclass
class Station:
    name:       str
    latlon:     tuple            # (lat, lon) WGS84
    chainage_m: float = 0.0
    dwell_s:    int   = DEFAULT_DWELL_S
    source:     str   = "manual"     # "auto" | "manual"
    dist_m:     float = 0.0          # perpendicular distance to the alignment


def _alignment_samples(elements: list[dict], interval: float = 1.0):
    """
    Dense (x, y) samples along the element chain plus the chainage of each
    sample. Returns (pts: np.ndarray (N,2), chain: np.ndarray (N,)).
    """
    from geometry.alignment import reconstruct_alignment_per_element

    pts: list = []
    chain: list = []
    for el, seg_pts in reconstruct_alignment_per_element(elements, interval):
        if not seg_pts:
            continue
        sta0   = float(el.get("sta_start", 0.0))
        length = float(el.get("length", 0.0))
        n = len(seg_pts)
        for i, p in enumerate(seg_pts):
            # Uniform parametrisation along the element is accurate enough
            # for station snapping (samples every `interval` metres).
            s = sta0 + length * (i / max(1, n - 1))
            pts.append(p)
            chain.append(s)
    if not pts:
        return np.zeros((0, 2)), np.zeros(0)
    return np.asarray(pts, dtype=float), np.asarray(chain, dtype=float)


def project_to_chainage(
    xy_pt:    tuple,
    elements: list[dict],
    samples=None,
) -> tuple[float, float]:
    """
    Project a projected-CRS point onto the alignment.

    Returns (chainage_m, perpendicular_distance_m). `samples` may be the
    precomputed result of `_alignment_samples(elements)` to amortise cost
    across many stations.
    """
    if samples is None:
        samples = _alignment_samples(elements)
    pts, chain = samples
    if len(pts) == 0:
        return 0.0, float("inf")
    d = np.hypot(pts[:, 0] - xy_pt[0], pts[:, 1] - xy_pt[1])
    i = int(np.argmin(d))
    return float(chain[i]), float(d[i])


def snap_stations(
    stations:  list[Station],
    elements:  list[dict],
    work_epsg: int,
) -> None:
    """
    Recompute chainage + distance for every station from its stored latlon
    (in place). Call after the alignment changes (refine / re-fit).
    """
    from geometry.projection import wgs84_to_projected
    if not elements or not stations:
        return
    samples = _alignment_samples(elements)
    lat_lon = [(s.latlon[0], s.latlon[1]) for s in stations]
    xy = wgs84_to_projected(lat_lon, work_epsg)
    for s, p in zip(stations, xy):
        s.chainage_m, s.dist_m = project_to_chainage(p, elements, samples)


def dedupe_by_name(stations: list[Station]) -> list[Station]:
    """
    One entry per unique name — the occurrence closest to the alignment
    wins. Unnamed stations get a chainage-based fallback name.
    """
    by_name: dict = {}
    for s in stations:
        name = (s.name or "").strip()
        if not name:
            name = f"Stop @ km {s.chainage_m / 1000.0:.3f}"
            s.name = name
        cur = by_name.get(name)
        if cur is None or s.dist_m < cur.dist_m:
            by_name[name] = s
    return sorted(by_name.values(), key=lambda s: s.chainage_m)


def write_stations_csv(stations: list[Station], filepath: str) -> int:
    """
    Write the `Station,Dwell Time,Name` CSV (km with 3 decimals, dwell in
    seconds). Rows sorted by chainage; names must already be unique
    (see dedupe_by_name). Returns the number of rows written.
    """
    rows = sorted(stations, key=lambda s: s.chainage_m)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Station", "Dwell Time", "Name"])
        for s in rows:
            w.writerow([f"{s.chainage_m / 1000.0:.3f}", int(s.dwell_s), s.name])
    return len(rows)
