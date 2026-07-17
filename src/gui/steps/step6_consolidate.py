"""
Step 6 — Consolidate.

Level 2/3 often split one physical curve into a run of consecutive
same-direction curves (Douglas-Peucker noise). This step scans for such runs
— curves that turn the same way and are connected directly or by a straight
shorter than a user-defined length — and offers to replace each run with a
single Spiral–Arc–Spiral.

A run is only proposed when the merged curve stays within the user-defined
maximum deviation from the OSM polyline. Rejected runs are listed with the
reason instead of being hidden.

Scan → tick/untick rows → Apply. One Undo restores the pre-Apply model.
"""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QFormLayout, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont, QColor


COLS = ["", "PIs", "Curves", "R before → after [m]", "Max dev [m]", "Status"]
COL_CHK, COL_PIS, COL_N, COL_R, COL_DEV, COL_STATUS = range(6)


class Step6Consolidate(QWidget):
    consolidation_done = Signal(list)   # elements → next step
    model_changed      = Signal()       # model edited → refresh map/table/staleness
    back_requested     = Signal()
    log_message        = Signal(str, str)
    highlight_span     = Signal(list)   # element_ids to emphasize on the map

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._groups: list = []
        self._undo_snapshot = None
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        title = QLabel("Consolidate Curves")
        title.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        v.addWidget(title)

        info = QLabel(
            "Replaces runs of consecutive curves that turn the same way — "
            "connected directly or by a short straight — with a single "
            "transition–circular–transition curve. Only runs whose merged "
            "curve stays within the deviation limit are offered."
        )
        info.setStyleSheet("color: #999; font-size: 10px;")
        info.setWordWrap(True)
        v.addWidget(info)

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
            "0 = only curves that touch directly (spiral → spiral).\n"
            "Note: runs separated by less than ~60 m are already merged\n"
            "automatically while the alignment is built."
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
        v.addWidget(params)

        btns = QHBoxLayout()
        self._scan_btn = QPushButton("🔍 Scan")
        self._scan_btn.clicked.connect(self._on_scan)
        btns.addWidget(self._scan_btn)

        self._apply_btn = QPushButton("✅ Apply selected")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self._apply_btn)

        self._undo_btn = QPushButton("↩ Undo")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._on_undo)
        btns.addWidget(self._undo_btn)
        v.addLayout(btns)

        self._status_lbl = QLabel("Press Scan to look for mergeable runs.")
        self._status_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._status_lbl.setWordWrap(True)
        v.addWidget(self._status_lbl)

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
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        v.addWidget(self._table, stretch=1)

        nav = QHBoxLayout()
        back = QPushButton("← Back")
        back.setFixedWidth(70)
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        self._next_btn = QPushButton("Next →")
        self._next_btn.setMinimumHeight(34)
        self._next_btn.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        self._next_btn.clicked.connect(self._on_next)
        nav.addWidget(self._next_btn)
        v.addLayout(nav)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, pi_model):
        """Bind the working PI model (App owns it; Refine edits the same one)."""
        self._model = pi_model
        self._groups = []
        self._undo_snapshot = None
        self._undo_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._table.setRowCount(0)
        if pi_model is None:
            self._status_lbl.setText(
                "This level has no editable PI model (Level 1 is the raw "
                "polyline) — nothing to consolidate.")
            self._scan_btn.setEnabled(False)
        else:
            self._scan_btn.setEnabled(True)
            self._status_lbl.setText("Press Scan to look for mergeable runs.")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _on_scan(self):
        from geometry.candidates import find_consolidation_groups
        if self._model is None:
            return
        self._scan_btn.setEnabled(False)
        self._status_lbl.setText("Scanning…")
        try:
            self._groups = find_consolidation_groups(
                self._model,
                max_straight_m=self._straight_spin.value(),
                max_dev_m=self._dev_spin.value(),
                progress_cb=lambda m: self._status_lbl.setText(m),
            )
        except Exception as exc:
            self._status_lbl.setText(f"⚠ Scan failed: {exc}")
            self.log_message.emit(f"Consolidate scan failed: {exc}", "error")
            self._scan_btn.setEnabled(True)
            return
        self._scan_btn.setEnabled(True)
        self._populate()
        n_ok = sum(1 for g in self._groups if g["ok"])
        msg = (f"{len(self._groups)} run(s) found, {n_ok} within tolerance."
               if self._groups else
               "No runs of same-direction curves match the rules.")
        self._status_lbl.setText(msg)
        self.log_message.emit(f"Consolidate scan: {msg}", "info")
        self._apply_btn.setEnabled(n_ok > 0)

    def _populate(self):
        self._table.setRowCount(0)
        grey = QColor("#888888")
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
    # Apply / Undo
    # ------------------------------------------------------------------

    def _selected_groups(self) -> list:
        out = []
        for row, g in enumerate(self._groups):
            it = self._table.item(row, COL_CHK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(dict(g, ok=True))   # honour a manual tick
        return out

    def _on_apply(self):
        from geometry.candidates import apply_consolidation
        if self._model is None:
            return
        chosen = self._selected_groups()
        if not chosen:
            self._status_lbl.setText("No rows ticked.")
            return
        self._undo_snapshot = copy.deepcopy(self._model)
        n, msgs = apply_consolidation(self._model, chosen)
        for m in msgs:
            self.log_message.emit(f"Consolidate: {m}", "ok")
        self._undo_btn.setEnabled(n > 0)
        self._apply_btn.setEnabled(False)
        self._status_lbl.setText(
            f"Applied {n} of {len(chosen)} run(s). Re-scan to look for more.")
        self.log_message.emit(
            f"Consolidate: applied {n} run(s); "
            f"{len(self._model.elements)} elements now.", "ok")
        self._groups = []
        self._table.setRowCount(0)
        self.model_changed.emit()

    def _on_undo(self):
        if self._undo_snapshot is None or self._model is None:
            return
        # Restore in place so App/table keep pointing at the same object
        snap = self._undo_snapshot
        self._model.V           = snap.V
        self._model.idx         = snap.idx
        self._model.pis         = snap.pis
        self._model.elements    = snap.elements
        self._model.tangent_stubs = snap.tangent_stubs
        self._model.last_stats  = snap.last_stats
        self._undo_snapshot = None
        self._undo_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._groups = []
        self._status_lbl.setText("Undone. Press Scan to look again.")
        self.log_message.emit("Consolidate: undone.", "info")
        self.model_changed.emit()

    def _on_next(self):
        els = self._model.elements if self._model is not None else []
        self.consolidation_done.emit(list(els))
