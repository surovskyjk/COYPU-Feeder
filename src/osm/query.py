"""
Overpass API queries for railway data.
Supports search by name, relation ID, and bounding box.

Fallback strategy:
  1. Disk cache (1 h for search results, 24 h for full relation data)
  2. Up to 5 public Overpass mirror endpoints, 15 s timeout each
  3. For fetch_relation_ways only: official OSM API v0.6 direct fallback
"""

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional

import requests

# ---------------------------------------------------------------------------
# Endpoints & timeouts
# ---------------------------------------------------------------------------

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.maptime.io/api/interpreter",
]

# Per-endpoint timeout.  Short so failures are discovered quickly.
# 5 endpoints × 15 s = 75 s worst case before giving up on Overpass.
TIMEOUT = 15

OSM_API_BASE = "https://api.openstreetmap.org/api/0.6"
OSM_API_TIMEOUT = 45   # single endpoint, generous

# Identifying User-Agent — required by the OSM/Overpass usage policy.
# overpass-api.de rejects the default python-requests agent with HTTP 406.
HTTP_HEADERS = {
    "User-Agent": "COYPU-Feeder/1.0 (+https://github.com/surovskyjk/COYPU-Feeder)"
}

# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

_CACHE_DIR = (
    Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    / "coypu_osm_cache"
)
_CACHE_TTL_SEARCH   = 3_600    # 1 hour for search / member-list results
_CACHE_TTL_RELATION = 86_400   # 24 hours for full relation way/node data


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.json"


