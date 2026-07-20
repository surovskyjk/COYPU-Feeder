"""
COYPU Feeder project files (``.coypu``).

Self-contained XML so a session can be reopened and edited later with no
network access, no re-fetching and no re-fitting.

The layout deliberately mirrors the LandXML house style used by
``landxml/builder.py`` — same 6-decimal formatting, and bulk coordinates in
``PntList2D``-style whitespace text blocks — so the file maps 1:1 onto the
in-memory dataclasses (``PIAlignment`` / ``PIData`` / ``Track`` /
``stationing.Station``) with no data transformation.

Elements are NOT stored: they are rebuilt from the PI model on load
(``rebuild_from_pi_model``), which reproduces them exactly.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
from lxml import etree

import app_meta as meta

FILE_VERSION = "1.1"   # 1.1 adds PI/@chainNext (high-angle chained merges)
NUM = "{:.6f}"          # LandXML house style — user-facing values
# Internal geometry (projected metres) is stored at nanometre precision so a
# save/load round-trip reproduces the element chain exactly rather than to the
# ~1 µm that 6 decimals would allow.
NUM_GEOM = "{:.9f}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pnt_list(coords, fmt: str = NUM_GEOM) -> str:
    """Flatten [(a, b), ...] into 'a b a b …' (LandXML PntList2D style)."""
    out = []
    for a, b in coords:
        out.append(fmt.format(float(a)))
        out.append(fmt.format(float(b)))
    return " ".join(out)


def _parse_pnt_list(text: str | None) -> list[tuple[float, float]]:
    if not text or not text.strip():
        return []
    v = [float(x) for x in text.split()]
    return list(zip(v[0::2], v[1::2]))


def _b(value: bool) -> str:
    return "true" if value else "false"


def _pb(text: str | None) -> bool:
    return str(text).lower() == "true"


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_project(filepath: str, state: dict) -> None:
    """
    Write a `.coypu` project.

    `state` keys (all optional except work_epsg):
      project_name, work_epsg, level, step, settings, tracks,
      selected_indices, pi_model, stations
    """
    root = etree.Element("CoypuProject")
    root.set("version", FILE_VERSION)
    root.set("appVersion", meta.APP_VERSION)
    root.set("created", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    proj = etree.SubElement(root, "Project")
    proj.set("name", str(state.get("project_name", "Railway Alignment")))
    proj.set("workEpsg", str(int(state.get("work_epsg", 32633))))
    proj.set("level", str(state.get("level", "")))
    proj.set("step", str(int(state.get("step", 0))))

    # ── Settings (Step 3 dict; scalars only) ─────────────────────────────
    st = etree.SubElement(root, "Settings")
    for k, v in (state.get("settings") or {}).items():
        if isinstance(v, bool):
            st.set(k, _b(v))
        elif isinstance(v, (int, float)):
            st.set(k, NUM.format(float(v)) if isinstance(v, float) else str(v))
        elif isinstance(v, str):
            st.set(k, v)

    # ── Tracks (raw OSM nodes → self-contained, no re-download) ──────────
    tracks = state.get("tracks") or []
    sel    = state.get("selected_indices") or []
    tr_el  = etree.SubElement(root, "Tracks")
    tr_el.set("selected", " ".join(str(int(i)) for i in sel))
    for t in tracks:
        e = etree.SubElement(tr_el, "Track")
        e.set("name", getattr(t, "name", "") or "")
        way_ids = getattr(t, "way_ids", None) or []
        if way_ids:
            e.set("wayIds", " ".join(str(int(w)) for w in way_ids))
        pl = etree.SubElement(e, "PntList2D")
        # lat/lon need more than 6 decimals: 1e-6° ≈ 0.11 m
        pl.text = _pnt_list(getattr(t, "nodes", []) or [], fmt="{:.9f}")

    # ── PI model (the editable source of truth) ──────────────────────────
    m = state.get("pi_model")
    if m is not None:
        pm = etree.SubElement(root, "PIModel")
        pm.set("tol", NUM.format(float(m.tol)))
        pm.set("minRadius", NUM.format(float(m.min_radius)))
        pm.set("spiralDefault", NUM.format(float(m.spiral_default)))
        pm.set("useSpirals", _b(bool(m.use_spirals)))

        ref = etree.SubElement(pm, "RefPolyline")
        rp = etree.SubElement(ref, "PntList2D")
        rp.text = _pnt_list(np.asarray(m.xy_ref, dtype=float))

        vs = etree.SubElement(pm, "Vertices")
        vp = etree.SubElement(vs, "PntList2D")
        vp.text = _pnt_list(np.asarray(m.V, dtype=float))

        ix = etree.SubElement(pm, "Idx")
        ix.text = " ".join(str(int(i)) for i in m.idx)

        pis = etree.SubElement(pm, "PIs")
        for p in m.pis:
            pe = etree.SubElement(pis, "PI")
            pe.set("index", str(int(p.index)))
            pe.set("x", NUM_GEOM.format(float(p.xy[0])))
            pe.set("y", NUM_GEOM.format(float(p.xy[1])))
            pe.set("deflection", NUM_GEOM.format(float(p.deflection)))
            pe.set("radius", NUM_GEOM.format(float(p.radius)))
            pe.set("radiusAuto", NUM_GEOM.format(float(p.radius_auto)))
            pe.set("spiralLen", NUM_GEOM.format(float(p.spiral_len)))
            pe.set("spiralLenAuto", NUM_GEOM.format(float(p.spiral_len_auto)))
            pe.set("omitted", _b(p.omitted))
            pe.set("mergedWithNext", _b(p.merged_with_next))
            pe.set("mergedWithPrev", _b(p.merged_with_prev))
            pe.set("mergePartner", str(int(p.merge_partner)))
            pe.set("premergeSpiralLen", NUM_GEOM.format(float(p.premerge_spiral_len)))
            pe.set("chainNext", _b(p.chain_next))

    # ── Stations ─────────────────────────────────────────────────────────
    sts = state.get("stations") or []
    se = etree.SubElement(root, "Stations")
    for s in sts:
        e = etree.SubElement(se, "Station")
        e.set("name", s.name or "")
        e.set("lat", NUM.format(float(s.latlon[0])))
        e.set("lon", NUM.format(float(s.latlon[1])))
        e.set("chainage", NUM.format(float(s.chainage_m)))
        e.set("dwell", str(int(s.dwell_s)))
        e.set("source", s.source or "manual")

    etree.ElementTree(root).write(
        filepath, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_project(filepath: str) -> dict:
    """
    Read a `.coypu` project and rebuild the in-memory state.

    Returns a dict with: project_name, work_epsg, level, step, settings,
    tracks, selected_indices, pi_model, elements, stations, xy_list,
    chainages_list.
    """
    from osm.parser import Track
    from geometry.candidates import PIData, PIAlignment, rebuild_from_pi_model
    from geometry.curvature import compute_chainages
    from geometry.stationing import Station

    tree = etree.parse(filepath)
    root = tree.getroot()
    if root.tag != "CoypuProject":
        raise ValueError("Not a COYPU Feeder project file.")

    proj = root.find("Project")
    state: dict = {
        "project_name": proj.get("name", "Railway Alignment") if proj is not None else "",
        "work_epsg":    int(proj.get("workEpsg", "32633")) if proj is not None else 32633,
        "level":        proj.get("level", "") if proj is not None else "",
        "step":         int(proj.get("step", "0")) if proj is not None else 0,
    }

    # Settings — restore numbers as numbers (they were written as text)
    settings: dict = {}
    st = root.find("Settings")
    if st is not None:
        for k, v in st.attrib.items():
            if v in ("true", "false"):
                settings[k] = (v == "true")
            else:
                try:
                    f = float(v)
                    settings[k] = int(f) if f.is_integer() and "." not in v else f
                except ValueError:
                    settings[k] = v
    state["settings"] = settings

    # Tracks
    tracks: list = []
    tr = root.find("Tracks")
    sel: list = []
    if tr is not None:
        if tr.get("selected"):
            sel = [int(x) for x in tr.get("selected").split()]
        for e in tr.findall("Track"):
            t = Track()
            t.name = e.get("name", "")
            if e.get("wayIds"):
                t.way_ids = [int(x) for x in e.get("wayIds").split()]
            pl = e.find("PntList2D")
            t.nodes = _parse_pnt_list(pl.text if pl is not None else "")
            tracks.append(t)
    state["tracks"] = tracks
    state["selected_indices"] = sel

    # PI model
    model = None
    pm = root.find("PIModel")
    if pm is not None:
        ref = pm.find("RefPolyline/PntList2D")
        xy_ref = np.array(_parse_pnt_list(ref.text if ref is not None else ""),
                          dtype=float)
        vs = pm.find("Vertices/PntList2D")
        V = np.array(_parse_pnt_list(vs.text if vs is not None else ""), dtype=float)
        ix = pm.find("Idx")
        idx = [int(x) for x in (ix.text or "").split()] if ix is not None else []
        # chainages are derived, never stored
        chain = compute_chainages(xy_ref) if len(xy_ref) else np.zeros(0)

        pis: list = []
        for pe in pm.findall("PIs/PI"):
            pis.append(PIData(
                index=int(pe.get("index", "0")),
                xy=(float(pe.get("x", "0")), float(pe.get("y", "0"))),
                deflection=float(pe.get("deflection", "0")),
                radius=float(pe.get("radius", "-1")),
                radius_auto=float(pe.get("radiusAuto", "0")),
                spiral_len=float(pe.get("spiralLen", "-1")),
                spiral_len_auto=float(pe.get("spiralLenAuto", "0")),
                omitted=_pb(pe.get("omitted", "false")),
                merged_with_next=_pb(pe.get("mergedWithNext", "false")),
                merged_with_prev=_pb(pe.get("mergedWithPrev", "false")),
                merge_partner=int(pe.get("mergePartner", "-1")),
                premerge_spiral_len=float(pe.get("premergeSpiralLen", "-1")),
                chain_next=_pb(pe.get("chainNext", "false")),
            ))
        model = PIAlignment(
            V=V, idx=idx, pis=pis, xy_ref=xy_ref, chainages_ref=chain,
            tol=float(pm.get("tol", "1.0")),
            min_radius=float(pm.get("minRadius", "150.0")),
            spiral_default=float(pm.get("spiralDefault", "20.0")),
            use_spirals=_pb(pm.get("useSpirals", "true")),
        )
        # Exact round-trip: the elements are regenerated, never stored
        rebuild_from_pi_model(model)
    state["pi_model"] = model
    state["elements"] = list(model.elements) if model is not None else []

    # Stations
    stations: list = []
    for e in root.findall("Stations/Station"):
        stations.append(Station(
            name=e.get("name", ""),
            latlon=(float(e.get("lat", "0")), float(e.get("lon", "0"))),
            chainage_m=float(e.get("chainage", "0")),
            dwell_s=int(e.get("dwell", "30")),
            source=e.get("source", "manual"),
        ))
    state["stations"] = stations

    # Projected coordinates of the selected tracks (needed by later steps)
    from geometry.projection import wgs84_to_projected
    xy_list, ch_list = [], []
    chosen = [tracks[i] for i in sel if 0 <= i < len(tracks)] or tracks
    for t in chosen:
        if not t.nodes:
            continue
        pts = np.array(wgs84_to_projected(t.nodes, state["work_epsg"]), dtype=float)
        xy_list.append(pts)
        ch_list.append(compute_chainages(pts))
    state["xy_list"] = xy_list
    state["chainages_list"] = ch_list
    state["selected_tracks"] = chosen

    # Level 1 has no PI model — re-derive its trivial element chain
    if model is None and xy_list:
        from geometry.candidates import CandidateGenerator
        gen = CandidateGenerator(xy_list[0], ch_list[0], settings or {})
        state["elements"] = gen._run_level1().elements

    return state
