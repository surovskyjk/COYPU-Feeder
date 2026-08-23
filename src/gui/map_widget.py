"""
Map widget: QWebEngineView hosting a Leaflet.js map.
Python ↔ JavaScript via QWebChannel.

The HTML page is served from a local http://127.0.0.1 server so the page
has a real HTTP origin — this is the only reliable way to allow Chromium
(QtWebEngine) to fetch remote tile URLs without CORS/mixed-content blocks.

Leaflet JS + CSS are served from the same local server (src/gui/static/).
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import socket
import threading
from collections import deque

from PySide6.QtCore import QObject, Signal, Slot, QUrl, QTimer, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout,
    QComboBox, QCheckBox, QSizePolicy, QSlider,
)

# ---------------------------------------------------------------------------
# Tile providers
# ---------------------------------------------------------------------------

TILE_PROVIDERS = [
    {
        "label":  "OpenStreetMap",
        "url":    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "subs":   "",
        "attr":   "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors",
        "maxZoom": 19,
    },
    {
        "label":  "CARTO Dark Matter",
        "url":    "https://{s}.basemaps.cartocdn.com/dark_matter/{z}/{x}/{y}.png",
        "subs":   "abcd",
        "attr":   "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> © <a href='https://carto.com/attributions'>CARTO</a>",
        "maxZoom": 20,
    },
    {
        "label":  "CARTO Voyager",
        "url":    "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        "subs":   "abcd",
        "attr":   "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> © <a href='https://carto.com/attributions'>CARTO</a>",
        "maxZoom": 20,
    },
    {
        "label":  "OpenTopoMap",
        "url":    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "subs":   "abc",
        "attr":   "© <a href='https://opentopomap.org'>OpenTopoMap</a> © OpenStreetMap contributors",
        "maxZoom": 17,
    },
    {
        "label":  "Esri Satellite",
        "url":    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "subs":   "",
        "attr":   "Tiles © Esri — Source: Esri, DigitalGlobe, GeoEye, Earthstar Geographics",
        "maxZoom": 19,
    },
    {
        "label":  "ČÚZK Ortofoto (CZ)",
        "url":    "https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO_WM/MapServer/tile/{z}/{y}/{x}",
        "subs":   "",
        "attr":   "<a href='https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb/Podminky-poskytovani-sitovych-sluzeb-CUZK.aspx'>ČÚZK – on-line</a>",
        "maxZoom": 20,
    },
    {
        "label":  "OpenRailwayMap (railway only)",
        "url":    "https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png",
        "subs":   "abc",
        "attr":   "© <a href='https://www.openrailwaymap.org/'>OpenRailwayMap</a> © OpenStreetMap contributors",
        "maxZoom": 19,
    },
]

# Default provider index
_DEFAULT_PROVIDER = 0   # OpenStreetMap

# Track colours (COYPU's categorical series palette)
from gui.palette import TRACK_COLORS   # noqa: E402,F401


# ---------------------------------------------------------------------------
# Local HTTP server (serves static/ directory so tiles can load freely)
# ---------------------------------------------------------------------------

class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that suppresses access log output."""
    def log_message(self, fmt, *args):
        pass


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_static_server(static_dir: str) -> int:
    """Serve *static_dir* over HTTP on a random local port. Returns the port."""
    port = _find_free_port()
    handler = functools.partial(_SilentHandler, directory=static_dir)
    server  = http.server.HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return port


# ---------------------------------------------------------------------------
# Debug page
# ---------------------------------------------------------------------------

class DebugPage(QWebEnginePage):
    js_error = Signal(str)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        ERROR = QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel
        WARN  = QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel
        INFO  = QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel
        names = {ERROR: "ERROR", WARN: "WARN", INFO: "INFO"}
        print(f"[MapJS {names.get(level,'LOG')}] {message}  (line {line_number})")
        if level == ERROR:
            self.js_error.emit(f"Map JS error: {message}")