def _cache_get(key: str, ttl: int) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > ttl:
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(key: str, data: dict) -> None:
    try:
        p = _cache_path(key)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _query_key(query: str) -> str:
    return hashlib.md5(query.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core Overpass runner
# ---------------------------------------------------------------------------

def _run_query(
    query: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    ttl: int = _CACHE_TTL_SEARCH,
) -> dict:
    """
    Run an Overpass QL query, trying each endpoint in turn.

    Results are cached to disk for *ttl* seconds.
    *progress_cb*, if given, is called with a short status string each time
    the endpoint changes (safe to call from any thread).
    """
    key = _query_key(query)
    cached = _cache_get(key, ttl)
    if cached is not None:
        if progress_cb:
            progress_cb("Loaded from cache.")
        return cached

    last_exc: Optional[Exception] = None
    n = len(OVERPASS_ENDPOINTS)
    for i, url in enumerate(OVERPASS_ENDPOINTS):
        if progress_cb:
            progress_cb(f"Trying Overpass server {i + 1}/{n}…")
        try:
            response = requests.post(
                url, data={"data": query}, timeout=TIMEOUT,
                headers=HTTP_HEADERS,
            )
            code = response.status_code
            if code == 429:
                # Rate-limited — short sleep then move to next mirror
                if progress_cb:
                    progress_cb(f"Server {i + 1}/{n} rate-limited, skipping…")
                time.sleep(2)
                last_exc = requests.exceptions.HTTPError(
                    f"429 Rate Limited", response=response
                )
                continue
            if not response.ok:
                # Any non-2xx from this mirror (400, 406, 500, 503, 504 …)
                # is treated as a mirror-specific failure.  Try the next one.
                if progress_cb:
                    progress_cb(
                        f"Server {i + 1}/{n} returned HTTP {code}, trying next…"
                    )
                last_exc = requests.exceptions.HTTPError(
                    f"HTTP {code}", response=response
                )
                if code in (503, 504):
                    time.sleep(2)
                continue
            data = response.json()
            _cache_put(key, data)
            return data
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if progress_cb:
                progress_cb(f"Server {i + 1}/{n} unreachable, trying next…")
            continue

    if last_exc is None:
        raise RuntimeError("All Overpass endpoints failed.")
    raise last_exc


# ---------------------------------------------------------------------------
# OSM API v0.6 XML fallback helpers
# ---------------------------------------------------------------------------

def _parse_osm_xml(xml_text: str) -> dict:
    """
    Convert an OSM API v0.6 XML response to an Overpass-compatible dict.

    Only nodes and ways are extracted (relations are ignored); this matches
    what ``fetch_relation_ways`` needs downstream.
    """
    root = ET.fromstring(xml_text)
    elements: list[dict] = []

    for node in root.findall("node"):
        lat = node.get("lat")
        lon = node.get("lon")
        if lat is None or lon is None:
            continue
        elements.append({
            "type": "node",
            "id":   int(node.get("id")),
            "lat":  float(lat),
            "lon":  float(lon),
            "tags": {tag.get("k"): tag.get("v") for tag in node.findall("tag")},
        })

    for way in root.findall("way"):
        nodes = [int(nd.get("ref")) for nd in way.findall("nd")]
        elements.append({
            "type":  "way",
            "id":    int(way.get("id")),
            "nodes": nodes,
            "tags":  {tag.get("k"): tag.get("v") for tag in way.findall("tag")},
        })

    return {"elements": elements}


def _fetch_relation_via_osm_api(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch relation ways/nodes directly from the official OSM API v0.6.
    Returns an Overpass-compatible dict.
    """
    url = f"{OSM_API_BASE}/relation/{relation_id}/full"
    if progress_cb:
        progress_cb("Trying OSM API directly…")
    response = requests.get(url, timeout=OSM_API_TIMEOUT, headers=HTTP_HEADERS)
    response.raise_for_status()
    data = _parse_osm_xml(response.text)
    # Cache under the same key as the Overpass query would use
    query_equiv = f"[out:json];relation({relation_id});way(r);(._; >;);out body;"
    _cache_put(_query_key(query_equiv), data)
    return data


def _fetch_relation_members_via_osm_api(
    parent_relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch member relations of a parent relation via the official OSM API v0.6.

    Two-step process:
      1. GET /relation/{id}        → extract member relation IDs
      2. GET /relations?relations=… → batch-fetch tags for those members

    Returns a list of dicts in the same shape as fetch_relation_members().
    """
    def _p(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    # Step 1 — get the list of member IDs
    _p("OSM API: fetching parent relation…")
    url = f"{OSM_API_BASE}/relation/{parent_relation_id}"
    resp = requests.get(url, timeout=OSM_API_TIMEOUT, headers=HTTP_HEADERS)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    member_ids: list[int] = []
    for rel_el in root.findall("relation"):
        for m in rel_el.findall("member"):
            if m.get("type") == "relation":
                try:
                    member_ids.append(int(m.get("ref")))
                except (TypeError, ValueError):
                    pass

    if not member_ids:
        return []

    # Step 2 — batch-fetch tags (max 500 IDs per request)
    BATCH = 500
    results: list[dict] = []
    for i in range(0, len(member_ids), BATCH):
        batch = member_ids[i: i + BATCH]
        ids_str = ",".join(str(x) for x in batch)
        _p(f"OSM API: fetching tags for {len(batch)} relations…")
        url2 = f"{OSM_API_BASE}/relations?relations={ids_str}"
        resp2 = requests.get(url2, timeout=OSM_API_TIMEOUT, headers=HTTP_HEADERS)
        resp2.raise_for_status()
        root2 = ET.fromstring(resp2.text)
        for rel_el in root2.findall("relation"):
            rid  = int(rel_el.get("id"))
            tags = {t.get("k"): t.get("v") for t in rel_el.findall("tag")}
            results.append({
                "id":       rid,
                "name":     tags.get("name", f"Relation {rid}"),
                "ref":      tags.get("ref", ""),
                "network":  tags.get("network", ""),
                "operator": tags.get("operator", ""),
                "from":     tags.get("from", ""),
                "to":       tags.get("to", ""),
            })

    # Sort: numeric ref first, then alphabetical name
    def _sort_key(r: dict):
        try:
            return (0, int(r.get("ref", "")), r["name"])
        except (ValueError, TypeError):
            return (1, 0, r["name"])

    results.sort(key=_sort_key)
    return results


def _fetch_relation_metadata_via_osm_api(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """
    Fetch tags for a single relation via the official OSM API v0.6.
    Returns a dict in the same shape as fetch_relation_metadata(), or None.
    """
    if progress_cb:
        progress_cb("OSM API: fetching relation metadata…")
    url  = f"{OSM_API_BASE}/relation/{relation_id}"
    resp = requests.get(url, timeout=OSM_API_TIMEOUT, headers=HTTP_HEADERS)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    for rel_el in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in rel_el.findall("tag")}
        return {
            "id":       relation_id,
            "name":     tags.get("name", f"Relation {relation_id}"),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        }
    return None


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def search_railways_by_name(
    name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations matching a name.
    Returns a list of dicts with keys: id, name, network, operator, from, to.
    """
    query = f"""
[out:json][timeout:30];
relation["type"="route"]["route"="railway"]["name"~"{name}",i];
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def fetch_relation_ways(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch all ways and nodes belonging to a railway route relation.
    Returns raw Overpass-compatible JSON with nodes and ways.

    Falls back to the official OSM API v0.6 if all Overpass mirrors fail.
    """
    query = f"""
[out:json][timeout:{TIMEOUT}];
relation({relation_id});
way(r);
(._; >;);
out body;
"""
    try:
        return _run_query(
            query,
            progress_cb=progress_cb,
            ttl=_CACHE_TTL_RELATION,
        )
    except Exception as overpass_exc:
        # Overpass completely unavailable — try the official OSM API
        try:
            return _fetch_relation_via_osm_api(relation_id, progress_cb=progress_cb)
        except Exception:
            # Re-raise original Overpass error (it contains more context)
            raise overpass_exc


def search_relations_in_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations whose geometry intersects a bounding box.
    Returns relation metadata (id, name, ref, from, to, network).
    """
    query = f"""
[out:json][timeout:30];
relation["type"="route"]["route"="railway"]({south},{west},{north},{east});
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def search_by_ref(
    ref: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations by timetable/line reference number.
    Matches the OSM 'ref' tag exactly (case-insensitive).
    Returns a list of relation metadata dicts.
    """
    safe_ref = ref.strip().replace(".", r"\.").replace("+", r"\+")
    query = f"""
[out:json][timeout:30];
relation["type"="route"]["route"="railway"]["ref"~"^{safe_ref}$",i];
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def search_by_number_in_name(
    number: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations whose name contains the given number,
    not adjacent to other digits.  Finds lines like "212 - Čerčany – Světlá…"
    even when the OSM 'ref' tag is missing.

    Uses a union of two Overpass queries because POSIX ERE (used by Overpass)
    does not treat '^' as a start-of-string anchor inside alternation groups.
    Query 1 catches the number at the very start of the name.
    Query 2 catches the number preceded by a non-digit character.
    """
    safe = number.strip().replace(".", r"\.").replace("+", r"\+")
    query = f"""
[out:json][timeout:30];
(
  relation["type"="route"]["route"="railway"]["name"~"^{safe}([^0-9]|$)"];
  relation["type"="route"]["route"="railway"]["name"~"[^0-9]{safe}([^0-9]|$)"];
);
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb)
    seen: set[int] = set()
    results = []
    for el in data.get("elements", []):
        if el["id"] in seen:
            continue
        seen.add(el["id"])
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def fetch_bbox_ways(
    south: float,
    west: float,
    north: float,
    east: float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch all railway ways within a bounding box.
    Returns raw Overpass JSON with nodes and ways.
    """
    query = f"""
[out:json][timeout:{TIMEOUT}];
(
  way["railway"="rail"]({south},{west},{north},{east});
  way["railway"="light_rail"]({south},{west},{north},{east});
  way["railway"="subway"]({south},{west},{north},{east});
);
(._; >;);
out body;
"""
    return _run_query(query, progress_cb=progress_cb)


def fetch_relation_members(
    parent_relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch the tags of all sub-relation members of a parent relation.
    Used to list all railway lines that belong to a national/regional collection
    (e.g. 'Railways in Czech Republic', OSM relation 2332889).

    Returns a list of dicts with the same shape as search_by_ref().
    Members that are ways or nodes (not relations) are silently skipped.
    Results are sorted by ref tag (numeric where possible), then by name.

    Falls back to the official OSM API v0.6 if all Overpass mirrors fail.
    """
    query = f"""
[out:json][timeout:{TIMEOUT}];
relation({parent_relation_id});
rel(r);
out tags;
"""
    try:
        data = _run_query(query, progress_cb=progress_cb, ttl=_CACHE_TTL_SEARCH)
    except Exception as overpass_exc:
        try:
            return _fetch_relation_members_via_osm_api(
                parent_relation_id, progress_cb=progress_cb
            )
        except Exception:
            raise overpass_exc

    results = []
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })

    # Sort: numeric ref first, then alpha name
    def _sort_key(r):
        ref = r.get("ref", "")
        try:
            return (0, int(ref), r["name"])
        except (ValueError, TypeError):
            return (1, 0, r["name"])

    results.sort(key=_sort_key)
    return results


def fetch_relation_metadata(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """
    Fetch tags for a single relation.
    Falls back to the official OSM API v0.6 if all Overpass mirrors fail.
    """
    query = f"""
[out:json][timeout:15];
relation({relation_id});
out tags;
"""
    try:
        data = _run_query(query, progress_cb=progress_cb)
    except Exception as overpass_exc:
        try:
            return _fetch_relation_metadata_via_osm_api(
                relation_id, progress_cb=progress_cb
            )
        except Exception:
            raise overpass_exc

    elements = data.get("elements", [])
    if not elements:
        return None
    tags = elements[0].get("tags", {})
    return {
        "id":       relation_id,
        "name":     tags.get("name", f"Relation {relation_id}"),
        "network":  tags.get("network", ""),
        "operator": tags.get("operator", ""),
        "from":     tags.get("from", ""),
        "to":       tags.get("to", ""),
    }


# ---------------------------------------------------------------------------
# Station / stop detection near a track
# ---------------------------------------------------------------------------

def fetch_stations_in_bbox(
    south: float, west: float, north: float, east: float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch passenger stations, halts and train stop positions inside a
    bounding box.

    Returns a list of dicts: {"name": str, "lat": float, "lon": float,
    "kind": "station"|"halt"|"stop_position"}.

    Freight-only and non-passenger facilities (yards, service stations,
    junctions, crossovers, sites with passenger=no) are excluded.
    """
    bbox = f"({south},{west},{north},{east})"
    query = f"""
[out:json][timeout:60];
(
  node["railway"="station"]{bbox};
  node["railway"="halt"]{bbox};
  node["public_transport"="stop_position"]["train"="yes"]{bbox};
);
out body;
"""
    data = _run_query(query, progress_cb=progress_cb)
    results: list[dict] = []
    seen_ids: set = set()
    for el in data.get("elements", []):
        if el.get("type") != "node" or el.get("id") in seen_ids:
            continue
        seen_ids.add(el.get("id"))
        tags = el.get("tags", {})
        # Passenger-relevant only
        if tags.get("passenger") == "no":
            continue
        if tags.get("railway") in ("yard", "service_station", "junction",
                                   "crossover", "site"):
            continue
        if tags.get("usage") in ("industrial", "military", "freight"):
            continue
        kind = tags.get("railway") or "stop_position"
        results.append({
            "name": tags.get("name", ""),
            "lat":  float(el.get("lat", 0.0)),
            "lon":  float(el.get("lon", 0.0)),
            "kind": kind,
        })
    return results
