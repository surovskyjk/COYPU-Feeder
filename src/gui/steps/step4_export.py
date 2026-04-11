"""
Step 4 — Export.
File chooser, progress bar with stage labels, start button.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QProgressBar, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from gui.worker import ExportWorker

STAGES = [
    "Projecting coordinates…",
    "Fitting geometry…",
    "Querying DEM elevation…",
    "Building LandXML…",
    "Writing file…",
    "Done!",
]

STAGE_PCT = {s: int(i / (len(STAGES) - 1) * 100) for i, s in enumerate(STAGES)}


class Step4Export(QWidget):
    export_done = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks = []
        self._settings: dict = {}
        self._worker: ExportWorker | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        lbl = QLabel("Export LandXML")
        lbl.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        layout.addWidget(lbl)

        # File path row
        file_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Choose output file…")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._path_edit)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        # Stage label
        self._stage_lbl = QLabel("Ready.")
        self._stage_lbl.setStyleSheet("color:#aaa; font-size:11px;")
        layout.addWidget(self._stage_lbl)

        # Station progress
        self._station_lbl = QLabel("")
        self._station_lbl.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(self._station_lbl)

        layout.addStretch()

        # Start button
        self._start_btn = QPushButton("▶  Start Export")
        self._start_btn.setMinimumHeight(44)
        self._start_btn.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        self._start_btn.setStyleSheet(
            "QPushButton { background:#27ae60; color:#fff; border-radius:6px; }"
            "QPushButton:hover { background:#2ecc71; }"
            "QPushButton:disabled { background:#444; color:#777; }"
        )
        self._start_btn.clicked.connect(self._start_export)
        layout.addWidget(self._start_btn)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def prepare(self, tracks, settings: dict):
        self._tracks = tracks
        self._settings = settings
        self._progress.setValue(0)
        self._stage_lbl.setText("Ready.")
        self._station_lbl.setText("")
        self._start_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save LandXML file", "",
            "LandXML files (*.xml);;All files (*.*)"
        )
        if path:
            self._path_edit.setText(path)

    def _start_export(self):
        filepath = self._path_edit.text().strip()
        if not filepath:
            QMessageBox.warning(self, "No file", "Please choose an output file first.")
            return
        if not self._tracks:
            QMessageBox.warning(self, "No data", "No tracks to export.")
            return

        self._start_btn.setEnabled(False)
        self._progress.setValue(0)
        self._stage_lbl.setText("Starting…")

        self._worker = ExportWorker(self._tracks, self._settings, filepath, self)
        self._worker.stage_changed.connect(self._on_stage)
        self._worker.station_progress.connect(self._on_station_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_stage(self, stage: str):
        self._stage_lbl.setText(stage)
        # Update progress: match known stages or advance incrementally
        pct = STAGE_PCT.get(stage)
        if pct is None:
            # Fitting/elevation stages — advance proportionally
            current = self._progress.value()
            pct = min(current + 15, 85)
        self._progress.setValue(pct)

    def _on_station_progress(self, current: float, total: float):
        if total > 0:
            self._station_lbl.setText(
                f"Chainage: {current:.0f} / {total:.0f} m"
            )

    def _on_finished(self, filepath: str):
        self._progress.setValue(100)
        self._stage_lbl.setText("Export complete!")
        self._station_lbl.setText(filepath)
        self._start_btn.setEnabled(True)
        QMessageBox.information(
            self, "Export complete",
            f"LandXML file written successfully:\n{filepath}"
        )

    def _on_failed(self, error: str):
        self._stage_lbl.setText("Export failed.")
        self._start_btn.setEnabled(True)
        QMessageBox.critical(self, "Export failed", error)

