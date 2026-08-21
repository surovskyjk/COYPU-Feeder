"""
Elevation querying via ČÚZK DMR 5G (Czech national lidar terrain model,
~0.2 m accuracy) with OpenTopoData (Copernicus GLO-30, 30 m resolution) as
fallback / alternate source. Samples elevations along an alignment and fits
a vertical geometry profile.
"""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import numpy as np
import requests

from osm.query import HTTP_HEADERS

# eudem25m = EU-DEM 25m (best for Europe); srtm30m = global fallback
OPENTOPODATA_DATASETS = ["eudem25m", "srtm30m", "aster30m"]
OPENTOPODATA_BASE = "https://api.opentopodata.org/v1/{dataset}"
BATCH_SIZE = 100  # API limit per request
REQUEST_TIMEOUT = 30

# ČÚZK DMR 5G (digital terrain model, 5th generation) — ~2 m grid, ~0.18 m
# accuracy in open terrain / ~0.3 m in forest. Public "identify" operation
# only (no auth); the batch "getSamples" operation is blocked by ČÚZK's
# firewall regardless of payload, so this is queried one point at a time.
# CUZK_CONCURRENCY is deliberately modest — ČÚZK's network-services terms
# reserve the right to block clients that overload its infrastructure, and
# it already hard-blocks getSamples outright, so per-point fetching must
# stay polite rather than maximally parallel.
CUZK_DMR5G_URL = "https://ags.cuzk.gov.cz/arcgis2/rest/services/dmr5g/ImageServer/identify"
CUZK_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# DEM querying
# ---------------------------------------------------------------------------

