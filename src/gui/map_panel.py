"""
Map panel — displays the railway route and allows bounding box drawing.
Rubber-band bbox: left-click-drag to draw, live rectangle on the canvas.
"""

import tkinter as tk
from typing import Callable, Optional
import customtkinter as ctk

try:
    from tkintermapview import TkinterMapView
    MAP_AVAILABLE = True
except ImportError:
    MAP_AVAILABLE = False

TRACK_COLOURS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12",
                 "#9b59b6", "#1abc9c", "#e67e22", "#34495e"]
HIGHLIGHT_COLOUR = "#f1c40f"


class MapPanel(ctk.CTkFrame):
    def __init__(self, parent, on_bbox: Callable, on_find_railways: Callable):
        super().__init__(parent)
        self._on_bbox = on_bbox
        self._on_find_railways = on_find_railways

        # Bbox state
        self._bbox_mode = False
        self._drag_start_canvas: Optional[tuple[int, int]] = None
        self._drag_rect_id: Optional[int] = None
        self._current_bbox: Optional[tuple[float, float, float, float]] = None

        # Map overlay objects
        self._bbox_polygon = None
        self._bbox_markers: list = []
        self._track_paths: list = []
        self._highlighted_idx: Optional[int] = None

        # Saved canvas bindings (restored on bbox mode exit)
        self._saved_press = None
        self._saved_motion = None
        self._saved_release = None

        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(toolbar, text="Map", font=("Helvetica", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=5
        )

        self._hint_var = tk.StringVar(value="")
        ctk.CTkLabel(toolbar, textvariable=self._hint_var,
                     text_color="#f39c12", font=("Helvetica", 10, "italic")).grid(
            row=0, column=1, sticky="w", padx=6
        )

        self._bbox_btn = ctk.CTkButton(
            toolbar, text="Draw BBox", width=110, command=self._toggle_bbox_mode
        )
        self._bbox_btn.grid(row=0, column=2, padx=5)

        ctk.CTkButton(
            toolbar, text="Clear BBox", width=90,
            fg_color="gray", hover_color="#555",
            command=self._clear_bbox,
        ).grid(row=0, column=3, padx=(0, 5))

        if MAP_AVAILABLE:
            self._map = TkinterMapView(self, corner_radius=6)
            self._map.grid(row=1, column=0, sticky="nsew", padx=5, pady=(4, 0))
            self._map.set_position(50.0, 15.5)
            self._map.set_zoom(7)
        else:
            ctk.CTkLabel(self, text="Map unavailable.\nInstall tkintermapview.",
                         text_color="gray").grid(row=1, column=0, sticky="nsew")

        # "Find railways in bbox" button — appears after bbox is drawn
        self._find_btn = ctk.CTkButton(
            self,
            text="Find railway lines in this bbox",
            height=36,
            font=("Helvetica", 13, "bold"),
            fg_color="#e67e22", hover_color="#d35400",
            state="disabled",
            command=self._do_find_railways,
        )
        self._find_btn.grid(row=2, column=0, sticky="ew", padx=5, pady=(4, 5))

    # ------------------------------------------------------------------
    # BBox rubber-band drawing
    # ------------------------------------------------------------------

    def _toggle_bbox_mode(self):
        if self._bbox_mode:
            self._exit_bbox_mode(cancel=True)
        else:
            self._enter_bbox_mode()

    def _enter_bbox_mode(self):
        if not MAP_AVAILABLE:
            return
        self._bbox_mode = True
        self._drag_start_canvas = None
        self._bbox_btn.configure(text="Cancel BBox",
                                 fg_color="#e67e22", hover_color="#d35400")
        self._hint_var.set("Click and drag on the map to draw a bounding box.")

        canvas = self._map.canvas
        # Save existing pan bindings by reference to the map widget methods
        self._saved_press = canvas.bind("<ButtonPress-1>")
        self._saved_motion = canvas.bind("<B1-Motion>")
        self._saved_release = canvas.bind("<ButtonRelease-1>")

        canvas.bind("<ButtonPress-1>", self._bbox_press)
        canvas.bind("<B1-Motion>", self._bbox_drag)
        canvas.bind("<ButtonRelease-1>", self._bbox_release)

    def _exit_bbox_mode(self, cancel: bool = False):
        self._bbox_mode = False
        self._bbox_btn.configure(text="Draw BBox",
                                 fg_color=["#3B8ED0", "#1F6AA5"],
                                 hover_color=["#2a7bbf", "#195a9e"])
        if cancel:
            self._hint_var.set("")

        if not MAP_AVAILABLE:
            return

        canvas = self._map.canvas
        # Remove rubber-band rect if still on canvas
        if self._drag_rect_id is not None:
            try:
                canvas.delete(self._drag_rect_id)
            except Exception:
                pass
            self._drag_rect_id = None

        # Restore original pan bindings via the map widget's own methods
        try:
            canvas.bind("<ButtonPress-1>", self._map.mouse_click)
            canvas.bind("<B1-Motion>", self._map.mouse_move)
            canvas.bind("<ButtonRelease-1>", self._map.mouse_release)
        except Exception:
            # Fallback: restore saved Tcl binding strings
            if self._saved_press:
                canvas.tk.eval(f"bind {canvas._w} <ButtonPress-1> {{{self._saved_press}}}")
            if self._saved_motion:
                canvas.tk.eval(f"bind {canvas._w} <B1-Motion> {{{self._saved_motion}}}")
            if self._saved_release:
                canvas.tk.eval(f"bind {canvas._w} <ButtonRelease-1> {{{self._saved_release}}}")

    def _bbox_press(self, event):
        self._drag_start_canvas = (event.x, event.y)
        if self._drag_rect_id is not None:
            try:
                self._map.canvas.delete(self._drag_rect_id)
            except Exception:
                pass
            self._drag_rect_id = None

    def _bbox_drag(self, event):
        if self._drag_start_canvas is None:
            return
        x0, y0 = self._drag_start_canvas
        if self._drag_rect_id is not None:
            try:
                self._map.canvas.delete(self._drag_rect_id)
            except Exception:
                pass
        self._drag_rect_id = self._map.canvas.create_rectangle(
            x0, y0, event.x, event.y,
            outline="#e67e22", width=2, dash=(6, 3),
            tags="bbox_rubber",
        )

    def _bbox_release(self, event):
        if self._drag_start_canvas is None:
            return
        x0, y0 = self._drag_start_canvas
        x1, y1 = event.x, event.y

        # Ignore tiny drags (accidental clicks)
        if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
            self._exit_bbox_mode(cancel=False)
            return

        try:
            lat0, lon0 = self._map.convert_canvas_coords_to_decimal_coords(x0, y0)
            lat1, lon1 = self._map.convert_canvas_coords_to_decimal_coords(x1, y1)
        except Exception:
            self._exit_bbox_mode(cancel=True)
            return

        south = min(lat0, lat1)
        north = max(lat0, lat1)
        west = min(lon0, lon1)
        east = max(lon0, lon1)
        self._current_bbox = (south, west, north, east)

        self._exit_bbox_mode(cancel=False)
        self._hint_var.set(
            f"Bbox: S {south:.4f}  W {west:.4f}  N {north:.4f}  E {east:.4f}"
        )
        self._draw_bbox_polygon(south, west, north, east)
        self._find_btn.configure(state="normal")
        self._on_bbox((south, west, north, east))

    # ------------------------------------------------------------------
    # Bbox polygon overlay
    # ------------------------------------------------------------------

    def _draw_bbox_polygon(self, south, west, north, east):
        if not MAP_AVAILABLE:
            return
        self._clear_bbox_overlay()
        try:
            self._bbox_polygon = self._map.set_polygon(
                [(south, west), (south, east), (north, east), (north, west)],
                fill_color=None,
                outline_color="#e67e22",
                border_width=2,
                name="bbox",
            )
        except Exception:
            pass
        try:
            self._bbox_markers.append(
                self._map.set_marker(south, west, text="SW",
                                     marker_color_circle="#e67e22",
                                     marker_color_outside="#f39c12")
            )
            self._bbox_markers.append(
                self._map.set_marker(north, east, text="NE",
                                     marker_color_circle="#e67e22",
                                     marker_color_outside="#f39c12")
            )
        except Exception:
            pass

    def _clear_bbox_overlay(self):
        if self._bbox_polygon:
            try:
                self._bbox_polygon.delete()
            except Exception:
                pass
            self._bbox_polygon = None
        for m in self._bbox_markers:
            try:
                m.delete()
            except Exception:
                pass
        self._bbox_markers.clear()

    def _clear_bbox(self):
        self._exit_bbox_mode(cancel=True)
        self._clear_bbox_overlay()
        self._current_bbox = None
        self._find_btn.configure(state="disabled")
        self._hint_var.set("")

    # ------------------------------------------------------------------
    # Find railways in bbox
    # ------------------------------------------------------------------

    def _do_find_railways(self):
        if self._current_bbox:
            self._on_find_railways(self._current_bbox)

    # ------------------------------------------------------------------
    # Track display & highlighting
    # ------------------------------------------------------------------

    def show_tracks(self, tracks):
        if not MAP_AVAILABLE:
            return
        self._clear_tracks()
        all_coords = []
        for i, track in enumerate(tracks):
            coords = track.nodes
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
        self._highlighted_idx = idx
        for i, path in enumerate(self._track_paths):
            if path is None:
                continue
            try:
                if idx is None:
                    colour = TRACK_COLOURS[i % len(TRACK_COLOURS)]
                elif i == idx:
                    colour = HIGHLIGHT_COLOUR
                else:
                    colour = "#888888"
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
    # Helpers
    # ------------------------------------------------------------------

    def _fit_view(self, coords: list[tuple]):
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        self._map.set_position((min(lats) + max(lats)) / 2,
                               (min(lons) + max(lons)) / 2)
        span = max(max(lats) - min(lats), max(lons) - min(lons))
        self._map.set_zoom(max(5, min(14, int(8 - span * 1.5))))
