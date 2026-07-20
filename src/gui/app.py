"""
Main application window — PySide6.
3-column layout: StepSidebar | MapWidget | QStackedWidget (step panels).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QGuiApplication, QAction, QActionGroup
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget,
    QSizePolicy, QMessageBox, QApplication, QSplitter,
    QToolBar, QLabel,
)

from .map_widget import MapWidget
from .element_table import ElementTableDock
from .log_panel import LogPanel
from .dialogs import AboutDialog, SettingsDialog
from .step_sidebar import StepSidebar
from .steps.step1_find import Step1Find
from .steps.step2_section import Step2Section
from .steps.step3_configure import Step3Configure
from .steps.step4_candidates import Step4Candidates
from .steps.step5_refine        import Step5Refine
from .steps.step6_consolidate   import Step6Consolidate
from .steps.step6_stations      import Step6Stations
from .steps.step6_crosssection  import Step6CrossSection
from .steps.step7_export        import Step7Export

# Stack indices — the UI numbers them 1-9
S_FIND, S_SELECT, S_CONFIG, S_CANDIDATES, S_REFINE, \
    S_CONSOLIDATE, S_STATIONS, S_CROSSSEC, S_EXPORT = range(9)

# Maximum map-view span (km) allowed for "search in view"
_MAX_VIEW_KM = 20.0


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("COYPU Feeder — OSM Railway to LandXML")
        self.resize(1340, 820)
        self.setMinimumSize(1050, 680)

        self._tracks: list          = []
        self._selected_tracks: list = []
        self._settings: dict        = {}
        self._bbox_workers: list    = []
        self._selected_candidate    = None
        # App owns the working alignment: every step reads these, and Refine /
        # Consolidate edit the SAME PI model — so edits remain possible at any
        # point, including after consolidating or exporting.
        self._pi_model              = None    # PIAlignment | None (Level 1 → None)
        self._elements: list        = []
        self._level: str            = ""
        self._stations: list        = []
        self._xy_list: list         = []
        self._chainages_list: list  = []
        self._work_epsg: int        = 32633
        self._current_step: int     = 0
        self._suggested_step: int | None = None
        self._done_steps: set       = set()
        self._stale: set            = set()
        self._project_path          = None
        self._dirty                 = False

        self._qsettings = QSettings("COYPU", "COYPU-Feeder")
        self._prefs = self._load_prefs()

        self._build_layout()
        self._build_menu()
        self._build_toolbar()
        self._wire_signals()
        self._connect_scheme_changes()
        self._apply_prefs()
        self._update_title()
        self._step_label.setText(self._STEP_TIPS[S_FIND][0])
        self._refresh_nav()

    # ------------------------------------------------------------------
    # Preferences (persisted via QSettings)
    # ------------------------------------------------------------------

    def _load_prefs(self) -> dict:
        s = self._qsettings
        return {
            "theme_mode": s.value("theme_mode", "auto", str),
            "font_pt":    int(s.value("font_pt", 9, int)),
            "show_log":   s.value("show_log", True, bool),
            "confirm_start_over": s.value("confirm_start_over", True, bool),
        }

    def _save_prefs(self):
        s = self._qsettings
        for k, val in self._prefs.items():
            s.setValue(k, val)
        s.sync()

    def _apply_prefs(self):
        from gui.theme import apply_theme, apply_font_size, resolve_dark
        app = QApplication.instance()
        dark = resolve_dark(self._prefs["theme_mode"])
        apply_theme(app, dark)
        apply_font_size(app, self._prefs["font_pt"])
        self.map_widget.set_theme(dark)
        self.log_panel.setVisible(self._prefs["show_log"])

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        new_act = QAction("New project", self)
        new_act.setShortcut("Ctrl+N")
        new_act.triggered.connect(self._on_new_project)
        file_menu.addAction(new_act)
        self._act_file_new = new_act
        open_act = QAction("Open project…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._on_open_project)
        file_menu.addAction(open_act)
        self._act_file_open = open_act
        file_menu.addSeparator()
        save_act = QAction("Save project", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._on_save_project)
        file_menu.addAction(save_act)
        self._act_file_save = save_act
        saveas_act = QAction("Save project as…", self)
        saveas_act.setShortcut("Ctrl+Shift+S")
        saveas_act.triggered.connect(self._on_save_project_as)
        file_menu.addAction(saveas_act)
        file_menu.addSeparator()
        self._recent_menu = file_menu.addMenu("Recent projects")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        quit_act = QAction("Exit", self)
        quit_act.setShortcut("Alt+F4")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = mb.addMenu("&View")
        self._act_show_log = QAction("Show log panel", self, checkable=True)
        self._act_show_log.setChecked(self._prefs["show_log"])
        self._act_show_log.toggled.connect(self._on_toggle_log)
        view_menu.addAction(self._act_show_log)
        view_menu.addSeparator()
        reset_act = QAction("Reset panel layout", self)
        reset_act.triggered.connect(self._reset_layout)
        view_menu.addAction(reset_act)

        settings_menu = mb.addMenu("&Settings")
        prefs_act = QAction("Preferences…", self)
        prefs_act.triggered.connect(self._open_settings)
        settings_menu.addAction(prefs_act)

        theme_menu = settings_menu.addMenu("Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for label, mode in (("Automatic (system)", "auto"),
                            ("Dark", "dark"), ("Light", "light")):
            a = QAction(label, self, checkable=True)
            a.setData(mode)
            a.setChecked(self._prefs["theme_mode"] == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_theme_mode(m))
            self._theme_group.addAction(a)
            theme_menu.addAction(a)

        help_menu = mb.addMenu("&Help")
        about_act = QAction("About COYPU Feeder…", self)
        about_act.triggered.connect(self._open_about)
        help_menu.addAction(about_act)
        readme_act = QAction("Open README", self)
        readme_act.triggered.connect(self._open_readme)
        help_menu.addAction(readme_act)
        rel_act = QAction("Check for updates (Releases)…", self)
        rel_act.triggered.connect(self._open_releases)
        help_menu.addAction(rel_act)

    # ------------------------------------------------------------------
    # Toolbar — step navigation + file + quick edit actions
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("Main", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._act_back = QAction("◀ Back", self)
        self._act_back.setToolTip("Go to the previous step")
        self._act_back.triggered.connect(self._on_toolbar_back)
        tb.addAction(self._act_back)

        self._act_forward = QAction("Forward ▶", self)
        self._act_forward.setToolTip("Go to the next suggested step")
        self._act_forward.triggered.connect(self._on_toolbar_forward)
        tb.addAction(self._act_forward)

        self._step_label = QLabel("")
        self._step_label.setStyleSheet("font-weight: bold; padding: 0 12px;")
        tb.addWidget(self._step_label)

        tb.addSeparator()
        tb.addAction(self._act_file_new)
        tb.addAction(self._act_file_open)
        tb.addAction(self._act_file_save)

        tb.addSeparator()
        self._act_show_alignment = QAction("🗺 Show alignment", self)
        self._act_show_alignment.setToolTip(
            "Re-draw the edited alignment and PI overlay on the map.")
        self._act_show_alignment.triggered.connect(
            lambda: self.element_table.show_alignment_requested.emit())
        tb.addAction(self._act_show_alignment)

        self._act_merge_selection = QAction("⤺ Merge selection", self)
        self._act_merge_selection.setToolTip(
            "Merge PI range → single curve (select rows spanning\n"
            "two or more curves in the element table first).")
        self._act_merge_selection.triggered.connect(
            lambda: self.element_table._on_merge_range())
        tb.addAction(self._act_merge_selection)

        self._act_undo_merge = QAction("↩ Undo merge", self)
        self._act_undo_merge.setToolTip(
            "Undo the merge for the selected curve (select its row first).")
        self._act_undo_merge.triggered.connect(
            lambda: self.element_table.undo_selected_merge())
        tb.addAction(self._act_undo_merge)

        # Back routing: each step's OWN back handler (some do overlay
        # cleanup) keyed by the step it goes back FROM — mirrors exactly
        # what that step's in-page "← Back" button does.
        self._toolbar_back_map = {
            S_SELECT:      lambda: self._goto_step(S_FIND),
            S_CONFIG:      lambda: self._goto_step(S_SELECT),
            S_CANDIDATES:  self._on_candidates_back,
            S_REFINE:      self._on_refine_back,
            S_CONSOLIDATE: lambda: self._goto_step(S_REFINE),
            S_STATIONS:    lambda: self._goto_step(S_CONSOLIDATE),
            S_CROSSSEC:    lambda: self._goto_step(S_STATIONS),
            S_EXPORT:      lambda: self._goto_step(S_CROSSSEC),
        }

    def _on_toolbar_back(self):
        handler = self._toolbar_back_map.get(self._current_step)
        if handler:
            handler()

    def _on_toolbar_forward(self):
        if self._suggested_step is not None:
            self._goto_step(self._suggested_step)

    def _refresh_toolbar_edit_actions(self):
        et = self.element_table
        in_edit_step = self._current_step in (S_REFINE, S_CONSOLIDATE)
        self._act_show_alignment.setEnabled(
            in_edit_step and self._pi_model is not None)
        self._act_merge_selection.setEnabled(
            in_edit_step and et._selected_pi_range() is not None)
        self._act_undo_merge.setEnabled(
            in_edit_step and et.selected_merge_pi() is not None)

    def _on_toggle_log(self, on: bool):
        self._prefs["show_log"] = bool(on)
        self.log_panel.setVisible(on)
        self._save_prefs()

    def _reset_layout(self):
        self._map_splitter.setSizes([460, 300])
        self._bottom_splitter.setSizes([280, 800])
        if not self.log_panel.isVisible():
            self._act_show_log.setChecked(True)
        self.log_panel.log("Panel layout reset.", "info")

    def _set_theme_mode(self, mode: str):
        self._prefs["theme_mode"] = mode
        self._save_prefs()
        self._apply_prefs()
        self.log_panel.log(f"Theme set to '{mode}'.", "info")

    def _open_settings(self):
        dlg = SettingsDialog(self._prefs, self)
        if dlg.exec():
            self._prefs.update(dlg.values())
            self._save_prefs()
            self._apply_prefs()
            # keep the menu toggles in sync
            self._act_show_log.setChecked(self._prefs["show_log"])
            for a in self._theme_group.actions():
                a.setChecked(a.data() == self._prefs["theme_mode"])
            self.log_panel.log("Preferences updated.", "info")

    def _open_about(self):
        AboutDialog(self).exec()

    def _open_readme(self):
        AboutDialog(self)._open_readme()

    def _open_releases(self):
        import webbrowser
        import app_meta as meta
        webbrowser.open(meta.RELEASES_URL)

    # ------------------------------------------------------------------
    # Project files (.coypu)
    # ------------------------------------------------------------------

    def _set_dirty(self, dirty: bool = True):
        self._dirty = dirty
        self._update_title()

    def _update_title(self):
        import os
        name = (os.path.basename(self._project_path) if self._project_path
                else "Untitled project")
        star = "*" if getattr(self, "_dirty", False) else ""
        self.setWindowTitle(f"{star}{name} — COYPU Feeder")

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        recent = self._qsettings.value("recent_projects", [], list) or []
        if not recent:
            act = QAction("(none)", self)
            act.setEnabled(False)
            self._recent_menu.addAction(act)
            return
        for path in recent[:8]:
            act = QAction(path, self)
            act.triggered.connect(lambda _=False, p=path: self._load_project(p))
            self._recent_menu.addAction(act)

    def _push_recent(self, path: str):
        recent = self._qsettings.value("recent_projects", [], list) or []
        recent = [p for p in recent if p != path]
        recent.insert(0, path)
        self._qsettings.setValue("recent_projects", recent[:8])
        self._qsettings.sync()
        self._rebuild_recent_menu()

    def _confirm_discard(self) -> bool:
        """Ask to save when there are unsaved changes. False = cancel."""
        if not getattr(self, "_dirty", False):
            return True
        r = QMessageBox.question(
            self, "Unsaved changes",
            "This project has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return self._on_save_project()
        return True

    def _collect_state(self) -> dict:
        sel_idx = [i for i, t in enumerate(self._tracks)
                   if t in self._selected_tracks]
        return {
            "project_name": self._settings.get("project_name", "Railway Alignment"),
            "work_epsg":    self._work_epsg,
            "level":        self._level,
            "step":         self._current_step,
            "settings":     self._settings,
            "tracks":       self._tracks,
            "selected_indices": sel_idx,
            "pi_model":     self._pi_model,
            "stations":     self._stations,
        }

    def _on_new_project(self):
        if not self._confirm_discard():
            return
        self._project_path = None
        self._start_over()
        self._set_dirty(False)
        self.log_panel.log("New project.", "step")

    def _on_open_project(self):
        from PySide6.QtWidgets import QFileDialog
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open COYPU Feeder project", "",
            "COYPU Feeder project (*.coypu);;All files (*.*)")
        if path:
            self._load_project(path)

    def _on_save_project(self) -> bool:
        if not self._project_path:
            return self._on_save_project_as()
        return self._save_project(self._project_path)

    def _on_save_project_as(self) -> bool:
        from PySide6.QtWidgets import QFileDialog
        suggested = self._settings.get("project_name", "project") + ".coypu"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save COYPU Feeder project", suggested,
            "COYPU Feeder project (*.coypu)")
        if not path:
            return False
        if not path.lower().endswith(".coypu"):
            path += ".coypu"
        return self._save_project(path)

    def _save_project(self, path: str) -> bool:
        from project_io import save_project
        if not self._tracks:
            QMessageBox.information(self, "Nothing to save",
                                    "Load a railway first.")
            return False
        try:
            save_project(path, self._collect_state())
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            self.log_panel.log(f"⚠ Save failed: {exc}", "error")
            return False
        self._project_path = path
        self._set_dirty(False)
        self._push_recent(path)
        self.log_panel.log(f"Project saved: {path}", "ok")
        self.statusBar().showMessage(f"✓ Project saved: {path}")
        return True

    def _load_project(self, path: str):
        from project_io import load_project
        from geometry.candidates import metrics_from_stats
        import os
        if not os.path.exists(path):
            QMessageBox.warning(self, "Not found", f"File no longer exists:\n{path}")
            return
        try:
            st = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            self.log_panel.log(f"⚠ Open failed: {exc}", "error")
            return

        self._start_over()
        self._settings        = st["settings"]
        self._work_epsg       = st["work_epsg"]
        self._level           = st["level"]
        self._tracks          = st["tracks"]
        self._selected_tracks = st["selected_tracks"]
        self._xy_list         = st["xy_list"]
        self._chainages_list  = st["chainages_list"]
        self._pi_model        = st["pi_model"]
        self._elements        = st["elements"]
        self._stations        = st["stations"]
        self._project_path    = path

        # Repopulate the steps that own UI state
        self.step2.populate(self._tracks)
        self.map_widget.show_tracks(self._tracks)
        if self._elements:
            metrics = metrics_from_stats(
                getattr(self._pi_model, "last_stats", {}) or {}, self._elements)
            self.element_table.prepare(
                self._pi_model, self._elements,
                check_interval=self._settings.get("check_interval", 5.0))
            self.step5_refine.set_elements(self._elements, metrics)
            self.step6_consolidate.prepare(self._pi_model)
            self._render_elements_on_map(self._elements, fit_view=True)
            self._show_pi_overlay(self._pi_model)
        if self._stations:
            self.step6_stations.set_stations(self._stations)
            self._on_stations_changed(self._stations)
        self._mark_stale()
        self._set_dirty(False)
        self._push_recent(path)
        n_pi = len(self._pi_model.pis) if self._pi_model else 0
        self.log_panel.log(
            f"Project opened: {path} — {len(self._tracks)} track(s), "
            f"{len(self._elements)} elements, {n_pi} PIs, "
            f"{len(self._stations)} station(s).", "ok")
        step = st.get("step", S_REFINE)
        if step not in self._step_reasons():
            self._goto_step(step)
        else:
            self._goto_step(S_REFINE if self._elements else S_FIND)

    def _build_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.sidebar = StepSidebar()
        self.sidebar.setFixedWidth(200)
        h.addWidget(self.sidebar)

        self.map_widget = MapWidget()
        self.map_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Map above; bottom half = log (left) + element table (right).
        # The log is always visible; the table appears in Step 5 only.
        self.element_table = ElementTableDock()
        self.element_table.setVisible(False)
        self.element_table.setMinimumWidth(460)
        self.log_panel = LogPanel()
        self.log_panel.setMinimumWidth(220)

        self._bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._bottom_splitter.addWidget(self.log_panel)
        self._bottom_splitter.addWidget(self.element_table)
        self._bottom_splitter.setStretchFactor(0, 1)
        self._bottom_splitter.setStretchFactor(1, 3)
        self._bottom_splitter.setSizes([280, 800])
        # Never let a drag collapse either panel to zero (unrecoverable).
        self._bottom_splitter.setChildrenCollapsible(False)

        self.map_widget.setMinimumHeight(200)
        self._bottom_splitter.setMinimumHeight(90)

        self._map_splitter = QSplitter(Qt.Orientation.Vertical)
        self._map_splitter.addWidget(self.map_widget)
        self._map_splitter.addWidget(self._bottom_splitter)
        self._map_splitter.setStretchFactor(0, 5)
        self._map_splitter.setStretchFactor(1, 4)
        self._map_splitter.setChildrenCollapsible(False)
        self._map_splitter.setSizes([460, 300])
        h.addWidget(self._map_splitter, stretch=1)

        self.stack = QStackedWidget()
        self.stack.setFixedWidth(340)

        self.step1              = Step1Find()
        self.step2              = Step2Section()
        self.step3              = Step3Configure()
        self.step4_candidates   = Step4Candidates()
        self.step5_refine       = Step5Refine()
        self.step6_consolidate  = Step6Consolidate()    # step 6 in the UI
        self.step6_stations     = Step6Stations()       # step 7 in the UI
        self.step6_crosssec     = Step6CrossSection()   # step 8 in the UI
        self.step7_export       = Step7Export()         # step 9 in the UI

        self.stack.addWidget(self.step1)              # S_FIND
        self.stack.addWidget(self.step2)              # S_SELECT
        self.stack.addWidget(self.step3)              # S_CONFIG
        self.stack.addWidget(self.step4_candidates)   # S_CANDIDATES
        self.stack.addWidget(self.step5_refine)       # S_REFINE
        self.stack.addWidget(self.step6_consolidate)  # S_CONSOLIDATE
        self.stack.addWidget(self.step6_stations)     # S_STATIONS
        self.stack.addWidget(self.step6_crosssec)     # S_CROSSSEC
        self.stack.addWidget(self.step7_export)       # S_EXPORT

        h.addWidget(self.stack)
        self.statusBar().showMessage(
            "Ready. Search for a railway or use 'Lines in View' after zooming in."
        )

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self):
        # Sidebar back-navigation
        self.sidebar.step_clicked.connect(self._goto_step)

        # Map JS errors → status bar
        self.map_widget.js_error.connect(
            lambda msg: self.statusBar().showMessage(f"⚠ {msg}")
        )

        # Map bounds → "Lines in View" search
        self.step1.search_in_view_requested.connect(self._on_search_in_view)
        self.map_widget.bounds_ready.connect(self._on_map_bounds_ready)

        # Step 1 → fetch railway
        self.step1.railway_fetched.connect(self._on_railway_fetched)

        # Step 2 → highlight / fit / confirm / back
        self.step2.highlight_changed.connect(self._on_highlight_changed)
        self.step2.fit_to_tracks_requested.connect(self._on_fit_to_tracks)
        self.step2.section_confirmed.connect(self._on_section_confirmed)
        self.step2.back_requested.connect(lambda: self._goto_step(S_FIND))

        # Step 3 → config confirmed / back
        self.step3.config_confirmed.connect(self._on_config_confirmed)
        self.step3.back_requested.connect(lambda: self._goto_step(S_SELECT))

        # Step 4 (candidates) → map update + selection + hover emphasis + back
        self.step4_candidates.candidate_map_update.connect(self._on_candidate_map_update)
        self.step4_candidates.candidate_selected.connect(self._on_candidate_selected)
        self.step4_candidates.candidate_hovered.connect(
            self.map_widget.emphasize_candidate)
        self.step4_candidates.back_requested.connect(self._on_candidates_back)

        # Step 5 (refine) → done / back
        self.step5_refine.refinement_done.connect(self._on_refinement_done)
        self.step5_refine.back_requested.connect(self._on_refine_back)

        # Element table (bottom dock) → rebuilds + row selection + log
        self.element_table.rebuilt.connect(self._on_elements_rebuilt)
        self.element_table.elements_selected.connect(self._on_element_rows_selected)
        self.element_table.log_message.connect(self.log_panel.log)
        self.element_table.show_alignment_requested.connect(
            self._on_show_alignment_clicked)
        self.element_table.split_pick_mode.connect(self._on_split_pick_mode)
        self.map_widget.split_point_clicked.connect(self._on_split_point_clicked)
        # Map element click (Ctrl = toggle into multiselect) → table selection
        self.map_widget.element_clicked.connect(self.element_table.select_element)

        # Step 6 (cross-section) → back / done / map overlay
        # Step 6 (consolidate) → model edits / navigation / map highlight
        self.step6_consolidate.model_changed.connect(
            self._on_consolidation_model_changed)
        self.step6_consolidate.consolidation_done.connect(
            self._on_consolidation_done)
        self.step6_consolidate.back_requested.connect(
            lambda: self._goto_step(S_REFINE))
        self.step6_consolidate.log_message.connect(self.log_panel.log)
        self.step6_consolidate.highlight_span.connect(
            self.map_widget.highlight_elements)

        # Step 7 (stations) → map markers / click mode / navigation
        self.step6_stations.stations_changed.connect(self._on_stations_changed)
        self.step6_stations.map_click_mode.connect(
            self.map_widget.set_station_click_mode)
        self.step6_stations.stations_done.connect(self._on_stations_done)
        self.step6_stations.back_requested.connect(lambda: self._goto_step(S_CONSOLIDATE))
        self.map_widget.map_clicked.connect(self.step6_stations.on_map_clicked)

        self.step6_crosssec.back_requested.connect(lambda: self._goto_step(S_STATIONS))
        self.step6_crosssec.analysis_done.connect(self._on_analysis_done)
        self.step6_crosssec.cross_section_ready.connect(self._on_cross_section_ready)

        # Step 7 (export) → back
        self.step7_export.back_requested.connect(lambda: self._goto_step(S_CROSSSEC))

        # Step 7 (export) → alignment display / fit / export / restart
        self.step7_export.osm_track_ready.connect(self._on_osm_track_ready)
        self.step7_export.alignment_ready.connect(self._on_alignment_ready)
        self.step7_export.alignment_segments_ready.connect(self._on_alignment_segments_ready)
        self.step7_export.fit_to_alignment_requested.connect(self._on_fit_to_alignment)
        self.step7_export.export_finished.connect(self._on_export_finished)
        self.step7_export.start_over_requested.connect(self._start_over)

    # ------------------------------------------------------------------
    # System colour-scheme changes (dark ↔ light)
    # ------------------------------------------------------------------

    def _connect_scheme_changes(self):
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._on_color_scheme_changed
            )
        except Exception:
            pass  # Qt < 6.5 — no signal, static theme is fine

    def _on_color_scheme_changed(self, scheme):
        # Only auto-follow the OS when the user left the theme on "Automatic".
        if self._prefs.get("theme_mode", "auto") != "auto":
            return
        from gui.theme import apply_theme, apply_font_size
        dark = (scheme == Qt.ColorScheme.Dark)
        apply_theme(QApplication.instance(), dark)
        apply_font_size(QApplication.instance(), self._prefs["font_pt"])
        self.map_widget.set_theme(dark)

    # ------------------------------------------------------------------
    # Step transitions
    # ------------------------------------------------------------------

    _STEP_TIPS = {
        S_FIND: ("Step 1 — Find Railway", [
            "Search by line number, name or OSM relation ID,",
            "or zoom the map and use 'Lines in View'.",
        ]),
        S_SELECT: ("Step 2 — Select Section", [
            "Tick the tracks that belong to your section;",
            "click a row to highlight it on the map.",
        ]),
        S_CONFIG: ("Step 3 — Configure", [
            "Max deviation = PI extraction tolerance;",
            "spiral length applies to Level 3.",
        ]),
        S_CANDIDATES: ("Step 4 — Candidates", [
            "Three levels are computed; hover a card to emphasise",
            "its line on the map, then Select one.",
        ]),
        S_REFINE: ("Step 5 — Refine", [
            "Edit Radius / Spiral L in the table; the map updates live.",
            "Click elements on the map to select rows; Ctrl+click multi-selects.",
            "Select first & last tangent → 'Merge PI range → single curve'.",
            "Short straights between curves offer 'Merge spirals'.",
            "🗺 'Show alignment' re-draws everything after a map reload.",
        ]),
        S_CONSOLIDATE: ("Step 6 — Consolidate", [
            "Scan for runs of same-direction curves joined by short straights",
            "and replace each run with one transition–circular–transition curve.",
            "Only runs within the deviation limit are offered; Undo restores.",
        ]),
        S_STATIONS: ("Step 7 — Stations", [
            "⚡ Auto-detect stations from OSM, 📍 place on map, or add rows.",
            "The CSV (Station,Dwell Time,Name) is written next to the LandXML.",
        ]),
        S_CROSSSEC: ("Step 8 — Cross-Section", [
            "Optional deviation profile of the fitted alignment vs OSM.",
        ]),
        S_EXPORT: ("Step 9 — Export", [
            "Pick the output CRS and file; 👁 Preview shows the LandXML first.",
        ]),
    }

    # Steps that consume the alignment and must be refreshed after an edit
    _DOWNSTREAM = (S_STATIONS, S_CROSSSEC, S_EXPORT)

    # ------------------------------------------------------------------
    # Prerequisites — steps suggest an order, they do not gate it
    # ------------------------------------------------------------------

    def _step_reasons(self) -> dict:
        """{step_index: why it is locked}. Absent key ⇒ available."""
        r = {}
        if not self._tracks:
            for i in (S_SELECT, S_CONFIG, S_CANDIDATES):
                r[i] = "Find and fetch a railway first (step 1)."
        elif not self._selected_tracks:
            for i in (S_CONFIG, S_CANDIDATES):
                r[i] = "Select at least one track (step 2)."
        elif not self._settings:
            r[S_CANDIDATES] = "Confirm the geometry settings (step 3)."
        if not self._elements:
            for i in (S_REFINE,) + self._DOWNSTREAM:
                r[i] = "Select a candidate level first (step 4)."
            r[S_CONSOLIDATE] = "Select a candidate level first (step 4)."
        elif self._pi_model is None:
            r[S_CONSOLIDATE] = ("Level 1 (raw OSM polyline) has no editable "
                                "curves — pick Level 2 or 3 to consolidate.")
        return r

    def _refresh_nav(self):
        reasons = self._step_reasons()
        available = {i for i in range(9) if i not in reasons}
        done = set(self._done_steps) & available
        suggested = None
        for i in range(self._current_step + 1, 9):
            if i in available:
                suggested = i
                break
        self.sidebar.set_states(available, done, suggested, reasons)
        self._suggested_step = suggested
        self._act_back.setEnabled(self._current_step in self._toolbar_back_map)
        self._act_forward.setEnabled(suggested is not None)
        self._refresh_toolbar_edit_actions()

    def _goto_step(self, idx: int):
        # Refresh a downstream step from the current alignment if it went stale
        if idx in self._DOWNSTREAM and idx in self._stale:
            self._refresh_downstream(idx)
        self._current_step = idx
        self._done_steps.add(idx)
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_step(idx)
        # The element table belongs to the alignment-editing steps
        self.element_table.setVisible(idx in (S_REFINE, S_CONSOLIDATE))
        tip = self._STEP_TIPS.get(idx)
        if tip:
            self.log_panel.log_step(tip[0], tip[1])
            self._step_label.setText(tip[0])
        self._refresh_nav()

    def _mark_stale(self):
        """The alignment changed — downstream steps must re-read it."""
        self._stale.update(self._DOWNSTREAM)
        self._set_dirty(True)

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def _refresh_downstream(self, idx: int):
        """Re-prepare a stale downstream step from the current elements."""
        try:
            if idx == S_STATIONS:
                # Station chainages are re-snapped to the new geometry
                self.step6_stations.prepare(self._elements,
                                            self._selected_tracks, self._work_epsg)
            elif idx == S_CROSSSEC:
                self.step6_crosssec.prepare(self._elements, self._work_epsg)
            elif idx == S_EXPORT:
                self.step7_export.prepare(
                    [self._elements], self._selected_tracks, self._settings,
                    self._work_epsg, self._xy_list,
                    stations=self._stations,
                )
            self._stale.discard(idx)
            self.log_panel.log(
                f"Step {idx + 1} refreshed from the edited alignment "
                f"({len(self._elements)} elements).", "info")
        except Exception as exc:
            self.log_panel.log(f"⚠ Could not refresh step {idx + 1}: {exc}", "warn")

    # ------------------------------------------------------------------
    # Step 2 map interactions
    # ------------------------------------------------------------------

    def _on_highlight_changed(self, idx: int):
        self.map_widget.highlight_track(idx)
        if idx < 0:
            self.statusBar().showMessage("Track highlight reset — all tracks shown.")
        elif idx < len(self._tracks):
            self.statusBar().showMessage(
                f"Highlighted track {idx + 1}: {self._tracks[idx].name}"
            )

    def _on_fit_to_tracks(self):
        if self._tracks:
            self.map_widget.fly_to_tracks()
            self.statusBar().showMessage(
                f"Zooming map to {len(self._tracks)} track(s)."
            )
        else:
            self.statusBar().showMessage("No tracks loaded yet.")

    # ------------------------------------------------------------------
    # Step 6 map interactions
    # ------------------------------------------------------------------

    def _on_osm_track_ready(self, alignments: list):
        """Show the raw OSM polyline as a dashed cyan reference while fitting runs."""
        if alignments and any(len(a) > 0 for a in alignments):
            self.map_widget.show_osm_reference(alignments)
            self.statusBar().showMessage(
                "OSM reference polyline drawn (dashed cyan). Fitting geometry…"
            )

    def _on_alignment_ready(self, alignments: list):
        if not alignments or not any(len(a) > 0 for a in alignments):
            self.statusBar().showMessage(
                "⚠ Export finished but alignment contains no points — "
                "nothing drawn on map."
            )
            return
        # Default merged-red drawing; superseded by per-element rendering
        # below when alignment_segments_ready fires.
        self.map_widget.show_alignment(alignments)
        total_pts = sum(len(a) for a in alignments)
        self.statusBar().showMessage(
            f"Both overlays ready — 🔴 red: fitted LandXML ({total_pts} pts)  "
            f"🔵 cyan dashed: OSM reference  ({len(alignments)} track(s))."
        )

    def _on_alignment_segments_ready(self, segments: list):
        """Per-element coloured rendering after final export."""
        if not segments:
            return
        self.map_widget.show_alignment_segmented(segments)
        n_line   = sum(1 for s in segments if s.get("type") == "Line")
        n_arc    = sum(1 for s in segments if s.get("type") == "Arc")
        n_spiral = sum(1 for s in segments if s.get("type") == "Spiral")
        self.statusBar().showMessage(
            f"Per-element view drawn — 🔵 {n_line} Lines · 🔴 {n_arc} Arcs · "
            f"🟢 {n_spiral} Spirals. Hover any segment for parameters."
        )

    def _on_fit_to_alignment(self):
        self.map_widget.fly_to_alignment()
        self.statusBar().showMessage("Zooming map to exported alignment.")

    # ------------------------------------------------------------------
    # "Lines in View" search
    # ------------------------------------------------------------------

    def _on_search_in_view(self):
        """Step 1 requested a search — ask the map for its current bounds."""
        self.step1.set_view_search_busy(True)
        self.map_widget.request_bounds()

    def _on_map_bounds_ready(self, s: float, w: float, n: float, e: float):
        """Map returned its bounds; run 'Lines in View' search."""
        # ── Overpass "Lines in View" search ──────────────────────────
        center_lat = (s + n) / 2.0
        lat_km = (n - s) * 111.0
        lon_km = (e - w) * 111.0 * math.cos(math.radians(center_lat))

        if lat_km > _MAX_VIEW_KM or lon_km > _MAX_VIEW_KM:
            self.step1.set_view_search_busy(False)
            self.step1.show_view_results(
                [],
                status=(
                    f"⚠ View too large ({lat_km:.0f} × {lon_km:.0f} km). "
                    f"Zoom in to ≤ {_MAX_VIEW_KM:.0f} km and try again."
                ),
            )
            self.statusBar().showMessage(
                "View too large for 'Lines in View' search — zoom in more."
            )
            return

        self.statusBar().showMessage(
            f"Searching railway lines in {lat_km:.1f} × {lon_km:.1f} km view…"
        )

        from gui.worker import SearchWorker
        worker = SearchWorker("bbox", (s, w, n, e), self)
        worker.results_ready.connect(self._on_view_results_ready)
        worker.failed.connect(self._on_view_search_failed)
        worker.finished.connect(lambda: self.step1.set_view_search_busy(False))
        worker.finished.connect(
            lambda: self._bbox_workers.remove(worker)
            if worker in self._bbox_workers else None
        )
        self._bbox_workers.append(worker)
        worker.start()

    def _on_view_results_ready(self, results: list):
        n = len(results)
        status = f"Found {n} railway line{'s' if n != 1 else ''} in current view."
        self.step1.show_view_results(results, status)
        self._goto_step(S_FIND)
        self.statusBar().showMessage(status + " Click a result to load it.")

    def _on_view_search_failed(self, error: str):
        self.step1.show_view_results([], status=f"Search failed: {error}")
        self.statusBar().showMessage(f"Lines-in-View search failed: {error}")

    # ------------------------------------------------------------------
    # Railway loaded
    # ------------------------------------------------------------------

    def _on_railway_fetched(self, overpass_data, relation_info: dict):
        from osm.parser import parse_tracks
        try:
            self._tracks = parse_tracks(overpass_data)
        except Exception as exc:
            QMessageBox.critical(self, "Parse error",
                                 f"Failed to parse track data:\n{exc}")
            self.statusBar().showMessage(f"⚠ Parse error: {exc}")
            return

        if not self._tracks:
            QMessageBox.warning(
                self, "No tracks found",
                "The relation was fetched but no continuous track could be "
                "extracted.\nThe relation may have no ways, or its ways are "
                "not connected."
            )
            self.statusBar().showMessage("⚠ No tracks found in relation.")
            return

        self.map_widget.clear_alignment()
        self.map_widget.show_tracks(self._tracks)
        self.step2.populate(self._tracks)
        n = len(self._tracks)
        name = relation_info.get("name", "")
        self.statusBar().showMessage(
            f"✓ Loaded '{name}' — {n} track{'s' if n != 1 else ''} drawn on map. "
            "Select tracks and click Next."
        )
        self._goto_step(S_SELECT)

    # ------------------------------------------------------------------
    # Section confirmed
    # ------------------------------------------------------------------

    def _on_section_confirmed(self, selected_tracks: list):
        self._selected_tracks = selected_tracks
        self.statusBar().showMessage(
            f"{len(selected_tracks)} track(s) selected. Configure export settings."
        )
        self._goto_step(S_CONFIG)

    # ------------------------------------------------------------------
    # Config confirmed → project coordinates + launch candidate worker
    # ------------------------------------------------------------------

    def _on_config_confirmed(self, settings: dict):
        from geometry.projection import wgs84_to_projected, auto_utm_epsg
        from geometry.curvature import compute_chainages
        import numpy as np

        self._settings = settings

        # Internal working CRS is always auto-UTM (metric, undistorted).
        # The user chooses the output CRS in Step 6 just before exporting.
        work_epsg = auto_utm_epsg(self._selected_tracks[0].nodes)
        self._work_epsg = work_epsg

        xy_list        = []
        chainages_list = []

        for track in self._selected_tracks:
            xy = np.array(wgs84_to_projected(track.nodes, work_epsg))
            xy_list.append(xy)
            chainages_list.append(compute_chainages(xy))

        self._xy_list        = xy_list
        self._chainages_list = chainages_list

        self.map_widget.clear_candidates()
        self.map_widget.clear_alignment()

        # Show dashed cyan OSM reference immediately so it's visible during
        # candidate generation (Step 4) and not just after export.
        osm_ref = [[[lat, lon] for lat, lon in t.nodes]
                   for t in self._selected_tracks]
        if any(len(r) > 0 for r in osm_ref):
            self.map_widget.show_osm_reference(osm_ref)

        self.step4_candidates.prepare(
            self._selected_tracks, settings, xy_list, chainages_list, work_epsg
        )
        self.statusBar().showMessage(
            "Projecting coordinates… Running candidate algorithms."
        )
        self._goto_step(S_CANDIDATES)

    # ------------------------------------------------------------------
    # Candidate map overlay update
    # ------------------------------------------------------------------

    def _on_candidate_map_update(self, candidates: list):
        """Called each time a candidate algorithm completes — update map overlays."""
        payload = [
            {
                "nodes": [[lat, lon] for lat, lon in c.geo_wgs84],
                "color": c.color_hex,
                "label": c.label,
                "algo":  c.algorithm_id,
            }
            for c in candidates
            if c.geo_wgs84
        ]
        self.map_widget.show_candidates(payload)

    # ------------------------------------------------------------------
    # Candidate selected → go to Step 5
    # ------------------------------------------------------------------

    def _on_candidate_selected(self, candidate):
        self._selected_candidate = candidate
        # App takes ownership of the working alignment: the PI model chosen
        # here is the one Refine AND Consolidate edit for the rest of the
        # session, so edits stay possible after any later step.
        self._pi_model = getattr(candidate, "pi_model", None)
        self._elements = list(getattr(candidate, "elements", []) or [])
        self._level    = getattr(candidate, "algorithm_id", "")
        self._mark_stale()
        # Clear candidate overlays; Step 5 will show the chosen one as
        # per-element coloured alignment with hover tooltips.
        self.map_widget.clear_candidates()
        xy        = self._xy_list[0]        if self._xy_list        else None
        chainages = self._chainages_list[0] if self._chainages_list else None
        self.step5_refine.prepare(candidate, xy, chainages, self._settings)
        self.step6_consolidate.prepare(self._pi_model)

        # Always draw the merged red polyline first so the user is guaranteed
        # to see *something* even if the segmented call has any issue. Then
        # overlay the per-element coloured polylines (which replace the merged
        # one via clearAlignment() inside showAlignmentSegmented).
        merged = getattr(candidate, "geo_wgs84", None)
        if merged:
            self.map_widget.show_alignment([[list(pt) for pt in merged]])

        segments = getattr(candidate, "geo_segments_wgs84", None) or []
        n_line   = sum(1 for s in segments if s.get("type") == "Line")
        n_arc    = sum(1 for s in segments if s.get("type") == "Arc")
        n_spiral = sum(1 for s in segments if s.get("type") == "Spiral")
        print(f"[App] candidate '{getattr(candidate, 'label', '?')}' selected: "
              f"{len(segments)} segments ({n_line} Lines, {n_arc} Arcs, {n_spiral} Spirals); "
              f"merged_pts={len(merged) if merged else 0}")
        if segments:
            self.map_widget.show_alignment_segmented(segments)

        # Element table dock (editable when the candidate carries a PI model)
        self.element_table.prepare(
            self._pi_model, self._elements,
            check_interval=self._settings.get("check_interval", 5.0),
        )
        self._show_pi_overlay(self._pi_model)

        self.statusBar().showMessage(
            f"Candidate '{getattr(candidate, 'label', '')}' selected — "
            f"{n_line} Lines · {n_arc} Arcs · {n_spiral} Spirals shown. "
            "Hover any segment for parameters."
        )
        self._goto_step(S_REFINE)

    def _on_candidates_back(self):
        """Go back from Candidates to Configure — clear overlays."""
        self.map_widget.clear_candidates()
        self.map_widget.clear_alignment()
        self.map_widget.clear_osm_reference()
        self._goto_step(S_CONFIG)

    def _render_elements_on_map(self, elements: list, fit_view: bool = False):
        """Render an element chain as the segmented alignment overlay.

        fit_view=False (default) keeps the user's current zoom/pan — used
        for edit-driven rebuilds from the element table."""
        from geometry.alignment import (
            reconstruct_alignment_projected,
            reconstruct_alignment_per_element,
        )
        from geometry.projection import projected_to_wgs84

        if not elements:
            return
        try:
            segments_payload = []
            try:
                from gui.worker import _serialise_element_params
                # Sample density scales with length: 2 m on short sections,
                # up to 20 m on very long ones — keeps the JSON payload and
                # the Leaflet layer work bounded on 50 km+ alignments.
                total_len = sum(float(e.get("length", 0.0)) for e in elements)
                interval = min(20.0, max(2.0, total_len / 4000.0))
                per_el = reconstruct_alignment_per_element(
                    elements, sample_interval=interval)
                # One batched projection for ALL samples (was one call per
                # element → hundreds of transformer builds per refresh).
                flat: list = []
                spans: list = []
                for el, pts in per_el:
                    spans.append((el, len(flat), len(pts)))
                    flat.extend(pts)
                wgs_all = projected_to_wgs84(flat, self._work_epsg) if flat else []
                for el, off, n in spans:
                    if not n:
                        continue
                    segments_payload.append({
                        "type":   el.get("type", "Line"),
                        "params": _serialise_element_params(el),
                        "points": [list(p) for p in wgs_all[off:off + n]],
                    })
            except Exception:
                segments_payload = []

            if segments_payload:
                if fit_view:
                    self.map_widget.show_alignment_segmented(segments_payload, True)
                else:
                    # edit-driven refresh: diff-update keeps unchanged layers
                    self.map_widget.update_alignment_segmented(segments_payload)
            else:
                geo_xy     = reconstruct_alignment_projected(elements, sample_interval=5.0)
                geo_latlon = projected_to_wgs84(geo_xy, self._work_epsg)
                self.map_widget.show_alignment([[[lat, lon] for lat, lon in geo_latlon]])
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ Map update failed: {exc}")

    def _show_pi_overlay(self, pi_model):
        """Draw PI markers + dashed virtual tangent stubs for the model."""
        from geometry.projection import projected_to_wgs84
        if pi_model is None or len(getattr(pi_model, "V", [])) < 3:
            self.map_widget.clear_pi_overlay()
            return
        try:
            import math as _m
            omitted = {p.index for p in pi_model.pis if p.omitted}
            defl    = {p.index: p.deflection for p in pi_model.pis}
            stubs   = list(getattr(pi_model, "tangent_stubs", []))

            # Collect every point first, then project ALL of them in one call
            # (was one call per PI *and* one per stub).
            ks   = list(range(1, len(pi_model.V) - 1))
            flat = [(float(pi_model.V[k, 0]), float(pi_model.V[k, 1])) for k in ks]
            for stub in stubs:
                tc, pixy, ct = stub["tc"], stub["pi_xy"], stub["ct"]
                flat.extend([(tc[0], tc[1]), (pixy[0], pixy[1]), (ct[0], ct[1])])
            wgs = projected_to_wgs84(flat, self._work_epsg) if flat else []

            pi_pts = [
                {"id": k, "latlon": [wgs[i][0], wgs[i][1]],
                 "omitted": k in omitted,
                 "defl_deg": _m.degrees(defl.get(k, 0.0))}
                for i, k in enumerate(ks)
            ]
            tangents = []
            base = len(ks)
            for j in range(len(stubs)):
                a, b, c = wgs[base + 3 * j], wgs[base + 3 * j + 1], wgs[base + 3 * j + 2]
                tangents.append({"from": list(a), "to": list(b)})
                tangents.append({"from": list(b), "to": list(c)})
            self.map_widget.show_pi_overlay({"pis": pi_pts, "tangents": tangents})
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ PI overlay failed: {exc}")

    def _on_elements_rebuilt(self, elements: list, metrics: dict):
        """Element table edited → refresh Step 5, map, PI overlay and log."""
        # App owns the alignment; any edit invalidates the downstream steps,
        # which re-read it when the user next opens them.
        self._elements = list(elements)
        self._mark_stale()
        self._refresh_nav()
        self.step5_refine.set_elements(elements, metrics)
        self._render_elements_on_map(elements)
        self._show_pi_overlay(self.element_table._model)
        n_spirals = sum(1 for e in elements if e.get("type") == "Spiral")
        msg = (f"Alignment rebuilt — {len(elements)} elements "
               f"({n_spirals} spiral{'s' if n_spirals != 1 else ''}), "
               f"max dev {metrics.get('max_deviation', 0.0):.2f} m, "
               f"C1 {metrics.get('max_heading_jump_deg', 0.0):.3f}°")
        self.statusBar().showMessage(msg)
        self.log_panel.log(msg, "ok")
        # Geometry notes from the rebuild (radius clamps, skipped curves, …)
        model = self.element_table._model
        for note in (getattr(model, "log", None) or []):
            self.log_panel.log(note, "warn" if note.startswith("⚠") else "info")

    def _on_element_rows_selected(self, element_ids: list):
        self.map_widget.highlight_elements(element_ids)
        self._refresh_toolbar_edit_actions()

    def _on_split_pick_mode(self, on: bool):
        self.map_widget.set_split_click_mode(on)
        if on:
            self.log_panel.log(
                "Split-pick mode armed — click a point on the map to "
                "insert a PI there.", "info")

    def _on_split_point_clicked(self, lat: float, lon: float):
        from geometry.projection import wgs84_to_projected
        xy = wgs84_to_projected([(lat, lon)], self._work_epsg)[0]
        self.element_table.insert_pi_at_xy(xy)

    def _on_show_alignment_clicked(self):
        """Re-draw the edited alignment + PI overlay (e.g. after map reload)."""
        elements = self._elements or self.element_table.current_elements()
        if not elements:
            self.log_panel.log("No alignment to show yet.", "warn")
            return
        self._render_elements_on_map(elements, fit_view=True)
        self._show_pi_overlay(self._pi_model)
        self.log_panel.log("Alignment re-drawn on the map.", "ok")

    def _on_refine_back(self):
        """Go back from Refine to Candidates — restore all candidate overlays."""
        self.map_widget.clear_alignment()
        self.map_widget.clear_pi_overlay()
        # Re-emit candidate overlays if available
        candidates = [c for c in self.step4_candidates._candidates.values()
                      if c.geo_wgs84]
        if candidates:
            payload = [
                {"nodes": [[lat, lon] for lat, lon in c.geo_wgs84],
                 "color": c.color_hex, "label": c.label, "algo": c.algorithm_id}
                for c in candidates
            ]
            self.map_widget.show_candidates(payload)
        self._goto_step(S_CANDIDATES)

    # ------------------------------------------------------------------
    # Refinement accepted → suggest Consolidate (step 6)
    # ------------------------------------------------------------------

    def _on_refinement_done(self, elements: list):
        self._elements = elements
        self._mark_stale()
        self.step6_consolidate.prepare(self._pi_model)
        self.statusBar().showMessage(
            "Refinement accepted. Consolidate runs of same-direction curves, "
            "or skip ahead — you can return and edit at any time."
        )
        self._goto_step(S_CONSOLIDATE)

    # ------------------------------------------------------------------
    # Consolidate (step 6)
    # ------------------------------------------------------------------

    def _on_consolidation_model_changed(self):
        """Consolidation applied/undone — refresh table, map and downstream."""
        if self._pi_model is None:
            return
        from geometry.candidates import metrics_from_stats
        self._elements = list(self._pi_model.elements)
        metrics = metrics_from_stats(
            getattr(self._pi_model, "last_stats", {}) or {}, self._elements)
        # Rebind the element table to the (structurally changed) model
        self.element_table.prepare(
            self._pi_model, self._elements,
            check_interval=self._settings.get("check_interval", 5.0))
        self.step5_refine.set_elements(self._elements, metrics)
        self._render_elements_on_map(self._elements)
        self._show_pi_overlay(self._pi_model)
        self._mark_stale()
        self._refresh_nav()
        self.statusBar().showMessage(
            f"Alignment now has {len(self._elements)} elements "
            f"(max dev {metrics.get('max_deviation', 0.0):.2f} m).")

    def _on_consolidation_done(self, elements: list):
        if elements:
            self._elements = elements
        self._mark_stale()
        self._goto_step(S_STATIONS)

    # ------------------------------------------------------------------
    # Stations done → go to Cross-section
    # ------------------------------------------------------------------

    def _on_stations_changed(self, stations: list):
        """Live station markers on the map."""
        payload = [
            {"name": s.name, "latlon": [s.latlon[0], s.latlon[1]],
             "km": s.chainage_m / 1000.0}
            for s in stations
            if s.latlon and (s.latlon[0] or s.latlon[1])
        ]
        self.map_widget.show_stations(payload)

    def _on_stations_done(self, stations: list):
        self._stations = stations
        self.map_widget.set_station_click_mode(False)
        self.step6_crosssec.prepare(self._elements, self._work_epsg)
        self._stale.discard(S_CROSSSEC)
        self.statusBar().showMessage(
            f"{len(stations)} station(s) recorded. Run cross-section "
            "analysis or skip to export."
        )
        self._goto_step(S_CROSSSEC)

    # ------------------------------------------------------------------
    # Cross-section analysis done → go to Export
    # ------------------------------------------------------------------

    def _on_analysis_done(self, results: list):
        # results may be [] if the step was skipped
        self.step7_export.prepare(
            [self._elements], self._selected_tracks, self._settings,
            self._work_epsg, self._xy_list,
            stations=self._stations,
        )
        self._stale.discard(S_EXPORT)
        n = len(results)
        msg = (
            f"Cross-section: {n} stations analysed. Choose a file and export."
            if n else
            "Skipped cross-section analysis. Choose a file and export."
        )
        self.statusBar().showMessage(msg)
        self._goto_step(S_EXPORT)

    def _on_cross_section_ready(self, left_pts: list, right_pts: list):
        """Show coloured cross-section overlays on the map."""
        self.map_widget.show_cross_section(left_pts, right_pts)

    # ------------------------------------------------------------------
    # Export finished
    # ------------------------------------------------------------------

    def _on_export_finished(self, filepath: str, work_epsg: int):
        # alignment_ready signal from step6 already triggered map display
        self.statusBar().showMessage(
            f"✓ Export complete (EPSG:{work_epsg}) — alignment shown on map. {filepath}"
        )

    # ------------------------------------------------------------------
    # Start over
    # ------------------------------------------------------------------

    def _start_over(self):
        self._tracks             = []
        self._selected_tracks    = []
        self._settings           = {}
        self._selected_candidate = None
        self._pi_model           = None
        self._elements           = []
        self._level              = ""
        self._stations           = []
        self._xy_list            = []
        self._chainages_list     = []
        self._done_steps         = set()
        self._stale              = set()
        self.map_widget.clear_all()   # clears tracks + osmRef + alignment + candidates + cross-section + PI/stations
        self.element_table.clear()
        self.step6_stations.reset()
        self.step6_consolidate.prepare(None)
        self.sidebar.reset()
        self._goto_step(S_FIND)
        self.statusBar().showMessage(
            "Ready. Search for a new railway or use 'Lines in View'."
        )