def sample_elevations(
    coords_latlon: list[tuple[float, float]],
    source: str = "cuzk_dmr5g",
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[Optional[float]]:
    """
    Query elevations at each (lat, lon), in the same order; None where
    unavailable.

    source="cuzk_dmr5g" (default): ČÚZK DMR 5G per-point lookups. Any point
        with no data (outside CZ, or a failed request) is transparently
        backfilled from OpenTopoData so the profile stays continuous near
        the border, without reverting the user's explicit choice for the
        rest of the line.
    source="opentopodata": OpenTopoData only — ČÚZK is never queried.
    """
    if source == "cuzk_dmr5g":
        elevations = _sample_cuzk_dmr5g(coords_latlon, progress_cb=progress_cb)
        missing_idx = [i for i, e in enumerate(elevations) if e is None]
        if missing_idx:
            fallback = _sample_opentopodata([coords_latlon[i] for i in missing_idx])
            for idx, val in zip(missing_idx, fallback):
                elevations[idx] = val
        return elevations
    return _sample_opentopodata(coords_latlon, progress_cb=progress_cb)


def _sample_cuzk_dmr5g(
    coords_latlon: list[tuple[float, float]],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[Optional[float]]:
    """Query ČÚZK DMR 5G's identify endpoint, one point per request,
    with bounded concurrency. Returns None per point on NoData/failure."""
    n = len(coords_latlon)
    results: list[Optional[float]] = [None] * n
    if n == 0:
        return results

    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    done = 0
    lock = threading.Lock()

    def fetch(i: int, lat: float, lon: float) -> tuple[int, Optional[float]]:
        return i, _identify_one(session, lat, lon)

    with ThreadPoolExecutor(max_workers=CUZK_CONCURRENCY) as ex:
        futures = [ex.submit(fetch, i, lat, lon)
                   for i, (lat, lon) in enumerate(coords_latlon)]
        for fut in as_completed(futures):
            i, val = fut.result()
            results[i] = val
            with lock:
                done += 1
                if progress_cb:
                    progress_cb(done, n)
    return results


def _identify_one(
    session: requests.Session, lat: float, lon: float, retry: bool = True,
) -> Optional[float]:
    params = {
        "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
        "geometryType": "esriGeometryPoint",
        "f": "json",
    }
    try:
        resp = session.get(CUZK_DMR5G_URL, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429 and retry:
            time.sleep(2)
            return _identify_one(session, lat, lon, retry=False)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return None
        value = data.get("value")
        if value is None or value == "NoData":
            return None
        return float(value)
    except Exception:
        return None


def _sample_opentopodata(
    coords_latlon: list[tuple[float, float]],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[Optional[float]]:
    """
    Query OpenTopoData for elevations at each (lat, lon).
    Tries EU-DEM first, falls back to SRTM30m and ASTER30m.
    Returns a list of elevations (m) in the same order; None where unavailable.
    """
    elevations = []
    n = len(coords_latlon)
    for batch_start in range(0, n, BATCH_SIZE):
        batch = coords_latlon[batch_start:batch_start + BATCH_SIZE]
        locations = "|".join(f"{lat},{lon}" for lat, lon in batch)
        batch_elevs = _query_batch(locations, len(batch))
        elevations.extend(batch_elevs)
        if progress_cb:
            progress_cb(len(elevations), n)
    return elevations


def _query_batch(locations: str, n: int) -> list[Optional[float]]:
    """Try each dataset in order; return first successful non-null result set."""
    for dataset in OPENTOPODATA_DATASETS:
        url = OPENTOPODATA_BASE.format(dataset=dataset)
        try:
            resp = requests.get(url, params={"locations": locations}, timeout=REQUEST_TIMEOUT, headers=HTTP_HEADERS)
            if resp.status_code == 429:
                time.sleep(2)
                resp = requests.get(url, params={"locations": locations}, timeout=REQUEST_TIMEOUT, headers=HTTP_HEADERS)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            elevs = [r.get("elevation") for r in results]
            if any(e is not None for e in elevs):
                return elevs
        except Exception:
            continue
    return [None] * n


def interpolate_along_alignment(
    coords_latlon: list[tuple[float, float]],
    chainages: np.ndarray,
    sample_interval: float = 20.0,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """
    Resample alignment at regular chainage intervals for DEM querying.
    Returns (sample_chainages, sample_latlon_coords).
    """
    total_length = float(chainages[-1])
    n_samples = max(2, int(total_length / sample_interval) + 1)
    target_chainages = np.linspace(0, total_length, n_samples)

    sample_coords = []
    for s in target_chainages:
        idx = np.searchsorted(chainages, s, side="right") - 1
        idx = int(np.clip(idx, 0, len(chainages) - 2))
        t = (s - chainages[idx]) / max(chainages[idx + 1] - chainages[idx], 1e-9)
        lat = coords_latlon[idx][0] + t * (coords_latlon[idx + 1][0] - coords_latlon[idx][0])
        lon = coords_latlon[idx][1] + t * (coords_latlon[idx + 1][1] - coords_latlon[idx][1])
        sample_coords.append((lat, lon))

    return target_chainages, sample_coords


# ---------------------------------------------------------------------------
# Vertical geometry fitting
# ---------------------------------------------------------------------------

def fit_vertical_geometry(
    chainages: np.ndarray,
    elevations: list[Optional[float]],
    min_grade_length: float = 50.0,
    vc_length: float = 100.0,
) -> list[dict]:
    """
    Fit a piecewise-linear grade model to the elevation profile, then insert
    parabolic vertical curves at each grade break.

    Returns a list of vertical element dicts:
      {"type": "PVI", "station": ..., "elevation": ...}
      {"type": "ParaCurve", "station": ..., "length": ..., "elevation": ...}
    """
    # Filter out None elevations
    valid_mask = np.array([e is not None for e in elevations])
    if valid_mask.sum() < 2:
        return []

    s = chainages[valid_mask]
    z = np.array([e for e in elevations if e is not None], dtype=float)

    # Smooth elevations to reduce DEM noise
    z_smooth = _smooth_elevations(s, z)

    # Detect grade break points via second-derivative thresholding
    pvs = _detect_grade_breaks(s, z_smooth, min_grade_length)

    # Build PVI list: start, breaks, end
    pvi_list = []
    for station, elevation in pvs:
        pvi_list.append({"type": "PVI", "station": float(station), "elevation": float(elevation)})

    # Insert parabolic vertical curves at interior PVIs
    elements = []
    for i, pvi in enumerate(pvi_list):
        elements.append(pvi)
        if 0 < i < len(pvi_list) - 1:
            # Insert ParaCurve centred on this PVI
            elements.append({
                "type": "ParaCurve",
                "station": float(pvi["station"]),
                "length": float(vc_length),
                "elevation": float(pvi["elevation"]),
            })

    return elements


def _smooth_elevations(s: np.ndarray, z: np.ndarray, window: int = 5) -> np.ndarray:
    """Simple moving-average smoothing."""
    if len(z) < window:
        return z
    kernel = np.ones(window) / window
    z_pad = np.pad(z, window // 2, mode="edge")
    return np.convolve(z_pad, kernel, mode="valid")[: len(z)]


def _detect_grade_breaks(
    s: np.ndarray,
    z: np.ndarray,
    min_length: float,
) -> list[tuple[float, float]]:
    """
    Detect significant grade changes using a sliding window gradient comparison.
    Returns a list of (station, elevation) PVI points including endpoints.
    """
    if len(s) < 3:
        return [(s[0], z[0]), (s[-1], z[-1])]

    grades = np.diff(z) / np.maximum(np.diff(s), 1e-9)
    grade_changes = np.abs(np.diff(grades))

    # Threshold: significant grade change > 0.5%
    threshold = 0.005
    break_indices = np.where(grade_changes > threshold)[0] + 1

    # Ensure minimum spacing between breaks
    filtered = [0]
    for idx in break_indices:
        if s[idx] - s[filtered[-1]] >= min_length:
            filtered.append(int(idx))
    if filtered[-1] != len(s) - 1:
        filtered.append(len(s) - 1)

    return [(float(s[i]), float(z[i])) for i in filtered]
