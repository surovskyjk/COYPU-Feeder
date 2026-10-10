"""
Step 6 — Cross-Section Elevation Analysis.

For the refined alignment the user samples terrain elevation at perpendicular
offsets to assess terrain cross-slope: green = flat (low embankment cost),
red = steep (high cost / potential tunnel/cut).  Results are visualised on
the Leaflet map and can be exported as a CSV file.

The step is optional — clicking "Skip" proceeds directly to the Vertical Profile step.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QFileDialog,
)
from PySide6.QtCore import Signal, Qt

from gui import style_kit as sk
from gui.style_kit import ThemedPanel
from gui import icons
from gui.theme import current_tokens
from gui.steps._widgets import (
    _hline, _labeled_combo, _labeled_spin, _stat_row,
)


class Step6CrossSection(ThemedPanel, QWidget):
    """
    Cross-Section Elevation Analysis step.

    Signals
    -------
    analysis_done(list)
        Emitted when user clicks "Continue".
        Carries the list of result dicts (empty list if the step was skipped).
    back_requested()
        User clicked ← Back (return to Step 5 Refine).
    cross_section_ready(list, list)
        Left and right coloured polyline data for the map:
        each is ``[[lat, lon, color_hex], …]``.
    """

    analysis_done      = Signal(list)
    back_requested     = Signal()
    cross_section_ready = Signal(list, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._elements:  list      = []
        self._work_epsg: int       = 32633
        self._results:   list      = []
        self._worker                = None
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Title
        title = QLabel("Cross-Section Analysis")
        self.bolded(title, 4)
        sk.set_role(title, "title")
        root.addWidget(title)

        sub = QLabel(
            "Sample terrain elevation at perpendicular offsets to assess\n"
            "where large embankments or cuttings would be needed."
        )
        sub.setWordWrap(True)
        self.themed(sub, sk.muted_label)
        root.addWidget(sub)

        root.addWidget(_hline())

        # ── Parameters ──────────────────────────────────────────────────
        params = QGroupBox("Parameters")
        params.setStyleSheet("QGroupBox { font-weight:600; }")
        pg = QVBoxLayout(params)
        pg.setSpacing(6)

        self._offset_spin = _labeled_spin(
            pg, "Offset distance:", 1.0, 500.0, 10.0, " m",
            "Perpendicular distance from centreline to left/right sample points.",
        )
        self._threshold_spin = _labeled_spin(
            pg, "Colour threshold:", 0.1, 100.0, 2.0, " m",
            "Elevation differences below this value appear green; "
            "above it — increasingly red.",
        )
        self._elev_source_combo = _labeled_combo(
            pg, "Elevation source:",
            ["ČÚZK DMR 5G (CZ, ~0.2 m accuracy)", "OpenTopoData (EU-DEM/SRTM, ~25–30 m)"],
            "Source for terrain elevation at each station and offset point.\n"
            "ČÚZK DMR 5G: Czech national lidar terrain model, © ČÚZK – on-line.\n"
            "Falls back to OpenTopoData for any point outside its coverage.",
        )
        root.addWidget(params)

        # ── Action buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedHeight(34)
        self._back_btn.clicked.connect(self.back_requested.emit)
        btn_row.addWidget(self._back_btn)

        self._run_btn = QPushButton("Run Analysis")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setIcon(icons.makeIcon("run"))
        self.themed(self._run_btn, sk.secondary_button)
        self._run_btn.clicked.connect(self._start_analysis)
        btn_row.addWidget(self._run_btn)

        self._skip_btn = QPushButton("Skip →")
        self._skip_btn.setFixedHeight(34)
        self.themed(self._skip_btn, sk.secondary_button)
        self._skip_btn.clicked.connect(self._skip)
        btn_row.addWidget(self._skip_btn)

        root.addLayout(btn_row)

        # ── Progress ─────────────────────────────────────────────────────
        self._status_lbl = QLabel("Ready.")
        self.themed(self._status_lbl, sk.muted_label)
        root.addWidget(self._status_lbl)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(16)
        self.themed(self._progress_bar, sk.progress_bar)
        self._progress_bar.hide()
        root.addWidget(self._progress_bar)

        root.addWidget(_hline())

        # ── Results summary ──────────────────────────────────────────────
        self._results_box = QGroupBox("Results")
        self._results_box.setStyleSheet("QGroupBox { font-weight:600; }")
        rg = QVBoxLayout(self._results_box)
        rg.setSpacing(4)

        self._stat_n     = _stat_row(rg, "Stations analysed:")
        self._stat_left  = _stat_row(rg, "Max |diff| left:")
        self._stat_right = _stat_row(rg, "Max |diff| right:")
        self._stat_over  = _stat_row(rg, "Stations > threshold:")

        self._results_box.setEnabled(False)
        root.addWidget(self._results_box)

        root.addWidget(_hline())

        # ── Bottom actions ───────────────────────────────────────────────
        bot = QHBoxLayout()
        bot.setSpacing(6)

        self._csv_btn = QPushButton("Export CSV…")
        self._csv_btn.setFixedHeight(32)
        self._csv_btn.setEnabled(False)
        self._csv_btn.clicked.connect(self._export_csv)
        bot.addWidget(self._csv_btn)

        self._continue_btn = QPushButton("Continue →")
        self._continue_btn.setFixedHeight(32)
        self._continue_btn.setEnabled(False)
        self.themed(self._continue_btn, sk.confirm_button)
        self._continue_btn.clicked.connect(self._continue)
        bot.addWidget(self._continue_btn)

        root.addLayout(bot)
        root.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, elements: list, work_epsg: int) -> None:
        """Called by App after Step 5 emits refinement_done."""
        self._elements  = elements
        self._work_epsg = work_epsg
        self._results   = []
        self._reset_ui()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_ui(self):
        self._run_btn.setEnabled(True)
        self._skip_btn.setEnabled(True)
        self._csv_btn.setEnabled(False)
        self._continue_btn.setEnabled(False)
        self._results_box.setEnabled(False)
        self._progress_bar.hide()
        self._progress_bar.setValue(0)
        self._status_lbl.setText("Ready.")
        self._stat_n.setText("—")
        self._stat_left.setText("—")
        self._stat_right.setText("—")
        self._stat_over.setText("—")

    def _set_busy(self, busy: bool):
        self._run_btn.setEnabled(not busy)
        self._skip_btn.setEnabled(not busy)
        self._back_btn.setEnabled(not busy)
        self._csv_btn.setEnabled(False)
        self._continue_btn.setEnabled(False)
        if busy:
            self._progress_bar.show()
        else:
            self._progress_bar.hide()

    def _start_analysis(self):
        if not self._elements:
            self._status_lbl.setText("No alignment loaded.")
            return

        from gui.worker import CrossSectionWorker

        offset_m  = self._offset_spin.value()
        elevation_source = ("cuzk_dmr5g"
                             if self._elev_source_combo.currentIndex() == 0
                             else "opentopodata")
        self._set_busy(True)
        self._status_lbl.setText("Querying elevation data…")
        self._progress_bar.setValue(0)

        self._worker = CrossSectionWorker(
            self._elements,
            self._work_epsg,
            offset_m,
            interval_m=5.0,
            elevation_source=elevation_source,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, current: int, total: int):
        pct = int(100 * current / max(total, 1))
        self._progress_bar.setValue(pct)
        self._status_lbl.setText(f"Querying elevations… {current} / {total} stations")

    def _on_finished(self, results: list):
        self._results = results
        self._set_busy(False)
        self._progress_bar.setValue(100)

        threshold = self._threshold_spin.value()
        self._populate_results(results, threshold)
        self._emit_map_data(results, threshold)

        self._results_box.setEnabled(True)
        self._csv_btn.setEnabled(bool(results))
        self._continue_btn.setEnabled(True)
        self._status_lbl.setText("Analysis complete.")

    def _on_failed(self, error: str):
        self._set_busy(False)
        self._status_lbl.setText(f"Error: {error}")

    def _populate_results(self, results: list, threshold: float):
        valid = [r for r in results
                 if r.get("diff_left") is not None and r.get("diff_right") is not None]
        n = len(results)
        n_valid = len(valid)

        max_left  = max((abs(r["diff_left"])  for r in valid), default=0.0)
        max_right = max((abs(r["diff_right"]) for r in valid), default=0.0)
        n_over    = sum(1 for r in valid
                        if abs(r["diff_left"])  > threshold
                        or abs(r["diff_right"]) > threshold)

        self._stat_n.setText(f"{n_valid} / {n}")
        self._stat_left.setText(f"{max_left:.2f} m")
        self._stat_right.setText(f"{max_right:.2f} m")
        pct = 100.0 * n_over / n_valid if n_valid else 0.0
        self._stat_over.setText(f"{n_over} / {n_valid}  ({pct:.1f} %)")

    def _emit_map_data(self, results: list, threshold: float):
        from geometry.cross_section import diff_to_color

        left_pts:  list = []
        right_pts: list = []

        for r in results:
            dl = r.get("diff_left")
            dr = r.get("diff_right")

            muted = current_tokens()["disabledText"]
            color_l = diff_to_color(abs(dl), threshold) if dl is not None else muted
            color_r = diff_to_color(abs(dr), threshold) if dr is not None else muted

            left_pts.append([r["lat_left"],  r["lon_left"],  color_l])
            right_pts.append([r["lat_right"], r["lon_right"], color_r])

        self.cross_section_ready.emit(left_pts, right_pts)

    def _export_csv(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "", "CSV files (*.csv)"
        )
        if not path:
            return
        from geometry.cross_section import export_csv
        try:
            export_csv(self._results, path)
            self._status_lbl.setText(f"CSV saved: {path}")
        except Exception as exc:
            self._status_lbl.setText(f"CSV export failed: {exc}")

    def _skip(self):
        self.analysis_done.emit([])

    def _continue(self):
        self.analysis_done.emit(self._results)
