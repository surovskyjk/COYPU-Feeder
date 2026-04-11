"""
Main application window — PySide6.
3-column layout: StepSidebar | MapWidget | QStackedWidget (step panels).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget, QSizePolicy, QMessageBox,
)
from PySide6.QtCore import Qt

from .map_widget import MapWidget
from .step_sidebar import StepSidebar
from .steps.step1_find import Step1Find
from .steps.step2_section import Step2Section
from .steps.step3_configure import Step3Configure
from .steps.step4_export import Step4Export


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Coypu-Feeder — OSM Railway to LandXML")
        self.resize(1340, 820)
        self.setMinimumSize(1050, 680)

        self._tracks = []
        self._selected_tracks = []
        self._settings: dict = {}
        self._bbox_workers: list = []

        self._build_layout()
        self._wire_signals()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # --- Left sidebar (200 px) ---
        self.sidebar = StepSidebar()
        self.sidebar.setFixedWidth(200)
        h.addWidget(self.sidebar)

        # --- Map (flex) ---
        self.map_widget = MapWidget()
        self.map_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        h.addWidget(self.map_widget, stretch=1)

        # --- Right step stack (340 px) ---
        self.stack = QStackedWidget()
        self.stack.setFixedWidth(340)

        self.step1 = Step1Find()
        self.step2 = Step2Section()
        self.step3 = Step3Configure()
        self.step4 = Step4Export()

        self.stack.addWidget(self.step1)   # index 0
        self.stack.addWidget(self.step2)   # index 1
        self.stack.addWidget(self.step3)   # index 2
        self.stack.addWidget(self.step4)   # index 3

        h.addWidget(self.stack)

        self.statusBar().showMessage("Ready. Draw a bbox on the map or search in the panel.")

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self):
        # Sidebar back-navigation
        self.sidebar.step_clicked.connect(self._goto_step)

        # Bbox drawn on map → update Step 1 AND switch to it so user sees the search button
        self.map_widget.bbox_drawn.connect(self.step1.set_bbox)
        self.map_widget.bbox_drawn.connect(self._on_bbox_drawn)

        # "Search Railways in Bbox" button on map toolbar → direct search
        self.map_widget.bbox_search_requested.connect(self._on_bbox_search_from_map)

        # Step 1 → load railway
        self.step1.railway_fetched.connect(self._on_railway_fetched)

        # Step 2 → confirm section + map highlight
        self.step2.section_confirmed.connect(self._on_section_confirmed)
        self.step2.highlight_changed.connect(self.map_widget.highlight_track)

        # Step 3 → confirm config
        self.step3.config_confirmed.connect(self._on_config_confirmed)

        # Step 4 → export done / start over
        self.step4.export_finished.connect(self._on_export_finished)
        self.step4.start_over_requested.connect(self._start_over)

    # ------------------------------------------------------------------
    # Step transitions
    # ------------------------------------------------------------------

    def _goto_step(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_step(idx)

    def _on_bbox_drawn(self, s: float, w: float, n: float, e: float):
        """Switch to Step 1 after bbox drawn so user sees the enabled search button."""
        self._goto_step(0)
        self.statusBar().showMessage(
            f"Bbox drawn ({s:.3f},{w:.3f} → {n:.3f},{e:.3f}). "
            "Click '🔍 Search Railways in Bbox' on the map toolbar, or use the panel."
        )

    def _on_bbox_search_from_map(self, s: float, w: float, n: float, e: float):
        """Direct bbox search triggered from the map toolbar button."""
        from gui.worker import SearchWorker
        self.statusBar().showMessage("Searching for railway lines in bbox…")
        worker = SearchWorker("bbox", (s, w, n, e), self)
        worker.results_ready.connect(self._on_bbox_results_ready)
        worker.failed.connect(lambda err: QMessageBox.critical(self, "Bbox search failed", err))
        worker.failed.connect(lambda: self.statusBar().showMessage("Bbox search failed."))
        worker.finished.connect(
            lambda: self._bbox_workers.remove(worker)
            if worker in self._bbox_workers else None
        )
        self._bbox_workers.append(worker)
        worker.start()

    def _on_bbox_results_ready(self, results: list):
        self.step1.populate_results(results)
        self._goto_step(0)
        n = len(results)
        self.statusBar().showMessage(
            f"Found {n} railway line{'s' if n != 1 else ''} in bbox. "
            "Click a result to load it."
        )

    def _on_railway_fetched(self, overpass_data, relation_info: dict):
        from osm.parser import parse_tracks
        self._tracks = parse_tracks(overpass_data)
        if not self._tracks:
            QMessageBox.warning(
                self, "No tracks found",
                "The relation was fetched but no track segments could be extracted.\n"
                "The relation may contain no ways, or its ways are not connected."
            )
            self.statusBar().showMessage("No tracks found in the fetched relation.")
            return
        self.map_widget.clear_alignment()
        self.map_widget.show_tracks(self._tracks)
        self.step2.populate(self._tracks)
        n = len(self._tracks)
        self.statusBar().showMessage(
            f"Loaded '{relation_info.get('name', '')}' — "
            f"{n} track{'s' if n != 1 else ''}. Select tracks and click Next."
        )
        self._goto_step(1)

    def _on_section_confirmed(self, selected_tracks: list):
        self._selected_tracks = selected_tracks
        self.statusBar().showMessage(
            f"{len(selected_tracks)} track(s) selected. Configure export settings."
        )
        self._goto_step(2)

    def _on_config_confirmed(self, settings: dict):
        self._settings = settings
        self.step4.prepare(self._selected_tracks, settings)
        self.statusBar().showMessage("Settings confirmed. Choose a file and click 'Start Export'.")
        self._goto_step(3)

    # ------------------------------------------------------------------
    # Export + alignment display
    # ------------------------------------------------------------------

    def _on_export_finished(self, filepath: str, work_epsg: int):
        """Parse the exported LandXML and draw the alignment back on the map."""
        self.statusBar().showMessage(f"Export complete: {filepath}")
        try:
            force_positive = self._settings.get("force_positive", False)
            alignments = self._parse_alignment_for_map(filepath, work_epsg, force_positive)
            if alignments:
                self.map_widget.show_alignment(alignments)
                self.statusBar().showMessage(
                    f"Export complete — alignment drawn on map (dashed pink). {filepath}"
                )
        except Exception as exc:
            # Don't let a display failure mask a successful export
            self.statusBar().showMessage(
                f"Export complete (map display failed: {exc}). {filepath}"
            )

    def _parse_alignment_for_map(
        self,
        filepath: str,
        work_epsg: int,
        force_positive: bool,
    ) -> list[list[tuple[float, float]]]:
        """
        Read a LandXML file, extract element Start/End coordinates,
        optionally undo the force-positive abs(), and back-project to WGS84.
        Returns: list of (lat, lon) lists, one per Alignment.
        """
        from lxml import etree
        from geometry.projection import projected_to_wgs84

        NS = "http://www.landxml.org/schema/LandXML-1.2"

        tree = etree.parse(filepath)
        alignments_latlon: list[list[tuple[float, float]]] = []

        for aln_el in tree.findall(f".//{{{NS}}}Alignment"):
            coord_geom = aln_el.find(f"{{{NS}}}CoordGeom")
            if coord_geom is None:
                continue

            pts_xy: list[tuple[float, float]] = []
            last_end: tuple[float, float] | None = None

            for el in coord_geom:
                # Start point of each element
                start_el = el.find(f"{{{NS}}}Start")
                if start_el is not None and start_el.text:
                    y_str, x_str = start_el.text.strip().split()
                    x, y = float(x_str), float(y_str)
                    if force_positive:
                        x, y = -abs(x), -abs(y)
                    pts_xy.append((x, y))

                end_el = el.find(f"{{{NS}}}End")
                if end_el is not None and end_el.text:
                    y_str, x_str = end_el.text.strip().split()
                    x, y = float(x_str), float(y_str)
                    if force_positive:
                        x, y = -abs(x), -abs(y)
                    last_end = (x, y)

            if last_end:
                pts_xy.append(last_end)

            if len(pts_xy) >= 2:
                latlon = projected_to_wgs84(pts_xy, work_epsg)
                alignments_latlon.append(latlon)

        return alignments_latlon

    # ------------------------------------------------------------------
    # Start over
    # ------------------------------------------------------------------

    def _start_over(self):
        """Reset all state and return to Step 1 for a new railway search."""
        self._tracks = []
        self._selected_tracks = []
        self._settings = {}
        self.map_widget.clear_all()
        self.sidebar.reset()
        self._goto_step(0)
        self.statusBar().showMessage(
            "Ready. Search for a new railway or draw a bbox on the map."
        )
