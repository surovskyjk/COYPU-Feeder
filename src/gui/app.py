"""
Main application window — PySide6.
3-column layout: StepSidebar | MapWidget | QStackedWidget (step panels).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget, QSizePolicy,
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

        # Status bar
        self.statusBar().showMessage("Ready.")

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self):
        # Sidebar navigation (back to completed step)
        self.sidebar.step_clicked.connect(self._goto_step)

        # Map → step 1 bbox
        self.map_widget.bbox_drawn.connect(self.step1.set_bbox)

        # Step 1 → load railway
        self.step1.railway_fetched.connect(self._on_railway_fetched)

        # Step 2 → confirm section
        self.step2.section_confirmed.connect(self._on_section_confirmed)
        self.step2.highlight_changed.connect(self.map_widget.highlight_track)

        # Step 3 → confirm config
        self.step3.config_confirmed.connect(self._on_config_confirmed)

    # ------------------------------------------------------------------
    # Step transitions
    # ------------------------------------------------------------------

    def _goto_step(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_step(idx)

    def _on_railway_fetched(self, overpass_data, relation_info: dict):
        from osm.parser import parse_tracks
        self._tracks = parse_tracks(overpass_data)
        self.map_widget.show_tracks(self._tracks)
        self.step2.populate(self._tracks)
        n = len(self._tracks)
        self.statusBar().showMessage(
            f"Loaded {n} track{'s' if n != 1 else ''}. "
            "Select tracks and click Next."
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
        self.statusBar().showMessage("Settings confirmed. Ready to export.")
        self._goto_step(3)
