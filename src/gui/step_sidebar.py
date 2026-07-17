"""
9-step numbered sidebar, always visible.

Steps *suggest* an order rather than gating it: any step whose prerequisites
are satisfied can be clicked at any time (including going back to Refine after
consolidating or exporting). Locked steps explain why in their tooltip.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QFrame, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont


STEPS = [
    ("1", "Find Railway"),
    ("2", "Select Section"),
    ("3", "Configure"),
    ("4", "Candidates"),
    ("5", "Refine"),
    ("6", "Consolidate"),
    ("7", "Stations"),
    ("8", "Cross-Section"),
    ("9", "Export"),
]


class StepButton(QPushButton):
    def __init__(self, number: str, label: str, parent=None):
        super().__init__(parent)
        self._number = number
        self._label = label
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(46)
        self._set_style("inactive")

    def set_state(self, state: str):
        """state: 'inactive' | 'active' | 'done' | 'available' | 'suggested'"""
        self._set_style(state)

    def _set_style(self, state: str):
        base = "border-radius:6px; text-align:left; padding:6px 10px;"
        prefix = "  "
        if state == "active":
            bg = "background:#2a82da; color:#fff; font-weight:bold;"
        elif state == "done":
            bg = "background:#2e5e2e; color:#8bc34a;"
        elif state == "suggested":
            # Reachable and the natural next move — accented outline
            bg = ("background:#3c3c3f; color:#e3f2fd;"
                  "border:1px solid #2a82da;")
            prefix = "▸ "
        elif state == "available":
            bg = "background:#3c3c3f; color:#cfd8dc;"
        else:   # inactive / locked
            bg = "background:#333336; color:#6b6b70;"
        self.setStyleSheet(f"QPushButton {{ {base} {bg} }}")
        self.setText(f"{prefix}{self._number}  {self._label}")


class StepSidebar(QWidget):
    step_clicked = Signal(int)  # 0-based step index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = 0
        self._buttons: list[StepButton] = []
        self._available: set[int] = {0}
        self._done: set[int] = set()
        self._suggested: int | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(5)

        title = QLabel("COYPU Feeder")
        title.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:#2a82da; margin-bottom:6px;")
        layout.addWidget(title)

        sub = QLabel("OSM → LandXML")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#666; font-size:10px; margin-bottom:8px;")
        layout.addWidget(sub)

        for i, (num, label) in enumerate(STEPS):
            btn = StepButton(num, label)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_clicked(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        hint = QLabel("Steps are suggestions —\nany unlocked step is clickable.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#555; font-size:9px; margin-top:6px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        self._update_styles()

    def _on_clicked(self, idx: int):
        # Gate on availability, not on how far the user has walked before.
        if idx in self._available:
            self.step_clicked.emit(idx)

    def _update_styles(self):
        for i, btn in enumerate(self._buttons):
            if i == self._current:
                btn.set_state("active")
            elif i in self._done:
                btn.set_state("done")
            elif i == self._suggested and i in self._available:
                btn.set_state("suggested")
            elif i in self._available:
                btn.set_state("available")
            else:
                btn.set_state("inactive")
            btn.setEnabled(i in self._available or i == self._current)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_step(self, idx: int):
        self._current = idx
        self._update_styles()

    def set_states(self, available: set, done: set,
                   suggested: int | None = None, reasons: dict | None = None):
        """
        available : step indices the user may open now
        done      : steps already completed (green)
        suggested : the natural next step (accented, never forced)
        reasons   : {idx: text} shown as the tooltip of a locked step
        """
        self._available = set(available)
        self._done = set(done)
        self._suggested = suggested
        reasons = reasons or {}
        for i, btn in enumerate(self._buttons):
            if i in self._available:
                btn.setToolTip(f"Go to step {i + 1}")
            else:
                btn.setToolTip(reasons.get(i, "Not available yet."))
        self._update_styles()

    def reset(self):
        self._current = 0
        self._available = {0}
        self._done = set()
        self._suggested = None
        self._update_styles()
