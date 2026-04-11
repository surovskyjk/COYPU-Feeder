"""
Step 1 — Find Railway.
Two tabs: Search (ref / name / number-in-name / relation ID / bbox) and Suggested Lines.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QButtonGroup, QListWidget, QListWidgetItem, QTabWidget,
    QScrollArea, QFrame, QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from gui.worker import SearchWorker, FetchWorker
from data.suggested_lines import get_countries, get_lines_for_country


class Step1Find(QWidget):
    railway_fetched = Signal(object, dict)  # (overpass_data, relation_info)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bbox: tuple | None = None
        self._workers: list = []
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        tabs = QTabWidget()
        tabs.addTab(self._build_search_tab(), "Search")
        tabs.addTab(self._build_suggested_tab(), "Suggested Lines")
        layout.addWidget(tabs)

    def _build_search_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        # --- Search mode radios ---
        mode_lbl = QLabel("Search by:")
        mode_lbl.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(mode_lbl)

        self._mode_group = QButtonGroup(self)
        self._radio_ref  = QRadioButton("Timetable line number (ref tag)")
        self._radio_name = QRadioButton("Name")
        self._radio_num  = QRadioButton("Number in relation name")
        self._radio_ref.setChecked(True)
        self._mode_group.addButton(self._radio_ref,  0)
        self._mode_group.addButton(self._radio_name, 1)
        self._mode_group.addButton(self._radio_num,  2)

        for r in (self._radio_ref, self._radio_name, self._radio_num):
            v.addWidget(r)

        self._num_hint = QLabel(
            "Searches for the number inside the relation name\n"
            "e.g. '212' → '212 - Čerčany – Světlá nad Sázavou'"
        )
        self._num_hint.setStyleSheet("color:#888; font-size:10px;")
        self._num_hint.setVisible(False)
        v.addWidget(self._num_hint)
        self._radio_num.toggled.connect(
            lambda checked: self._num_hint.setVisible(checked)
        )

        # --- Query input row ---
        row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Enter search term…")
        self._search_edit.returnPressed.connect(self._do_search)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._do_search)
        row.addWidget(self._search_edit)
        row.addWidget(self._search_btn)
        v.addLayout(row)

        # --- Results list ---
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

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#555;")
        v.addWidget(sep)

        # --- Direct relation ID ---
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

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#555;")
        v.addWidget(sep2)

        # --- BBox ---
        bbox_lbl = QLabel("From bounding box:")
        bbox_lbl.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        v.addWidget(bbox_lbl)

        v.addWidget(QLabel("Draw a bbox on the map, then click below."))
        self._bbox_status = QLabel("No bbox drawn yet.")
        self._bbox_status.setStyleSheet("color:#888; font-size:10px;")
        v.addWidget(self._bbox_status)

        self._bbox_btn = QPushButton("Search railways in bbox")
        self._bbox_btn.setEnabled(False)
        self._bbox_btn.setStyleSheet(
            "QPushButton { background:#d35400; color:#fff; border-radius:4px; padding:5px; }"
            "QPushButton:hover { background:#e67e22; }"
            "QPushButton:disabled { background:#555; color:#888; }"
        )
        self._bbox_btn.clicked.connect(self._search_in_bbox)
        v.addWidget(self._bbox_btn)

        return w

    def _build_suggested_tab(self) -> QWidget:
        outer = QWidget()
        v = QVBoxLayout(outer)
        v.setContentsMargins(0, 0, 0, 0)

        hint = QLabel("Click 'Fetch' to load a line directly from OSM.")
        hint.setStyleSheet("color:#888; font-size:10px; padding:4px;")
        v.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_v = QVBoxLayout(inner)
        inner_v.setContentsMargins(4, 4, 4, 4)
        inner_v.setSpacing(4)

        for country in get_countries():
            hdr = QLabel(f"  {country}")
            hdr.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
            hdr.setStyleSheet("color:#aaa; margin-top:8px;")
            inner_v.addWidget(hdr)

            for line in get_lines_for_country(country):
                inner_v.addWidget(self._make_line_card(line))

        inner_v.addStretch()
        scroll.setWidget(inner)
        v.addWidget(scroll)
        return outer

    def _make_line_card(self, line: dict) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:#3c3c3f; border-radius:6px; }"
        )
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 6, 8, 6)
        cv.setSpacing(2)

        name_lbl = QLabel(line["name"])
        name_lbl.setWordWrap(True)
        name_lbl.setFont(QFont("Helvetica", 11))
        cv.addWidget(name_lbl)

        if line.get("note"):
            note = QLabel(line["note"])
            note.setStyleSheet("color:#888; font-size:9px;")
            note.setWordWrap(True)
            cv.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        if line.get("relation_id"):
            rid = line["relation_id"]
            fetch_btn = QPushButton(f"Fetch (rel. {rid})")
            fetch_btn.setFixedHeight(24)
            fetch_btn.setFont(QFont("Helvetica", 9))
            fetch_btn.clicked.connect(lambda checked=False, r=rid: self._do_fetch(r))
            btn_row.addWidget(fetch_btn)

        search_term = (
            line.get("search")
            or line["name"].split("—")[-1].strip().split("(")[0].strip()
        )
        srch_btn = QPushButton("Search by name")
        srch_btn.setFixedHeight(24)
        srch_btn.setFont(QFont("Helvetica", 9))
        srch_btn.setStyleSheet(
            "QPushButton { background:#555; color:#ddd; border-radius:3px; }"
            "QPushButton:hover { background:#666; }"
        )
        srch_btn.clicked.connect(lambda checked=False, s=search_term: self._prefill_search(s))
        btn_row.addWidget(srch_btn)
        btn_row.addStretch()
        cv.addLayout(btn_row)

        return card

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

        btn_id = self._mode_group.checkedId()
        mode = ["ref", "name", "number_in_name"][btn_id]

        worker = SearchWorker(mode, term, self)
        worker.results_ready.connect(self._on_results)
        worker.failed.connect(lambda e: QMessageBox.critical(self, "Search error", e))
        worker.finished.connect(lambda: self._search_btn.setText("Search"))
        worker.finished.connect(lambda: self._search_btn.setEnabled(True))
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_results(self, results: list):
        self._results_list.clear()
        if not results:
            item = QListWidgetItem("No results found.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._results_list.addItem(item)
            return
        for r in results:
            label = r.get("name") or f"Relation {r['id']}"
            sub = ""
            if r.get("from") and r.get("to"):
                sub = f"  {r['from']} → {r['to']}"
            elif r.get("network"):
                sub = f"  {r['network']}"
            item = QListWidgetItem(label + sub)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self._results_list.addItem(item)

    def _on_result_double_clicked(self, item: QListWidgetItem):
        r = item.data(Qt.ItemDataRole.UserRole)
        if r:
            self._do_fetch(r["id"])

    def _fetch_selected(self):
        item = self._results_list.currentItem()
        if not item:
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        if r:
            self._do_fetch(r["id"])

    def _fetch_by_relation(self):
        text = self._rel_edit.text().strip()
        if not text.isdigit():
            QMessageBox.warning(self, "Invalid ID", "Please enter a numeric OSM relation ID.")
            return
        self._do_fetch(int(text))

    def _search_in_bbox(self):
        if not self._bbox:
            return
        self._bbox_btn.setEnabled(False)
        self._bbox_btn.setText("Searching…")
        s, w, n, e = self._bbox

        worker = SearchWorker("bbox", (s, w, n, e), self)
        worker.results_ready.connect(self._on_results)
        worker.failed.connect(lambda err: QMessageBox.critical(self, "Bbox search failed", err))
        worker.finished.connect(lambda: self._bbox_btn.setText("Search railways in bbox"))
        worker.finished.connect(lambda: self._bbox_btn.setEnabled(bool(self._bbox)))
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _do_fetch(self, relation_id: int):
        self.setEnabled(False)
        worker = FetchWorker(relation_id, self)
        worker.data_ready.connect(self._on_data_ready)
        worker.failed.connect(lambda e: QMessageBox.critical(self, "Fetch error", e))
        worker.finished.connect(lambda: self.setEnabled(True))
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_data_ready(self, data, info: dict):
        self.railway_fetched.emit(data, info)

    def _prefill_search(self, term: str):
        self._search_edit.setText(term)
        self._radio_name.setChecked(True)
        self._do_search()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_bbox(self, bbox: tuple):
        self._bbox = bbox
        s, w, n, e = bbox
        self._bbox_status.setText(
            f"S {s:.4f}  W {w:.4f}  N {n:.4f}  E {e:.4f}"
        )
        self._bbox_status.setStyleSheet("color:#aaa; font-size:10px;")
        self._bbox_btn.setEnabled(True)
