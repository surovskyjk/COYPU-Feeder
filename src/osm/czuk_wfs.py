"""
ČÚZK INSPIRE WFS — Czech railway transport network (RailwayLink) data.

Endpoint: https://geoportal.cuzk.cz/wfs_inspire_tn_rail/WFService.aspx
Standard: OGC WFS 2.0.0 / INSPIRE Transport Networks (Rail)
Access:   Free, no authentication required.
Coverage: Czech Republic only.

Returns Track objects with the same interface as osm.parser.parse_tracks(),
so the rest of the pipeline (Step 2 onwards) is fully unaffected.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Callable, Optional

import requests

from osm.parser import Track

CZUK_WFS_URL = "https://geoportal.cuzk.cz/wfs_inspire_tn_rail/WFService.aspx"
TIMEOUT = 45   # WFS can be slow on first cold response


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _track_length_km(nodes: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(nodes) - 1):
        total += _haversine_km(*nodes[i], *nodes[i + 1])
    return total


def _looks_like_czech_wgs84(nodes: list[tuple[float, float]]) -> bool:
    """
    Check whether (a, b) pairs look like (lat, lon) in Czech Republic.
    Czech bounding box: lat 48.5–51.2 N, lon 12.1–18.9 E.
    """
    if not nodes:
        return False
    a0, b0 = nodes[0]
    return 48.0 <= a0 <= 52.0 and 11.0 <= b0 <= 20.0


# ---------------------------------------------------------------------------
# GML parser
# ---------------------------------------------------------------------------

def _parse_pos_list(text: str) -> list[tuple[float, float]]:
    """Parse whitespace-separated coordinate pairs from a GML posList."""
    parts = text.split()
    coords: list[tuple[float, float]] = []
    for i in range(0, len(parts) - 1, 2):
        try:
            coords.append((float(parts[i]), float(parts[i + 1])))
        except ValueError:
            pass
    return coords


def _extract_geom(feature_elem) -> list[tuple[float, float]]:
    """
    Extract a list of (a, b) pairs from any GML geometry element.
    Works for LineString, Curve, MultiCurve.
    """
    for ns_uri in [
        "http://www.opengis.net/gml/3.2",
        "http://www.opengis.net/gml",
    ]:
        # posList (most common in GML 3.x)
        el = feature_elem.find(f".//{{{ns_uri}}}posList")
        if el is not None and el.text:
            return _parse_pos_list(el.text.strip())
        # coordinates (GML 2 style)
        el = feature_elem.find(f".//{{{ns_uri}}}coordinates")
        if el is not None and el.text:
            coords: list[tuple[float, float]] = []
            for pair in el.text.strip().split():
                parts = pair.split(",")
                if len(parts) >= 2:
                    try:
                        # GML coordinates are x,y (lon,lat for geographic)
                        coords.append((float(parts[1]), float(parts[0])))
                    except ValueError:
                        pass
            return coords
        # pos (single point — build list from multiple <pos> elements)
        pos_elems = feature_elem.findall(f".//{{{ns_uri}}}pos")
        if pos_elems:
            coords = []
            for el in pos_elems:
                if el.text:
                    vals = el.text.split()
                    if len(vals) >= 2:
                        try:
                            coords.append((float(vals[0]), float(vals[1])))
                        except ValueError:
                            pass
            if coords:
                return coords
    return []


def _gml_to_tracks(xml_text: str) -> list[Track]:
    """Parse a WFS GML response into Track objects."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    tracks: list[Track] = []
    idx = 0

    for child in root.iter():
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local not in ("member", "featureMember"):
            continue
        for feature in child:
            raw = _extract_geom(feature)
            if len(raw) < 2:
                continue

            idx += 1
            # Detect axis order — if values look like (lon, lat) swap them.
            # Czech lat range 48–52; Czech lon range 12–19.
            # If first coord has a<10 and b>10 it's probably (lon, lat).
            a0, b0 = raw[0]
            if not (48.0 <= a0 <= 52.5) and (48.0 <= b0 <= 52.5):
                nodes = [(b, a) for a, b in raw]   # swap to (lat, lon)
            else:
                nodes = raw

            if len(nodes) < 2:
                continue

            km = _track_length_km(nodes)
            tracks.append(Track(
                way_ids=[idx],
                nodes=nodes,
                tags={"railway": "rail", "source": "ČÚZK WFS"},
                name=f"ČÚZK Segment {idx}  ({km:.2f} km)",
            ))

    return tracks


