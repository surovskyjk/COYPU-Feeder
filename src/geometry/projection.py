"""
WGS84 ↔ projected CRS transformations using pyproj.
The internal working CRS is always a metric flat plane (auto-selected UTM or user-chosen).
"""

import math
from pyproj import Transformer, CRS


def utm_epsg_for_lon(lon: float) -> int:
    """Return the EPSG code of the UTM zone covering the given longitude."""
    zone = int((lon + 180) / 6) + 1
    # Assume northern hemisphere for simplicity; railway tool can handle both
    return 32600 + zone  # WGS84 UTM North


def make_transformer(source_epsg: int, target_epsg: int) -> Transformer:
    return Transformer.from_crs(source_epsg, target_epsg, always_xy=True)


def wgs84_to_projected(
    coords_latlon: list[tuple[float, float]],
    target_epsg: int,
) -> list[tuple[float, float]]:
    """
    Project (lat, lon) pairs to (easting, northing) in target_epsg.
    Returns (x, y) = (easting, northing).
    """
    transformer = make_transformer(4326, target_epsg)
    result = []
    for lat, lon in coords_latlon:
        x, y = transformer.transform(lon, lat)
        result.append((x, y))
    return result


def projected_to_wgs84(
    coords_xy: list[tuple[float, float]],
    source_epsg: int,
) -> list[tuple[float, float]]:
    """Inverse: (x, y) → (lat, lon)."""
    transformer = make_transformer(source_epsg, 4326)
    result = []
    for x, y in coords_xy:
        lon, lat = transformer.transform(x, y)
        result.append((lat, lon))
    return result


def auto_utm_epsg(coords_latlon: list[tuple[float, float]]) -> int:
    """Pick a UTM zone based on the centroid of the track."""
    lats = [c[0] for c in coords_latlon]
    lons = [c[1] for c in coords_latlon]
    centre_lon = sum(lons) / len(lons)
    centre_lat = sum(lats) / len(lats)
    zone = int((centre_lon + 180) / 6) + 1
    if centre_lat >= 0:
        return 32600 + zone
    else:
        return 32700 + zone


def transform_elements(
    elements: list[dict],
    from_epsg: int,
    to_epsg: int,
    force_positive: bool = False,
) -> list[dict]:
    """
    Reproject the projected coordinates stored inside element dicts from
    *from_epsg* to *to_epsg*.

    Element point fields transformed:
      Line   — start, end  →  direction_rad and length recomputed
      Arc    — start, end, center  →  radius, chord, length recomputed
               (arc_angle = length/radius is preserved)
      Spiral — start, end  →  radius_start/end, length, clothoid_A
               scaled by local linear scale factor

    If *force_positive* is True, abs() is applied to all output coordinates
    (S-JTSK positive convention).

    If *from_epsg* == *to_epsg* and not *force_positive*, the list is
    returned unchanged.
    """
    import math as _math

    if from_epsg == to_epsg and not force_positive:
        return elements

    tr = Transformer.from_crs(from_epsg, to_epsg, always_xy=True)

    def _tp(pt: list | None) -> list | None:
        if pt is None:
            return None
        x, y = tr.transform(pt[0], pt[1])
        if force_positive:
            x, y = abs(x), abs(y)
        return [x, y]

    result: list[dict] = []
    for el in elements:
        e = dict(el)
        etype = e.get("type", "Line")

        old_start = e.get("start")
        old_end   = e.get("end")
        new_start = _tp(old_start)
        new_end   = _tp(old_end)

        if new_start:
            e["start"] = new_start
        if new_end:
            e["end"] = new_end

        if etype == "Line":
            if new_start and new_end:
                e["direction_rad"] = _math.atan2(
                    new_end[1] - new_start[1],
                    new_end[0] - new_start[0],
                )
                e["length"] = _math.hypot(
                    new_end[0] - new_start[0],
                    new_end[1] - new_start[1],
                )

        elif etype == "Arc":
            old_center = e.get("center")
            new_center = _tp(old_center)
            if new_center:
                e["center"] = new_center
            if new_center and new_start and new_end:
                r_s = _math.hypot(new_start[0] - new_center[0],
                                  new_start[1] - new_center[1])
                r_e = _math.hypot(new_end[0]   - new_center[0],
                                  new_end[1]   - new_center[1])
                new_radius = (r_s + r_e) / 2.0
                e["radius"] = new_radius
                e["chord"]  = _math.hypot(new_end[0] - new_start[0],
                                          new_end[1] - new_start[1])
                # preserve arc_angle so arc length scales with radius
                old_radius = float(el.get("radius", new_radius) or new_radius)
                old_length = float(el.get("length", 0.0))
                if old_radius > 0:
                    arc_angle = old_length / old_radius
                    e["length"] = new_radius * arc_angle

        elif etype == "Spiral":
            # Scale radii/length by chord-to-chord scale factor
            if old_start and old_end and new_start and new_end:
                old_chord = _math.hypot(old_end[0] - old_start[0],
                                        old_end[1] - old_start[1])
                new_chord = _math.hypot(new_end[0] - new_start[0],
                                        new_end[1] - new_start[1])
                k = (new_chord / old_chord) if old_chord > 1e-9 else 1.0
                r_s = el.get("radius_start", float("inf"))
                r_e = el.get("radius_end",   float("inf"))
                e["radius_start"] = r_s * k if not _math.isinf(r_s) else r_s
                e["radius_end"]   = r_e * k if not _math.isinf(r_e) else r_e
                e["length"]       = float(el.get("length", 0.0)) * k
                old_A = float(el.get("clothoid_A", 0.0))
                e["clothoid_A"]   = old_A * _math.sqrt(k)

        result.append(e)

    # Recompute sta_start sequentially
    sta = 0.0
    for e in result:
        e["sta_start"] = sta
        sta += float(e.get("length", 0.0))

    return result


# Preset CRS options shown in the GUI
CRS_PRESETS: list[tuple[str, int]] = [
    ("WGS 84 (EPSG:4326)", 4326),
    ("S-JTSK / Krovak East North (EPSG:5514)", 5514),
    ("UTM zone 32N (EPSG:32632)", 32632),
    ("UTM zone 33N (EPSG:32633)", 32633),
    ("UTM zone 34N (EPSG:32634)", 32634),
    ("ETRS89 / UTM zone 32N (EPSG:25832)", 25832),
    ("ETRS89 / UTM zone 33N (EPSG:25833)", 25833),
    ("Auto UTM (from track centroid)", -1),
]
