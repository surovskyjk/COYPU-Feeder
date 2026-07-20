"""
Step 2 — Select Section.
Shows the fetched tracks; user picks which ones to export.

Highlight: clicking a row in the list auto-highlights it on the map.
Use the "👁 Highlight" button to highlight after a multi-selection change.
Use "📍 Fit to all tracks" to re-centre the map.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QAbstractItemView, QMessageBox,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont


class Step2Section(QWidget):
    section_confirmed       = Signal(list)   # selected Track objects
    highlight_changed       = Signal(int)    # track index  (-1 = reset all)
    fit_to_tracks_requested = Signal()
    back_requested          = Signal()       # user wants to go back to Find Railway
    tracks_changed          = Signal(list)   # self._tracks mutated (split/merge/remove)
    log_message             = Signal(str, str)   # (text, level) → log panel
    split_pick_mode         = Signal(bool)   # "Split at map point" toggled

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
            "Click a track to highlight it on the map.\n"
            "Hold Ctrl/Shift to select multiple tracks."
        )
        hint.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Single-click on a row → highlight that track on map
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, stretch=1)

        # ── Map / selection buttons — two rows to fit the 340px panel ──
        self._select_all_btn = QPushButton("Select all")
        self._select_all_btn.clicked.connect(self._list.selectAll)

        self._clear_sel_btn = QPushButton("Clear selection")
        self._clear_sel_btn.clicked.connect(self._list.clearSelection)

        self._highlight_btn = QPushButton("👁  Highlight selected")
        self._highlight_btn.setToolTip("Highlight the currently focused track on the map")
        self._highlight_btn.clicked.connect(self._on_highlight_btn)

        self._reset_hl_btn = QPushButton("🔄  Reset colours")
        self._reset_hl_btn.setToolTip("Reset all track colours to defaults")
        self._reset_hl_btn.clicked.connect(lambda: self.highlight_changed.emit(-1))

        self._fit_btn = QPushButton("📍  Fit map to tracks")
        self._fit_btn.setToolTip("Zoom and pan the map to show all loaded tracks")
        self._fit_btn.clicked.connect(self.fit_to_tracks_requested.emit)

        # Row 1: Select all | Clear selection
        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(6)
        btn_row1.addWidget(self._select_all_btn)
        btn_row1.addWidget(self._clear_sel_btn)
        layout.addLayout(btn_row1)

        # Row 2: Highlight | Reset | Fit
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(6)
        btn_row2.addWidget(self._highlight_btn)
        btn_row2.addWidget(self._reset_hl_btn)
        btn_row2.addWidget(self._fit_btn)
        layout.addLayout(btn_row2)

        # ── Track editing: split / merge / remove ───────────────────────
        edit_lbl = QLabel("Edit tracks:")
        edit_lbl.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(edit_lbl)

        self._split_track_btn = QPushButton("📍 Split at map point")
        self._split_track_btn.setCheckable(True)
        self._split_track_btn.setToolTip(
            "Select exactly ONE track, click this, then click a point on\n"
            "the map — the track splits there into two.")
        self._split_track_btn.toggled.connect(self._on_split_toggled)

        self._merge_tracks_btn = QPushButton("🔗 Merge selected")
        self._merge_tracks_btn.setToolTip(
            "Select 2+ tracks and chain them end-to-end (closest endpoints\n"
            "matched automatically, reversed if needed).")
        self._merge_tracks_btn.clicked.connect(self._on_merge_tracks)

        self._remove_track_btn = QPushButton("🗑 Remove selected")
        self._remove_track_btn.setToolTip(
            "Drop the selected track(s) from the list (the OSM data can\n"
            "always be re-fetched).")
        self._remove_track_btn.clicked.connect(self._on_remove_tracks)

        btn_row3 = QHBoxLayout()
        btn_row3.setSpacing(6)
        btn_row3.addWidget(self._split_track_btn)
        btn_row3.addWidget(self._merge_tracks_btn)
        btn_row3.addWidget(self._remove_track_btn)
        layout.addLayout(btn_row3)

        # ── Navigation row ───────────────────────────────────────────
        nav_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedWidth(80)
        self._back_btn.clicked.connect(self.back_requested.emit)
        nav_row.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →  Configure")
        self._next_btn.setMinimumHeight(38)
        self._next_btn.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
        self._next_btn.clicked.connect(self._on_next)
        nav_row.addWidget(self._next_btn)
        layout.addLayout(nav_row)

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
        self._list.selectAll()

    def get_selected_tracks(self) -> list:
        selected = self._list.selectedItems()
        indices  = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        return [self._tracks[i] for i in indices if i < len(self._tracks)]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_row_changed(self, row: int):
        """Single-click on a row → highlight that track."""
        self.highlight_changed.emit(row)

    def _on_highlight_btn(self):
        """Explicit highlight button — highlight the current (focused) item."""
        row = self._list.currentRow()
        self.highlight_changed.emit(row)

    def _on_next(self):
        selected = self.get_selected_tracks()
        if not selected:
            QMessageBox.warning(self, "No tracks",
                                "Please select at least one track.")
            return
        self.section_confirmed.emit(selected)

    def _selected_indices(self) -> list:
        items = self._list.selectedItems()
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in items)

    def _on_split_toggled(self, on: bool):
        self.split_pick_mode.emit(on)
        if on:
            idxs = self._selected_indices()
            if len(idxs) != 1:
                self.log_message.emit(
                    "Select exactly one track before picking a split point.",
                    "warn")
            else:
                self.log_message.emit(
                    f"Split-pick mode armed for '{self._tracks[idxs[0]].name}' "
                    "— click a point on the map.", "info")

    def on_map_split_point(self, lat: float, lon: float):
        """Forwarded from App while the split-pick mode is armed."""
        from osm.parser import split_track, nearest_track_node_index
        idxs = self._selected_indices()
        if len(idxs) != 1:
            self.log_message.emit(
                "Split: select exactly one track first.", "warn")
            return
        i = idxs[0]
        track = self._tracks[i]
        j = nearest_track_node_index(track, (lat, lon))
        parts = split_track(track, j)
        if parts is None:
            self.log_message.emit(
                "Split: picked point is too close to a track end.", "warn")
            return
        self._tracks = self._tracks[:i] + list(parts) + self._tracks[i + 1:]
        self.populate(self._tracks)
        self._list.setCurrentRow(i)
        self.log_message.emit(
            f"Track split into '{parts[0].name}' ({len(parts[0].nodes)} nodes) "
            f"and '{parts[1].name}' ({len(parts[1].nodes)} nodes).", "ok")
        self.tracks_changed.emit(self._tracks)

    def _on_merge_tracks(self):
        from osm.parser import chain_polylines
        idxs = self._selected_indices()
        if len(idxs) < 2:
            QMessageBox.information(
                self, "Merge tracks",
                "Select two or more tracks to merge (Ctrl/Shift-click rows).")
            return
        chosen = [self._tracks[i] for i in idxs]
        merged = chain_polylines(chosen, gap_tol_m=50.0)
        if merged is None:
            QMessageBox.warning(
                self, "Cannot merge",
                "These tracks' endpoints are more than 50 m apart — they "
                "don't look like one continuous line.")
            self.log_message.emit(
                "Merge tracks: endpoints too far apart (limit 50 m).", "warn")
            return
        idxset = set(idxs)
        insert_at = idxs[0]
        before = [t for k, t in enumerate(self._tracks)
                 if k not in idxset and k < insert_at]
        after = [t for k, t in enumerate(self._tracks)
                if k not in idxset and k >= insert_at]
        self._tracks = before + [merged] + after
        self.populate(self._tracks)
        self._list.setCurrentRow(len(before))
        self.log_message.emit(
            f"Merged {len(idxs)} tracks into '{merged.name}' "
            f"({len(merged.nodes)} nodes).", "ok")
        self.tracks_changed.emit(self._tracks)

    def _on_remove_tracks(self):
        idxs = self._selected_indices()
        if not idxs:
            QMessageBox.information(self, "Remove tracks",
                                    "Select at least one track first.")
            return
        names = ", ".join(self._tracks[i].name for i in idxs)
        reply = QMessageBox.question(
            self, "Remove tracks", f"Remove {len(idxs)} track(s)?\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        idxset = set(idxs)
        self._tracks = [t for k, t in enumerate(self._tracks) if k not in idxset]
        self.populate(self._tracks)
        self.log_message.emit(f"Removed {len(idxs)} track(s).", "ok")
        self.tracks_changed.emit(self._tracks)
