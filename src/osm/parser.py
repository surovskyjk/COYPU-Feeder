"""
Parse raw Overpass JSON into ordered node sequences (tracks).
Each 'track' is a list of (lat, lon) tuples forming a continuous polyline.
Ways are chained by shared endpoints.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    way_ids: list[int] = field(default_factory=list)
    nodes: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon)
    tags: dict = field(default_factory=dict)
    name: str = ""


def _build_node_index(elements: list[dict]) -> dict[int, tuple[float, float]]:
    return {
        el["id"]: (el["lat"], el["lon"])
        for el in elements
        if el["type"] == "node"
    }


def _extract_ways(elements: list[dict]) -> list[dict]:
    return [el for el in elements if el["type"] == "way"]


def _chain_ways(ways: list[dict], node_index: dict) -> list[list[int]]:
    """
    Chain ways into continuous sequences by matching endpoints.
    Returns a list of node-ID chains.
    """
    # Build adjacency: endpoint node_id → list of way indices
    endpoint_map: dict[int, list[int]] = {}
    for i, way in enumerate(ways):
        nodes = way.get("nodes", [])
        if len(nodes) < 2:
            continue
        for endpoint in (nodes[0], nodes[-1]):
            endpoint_map.setdefault(endpoint, []).append(i)

    used = set()
    chains = []

    for start_idx in range(len(ways)):
        if start_idx in used:
            continue
        way = ways[start_idx]
        nodes = way.get("nodes", [])
        if len(nodes) < 2:
            used.add(start_idx)
            continue

        chain = list(nodes)
        used.add(start_idx)

        # Extend forward
        while True:
            tail = chain[-1]
            candidates = [i for i in endpoint_map.get(tail, []) if i not in used]
            if not candidates:
                break
            next_idx = candidates[0]
            next_nodes = ways[next_idx].get("nodes", [])
            if next_nodes[0] == tail:
                chain.extend(next_nodes[1:])
            else:
                chain.extend(reversed(next_nodes[:-1]))
            used.add(next_idx)

        # Extend backward
        while True:
            head = chain[0]
            candidates = [i for i in endpoint_map.get(head, []) if i not in used]
            if not candidates:
                break
            next_idx = candidates[0]
            next_nodes = ways[next_idx].get("nodes", [])
            if next_nodes[-1] == head:
                chain = next_nodes + chain[1:]
            else:
                chain = list(reversed(next_nodes)) + chain[1:]
            used.add(next_idx)

        chains.append(chain)

    return chains


def _group_by_track(ways: list[dict]) -> list[list[dict]]:
    """
    Group ways by track number (OSM tag 'railway:track' or positional proximity).
    Falls back to treating all ways as one group if no track tags exist.
    """
    track_map: dict[str, list[dict]] = {}
    for way in ways:
        tags = way.get("tags", {})
        track_key = tags.get("railway:track", tags.get("track", "1"))
        track_map.setdefault(track_key, []).append(way)

    # If only one implicit group, don't split further
    if len(track_map) == 1:
        return list(track_map.values())

    return list(track_map.values())


def parse_tracks(overpass_data: dict) -> list[Track]:
    """
    Parse Overpass JSON into a list of Track objects.
    Multiple parallel tracks produce separate Track entries.
    """
    elements = overpass_data.get("elements", [])
    node_index = _build_node_index(elements)
    ways = _extract_ways(elements)

    if not ways:
        return []

    way_groups = _group_by_track(ways)
    tracks = []

    for group_idx, group in enumerate(way_groups):
        chains = _chain_ways(group, node_index)
        for chain_idx, node_ids in enumerate(chains):
            coords = []
            for nid in node_ids:
                if nid in node_index:
                    coords.append(node_index[nid])

            if len(coords) < 2:
                continue

            # Collect representative tags from the first way in group
            tags = group[0].get("tags", {}) if group else {}
            track_num = tags.get("railway:track", tags.get("track", str(group_idx + 1)))
            name = f"Track {track_num}" if len(way_groups) > 1 else "Track 1"
            if len(chains) > 1:
                name += f" (segment {chain_idx + 1})"

            track = Track(
                way_ids=[w["id"] for w in group],
                nodes=coords,
                tags=tags,
                name=name,
            )
            tracks.append(track)

    return tracks


def get_track_names(tracks: list[Track]) -> list[str]:
    return [t.name for t in tracks]


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in metres."""
    import math
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2.0 * 6371000.0 * math.asin(min(1.0, math.sqrt(h)))


def chain_polylines(tracks: list[Track], gap_tol_m: float = 50.0) -> Track | None:
    """
    Chain 2+ tracks end-to-end into one continuous track, matching whichever
    endpoints are geographically closest (reversing tracks as needed).
    Order-independent — greedily extends from the first track, always
    attaching whichever remaining track has the nearest endpoint to either
    end of the growing chain.

    Returns the merged Track, or None if some track couldn't be attached
    within gap_tol_m (the caller can report the shortfall by re-checking
    endpoint distances itself).
    """
    if len(tracks) < 2 or any(not t.nodes for t in tracks):
        return None
    remaining = list(tracks[1:])
    chain = list(tracks[0].nodes)
    way_ids = list(tracks[0].way_ids)
    while remaining:
        head, tail = chain[0], chain[-1]
        best = None   # (dist, idx, attach_to, reverse)
        for i, t in enumerate(remaining):
            for attach_to, target in (("tail", tail), ("head", head)):
                for reverse in (False, True):
                    pt = t.nodes[-1] if reverse else t.nodes[0]
                    d = _haversine_m(target, pt)
                    if best is None or d < best[0]:
                        best = (d, i, attach_to, reverse)
        if best is None or best[0] > gap_tol_m:
            return None
        _, i, attach_to, reverse = best
        t = remaining.pop(i)
        nodes = list(reversed(t.nodes)) if reverse else list(t.nodes)
        if attach_to == "tail":
            chain.extend(nodes)
        else:
            chain = nodes + chain
        way_ids.extend(t.way_ids)
    names = " + ".join(t.name for t in tracks if t.name)
    return Track(way_ids=way_ids, nodes=chain, tags={},
                name=names or "merged track")


def split_track(track: Track, at_index: int) -> tuple[Track, Track] | None:
    """
    Split `track` into two, sharing the boundary node at `at_index` so
    there is no gap. None if the split would leave either half with fewer
    than 2 nodes.
    """
    n = len(track.nodes)
    if not (1 <= at_index <= n - 2):
        return None
    first = Track(way_ids=list(track.way_ids), nodes=track.nodes[:at_index + 1],
                 tags=dict(track.tags), name=f"{track.name} (1/2)")
    second = Track(way_ids=list(track.way_ids), nodes=track.nodes[at_index:],
                  tags=dict(track.tags), name=f"{track.name} (2/2)")
    return first, second


def nearest_track_node_index(track: Track, latlon: tuple[float, float]) -> int:
    """Index of the track node closest to `latlon` (haversine)."""
    return min(range(len(track.nodes)),
              key=lambda i: _haversine_m(track.nodes[i], latlon))
