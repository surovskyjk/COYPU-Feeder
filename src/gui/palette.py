"""
Domain colours that are deliberately theme-independent.

Two kinds live here, and they are the only hex literals outside
:mod:`gui.theme_manager` and :mod:`gui.icons`:

* ``ELEMENT_COLORS`` — the Line / Spiral / Arc identity the Feeder draws on the
  map and in the element table. These stay out of the theme tokens because they
  carry geometric meaning rather than chrome semantics, and must stay
  recognisable over any basemap. Previously duplicated verbatim in
  ``element_table.py`` and ``static/map.html``; this is now the single source
  and the map is handed the dict at init.
* ``WARNING`` — COYPU's amber. ``theme_manager`` has ``accentDone`` and
  ``error`` tokens but no warning hue, so the value is taken from COYPU's
  ``plot_widgets.py`` to keep the two applications in agreement.
"""

from __future__ import annotations

ELEMENT_COLORS = {
    "Line":   "#42a5f5",
    "Arc":    "#ef5350",
    "Spiral": "#66bb6a",
}

# COYPU's amber (plot_widgets.VEHICLE_SPEED_COLORS[-1]).
WARNING = "#d68f2e"

# Categorical palette for telling multiple items apart on the map (OSM
# tracks). Taken from COYPU's plot_widgets.VARIANT_COLORS so a run of
# items is colour-coded identically in both applications.
TRACK_COLORS = [
    "#d64545", "#2e9e4f", "#4a7fd4", "#a04fd6", "#d68f2e",
    "#0f8b8d", "#e377c2", "#8c564b", "#bcbd22", "#17becf",
]

# Candidate levels, keyed by algo_id. Previously duplicated between
# gui/worker.py and gui/steps/step4_candidates.py. The hues are COYPU's
# categorical ones (purple / amber / teal) so levels read the same way
# the two applications colour-code any other series.
LEVEL_COLORS = {
    "level1": "#a04fd6",   # OSM polyline - purple
    "level2": "#d68f2e",   # Lines + Arcs - amber
    "level3": "#0f8b8d",   # Lines + Spirals + Arcs - teal
}

# Colours the Leaflet page draws with. Pushed into the page by
# MapWidget._push_page_theme so this module stays the single source; the
# page keeps matching defaults for the moments before that call lands.
MAP_COLORS = {
    "trackFallback":   TRACK_COLORS[0],
    "trackHighlight":  "#ffeb3b",   # the one track under the cursor
    "elementFallback": "#ffffff",   # unknown element type
    # Dashed reference overlay of the original OSM alignment. This is also
    # the factory default for the user-adjustable osm_ref_color preference.
    "osmReference":    "#00e5ff",
    "glow":            "#ffffff",   # halo drawn under an emphasised line
    "selected":        "#ff1744",   # the exported alignment preview
    "piBorder":        "#37474f",   # PI marker outline / fill / omitted
    "piFill":          "#ffffff",
    "piOmitted":       "#9e9e9e",
    "stationBorder":   "#4a148c",   # station marker
    "stationFill":     "#ffd54f",
}

# Factory default for the OSM reference overlay preference (see app.py).
OSM_REF_DEFAULT = MAP_COLORS["osmReference"]


def element_color(kind: str, default: str = "#ffffff") -> str:
    """Colour for an element type name ('Line' | 'Arc' | 'Spiral')."""
    return ELEMENT_COLORS.get(kind, default)


def contrast_text(hex_color: str) -> str:
    """Black or white — whichever stays readable on *hex_color*.

    Used for colour swatches showing a user-chosen colour, where neither
    the theme's `text` nor `highlightText` token is the right answer: the
    background is arbitrary, so the foreground has to be derived from it.
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#000000"
    # Rec. 601 luma
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


# ---------------------------------------------------------------------------
# Brand mark
# ---------------------------------------------------------------------------

# The application icon's tile. Deliberately theme-independent: the icon is
# painted once at startup and lands in the taskbar and title bar, where the
# application's own theme does not apply and a colour that shifted with the OS
# theme would read as a different app. The splash screen, which is drawn inside
# the themed window, uses tokens instead (see gui/branding.py).
BRAND = {
    "bgTop":  "#20242d",
    "bgBot":  "#14161c",
    "accent": "#2f6fb5",   # COYPU's light-theme highlight
}
