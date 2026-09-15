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
    QProgressBar, QDoubleSpinBox,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QColor, QFont

from gui import icons
from gui.theme import current_tokens
from gui.palette import ELEMENT_COLORS
from gui import style_kit as sk

# Line / Spiral / Arc identity, shared with the map (see gui.palette).
TYPE_COLORS = {k: QColor(v) for k, v in ELEMENT_COLORS.items()}

COLS = ["ID", "Type", "Station [km]", "Length [m]", "Radius [m]",
        "A [m]", "Defl [°]", "Spiral L [m]", "Actions"]
COL_ID, COL_TYPE, COL_STA, COL_LEN, COL_R, COL_A, COL_DEFL, COL_SL, COL_ACT = range(9)

# Lines shorter than this between two spirals qualify for the merge action
MERGE_LINE_THRESHOLD = 30.0
# Lines longer than this offer "Split here" (inserts a PI at its midpoint)
MIN_SPLIT_LINE_LENGTH = 15.0


class ElementTableDock(QWidget):
    rebuilt                  = Signal(list, dict, object)  # (elements, metrics, splice|None)
    elements_selected        = Signal(list)        # selected element_ids (map highlight)
    log_message              = Signal(str, str)    # (text, level) → log panel
    show_alignment_requested = Signal()            # re-draw alignment on the map
    sync_parameters_requested = Signal()           # force a full table↔map↔metrics resync
    split_pick_mode          = Signal(bool)        # "Pick split point" toggled
    # Radius/spiral-length live preview (Part A): a cheap, map-only tick
    # while the spinbox is being dragged/typed into — App refreshes just the
    # map from (elements, splice), skipping the table/metrics/log/staleness
    # catch-up that `rebuilt` triggers (mirrors the PI-drag preview signals).
    preview_changed           = Signal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens         = current_tokens()
        self._model          = None     # PIAlignment or None (read-only)
        self._elements: list = []
        self._check_interval = 5.0
        self._populating     = False
        self._pending: dict  = {}       # pi_index -> {field: value}
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        # 800 ms (was 300): keyboard-tracking is off on the value spinboxes
        # (Part 2A) so mid-typing digits never reach here at all — this
        # interval only matters for the gap between the LAST real edit and
        # the full table/metrics/log catch-up, and 800 ms comfortably
        # outlasts a spin-button/wheel-step burst.
        self._debounce.setInterval(800)
        self._debounce.timeout.connect(self._apply_pending)
        # Live preview (Part A): mirrors the PI-drag start/tick/end pattern.
        # A "gesture" is one continuous stretch of ticks on the SAME
        # (pi_index, field) — history is pushed once, at its first tick, not
        # on every tick and not (again) when the debounce above commits.
        self._live_preview_enabled = True   # synced from App._prefs
        self._preview_gesture: tuple[int, str] | None = None
        self._preview_pending: tuple[int, str, float] | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(0)
        self._preview_timer.timeout.connect(self._apply_preview_tick)
        # Set by App: on_before_edit(label) is called just BEFORE every
        # model mutation below, so the toolbar's Back/Forward undo/redo
        # (Part B) has a snapshot of the pre-edit state; on_edit_failed()
        # discards that snapshot when the attempted edit turned out to be a
        # no-op (the mutator reported failure). Both None outside an
        # editing step.
        self.on_before_edit = None
        self.on_edit_failed = None
        self._build()

    def _push_history(self, label: str):
        if self.on_before_edit is not None and self._model is not None:
            self.on_before_edit(label)

    def _discard_history(self):
        if self.on_edit_failed is not None:
            self.on_edit_failed()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(4)

        header = QHBoxLayout()
        self._title = QLabel("Alignment Elements")
        sk.bold_font(self._title, 1)
        # Req 7: the hint text used to be its own wide QLabel in this row —
        # at 1080p it, plus the six buttons beside it, routinely overflowed
        # the dock's width with nowhere for the user to reclaim the space.
        # The row is now horizontally scrollable (below) regardless, but
        # dropping the hint's own width demand still leaves far more of it
        # usable before a scroll is ever needed.
        self._title.setToolTip(
            "Edit Radius (arc rows) or Spiral L (spiral rows) — the map "
            "updates automatically.\nOmit PI removes a curve; neighbours "
            "absorb its deflection."
        )
        header.addWidget(self._title)

        self._metrics_lbl = QLabel("")
        header.addWidget(self._metrics_lbl)
        header.addStretch()

        self._show_btn = QPushButton("Show alignment")
        self._show_btn.setIcon(icons.makeIcon("showAlignment"))
        self._show_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        self._show_btn.setToolTip(
            "Re-draw the edited alignment and the PI overlay on the map\n"
            "(useful after the map was reloaded and the overlays vanished)."
        )
        self._show_btn.clicked.connect(self.show_alignment_requested.emit)
        header.addWidget(self._show_btn)

        self._sync_btn = QPushButton("⟳ Sync")
        self._sync_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        self._sync_btn.setToolTip(
            "Force a full resync between the table, the map, and the\n"
            "metrics readouts — re-highlights the selected element and\n"
            "refreshes every number, in case an edit left them momentarily\n"
            "out of step."
        )
        self._sync_btn.clicked.connect(self.sync_parameters_requested.emit)
        header.addWidget(self._sync_btn)

        self._range_btn = QPushButton("Merge PI range → single curve")
        self._range_btn.setIcon(icons.makeIcon("merge"))
        self._range_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        self._range_btn.setToolTip(
            "Select the rows spanning two or more curves (e.g. the first and\n"
            "last tangent of the section, or Ctrl+click them on the map).\n"
            "All PIs in between are replaced by ONE Spiral–Arc–Spiral: the\n"
            "outer tangents are kept, the new PI is their intersection."
        )
        self._range_btn.setEnabled(False)
        self._range_btn.clicked.connect(self._on_merge_range)
        header.addWidget(self._range_btn)

        # Merges are near-instant (span-local rebuild), so a full progress
        # worker isn't warranted — this indeterminate bar just keeps a rare
        # slow case (a very long alignment) from reading as a frozen UI.
        self._merge_busy_bar = QProgressBar()
        self._merge_busy_bar.setRange(0, 0)
        self._merge_busy_bar.setTextVisible(False)
        self._merge_busy_bar.setFixedSize(60, 12)
        self._merge_busy_bar.setVisible(False)
        header.addWidget(self._merge_busy_bar)

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

        self._pick_split_btn = QPushButton("Pick split point")
        self._pick_split_btn.setIcon(icons.makeIcon("stops"))
        self._pick_split_btn.setCheckable(True)
        self._pick_split_btn.setStyleSheet("font-size: 10px; padding: 3px 10px;")
        # Green when armed, red-tinted when off (theme_manager styles this).
        self._pick_split_btn.setProperty("seriesToggle", True)
        self._pick_split_btn.setToolTip(
            "Click, then click a point on the map to insert a PI there\n"
            "(splits whichever Line currently covers that point).\n"
            "Stays armed for multiple picks — click again to stop.")
        self._pick_split_btn.toggled.connect(self.split_pick_mode.emit)
        header.addWidget(self._pick_split_btn)

        # Req 7: title + metrics + five buttons in one non-wrapping row
        # routinely overflowed the dock's width at 1080p — same fix as the
        # main toolbar (Part C): a real horizontal scrollbar with `<`/`>`
        # buttons that only appear once there's something to scroll to.
        from gui.scroll_row import mount_horizontal_scroll
        header_widget = QWidget()
        header_widget.setLayout(header)
        v.addWidget(mount_horizontal_scroll(header_widget, self))

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hdr = self._table.horizontalHeader()
        # Req 7 (1080p clipping): ResizeToContents also ignores setCellWidget
        # widgets entirely when sizing a column (the radius/spiral spinboxes
        # never get a say), and it can never shrink below its own computed
        # minimum — on a narrow window that starved the map/step panel of
        # space with nowhere for the user to reclaim it. Interactive with
        # sane defaults + a real horizontal scrollbar lets the table itself
        # absorb the squeeze instead.
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in (
            (COL_ID, 55), (COL_TYPE, 95), (COL_STA, 80), (COL_LEN, 70),
            (COL_R, 85), (COL_A, 55), (COL_DEFL, 60), (COL_SL, 85),
        ):
            self._table.setColumnWidth(col, width)
        hdr.setSectionResizeMode(COL_ACT, QHeaderView.ResizeMode.Stretch)
        self._table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Taller, easier-to-hit rows; slightly larger font
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.setStyleSheet(
            "QTableWidget { font-size: 11px; }"
            "QTableWidget::item { padding: 4px 6px; }"
        )
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
        self._preview_gesture = None
        self._preview_pending = None
        editable = pi_model is not None
        self._title.setToolTip(
            "Edit Radius (arc rows) or Spiral L (spiral rows) — the map "
            "updates automatically.\nOmit PI removes a curve; neighbours "
            "absorb its deflection." if editable else
            "Read-only — this level has no editable PI model.")
        self._populate()
        self._refresh_metrics()

    def clear(self):
        self._model    = None
        self._elements = []
        self._pending.clear()
        self._preview_gesture = None
        self._preview_pending = None
        self._table.setRowCount(0)
        self._metrics_lbl.setText("")

    def current_elements(self) -> list:
        return self._elements

    def set_live_preview(self, on: bool):
        """Radius/spiral-length edits get a cheap live map preview while
        dragging/typing when on; off reproduces the exact pre-Part-A
        behavior (only the debounced full commit). Mirrors
        MapWidget.set_live_drag_preview — same `live_drag_preview` pref."""
        self._live_preview_enabled = bool(on)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        """Restyle in place after a theme switch (COYPU's dock convention)."""
        self._tokens = tokens or current_tokens()
        t = self._tokens
        sk.bold_font(self._title, 1)
        self._metrics_lbl.setStyleSheet(
            f"color:{t['disabledText']}; font-size:10px;")
        # Cached QIcons still hold pixmaps painted in the old colour, so the
        # header buttons need their icons re-fetched from the refreshed factory.
        self._show_btn.setIcon(icons.makeIcon("showAlignment"))
        self._range_btn.setIcon(icons.makeIcon("merge"))
        self._pick_split_btn.setIcon(icons.makeIcon("stops"))
        # Row items bake their colours in at populate time.
        if self._elements:
            self._populate()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _mk_item(self, text: str, color: QColor | None = None,
                 align_right: bool = True):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
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
                pi     = el.get("_pi")
                sta    = float(el.get("sta_start", 0.0))
                length = float(el.get("length", 0.0))
                self._table.item(i, COL_STA).setText(f"{sta/1000.0:.3f}")
                self._table.item(i, COL_LEN).setText(f"{length:.2f}")
                if et == "Arc":
                    self._refresh_value_cell(i, COL_R, pi, float(el.get("radius", 0.0)))
                    defl = el.get("_deflection")
                    self._table.item(i, COL_DEFL).setText(
                        f"{math.degrees(defl):+.2f}" if defl is not None else "—")
                elif et == "Spiral":
                    r_fin = self._spiral_R(el)
                    self._table.item(i, COL_R).setText(
                        f"{r_fin:.1f}" if math.isfinite(r_fin) else "∞")
                    self._table.item(i, COL_A).setText(
                        f"{float(el.get('clothoid_A', 0.0)):.1f}")
                    self._refresh_value_cell(i, COL_SL, pi, length)
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
            grey = QColor(self._tokens["disabledText"])
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
                f"color: {self._tokens['error']}; }}")
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

        pi = el.get("_pi")
        if et == "Arc":
            r = float(el.get("radius", 0.0))
            if editable and pi is not None:
                self._table.setCellWidget(
                    row, COL_R, self._mk_value_spin(
                        pi, "radius", r, minimum=1.0, maximum=100000.0))
            else:
                self._table.setItem(row, COL_R, self._mk_item(f"{r:.1f}"))
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
            if editable and pi is not None:
                self._table.setCellWidget(
                    row, COL_SL, self._mk_value_spin(
                        pi, "spiral_len", length, minimum=0.0, maximum=1000.0))
            else:
                self._table.setItem(row, COL_SL, self._mk_item(f"{length:.1f}"))
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
        pi = el.get("_pi")
        if et == "Arc":
            self._refresh_value_cell(row, COL_R, pi, float(el.get("radius", 0.0)))
            defl = el.get("_deflection")
            self._table.item(row, COL_DEFL).setText(
                f"{math.degrees(defl):+.2f}" if defl is not None else "—")
        elif et == "Spiral":
            r_fin = self._spiral_R(el)
            self._table.item(row, COL_R).setText(
                f"{r_fin:.1f}" if math.isfinite(r_fin) else "∞")
            self._table.item(row, COL_A).setText(
                f"{float(el.get('clothoid_A', 0.0)):.1f}")
            self._refresh_value_cell(row, COL_SL, pi, length)
        it.setData(Qt.ItemDataRole.UserRole,
                   {"pi": pi, "etype": et, "elem_index": i,
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
            # Part 2B: the map side just redrew the edited span with fresh,
            # unselected layers (patch_alignment_segmented/
            # update_alignment_segmented reset _last_highlight_ids), so the
            # selected row's halo would silently vanish on every radius/
            # spiral commit unless the selection is re-pushed — same reason
            # _update_diff below re-emits deliberately.
            self._on_selection()
            return
        if self._table.rowCount() and self._update_diff():
            self._on_selection()   # rows were removed — re-emit the (possibly
            return                 # shrunken) selection once, deliberately

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
            has_entry = (elem_index > 0
                        and elements[elem_index - 1].get("type") == "Spiral"
                        and elements[elem_index - 1].get("_pi") == pi)
            has_exit = (elem_index < len(elements) - 1
                       and elements[elem_index + 1].get("type") == "Spiral"
                       and elements[elem_index + 1].get("_pi") == pi)
            has_spirals = has_entry or has_exit
            # Level 2 (model.use_spirals False) never offers "+ Spirals" —
            # add_spirals() would just refuse.
            can_add_spirals = (model is not None and model.use_spirals
                              and not has_spirals)
            return ("arc", merged, has_spirals, can_add_spirals)
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
                f"color: {self._tokens['error']}; }}")
            delete.setToolTip(
                "Physically remove this PI — unlike Omit, the tangent\n"
                "polygon loses the vertex and its neighbours connect directly.")
            buttons.append(delete)

            has_spirals, can_add_spirals = sig[2], sig[3]
            spiral_btn = None
            if has_spirals:
                spiral_btn = QPushButton("− Spirals")
                spiral_btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
                spiral_btn.setToolTip(
                    "Remove this curve's transition spirals (L-S-C-S-L →\n"
                    "L-C-L) — the arc extends to meet the tangents directly.")
                buttons.append(spiral_btn)
            elif can_add_spirals:
                spiral_btn = QPushButton("+ Spirals")
                spiral_btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
                spiral_btn.setToolTip(
                    "Add symmetric transition spirals to this curve\n"
                    "(L-C-L → L-S-C-S-L), using the project's default length.")
                buttons.append(spiral_btn)

            undo = None
            if sig[1]:
                undo = self._mk_merge_button("Undo merge", "undo")
                buttons.append(undo)

            wrap = self._wrap_buttons(buttons)
            wrap._meta = meta
            omit.clicked.connect(lambda _=False, w=wrap: self._omit_pi(w._meta["pi"]))
            reset.clicked.connect(lambda _=False, w=wrap: self._reset_pi(w._meta["pi"]))
            delete.clicked.connect(lambda _=False, w=wrap: self._delete_pi(w._meta["pi"]))
            if spiral_btn is not None:
                if has_spirals:
                    spiral_btn.clicked.connect(
                        lambda _=False, w=wrap: self._remove_spirals(w._meta["pi"]))
                else:
                    spiral_btn.clicked.connect(
                        lambda _=False, w=wrap: self._add_spirals(w._meta["pi"]))
            if undo is not None:
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
                merge = self._mk_merge_button("Merge spirals ↔")
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
    def _mk_merge_button(text: str, icon_name: str = "merge") -> QPushButton:
        """Merge/undo-merge action: flat like COYPU, recognised by its icon."""
        b = QPushButton(text)
        b.setIcon(icons.makeIcon(icon_name))
        b.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        return b

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _mk_value_spin(self, pi_index: int, field: str, value: float,
                       minimum: float, maximum: float,
                       step: float = 5.0, decimals: int = 1) -> QDoubleSpinBox:
        """Embedded radius/spiral-length editor (replaces the old free-text
        cell) — `valueChanged` fires on every tick (arrow click, wheel step,
        or — since keyboard tracking is off — Enter/focus-out, never on a
        mid-typing digit), driving the live preview below; the debounced
        `_pending`/`_apply_pending` commit path is unchanged.

        `setKeyboardTracking(False)` is the actual fix for "typing 2 on the
        way to 200 destroys the spiral": Qt emits `valueChanged` for every
        typed digit by default (9, 99, 999 style), each one a real (if
        transient) geometry rebuild; with it off, typed digits only commit
        once — on Return or focus-out (`editingFinished`, wired below) —
        while arrow-click/wheel stepping (which calls `stepBy()`, not the
        keyboard-typing path) still emits immediately, so that live-preview
        feedback is untouched."""
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setStyleSheet("font-size: 11px;")
        # ResizeToContents (this table's header mode) never consults a
        # setCellWidget widget's size hint — without an explicit floor the
        # column can size down to the header text alone and squeeze the
        # step arrows (Req 8) out of the cell entirely.
        spin.setMinimumWidth(70)
        spin.setKeyboardTracking(False)
        spin.setValue(value)   # before connecting — no spurious first tick
        spin.setProperty("pi_index", pi_index)
        spin.setProperty("field", field)
        spin.valueChanged.connect(self._on_value_spin_changed)
        spin.editingFinished.connect(self._on_value_spin_editing_finished)
        return spin

    def _refresh_value_cell(self, row: int, col: int, pi_index,
                            value: float, decimals: int = 1):
        """Route a radius/spiral-length refresh during a structural-reuse
        table update (_refresh_row/_update_in_place, both called only
        inside a `_populating=True` block, so this never reads as a user
        tick) to whichever widget kind the cell currently holds — a
        QDoubleSpinBox (editable model) or a plain read-only item."""
        w = self._table.cellWidget(row, col)
        if isinstance(w, QDoubleSpinBox):
            w.setProperty("pi_index", pi_index)
            w.setValue(value)
        else:
            it = self._table.item(row, col)
            if it is not None:
                it.setText(f"{value:.{decimals}f}")

    def _on_value_spin_changed(self, value: float):
        if self._populating or self._model is None:
            return
        spin = self.sender()
        pi_index = spin.property("pi_index") if spin is not None else None
        field = spin.property("field") if spin is not None else None
        if pi_index is None or field is None:
            return
        self._on_radius_spiral_tick(int(pi_index), str(field), float(value))

    def _on_value_spin_editing_finished(self):
        """Enter / focus-out on a radius or spiral-length cell — flush the
        pending commit immediately instead of waiting out the debounce
        timer (Part 2A: the debounce only exists to coalesce a burst of
        spin-button/wheel ticks; once the user is clearly done, there's no
        reason to make them wait)."""
        if self._populating or self._model is None or not self._pending:
            return
        self._debounce.stop()
        self._apply_pending()

    def _on_radius_spiral_tick(self, pi_index: int, field: str, value: float):
        # Always arm the debounced full commit (table, metrics, log,
        # staleness) — exactly the pre-Part-A behavior, live preview or not.
        self._pending.setdefault(pi_index, {})[field] = value
        self._debounce.start()
        if not self._live_preview_enabled:
            return
        # Gesture-start: push ONE history entry for the whole preview
        # gesture (mirrors App._on_pi_drag_start) — later ticks on the SAME
        # (pi, field) don't push again, and _apply_pending below skips its
        # own push once this has already happened.
        if self._preview_gesture != (pi_index, field):
            self._push_history("radius/spiral change")
            self._preview_gesture = (pi_index, field)
        self._preview_pending = (pi_index, field, value)
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _apply_preview_tick(self):
        """Fires the latest coalesced live-preview tick: move the model and
        emit `preview_changed` for a map-only refresh — App's handler
        deliberately skips the element table, metrics chip and staleness,
        which the debounced `_apply_pending` catches up on once the user
        stops (mirrors App._apply_pending_drag)."""
        if self._preview_pending is None or self._model is None:
            return
        pi_index, field, value = self._preview_pending
        self._preview_pending = None
        pid = next((p for p in self._model.pis if p.index == pi_index), None)
        if pid is None:
            return
        from geometry.candidates import rebuild_pi_span
        setattr(pid, field, value)
        rebuild_pi_span(self._model, pi_index, pi_index)
        self._elements = list(self._model.elements)
        self.preview_changed.emit(self._elements, self._model.last_splice)

    def _apply_pending(self):
        if self._model is None or not self._pending:
            return
        if self._preview_gesture is None:
            self._push_history("radius/spiral change")
        self._preview_gesture = None
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
        self._push_history(f"reset PI {pi_index}")
        pid.radius = -1.0
        pid.spiral_len = -1.0
        pid.merged_with_next = False
        self._rebuild(span=(pi_index, pi_index))

    def _add_spirals(self, pi_index: int):
        from geometry.candidates import add_spirals
        if self._model is None:
            return
        self._push_history(f"add spirals PI {pi_index}")
        ok, msg = add_spirals(self._model, pi_index)
        if not ok:
            self._discard_history()
            self.log_message.emit(f"Add spirals PI {pi_index}: {msg}", "warn")
            return
        self.log_message.emit(f"Add spirals PI {pi_index}: {msg}", "ok")
        self._rebuild(span=(pi_index, pi_index))

    def _remove_spirals(self, pi_index: int):
        from geometry.candidates import remove_spirals
        if self._model is None:
            return
        self._push_history(f"remove spirals PI {pi_index}")
        ok, msg = remove_spirals(self._model, pi_index)
        if not ok:
            self._discard_history()
            self.log_message.emit(f"Remove spirals PI {pi_index}: {msg}", "warn")
            return
        self.log_message.emit(f"Remove spirals PI {pi_index}: {msg}", "ok")
        self._rebuild(span=(pi_index, pi_index))

    def _set_pi(self, pi_index: int, omitted: bool):
        if self._model is None:
            return
        pid = next((p for p in self._model.pis if p.index == pi_index), None)
        if pid is None:
            return
        self._push_history(f"{'omit' if omitted else 'restore'} PI {pi_index}")
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
        self._push_history("split at map point")
        ok, msg = insert_pi(self._model, j_ref)
        if ok:
            self.log_message.emit(msg, "ok")
            self._rebuild(regenerate=False)
        else:
            self._discard_history()
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
        self._push_history("split line")
        ok, msg = insert_pi(self._model, j_ref)
        if not ok:
            self._discard_history()
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
        self._push_history(f"delete PI {pi_index}")
        ok, msg = delete_pi(self._model, pi_index)
        if not ok:
            self._discard_history()
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
            "project or track) — except via the toolbar's Back button, "
            "which still works like any other edit.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._push_history(f"trim {which}")
        ok, msg = trim_alignment(self._model, j_start, j_end)
        if not ok:
            self._discard_history()
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
        self._push_history(f"merge spirals PI {pi_a}+{pi_b}")
        ok, msg = merge_intermediate_line(self._model, pi_a, pi_b)
        if not ok:
            self._discard_history()
            self.log_message.emit(f"Merge spirals PI {pi_a}+{pi_b}: {msg}", "warn")
            QMessageBox.warning(self, "Cannot merge spirals", msg)
            return
        self.log_message.emit(f"Merge spirals PI {pi_a}+{pi_b}: {msg}", "ok")
        self._rebuild(regenerate=False)   # merge already rebuilt the geometry

    def _undo_merge(self, pi_a: int):
        from geometry.candidates import undo_merge
        if self._model is None:
            return
        self._push_history(f"undo spiral merge PI {pi_a}")
        ok, _ = undo_merge(self._model, pi_a)
        if not ok:
            self._discard_history()
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
        self.rebuilt.emit(els, metrics, getattr(self._model, "last_splice", None))

    def _refresh_metrics(self, metrics: dict | None = None):
        if metrics is None:
            self._metrics_lbl.setText(f"· {len(self._elements)} elements")
            return
        self._metrics_lbl.setText(
            f"· {len(self._elements)} elements   "
            f"mean dev {metrics.get('mean_deviation', 0.0):.2f} m   "
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
        from PySide6.QtWidgets import QMessageBox, QApplication
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

        self._push_history(f"merge PIs {rng[0]}–{rng[1]}")
        self._merge_busy_bar.setVisible(True)
        QApplication.processEvents()   # let the bar actually paint before the call
        try:
            ok, msg = merge_pi_range(self._model, rng[0], rng[1], prefer=prefer)
        finally:
            self._merge_busy_bar.setVisible(False)
        if not ok:
            self._discard_history()
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
        # Row teardown during (re)population fires selectionChanged too —
        # without this guard every repopulate leaked 2-3 spurious
        # highlightElements IPCs (each a whole-canvas repaint on the map).
        # The tail of _populate re-emits once deliberately.
        if self._populating:
            return
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