# ---------------------------------------------------------------------------
# GeoJSON parser
# ---------------------------------------------------------------------------

def _geojson_to_tracks(data: dict) -> list[Track]:
    """Convert a GeoJSON FeatureCollection to Track objects."""
    tracks: list[Track] = []
    features = data.get("features", [])
    if not features and "geometry" in data:
        # single feature
        features = [data]

    for i, feat in enumerate(features):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type", "")

        if gtype == "LineString":
            raw = geom.get("coordinates", [])
            # GeoJSON: [lon, lat] or [lon, lat, elev]
            coords = [(float(c[1]), float(c[0])) for c in raw if len(c) >= 2]
        elif gtype == "MultiLineString":
            segs = geom.get("coordinates", [])
            if not segs:
                continue
            # Take the longest segment
            raw = max(segs, key=len)
            coords = [(float(c[1]), float(c[0])) for c in raw if len(c) >= 2]
        else:
            continue

        if len(coords) < 2:
            continue

        km = _track_length_km(coords)
        tracks.append(Track(
            way_ids=[i + 1],
            nodes=coords,
            tags={"railway": "rail", "source": "ČÚZK WFS"},
            name=f"ČÚZK Segment {i + 1}  ({km:.2f} km)",
        ))

    return tracks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_railway_links(
    south: float,
    west:  float,
    north: float,
    east:  float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[Track]:
    """
    Fetch INSPIRE RailwayLink features from the ČÚZK WFS within the given
    WGS84 bounding box.

    Tries JSON then GML output formats, and both lon-lat and lat-lon BBOX
    axis orders, to cope with server-side variation.

    Returns a list of Track objects (possibly empty if the area has no data).
    Raises requests.RequestException on complete network failure.
    """
    def _p(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    # Four attempts: JSON vs GML × two common BBOX axis orders
    attempts = [
        ("application/json", f"{west},{south},{east},{north},EPSG:4326"),
        ("application/json", f"{south},{west},{north},{east},EPSG:4326"),
        ("GML32",            f"{west},{south},{east},{north},EPSG:4326"),
        ("GML32",            f"{south},{west},{north},{east},EPSG:4326"),
    ]

    last_exc: Optional[Exception] = None

    for k, (out_fmt, bbox) in enumerate(attempts):
        fmt_label = "JSON" if "json" in out_fmt.lower() else "GML"
        _p(f"ČÚZK WFS: querying ({fmt_label}, attempt {k + 1}/{len(attempts)})…")

        params = {
            "SERVICE":      "WFS",
            "VERSION":      "2.0.0",
            "REQUEST":      "GetFeature",
            "TYPENAMES":    "tn-ra:RailwayLink",
            "OUTPUTFORMAT": out_fmt,
            "BBOX":         bbox,
            "COUNT":        "5000",
        }

        try:
            resp = requests.get(CZUK_WFS_URL, params=params, timeout=TIMEOUT)
            if not resp.ok:
                _p(f"  HTTP {resp.status_code} — retrying…")
                last_exc = requests.exceptions.HTTPError(
                    f"HTTP {resp.status_code}", response=resp
                )
                continue

            ct = resp.headers.get("Content-Type", "")
            tracks: list[Track] = []

            if "json" in ct or fmt_label == "JSON":
                try:
                    data = resp.json()
                    tracks = _geojson_to_tracks(data)
                except Exception:
                    pass   # fall through to GML attempt

            if not tracks:
                tracks = _gml_to_tracks(resp.text)

            if tracks:
                _p(f"ČÚZK WFS: {len(tracks)} railway segments loaded.")
                return tracks

            # Got a valid response but zero features — not an error, just empty area
            _p("ČÚZK WFS: query succeeded but no railway links in this area.")
            return []

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            _p(f"  Connection error: {exc}")
            continue

    # All attempts exhausted
    _p("ČÚZK WFS: all attempts failed.")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("ČÚZK WFS: could not retrieve data.")