# ---------------------------------------------------------------------------
# Python ↔ JS bridge
# ---------------------------------------------------------------------------

class MapBridge(QObject):
    bounds_ready       = Signal(float, float, float, float)
    ready              = Signal()
    map_clicked        = Signal(float, float)   # lat, lon (station-placement mode)
    element_clicked    = Signal(str, bool)       # element_id, ctrl held (multiselect)
    split_point_clicked = Signal(float, float)   # lat, lon (Refine split-pick mode)
    pi_dragged         = Signal(int, float, float)  # pi index, lat, lon (edit mode)
    pi_restore_requested = Signal(int)           # pi index (edit-mode restore badge)
    pi_drag_start      = Signal(int)                # pi index — drag gesture began
    pi_drag_preview     = Signal(int, float, float)  # pi index, lat, lon — live, throttled

    @Slot(float, float, float, float)
    def on_bounds_ready(self, s, w, n, e):
        self.bounds_ready.emit(s, w, n, e)

    @Slot()
    def on_ready(self):
        self.ready.emit()

    @Slot(float, float)
    def on_map_clicked(self, lat, lon):
        self.map_clicked.emit(lat, lon)

    @Slot(str, bool)
    def on_element_clicked(self, element_id, ctrl):
        self.element_clicked.emit(element_id, ctrl)

    @Slot(float, float)
    def on_split_point_clicked(self, lat, lon):
        self.split_point_clicked.emit(lat, lon)

    @Slot(int, float, float)
    def on_pi_dragged(self, pi_index, lat, lon):
        self.pi_dragged.emit(pi_index, lat, lon)

    @Slot(int)
    def on_pi_restore_requested(self, pi_index):
        self.pi_restore_requested.emit(pi_index)

    @Slot(int)
    def on_pi_drag_start(self, pi_index):
        self.pi_drag_start.emit(pi_index)

    @Slot(int, float, float)
    def on_pi_drag_preview(self, pi_index, lat, lon):
        self.pi_drag_preview.emit(pi_index, lat, lon)


# ---------------------------------------------------------------------------
# MapWidget
# ---------------------------------------------------------------------------

