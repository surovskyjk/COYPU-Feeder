"""
Persistent deviation-metrics chip — lives in the status bar's permanent
widget area so it stays visible across Refine, Stations, Cross-section and
Export (previously these numbers vanished once Consolidate/Refine was left
behind; only the element table and Step 5's own panel showed them, and both
disappear once those steps are no longer on screen).

Req 9a: clickable when the metrics carry a `worst_element_id` (the element
with the largest deviation from the OSM baseline) — clicking highlights and
pans to it on the map, exactly like clicking its row in the element table.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from gui import style_kit as sk
from gui.style_kit import ThemedPanel


class MetricsChip(ThemedPanel, QWidget):
    # Emitted on click, only while a worst-deviation element is known.
    metric_clicked = Signal(str)   # element_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worst_element_id: str | None = None
        self._build()
        self.setVisible(False)   # nothing to show before a model exists

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(0)
        self._label = QLabel("")
        self.themed(self._label, sk.value_label)
        layout.addWidget(self._label)

    # ------------------------------------------------------------------

    def set_metrics(self, metrics: dict | None, n_points: int = 0,
                    level: str = ""):
        """metrics: a `metrics_from_stats`-shaped dict, or None/empty to
        hide the chip (no working alignment yet)."""
        if not metrics:
            self.setVisible(False)
            self._worst_element_id = None
            self._update_cursor()
            return
        self.setVisible(True)
        self._label.setText(
            f"Δmean {metrics.get('mean_deviation', 0.0):.2f} m  ·  "
            f"RMSE {metrics.get('rmse', 0.0):.2f} m  ·  "
            f"Δmax {metrics.get('max_deviation', 0.0):.2f} m  ·  "
            f"C1 {metrics.get('max_heading_jump_deg', 0.0):.3f}°  ·  "
            f"{metrics.get('n_elements', 0)} el."
        )
        self._worst_element_id = metrics.get("worst_element_id")
        self._update_cursor()
        tip = "Deviation vs the OSM baseline polyline"
        if n_points:
            tip += f" ({n_points} points)"
        if level:
            tip += f" — {level}"
        if self._worst_element_id:
            tip += f"\nClick to jump to the worst element ({self._worst_element_id})."
        self._label.setToolTip(tip)

    def clear(self):
        self.setVisible(False)
        self._worst_element_id = None
        self._update_cursor()

    def _update_cursor(self):
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if self._worst_element_id
            else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._worst_element_id:
            self.metric_clicked.emit(self._worst_element_id)
        super().mousePressEvent(event)
