"""
Map panel — displays the railway route and allows bounding box drawing.
Provides visual feedback: corner markers, drawn rectangle, per-track colours.
"""

import tkinter as tk
from typing import Callable, Optional
import customtkinter as ctk

try:
    from tkintermapview import TkinterMapView
    MAP_AVAILABLE = True
except ImportError:
    MAP_AVAILABLE = False

# Colours used for each track path (cycles if more than 8 tracks)
TRACK_COLOURS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12",
                 "#9b59b6", "#1abc9c", "#e67e22", "#34495e"]
HIGHLIGHT_COLOUR = "#f1c40f"   # yellow — selected track


class MapPanel(ctk.CTkFrame):
    def __init__(self, parent, on_bbox: Callable):
        super().__init__(parent)
        self._on_bbox = on_bbox

        self._bbox_mode = False
        self._bbox_corner: Optional[tuple] = None   # first corner (lat, lon)
        self._bbox_marker_a = None
        self._bbox_marker_b = None
        self._bbox_polygon = None

        self._track_paths: list = []    # one path widget per track
        self._highlighted_idx: Optional[int] = None

        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(toolbar, text="Map", font=("Helvetica", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=5
        )

        self._bbox_btn = ctk.CTkButton(
            toolbar, text="Draw BBox", width=110, command=self._toggle_bbox_mode
        )
        self._bbox_btn.grid(row=0, column=2, padx=5)

        self._clear_bbox_btn = ctk.CTkButton(
            toolbar, text="Clear BBox", width=90,
            fg_color="gray", hover_color="#555",
            command=self._clear_bbox,
        )
        self._clear_bbox_btn.grid(row=0, column=3, padx=(0, 5))

        # Instruction label (hidden by default)
        self._instruction_var = tk.StringVar(value="")
        self._instruction_label = ctk.CTkLabel(
            self, textvariable=self._instruction_var,
            text_color="#f39c12", font=("Helvetica", 11, "italic"),
        )
        self._instruction_label.grid(row=1, column=0, sticky="n", pady=(4, 0))

        if MAP_AVAILABLE:
            self._map = TkinterMapView(self, corner_radius=6)
            self._map.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
            self.grid_rowconfigure(2, weight=1)
            self._map.set_position(50.0, 15.5)   # central Bohemia default
            self._map.set_zoom(7)
        else:
            ctk.CTkLabel(
                self,
                text="Map unavailable.\nInstall tkintermapview to enable.",
                text_color="gray",
            ).grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
            self.grid_rowconfigure(2, weight=1)

    # ------------------------------------------------------------------
    # BBox drawing
    # ------------------------------------------------------------------

    def _toggle_bbox_mode(self):
        if self._bbox_mode:
            self._exit_bbox_mode()
        else:
            self._enter_bbox_mode()

    def _enter_bbox_mode(self):
        self._bbox_mode = True
        self._bbox_corner = None
        self._bbox_btn.configure(text="Cancel BBox", fg_color="#e67e22", hover_color="#d35400")
        self._instruction_var.set("Click once on the map to set the first corner of the bounding box.")
        if MAP_AVAILABLE:
            self._map.add_left_click_map_command(self._on_map_click)

    def _exit_bbox_mode(self):
        self._bbox_mode = False
        self._bbox_corner = None
        self._bbox_btn.configure(text="Draw BBox", fg_color=["#3B8ED0", "#1F6AA5"],
                                 hover_color=["#2a7bbf", "#195a9e"])
        self._instruction_var.set("")
        if MAP_AVAILABLE:
            self._map.add_left_click_map_command(None)

    def _on_map_click(self, coords):
        if not self._bbox_mode:
            return
        lat, lon = coords

        if self._bbox_corner is None:
            # First corner
            self._bbox_corner = (lat, lon)
            self._instruction_var.set(
                f"First corner set ({lat:.4f}, {lon:.4f}). "
                "Now click the opposite corner."
            )
            if MAP_AVAILABLE:
                self._bbox_marker_a = self._map.set_marker(
                    lat, lon, text="A", marker_color_circle="#e67e22", marker_color_outside="#f39c12"
                )
        else:
            # Second corner — complete the bbox
            lat0, lon0 = self._bbox_corner
            south = min(lat0, lat)
            north = max(lat0, lat)
            west = min(lon0, lon)
            east = max(lon0, lon)

            self._draw_bbox_rect(south, west, north, east)
            self._exit_bbox_mode()
            self._instruction_var.set(
                f"BBox ready: ({south:.4f},{west:.4f}) → ({north:.4f},{east:.4f})"
            )
            self._on_bbox((south, west, north, east))

    def _draw_bbox_rect(self, south, west, north, east):
        """Draw a rectangle polygon on the map for the selected bbox."""
        if not MAP_AVAILABLE:
            return
        # Clear previous
        if self._bbox_polygon:
            try:
                self._bbox_polygon.delete()
            except Exception:
                pass
        if self._bbox_marker_a:
            try:
                self._bbox_marker_a.delete()
            except Exception:
                pass
        if self._bbox_marker_b:
            try:
                self._bbox_marker_b.delete()
            except Exception:
                pass

        corners = [
            (south, west), (south, east),
            (north, east), (north, west),
        ]
        try:
            self._bbox_polygon = self._map.set_polygon(
                corners,
                fill_color=None,
                outline_color="#e67e22",
                border_width=2,
                name="bbox",
            )
        except Exception:
            pass

        # Corner markers
        try:
            self._bbox_marker_a = self._map.set_marker(
                south, west, text="SW", marker_color_circle="#e67e22", marker_color_outside="#f39c12"
            )
            self._bbox_marker_b = self._map.set_marker(
                north, east, text="NE", marker_color_circle="#e67e22", marker_color_outside="#f39c12"
            )
        except Exception:
            pass

    def _clear_bbox(self):
        self._exit_bbox_mode()
        self._instruction_var.set("")
        if not MAP_AVAILABLE:
            return
        for obj in (self._bbox_polygon, self._bbox_marker_a, self._bbox_marker_b):
            if obj:
                try:
                    obj.delete()
                except Exception:
                    pass
        self._bbox_polygon = None
        self._bbox_marker_a = None
        self._bbox_marker_b = None

    # ------------------------------------------------------------------
    # Track display & highlighting
    # ------------------------------------------------------------------

    def show_tracks(self, tracks):
        """Draw each track as a separate coloured path."""
        if not MAP_AVAILABLE:
            return

        self._clear_tracks()
        all_coords = []

        for i, track in enumerate(tracks):
            coords = track.nodes   # list of (lat, lon)
            if len(coords) < 2:
                continue
            colour = TRACK_COLOURS[i % len(TRACK_COLOURS)]
            try:
                path = self._map.set_path(coords, color=colour, width=3)
                self._track_paths.append(path)
            except Exception:
                self._track_paths.append(None)
            all_coords.extend(coords)

        if all_coords:
            self._fit_view(all_coords)

    def highlight_track(self, idx: Optional[int]):
        """
        Highlight one track (by its index in the list) in yellow;
        dim all others. Pass None to reset all to default colours.
        """
        if not MAP_AVAILABLE:
            return
        self._highlighted_idx = idx
        for i, path in enumerate(self._track_paths):
            if path is None:
                continue
            try:
                if idx is None or i == idx:
                    colour = HIGHLIGHT_COLOUR if i == idx else TRACK_COLOURS[i % len(TRACK_COLOURS)]
                else:
                    colour = "#888888"   # dimmed
                path.change_line_color(colour)
            except Exception:
                pass

    def _clear_tracks(self):
        for path in self._track_paths:
            if path:
                try:
                    path.delete()
                except Exception:
                    pass
        self._track_paths.clear()
        self._highlighted_idx = None

    # ------------------------------------------------------------------
    # Legacy entry point (called from app.py after fetching a relation)
    # ------------------------------------------------------------------

    def show_relation(self, overpass_data: dict):
        """Draw raw OSM ways on the map (before parsing into Track objects)."""
        if not MAP_AVAILABLE:
            return

        self._clear_tracks()
        node_index = {
            el["id"]: (el["lat"], el["lon"])
            for el in overpass_data.get("elements", [])
            if el["type"] == "node"
        }

        all_coords = []
        for el in overpass_data.get("elements", []):
            if el["type"] != "way":
                continue
            coords = [node_index[n] for n in el.get("nodes", []) if n in node_index]
            if len(coords) >= 2:
                try:
                    path = self._map.set_path(coords, color=TRACK_COLOURS[0], width=3)
                    self._track_paths.append(path)
                except Exception:
                    self._track_paths.append(None)
                all_coords.extend(coords)

        if all_coords:
            self._fit_view(all_coords)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fit_view(self, coords: list[tuple]):
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        centre_lat = (min(lats) + max(lats)) / 2
        centre_lon = (min(lons) + max(lons)) / 2
        self._map.set_position(centre_lat, centre_lon)
        span = max(max(lats) - min(lats), max(lons) - min(lons))
        zoom = max(5, min(14, int(8 - span * 1.5)))
        self._map.set_zoom(zoom)
