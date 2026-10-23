"""
Single source of truth for application metadata (name, version, authorship,
data sources, license). Imported by the GUI (splash, About dialog), the
LandXML builder and the packaging spec.
"""

from __future__ import annotations

APP_NAME    = "COYPU Feeder"
APP_VERSION = "1.3.0"
APP_TAGLINE = "OSM railways → LandXML alignments"

REPO_URL    = "https://github.com/surovskyjk/COYPU-Feeder"
README_URL  = "https://github.com/surovskyjk/COYPU-Feeder#readme"
RELEASES_URL = "https://github.com/surovskyjk/COYPU-Feeder/releases/latest"
COYPU_REPO_URL = "https://github.com/surovskyjk/COYPU"

AUTHOR      = "Jakub Surovsky"
LICENSE     = "MIT License"
COPYRIGHT   = "© 2025–2026 Jakub Surovsky"

# Short human-readable descriptions reused by the About dialog.
ALGORITHM_DESC = (
    "COYPU Feeder converts an OpenStreetMap railway polyline into a clean, "
    "C1-continuous horizontal alignment. The polyline is simplified into a "
    "tangent polygon whose interior vertices are the Points of Intersection "
    "(PIs). Three levels are offered:\n\n"
    "  • Level 1 — the raw OSM polyline (reference).\n"
    "  • Level 2 — straight lines + circular arcs tangent at every PI.\n"
    "  • Level 3 — lines + clothoid transition spirals + arcs.\n\n"
    "Spiral placement uses the exact Fresnel shift p and abscissa k so the "
    "Line–Spiral–Arc–Spiral–Line zone closes on the outgoing tangent by "
    "construction; the alignment starts and ends exactly at the OSM "
    "endpoints. Radii, spiral lengths and PIs can be edited interactively, "
    "and short intermediate straights can be absorbed into symmetric "
    "prolonged spirals. Elevation is sampled from a DEM along the "
    "fitted centreline and smoothed into a vertical profile of "
    "constant-grade tangents under a configurable maximum gradient, "
    "with parabolic vertical curves at each PVI and optional manual "
    "straight connections across tunnels and viaducts. Output is LandXML 1.2 with a "
    "Cant block plus an optional stations CSV."
)

DATA_SOURCES = [
    ("OpenStreetMap (railway geometry)",
     "© OpenStreetMap contributors, ODbL — https://www.openstreetmap.org"),
    ("Overpass API (data queries)",
     "https://overpass-api.de"),
    ("OpenTopoData / DEM (elevation)",
     "https://www.opentopodata.org"),
    ("ČÚZK DMR 5G (elevation)",
     "© ČÚZK – on-line. Lidar terrain model, ~0.2 m accuracy. Free network service; "
     "underlying data CC BY 4.0. "
     "<a href='https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb/"
     "Podminky-poskytovani-sitovych-sluzeb-CUZK.aspx'>Terms of use</a>"),
    ("ČÚZK Ortofoto / Katastrální mapa (map layers)",
     "© ČÚZK – on-line. Aerial orthophoto and land-register (cadastral) overlay. "
     "The land-register layer is for orientation only — <b>not</b> a legally binding "
     "cadastre extract. "
     "<a href='https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb/"
     "Podminky-poskytovani-sitovych-sluzeb-CUZK.aspx'>Terms of use</a>"),
    ("CARTO basemaps (map layers)",
     "© CARTO, © OpenStreetMap contributors — https://carto.com/attributions. "
     "Needs your own API key (Settings ▸ Preferences ▸ Map basemap)."),
    ("Leaflet (map rendering)",
     "© Leaflet contributors, BSD-2 — https://leafletjs.com"),
]
