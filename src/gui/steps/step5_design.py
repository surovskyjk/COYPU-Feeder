"""
Step 5 — Refine & Consolidate.

The unified alignment-design workspace: manual editing (curve radius,
spiral length, omitting PIs, merging spirals, PI drag on the map) happens
in the element-table dock below the map, exactly as before; this panel
carries the summary metrics, a collapsible "Consolidate curve runs" tool
(Scan → tick/untick rows → Apply, replacing runs of same-direction curves
with a single transition–circular–transition curve), and the Back/Accept
navigation.

Automatic consolidation already runs during candidate generation (see
`geometry.candidates.auto_consolidate`), so by the time this step opens
most curve runs are already merged — the tool here exists to push the
straight/deviation thresholds wider than the automatic pass allowed. It
starts collapsed for that reason: available, not front-and-center.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QToolButton, QGroupBox, QFormLayout, QDoubleSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar,
)
from PySide6.QtCore import Signal, Qt

from gui import style_kit as sk
from gui.style_kit import ThemedPanel
from gui.theme import current_tokens


COLS = ["", "PIs", "Curves", "R before → after [m]", "Max dev [m]", "Status"]
COL_CHK, COL_PIS, COL_N, COL_R, COL_DEV, COL_STATUS = range(6)


class Step5Design(ThemedPanel, QWidget):
    design_done    = Signal(list)   # final list[dict] elements → Stations
    model_changed  = Signal()       # consolidation applied/undone → App refresh
    back_requested = Signal()       # user wants to go back to Candidates
    log_message    = Signal(str, str)
    highlight_span = Signal(list)   # element_ids to emphasize on the map

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidate = None
        self._model = None
        self._working_elements: list[dict] = []
        self._groups: list = []
        self._worker = None    # ConsolidationScanWorker | ConsolidationApplyWorker | None
        self._timer  = None    # ElapsedTimer, started when the current worker began
        # Set by App, mirroring element_table's undo-history hooks — the
        # consolidation Apply is a real edit and must land in the SAME
        # undo/redo stack as every other Refine action.
        self.on_before_edit = None   # callable(label: str)
        self.on_edit_failed = None   # callable()
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._title_lbl = QLabel("Refine & Consolidate")
        self.bolded(self._title_lbl, 4)
        outer.addWidget(self._title_lbl)

        self._algo_lbl = QLabel("")
        self.themed(self._algo_lbl, sk.muted_label)
        outer.addWidget(self._algo_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sk.set_role(sep, "separator")
        outer.addWidget(sep)

        info = QLabel(
            "All elements are listed in the table below the map:\n\n"
            "• Edit an arc Radius or a Spiral L value — the alignment\n"
            "  rebuilds and the map updates automatically.\n"
            "• Omit PI removes a curve (neighbours absorb the deflection);\n"
            "  omitted PIs can be restored at the bottom of the table.\n"
            "• Short straights between two curves offer 'Merge spirals' —\n"
            "  the straight is replaced by prolonged, symmetric spirals.\n"
            "• Click a table row to highlight that element on the map, or\n"
            "  click an element directly on the map to select its row;\n"
            "  Ctrl+click on the map multi-selects (e.g. pick the first and\n"
            "  last tangent for 'Merge PI range'). Hover an element for 3 s\n"
            "  to see its statistics.\n\n"
            "Points of Intersection are shown as markers; the dashed grey\n"
            "stubs are the virtual tangent extensions toward each PI."
        )
        self.themed(info, sk.muted_label)
        info.setWordWrap(True)
        outer.addWidget(info)

        # Metrics (persistent deviation numbers also live in the status-bar
        # chip; this panel keeps its own copy for readability inside Step 5)
        metrics_frame = QFrame()
        self.themed(metrics_frame, sk.panel_frame)
        mf = QVBoxLayout(metrics_frame)
        mf.setContentsMargins(8, 6, 8, 6)
        self._metrics_lbl = QLabel("No candidate selected.")
        self.themed(self._metrics_lbl, sk.muted_label)
        mf.addWidget(self._metrics_lbl)
        outer.addWidget(metrics_frame)

        outer.addWidget(self._build_consolidate_section())

        outer.addStretch()

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(70)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._accept_btn = QPushButton("Accept →")
        self._accept_btn.setMinimumHeight(36)
        self.bolded(self._accept_btn, 2)
        self.themed(self._accept_btn, sk.confirm_button)
        self._accept_btn.setEnabled(False)
        self._accept_btn.clicked.connect(self._on_accept)
        nav_row.addWidget(self._accept_btn)

        outer.addLayout(nav_row)

    def _build_consolidate_section(self) -> QWidget:
        """A collapsed-by-default section — the Rules/Scan/Apply tool moved
        verbatim from the old standalone Consolidate step."""
        section = QWidget()
        sv = QVBoxLayout(section)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(4)

        self._consolidate_toggle = QToolButton()
        self._consolidate_toggle.setCheckable(True)
        self._consolidate_toggle.setChecked(False)
        self._consolidate_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._consolidate_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._consolidate_toggle.setText("Consolidate curve runs")
        self._consolidate_toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: 600; }")
        self._consolidate_toggle.toggled.connect(self._on_consolidate_toggled)
        sv.addWidget(self._consolidate_toggle)

        self._consolidate_body = QWidget()
        self._consolidate_body.setVisible(False)
        body = QVBoxLayout(self._consolidate_body)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)

        info = QLabel(
            "Replaces runs of consecutive curves that turn the same way — "
            "connected directly or by a short straight — with a single "
            "transition–circular–transition curve. Only runs whose merged "
            "curve stays within the deviation limit are offered. Runs are "
            "already auto-merged during fitting where possible — use this "
            "to push the thresholds further."
        )
        self.themed(info, sk.muted_label)
        info.setWordWrap(True)
        body.addWidget(info)

        params = QGroupBox("Rules")
        pf = QFormLayout(params)

        self._straight_spin = QDoubleSpinBox()
        self._straight_spin.setRange(0.0, 1000.0)
        self._straight_spin.setSingleStep(10.0)
        self._straight_spin.setValue(30.0)
        self._straight_spin.setDecimals(0)
        self._straight_spin.setSuffix(" m")
        self._straight_spin.setToolTip(
            "Curves separated by a straight LONGER than this are left alone.\n"
            "0 = only curves that touch directly (spiral → spiral)."
        )
        pf.addRow("Max intermediate straight:", self._straight_spin)

        self._dev_spin = QDoubleSpinBox()
        self._dev_spin.setRange(0.1, 50.0)
        self._dev_spin.setSingleStep(0.5)
        self._dev_spin.setValue(2.0)
        self._dev_spin.setDecimals(2)
        self._dev_spin.setSuffix(" m")
        self._dev_spin.setToolTip(
            "The merged curve must stay within this distance of the OSM\n"
            "polyline, otherwise the run is rejected."
        )
        pf.addRow("Max deviation from OSM:", self._dev_spin)
        body.addWidget(params)

        btns = QHBoxLayout()
        self._scan_btn = QPushButton("🔍 Scan")
        self._scan_btn.clicked.connect(self._on_scan)
        btns.addWidget(self._scan_btn)

        self._apply_btn = QPushButton("✅ Apply selected")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self._apply_btn)
        body.addLayout(btns)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        body.addWidget(self._progress_bar)

        self._status_lbl = QLabel("Press Scan to look for mergeable runs.")
        self.themed(self._status_lbl, sk.muted_label)
        self._status_lbl.setWordWrap(True)
        body.addWidget(self._status_lbl)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setStyleSheet("font-size: 10px;")
        self._table.setMaximumHeight(180)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        body.addWidget(self._table)

        sv.addWidget(self._consolidate_body)
        return section

    def _on_consolidate_toggled(self, on: bool):
        self._consolidate_toggle.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self._consolidate_body.setVisible(on)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, candidate, pi_model, xy, chainages, settings: dict):
        """Called by App when the user selects a candidate in Step 4."""
        self._candidate = candidate
        self._model = pi_model
        self._working_elements = list(getattr(candidate, "elements", []) or [])
        label = getattr(candidate, "label", "Unknown")
        self._title_lbl.setText(f"Refine & Consolidate: {label}")
        editable = pi_model is not None
        self._algo_lbl.setText(
            f"{label}" + ("" if editable else "  (read-only — no PI model)"))
        self._accept_btn.setEnabled(len(self._working_elements) > 0)
        self._refresh_metrics({
            "max_deviation":        getattr(candidate, "max_deviation", 0.0),
            "rmse":                 getattr(candidate, "rmse", 0.0),
            "mean_deviation":       getattr(candidate, "mean_deviation", 0.0),
            "max_heading_jump_deg": getattr(candidate, "max_heading_jump_deg", 0.0),
        })

        self._groups = []
        self._table.setRowCount(0)
        self._progress_bar.setVisible(False)
        auto_merged = int(getattr(candidate, "auto_merged", 0) or 0)
        if pi_model is None:
            self._status_lbl.setText(
                "This level has no editable PI model (Level 1 is the raw "
                "polyline) — nothing to consolidate.")
            self._scan_btn.setEnabled(False)
            self._apply_btn.setEnabled(False)
        else:
            self._scan_btn.setEnabled(True)
            self._status_lbl.setText(
                (f"{auto_merged} run(s) were already auto-merged during "
                 "fitting — Scan to look for more."
                 if auto_merged > 0 else
                 "Press Scan to look for mergeable runs.")
            )

    def set_elements(self, elements: list, metrics: dict | None = None):
        """Called by App after every table-driven rebuild."""
        self._working_elements = list(elements or [])
        self._accept_btn.setEnabled(len(self._working_elements) > 0)
        if metrics:
            self._refresh_metrics(metrics)

    def bind(self, pi_model):
        """Rebind the working PI model without a full `prepare()` — used by
        project load, where there is no `CandidateAlignment` to read a
        label/deviation snapshot from. Also resets the Consolidate section's
        transient scan state, matching `prepare()`'s behaviour there."""
        self._model = pi_model
        self._groups = []
        self._table.setRowCount(0)
        self._progress_bar.setVisible(False)
        if pi_model is None:
            self._status_lbl.setText(
                "This level has no editable PI model (Level 1 is the raw "
                "polyline) — nothing to consolidate.")
            self._scan_btn.setEnabled(False)
            self._apply_btn.setEnabled(False)
        else:
            self._scan_btn.setEnabled(True)
            self._apply_btn.setEnabled(False)
            self._status_lbl.setText("Press Scan to look for mergeable runs.")

    def reset(self):
        """Called by App's Start Over."""
        self._candidate = None
        self._model = None
        self._working_elements = []
        self._groups = []
        self._title_lbl.setText("Refine & Consolidate")
        self._algo_lbl.setText("")
        self._metrics_lbl.setText("No candidate selected.")
        self._accept_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._progress_bar.setVisible(False)
        self._scan_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._status_lbl.setText("Press Scan to look for mergeable runs.")

    # ------------------------------------------------------------------
    # Internal — metrics
    # ------------------------------------------------------------------

    def _refresh_metrics(self, metrics: dict):
        n = len(self._working_elements)
        self._metrics_lbl.setText(
            f"Elements: {n}    "
            f"Mean deviation: {metrics.get('mean_deviation', 0.0):.2f} m    "
            f"Max deviation: {metrics.get('max_deviation', 0.0):.2f} m\n"
            f"RMSE: {metrics.get('rmse', 0.0):.2f} m    "
            f"C1 mismatch: {metrics.get('max_heading_jump_deg', 0.0):.3f}°"
        )

    def _on_accept(self):
        if self._working_elements:
            self.design_done.emit(list(self._working_elements))

    # ------------------------------------------------------------------
    # Consolidate — Scan
    # ------------------------------------------------------------------

    def _on_scan(self):
        from gui.worker import ConsolidationScanWorker
        from gui.progress_util import ElapsedTimer
        if self._model is None or self._worker is not None:
            return
        self._scan_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._progress_bar.setRange(0, 0)   # indeterminate until the first progress tick
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_lbl.setText("Scanning…")
        self._timer = ElapsedTimer()
        self._worker = ConsolidationScanWorker(
            self._model, self._straight_spin.value(), self._dev_spin.value(), self)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.start()

    def _on_scan_progress(self, done: int, total: int):
        from gui.progress_util import format_eta
        if self._progress_bar.maximum() == 0 and total > 0:
            self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(done)
        elapsed = self._timer.elapsed() if self._timer else 0.0
        self._status_lbl.setText(f"Scanning… {format_eta(elapsed, done, total)}")

    def _on_scan_finished(self, groups: list):
        self._worker = None
        self._progress_bar.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._groups = groups
        self._populate()
        n_ok = sum(1 for g in self._groups if g["ok"])
        msg = (f"{len(self._groups)} run(s) found, {n_ok} within tolerance."
               if self._groups else
               "No runs of same-direction curves match the rules.")
        self._status_lbl.setText(msg)
        self.log_message.emit(f"Consolidate scan: {msg}", "info")
        self._apply_btn.setEnabled(n_ok > 0)

    def _on_scan_failed(self, error: str):
        self._worker = None
        self._progress_bar.setVisible(False)
        self._status_lbl.setText(f"⚠ Scan failed: {error}")
        self.log_message.emit(f"Consolidate scan failed: {error}", "error")
        self._scan_btn.setEnabled(True)

    def _populate(self):
        self._table.setRowCount(0)
        from PySide6.QtGui import QColor
        grey = QColor(current_tokens()["disabledText"])
        for g in self._groups:
            row = self._table.rowCount()
            self._table.insertRow(row)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                         | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if g["ok"]
                              else Qt.CheckState.Unchecked)
            self._table.setItem(row, COL_CHK, chk)

            def mk(text, right=False):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if right:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
                if not g["ok"]:
                    it.setForeground(grey)
                return it

            self._table.setItem(row, COL_PIS, mk(f"{g['k_from']}–{g['k_to']}"))
            self._table.setItem(row, COL_N,   mk(str(g["n_curves"]), True))
            radii = ", ".join(f"{r:.0f}" for r in g["radii_before"])
            after = f"{g['radius_after']:.0f}" if g["radius_after"] else "—"
            self._table.setItem(row, COL_R, mk(f"{radii} → {after}"))
            self._table.setItem(row, COL_DEV, mk(
                f"{g['max_dev']:.2f}" if g["max_dev"] is not None else "—", True))
            self._table.setItem(row, COL_STATUS, mk(g["reason"]))

    def _on_row_selected(self):
        """Highlight the elements of the selected run on the map."""
        rows = self._table.selectionModel().selectedRows()
        if not rows or self._model is None:
            return
        g = self._groups[rows[0].row()]
        span = range(g["k_from"], g["k_to"] + 1)
        ids = [e.get("element_id") for e in self._model.elements
               if e.get("_pi") in span and e.get("element_id")]
        self.highlight_span.emit(ids)

    # ------------------------------------------------------------------
    # Consolidate — Apply (folded into the shared EditHistory)
    # ------------------------------------------------------------------

    def _selected_groups(self) -> list:
        out = []
        for row, g in enumerate(self._groups):
            it = self._table.item(row, COL_CHK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(dict(g, ok=True))   # honour a manual tick
        return out

    def _on_apply(self):
        from gui.worker import ConsolidationApplyWorker
        from gui.progress_util import ElapsedTimer
        if self._model is None or self._worker is not None:
            return
        chosen = self._selected_groups()
        if not chosen:
            self._status_lbl.setText("No rows ticked.")
            return
        if self.on_before_edit is not None:
            self.on_before_edit(f"consolidate {len(chosen)} run(s)")
        self._apply_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._progress_bar.setRange(0, len(chosen))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_lbl.setText("Applying…")
        self._timer = ElapsedTimer()
        self._worker = ConsolidationApplyWorker(self._model, chosen, self)
        self._worker.progress.connect(self._on_apply_progress)
        self._worker.finished.connect(
            lambda applied, msgs: self._on_apply_finished(applied, msgs, len(chosen)))
        self._worker.failed.connect(self._on_apply_failed)
        self._worker.start()

    def _on_apply_progress(self, done: int, total: int):
        from gui.progress_util import format_eta
        self._progress_bar.setValue(done)
        elapsed = self._timer.elapsed() if self._timer else 0.0
        self._status_lbl.setText(f"Applying… {format_eta(elapsed, done, total)}")

    def _on_apply_finished(self, n: int, msgs: list, n_chosen: int):
        self._worker = None
        self._progress_bar.setVisible(False)
        self._scan_btn.setEnabled(True)
        for m in msgs:
            self.log_message.emit(f"Consolidate: {m}", "ok")
        if n == 0 and self.on_edit_failed is not None:
            # Nothing was actually applied — don't leave a do-nothing entry
            # in the undo stack.
            self.on_edit_failed()
        self._status_lbl.setText(
            f"Applied {n} of {n_chosen} run(s). Re-scan to look for more.")
        self.log_message.emit(
            f"Consolidate: applied {n} run(s); "
            f"{len(self._model.elements)} elements now.", "ok")
        self._groups = []
        self._table.setRowCount(0)
        self.model_changed.emit()

    def _on_apply_failed(self, error: str):
        self._worker = None
        self._progress_bar.setVisible(False)
        self._scan_btn.setEnabled(True)
        if self.on_edit_failed is not None:
            self.on_edit_failed()
        self._status_lbl.setText(f"⚠ Apply failed: {error}")
        self.log_message.emit(f"Consolidate apply failed: {error}", "error")
