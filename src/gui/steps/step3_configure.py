"""
Step 3 — Configure geometry settings.
CRS / output projection is chosen later in Step 7 (Export).

The three alignment levels computed in Step 4 use:
  • Max deviation   → Douglas-Peucker tolerance for the tangent polygon (PIs)
  • Min curve radius → radius floor for all circular arcs
  • Spiral length   → clothoid length for Level 3
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QDoubleSpinBox, QScrollArea, QFormLayout, QGroupBox,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont


class Step3Configure(QWidget):
    config_confirmed = Signal(dict)
    back_requested   = Signal()   # user wants to go back to Select Section

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)

        # ── Project ──────────────────────────────────────────────────
        proj_group = QGroupBox("Project")
        pf = QFormLayout(proj_group)
        self._project_edit = QLineEdit()
        self._project_edit.setPlaceholderText("Railway Alignment")
        pf.addRow("Project name:", self._project_edit)
        v.addWidget(proj_group)

        # ── Alignment Geometry ────────────────────────────────────────
        geo_group = QGroupBox("Alignment Geometry")
        geo_group.setToolTip(
            "Parameters for the Level 2 / Level 3 alignment construction.\n"
            "Level 1 (raw OSM polyline) has no parameters."
        )
        gf = QFormLayout(geo_group)

        self._max_dev_spin = QDoubleSpinBox()
        self._max_dev_spin.setRange(0.1, 10.0)
        self._max_dev_spin.setSingleStep(0.1)
        self._max_dev_spin.setValue(1.0)
        self._max_dev_spin.setSuffix(" m")
        self._max_dev_spin.setToolTip(
            "Douglas-Peucker tolerance used to extract the tangent polygon\n"
            "(the Points of Intersection) from the OSM polyline.\n\n"
            "  0.5 m — hugs the OSM data, many PIs / short elements\n"
            "  1.0 m — balanced (default)\n"
            "  3.0 m — generalises heavily, few PIs / long elements\n\n"
            "Step 4 automatically tries 0.5× / 1× / 2.5× of this value and\n"
            "keeps the best result per level."
        )
        gf.addRow("Max deviation (PI tolerance):", self._max_dev_spin)

        self._min_radius_spin = QDoubleSpinBox()
        self._min_radius_spin.setRange(50.0, 10000.0)
        self._min_radius_spin.setSingleStep(25.0)
        self._min_radius_spin.setDecimals(0)
        self._min_radius_spin.setValue(150.0)
        self._min_radius_spin.setSuffix(" m")
        self._min_radius_spin.setToolTip(
            "Minimum horizontal curve radius for all circular arcs.\n"
            "Mainline railway: ≥ 300 m  |  Secondary line: ≥ 150 m\n"
            "Tramway / light rail: 50–100 m  |  High-speed rail: ≥ 1500 m\n\n"
            "Note: where two PIs are so close that even this radius cannot\n"
            "fit tangentially, a smaller radius is used — C1 continuity\n"
            "always wins over the radius floor."
        )
        gf.addRow("Minimum curve radius:", self._min_radius_spin)

        self._spiral_length_spin = QDoubleSpinBox()
        self._spiral_length_spin.setRange(0.0, 200.0)
        self._spiral_length_spin.setSingleStep(5.0)
        self._spiral_length_spin.setDecimals(0)
        self._spiral_length_spin.setValue(20.0)
        self._spiral_length_spin.setSuffix(" m")
        self._spiral_length_spin.setSpecialValueText("Disabled")
        self._spiral_length_spin.setToolTip(
            "Length of each entry/exit clothoid spiral (Level 3 only).\n"
            "Automatically shortened where tangents are too short;\n"
            "curves where no spiral fits fall back to a plain arc.\n"
            "Typical mainline railway: 20–80 m."
        )
        gf.addRow("Spiral length (Level 3):", self._spiral_length_spin)

        v.addWidget(geo_group)

        # ── Export Settings ───────────────────────────────────────────
        exp_group = QGroupBox("Export Settings")
        exp_group.setToolTip(
            "These settings control the LandXML output — elevation sampling\n"
            "density and vertical curve shape. They do not affect the\n"
            "horizontal geometry."
        )
        ef = QFormLayout(exp_group)

        self._sample_spin = QDoubleSpinBox()
        self._sample_spin.setRange(1.0, 500.0)
        self._sample_spin.setSingleStep(5.0)
        self._sample_spin.setValue(20.0)
        self._sample_spin.setSuffix(" m")
        self._sample_spin.setToolTip(
            "Distance between elevation sampling points along the alignment.\n"
            "Smaller = more elevation detail in the LandXML output."
        )
        ef.addRow("Elevation sample interval:", self._sample_spin)

        self._vc_spin = QDoubleSpinBox()
        self._vc_spin.setRange(10.0, 2000.0)
        self._vc_spin.setSingleStep(10.0)
        self._vc_spin.setValue(100.0)
        self._vc_spin.setSuffix(" m")
        self._vc_spin.setToolTip(
            "Length of fitted parabolic vertical curves (sags and crests)\n"
            "in the LandXML output.  Mainline: 100–500 m."
        )
        ef.addRow("Vertical curve length:", self._vc_spin)

        v.addWidget(exp_group)

        v.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Navigation row (outside scroll)
        nav_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(80)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →  Candidates")
        self._next_btn.setMinimumHeight(38)
        self._next_btn.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
        self._next_btn.clicked.connect(self._on_next)
        nav_row.addWidget(self._next_btn)
        outer.addLayout(nav_row)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_next(self):
        self.config_confirmed.emit({
            "project_name":    self._project_edit.text().strip() or "Railway Alignment",
            "sample_interval": self._sample_spin.value(),
            "vc_length":       self._vc_spin.value(),
            "max_deviation":   self._max_dev_spin.value(),
            "min_radius":      self._min_radius_spin.value(),
            "spiral_length":   self._spiral_length_spin.value(),
            # Defaults for keys still referenced by legacy code paths
            # (old algorithms remain callable from CandidateGenerator).
            "smooth_window":      21,
            "merge_radius_pct":   15.0,
            "time_budget_s":      60.0,
            "division_length":    500.0,
            "min_tangent_length": 30.0,
            "min_kappa_radius":   0.0,
            "min_kappa_length":   200.0,
            "check_interval":     5.0,
            "min_line_length":    10.0,
            "min_arc_length":     10.0,
            "min_spiral_length":  10.0,
        })
