"""
Element table dock — full-width panel under the map (Step 5).

Lists every element (Line / Arc / Spiral) of the selected level with its
parameters. For Level 2/3 candidates (which carry an editable PI model) the
table supports:

  • editing the curve radius (Arc rows) and spiral length (Spiral rows),
    with an automatic, debounced rebuild + map refresh;
  • omitting / restoring a Point of Intersection;
  • resetting a PI back to its auto-estimated values;
  • merging a short intermediate straight between two curves into
    prolonged, symmetric transition spirals (see candidates.merge_…).

The dock emits `rebuilt(elements, metrics)` after every model rebuild and
`element_selected(element_id)` when a row is clicked (map highlight).
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QColor, QFont


TYPE_COLORS = {
    "Line":   QColor("#42a5f5"),
    "Arc":    QColor("#ef5350"),
    "Spiral": QColor("#66bb6a"),
}

COLS = ["ID", "Type", "Station [km]", "Length [m]", "Radius [m]",
        "A [m]", "Defl [°]", "Spiral L [m]", "Actions"]
COL_ID, COL_TYPE, COL_STA, COL_LEN, COL_R, COL_A, COL_DEFL, COL_SL, COL_ACT = range(9)

# Lines shorter than this between two spirals qualify for the merge action
MERGE_LINE_THRESHOLD = 30.0


class ElementTableDock(QWidget):
    rebuilt          = Signal(list, dict)   # (elements, metrics) after model rebuild
    element_selected = Signal(str)          # element_id (row clicked)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model          = None     # PIAlignment or None (read-only)
        self._elements: list = []
        self._check_interval = 5.0
        self._populating     = False
        self._pending: dict  = {}       # pi_index -> {field: value}
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._apply_pending)
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("Alignment Elements")
        title.setFont(QFont("Helvetica", 10, QFont.Weight.Bold))
        header.addWidget(title)

        self._metrics_lbl = QLabel("")
        self._metrics_lbl.setStyleSheet("color: #999; font-size: 10px;")
        header.addWidget(self._metrics_lbl)
        header.addStretch()

        self._hint_lbl = QLabel(
            "Edit Radius (arc rows) or Spiral L (spiral rows) — the map "
            "updates automatically.  Omit PI removes a curve; neighbours "
            "absorb its deflection."
        )
        self._hint_lbl.setStyleSheet("color: #777; font-size: 9px;")
        header.addWidget(self._hint_lbl)
        v.addLayout(header)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_ACT, QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet("font-size: 10px;")
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.itemSelectionChanged.connect(self._on_selection)
        v.addWidget(self._table)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, pi_model, elements: list, check_interval: float = 5.0):
        """
        Show `elements`; if `pi_model` (PIAlignment) is given, enable editing.
        """
        self._model          = pi_model
        self._elements       = list(elements or [])
        self._check_interval = check_interval
        self._pending.clear()
        editable = pi_model is not None
        self._hint_lbl.setVisible(editable)
        self._populate()
        self._refresh_metrics()

    def clear(self):
        self._model    = None
        self._elements = []
        self._table.setRowCount(0)
        self._metrics_lbl.setText("")

    def current_elements(self) -> list:
        return self._elements

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _mk_item(self, text: str, editable: bool = False,
                 color: QColor | None = None, align_right: bool = True):
        it = QTableWidgetItem(text)
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
            it.setBackground(QColor(60, 60, 70))
        it.setFlags(flags)
        if color:
            it.setForeground(color)
        if align_right:
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        return it

    def _populate(self):
        self._populating = True
        try:
            self._table.setRowCount(0)
            editable = self._model is not None
            els = self._elements

            for i, el in enumerate(els):
                row = self._table.rowCount()
                self._table.insertRow(row)
                et    = el.get("type", "?")
                col   = TYPE_COLORS.get(et)
                eid   = el.get("element_id", f"#{i}")
                sta   = float(el.get("sta_start", 0.0))
                length = float(el.get("length", 0.0))

                self._table.setItem(row, COL_ID,   self._mk_item(eid, color=col, align_right=False))
                self._table.setItem(row, COL_TYPE, self._mk_item(self._type_label(el), color=col, align_right=False))
                self._table.setItem(row, COL_STA,  self._mk_item(f"{sta/1000.0:.3f}"))
                self._table.setItem(row, COL_LEN,  self._mk_item(f"{length:.2f}"))

                if et == "Arc":
                    r = float(el.get("radius", 0.0))
                    self._table.setItem(row, COL_R, self._mk_item(f"{r:.1f}", editable=editable))
                    defl = el.get("_deflection")
                    self._table.setItem(row, COL_DEFL,
                        self._mk_item(f"{math.degrees(defl):+.2f}" if defl is not None else "—"))
                    self._table.setItem(row, COL_A,  self._mk_item("—"))
                    self._table.setItem(row, COL_SL, self._mk_item("—"))
                elif et == "Spiral":
                    r_fin = self._spiral_R(el)
                    self._table.setItem(row, COL_R, self._mk_item(
                        f"{r_fin:.1f}" if math.isfinite(r_fin) else "∞"))
                    self._table.setItem(row, COL_A,
                        self._mk_item(f"{float(el.get('clothoid_A', 0.0)):.1f}"))
                    self._table.setItem(row, COL_DEFL, self._mk_item("—"))
                    self._table.setItem(row, COL_SL,
                        self._mk_item(f"{length:.1f}", editable=editable))
                else:  # Line
                    for c in (COL_R, COL_A, COL_DEFL, COL_SL):
                        self._table.setItem(row, c, self._mk_item("—"))

                # Row metadata for edit routing
                self._table.item(row, COL_ID).setData(Qt.ItemDataRole.UserRole,
                                                      {"pi": el.get("_pi"), "etype": et,
                                                       "elem_index": i})

                self._add_actions(row, i, el, editable)

            # Omitted-PI section (restorable)
            if editable:
                for pid in self._model.pis:
                    if not pid.omitted:
                        continue
                    row = self._table.rowCount()
                    self._table.insertRow(row)
                    grey = QColor("#777777")
                    it = self._mk_item(f"PI {pid.index}", color=grey, align_right=False)
                    f = it.font(); f.setStrikeOut(True); it.setFont(f)
                    self._table.setItem(row, COL_ID, it)
                    self._table.setItem(row, COL_TYPE,
                        self._mk_item("omitted PI", color=grey, align_right=False))
                    self._table.setItem(row, COL_DEFL,
                        self._mk_item(f"{math.degrees(pid.deflection):+.2f}", color=grey))
                    for c in (COL_STA, COL_LEN, COL_R, COL_A, COL_SL):
                        self._table.setItem(row, c, self._mk_item("—", color=grey))
                    self._table.item(row, COL_ID).setData(
                        Qt.ItemDataRole.UserRole, {"pi": pid.index, "etype": "omitted"})
                    btn = QPushButton("Restore PI")
                    btn.setStyleSheet("font-size: 9px; padding: 1px 6px;")
                    btn.clicked.connect(lambda _=False, k=pid.index: self._restore_pi(k))
                    self._table.setCellWidget(row, COL_ACT, self._wrap_buttons([btn]))
        finally:
            self._populating = False

    def _type_label(self, el: dict) -> str:
        et = el.get("type", "?")
        if et == "Spiral":
            r_st = float(el.get("radius_start", float("inf")))
            return "Spiral (entry)" if math.isinf(r_st) else "Spiral (exit)"
        return et

    @staticmethod
    def _spiral_R(el: dict) -> float:
        r_st = float(el.get("radius_start", float("inf")))
        r_en = float(el.get("radius_end",   float("inf")))
        return r_en if math.isinf(r_st) else r_st

    @staticmethod
    def _wrap_buttons(buttons: list) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(4)
        for b in buttons:
            h.addWidget(b)
        h.addStretch()
        return w

    def _add_actions(self, row: int, elem_index: int, el: dict, editable: bool):
        if not editable:
            return
        buttons = []
        et = el.get("type")
        pi = el.get("_pi")

        if et == "Arc" and pi is not None:
            omit = QPushButton("Omit PI")
            omit.setStyleSheet("font-size: 9px; padding: 1px 6px;")
            omit.setToolTip("Remove this curve; neighbouring curves absorb its deflection.")
            omit.clicked.connect(lambda _=False, k=pi: self._omit_pi(k))
            buttons.append(omit)

            reset = QPushButton("Reset")
            reset.setStyleSheet("font-size: 9px; padding: 1px 6px;")
            reset.setToolTip("Reset radius and spiral length to their auto-estimated values.")
            reset.clicked.connect(lambda _=False, k=pi: self._reset_pi(k))
            buttons.append(reset)

            pid = next((p for p in self._model.pis if p.index == pi), None)
            if pid is not None and pid.merged_with_next:
                undo = QPushButton("Undo merge")
                undo.setStyleSheet("font-size: 9px; padding: 1px 6px; color: #ffd54f;")
                undo.clicked.connect(lambda _=False, k=pi: self._undo_merge(k))
                buttons.append(undo)

        elif et == "Line":
            # Merge action: short straight sandwiched between an exit spiral
            # and an entry spiral of two different curves.
            if (0 < elem_index < len(self._elements) - 1
                    and float(el.get("length", 0.0)) < MERGE_LINE_THRESHOLD):
                prev_el = self._elements[elem_index - 1]
                next_el = self._elements[elem_index + 1]
                if (prev_el.get("type") == "Spiral" and next_el.get("type") == "Spiral"
                        and math.isinf(float(prev_el.get("radius_end", 0.0) or 0.0))
                        and math.isinf(float(next_el.get("radius_start", 0.0) or 0.0))
                        and prev_el.get("_pi") is not None
                        and next_el.get("_pi") is not None):
                    merge = QPushButton("Merge spirals ↔")
                    merge.setStyleSheet(
                        "font-size: 9px; padding: 1px 6px; color: #ffd54f;")
                    merge.setToolTip(
                        "Remove this short straight by prolonging the adjacent\n"
                        "transition spirals (kept symmetrical on both curves).")
                    merge.clicked.connect(
                        lambda _=False, a=prev_el.get("_pi"), b=next_el.get("_pi"):
                        self._merge_spirals(a, b))
                    buttons.append(merge)

        if buttons:
            self._table.setCellWidget(row, COL_ACT, self._wrap_buttons(buttons))

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _on_cell_changed(self, row: int, col: int):
        if self._populating or self._model is None:
            return
        meta_item = self._table.item(row, COL_ID)
        if meta_item is None:
            return
        meta = meta_item.data(Qt.ItemDataRole.UserRole) or {}
        pi = meta.get("pi")
        if pi is None:
            return
        try:
            value = float(self._table.item(row, col).text().replace(",", "."))
        except (ValueError, AttributeError):
            self._populate()      # revert malformed input
            return

        if col == COL_R and meta.get("etype") == "Arc":
            self._pending.setdefault(pi, {})["radius"] = max(1.0, value)
        elif col == COL_SL and meta.get("etype") == "Spiral":
            self._pending.setdefault(pi, {})["spiral_len"] = max(0.0, value)
        else:
            return
        self._debounce.start()

    def _apply_pending(self):
        if self._model is None or not self._pending:
            return
        by_index = {p.index: p for p in self._model.pis}
        for pi_idx, changes in self._pending.items():
            pid = by_index.get(pi_idx)
            if pid is None:
                continue
            for field_name, value in changes.items():
                setattr(pid, field_name, value)
        self._pending.clear()
        self._rebuild()

    def _omit_pi(self, pi_index: int):
        self._set_pi(pi_index, omitted=True)

    def _restore_pi(self, pi_index: int):
        self._set_pi(pi_index, omitted=False)

    def _reset_pi(self, pi_index: int):
        pid = next((p for p in self._model.pis if p.index == pi_index), None)
        if pid is None:
            return
        pid.radius = -1.0
        pid.spiral_len = -1.0
        pid.merged_with_next = False
        self._rebuild()

    def _set_pi(self, pi_index: int, omitted: bool):
        if self._model is None:
            return
        pid = next((p for p in self._model.pis if p.index == pi_index), None)
        if pid is None:
            return
        pid.omitted = omitted
        self._rebuild()

    def _merge_spirals(self, pi_a: int, pi_b: int):
        from geometry.candidates import merge_intermediate_line
        from PySide6.QtWidgets import QMessageBox
        if self._model is None:
            return
        ok, msg = merge_intermediate_line(self._model, pi_a, pi_b)
        if not ok:
            QMessageBox.warning(self, "Cannot merge spirals", msg)
            return
        self._rebuild()

    def _undo_merge(self, pi_a: int):
        from geometry.candidates import undo_merge
        if self._model is None:
            return
        undo_merge(self._model, pi_a)
        self._rebuild()

    # ------------------------------------------------------------------
    # Rebuild + metrics
    # ------------------------------------------------------------------

    def _rebuild(self):
        from geometry.candidates import rebuild_from_pi_model, evaluate_candidate
        els = rebuild_from_pi_model(self._model)
        metrics = evaluate_candidate(
            els, self._model.xy_ref, self._model.chainages_ref, self._check_interval)
        self._elements = els
        self._populate()
        self._refresh_metrics(metrics)
        self.rebuilt.emit(els, metrics)

    def _refresh_metrics(self, metrics: dict | None = None):
        if metrics is None:
            self._metrics_lbl.setText(f"· {len(self._elements)} elements")
            return
        self._metrics_lbl.setText(
            f"· {len(self._elements)} elements   "
            f"max dev {metrics.get('max_deviation', 0.0):.2f} m   "
            f"RMSE {metrics.get('rmse', 0.0):.2f} m   "
            f"C1 {metrics.get('max_heading_jump_deg', 0.0):.3f}°"
        )

    # ------------------------------------------------------------------
    # Selection → map highlight
    # ------------------------------------------------------------------

    def _on_selection(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        it = self._table.item(rows[0].row(), COL_ID)
        if it is None:
            return
        meta = it.data(Qt.ItemDataRole.UserRole) or {}
        if meta.get("etype") not in (None, "omitted"):
            self.element_selected.emit(it.text())
