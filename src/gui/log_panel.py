"""
Log / console panel — bottom-left of the window.

A lightweight, always-visible activity log. Steps push short instructions
and results here (e.g. why a radius edit was clamped), so the user has a
running history instead of only the transient status-bar message.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
)
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt


_LEVEL_COLOR = {
    "info":  "#cfd8dc",
    "step":  "#4fc3f7",
    "ok":    "#81c784",
    "warn":  "#ffb74d",
    "error": "#e57373",
}


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(3)

        header = QHBoxLayout()
        title = QLabel("Log")
        title.setFont(QFont("Helvetica", 10, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        clear = QPushButton("Clear")
        clear.setFixedWidth(52)
        clear.setStyleSheet("font-size: 9px; padding: 1px 4px;")
        clear.clicked.connect(self.clear)
        header.addWidget(clear)
        v.addLayout(header)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(2000)   # keep memory bounded
        self._view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._view.setStyleSheet(
            "QPlainTextEdit { background:#1e1e22; color:#cfd8dc; "
            "font-size:10px; border:1px solid #3c3c40; border-radius:4px; }"
        )
        v.addWidget(self._view)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        """Append one line (level: info | step | ok | warn | error)."""
        ts  = datetime.now().strftime("%H:%M:%S")
        col = _LEVEL_COLOR.get(level, _LEVEL_COLOR["info"])
        safe = (message.replace("&", "&amp;")
                       .replace("<", "&lt;").replace(">", "&gt;"))
        self._view.appendHtml(
            f'<span style="color:#607d8b;">{ts}</span>&nbsp;'
            f'<span style="color:{col};">{safe}</span>'
        )
        self._view.verticalScrollBar().setValue(
            self._view.verticalScrollBar().maximum()
        )

    def log_step(self, title: str, lines: list[str] | None = None):
        """Log a step banner followed by optional instruction bullets."""
        self.log(f"— {title} —", "step")
        for ln in (lines or []):
            self.log(f"  • {ln}", "info")

    def clear(self):
        self._view.clear()
