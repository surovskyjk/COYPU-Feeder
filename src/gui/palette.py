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
    # Deliberately muted/subdued — it's a reference to fit against, not a
    # result to look at; the constructed alignment (ELEMENT_COLORS) carries
    # the saturated hues so the two never compete for attention.
    "osmReference":    "#8a8a8a",
    "glow":            "#ffffff",   # halo drawn under an emphasised line
    "selected":        "#ff1744",   # the exported alignment preview
    # An UNPLANNED edit-time fallback (segmented rendering failed after a
    # split/edit, see App._sample_segments) — muted and thin so it never
    # reads as "here's your result" the way "selected" deliberately does,
    # and never visually buries the OSM reference layer under it.
    "alignmentDegraded": "#78909c",
    "piBorder":        "#37474f",   # PI marker outline / fill / omitted
    "piFill":          "#ffffff",
    "piOmitted":       "#9e9e9e",
    "stationBorder":   "#4a148c",   # station marker
    "stationFill":     "#ffd54f",
    # Vertical-profile hover dot (COYPU's track-cursor ring and fill).
    "profileCursor":     "#2f6fb5",
    "profileCursorFill": "#ffffff",
}

# Factory default for the OSM reference overlay preference (see app.py).
OSM_REF_DEFAULT = MAP_COLORS["osmReference"]

# Map legend entries — pushed to the page alongside ELEMENT_COLORS/
# MAP_COLORS so the swatches always match what's actually drawn. `dashed`
# marks the OSM-reference sample so the legend swatch itself carries the
# same dash pattern as the real overlay.
LEGEND_ENTRIES = [
    {"label": "Line",          "color": ELEMENT_COLORS["Line"]},
    {"label": "Arc",           "color": ELEMENT_COLORS["Arc"]},
    {"label": "Spiral",        "color": ELEMENT_COLORS["Spiral"]},
    {"label": "OSM reference", "color": MAP_COLORS["osmReference"], "dashed": True},
]

def element_color(kind: str, default: str = "#ffffff") -> str:
    """Colour for an element type name ('Line' | 'Arc' | 'Spiral')."""
    return ELEMENT_COLORS.get(kind, default)


# Deviation heatmap (Req 9b) — "Colour by baseline deviation" render mode:
# a continuous green -> yellow -> red ramp, keyed on an element's `_max_dev`
# (annotate_element_deviations) against a caller-supplied threshold (usually
# the project's own max-deviation tolerance, so "red" means "outside the
# tolerance the user actually configured", not an arbitrary fixed number).
DEVIATION_RAMP_STOPS = [
    (0.00, "#2e9e4f"),   # good — within tolerance
    (0.50, "#d68f2e"),   # amber — approaching the limit
    (1.00, "#d64545"),   # red — at/over the limit
]


def deviation_color(max_dev: float, threshold: float) -> str:
    """Hex colour for one element's `_max_dev` on the ramp above, where
    `max_dev == 0` is fully green and `max_dev >= threshold` is fully red
    (clamped — a track well outside tolerance doesn't get an even angrier
    colour, it just stays at the ramp's worst stop)."""
    threshold = max(1e-6, float(threshold))
    t = max(0.0, min(1.0, float(max_dev) / threshold))
    for (t0, c0), (t1, c1) in zip(DEVIATION_RAMP_STOPS, DEVIATION_RAMP_STOPS[1:]):
        if t <= t1:
            local = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return _lerp_hex(c0, c1, local)
    return DEVIATION_RAMP_STOPS[-1][1]


def _lerp_hex(c0: str, c1: str, t: float) -> str:
    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    r0, g0, b0 = rgb(c0)
    r1, g1, b1 = rgb(c1)
    r = round(r0 + (r1 - r0) * t)
    g = round(g0 + (g1 - g0) * t)
    b = round(b0 + (b1 - b0) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# Legend shown while the map is in "Colour: deviation" mode (Req 9b) —
# swapped in for LEGEND_ENTRIES above, same setLegend(entries, visible) call.
DEVIATION_LEGEND_ENTRIES = [
    {"label": "Within tolerance", "color": DEVIATION_RAMP_STOPS[0][1]},
    {"label": "Approaching limit", "color": DEVIATION_RAMP_STOPS[1][1]},
    {"label": "At/over limit",    "color": DEVIATION_RAMP_STOPS[2][1]},
    {"label": "OSM reference",    "color": MAP_COLORS["osmReference"], "dashed": True},
]


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
