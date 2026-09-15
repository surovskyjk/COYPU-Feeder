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
    QGroupBox, QCheckBox, QDoubleSpinBox, QScrollArea,
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
        from gui.theme import current_tokens
        muted = current_tokens()["disabledText"]
        w = QTextBrowser()
        w.setOpenExternalLinks(True)
        w.setHtml(
            f"<h2>{meta.APP_NAME} <span style='color:{muted};'>v{meta.APP_VERSION}</span></h2>"
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

    # Engineering-default keys this dialog edits, with (label, suffix,
    # range, decimals, fallback, tooltip) — every one a genuine railway
    # engineering parameter (radius/length floors and tolerances), never an
    # internal numerical-stability constant (span-rebuild tolerances, the
    # ~180° singular-band threshold, etc. stay hardcoded — see CLAUDE.md's
    # "bit-identical" proof obligation, which makes those correctness
    # knobs, not design knobs). Pre-fills Step 3 for a NEW project; Step 3
    # remains the per-project override point, this only changes what it
    # starts from.
    ENGINEERING_DEFAULTS = [
        ("default_max_deviation", "Max deviation (PI tolerance):", " m",
         (0.1, 10.0), 2, 1.0,
         "Douglas-Peucker tolerance used to extract the tangent polygon."),
        ("default_min_radius", "Minimum curve radius:", " m",
         (50.0, 10000.0), 0, 150.0,
         "Radius floor for all circular arcs."),
        ("default_spiral_length", "Spiral length:", " m",
         (0.0, 200.0), 0, 20.0,
         "Length of each entry/exit clothoid spiral."),
        ("default_pi_min_tangent", "Auto-merge tangent gate:", " m",
         (0.0, 500.0), 0, 60.0,
         "Lines shorter than this between two same-rotation curves\n"
         "auto-merge into one PI when a project is first fitted."),
        ("default_min_tangent_length", "Min. tangent (initial fit):", " m",
         (0.0, 500.0), 0, 30.0,
         "Minimum straight length before two same-sign arcs merge\n"
         "during the initial candidate fit."),
        ("default_min_line_length", "Min. line length:", " m",
         (0.0, 200.0), 0, 10.0, "Shortest Line element kept as its own segment."),
        ("default_min_arc_length", "Min. arc length:", " m",
         (0.0, 200.0), 0, 10.0, "Shortest Arc element kept as its own segment."),
        ("default_min_spiral_length", "Min. spiral length:", " m",
         (0.0, 200.0), 0, 10.0, "Shortest Spiral element kept as its own segment."),
    ]

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(420, 520)
        self._build(current)

    def _build(self, cur: dict):
        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)

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

        engineering = QGroupBox("Engineering defaults (new projects)")
        engineering.setToolTip(
            "Pre-fills Step 3 — Configure for a new project. Each project "
            "can still override these there; changing them here only "
            "affects projects started AFTER this change.")
        eform = QFormLayout(engineering)
        self._engineering_spins: dict[str, QDoubleSpinBox] = {}
        for key, label, suffix, rng, decimals, fallback, tip in self.ENGINEERING_DEFAULTS:
            spin = QDoubleSpinBox()
            spin.setRange(*rng)
            spin.setDecimals(decimals)
            spin.setSuffix(suffix)
            spin.setValue(float(cur.get(key, fallback)))
            spin.setToolTip(tip)
            eform.addRow(label, spin)
            self._engineering_spins[key] = spin
        v.addWidget(engineering)

        v.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def values(self) -> dict:
        out = {
            "theme_mode": self._theme.currentData(),
            "font_pt":    self._font.value(),
            "show_log":   self._show_log.isChecked(),
            "confirm_start_over": self._confirm_start_over.isChecked(),
        }
        for key, spin in self._engineering_spins.items():
            out[key] = spin.value()
        return out
