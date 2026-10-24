"""
Station / stop chainage estimation, COYPU stopping patterns and CSV export.

Chainages are measured along the *fitted* element chain (the same chain
written to LandXML), so the CSV stationing is consistent with the LandXML
internal stationing by construction (staStart = 0 at the alignment start).

The CSV is COYPU's stops CSV (``stops_model.CSV_COLUMNS`` /
``serializeStopsCsv`` in the COYPU repository), one row per unique station
name, sorted by chainage:

    stopName,stationingKm,dwellTimeS,municipalityName,population,stopInOs,stopInSp,stopInEx
    Kolín,61.234,30,Kolín,33444,1,1,0

``stationingKm`` is the chainage in km with 3 decimals, ``dwellTimeS`` the
dwell in seconds, ``population`` is empty when unknown and the three
``stopIn*`` flags are 1/0.

The Os / Sp / Ex stopping patterns follow COYPU's own rules (ported from
``stops_model``): every stop is in Os, the line endpoints and every stop
whose municipality reaches the population threshold are in Sp, and only the
endpoints are in Ex. A hand-set Sp or Ex flag is a manual override the rules
leave alone.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field

import numpy as np


DEFAULT_DWELL_S = 30

# COYPU's stops_model.DEFAULT_SP_POPULATION_THRESHOLD — keep the two equal,
# so the Sp flags written here read back in COYPU as following its own rule
# rather than as manual overrides.
DEFAULT_SP_THRESHOLD = 2000

# COYPU's stops CSV header (stops_model.CSV_COLUMNS), in its order.
CSV_COLUMNS = ("stopName", "stationingKm", "dwellTimeS", "municipalityName",
               "population", "stopInOs", "stopInSp", "stopInEx")

PATTERN_KEYS = ("Os", "Sp", "Ex")


@dataclass
class Station:
    name:       str
    latlon:     tuple            # (lat, lon) WGS84
    chainage_m: float = 0.0
    dwell_s:    int   = DEFAULT_DWELL_S
    source:     str   = "manual"     # "auto" | "manual"
    dist_m:     float = 0.0          # perpendicular distance to the alignment
    # OSM identity (auto-detect): the station/halt node when one exists,
    # otherwise the stop position the chainage was taken from.
    osm_id:     int | None = None
    kind:       str   = ""           # "station" | "halt" | "stop_position" | ""
    # Municipality the stop lies in and its number of inhabitants.
    municipality:      str        = ""
    population:        int | None = None
    population_year:   int | None = None
    population_source: str        = ""     # "wikidata" | "osm" | "manual" | ""
    # Municipality/population typed in by hand — lookups leave them alone.
    info_manual: bool = False
    # COYPU stopping patterns (see apply_pattern_rules).
    stop_os:   bool = True
    stop_sp:   bool = False
    stop_ex:   bool = False
    sp_manual: bool = False
    ex_manual: bool = False


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


def latlon_at_chainage(
    chainage_m: float,
    elements:   list[dict],
    work_epsg:  int,
) -> tuple | None:
    """
    Inverse of `project_to_chainage`: the WGS84 position of the alignment
    point at the given chainage. Used to place a map marker for stations
    entered manually by their km value.
    """
    from geometry.projection import projected_to_wgs84
    if not elements:
        return None
    pts, chain = _alignment_samples(elements)
    if len(pts) == 0:
        return None
    i = int(np.argmin(np.abs(chain - chainage_m)))
    lat, lon = projected_to_wgs84([tuple(pts[i])], work_epsg)[0]
    return (lat, lon)


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

    The winner is usually a stop position on the track; when a same-name
    railway=station/halt node exists, its kind and node id are kept, so the
    list still tells a station from a halt.
    """
    by_name: dict = {}
    place: dict = {}         # name → the station/halt node of that name
    for s in stations:
        name = (s.name or "").strip()
        if not name:
            name = f"Stop @ km {s.chainage_m / 1000.0:.3f}"
            s.name = name
        cur = by_name.get(name)
        if cur is None or s.dist_m < cur.dist_m:
            by_name[name] = s
        if s.kind in ("station", "halt") and name not in place:
            place[name] = s
    for name, s in by_name.items():
        donor = place.get(name)
        if donor is not None and donor is not s:
            s.kind, s.osm_id = donor.kind, donor.osm_id
    return sorted(by_name.values(), key=lambda s: s.chainage_m)


