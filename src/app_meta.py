"""
Single source of truth for application metadata (name, version, authorship,
data sources, license). Imported by the GUI (splash, About dialog), the
LandXML builder and the packaging spec.
"""

from __future__ import annotations

APP_NAME    = "COYPU Feeder"
APP_VERSION = "1.1.0"
APP_TAGLINE = "OSM railways → LandXML alignments"

REPO_URL    = "https://github.com/surovskyjk/COYPU-Feeder"
README_URL  = "https://github.com/surovskyjk/COYPU-Feeder#readme"
RELEASES_URL = "https://github.com/surovskyjk/COYPU-Feeder/releases/latest"

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
    "prolonged spirals. Elevation is sampled from a DEM and fitted as a "
    "vertical profile with parabolic curves. Output is LandXML 1.2 with a "
    "Cant block plus an optional stations CSV."
)

DATA_SOURCES = [
    ("OpenStreetMap (railway geometry)",
     "© OpenStreetMap contributors, ODbL — https://www.openstreetmap.org"),
    ("Overpass API (data queries)",
     "https://overpass-api.de"),
    ("OpenTopoData / DEM (elevation)",
     "https://www.opentopodata.org"),
    ("Leaflet (map rendering)",
     "© Leaflet contributors, BSD-2 — https://leafletjs.com"),
]