class MapWidget(QWidget):
    bounds_ready        = Signal(float, float, float, float)
    js_error            = Signal(str)
    map_clicked         = Signal(float, float)   # lat, lon (station-placement mode)
    element_clicked     = Signal(str, bool)       # element_id, ctrl held (multiselect)
    split_point_clicked = Signal(float, float)    # lat, lon (Refine split-pick mode)
    pi_dragged          = Signal(int, float, float)  # pi index, lat, lon (edit mode)
    pi_restore_requested = Signal(int)            # pi index (edit-mode restore badge)
    pi_drag_start       = Signal(int)                # pi index — drag gesture began
    pi_drag_preview     = Signal(int, float, float)  # pi index, lat, lon — live, throttled

    def __init__(self, parent=None):
        super().__init__(parent)
        from gui.theme import is_dark_active, current_tokens
        self._dark = is_dark_active()
        self._tokens = current_tokens()
        self._map_ready  = False
        self._js_queue: deque[str] = deque()
        self._last_highlight_ids: list | None = None
        self._js_aln_count: int | None = None
        self._legend_visible = True
        self._build()
        # Queued, so the page has its palette before the first draw call.
        self._push_page_theme()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Map settings widgets ─────────────────────────────────────
        # Owned by MapWidget (it wires their signals and reads them back
        # in _apply_current_provider/set_theme), but laid out on the main
        # window's toolbar instead of a dedicated row here (Part J) — App
        # reaches into self.map_widget._provider_combo/_rail_chk when
        # building the toolbar, once this widget exists.
        self._provider_combo = QComboBox()
        for p in TILE_PROVIDERS:
            self._provider_combo.addItem(p["label"])
        self._provider_combo.setCurrentIndex(_DEFAULT_PROVIDER)
        self._provider_combo.setToolTip("Choose base map tile provider")
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        # `activated` only fires for USER selections (not programmatic
        # setCurrentIndex) — once the user picks a provider, theme switches
        # stop overriding their choice (see set_theme).
        self._provider_combo.activated.connect(self._on_provider_activated)
        self._user_provider_choice = False
        self._provider_combo.setFixedWidth(160)

        self._rail_chk = QCheckBox("🚂 Railway overlay")
        self._rail_chk.setChecked(True)
        self._rail_chk.setToolTip("Show OpenRailwayMap overlay on top of base tiles")
        self._rail_chk.toggled.connect(self._on_rail_overlay_toggled)

        self._cadastre_chk = QCheckBox("🗺️ Land register overlay")
        self._cadastre_chk.setChecked(False)
        self._cadastre_chk.setToolTip(
            "Cadastral parcel/building outlines — © ČÚZK – on-line.\n"
            "For orientation only; not a legally binding land-register extract.\n"
            "Only renders when zoomed in close (parcels don't draw at low zoom)."
        )
        self._cadastre_chk.toggled.connect(self._on_cadastre_overlay_toggled)

        self._cadastre_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._cadastre_opacity_slider.setRange(0, 100)
        self._cadastre_opacity_slider.setValue(60)
        self._cadastre_opacity_slider.setFixedWidth(80)
        self._cadastre_opacity_slider.setEnabled(False)
        self._cadastre_opacity_slider.setToolTip("Land register overlay opacity")
        self._cadastre_opacity_slider.valueChanged.connect(self._on_cadastre_opacity_changed)

        # ── Web view ──────────────────────────────────────────────────
        page = DebugPage()
        page.js_error.connect(self.js_error)

        self._view = QWebEngineView()
        self._view.setPage(page)
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._bridge  = MapBridge()
        self._channel = QWebChannel()
        self._channel.registerObject("backend", self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._bridge.ready.connect(self._on_map_ready)
        self._bridge.bounds_ready.connect(self.bounds_ready)
        self._bridge.map_clicked.connect(self.map_clicked)
        self._bridge.element_clicked.connect(self.element_clicked)
        self._bridge.split_point_clicked.connect(self.split_point_clicked)
        self._bridge.pi_dragged.connect(self.pi_dragged)
        self._bridge.pi_restore_requested.connect(self.pi_restore_requested)
        self._bridge.pi_drag_start.connect(self.pi_drag_start)
        self._bridge.pi_drag_preview.connect(self.pi_drag_preview)

        # Serve gui/static/ (which contains map.html) over a local HTTP
        # server. Using http://127.0.0.1 as origin removes ALL cross-origin
        # tile blocks. map.html is maintained ON DISK — do NOT regenerate it
        # here (an earlier embedded-HTML write-out silently reverted every
        # map feature on each launch).
        static_dir = os.path.join(os.path.dirname(__file__), "static")

        self._server_port = _start_static_server(static_dir)
        self._view.setUrl(QUrl(f"http://127.0.0.1:{self._server_port}/map.html"))

        layout.addWidget(self._view)

        # Safety fallback: if QWebChannel never fires (slow startup), unblock after 12 s
        self._ready_timer = QTimer(self)
        self._ready_timer.setSingleShot(True)
        self._ready_timer.timeout.connect(self._on_ready_timeout)
        self._ready_timer.start(12_000)

    # ------------------------------------------------------------------
    # Ready / queue
    # ------------------------------------------------------------------

    def _on_map_ready(self):
        self._ready_timer.stop()
        if self._map_ready:
            return
        self._map_ready = True
        # Apply the correct theme-based provider
        self._apply_current_provider()
        self._push_page_theme()
        while self._js_queue:
            self._view.page().runJavaScript(self._js_queue.popleft())

    def _on_ready_timeout(self):
        if not self._map_ready:
            print("[MapWidget] QWebChannel timeout — forcing ready.")
            self._map_ready = True
            self._apply_current_provider()
            self._push_page_theme()
            while self._js_queue:
                self._view.page().runJavaScript(self._js_queue.popleft())

    def _run_js(self, js: str, callback=None):
        if self._map_ready:
            if callback is not None:
                self._view.page().runJavaScript(js, callback)
            else:
                self._view.page().runJavaScript(js)
        else:
            # Queued calls never carry a callback through today (nothing
            # currently needs a result from a call made before map-ready).
            self._js_queue.append(js)

    # Payloads above this size are shipped as a JSON string + JSON.parse()
    # instead of a JS array/object literal: Chromium tokenizes+parses a JS
    # literal as source code (slow, ~1 MB on a long alignment edit), while
    # JSON.parse on the same bytes is a dedicated fast path.
    _JSON_PARSE_THRESHOLD = 50_000

    def _run_js_call(self, fn_name: str, *args, callback=None):
        """Call `fn_name(*args)`, encoding any large arg as JSON.parse(...)."""
        parts = []
        for a in args:
            s = json.dumps(a)
            if len(s) > self._JSON_PARSE_THRESHOLD:
                parts.append(f"JSON.parse({json.dumps(s)})")
            else:
                parts.append(s)
        self._run_js(f"{fn_name}({', '.join(parts)})", callback=callback)

    # ------------------------------------------------------------------
    # Tile provider helpers
    # ------------------------------------------------------------------

    def _apply_current_provider(self):
        idx = self._provider_combo.currentIndex()
        p = TILE_PROVIDERS[idx]
        self._run_js(
            f"setTileProvider({json.dumps(p['url'])}, {json.dumps(p['subs'])}, "
            f"{json.dumps(p['attr'])}, {p['maxZoom']})"
        )
        show_rail = self._rail_chk.isChecked()
        self._run_js(f"setRailOverlay({'true' if show_rail else 'false'})")
        show_cadastre = self._cadastre_chk.isChecked()
        self._run_js(f"setCadastreOverlay({'true' if show_cadastre else 'false'})")
        self._run_js(f"setCadastreOpacity({self._cadastre_opacity_slider.value() / 100})")

    def _on_provider_changed(self, idx: int):
        p = TILE_PROVIDERS[idx]
        self._run_js(
            f"setTileProvider({json.dumps(p['url'])}, {json.dumps(p['subs'])}, "
            f"{json.dumps(p['attr'])}, {p['maxZoom']})"
        )

    def _on_provider_activated(self, idx: int):
        self._user_provider_choice = True

    def _on_rail_overlay_toggled(self, checked: bool):
        self._run_js(f"setRailOverlay({'true' if checked else 'false'})")

    def _on_cadastre_overlay_toggled(self, checked: bool):
        self._cadastre_opacity_slider.setEnabled(checked)
        self._run_js(f"setCadastreOverlay({'true' if checked else 'false'})")

    def _on_cadastre_opacity_changed(self, value: int):
        self._run_js(f"setCadastreOpacity({value / 100})")

    # ------------------------------------------------------------------
    # Public API (called from App)
    # ------------------------------------------------------------------

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        """Restyle in place after a theme switch (COYPU's dock convention).

        Basemap tiles are left to the provider, exactly as COYPU does in
        map_viewer.applyTheme - only the page's own chrome is themed.
        """
        from gui.theme import current_tokens, is_dark_active
        self._tokens = tokens or current_tokens()
        self.set_theme(is_dark_active() if isDark is None else bool(isDark))
        self._push_page_theme()

    def _push_page_theme(self):
        """Hand the page its colour variables and the element palette."""
        from gui.theme import current_tokens
        from gui.palette import ELEMENT_COLORS, MAP_COLORS, LEGEND_ENTRIES
        t = getattr(self, "_tokens", None) or current_tokens()
        panel = "rgba(43,43,43,0.94)" if self._dark else "rgba(255,255,255,0.94)"
        theme = {
            "bg":     t["base"],
            "panel":  panel,
            "fg":     t["text"],
            "border": t["border"],
            "accent": t["highlight"],
            "ok":     t["accentDone"],
            "muted":  t["disabledText"],
            "link":   t["highlight"],
        }
        self._run_js(f"setElementColors({json.dumps(ELEMENT_COLORS)})")
        self._run_js(f"setMapColors({json.dumps(MAP_COLORS)})")
        self._run_js(f"setPageTheme({json.dumps(theme)})")
        self._run_js(f"setLegend({json.dumps(LEGEND_ENTRIES)}, "
                     f"{'true' if self._legend_visible else 'false'})")

    def set_legend_visible(self, visible: bool):
        self._legend_visible = bool(visible)
        self._run_js(f"setLegend(null, {'true' if visible else 'false'})")

    def set_theme(self, dark: bool):
        self._dark = dark
        # Auto-switch to a sensible provider for the new theme — but never
        # override a provider the user picked by hand (that teardown/refetch
        # of the basemap was also a visible flash on every theme change).
        if self._user_provider_choice:
            return
        target = "CARTO Dark Matter" if dark else "CARTO Voyager"
        for i, p in enumerate(TILE_PROVIDERS):
            if p["label"] == target:
                if self._provider_combo.currentIndex() != i:
                    self._provider_combo.setCurrentIndex(i)
                break

    def show_tracks(self, tracks, fit: bool = True):
        """fit=False keeps the current zoom/pan (in-place track edits)."""
        payload = [
            {"nodes": [[n[0], n[1]] for n in t.nodes],
             "color": TRACK_COLORS[i % len(TRACK_COLORS)],
             "name":  t.name}
            for i, t in enumerate(tracks)
        ]
        self._run_js_call("showTracks", payload, fit)

    def highlight_track(self, idx: int):
        self._run_js(f"highlightTrack({idx})")

    def fly_to_tracks(self):
        self._run_js("flyToTracks()")

    def show_osm_reference(self, alignments: list):
        """Draw the raw OSM polyline as a dashed reference overlay (colour/
        opacity/visibility as last set by `set_osm_reference_style`)."""
        payload = [{"nodes": nodes} for nodes in alignments]
        self._run_js_call("showOSMReference", payload)

    def clear_osm_reference(self):
        self._run_js("clearOSMReference()")

    def set_osm_reference_style(self, visible: bool, color: str, opacity: float):
        """
        Part E: user control of the original (OSM) alignment's appearance.
        Restyles the CURRENTLY drawn overlay in place (setStyle / add-remove
        — no redraw) and remembers the choice for the next showOSMReference.
        """
        self._run_js(
            f"setOSMReferenceStyle({'true' if visible else 'false'}, "
            f"{json.dumps(color)}, {float(opacity)})")

    def show_alignment(self, alignments: list):
        payload = [{"nodes": nodes} for nodes in alignments]
        self._last_highlight_ids = None
        self._js_aln_count = 0   # this path doesn't populate alignEntries
        self._run_js_call("showAlignment", payload)

    def show_alignment_segmented(self, segments: list, fit_view: bool = True):
        """
        Draw the alignment as a sequence of per-element coloured polylines.

        Parameters
        ----------
        segments : list of dicts, each shaped:
            {"type":   "Line" | "Arc" | "Spiral",
             "params": {length: …, sta_start: …, radius: …, ...},
             "points": [[lat, lon], [lat, lon], ...]}
        fit_view : fly the map to the alignment bounds. Pass False for
            edit-driven rebuilds so the user's current zoom/pan is kept.

        The JS side picks a colour by element type (Line=blue, Arc=red,
        Spiral=green) and binds a sticky tooltip listing the element's
        parameters.
        """
        self._last_highlight_ids = None
        self._js_aln_count = len(segments)
        self._run_js_call("showAlignmentSegmented", segments, fit_view)

    def update_alignment_segmented(self, segments: list):
        """
        Diff-update the segmented alignment after an edit: the JS side keeps
        the Leaflet layers of unchanged elements and recreates only the
        edited span (falls back to a full redraw when nothing is shown yet).
        Keeps the user's current zoom/pan.
        """
        self._last_highlight_ids = None
        self._js_aln_count = len(segments)
        self._run_js_call("updateAlignmentSegmented", segments)

    def patch_alignment_segmented(self, start: int, removed: int,
                                  new_segments: list, metas: list):
        """
        Span-local update after a `rebuild_pi_span` edit: replaces
        `alignEntries[start : start+removed]` with `new_segments` (each a
        full {type, params, points} dict) and retargets every OTHER kept
        entry's tooltip/params from the parallel `metas` list (light
        {type, params} dicts, no points — ids and stations shift after a
        merge/omit even for elements whose geometry didn't move).

        Returns False (doing nothing) when the entry count currently on the
        page doesn't match what this splice expects — e.g. the map was
        cleared/reloaded since the last full draw, or a previous patch
        desynced — so the caller can fall back to a full redraw instead of
        corrupting the page's layer bookkeeping.
        """
        old_count_expected = len(metas) - len(new_segments) + removed
        if getattr(self, "_js_aln_count", None) != old_count_expected:
            return False   # caller should fall back to a full redraw
        self._last_highlight_ids = None
        self._js_aln_count = len(metas)

        def _on_patch_result(ok):
            # The JS side can still refuse the patch (e.g. a degenerate
            # near-zero-length element didn't get its own layer, so the
            # page's actual entry count doesn't match what we assumed).
            # Invalidate the counter so the NEXT edit's pre-check fails
            # closed and falls back to a full redraw instead of silently
            # compounding a desync.
            if ok is False:
                self._js_aln_count = None

        self._run_js_call("patchAlignmentSegmented",
                          start, removed, new_segments, metas,
                          callback=_on_patch_result)
        return True

    def fly_to_alignment(self):
        self._run_js("flyToAlignment()")

    def clear_alignment(self):
        self._last_highlight_ids = None
        self._js_aln_count = 0
        self._run_js("clearAlignment()")

    def request_bounds(self):
        self._run_js("getMapBounds()")

    def show_candidates(self, candidates: list):
        """
        Show multiple candidate alignments as coloured polylines.

        Parameters
        ----------
        candidates : list of dicts with keys: nodes [[lat,lon],...], color, label
        """
        self._run_js(f"showCandidates({json.dumps(candidates)})")

    def clear_candidates(self):
        """Remove all candidate alignment overlays."""
        self._run_js("clearCandidates()")

    def show_pi_overlay(self, payload: dict):
        """
        Draw Points of Intersection + dashed virtual tangent extensions.

        payload = {"pis":      [{"id", "latlon", "omitted", "defl_deg"}, …],
                   "tangents": [{"from": [lat,lon], "to": [lat,lon]}, …]}

        Skipped entirely when the payload is identical to the one currently
        displayed (edits far from any PI leave the overlay untouched).
        """
        js = json.dumps(payload)
        if js == getattr(self, "_last_pi_overlay", None):
            return
        self._last_pi_overlay = js
        if len(js) > self._JSON_PARSE_THRESHOLD:
            self._run_js(f"showPIOverlay(JSON.parse({json.dumps(js)}))")
        else:
            self._run_js(f"showPIOverlay({js})")

    def move_pi_marker(self, pi_id: int, latlon: tuple[float, float]):
        """
        Move ONE PI marker in place instead of tearing down and recreating
        every marker (the old `show_pi_overlay(_lastPIPayload)` full rebuild
        also made the dragged marker itself visibly snap/recreate). Falls
        back to a full `show_pi_overlay` call site-side when no payload is
        cached yet (nothing to patch).
        """
        self._run_js_call("movePIMarker", pi_id, [float(latlon[0]), float(latlon[1])])

    def update_pi_tangents(self, tangents: list):
        """Rebuild only the dashed virtual-tangent stub polylines (few of
        them per PI) without touching the PI markers themselves."""
        self._run_js_call("updatePITangents", tangents)

    def sync_pi_overlay_cache(self, payload: dict):
        """Record `payload` as the currently-displayed PI overlay without a
        JS round-trip — call after an incremental update (move_pi_marker +
        update_pi_tangents) so a later full `show_pi_overlay` dedupe check
        still compares against what's actually on the page."""
        self._last_pi_overlay = json.dumps(payload)

    def clear_pi_overlay(self):
        self._last_pi_overlay = None
        self._run_js("clearPIOverlay()")

    def highlight_element(self, element_id: str):
        """Emphasize (glow) one alignment element by its stable id."""
        self._run_js(f"highlightElement({json.dumps(element_id)})")

    def highlight_elements(self, element_ids: list):
        """Emphasize (glow) several alignment elements (table multiselect).

        Consecutive identical requests are dropped Python-side (table
        repopulates re-emit the same selection); the cache is invalidated
        whenever the alignment layer set is rebuilt below."""
        ids = list(element_ids)
        if ids == self._last_highlight_ids:
            return
        self._last_highlight_ids = ids
        self._run_js(f"highlightElements({json.dumps(ids)})")

    def emphasize_candidate(self, algo_id: str):
        """Thicken one Step-4 candidate polyline (empty string resets)."""
        self._run_js(f"emphasizeCandidate({json.dumps(algo_id)})")

    def show_stations(self, stations: list):
        """stations = [{"name", "latlon": [lat,lon], "km"}, …]"""
        self._run_js(f"showStations({json.dumps(stations)})")

    def clear_stations(self):
        self._run_js("clearStations()")

    def set_station_click_mode(self, on: bool):
        """Arm/disarm the click-to-place-station mode (crosshair cursor)."""
        self._run_js(f"setStationClickMode({'true' if on else 'false'})")

    def set_split_click_mode(self, on: bool):
        """Arm/disarm click-to-pick-a-split-point mode (crosshair cursor)."""
        self._run_js(f"setSplitClickMode({'true' if on else 'false'})")

    def set_pi_edit_mode(self, on: bool):
        """
        Toggle "Edit mode" (Part C): PI markers become draggable when on —
        dragend fires `pi_dragged(index, lat, lon)`. The PI overlay is
        redrawn with the new marker type, so call this before/after
        `show_pi_overlay` as convenient; either order ends up consistent
        because `showPIOverlay` itself reads the current mode.
        """
        self._run_js(f"setPIEditMode({'true' if on else 'false'})")

    def set_live_drag_preview(self, on: bool):
        """Enable/disable the throttled `pi_drag_preview` signal fired
        while a PI marker is being dragged (Part I). Off = only `dragend`
        updates anything, exactly as before this feature existed."""
        self._run_js(f"setLiveDragPreview({'true' if on else 'false'})")

    def show_cross_section(self, left_pts: list, right_pts: list):
        """
        Draw colour-coded cross-section overlays.

        Parameters
        ----------
        left_pts / right_pts : list of [lat, lon, color_hex]
            One entry per sampled station; consecutive entries are connected
            by polyline segments coloured by the first entry's colour.

        Very long corridors are decimated to a bounded vertex count before
        shipping — colour-transition points are always kept so the coloured
        runs stay visually exact.
        """
        self._run_js_call("showCrossSection",
                          self._decimate_cs(left_pts), self._decimate_cs(right_pts))

    @staticmethod
    def _decimate_cs(pts: list, cap: int = 2000) -> list:
        if not pts or len(pts) <= cap:
            return pts
        stride = max(1, len(pts) // cap)
        out = []
        for i, p in enumerate(pts):
            if (i % stride == 0 or i == len(pts) - 1
                    or (i > 0 and p[2] != pts[i - 1][2])):
                out.append(p)
        return out

    def clear_cross_section(self):
        """Remove cross-section overlays."""
        self._run_js("clearCrossSection()")

    def clear_all(self):
        self._run_js("clearAll()")   # clears tracks + osmRef + alignment + candidates + cross-section
