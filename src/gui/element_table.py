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
# Lines longer than this offer "Split here" (inserts a PI at its midpoint)
MIN_SPLIT_LINE_LENGTH = 15.0


class ElementTableDock(QWidget):
    rebuilt                  = Signal(list, dict)  # (elements, metrics) after rebuild
    elements_selected        = Signal(list)        # selected element_ids (map highlight)
    log_message              = Signal(str, str)    # (text, level) → log panel
    show_alignment_requested = Signal()            # re-draw alignment on the map
    split_pick_mode          = Signal(bool)        # "Pick split point" toggled

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

        self._show_btn = QPushButton("🗺 Show alignment")
        self._show_btn.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 3px 10px; "
            "background: #2a82da; color: #ffffff; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #3b93eb; }")
        self._show_btn.setToolTip(
            "Re-draw the edited alignment and the PI overlay on the map\n"
            "(useful after the map was reloaded and the overlays vanished)."
        )
        self._show_btn.clicked.connect(self.show_alignment_requested.emit)
        header.addWidget(self._show_btn)

        self._range_btn = QPushButton("Merge PI range → single curve")
        self._range_btn.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 3px 10px; "
            "background: #ffb300; color: #212121; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #ffc633; }"
            "QPushButton:disabled { background: #4a4a4e; color: #8a8a8e; }")
        self._range_btn.setToolTip(
            "Select the rows spanning two or more curves (e.g. the first and\n"
            "last tangent of the section, or Ctrl+click them on the map).\n"
            "All PIs in between are replaced by ONE Spiral–Arc–Spiral: the\n"
            "outer tangents are kept, the new PI is their intersection."
        )
        self._range_btn.setEnabled(False)
        self._range_btn.clicked.connect(self._on_merge_range)
        header.addWidget(self._range_btn)

        self._trim_start_btn = QPushButton("✂ Trim start")
        self._trim_start_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        self._trim_start_btn.setToolTip(
            "Discard everything before the selected row — the alignment\n"
            "starts fresh at that point.")
        self._trim_start_btn.setEnabled(False)
        self._trim_start_btn.clicked.connect(lambda: self._on_trim("start"))
        header.addWidget(self._trim_start_btn)

        self._trim_end_btn = QPushButton("✂ Trim end")
        self._trim_end_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        self._trim_end_btn.setToolTip(
            "Discard everything after the selected row — the alignment\n"
            "ends at that point.")
        self._trim_end_btn.setEnabled(False)
        self._trim_end_btn.clicked.connect(lambda: self._on_trim("end"))
        header.addWidget(self._trim_end_btn)

        self._pick_split_btn = QPushButton("📍 Pick split point")
        self._pick_split_btn.setCheckable(True)
        self._pick_split_btn.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 3px 10px; }"
            "QPushButton:checked { background: #2a82da; color: #ffffff; "
            "font-weight: bold; }")
        self._pick_split_btn.setToolTip(
            "Click, then click a point on the map to insert a PI there\n"
            "(splits whichever Line currently covers that point).\n"
            "Stays armed for multiple picks — click again to stop.")
        self._pick_split_btn.toggled.connect(self.split_pick_mode.emit)
        header.addWidget(self._pick_split_btn)
        v.addLayout(header)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_ACT, QHeaderView.ResizeMode.Stretch)
        # Taller, easier-to-hit rows; slightly larger font
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.setStyleSheet(
            "QTableWidget { font-size: 11px; }"
            "QTableWidget::item { padding: 4px 6px; }"
            "QTableWidget::item:selected { background: #2a82da; color: #ffffff; }"
        )
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

    def _displayed_ids(self) -> list:
        """element_id of every currently displayed row (incl. omitted-PI rows)."""
        out = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, COL_ID)
            out.append(it.text() if it is not None else None)
        return out

    def _expected_ids(self) -> list:
        """The ids _populate() would produce for the current model/elements."""
        out = [el.get("element_id", f"#{i}") for i, el in enumerate(self._elements)]
        if self._model is not None:
            out += [f"PI {p.index}" for p in self._model.pis if p.omitted]
        return out

    def _update_in_place(self) -> bool:
        """
        Fast path: the row structure is unchanged (the usual case for radius /
        spiral-length edits — ids only change on merges and omits), so just
        refresh the value cells instead of tearing down and recreating ~6 300
        Qt objects (items + per-row buttons).
        """
        if self._displayed_ids() != self._expected_ids():
            return False
        self._populating = True
        self._table.setUpdatesEnabled(False)
        try:
            for i, el in enumerate(self._elements):
                et     = el.get("type", "?")
                sta    = float(el.get("sta_start", 0.0))
                length = float(el.get("length", 0.0))
                self._table.item(i, COL_STA).setText(f"{sta/1000.0:.3f}")
                self._table.item(i, COL_LEN).setText(f"{length:.2f}")
                if et == "Arc":
                    self._table.item(i, COL_R).setText(
                        f"{float(el.get('radius', 0.0)):.1f}")
                    defl = el.get("_deflection")
                    self._table.item(i, COL_DEFL).setText(
                        f"{math.degrees(defl):+.2f}" if defl is not None else "—")
                elif et == "Spiral":
                    r_fin = self._spiral_R(el)
                    self._table.item(i, COL_R).setText(
                        f"{r_fin:.1f}" if math.isfinite(r_fin) else "∞")
                    self._table.item(i, COL_A).setText(
                        f"{float(el.get('clothoid_A', 0.0)):.1f}")
                    self._table.item(i, COL_SL).setText(f"{length:.1f}")
        except Exception:
            return False
        finally:
            self._populating = False
            self._table.setUpdatesEnabled(True)
        return True

    def _row_specs(self) -> list:
        """One spec per row _populate would produce (elements + omitted PIs)."""
        editable = self._model is not None
        specs = []
        for i, el in enumerate(self._elements):
            specs.append({
                "kind": "el", "i": i, "el": el,
                "id": el.get("element_id", f"#{i}"),
                "sig": (el.get("type", "?"),) + self._action_sig(
                    el, self._elements, i, self._model, editable),
            })
        if editable:
            for pid in self._model.pis:
                if pid.omitted:
                    specs.append({"kind": "om", "pid": pid,
                                  "id": f"PI {pid.index}", "sig": ("omitted",)})
        return specs

    def _fill_row(self, row: int, spec: dict):
        """Create all items + action widget for one row from its spec."""
        editable = self._model is not None
        if spec["kind"] == "om":
            pid = spec["pid"]
            grey = QColor("#777777")
            it = self._mk_item(spec["id"], color=grey, align_right=False)
            f = it.font(); f.setStrikeOut(True); it.setFont(f)
            self._table.setItem(row, COL_ID, it)
            self._table.setItem(row, COL_TYPE,
                self._mk_item("omitted PI", color=grey, align_right=False))
            self._table.setItem(row, COL_DEFL,
                self._mk_item(f"{math.degrees(pid.deflection):+.2f}", color=grey))
            for c in (COL_STA, COL_LEN, COL_R, COL_A, COL_SL):
                self._table.setItem(row, c, self._mk_item("—", color=grey))
            it.setData(Qt.ItemDataRole.UserRole,
                       {"pi": pid.index, "etype": "omitted", "sig": spec["sig"]})
            btn = QPushButton("Restore PI")
            btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
            delete = QPushButton("🗑 Delete")
            delete.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 2px 8px; "
                "color: #ff8a80; }")
            delete.setToolTip(
                "Physically remove this PI (it is currently only omitted,\n"
                "not deleted — the vertex is still in the tangent polygon).")
            wrap = self._wrap_buttons([btn, delete])
            wrap._meta = {"pi": pid.index}
            btn.clicked.connect(lambda _=False, w=wrap: self._restore_pi(w._meta["pi"]))
            delete.clicked.connect(lambda _=False, w=wrap: self._delete_pi(w._meta["pi"]))
            self._table.setCellWidget(row, COL_ACT, wrap)
            return

        i, el = spec["i"], spec["el"]
        et    = el.get("type", "?")
        col   = TYPE_COLORS.get(et)
        sta   = float(el.get("sta_start", 0.0))
        length = float(el.get("length", 0.0))

        self._table.setItem(row, COL_ID,   self._mk_item(spec["id"], color=col, align_right=False))
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

        self._table.item(row, COL_ID).setData(
            Qt.ItemDataRole.UserRole,
            {"pi": el.get("_pi"), "etype": et, "elem_index": i,
             "sig": spec["sig"]})
        self._add_actions(row, i, el, editable)

    def _refresh_row(self, row: int, spec: dict):
        """Update a structurally-matching row in place (texts + meta only)."""
        it = self._table.item(row, COL_ID)
        it.setText(spec["id"])
        if spec["kind"] == "om":
            pid = spec["pid"]
            self._table.item(row, COL_DEFL).setText(
                f"{math.degrees(pid.deflection):+.2f}")
            it.setData(Qt.ItemDataRole.UserRole,
                       {"pi": pid.index, "etype": "omitted", "sig": spec["sig"]})
            wrap = self._table.cellWidget(row, COL_ACT)
            if wrap is not None and hasattr(wrap, "_meta"):
                wrap._meta["pi"] = pid.index
            return
        i, el = spec["i"], spec["el"]
        et     = el.get("type", "?")
        sta    = float(el.get("sta_start", 0.0))
        length = float(el.get("length", 0.0))
        self._table.item(row, COL_STA).setText(f"{sta/1000.0:.3f}")
        self._table.item(row, COL_LEN).setText(f"{length:.2f}")
        if et == "Arc":
            self._table.item(row, COL_R).setText(f"{float(el.get('radius', 0.0)):.1f}")
            defl = el.get("_deflection")
            self._table.item(row, COL_DEFL).setText(
                f"{math.degrees(defl):+.2f}" if defl is not None else "—")
        elif et == "Spiral":
            r_fin = self._spiral_R(el)
            self._table.item(row, COL_R).setText(
                f"{r_fin:.1f}" if math.isfinite(r_fin) else "∞")
            self._table.item(row, COL_A).setText(
                f"{float(el.get('clothoid_A', 0.0)):.1f}")
            self._table.item(row, COL_SL).setText(f"{length:.1f}")
        it.setData(Qt.ItemDataRole.UserRole,
                   {"pi": el.get("_pi"), "etype": et, "elem_index": i,
                    "sig": spec["sig"]})
        wrap = self._table.cellWidget(row, COL_ACT)
        if wrap is not None and hasattr(wrap, "_meta"):
            if "elem_index" in wrap._meta:    # Line row (Split and/or Merge)
                wrap._meta["elem_index"] = i
                if "pi_b" in wrap._meta and 0 < i < len(self._elements) - 1:
                    wrap._meta["pi"] = self._elements[i - 1].get("_pi")
                    wrap._meta["pi_b"] = self._elements[i + 1].get("_pi")
            else:
                wrap._meta["pi"] = el.get("_pi")

    def _update_diff(self) -> bool:
        """
        Structural diff: keep the common prefix (same id + signature) and
        common suffix (same signature; ids/stations are refreshed in place —
        they shift after a merge renumbers downstream elements), rebuild only
        the middle rows. Turns a merge's table update from ~N rows of Qt
        churn into ~6.
        """
        specs = self._row_specs()
        n_new, n_old = len(specs), self._table.rowCount()
        lim = min(n_old, n_new)

        p = 0
        while p < lim:
            it = self._table.item(p, COL_ID)
            if it is None:
                return False
            meta = it.data(Qt.ItemDataRole.UserRole) or {}
            if it.text() == specs[p]["id"] and meta.get("sig") == specs[p]["sig"]:
                p += 1
            else:
                break

        s = 0
        while s < lim - p:
            it = self._table.item(n_old - 1 - s, COL_ID)
            if it is None:
                return False
            meta = it.data(Qt.ItemDataRole.UserRole) or {}
            if meta.get("sig") == specs[n_new - 1 - s]["sig"]:
                s += 1
            else:
                break

        if p + s == 0:
            return False                     # nothing reusable — full rebuild
        mid_old = n_old - p - s
        mid_new = n_new - p - s

        self._populating = True
        self._table.setUpdatesEnabled(False)
        try:
            for _ in range(mid_old):
                self._table.removeRow(p)
            for q in range(mid_new):
                self._table.insertRow(p + q)
                self._fill_row(p + q, specs[p + q])
            for q in range(s):
                r = p + mid_new + q
                self._refresh_row(r, specs[r])
        finally:
            self._populating = False
            self._table.setUpdatesEnabled(True)
        return True

    def _populate(self):
        # Fast paths first — keep interactive edits snappy on long alignments:
        # identical structure → cell updates only; localized change → diff.
        if self._table.rowCount() and self._update_in_place():
            return
        if self._table.rowCount() and self._update_diff():
            return

        # Preserve the current selection (blue rows) across the repopulation
        # that follows every value edit / rebuild.
        prev_sel: set = set()
        sm = self._table.selectionModel()
        if sm is not None:
            for r in (x.row() for x in sm.selectedRows()):
                it = self._table.item(r, COL_ID)
                if it is not None:
                    prev_sel.add(it.text())

        self._populating = True
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(0)
            for row, spec in enumerate(self._row_specs()):
                self._table.insertRow(row)
                self._fill_row(row, spec)
        finally:
            self._populating = False
            self._table.setUpdatesEnabled(True)

        # Restore the previous selection so edited rows stay highlighted
        if prev_sel:
            from PySide6.QtCore import QItemSelectionModel
            sm = self._table.selectionModel()
            self._table.blockSignals(True)
            try:
                for r in range(self._table.rowCount()):
                    it = self._table.item(r, COL_ID)
                    if it is not None and it.text() in prev_sel:
                        sm.select(self._table.model().index(r, 0),
                                  QItemSelectionModel.SelectionFlag.Select
                                  | QItemSelectionModel.SelectionFlag.Rows)
            finally:
                self._table.blockSignals(False)
            self._on_selection()   # single re-emit → map glow restored

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
    def _action_sig(el: dict, elements: list, elem_index: int,
                    model, editable: bool) -> tuple:
        """
        Signature of the action-button set a row needs. Rows whose signature
        is unchanged keep their widgets across rebuilds (the diff path
        rewrites only the target PI in the widget's meta dict).
        """
        if not editable:
            return ()
        et = el.get("type")
        pi = el.get("_pi")
        if et == "Arc" and pi is not None:
            pid = next((p for p in model.pis if p.index == pi), None)
            merged = bool(pid is not None and pid.merged_with_next)
            return ("arc", merged)
        if et == "Line":
            can_merge = False
            if (0 < elem_index < len(elements) - 1
                    and float(el.get("length", 0.0)) < MERGE_LINE_THRESHOLD):
                prev_el = elements[elem_index - 1]
                next_el = elements[elem_index + 1]
                if (prev_el.get("type") == "Spiral"
                        and next_el.get("type") == "Spiral"
                        and math.isinf(float(prev_el.get("radius_end", 0.0) or 0.0))
                        and math.isinf(float(next_el.get("radius_start", 0.0) or 0.0))
                        and prev_el.get("_pi") is not None
                        and next_el.get("_pi") is not None):
                    can_merge = True
            can_split = float(el.get("length", 0.0)) >= MIN_SPLIT_LINE_LENGTH
            if can_merge or can_split:
                return ("line", can_merge, can_split)
            return ()
        return ()

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
        """
        Create the action-button widget for a row. Callbacks read the target
        PI from the widget's mutable `_meta` dict (never from a closure), so
        the diff path can retarget a reused widget with a dict update.
        """
        sig = self._action_sig(el, self._elements, elem_index,
                               self._model, editable)
        if not sig:
            return
        et = el.get("type")
        pi = el.get("_pi")
        buttons = []
        meta = {"pi": pi}

        if sig[0] == "arc":
            omit = QPushButton("Omit PI")
            omit.setStyleSheet("font-size: 10px; padding: 2px 8px;")
            omit.setToolTip("Remove this curve; neighbouring curves absorb its deflection.")
            buttons.append(omit)

            reset = QPushButton("Reset")
            reset.setStyleSheet("font-size: 10px; padding: 2px 8px;")
            reset.setToolTip("Reset radius and spiral length to their auto-estimated values.")
            buttons.append(reset)

            delete = QPushButton("🗑 Delete")
            delete.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 2px 8px; "
                "color: #ff8a80; }")
            delete.setToolTip(
                "Physically remove this PI — unlike Omit, the tangent\n"
                "polygon loses the vertex and its neighbours connect directly.")
            buttons.append(delete)

            if sig[1]:
                buttons.append(self._mk_amber_button("Undo merge"))
            wrap = self._wrap_buttons(buttons)
            wrap._meta = meta
            omit.clicked.connect(lambda _=False, w=wrap: self._omit_pi(w._meta["pi"]))
            reset.clicked.connect(lambda _=False, w=wrap: self._reset_pi(w._meta["pi"]))
            delete.clicked.connect(lambda _=False, w=wrap: self._delete_pi(w._meta["pi"]))
            if sig[1]:
                undo = wrap.layout().itemAt(3).widget()
                undo.clicked.connect(
                    lambda _=False, w=wrap: self._undo_merge(w._meta["pi"]))
            self._table.setCellWidget(row, COL_ACT, wrap)
            return

        if sig[0] == "line":
            _, can_merge, can_split = sig
            meta = {"elem_index": elem_index}
            buttons = []
            if can_split:
                split = QPushButton("✂ Split here")
                split.setStyleSheet("font-size: 10px; padding: 2px 8px;")
                split.setToolTip(
                    "Insert a new PI at this line's midpoint — lets the fit\n"
                    "pick up a curve that Douglas-Peucker simplified away.")
                buttons.append(split)
            if can_merge:
                prev_el = self._elements[elem_index - 1]
                next_el = self._elements[elem_index + 1]
                meta["pi"] = prev_el.get("_pi")
                meta["pi_b"] = next_el.get("_pi")
                merge = self._mk_amber_button("Merge spirals ↔")
                merge.setToolTip(
                    "Remove this short straight by prolonging the adjacent\n"
                    "transition spirals (kept symmetrical on both curves).")
                buttons.append(merge)
            wrap = self._wrap_buttons(buttons)
            wrap._meta = meta
            if can_split:
                split.clicked.connect(
                    lambda _=False, w=wrap: self._split_line(w._meta["elem_index"]))
            if can_merge:
                merge.clicked.connect(
                    lambda _=False, w=wrap:
                    self._merge_spirals(w._meta["pi"], w._meta["pi_b"]))
            self._table.setCellWidget(row, COL_ACT, wrap)

    @staticmethod
    def _mk_amber_button(text: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(
            "QPushButton { font-size: 10px; padding: 2px 8px; "
            "background: #ffb300; color: #212121; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #ffc633; }")
        return b

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
        labels = {"radius": "radius", "spiral_len": "spiral length"}
        for pi_idx, changes in self._pending.items():
            pid = by_index.get(pi_idx)
            if pid is None:
                continue
            for field_name, value in changes.items():
                setattr(pid, field_name, value)
                self.log_message.emit(
                    f"PI {pi_idx}: {labels.get(field_name, field_name)} → "
                    f"{value:.1f} m requested…", "info")
        changed = list(self._pending.keys())
        self._pending.clear()
        if changed:
            self._rebuild(span=(min(changed), max(changed)))
        else:
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
        self._rebuild(span=(pi_index, pi_index))

    def _set_pi(self, pi_index: int, omitted: bool):
        if self._model is None:
            return
        pid = next((p for p in self._model.pis if p.index == pi_index), None)
        if pid is None:
            return
        pid.omitted = omitted
        self._rebuild(span=(pi_index, pi_index))

    def insert_pi_at_xy(self, point_xy) -> tuple[bool, str]:
        """
        Insert a PI at the OSM point nearest `point_xy` (projected coords —
        the map-click pick mode; app.py projects lat/lon before calling
        this). Returns (ok, message); logs and refreshes on success.
        """
        from geometry.candidates import insert_pi, nearest_xy_ref_index
        if self._model is None:
            return False, "No editable model."
        j_ref = nearest_xy_ref_index(self._model, point_xy)
        ok, msg = insert_pi(self._model, j_ref)
        if ok:
            self.log_message.emit(msg, "ok")
            self._rebuild(regenerate=False)
        else:
            self.log_message.emit(f"Split at picked point: {msg}", "warn")
        return ok, msg

    def _split_line(self, elem_index: int):
        from geometry.candidates import insert_pi, nearest_xy_ref_index
        from PySide6.QtWidgets import QMessageBox
        if self._model is None or elem_index >= len(self._elements):
            return
        el = self._elements[elem_index]
        mid = [(el["start"][0] + el["end"][0]) / 2.0,
               (el["start"][1] + el["end"][1]) / 2.0]
        j_ref = nearest_xy_ref_index(self._model, mid)
        ok, msg = insert_pi(self._model, j_ref)
        if not ok:
            self.log_message.emit(f"Split line: {msg}", "warn")
            QMessageBox.information(self, "Cannot split here", msg)
            return
        self.log_message.emit(msg, "ok")
        self._rebuild(regenerate=False)   # insert_pi already rebuilt

    def _delete_pi(self, pi_index: int):
        from geometry.candidates import delete_pi
        from PySide6.QtWidgets import QMessageBox
        if self._model is None:
            return
        reply = QMessageBox.question(
            self, "Delete PI",
            f"Physically remove PI {pi_index}? Its neighbouring curves "
            "will connect directly.\n\nThis is different from Omit, which "
            "keeps the vertex and can be restored.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = delete_pi(self._model, pi_index)
        if not ok:
            self.log_message.emit(f"Delete PI {pi_index}: {msg}", "warn")
            QMessageBox.warning(self, "Cannot delete PI", msg)
            return
        self.log_message.emit(msg, "ok")
        self._rebuild(regenerate=False)   # delete_pi already rebuilt

    def _on_trim(self, which: str):
        from geometry.candidates import trim_alignment, nearest_xy_ref_index
        from PySide6.QtWidgets import QMessageBox
        el = self._selected_single_element()
        if self._model is None or el is None:
            return
        pt = el["start"] if which == "start" else el["end"]
        j_ref = nearest_xy_ref_index(self._model, pt)
        n = len(self._model.xy_ref)
        j_start, j_end = (j_ref, n - 1) if which == "start" else (0, j_ref)
        removed_pts = j_start + (n - 1 - j_end)
        reply = QMessageBox.question(
            self, f"Trim {which}",
            f"Discard the alignment {'before' if which == 'start' else 'after'} "
            f"the selected row?\n\n{removed_pts} of {n} OSM points will be "
            "removed. This cannot be undone (other than reloading the "
            "project or track).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = trim_alignment(self._model, j_start, j_end)
        if not ok:
            self.log_message.emit(f"Trim {which}: {msg}", "warn")
            QMessageBox.warning(self, "Cannot trim", msg)
            return
        self.log_message.emit(msg, "ok")
        self._rebuild(regenerate=False)   # trim_alignment already rebuilt

    def _merge_spirals(self, pi_a: int, pi_b: int):
        from geometry.candidates import merge_intermediate_line
        from PySide6.QtWidgets import QMessageBox
        if self._model is None:
            return
        ok, msg = merge_intermediate_line(self._model, pi_a, pi_b)
        if not ok:
            self.log_message.emit(f"Merge spirals PI {pi_a}+{pi_b}: {msg}", "warn")
            QMessageBox.warning(self, "Cannot merge spirals", msg)
            return
        self.log_message.emit(f"Merge spirals PI {pi_a}+{pi_b}: {msg}", "ok")
        self._rebuild(regenerate=False)   # merge already rebuilt the geometry

    def _undo_merge(self, pi_a: int):
        from geometry.candidates import undo_merge
        if self._model is None:
            return
        undo_merge(self._model, pi_a)
        self.log_message.emit(f"Merge at PI {pi_a} undone.", "info")
        self._rebuild(regenerate=False)   # undo already rebuilt the geometry

    def selected_merge_pi(self) -> int | None:
        """
        The PI a toolbar "Undo merge" quick action would target: the single
        selected row's own merge, or its partner's if it's the second half
        of a pair. None if nothing selected/mergeable is selected.
        """
        if self._model is None:
            return None
        rows = self._table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        it = self._table.item(rows[0].row(), COL_ID)
        if it is None:
            return None
        meta = it.data(Qt.ItemDataRole.UserRole) or {}
        pi = meta.get("pi")
        pid = next((p for p in self._model.pis if p.index == pi), None)
        if pid is None:
            return None
        if pid.merged_with_next:
            return pid.index
        if pid.merged_with_prev:
            return pid.merge_partner
        return None

    def undo_selected_merge(self) -> bool:
        """Undo the merge targeted by `selected_merge_pi`. False if none."""
        pi_a = self.selected_merge_pi()
        if pi_a is None:
            return False
        self._undo_merge(pi_a)
        return True

    # ------------------------------------------------------------------
    # Rebuild + metrics
    # ------------------------------------------------------------------

    def _rebuild(self, regenerate: bool = True,
                 span: tuple | None = None):
        """
        Refresh table + map from the model.

        `regenerate=False` is used by the merge/undo handlers: those functions
        already rebuilt the geometry internally. `span=(k_lo, k_hi)` routes a
        value edit / omit / reset through the span-local rebuild — on long
        lines that is ~60x faster than the full reconstruction.
        """
        from geometry.candidates import (rebuild_from_pi_model,
                                         rebuild_pi_span,
                                         metrics_from_stats, evaluate_candidate)
        if regenerate and span is not None:
            rebuild_pi_span(self._model, span[0], span[1])
            els = self._model.elements
        elif regenerate:
            els = rebuild_from_pi_model(self._model)
        else:
            els = self._model.elements
        # The rebuild already made exactly one deviation pass — reuse it.
        stats = getattr(self._model, "last_stats", None)
        if stats:
            metrics = metrics_from_stats(stats, els)
        else:
            metrics = evaluate_candidate(
                els, self._model.xy_ref, self._model.chainages_ref,
                self._check_interval)
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

    def _selected_pi_range(self) -> tuple[int, int] | None:
        """
        Derive the PI range from the current row selection.

        Any selection spanning ≥ 2 distinct PIs works: curve rows contribute
        their own PI; selecting the first and last tangent Lines of a section
        contributes every PI of the elements between them.
        """
        rows = sorted(r.row() for r in self._table.selectionModel().selectedRows())
        if len(rows) < 2:
            return None
        # Element indices of the selected rows (skip omitted-PI pseudo rows)
        idxs = []
        for r in rows:
            it = self._table.item(r, COL_ID)
            meta = (it.data(Qt.ItemDataRole.UserRole) or {}) if it else {}
            if meta.get("etype") in (None, "omitted"):
                continue
            idxs.append(meta.get("elem_index", -1))
        idxs = [i for i in idxs if i >= 0]
        if len(idxs) < 2:
            return None
        lo, hi = min(idxs), max(idxs)
        pis = sorted({e.get("_pi") for e in self._elements[lo:hi + 1]
                      if e.get("_pi") is not None})
        if len(pis) < 2:
            return None
        return pis[0], pis[-1]

    def _on_merge_range(self):
        from geometry.candidates import merge_pi_range, merge_range_ambiguity
        from PySide6.QtWidgets import QMessageBox
        if self._model is None:
            return
        rng = self._selected_pi_range()
        if rng is None:
            QMessageBox.information(
                self, "Merge PI range",
                "Select rows spanning at least two curves first "
                "(e.g. the first and last tangent of the section).")
            return

        prefer = None
        choice = merge_range_ambiguity(self._model, rng[0], rng[1])
        if choice is not None and choice.needs_choice:
            prefer = self._ask_merge_choice(choice)
            if prefer is None:
                return   # user cancelled

        ok, msg = merge_pi_range(self._model, rng[0], rng[1], prefer=prefer)
        if not ok:
            self.log_message.emit(f"Merge PI range {rng[0]}–{rng[1]}: {msg}", "warn")
            QMessageBox.warning(self, "Cannot merge PI range", msg)
            return
        self.log_message.emit(msg, "ok")
        self._rebuild(regenerate=False)   # merge_pi_range already rebuilt

    def _ask_merge_choice(self, choice) -> float | None:
        """
        A range's total turn is genuinely ambiguous (some PI's own reading
        is itself near +-180 deg — see merge_range_ambiguity): let the user
        pick which interpretation they mean. Returns the chosen total in
        radians, or None if cancelled.
        """
        import math as _m
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Which turn did you mean?")
        box.setIcon(QMessageBox.Icon.Question)
        lines = ["This range's total turn can be read two ways:"]
        buttons = []
        for i, c in enumerate(choice.candidates):
            dev_txt = f"{c.max_dev:.2f} m deviation" if c.ok and c.max_dev is not None \
                else "could not be built"
            label = f"{c.total_deg:+.0f}°  ({dev_txt})"
            lines.append(f"  {i + 1}. {label}")
            btn = box.addButton(label, QMessageBox.ButtonRole.ActionRole)
            buttons.append((btn, c))
        box.setText("\n".join(lines))
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel or clicked is None:
            return None
        for btn, c in buttons:
            if btn is clicked:
                return _m.radians(c.total_deg)
        return None

    def _on_selection(self):
        rows = self._table.selectionModel().selectedRows()
        self._range_btn.setEnabled(
            self._model is not None and self._selected_pi_range() is not None)
        single_el = self._selected_single_element()
        self._trim_start_btn.setEnabled(single_el is not None)
        self._trim_end_btn.setEnabled(single_el is not None)
        ids = []
        for r in sorted(x.row() for x in rows):
            it = self._table.item(r, COL_ID)
            if it is None:
                continue
            meta = it.data(Qt.ItemDataRole.UserRole) or {}
            if meta.get("etype") not in (None, "omitted"):
                ids.append(it.text())
        self.elements_selected.emit(ids)

    def _selected_single_element(self) -> dict | None:
        """The one selected row's element dict, or None (0/2+ selected,
        or the selection is an omitted-PI row)."""
        if self._model is None:
            return None
        rows = self._table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        it = self._table.item(rows[0].row(), COL_ID)
        if it is None:
            return None
        meta = it.data(Qt.ItemDataRole.UserRole) or {}
        i = meta.get("elem_index")
        if i is None or not (0 <= i < len(self._elements)):
            return None
        return self._elements[i]

    # ------------------------------------------------------------------
    # Map click → table selection (Ctrl toggles into a multi-selection)
    # ------------------------------------------------------------------

    def select_element(self, element_id: str, ctrl: bool = False):
        """
        Select the row of `element_id` (clicked on the map). With ctrl=True
        the row is toggled into the existing selection so a PI range for
        the merge function can be picked directly on the map.
        """
        from PySide6.QtCore import QItemSelectionModel
        row = None
        for r in range(self._table.rowCount()):
            it = self._table.item(r, COL_ID)
            if it is not None and it.text() == element_id:
                row = r
                break
        if row is None:
            return
        sel_model = self._table.selectionModel()
        idx = self._table.model().index(row, 0)
        flag = (QItemSelectionModel.SelectionFlag.Toggle if ctrl
                else QItemSelectionModel.SelectionFlag.ClearAndSelect)
        sel_model.select(idx, flag | QItemSelectionModel.SelectionFlag.Rows)
        self._table.scrollTo(idx)