# ---------------------------------------------------------------------------
# COYPU stopping patterns (port of COYPU's stops_model rules)
# ---------------------------------------------------------------------------

def endpoint_indices(stations: list[Station]) -> set[int]:
    """Indices of the lowest and the highest chainage — the line endpoints
    (COYPU ``endpointIndices``)."""
    if not stations:
        return set()
    chain = [float(s.chainage_m) for s in stations]
    return {chain.index(min(chain)), chain.index(max(chain))}


def automatic_sp(station: Station, is_endpoint: bool, threshold: float) -> bool:
    """The Sp flag the automatic rule gives one stop: an endpoint, or a
    population at or above *threshold* — a missing population never
    qualifies (COYPU ``automaticSpFlag``)."""
    pop = station.population
    return bool(is_endpoint or (pop is not None and float(pop) >= float(threshold)))


def apply_pattern_rules(
    stations:  list[Station],
    threshold: float = DEFAULT_SP_THRESHOLD,
    sp_auto:   bool  = True,
) -> list[Station]:
    """Recompute every automatic flag in place: Ex always follows the
    endpoints, Sp follows the population rule only while *sp_auto* is on;
    manual overrides are left alone. Os has no rule — it is on unless the
    user turns it off (COYPU ``applyPatternRules``)."""
    endpoints = endpoint_indices(stations)
    for i, s in enumerate(stations):
        is_end = i in endpoints
        if not s.ex_manual:
            s.stop_ex = is_end
        if sp_auto and not s.sp_manual:
            s.stop_sp = automatic_sp(s, is_end, threshold)
    return stations


def reset_overrides(
    stations:  list[Station],
    threshold: float = DEFAULT_SP_THRESHOLD,
) -> list[Station]:
    """Clear every manual Sp/Ex override and re-apply the automatic rules
    (COYPU ``resetSpOverrides``)."""
    for s in stations:
        s.sp_manual = False
        s.ex_manual = False
    return apply_pattern_rules(stations, threshold, True)


def pattern_counts(stations: list[Station]) -> dict:
    """Number of stops in each pattern: {"Os": n, "Sp": n, "Ex": n}."""
    return {"Os": sum(1 for s in stations if s.stop_os),
            "Sp": sum(1 for s in stations if s.stop_sp),
            "Ex": sum(1 for s in stations if s.stop_ex)}


_SPACES = (" ", " ", " ")


def parse_population(text) -> int | None:
    """Whole number from text; thousands separators such as ``12,500``,
    ``12 500`` or ``12.500`` are dropped, blank or unreadable text gives
    None (COYPU ``parseInteger``)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(round(float(text)))
    cleaned = str(text).strip()
    for sp in _SPACES:
        cleaned = cleaned.replace(sp, "")
    if not cleaned:
        return None
    for sep in (",", "."):
        groups = cleaned.split(sep)
        grouped = (len(groups) > 1 and groups[0].isdigit() and len(groups[0]) <= 3
                   and all(len(g) == 3 and g.isdigit() for g in groups[1:]))
        if grouped:
            return int("".join(groups))
    try:
        return int(round(float(cleaned.replace(",", "."))))
    except ValueError:
        return None


def write_stations_csv(stations: list[Station], filepath: str) -> int:
    """
    Write COYPU's stops CSV (see the module docstring): km with 3 decimals,
    dwell in seconds, population empty when unknown, pattern flags 1/0.
    Rows sorted by chainage; names must already be unique (see
    dedupe_by_name). Returns the number of rows written.
    """
    rows = sorted(stations, key=lambda s: s.chainage_m)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        for s in rows:
            w.writerow([
                s.name,
                f"{s.chainage_m / 1000.0:.3f}",
                int(s.dwell_s),
                s.municipality or "",
                "" if s.population is None else int(s.population),
                int(bool(s.stop_os)),
                int(bool(s.stop_sp)),
                int(bool(s.stop_ex)),
            ])
    return len(rows)
