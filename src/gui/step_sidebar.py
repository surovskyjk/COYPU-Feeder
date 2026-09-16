"""
9-step numbered sidebar, always visible.

Steps *suggest* an order rather than gating it: any step whose prerequisites
are satisfied can be clicked at any time (including going back to Refine after
consolidating or exporting). Locked steps explain why in their tooltip.

Every colour comes from the active theme tokens (see gui.theme_manager), so the
sidebar reads correctly in both light and dark themes; call `applyTheme` after a
theme switch to restyle in place.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt

from gui.theme import current_tokens


STEPS = [
    ("1", "Find Railway"),
    ("2", "Select Section"),
    ("3", "Configure"),
    ("4", "Candidates"),
    ("5", "Refine & Consolidate"),
    ("6", "Stations"),
    ("7", "Cross-Section"),
    ("8", "Vertical Profile"),
    ("9", "Export"),
]


class StepButton(QPushButton):
    def __init__(self, number: str, label: str, parent=None):
        super().__init__(parent)
        self._number = number
        self._label = label
        self._state = "inactive"
        self._tokens: dict = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_tokens(self, tokens: dict):
        self._tokens = tokens or {}
        self._set_style(self._state)

    def set_state(self, state: str):
        """state: 'inactive' | 'active' | 'done' | 'available' | 'suggested'"""
        self._state = state
        self._set_style(state)

    def _set_style(self, state: str):
        t = self._tokens or current_tokens()
        # Every state carries a 1px border so switching state never nudges the
        # text by a pixel; only its colour changes.
        base = ("border-radius:3px; border:1px solid; text-align:left;"
                " padding:6px 10px;")
        prefix = "  "
        hover = ""
        if state == "active":
            bg = (f"background:{t['highlight']}; color:{t['highlightText']};"
                  f" border-color:{t['highlight']}; font-weight:600;")
        elif state == "done":
            bg = (f"background:{t['accentDoneBackground']}; color:{t['accentDone']};"
                  f" border-color:{t['accentDone']};")
            hover = f"QPushButton:hover {{ border-color:{t['highlight']}; }}"
        elif state == "suggested":
            # Reachable and the natural next move - accented outline
            bg = (f"background:{t['button']}; color:{t['text']};"
                  f" border-color:{t['highlight']};")
            hover = f"QPushButton:hover {{ background:{t['alternateBase']}; }}"
            prefix = "▸ "
        elif state == "available":
            bg = (f"background:{t['button']}; color:{t['text']};"
                  f" border-color:{t['border']};")
            hover = f"QPushButton:hover {{ background:{t['alternateBase']}; }}"
        else:   # inactive / locked
            bg = (f"background:transparent; color:{t['disabledText']};"
                  f" border-color:transparent;")
        self.setStyleSheet(f"QPushButton {{ {base} {bg} }} {hover}")
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
        self._tokens: dict = current_tokens()
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(5)

        # Font family/size are left to the application font so the Settings
        # dialog's font-size preference governs here too.
        self._title = QLabel("COYPU Feeder")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)

        self._sub = QLabel("OSM → LandXML")
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._sub)

        for i, (num, label) in enumerate(STEPS):
            btn = StepButton(num, label)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_clicked(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        self._hint = QLabel("Steps are suggestions —\nany unlocked step is clickable.")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        layout.addStretch()
        self.applyTheme(None, self._tokens)

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
    # Theming
    # ------------------------------------------------------------------

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        """Restyle in place after a theme switch (COYPU's dock convention)."""
        self._tokens = tokens or current_tokens()
        t = self._tokens
        self._title.setStyleSheet(
            f"color:{t['highlight']}; font-size:15px; font-weight:600;"
            " margin-bottom:6px;")
        self._sub.setStyleSheet(
            f"color:{t['disabledText']}; font-size:10px; margin-bottom:8px;")
        self._hint.setStyleSheet(
            f"color:{t['disabledText']}; font-size:9px; margin-top:6px;")
        for btn in self._buttons:
            btn.set_tokens(t)
        self._update_styles()

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
