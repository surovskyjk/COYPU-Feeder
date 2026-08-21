"""
Log / console panel — bottom-left of the window.

A lightweight, always-visible activity log. Steps push short instructions
and results here (e.g. why a radius edit was clamped), so the user has a
running history instead of only the transient status-bar message.

Severity colours resolve from the active theme tokens. Entries are kept as
(timestamp, message, level) so a theme switch can re-render the whole
scrollback in the new colours instead of leaving old lines tuned for the
previous theme (and, in light mode, barely legible).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
)
from PySide6.QtGui import QFontDatabase

from gui.theme import current_tokens
from gui.palette import WARNING


def _level_color(level: str, tokens: dict) -> str:
    """Map a severity level onto the active token set."""
    return {
        "info":  tokens["text"],
        "step":  tokens["highlight"],
        "ok":    tokens["accentDone"],
        "warn":  WARNING,
        "error": tokens["error"],
    }.get(level, tokens["text"])


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens = current_tokens()
        # (timestamp, message, level) - kept so applyTheme can re-render.
        self._entries: list[tuple[str, str, str]] = []
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(3)

        header = QHBoxLayout()
        self._title = QLabel("Log")
        header.addWidget(self._title)
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
        v.addWidget(self._view)

        self.applyTheme(None, self._tokens)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        """Restyle in place after a theme switch (COYPU's dock convention)."""
        self._tokens = tokens or current_tokens()
        t = self._tokens
        self._title.setStyleSheet(f"color:{t['text']}; font-weight:600;")
        self._view.setStyleSheet(
            f"QPlainTextEdit {{ background:{t['base']}; color:{t['text']};"
            f" font-size:10px; border:1px solid {t['border']};"
            " border-radius:3px; }"
        )
        self._render_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        """Append one line (level: info | step | ok | warn | error)."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._entries.append((ts, message, level))
        # Match QPlainTextEdit's own cap so the two stay in step.
        if len(self._entries) > self._view.maximumBlockCount():
            del self._entries[:-self._view.maximumBlockCount()]
        self._view.appendHtml(self._html(ts, message, level))
        self._scroll_to_end()

    def _html(self, ts: str, message: str, level: str) -> str:
        t = self._tokens or current_tokens()
        safe = (message.replace("&", "&amp;")
                       .replace("<", "&lt;").replace(">", "&gt;"))
        return (f'<span style="color:{t["disabledText"]};">{ts}</span>&nbsp;'
                f'<span style="color:{_level_color(level, t)};">{safe}</span>')

    def _render_all(self):
        """Redraw the whole scrollback in the active theme's colours."""
        if not self._entries:
            return
        self._view.clear()
        for ts, message, level in self._entries:
            self._view.appendHtml(self._html(ts, message, level))
        self._scroll_to_end()

    def _scroll_to_end(self):
        self._view.verticalScrollBar().setValue(
            self._view.verticalScrollBar().maximum()
        )

    def log_step(self, title: str, lines: list[str] | None = None):
        """Log a step banner followed by optional instruction bullets."""
        self.log(f"— {title} —", "step")
        for ln in (lines or []):
            self.log(f"  • {ln}", "info")

    def clear(self):
        self._entries.clear()
        self._view.clear()
