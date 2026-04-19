"""
Step 1 — Find Railway.

Three tabs:
  • Search           — ref / name / number-in-name / direct relation ID
  • Lines in View    — search for all railway lines visible in the current map view
                       (available only when the view is ≤ ~20 km wide/tall)
  • Czech Railways   — browse / filter all members of OSM relation 2332889
                       ("Railways in Czech Republic"), loaded once on demand
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QButtonGroup, QListWidget, QListWidgetItem, QTabWidget,
    QFrame, QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from gui.worker import SearchWorker, FetchWorker, CzukWorker

# OSM relation ID for the Czech Railways collection
CZ_RAILWAYS_RELATION = 2332889


def _friendly_error(raw: str) -> str:
    """Convert a raw exception string to a human-readable message."""
    low = raw.lower()
    if "429" in raw or "too many requests" in low:
        return (
            "Overpass API rate limit reached (HTTP 429).\n"
            "Please wait a few minutes and try again."
        )
    if "timed out" in low or "timeout" in low or "read timed out" in low:
        return (
            "All Overpass servers timed out.\n"
            "Check your internet connection, or try again later — "
            "the public mirrors may be temporarily busy."
        )
    if "504" in raw or "gateway" in low:
        return (
            "Overpass gateway error (HTTP 504).\n"
            "The server is overloaded. Try again in a minute."
        )
    if "connection" in low or "network" in low or "name or service not known" in low:
        return (
            "Could not connect to the Overpass API.\n"
            "Please check your internet connection."
        )
    if "404" in raw or "not found" in low:
        return "The requested OSM relation was not found. Please check the relation ID."
    # Generic fallback: show raw but not a Python traceback dump
    first_line = raw.split("\n")[0]
    return first_line if first_line else raw


class Step1Find(QWidget):
    railway_fetched          = Signal(object, dict)   # (overpass_data_or_tracks, info)
    search_in_view_requested = Signal()               # → App requests map bounds
    czuk_fetch_requested     = Signal()               # → App requests map bounds for ČÚZK

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list = []
        # Cache for the Czech Railways member list (fetched once)
        self._cz_all_results: list[dict] = []
        self._cz_loaded = False
        self._czuk_tracks: list = []   # fetched Track objects from ČÚZK WFS
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_search_tab(),  "Search")
        self._tabs.addTab(self._build_view_tab(),    "In View")
        self._tabs.addTab(self._build_cz_tab(),      "Czech Railways")
        self._tabs.addTab(self._build_czuk_tab(),    "ČÚZK (CZ)")
        layout.addWidget(self._tabs)

        # Shared fetch-status bar (visible only while a FetchWorker is running)
        self._fetch_status_lbl = QLabel("")
        self._fetch_status_lbl.setStyleSheet(
            "color:#aaa; font-size:10px; padding:2px 4px;"
        )
        self._fetch_status_lbl.setWordWrap(True)
        self._fetch_status_lbl.setVisible(False)
        layout.addWidget(self._fetch_status_lbl)

    # ── Search tab ────────────────────────────────────────────────────

    def _build_search_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        mode_lbl = QLabel("Search by:")
        mode_lbl.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(mode_lbl)

        self._mode_group = QButtonGroup(self)
        self._radio_ref  = QRadioButton("Timetable line number  (ref tag)")
        self._radio_name = QRadioButton("Name")
        self._radio_num  = QRadioButton("Number in relation name")
        self._radio_ref.setChecked(True)
        for i, r in enumerate([self._radio_ref, self._radio_name, self._radio_num]):
            self._mode_group.addButton(r, i)
            v.addWidget(r)

        self._num_hint = QLabel(
            "Searches the number inside the name field.\n"
            "e.g. '212' → '212 – Čerčany – Světlá nad Sázavou'"
        )
        self._num_hint.setStyleSheet("color:#888; font-size:10px;")
        self._num_hint.setVisible(False)
        v.addWidget(self._num_hint)
        self._radio_num.toggled.connect(self._num_hint.setVisible)

        # Query row
        row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Enter search term…")
        self._search_edit.returnPressed.connect(self._do_search)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._do_search)
        row.addWidget(self._search_edit)
        row.addWidget(self._search_btn)
        v.addLayout(row)

        # Results
        self._results_list = QListWidget()
        self._results_list.setAlternatingRowColors(True)
        self._results_list.itemDoubleClicked.connect(self._on_result_double_clicked)
        v.addWidget(self._results_list, stretch=1)

        fetch_row = QHBoxLayout()
        self._fetch_btn = QPushButton("Fetch selected")
        self._fetch_btn.clicked.connect(self._fetch_selected)
        fetch_row.addStretch()
        fetch_row.addWidget(self._fetch_btn)
        v.addLayout(fetch_row)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#555;")
        v.addWidget(sep)

        # Direct relation ID
        rel_lbl = QLabel("By relation ID:")
        rel_lbl.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(rel_lbl)

        rel_row = QHBoxLayout()
        self._rel_edit = QLineEdit()
        self._rel_edit.setPlaceholderText("e.g. 3128446")
        self._rel_edit.returnPressed.connect(self._fetch_by_relation)
        self._rel_fetch_btn = QPushButton("Fetch")
        self._rel_fetch_btn.clicked.connect(self._fetch_by_relation)
        rel_row.addWidget(self._rel_edit)
        rel_row.addWidget(self._rel_fetch_btn)
        v.addLayout(rel_row)

        return w

    # ── Lines in View tab ─────────────────────────────────────────────

    def _build_view_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        v.addWidget(QLabel("Find all railway relations in the current map view."))

        hint = QLabel(
            "Zoom into an area of roughly 20 × 20 km or smaller, then click\n"
            "the button below. Searching over a large area is blocked to avoid\n"
            "slow or rate-limited Overpass requests."
        )
        hint.setStyleSheet("color:#888; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._view_search_btn = QPushButton("🔍  Search Railway Lines in Current View")
        self._view_search_btn.setMinimumHeight(36)
        self._view_search_btn.setStyleSheet(
            "QPushButton { background:#d35400; color:#fff; border-radius:4px; padding:5px; }"
            "QPushButton:hover { background:#e67e22; }"
            "QPushButton:disabled { background:#555; color:#888; }"
        )
        self._view_search_btn.clicked.connect(self.search_in_view_requested.emit)
        v.addWidget(self._view_search_btn)

        self._view_status = QLabel("")
        self._view_status.setStyleSheet("color:#aaa; font-size:10px;")
        self._view_status.setWordWrap(True)
        v.addWidget(self._view_status)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#555;")
        v.addWidget(sep)

        v.addWidget(QLabel("Results:"))

        self._view_results_list = QListWidget()
        self._view_results_list.setAlternatingRowColors(True)
        self._view_results_list.itemDoubleClicked.connect(self._on_view_result_double_clicked)
        v.addWidget(self._view_results_list, stretch=1)

        view_fetch_row = QHBoxLayout()
        self._view_fetch_btn = QPushButton("Fetch selected")
        self._view_fetch_btn.clicked.connect(self._fetch_view_selected)
        view_fetch_row.addStretch()
        view_fetch_row.addWidget(self._view_fetch_btn)
        v.addLayout(view_fetch_row)

        return w

    # ── Czech Railways tab ────────────────────────────────────────────

    def _build_cz_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        hdr = QLabel("Railways in Czech Republic")
        hdr.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(hdr)

        hint = QLabel(
            "Lists all member relations of OSM relation 2332889\n"
            "(Railways in Czech Republic). Loaded once on demand.\n"
            "Use the filter box to narrow the list instantly."
        )
        hint.setStyleSheet("color:#888; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # Load button (shown only before first load)
        self._cz_load_btn = QPushButton("📥  Load Czech Railway Lines")
        self._cz_load_btn.setMinimumHeight(34)
        self._cz_load_btn.clicked.connect(self._load_cz_railways)
        v.addWidget(self._cz_load_btn)

        self._cz_status = QLabel("")
        self._cz_status.setStyleSheet("color:#aaa; font-size:10px;")
        self._cz_status.setWordWrap(True)
        v.addWidget(self._cz_status)

        # Filter box (always visible, useful after load)
        filter_row = QHBoxLayout()
        filter_lbl = QLabel("Filter:")
        self._cz_filter = QLineEdit()
        self._cz_filter.setPlaceholderText("Type to filter by name, ref, from/to…")
        self._cz_filter.textChanged.connect(self._apply_cz_filter)
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self._cz_filter)
        v.addLayout(filter_row)

        self._cz_count_lbl = QLabel("")
        self._cz_count_lbl.setStyleSheet("color:#888; font-size:10px;")
        v.addWidget(self._cz_count_lbl)

        self._cz_list = QListWidget()
        self._cz_list.setAlternatingRowColors(True)
        self._cz_list.itemDoubleClicked.connect(self._on_cz_result_double_clicked)
        v.addWidget(self._cz_list, stretch=1)

        cz_fetch_row = QHBoxLayout()
        self._cz_fetch_btn = QPushButton("Fetch selected")
        self._cz_fetch_btn.clicked.connect(self._fetch_cz_selected)
        cz_fetch_row.addStretch()
        cz_fetch_row.addWidget(self._cz_fetch_btn)
        v.addLayout(cz_fetch_row)

        return w

    # ── ČÚZK INSPIRE WFS tab ─────────────────────────────────────────

    def _build_czuk_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        hdr = QLabel("ČÚZK INSPIRE — Czech Railway Tracks")
        hdr.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(hdr)

        hint = QLabel(
            "Downloads railway track geometry directly from the Czech national\n"
            "GIS database (ČÚZK INSPIRE WFS), independent of Overpass/OSM.\n\n"
            "Zoom the map to your area of interest (a few tens of km),\n"
            "then click the button below."
        )
        hint.setStyleSheet("color:#888; font-size:10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._czuk_fetch_btn = QPushButton(
            "🗺  Fetch from Current Map View"
        )
        self._czuk_fetch_btn.setMinimumHeight(36)
        self._czuk_fetch_btn.setStyleSheet(
            "QPushButton { background:#1565c0; color:#fff; border-radius:4px; padding:5px; }"
            "QPushButton:hover { background:#1976d2; }"
            "QPushButton:disabled { background:#555; color:#888; }"
        )
        self._czuk_fetch_btn.clicked.connect(self.czuk_fetch_requested.emit)
        v.addWidget(self._czuk_fetch_btn)

        self._czuk_status = QLabel("")
        self._czuk_status.setStyleSheet("color:#aaa; font-size:10px;")
        self._czuk_status.setWordWrap(True)
        v.addWidget(self._czuk_status)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#555;")
        v.addWidget(sep)

        v.addWidget(QLabel("Found segments:"))

        self._czuk_list = QListWidget()
        self._czuk_list.setAlternatingRowColors(True)
        self._czuk_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        v.addWidget(self._czuk_list, stretch=1)

        czuk_load_row = QHBoxLayout()
        self._czuk_load_btn = QPushButton("📥  Load selected segments")
        self._czuk_load_btn.setEnabled(False)
        self._czuk_load_btn.clicked.connect(self._load_czuk_selected)
        czuk_load_row.addStretch()
        czuk_load_row.addWidget(self._czuk_load_btn)
        v.addLayout(czuk_load_row)

        return w

    # ------------------------------------------------------------------
    # Search actions
    # ------------------------------------------------------------------

    def _do_search(self):
        term = self._search_edit.text().strip()
        if not term:
            return
        self._search_btn.setEnabled(False)
        self._search_btn.setText("Searching…")
        self._results_list.clear()

        mode = ["ref", "name", "number_in_name"][self._mode_group.checkedId()]
        worker = SearchWorker(mode, term, self)
        worker.results_ready.connect(self._on_search_results)
        worker.failed.connect(lambda e: self._on_search_failed(e))
        worker.status_update.connect(self._fetch_status_lbl.setText)
        worker.finished.connect(lambda: self._search_btn.setText("Search"))
        worker.finished.connect(lambda: self._search_btn.setEnabled(True))
        worker.finished.connect(lambda: self._fetch_status_lbl.setVisible(False))
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        self._fetch_status_lbl.setText("Connecting to Overpass API…")
        self._fetch_status_lbl.setVisible(True)
        worker.start()

    def _on_search_results(self, results: list):
        self._populate_list(self._results_list, results)

    def _on_search_failed(self, error: str):
        self._fetch_status_lbl.setVisible(False)
        QMessageBox.critical(self, "Search error", _friendly_error(error))

    def _on_result_double_clicked(self, item: QListWidgetItem):
        r = item.data(Qt.ItemDataRole.UserRole)
        if r:
            self._do_fetch(r["id"])

    def _fetch_selected(self):
        item = self._results_list.currentItem()
        if item:
            r = item.data(Qt.ItemDataRole.UserRole)
            if r:
                self._do_fetch(r["id"])

    def _fetch_by_relation(self):
        text = self._rel_edit.text().strip()
        if not text.isdigit():
            QMessageBox.warning(self, "Invalid ID",
                                "Please enter a numeric OSM relation ID.")
            return
        self._do_fetch(int(text))

    # ── Lines in View actions ─────────────────────────────────────────

    def _on_view_result_double_clicked(self, item: QListWidgetItem):
        r = item.data(Qt.ItemDataRole.UserRole)
        if r:
            self._do_fetch(r["id"])

    def _fetch_view_selected(self):
        item = self._view_results_list.currentItem()
        if item:
            r = item.data(Qt.ItemDataRole.UserRole)
            if r:
                self._do_fetch(r["id"])

    # ── Czech Railways actions ────────────────────────────────────────

    def _load_cz_railways(self):
        if self._cz_loaded:
            return
        self._cz_load_btn.setEnabled(False)
        self._cz_load_btn.setText("Loading…")
        self._cz_status.setText("Connecting to Overpass API…")

        worker = SearchWorker("relation_members", str(CZ_RAILWAYS_RELATION), self)
        worker.results_ready.connect(self._on_cz_loaded)
        worker.failed.connect(self._on_cz_load_failed)
        worker.status_update.connect(self._cz_status.setText)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_cz_loaded(self, results: list):
        self._cz_all_results = results
        self._cz_loaded = True
        self._cz_load_btn.setVisible(False)
        self._cz_status.setText(
            f"Loaded {len(results)} railway lines. Type to filter."
        )
        self._apply_cz_filter(self._cz_filter.text())

    def _on_cz_load_failed(self, error: str):
        self._cz_load_btn.setEnabled(True)
        self._cz_load_btn.setText("📥  Load Czech Railway Lines")
        self._cz_status.setText(f"Load failed: {_friendly_error(error)}")

    def _apply_cz_filter(self, text: str):
        """Filter the already-loaded list client-side (no network call)."""
        term = text.strip().lower()
        if term:
            filtered = [
                r for r in self._cz_all_results
                if term in (r.get("name") or "").lower()
                or term in (r.get("ref") or "").lower()
                or term in (r.get("from") or "").lower()
                or term in (r.get("to") or "").lower()
            ]
        else:
            filtered = self._cz_all_results

        self._cz_list.clear()
        for r in filtered:
            ref  = r.get("ref", "")
            name = r.get("name") or f"Relation {r['id']}"
            fr   = r.get("from", "")
            to   = r.get("to", "")
            if ref:
                label = f"[{ref}]  {name}"
            else:
                label = name
            if fr and to:
                label += f"  ({fr} → {to})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self._cz_list.addItem(item)

        total = len(self._cz_all_results)
        shown = len(filtered)
        if total:
            self._cz_count_lbl.setText(
                f"Showing {shown} of {total} lines"
                if term else f"{total} lines total"
            )

    def _on_cz_result_double_clicked(self, item: QListWidgetItem):
        r = item.data(Qt.ItemDataRole.UserRole)
        if r:
            self._do_fetch(r["id"])

    def _fetch_cz_selected(self):
        item = self._cz_list.currentItem()
        if item:
            r = item.data(Qt.ItemDataRole.UserRole)
            if r:
                self._do_fetch(r["id"])

    # ── ČÚZK actions ─────────────────────────────────────────────────

    def do_czuk_fetch(self, south: float, west: float,
                      north: float, east: float) -> None:
        """Called by App with the current map bounds to start a ČÚZK WFS query."""
        self._czuk_fetch_btn.setEnabled(False)
        self._czuk_fetch_btn.setText("Fetching…")
        self._czuk_status.setText("Connecting to ČÚZK INSPIRE WFS…")
        self._czuk_list.clear()
        self._czuk_load_btn.setEnabled(False)
        self._czuk_tracks = []

        worker = CzukWorker(south, west, north, east, self)
        worker.tracks_ready.connect(self._on_czuk_tracks_ready)
        worker.failed.connect(self._on_czuk_failed)
        worker.status_update.connect(self._czuk_status.setText)
        worker.finished.connect(
            lambda: self._czuk_fetch_btn.setEnabled(True)
        )
        worker.finished.connect(
            lambda: self._czuk_fetch_btn.setText("🗺  Fetch from Current Map View")
        )
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_czuk_tracks_ready(self, tracks: list) -> None:
        self._czuk_tracks = tracks
        self._czuk_list.clear()
        if not tracks:
            self._czuk_status.setText(
                "No railway links found in this area. "
                "Try zooming into a Czech railway corridor."
            )
            self._czuk_load_btn.setEnabled(False)
            return

        for i, t in enumerate(tracks):
            item = QListWidgetItem(t.name)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._czuk_list.addItem(item)

        self._czuk_list.selectAll()
        total_km = sum(
            self._approx_track_km(t) for t in tracks
        )
        self._czuk_status.setText(
            f"Found {len(tracks)} segment{'s' if len(tracks) != 1 else ''}"
            f" ({total_km:.1f} km total). Select segments and click Load."
        )
        self._czuk_load_btn.setEnabled(True)

    def _on_czuk_failed(self, error: str) -> None:
        self._czuk_status.setText(f"ČÚZK error: {error}")
        self._czuk_load_btn.setEnabled(False)

    def _load_czuk_selected(self) -> None:
        selected_items = self._czuk_list.selectedItems()
        if not selected_items or not self._czuk_tracks:
            return
        indices = [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        selected_tracks = [self._czuk_tracks[i] for i in indices
                           if i < len(self._czuk_tracks)]
        if not selected_tracks:
            return
        info = {
            "id":       0,
            "name":     f"ČÚZK WFS ({len(selected_tracks)} segments)",
            "network":  "ČÚZK INSPIRE",
            "operator": "ČÚZK",
            "from":     "",
            "to":       "",
        }
        # Emit pre-parsed Track objects — app.py detects the list type
        self.railway_fetched.emit(selected_tracks, info)

    @staticmethod
    def _approx_track_km(track) -> float:
        """Quick km estimate from Track.nodes (lat, lon tuples)."""
        import math
        nodes = track.nodes
        total = 0.0
        for i in range(len(nodes) - 1):
            lat1, lon1 = nodes[i]
            lat2, lon2 = nodes[i + 1]
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = (math.sin(dlat / 2) ** 2
                 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
                 * math.sin(dlon / 2) ** 2)
            total += 6371.0 * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))
        return total

    # ── Shared fetch ──────────────────────────────────────────────────

    def _do_fetch(self, relation_id: int):
        self.setEnabled(False)
        self._fetch_status_lbl.setText(f"Fetching relation {relation_id}…")
        self._fetch_status_lbl.setVisible(True)
        worker = FetchWorker(relation_id, self)
        worker.data_ready.connect(self._on_data_ready)
        worker.failed.connect(lambda e: self._on_fetch_failed(relation_id, e))
        worker.status_update.connect(self._fetch_status_lbl.setText)
        worker.finished.connect(lambda: self.setEnabled(True))
        worker.finished.connect(lambda: self._fetch_status_lbl.setVisible(False))
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_fetch_failed(self, relation_id: int, error: str):
        self._fetch_status_lbl.setVisible(False)
        QMessageBox.critical(
            self, "Fetch error",
            f"Could not load relation {relation_id}.\n\n{_friendly_error(error)}"
        )

    def _on_data_ready(self, data, info: dict):
        self.railway_fetched.emit(data, info)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_list(self, lst: QListWidget, results: list):
        lst.clear()
        if not results:
            item = QListWidgetItem("No results found.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            lst.addItem(item)
            return
        for r in results:
            label = r.get("name") or f"Relation {r['id']}"
            sub   = ""
            if r.get("from") and r.get("to"):
                sub = f"  {r['from']} → {r['to']}"
            elif r.get("network"):
                sub = f"  {r['network']}"
            item = QListWidgetItem(label + sub)
            item.setData(Qt.ItemDataRole.UserRole, r)
            lst.addItem(item)

    def _cleanup_worker(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    # ------------------------------------------------------------------
    # Public API (called from App)
    # ------------------------------------------------------------------

    def show_view_results(self, results: list, status: str = ""):
        """Populate the 'In View' tab and switch to it."""
        self._populate_list(self._view_results_list, results)
        if status:
            self._view_status.setText(status)
        self._tabs.setCurrentIndex(1)

    def set_view_search_busy(self, busy: bool):
        self._view_search_btn.setEnabled(not busy)
        self._view_search_btn.setText(
            "Searching…" if busy
            else "🔍  Search Railway Lines in Current View"
        )

    def populate_results(self, results: list):
        """Compatibility: populate Search tab results list."""
        self._populate_list(self._results_list, results)
