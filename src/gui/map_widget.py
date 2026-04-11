"""
Map widget: QWebEngineView hosting a Leaflet.js map.
Python ↔ JavaScript communication via QWebChannel.
"""

from __future__ import annotations

import json
from collections import deque

from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolBar, QPushButton, QSizePolicy
from PySide6.QtGui import QAction

# ---------------------------------------------------------------------------
# Track colours (cycle)
# ---------------------------------------------------------------------------
TRACK_COLORS = [
    "#4fc3f7", "#81c784", "#ffb74d", "#e57373",
    "#ce93d8", "#80cbc4", "#fff176", "#ff8a65",
]
HIGHLIGHT_COLOR = "#ffeb3b"


# ---------------------------------------------------------------------------
# Inline HTML / JavaScript
# ---------------------------------------------------------------------------

MAP_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  html, body, #map { width:100%; height:100%; margin:0; padding:0; background:#1a1a1a; }
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
</head>
<body>
<div id="map"></div>
<script>
var map = L.map('map', {zoomControl: true}).setView([50.05, 14.42], 7);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors',
  maxZoom: 19
}).addTo(map);

var trackLayers = [];
var bboxLayer   = null;
var bboxMode    = false;
var bboxStart   = null;
var backend     = null;

/* ---- QWebChannel bootstrap ---- */
new QWebChannel(qt.webChannelTransport, function(channel) {
  backend = channel.objects.backend;
  backend.on_ready();
});

/* ---- Bbox drawing ---- */
function setBboxMode(enabled) {
  bboxMode = enabled;
  if (enabled) {
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
  } else {
    map.dragging.enable();
    map.getContainer().style.cursor = '';
  }
}

map.on('mousedown', function(e) {
  if (!bboxMode) return;
  bboxStart = e.latlng;
  if (bboxLayer) { map.removeLayer(bboxLayer); bboxLayer = null; }
});

map.on('mousemove', function(e) {
  if (!bboxMode || !bboxStart) return;
  if (bboxLayer) map.removeLayer(bboxLayer);
  bboxLayer = L.rectangle([bboxStart, e.latlng], {
    color: '#e67e22', weight: 2, dashArray: '6,4', fillOpacity: 0.08
  }).addTo(map);
});

map.on('mouseup', function(e) {
  if (!bboxMode || !bboxStart) return;
  var end = e.latlng;
  var s = Math.min(bboxStart.lat, end.lat);
  var n = Math.max(bboxStart.lat, end.lat);
  var w = Math.min(bboxStart.lng, end.lng);
  var ea = Math.max(bboxStart.lng, end.lng);
  bboxStart = null;
  setBboxMode(false);
  if (backend) backend.on_bbox_drawn(s, w, n, ea);
});

/* ---- Track display ---- */
function showTracks(jsonStr) {
  trackLayers.forEach(function(l) { map.removeLayer(l); });
  trackLayers = [];
  var tracks = JSON.parse(jsonStr);
  var allLatLng = [];
  tracks.forEach(function(t, i) {
    var color = t.color || '#4fc3f7';
    var latlngs = t.nodes.map(function(n) { return [n[0], n[1]]; });
    allLatLng = allLatLng.concat(latlngs);
    var pl = L.polyline(latlngs, {color: color, weight: 3, opacity: 0.85});
    pl.addTo(map);
    trackLayers.push(pl);
  });
  if (allLatLng.length > 0) {
    map.fitBounds(allLatLng, {padding: [20, 20]});
  }
}

function highlightTrack(idx) {
  trackLayers.forEach(function(l, i) {
    var base = l.options._baseColor || l.options.color;
    l.options._baseColor = base;
    if (idx < 0) {
      l.setStyle({color: base, weight: 3, opacity: 0.85});
    } else if (i === idx) {
      l.setStyle({color: '#ffeb3b', weight: 5, opacity: 1.0});
      l.bringToFront();
    } else {
      l.setStyle({color: base, weight: 2, opacity: 0.4});
    }
  });
}

function clearBbox() {
  if (bboxLayer) { map.removeLayer(bboxLayer); bboxLayer = null; }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Python ↔ JS bridge object
# ---------------------------------------------------------------------------

class MapBridge(QObject):
    bbox_drawn  = Signal(float, float, float, float)  # s, w, n, e
    map_clicked = Signal(float, float)
    ready       = Signal()

    @Slot(float, float, float, float)
    def on_bbox_drawn(self, s: float, w: float, n: float, e: float):
        self.bbox_drawn.emit(s, w, n, e)

    @Slot(float, float)
    def on_map_clicked(self, lat: float, lon: float):
        self.map_clicked.emit(lat, lon)

    @Slot()
    def on_ready(self):
        self.ready.emit()


# ---------------------------------------------------------------------------
# MapWidget
# ---------------------------------------------------------------------------

class MapWidget(QWidget):
    bbox_drawn = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._map_ready = False
        self._js_queue: deque[str] = deque()
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self._bbox_btn = QPushButton("✏  Draw bbox")
        self._bbox_btn.setCheckable(True)
        self._bbox_btn.setToolTip("Click and drag on the map to draw a search bounding box")
        self._bbox_btn.clicked.connect(self._toggle_bbox_mode)
        toolbar.addWidget(self._bbox_btn)
        layout.addWidget(toolbar)

        # WebEngine
        self._view = QWebEngineView()
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Channel + bridge
        self._bridge = MapBridge()
        self._channel = QWebChannel()
        self._channel.registerObject("backend", self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._bridge.ready.connect(self._on_map_ready)
        self._bridge.bbox_drawn.connect(self._on_bbox_drawn_internal)

        self._view.setHtml(MAP_HTML, QUrl("qrc:///"))
        layout.addWidget(self._view)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_map_ready(self):
        self._map_ready = True
        while self._js_queue:
            self._view.page().runJavaScript(self._js_queue.popleft())

    def _run_js(self, js: str):
        if self._map_ready:
            self._view.page().runJavaScript(js)
        else:
            self._js_queue.append(js)

    def _toggle_bbox_mode(self, checked: bool):
        self._run_js(f"setBboxMode({'true' if checked else 'false'})")
        if not checked:
            self._run_js("clearBbox()")

    def _on_bbox_drawn_internal(self, s: float, w: float, n: float, e: float):
        self._bbox_btn.setChecked(False)
        self.bbox_drawn.emit(s, w, n, e)

    # ------------------------------------------------------------------
    # Public API (called from App)
    # ------------------------------------------------------------------

    def show_tracks(self, tracks):
        payload = []
        for i, t in enumerate(tracks):
            payload.append({
                "nodes": [[n[0], n[1]] for n in t.nodes],
                "color": TRACK_COLORS[i % len(TRACK_COLORS)],
                "name": t.name,
            })
        js = f"showTracks({json.dumps(payload)})"
        self._run_js(js)

    def highlight_track(self, idx: int):
        self._run_js(f"highlightTrack({idx})")

    def set_bbox_mode(self, enabled: bool):
        self._bbox_btn.setChecked(enabled)
        self._run_js(f"setBboxMode({'true' if enabled else 'false'})")

    def clear_bbox(self):
        self._run_js("clearBbox()")
        self._bbox_btn.setChecked(False)
