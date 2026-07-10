"""
About and Settings dialogs for COYPU Feeder.
"""

from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QTextBrowser, QComboBox, QSpinBox, QFormLayout, QDialogButtonBox,
    QGroupBox, QCheckBox,
)
from PySide6.QtGui import QPixmap

import app_meta as meta
from .branding import make_splash_pixmap


# ---------------------------------------------------------------------------
# About / Help
# ---------------------------------------------------------------------------

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {meta.APP_NAME}")
        self.resize(640, 560)
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        # Banner (reuse the splash artwork)
        banner = QLabel()
        pm: QPixmap = make_splash_pixmap()
        banner.setPixmap(pm.scaledToWidth(600, Qt.TransformationMode.SmoothTransformation))
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(banner)

        tabs = QTabWidget()
        tabs.addTab(self._about_tab(), "About")
        tabs.addTab(self._algorithm_tab(), "Algorithm")
        tabs.addTab(self._data_tab(), "Data & License")
        v.addWidget(tabs, stretch=1)

        # Buttons: README, Releases, Close
        row = QHBoxLayout()
        readme_btn = QPushButton("📖 Open README")
        readme_btn.clicked.connect(self._open_readme)
        row.addWidget(readme_btn)
        rel_btn = QPushButton("⬇ Releases")
        rel_btn.clicked.connect(lambda: webbrowser.open(meta.RELEASES_URL))
        row.addWidget(rel_btn)
        repo_btn = QPushButton("🐙 GitHub")
        repo_btn.clicked.connect(lambda: webbrowser.open(meta.REPO_URL))
        row.addWidget(repo_btn)
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        v.addLayout(row)

    def _about_tab(self) -> QWidget:
        w = QTextBrowser()
        w.setOpenExternalLinks(True)
        w.setHtml(
            f"<h2>{meta.APP_NAME} <span style='color:#888;'>v{meta.APP_VERSION}</span></h2>"
            f"<p>{meta.APP_TAGLINE}</p>"
            f"<p>A desktop tool that queries railway route geometry from "
            f"OpenStreetMap and converts it into a clean, C1-continuous "
            f"LandXML 1.2 alignment for the COYPU railway software.</p>"
            f"<p><b>Author:</b> {meta.AUTHOR}<br>"
            f"<b>License:</b> {meta.LICENSE}<br>"
            f"<b>{meta.COPYRIGHT}</b></p>"
            f"<p><a href='{meta.REPO_URL}'>{meta.REPO_URL}</a></p>"
        )
        return w

    def _algorithm_tab(self) -> QWidget:
        w = QTextBrowser()
        html = "<h3>How the alignment is built</h3><p>" + \
               meta.ALGORITHM_DESC.replace("\n\n", "</p><p>").replace("\n", "<br>") + \
               "</p>"
        w.setHtml(html)
        return w

    def _data_tab(self) -> QWidget:
        w = QTextBrowser()
        w.setOpenExternalLinks(True)
        rows = "".join(
            f"<li><b>{name}</b><br>{desc}</li>"
            for name, desc in meta.DATA_SOURCES
        )
        w.setHtml(
            f"<h3>Data sources</h3><ul>{rows}</ul>"
            f"<h3>License</h3><p>{meta.LICENSE} — {meta.COPYRIGHT}.</p>"
            f"<p>Railway geometry is derived from crowd-sourced "
            f"OpenStreetMap data and is <b>not</b> a surveyed or design "
            f"alignment — verify before any engineering use. Cant values in "
            f"the LandXML export are 0&nbsp;mm placeholders.</p>"
        )
        return w

    def _open_readme(self):
        # Prefer the local README; fall back to the online copy.
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        local = os.path.join(here, "README.md")
        if os.path.exists(local):
            webbrowser.open("file:///" + local.replace("\\", "/"))
        else:
            webbrowser.open(meta.README_URL)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """
    Edits UI preferences. Returns the chosen values via `values()`; the
    caller persists them (QSettings) and applies them live.
    """

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(380, 260)
        self._build(current)

    def _build(self, cur: dict):
        v = QVBoxLayout(self)

        appearance = QGroupBox("Appearance")
        form = QFormLayout(appearance)

        self._theme = QComboBox()
        self._theme.addItem("Automatic (follow system)", "auto")
        self._theme.addItem("Dark", "dark")
        self._theme.addItem("Light", "light")
        i = self._theme.findData(cur.get("theme_mode", "auto"))
        self._theme.setCurrentIndex(max(0, i))
        form.addRow("Theme:", self._theme)

        self._font = QSpinBox()
        self._font.setRange(8, 18)
        self._font.setSuffix(" pt")
        self._font.setValue(int(cur.get("font_pt", 9)))
        form.addRow("Font size:", self._font)

        v.addWidget(appearance)

        behaviour = QGroupBox("Behaviour")
        bform = QFormLayout(behaviour)
        self._show_log = QCheckBox("Show the log panel")
        self._show_log.setChecked(bool(cur.get("show_log", True)))
        bform.addRow(self._show_log)
        self._confirm_start_over = QCheckBox("Confirm before 'Start over'")
        self._confirm_start_over.setChecked(bool(cur.get("confirm_start_over", True)))
        bform.addRow(self._confirm_start_over)
        v.addWidget(behaviour)

        v.addStretch()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def values(self) -> dict:
        return {
            "theme_mode": self._theme.currentData(),
            "font_pt":    self._font.value(),
            "show_log":   self._show_log.isChecked(),
            "confirm_start_over": self._confirm_start_over.isChecked(),
        }
