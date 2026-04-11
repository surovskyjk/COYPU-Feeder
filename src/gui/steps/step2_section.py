"""
Step 2 — Select Section.
Shows the fetched tracks; user picks which ones to export.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QAbstractItemView,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont


class Step2Section(QWidget):
    section_confirmed = Signal(list)   # selected Track objects
    highlight_changed = Signal(int)    # track index (-1 = reset)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list = []
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        lbl = QLabel("Select tracks to export:")
        lbl.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
        layout.addWidget(lbl)

        hint = QLabel(
            "Select one or more tracks from the list below.\n"
            "Double-click to highlight on the map."
        )
        hint.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list, stretch=1)

        # Action row
        row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select all")
        self._select_all_btn.clicked.connect(self._select_all)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._list.clearSelection)
        row.addWidget(self._select_all_btn)
        row.addWidget(self._clear_btn)
        row.addStretch()
        layout.addLayout(row)

        # Next button
        self._next_btn = QPushButton("Next →  Configure")
        self._next_btn.setMinimumHeight(38)
        self._next_btn.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
        self._next_btn.setStyleSheet(
            "QPushButton { background:#2a82da; color:#fff; border-radius:5px; }"
            "QPushButton:hover { background:#3a92ea; }"
            "QPushButton:disabled { background:#444; color:#777; }"
        )
        self._next_btn.clicked.connect(self._on_next)
        layout.addWidget(self._next_btn)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def populate(self, tracks):
        self._tracks = tracks
        self._list.clear()
        for i, t in enumerate(tracks):
            node_count = len(t.nodes) if hasattr(t, "nodes") else 0
            item = QListWidgetItem(f"{t.name}  ({node_count} nodes)")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._list.addItem(item)
        self._select_all()

    def get_selected_tracks(self) -> list:
        selected = self._list.selectedItems()
        indices = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        return [self._tracks[i] for i in indices if i < len(self._tracks)]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _select_all(self):
        self._list.selectAll()

    def _on_row_changed(self, row: int):
        self.highlight_changed.emit(row)

    def _on_double_click(self, item: QListWidgetItem):
        idx = item.data(Qt.ItemDataRole.UserRole)
        self.highlight_changed.emit(idx)

    def _on_next(self):
        selected = self.get_selected_tracks()
        if not selected:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No tracks", "Please select at least one track.")
            return
        self.section_confirmed.emit(selected)
