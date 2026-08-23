"""
Persistent deviation-metrics chip — lives in the status bar's permanent
widget area so it stays visible across Refine, Stations, Cross-section and
Export (previously these numbers vanished once Consolidate/Refine was left
behind; only the element table and Step 5's own panel showed them, and both
disappear once those steps are no longer on screen).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from gui import style_kit as sk
from gui.style_kit import ThemedPanel


class MetricsChip(ThemedPanel, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
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
            return
        self.setVisible(True)
        self._label.setText(
            f"Δmean {metrics.get('mean_deviation', 0.0):.2f} m  ·  "
            f"RMSE {metrics.get('rmse', 0.0):.2f} m  ·  "
            f"Δmax {metrics.get('max_deviation', 0.0):.2f} m  ·  "
            f"C1 {metrics.get('max_heading_jump_deg', 0.0):.3f}°  ·  "
            f"{metrics.get('n_elements', 0)} el."
        )
        tip = "Deviation vs the OSM baseline polyline"
        if n_points:
            tip += f" ({n_points} points)"
        if level:
            tip += f" — {level}"
        self._label.setToolTip(tip)

    def clear(self):
        self.setVisible(False)
