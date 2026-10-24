"""
Step 6 — Stations & Stops.

Estimates the chainage (km) of railway stations/stops along the fitted
alignment and builds the list exported next to the LandXML file as COYPU's
stops CSV (``stopName,stationingKm,dwellTimeS,municipalityName,population,
stopInOs,stopInSp,stopInEx`` — see geometry.stationing), which COYPU's
Stops dialog imports as-is.

Three ways to add stations:
  • ⚡ Auto-detect — Overpass query for railway=station/halt +
    train stop positions near the alignment (passenger only);
  • 📍 Place on map — click the map, the point is snapped to the
    alignment and named via a prompt;
  • ＋ Add row — manual entry directly in the table.

🏛 Fetch municipality & population is a separate lookup (Overpass is_in +
Wikidata), so a missing or unreachable source never stands in the way of
detecting the stations themselves. The population drives COYPU's Sp rule.

The Os / Sp / Ex stopping patterns follow COYPU's rules (Os everywhere, Sp at
the endpoints and in municipalities at or above the population threshold, Ex
at the endpoints only); ticking Sp or Ex by hand overrides the rule — the
cell is then marked ✎ in italics, as COYPU's Stops dialog shows it.

The station table is too wide for the 340 px step column: the step builds it
(``table_panel()``) and App shows it in the bottom row under the map.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QInputDialog, QMessageBox, QGroupBox, QSpinBox, QCheckBox,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont, QColor

from gui import style_kit as sk
from gui.style_kit import ThemedPanel
from gui.theme import current_tokens
from geometry.stationing import (
    DEFAULT_SP_THRESHOLD, apply_pattern_rules, parse_population,
    pattern_counts, reset_overrides,
)


COLS = ["km", "Dwell [s]", "Name", "Municipality", "Population",
        "Os", "Sp", "Ex", "Source", ""]
(COL_KM, COL_DWELL, COL_NAME, COL_MUNI, COL_POP,
 COL_OS, COL_SP, COL_EX, COL_SRC, COL_DEL) = range(len(COLS))
_PATTERN_COLS = (COL_OS, COL_SP, COL_EX)

_POP_SOURCE_TEXT = {
    "wikidata": "Wikidata",
    "osm":      "OSM population tag",
    "manual":   "entered by hand",
}


def _thousands(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


class Step6Stations(ThemedPanel, QWidget):
    stations_changed     = Signal(list)   # list[Station] → map markers + export
    stations_edited      = Signal()       # a user edit (→ project dirty)
    stations_done        = Signal(list)   # user clicked Next
    back_requested       = Signal()
    map_click_mode       = Signal(bool)   # arm/disarm crosshair on the map

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stations: list = []          # list[stationing.Station]
        self._elements: list = []
        self._tracks: list   = []
        self._fallback_stops: list = []    # see prepare()
        self._work_epsg      = 32633
        self._worker         = None
        self._info_worker    = None
        self._info_targets: list = []      # stations the running lookup is for
        self._click_armed    = False
        self._populating     = False
        self._sp_threshold   = DEFAULT_SP_THRESHOLD
        self._sp_auto        = True
        self._build()
        self._build_table_panel()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("Stations & Stops")
        self.bolded(title, 4)
        outer.addWidget(title)

        info = QLabel(
            "Chainages are measured along the fitted alignment — identical "
            "to the LandXML stationing. The station table is in the panel "
            "under the map; it is exported next to the LandXML as COYPU's "
            "stops CSV (COYPU ▸ Stops ▸ Import CSV)."
        )
        self.themed(info, sk.muted_label)
        info.setWordWrap(True)
        outer.addWidget(info)

        btn_row = QHBoxLayout()
        self._auto_btn = QPushButton("⚡ Auto-detect")
        self._auto_btn.setToolTip(
            "Query OpenStreetMap for passenger stations, halts and train\n"
            "stop positions within 100 m of the alignment."
        )
        self._auto_btn.clicked.connect(self._on_auto_detect)
        btn_row.addWidget(self._auto_btn)

        self._place_btn = QPushButton("📍 Place on map")
        self._place_btn.setCheckable(True)
        self._place_btn.setToolTip(
            "Arm click mode: the next click on the map places a station\n"
            "snapped to the alignment (you will be asked for its name)."
        )
        self._place_btn.toggled.connect(self._on_place_toggled)
        btn_row.addWidget(self._place_btn)

        self._add_btn = QPushButton("＋ Add row")
        self._add_btn.clicked.connect(self._on_add_row)
        btn_row.addWidget(self._add_btn)
        outer.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        self._info_btn = QPushButton("🏛 Municipality && population")
        self._info_btn.setToolTip(
            "Look up the municipality each station lies in (OpenStreetMap\n"
            "administrative boundaries) and its number of inhabitants\n"
            "(Wikidata, or the OSM population tag when Wikidata has none).\n"
            "Rows you edited by hand are left alone — clear their\n"
            "municipality and population to have them looked up again."
        )
        self._info_btn.clicked.connect(self._on_fetch_info)
        btn_row2.addWidget(self._info_btn)

        self._csv_btn = QPushButton("💾 Export CSV…")
        self._csv_btn.setToolTip(
            "Save the station list as COYPU's stops CSV now\n"
            "(it is also written automatically next to the LandXML in Step 9)."
        )
        self._csv_btn.clicked.connect(self._on_export_csv)
        btn_row2.addWidget(self._csv_btn)
        outer.addLayout(btn_row2)

        self._status_lbl = QLabel("")
        self.themed(self._status_lbl, sk.muted_label)
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        outer.addWidget(self._build_patterns_box())
        outer.addStretch(1)

        nav_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(70)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setMinimumHeight(36)
        self.bolded(self._next_btn, 2)
        self._next_btn.clicked.connect(self._on_next)
        nav_row.addWidget(self._next_btn)
        outer.addLayout(nav_row)

    def _build_patterns_box(self) -> QGroupBox:
        box = QGroupBox("Stopping patterns (COYPU)")
        box.setStyleSheet("QGroupBox { font-weight:600; }")
        g = QVBoxLayout(box)
        g.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel("Sp threshold:")
        lbl.setStyleSheet("font-size: 11px;")
        row.addWidget(lbl)
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 10_000_000)
        self._threshold_spin.setSingleStep(100)
        self._threshold_spin.setGroupSeparatorShown(True)
        self._threshold_spin.setSuffix(" inh.")
        self._threshold_spin.setKeyboardTracking(False)
        self._threshold_spin.setValue(self._sp_threshold)
        self._threshold_spin.setToolTip(
            "Municipalities with at least this many inhabitants get a stop\n"
            "in the Sp pattern (the line endpoints always do).")
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        row.addWidget(self._threshold_spin)
        self._auto_check = QCheckBox("Automatic")
        self._auto_check.setChecked(self._sp_auto)
        self._auto_check.setToolTip(
            "On: Sp follows the population rule (except manual overrides).\n"
            "Off: Sp is set by hand only.")
        self._auto_check.toggled.connect(self._on_auto_toggled)
        row.addWidget(self._auto_check)
        row.addStretch()
        g.addLayout(row)

        row2 = QHBoxLayout()
        self._reset_btn = QPushButton("Reset overrides")
        self._reset_btn.setToolTip(
            "Clear every hand-set Sp and Ex tick and apply the rules again.")
        self._reset_btn.clicked.connect(self._on_reset_overrides)
        row2.addWidget(self._reset_btn)
        self._counts_lbl = QLabel("")
        self.themed(self._counts_lbl, sk.value_label)
        row2.addWidget(self._counts_lbl)
        row2.addStretch()
        g.addLayout(row2)

        hint = QLabel(
            "Os stops everywhere. Sp stops at both ends and in municipalities "
            "at or above the threshold. Ex stops at both ends only. Tick Sp "
            "or Ex in the table to override the rule (marked ✎)."
        )
        hint.setWordWrap(True)
        self.themed(hint, sk.muted_label)
        g.addWidget(hint)
        return box

    def _build_table_panel(self):
        """The station table and its caption, shown by App in the bottom
        row (it is not part of this panel's own layout)."""
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(4)

        head = QHBoxLayout()
        cap = QLabel("Stations & stops")
        self.bolded(cap, 1)
        head.addWidget(cap)
        self._table_counts_lbl = QLabel("")
        self.themed(self._table_counts_lbl, sk.muted_label)
        head.addWidget(self._table_counts_lbl)
        head.addStretch()
        v.addLayout(head)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_MUNI, QHeaderView.ResizeMode.Stretch)
        for col, tip in ((COL_OS, "Os — local trains stop here"),
                         (COL_SP, "Sp — semi-fast trains stop here"),
                         (COL_EX, "Ex — express trains stop here")):
            self._table.horizontalHeaderItem(col).setToolTip(tip)
        self._table.setStyleSheet("font-size: 10px;")
        self._table.cellChanged.connect(self._on_cell_changed)
        v.addWidget(self._table, stretch=1)
        self._table_panel = panel

    def table_panel(self) -> QWidget:
        return self._table_panel

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        super().applyTheme(isDark, tokens)
        # The Source column is tinted from the tokens when it is filled.
        self._populate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, elements: list, tracks: list, work_epsg: int,
                fallback_stops: list | None = None):
        """Called by App after Step 5 Accept. *fallback_stops* are the
        stop-like nodes fetched with the relation (Auto-detect's stand-in
        when Overpass is down)."""
        self._elements  = elements
        self._tracks    = tracks
        self._work_epsg = work_epsg
        self._fallback_stops = list(fallback_stops or [])
        self._click_armed = False
        self._place_btn.setChecked(False)
        # Re-snap any stations placed in a previous pass (alignment may have
        # changed in Step 5).
        if self._stations:
            from geometry.stationing import snap_stations
            snap_stations(self._stations, elements, work_epsg)
        self._sort_and_refresh(edited=False)
        self._status_lbl.setText(
            f"{len(self._stations)} station(s). Auto-detect, click the map, "
            "or add rows manually.")

    def stations(self) -> list:
        return list(self._stations)

    def rule_settings(self) -> dict:
        """The Sp rule settings, persisted with the project."""
        return {"sp_threshold": int(self._sp_threshold),
                "sp_auto": bool(self._sp_auto)}

    def reset(self):
        """Clear all stations (start-over)."""
        self._stations.clear()
        self._fallback_stops = []
        self._set_rule_settings(DEFAULT_SP_THRESHOLD, True)
        self._populate()
        self._update_counts()
        self._status_lbl.setText("")
        self._status_lbl.setToolTip("")

    def set_stations(self, stations: list, rules: dict | None = None):
        """Replace the station list (e.g. when a project is opened)."""
        rules = rules or {}
        self._set_rule_settings(
            int(rules.get("sp_threshold", DEFAULT_SP_THRESHOLD)),
            bool(rules.get("sp_auto", True)))
        self._stations = list(stations or [])
        self._sort_and_refresh(edited=False)
        self._status_lbl.setText(f"{len(self._stations)} station(s) loaded.")

    def on_map_clicked(self, lat: float, lon: float):
        """Forwarded from App when click mode is armed."""
        if not self._click_armed:
            return
        from geometry.stationing import Station, snap_stations
        name, ok = QInputDialog.getText(
            self, "New station", "Station / stop name:")
        if not ok:
            return
        st = Station(name=name.strip(), latlon=(lat, lon), source="manual")
        snap_stations([st], self._elements, self._work_epsg)
        if st.dist_m > 500.0:
            QMessageBox.warning(
                self, "Too far from alignment",
                f"The clicked point is {st.dist_m:.0f} m from the alignment "
                "(limit 500 m). Station not added.")
            return
        if not self._unique_name(st.name):
            return
        self._stations.append(st)
        self._sort_and_refresh()

    # ------------------------------------------------------------------
    # Auto-detect
    # ------------------------------------------------------------------

    def _on_auto_detect(self):
        from gui.worker import StationDetectWorker
        if not self._elements or not self._tracks:
            self._status_lbl.setText("No alignment available.")
            return
        self._auto_btn.setEnabled(False)
        self._status_lbl.setText("Querying Overpass for stations…")
        self._status_lbl.setToolTip("")
        self._worker = StationDetectWorker(
            self._elements, self._tracks, self._work_epsg,
            fallback_stops=self._fallback_stops, parent=self)
        self._worker.status_update.connect(self._status_lbl.setText)
        self._worker.stations_ready.connect(self._on_auto_result)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_auto_result(self, found: list, note: str = ""):
        self._auto_btn.setEnabled(True)
        existing = {s.name: s for s in self._stations}
        added = 0
        for st in found:
            cur = existing.get(st.name)
            if cur is None:
                self._stations.append(st)
                existing[st.name] = st
                added += 1
            elif cur.osm_id is None and st.osm_id is not None:
                # A station typed in by hand gains its OSM identity.
                cur.osm_id, cur.kind = st.osm_id, st.kind
        self._sort_and_refresh()
        msg = (f"Auto-detect: {added} new station(s) added "
               f"({len(found) - added} already present).")
        if added:
            msg += (" Use 🏛 Municipality & population to fill in where "
                    "they lie — the population drives the Sp rule.")
        self._status_lbl.setText(f"⚠ {note}\n{msg}" if note else msg)

    def _on_auto_failed(self, msg: str):
        self._auto_btn.setEnabled(True)
        lines = msg.splitlines()
        self._status_lbl.setText(f"⚠ Auto-detect failed: {lines[0] if lines else msg}")
        # Per-server detail (or a traceback) stays one hover away.
        self._status_lbl.setToolTip(msg if len(lines) > 1 else "")

    # ------------------------------------------------------------------
    # Municipality & population lookup
    # ------------------------------------------------------------------

    def _on_fetch_info(self):
        from gui.worker import StationInfoWorker
        targets = [s for s in self._stations
                   if s.latlon and (s.latlon[0] or s.latlon[1])]
        if not targets:
            self._status_lbl.setText(
                "No stations to look up — auto-detect or add stations first.")
            return
        self._info_btn.setEnabled(False)
        self._info_targets = targets
        self._status_lbl.setText("Looking up municipalities…")
        self._status_lbl.setToolTip("")
        self._info_worker = StationInfoWorker(
            [s.latlon for s in targets], parent=self)
        self._info_worker.status_update.connect(self._status_lbl.setText)
        self._info_worker.info_ready.connect(self._on_info_result)
        self._info_worker.failed.connect(self._on_info_failed)
        self._info_worker.start()

    def _on_info_result(self, results: list, note: str = ""):
        self._info_btn.setEnabled(True)
        # Stations deleted while the lookup ran are skipped; rows edited by
        # hand keep what the user typed.
        alive = {id(s) for s in self._stations}
        updated = kept = missing = 0
        for s, info in zip(self._info_targets, results):
            if id(s) not in alive:
                continue
            if s.info_manual:
                kept += 1
                continue
            if info is None:
                missing += 1
                continue
            s.municipality      = info.get("municipality") or ""
            s.population        = info.get("population")
            s.population_year   = info.get("population_year")
            s.population_source = info.get("population_source") or ""
            updated += 1
        self._info_targets = []
        self._sort_and_refresh(edited=updated > 0)
        msg = f"Municipality lookup: {updated} updated"
        if kept:
            msg += f" · {kept} kept (edited by hand)"
        if missing:
            msg += f" · {missing} not found"
        self._status_lbl.setText(f"⚠ {note}\n{msg}" if note else msg)

    def _on_info_failed(self, msg: str):
        self._info_btn.setEnabled(True)
        self._info_targets = []
        lines = msg.splitlines()
        self._status_lbl.setText(
            f"⚠ Municipality lookup failed: {lines[0] if lines else msg}")
        self._status_lbl.setToolTip(msg if len(lines) > 1 else "")

    # ------------------------------------------------------------------
    # Stopping-pattern rules
    # ------------------------------------------------------------------

    def _set_rule_settings(self, threshold: int, auto: bool):
        self._sp_threshold = int(threshold)
        self._sp_auto = bool(auto)
        for w, setter, value in ((self._threshold_spin, "setValue", self._sp_threshold),
                                 (self._auto_check, "setChecked", self._sp_auto)):
            w.blockSignals(True)
            getattr(w, setter)(value)
            w.blockSignals(False)
        self._threshold_spin.setEnabled(self._sp_auto)
        self._reset_btn.setEnabled(self._sp_auto)

    def _on_threshold_changed(self, value: int):
        self._sp_threshold = int(value)
        self._sort_and_refresh()

    def _on_auto_toggled(self, on: bool):
        self._sp_auto = bool(on)
        self._threshold_spin.setEnabled(on)
        self._reset_btn.setEnabled(on)
        self._sort_and_refresh()

    def _on_reset_overrides(self):
        reset_overrides(self._stations, self._sp_threshold)
        self._sort_and_refresh()

    def _update_counts(self):
        c = pattern_counts(self._stations)
        text = f"Os {c['Os']} · Sp {c['Sp']} · Ex {c['Ex']}"
        self._counts_lbl.setText(text)
        self._table_counts_lbl.setText(
            f"{len(self._stations)} station(s) · {text}" if self._stations else "")

    # ------------------------------------------------------------------
    # Manual placement / rows
    # ------------------------------------------------------------------

    def _on_place_toggled(self, on: bool):
        self._click_armed = on
        self.map_click_mode.emit(on)
        self._status_lbl.setText(
            "Click the map to place a station…" if on else "")

    def _on_add_row(self):
        from geometry.stationing import Station, latlon_at_chainage
        name, ok = QInputDialog.getText(self, "New station", "Station / stop name:")
        if not ok or not name.strip():
            return
        if not self._unique_name(name.strip()):
            return
        km, ok = QInputDialog.getDouble(
            self, "New station", "Station chainage [km]:",
            0.0, 0.0, 10_000.0, 3)
        if not ok:
            return
        st = Station(name=name.strip(), latlon=(0.0, 0.0),
                     chainage_m=km * 1000.0, source="manual")
        ll = latlon_at_chainage(st.chainage_m, self._elements, self._work_epsg)
        if ll:
            st.latlon = ll
        self._stations.append(st)
        self._sort_and_refresh()
        self._status_lbl.setText(
            f"'{name.strip()}' added at km {km:.3f} — the km value can be "
            "edited in the table.")

    def _on_export_csv(self):
        from PySide6.QtWidgets import QFileDialog
        from geometry.stationing import write_stations_csv
        if not self._stations:
            self._status_lbl.setText("No stations to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export stations CSV", "stations.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            n = write_stations_csv(self._stations, path)
            self._status_lbl.setText(
                f"✓ {n} station(s) written to {path} (COYPU stops CSV)")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _unique_name(self, name: str) -> bool:
        if any(s.name == name for s in self._stations):
            QMessageBox.warning(self, "Duplicate name",
                                f"A station named '{name}' already exists — "
                                "names must be unique.")
            return False
        return True

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _populate(self):
        self._populating = True
        try:
            muted = QColor(current_tokens()["disabledText"])
            italic = QFont()
            italic.setItalic(True)
            self._table.setRowCount(0)
            for i, s in enumerate(self._stations):
                row = self._table.rowCount()
                self._table.insertRow(row)
                right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

                km = QTableWidgetItem(f"{s.chainage_m / 1000.0:.3f}")
                km.setTextAlignment(right)
                self._table.setItem(row, COL_KM, km)

                dw = QTableWidgetItem(str(int(s.dwell_s)))
                dw.setTextAlignment(right)
                self._table.setItem(row, COL_DWELL, dw)

                self._table.setItem(row, COL_NAME, QTableWidgetItem(s.name))
                self._table.setItem(row, COL_MUNI, QTableWidgetItem(s.municipality))

                pop = QTableWidgetItem(
                    "" if s.population is None else _thousands(s.population))
                pop.setTextAlignment(right)
                pop.setToolTip(self._population_tip(s))
                self._table.setItem(row, COL_POP, pop)

                for col, on, manual in ((COL_OS, s.stop_os, False),
                                        (COL_SP, s.stop_sp, s.sp_manual),
                                        (COL_EX, s.stop_ex, s.ex_manual)):
                    it = QTableWidgetItem("")
                    it.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                                | Qt.ItemFlag.ItemIsEnabled
                                | Qt.ItemFlag.ItemIsSelectable)
                    it.setCheckState(Qt.CheckState.Checked if on
                                     else Qt.CheckState.Unchecked)
                    if manual:
                        it.setFont(italic)
                        it.setText("✎")
                        it.setToolTip("Set by hand — 'Reset overrides' "
                                      "returns it to the rule.")
                    self._table.setItem(row, col, it)

                src_text = s.source + (f" · {s.kind}" if s.kind else "")
                src = QTableWidgetItem(src_text)
                src.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                src.setForeground(muted)
                src.setToolTip(self._source_tip(s))
                self._table.setItem(row, COL_SRC, src)

                btn = QPushButton("✕")
                btn.setFixedWidth(28)
                btn.setStyleSheet("font-size: 9px;")
                btn.clicked.connect(lambda _=False, idx=i: self._on_delete(idx))
                self._table.setCellWidget(row, COL_DEL, btn)
        finally:
            self._populating = False

    @staticmethod
    def _population_tip(s) -> str:
        if s.population is None:
            return ("Unknown — 🏛 Municipality & population looks it up; "
                    "a stop without a population is not in Sp by the rule.")
        bits = [b for b in (str(s.population_year) if s.population_year else "",
                            _POP_SOURCE_TEXT.get(s.population_source, ""))
                if b]
        return _thousands(s.population) + (f" ({', '.join(bits)})" if bits else "")

    @staticmethod
    def _source_tip(s) -> str:
        parts = ["Auto-detected" if s.source == "auto" else "Added by hand"]
        if s.kind:
            parts.append(f"OSM {s.kind}"
                         + (f" (node {s.osm_id})" if s.osm_id is not None else ""))
        parts.append(f"{s.dist_m:.0f} m from the alignment")
        return " · ".join(parts)

    def _on_cell_changed(self, row: int, col: int):
        if self._populating or row >= len(self._stations):
            return
        s = self._stations[row]
        item = self._table.item(row, col)
        if col in _PATTERN_COLS:
            on = item.checkState() == Qt.CheckState.Checked
            if col == COL_OS:
                s.stop_os = on
            elif col == COL_SP:
                s.stop_sp, s.sp_manual = on, True
            else:
                s.stop_ex, s.ex_manual = on, True
            self._sort_and_refresh()
            return
        text = (item.text() or "").strip()
        if col == COL_KM:
            try:
                s.chainage_m = max(0.0, float(text.replace(",", ".")) * 1000.0)
                # Keep the map marker on the alignment at the edited km
                from geometry.stationing import latlon_at_chainage
                ll = latlon_at_chainage(s.chainage_m, self._elements,
                                        self._work_epsg)
                if ll:
                    s.latlon = ll
            except ValueError:
                pass
        elif col == COL_DWELL:
            try:
                s.dwell_s = max(0, int(float(text.replace(",", "."))))
            except ValueError:
                pass
        elif col == COL_NAME:
            if text and text != s.name:
                if any(o.name == text for o in self._stations if o is not s):
                    QMessageBox.warning(self, "Duplicate name",
                                        f"'{text}' already exists.")
                else:
                    s.name = text
        elif col == COL_MUNI:
            s.municipality = text
            s.info_manual = bool(s.municipality or s.population is not None)
        elif col == COL_POP:
            pop = parse_population(text)
            if pop is not None or not text:
                s.population = pop
                s.population_year = None
                s.population_source = "manual" if pop is not None else ""
                s.info_manual = bool(s.municipality or s.population is not None)
        self._sort_and_refresh()

    def _on_delete(self, idx: int):
        if 0 <= idx < len(self._stations):
            del self._stations[idx]
            self._sort_and_refresh()

    def _sort_and_refresh(self, edited: bool = True):
        """Sort by chainage, re-apply the stopping-pattern rules (the
        endpoints or a population may have changed), redraw, and announce
        the change; *edited* marks it as the user's (→ project dirty)."""
        self._stations.sort(key=lambda s: s.chainage_m)
        apply_pattern_rules(self._stations, self._sp_threshold, self._sp_auto)
        self._populate()
        self._update_counts()
        self._emit_changed()
        if edited:
            self.stations_edited.emit()

    # ------------------------------------------------------------------
    # Signals out
    # ------------------------------------------------------------------

    def _emit_changed(self):
        self.stations_changed.emit(list(self._stations))

    def _on_next(self):
        if self._click_armed:
            self._place_btn.setChecked(False)
        self.stations_done.emit(list(self._stations))
