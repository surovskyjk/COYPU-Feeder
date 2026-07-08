"""
Step 6 — Stations & Stops.

Estimates the chainage (km) of railway stations/stops along the fitted
alignment and builds the list exported as a CSV next to the LandXML file
(`Station,Dwell Time,Name`; km with 3 decimals, dwell in seconds, names
unique).

Three ways to add stations:
  • ⚡ Auto-detect — Overpass query for railway=station/halt +
    train stop positions near the alignment (passenger only);
  • 📍 Place on map — click the map, the point is snapped to the
    alignment and named via a prompt;
  • ＋ Add row — manual entry directly in the table.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QInputDialog, QMessageBox,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont, QColor


COLS = ["Station [km]", "Dwell [s]", "Name", "Source", ""]
COL_KM, COL_DWELL, COL_NAME, COL_SRC, COL_DEL = range(5)


class Step6Stations(QWidget):
    stations_changed     = Signal(list)   # list[Station] → map markers + export
    stations_done        = Signal(list)   # user clicked Next
    back_requested       = Signal()
    map_click_mode       = Signal(bool)   # arm/disarm crosshair on the map

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stations: list = []          # list[stationing.Station]
        self._elements: list = []
        self._tracks: list   = []
        self._work_epsg      = 32633
        self._worker         = None
        self._click_armed    = False
        self._populating     = False
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("Stations && Stops")
        title.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        outer.addWidget(title)

        info = QLabel(
            "Chainages are measured along the fitted alignment — identical "
            "to the LandXML stationing. The list is exported as a CSV "
            "(Station,Dwell Time,Name) next to the LandXML file."
        )
        info.setStyleSheet("color: #999; font-size: 10px;")
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

        self._csv_btn = QPushButton("💾 Export CSV…")
        self._csv_btn.setToolTip(
            "Save the station list as a CSV file now\n"
            "(it is also written automatically next to the LandXML in Step 8)."
        )
        self._csv_btn.clicked.connect(self._on_export_csv)
        btn_row.addWidget(self._csv_btn)
        outer.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet("font-size: 10px;")
        self._table.cellChanged.connect(self._on_cell_changed)
        outer.addWidget(self._table, stretch=1)

        nav_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(70)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setMinimumHeight(36)
        self._next_btn.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        self._next_btn.clicked.connect(self._on_next)
        nav_row.addWidget(self._next_btn)
        outer.addLayout(nav_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, elements: list, tracks: list, work_epsg: int):
        """Called by App after Step 5 Accept."""
        self._elements  = elements
        self._tracks    = tracks
        self._work_epsg = work_epsg
        self._click_armed = False
        self._place_btn.setChecked(False)
        # Re-snap any stations placed in a previous pass (alignment may have
        # changed in Step 5).
        if self._stations:
            from geometry.stationing import snap_stations
            snap_stations(self._stations, elements, work_epsg)
        self._populate()
        self._emit_changed()
        self._status_lbl.setText(
            f"{len(self._stations)} station(s). Auto-detect, click the map, "
            "or add rows manually.")

    def stations(self) -> list:
        return list(self._stations)

    def reset(self):
        """Clear all stations (start-over)."""
        self._stations.clear()
        self._populate()
        self._status_lbl.setText("")

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
        self._worker = StationDetectWorker(
            self._elements, self._tracks, self._work_epsg, parent=self)
        self._worker.status_update.connect(self._status_lbl.setText)
        self._worker.stations_ready.connect(self._on_auto_result)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_auto_result(self, found: list):
        self._auto_btn.setEnabled(True)
        existing = {s.name for s in self._stations}
        added = 0
        for st in found:
            if st.name not in existing:
                self._stations.append(st)
                existing.add(st.name)
                added += 1
        self._sort_and_refresh()
        self._status_lbl.setText(
            f"Auto-detect: {added} new station(s) added "
            f"({len(found) - added} already present).")

    def _on_auto_failed(self, msg: str):
        self._auto_btn.setEnabled(True)
        self._status_lbl.setText(f"⚠ Auto-detect failed: {msg.splitlines()[0]}")

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
            self._status_lbl.setText(f"✓ {n} station(s) written to {path}")
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
            self._table.setRowCount(0)
            for i, s in enumerate(self._stations):
                row = self._table.rowCount()
                self._table.insertRow(row)

                km = QTableWidgetItem(f"{s.chainage_m / 1000.0:.3f}")
                km.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, COL_KM, km)

                dw = QTableWidgetItem(str(int(s.dwell_s)))
                dw.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, COL_DWELL, dw)

                self._table.setItem(row, COL_NAME, QTableWidgetItem(s.name))

                src = QTableWidgetItem(s.source)
                src.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                src.setForeground(QColor("#888888"))
                self._table.setItem(row, COL_SRC, src)

                btn = QPushButton("✕")
                btn.setFixedWidth(28)
                btn.setStyleSheet("font-size: 9px;")
                btn.clicked.connect(lambda _=False, idx=i: self._on_delete(idx))
                self._table.setCellWidget(row, COL_DEL, btn)
        finally:
            self._populating = False

    def _on_cell_changed(self, row: int, col: int):
        if self._populating or row >= len(self._stations):
            return
        s = self._stations[row]
        text = (self._table.item(row, col).text() or "").strip()
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
            self._sort_and_refresh()
            return
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
        self._populate()
        self._emit_changed()

    def _on_delete(self, idx: int):
        if 0 <= idx < len(self._stations):
            del self._stations[idx]
            self._sort_and_refresh()

    def _sort_and_refresh(self):
        self._stations.sort(key=lambda s: s.chainage_m)
        self._populate()
        self._emit_changed()

    # ------------------------------------------------------------------
    # Signals out
    # ------------------------------------------------------------------

    def _emit_changed(self):
        self.stations_changed.emit(list(self._stations))

    def _on_next(self):
        if self._click_armed:
            self._place_btn.setChecked(False)
        self.stations_done.emit(list(self._stations))
