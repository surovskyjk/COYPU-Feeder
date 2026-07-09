"""
Step 5 — Refine Alignment.

The selected level is shown on the map with per-element colouring and the
full element table in the dock below the map. Editing (curve radius,
spiral length, omitting PIs, merging spirals) happens in that table; this
panel shows the summary metrics and carries the Back / Accept navigation.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont


class Step5Refine(QWidget):
    refinement_done = Signal(list)   # final list[dict] elements
    back_requested  = Signal()       # user wants to go back to candidates

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidate = None
        self._working_elements: list[dict] = []
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._title_lbl = QLabel("Refine Alignment")
        self._title_lbl.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        outer.addWidget(self._title_lbl)

        self._algo_lbl = QLabel("")
        self._algo_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        outer.addWidget(self._algo_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555;")
        outer.addWidget(sep)

        info = QLabel(
            "All elements are listed in the table below the map:\n\n"
            "• Edit an arc Radius or a Spiral L value — the alignment\n"
            "  rebuilds and the map updates automatically.\n"
            "• Omit PI removes a curve (neighbours absorb the deflection);\n"
            "  omitted PIs can be restored at the bottom of the table.\n"
            "• Short straights between two curves offer 'Merge spirals' —\n"
            "  the straight is replaced by prolonged, symmetric spirals.\n"
            "• Click a table row to highlight that element on the map, or\n"
            "  click an element directly on the map to select its row;\n"
            "  Ctrl+click on the map multi-selects (e.g. pick the first and\n"
            "  last tangent for 'Merge PI range'). Hover an element for 3 s\n"
            "  to see its statistics.\n\n"
            "Points of Intersection are shown as markers; the dashed grey\n"
            "stubs are the virtual tangent extensions toward each PI."
        )
        info.setStyleSheet("color: #999; font-size: 10px;")
        info.setWordWrap(True)
        outer.addWidget(info)

        # Metrics
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet(
            "QFrame { background: #2a2a2e; border-radius: 4px; }"
        )
        mf = QVBoxLayout(metrics_frame)
        mf.setContentsMargins(8, 6, 8, 6)
        self._metrics_lbl = QLabel("No candidate selected.")
        self._metrics_lbl.setStyleSheet("color: #888; font-size: 10px;")
        mf.addWidget(self._metrics_lbl)
        outer.addWidget(metrics_frame)

        outer.addStretch()

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(70)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._accept_btn = QPushButton("Accept →")
        self._accept_btn.setMinimumHeight(36)
        self._accept_btn.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        self._accept_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: #fff; border-radius: 4px; "
            "font-weight: bold; padding: 6px 12px; }"
            "QPushButton:hover { background: #2ecc71; }"
            "QPushButton:disabled { background: #444; color: #777; }"
        )
        self._accept_btn.setEnabled(False)
        self._accept_btn.clicked.connect(self._on_accept)
        nav_row.addWidget(self._accept_btn)

        outer.addLayout(nav_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, candidate, xy, chainages, settings: dict):
        """Called by App when the user selects a candidate in Step 4."""
        self._candidate = candidate
        self._working_elements = list(getattr(candidate, "elements", []) or [])
        label = getattr(candidate, "label", "Unknown")
        self._title_lbl.setText(f"Refine: {label}")
        editable = getattr(candidate, "pi_model", None) is not None
        self._algo_lbl.setText(
            f"{label}" + ("" if editable else "  (read-only — no PI model)"))
        self._accept_btn.setEnabled(len(self._working_elements) > 0)
        self._refresh_metrics({
            "max_deviation":        getattr(candidate, "max_deviation", 0.0),
            "rmse":                 getattr(candidate, "rmse", 0.0),
            "max_heading_jump_deg": getattr(candidate, "max_heading_jump_deg", 0.0),
        })

    def set_elements(self, elements: list, metrics: dict | None = None):
        """Called by App after every table-driven rebuild."""
        self._working_elements = list(elements or [])
        self._accept_btn.setEnabled(len(self._working_elements) > 0)
        if metrics:
            self._refresh_metrics(metrics)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_metrics(self, metrics: dict):
        n = len(self._working_elements)
        self._metrics_lbl.setText(
            f"Elements: {n}    "
            f"Max deviation: {metrics.get('max_deviation', 0.0):.2f} m\n"
            f"RMSE: {metrics.get('rmse', 0.0):.2f} m    "
            f"C1 mismatch: {metrics.get('max_heading_jump_deg', 0.0):.3f}°"
        )

    def _on_accept(self):
        if self._working_elements:
            self.refinement_done.emit(self._working_elements)
