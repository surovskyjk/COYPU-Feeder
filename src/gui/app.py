"""
Main application window — PySide6.
3-column layout: StepSidebar | MapWidget | QStackedWidget (step panels).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QGuiApplication, QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QSizePolicy, QMessageBox, QApplication, QSplitter,
    QToolBar, QLabel, QScrollArea, QFrame, QToolButton, QComboBox,
)

from gui import icons
from gui.palette import OSM_REF_DEFAULT
from .map_widget import MapWidget
from .element_table import ElementTableDock
from .metrics_chip import MetricsChip
from .edit_history import EditHistory
from .log_panel import LogPanel
from .dialogs import AboutDialog, SettingsDialog
from .step_sidebar import StepSidebar
from .steps.step1_find import Step1Find
from .steps.step2_section import Step2Section
from .steps.step3_configure import Step3Configure
from .steps.step4_candidates import Step4Candidates
from .steps.step5_design        import Step5Design
from .steps.step6_stations      import Step6Stations
from .steps.step6_crosssection  import Step6CrossSection
from .steps.step8_vertical      import Step8Vertical
from .steps.step7_export        import Step7Export
from .profile_canvas import ProfileCanvas

# Stack indices — the UI numbers them 1-9. Steps 5 (Refine) and 6
# (Consolidate) were merged into one "Refine & Design" workspace;
# Vertical Profile was later inserted between Cross-Section and Export.
S_FIND, S_SELECT, S_CONFIG, S_CANDIDATES, S_DESIGN, \
    S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT = range(9)
N_STEPS = 9

# Maximum map-view span (km) allowed for "search in view"
_MAX_VIEW_KM = 20.0


def _replay_tail(log: list, already_replayed: int) -> tuple[list, int]:
    """`log` (e.g. a PIAlignment's `.log`) only ever GROWS across edits
    within one model's lifetime — only a full rebuild resets it to `[]`.
    Returns (new_entries_since_last_call, updated_replayed_count):
    just the new tail on an ordinary edit; the whole (usually short/empty)
    log again if `log` is shorter than what was already replayed, which
    only happens right after a reset."""
    if len(log) < already_replayed:
        already_replayed = 0
    return log[already_replayed:], len(log)


class _ToolbarScrollArea(QScrollArea):
    """Horizontally-scrollable host for the main toolbar (Part C).

    A plain QScrollArea only reacts to wheel events along the axis the
    wheel itself reports; there's nothing to scroll vertically in a
    single-row bar, so any wheel tick over it (Shift-held or not) pans the
    toolbar horizontally instead of doing nothing."""

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)


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
        # Undo/redo for alignment edits (merge, radius/spiral, omit, split,
        # delete, trim, PI drag) — the toolbar Back/Forward buttons drain
        # this first while in Refine/Consolidate before falling back to
        # ordinary step navigation (see _on_toolbar_back/_on_toolbar_forward).
        self._edit_history           = EditHistory()
        # Freshly-fitted baseline stashed whenever a candidate is chosen or a
        # project is loaded — "Restore default" reverts to this and wipes
        # the undo/redo history (Part D).
        self._refine_baseline        = None   # PIAlignment | None
        self._elements: list        = []
        self._level: str            = ""
        # Level 1 (raw OSM polyline) has no PI model to read live stats from
        # — its metrics are computed once at candidate selection and cached
        # here for the persistent metrics chip.
        self._level1_metrics: dict | None = None
        # Live PI-drag preview (Phase 8): dragstart pushes ONE history
        # entry for the whole gesture; drag ticks move the model directly
        # without re-pushing. _drag_history_pushed tracks whether dragend
        # needs to push its own (live preview was off, or never started).
        self._drag_history_pushed = False
        self._pending_drag: tuple[int, float, float] | None = None
        self._drag_preview_timer: QTimer | None = None
        self._stations: list        = []
        # Stop-like nodes (stop positions, halts, stations) that came WITH the
        # fetched relation's track nodes — Step 6's fallback when every
        # Overpass server is down. Not persisted: empty after a project load.
        self._osm_stop_nodes: list  = []
        # The vertical counterpart of _pi_model: App is the single owner,
        # Step8Vertical mutates it in place and never replaces it.
        self._profile               = None   # VerticalProfile | None
        self._xy_list: list         = []
        self._chainages_list: list  = []
        self._work_epsg: int        = 32633
        self._current_step: int     = 0
        self._suggested_step: int | None = None
        self._done_steps: set       = set()
        self._stale: set            = set()
        self._project_path          = None
        self._dirty                 = False
        self._split_pick_target: str | None = None   # "line" | "track" | None
        # How much of model.log (candidates.rebuild_pi_span's accumulating
        # clamp/skip notes — only ever reset by a full rebuild) has already
        # been replayed to the log panel. Without this, _on_elements_rebuilt
        # used to re-log the ENTIRE history on every single edit, so a
        # session's Nth radius tweak cost O(N) just for logging — the one
        # part of "editing gets slower the more you've done" that scaled
        # with edits-so-far rather than alignment size (see _on_elements_rebuilt).
        self._pi_log_replayed = 0

        self._qsettings = QSettings("COYPU", "COYPU-Feeder")
        self._prefs = self._load_prefs()

        self._build_layout()
        self._build_menu()
        self._build_toolbar()
        self._finish_menu_toolbar_wiring()
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
            "show_toolbar": s.value("show_toolbar", True, bool),
            "show_metrics_chip": s.value("show_metrics_chip", True, bool),
            "show_map_legend": s.value("show_map_legend", True, bool),
            # Req 9b: "type" (Line/Arc/Spiral colours) or "deviation" (green
            # -> yellow -> red heatmap against the project's max-deviation
            # tolerance).
            "map_color_mode": s.value("map_color_mode", "type", str),
            "live_drag_preview": s.value("live_drag_preview", True, bool),
            "confirm_start_over": s.value("confirm_start_over", True, bool),
            # Original-alignment (OSM reference) appearance — Part E
            "osm_ref_visible": s.value("osm_ref_visible", True, bool),
            "osm_ref_color":   s.value("osm_ref_color", OSM_REF_DEFAULT, str),
            "osm_ref_opacity": float(s.value("osm_ref_opacity", 0.45, float)),
            # Engineering defaults pre-filling Step 3 for a NEW project (see
            # SettingsDialog.ENGINEERING_DEFAULTS for the matching fallbacks
            # — every one a railway-engineering parameter, never an
            # internal numerical-stability constant).
            "default_max_deviation":     float(s.value("default_max_deviation", 1.0, float)),
            "default_min_radius":        float(s.value("default_min_radius", 150.0, float)),
            "default_spiral_length":     float(s.value("default_spiral_length", 20.0, float)),
            "default_pi_min_tangent":    float(s.value("default_pi_min_tangent", 60.0, float)),
            "default_min_tangent_length": float(s.value("default_min_tangent_length", 30.0, float)),
            "default_min_line_length":   float(s.value("default_min_line_length", 10.0, float)),
            "default_min_arc_length":    float(s.value("default_min_arc_length", 10.0, float)),
            "default_min_spiral_length": float(s.value("default_min_spiral_length", 10.0, float)),
        }

    def _save_prefs(self):
        s = self._qsettings
        for k, val in self._prefs.items():
            s.setValue(k, val)
        s.sync()

    def _save_prefs_debounced(self):
        """Coalesce rapid pref writes (opacity slider drags fired a
        synchronous QSettings disk sync per tick) into one save."""
        t = getattr(self, "_prefs_save_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(500)
            t.timeout.connect(self._save_prefs)
            self._prefs_save_timer = t
        t.start()

    def _apply_prefs(self):
        from gui.theme import apply_theme_mode, apply_font_size
        app = QApplication.instance()
        # Font size first: the theme fanout re-derives every bold label font
        # from the application font, so the new size must already be set.
        apply_font_size(app, self._prefs["font_pt"])
        # Passing the mode (not a resolved bool) keeps "auto" tracking the OS.
        # This emits themeChanged, which drives _on_theme_changed below.
        apply_theme_mode(self._prefs["theme_mode"])
        self.log_panel.setVisible(self._prefs["show_log"])
        self.map_widget.set_osm_reference_style(
            self._prefs["osm_ref_visible"], self._prefs["osm_ref_color"],
            self._prefs["osm_ref_opacity"])
        self.map_widget.set_legend_visible(self._prefs["show_map_legend"])
        self.map_widget.set_live_drag_preview(self._prefs["live_drag_preview"])
        self.element_table.set_live_preview(self._prefs["live_drag_preview"])
        combo = getattr(self, "_color_mode_combo", None)
        if combo is not None:
            i = combo.findData(self._prefs.get("map_color_mode", "type"))
            if i >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self):
        mb = self.menuBar()

        style = self.style()
        file_menu = mb.addMenu("&File")
        new_act = QAction(icons.makeIcon("newProject"), "New project", self)
        new_act.setShortcut("Ctrl+N")
        new_act.setToolTip(
            "Start over with a blank project — clears the current "
            "tracks, alignment and edit history.")
        new_act.triggered.connect(self._on_new_project)
        file_menu.addAction(new_act)
        self._act_file_new = new_act
        open_act = QAction(icons.makeIcon("open"), "Open project…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.setToolTip("Open a saved .coypuf project file.")
        open_act.triggered.connect(self._on_open_project)
        file_menu.addAction(open_act)
        self._act_file_open = open_act
        file_menu.addSeparator()
        save_act = QAction(icons.makeIcon("save"), "Save project", self)
        save_act.setShortcut("Ctrl+S")
        save_act.setToolTip(
            "Save tracks, alignment and edit state to the current .coypuf "
            "file (or prompts for a location the first time).")
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

        self._metrics_chip = MetricsChip()
        self.statusBar().addPermanentWidget(self._metrics_chip)
        self._act_show_metrics = QAction(
            "Show deviation metrics", self, checkable=True)
        self._act_show_metrics.setChecked(self._prefs["show_metrics_chip"])
        self._act_show_metrics.toggled.connect(self._on_toggle_metrics_chip)
        view_menu.addAction(self._act_show_metrics)

        self._act_show_legend = QAction("Show map legend", self, checkable=True)
        self._act_show_legend.setChecked(self._prefs["show_map_legend"])
        self._act_show_legend.toggled.connect(self._on_toggle_map_legend)
        view_menu.addAction(self._act_show_legend)

        self._act_live_drag_preview = QAction(
            "Live preview while editing", self, checkable=True)
        self._act_live_drag_preview.setChecked(self._prefs["live_drag_preview"])
        self._act_live_drag_preview.setToolTip(
            "Update the map continuously while dragging a PI or adjusting a "
            "radius/spiral length, instead of only once you release it/stop "
            "typing. Turn off on very long alignments if edits feel laggy.")
        self._act_live_drag_preview.toggled.connect(self._on_toggle_live_drag_preview)
        view_menu.addAction(self._act_live_drag_preview)

        view_menu.addSeparator()
        reset_act = QAction("Reset panel layout", self)
        reset_act.triggered.connect(self._reset_layout)
        view_menu.addAction(reset_act)
        self._view_menu = view_menu   # "Show toolbar" is appended once the
                                       # toolbar itself exists — see _finish_menu_toolbar_wiring

        settings_menu = mb.addMenu("&Settings")
        self._settings_menu = settings_menu   # anchor for inserting the Edit
                                               # menu once toolbar actions exist
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
        from PySide6.QtCore import QSize
        tb = QToolBar("Main", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        # Compact two-row buttons (icon above text) — denser than the
        # previous single-row icon-beside-text layout.
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tb.setIconSize(QSize(18, 18))
        self._toolbar = tb

        style = self.style()

        brand = QLabel("COYPU Feeder")
        brand.setObjectName("brandLabel")
        brand.setToolTip(
            "COYPU Feeder — converts OSM railway polylines into "
            "C1-continuous LandXML alignments for COYPU.")
        self._brand = brand   # restyled from tokens in _on_theme_changed
        tb.addWidget(brand)
        tb.addSeparator()

        # Undo / Redo are dual-purpose: inside Refine/Consolidate they drain
        # the edit-history stack first; everywhere else (and once history is
        # empty) they fall back to ordinary step navigation — see
        # _on_toolbar_back/_on_toolbar_forward and _refresh_nav's dynamic
        # tooltip. Always labelled Undo/Redo since step navigation is also
        # freely reachable from the sidebar.
        self._act_back = QAction(icons.makeIcon("undo"), "Undo", self)
        self._act_back.setToolTip("Go to the previous step")
        self._act_back.setShortcut(QKeySequence.StandardKey.Undo)   # Ctrl+Z
        self._act_back.triggered.connect(self._on_toolbar_back)
        tb.addAction(self._act_back)

        self._act_forward = QAction(icons.makeIcon("redo"), "Redo", self)
        self._act_forward.setToolTip("Go to the next suggested step")
        self._act_forward.setShortcut(QKeySequence.StandardKey.Redo)  # Ctrl+Y
        self._act_forward.triggered.connect(self._on_toolbar_forward)
        tb.addAction(self._act_forward)

        self._step_label = QLabel("")
        self._step_label.setStyleSheet("font-weight: bold; padding: 0 10px;")
        self._step_label.setToolTip("The wizard step currently shown.")
        tb.addWidget(self._step_label)

        tb.addSeparator()
        tb.addAction(self._act_file_new)
        tb.addAction(self._act_file_open)
        tb.addAction(self._act_file_save)
        # These three are self-explanatory even icon-only — saves width for
        # the domain-specific actions that need their text label.
        for _act in (self._act_file_new, self._act_file_open, self._act_file_save):
            _btn = tb.widgetForAction(_act)
            if _btn is not None:
                _btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        tb.addSeparator()
        self._act_show_alignment = QAction(
            icons.makeIcon("showAlignment"), "Show alignment", self)
        self._act_show_alignment.setToolTip(
            "Re-draw the edited alignment and PI overlay on the map.")
        self._act_show_alignment.triggered.connect(
            lambda: self.element_table.show_alignment_requested.emit())
        tb.addAction(self._act_show_alignment)

        self._act_merge_selection = QAction(
            icons.makeIcon("merge"), "Merge selection", self)
        self._act_merge_selection.setToolTip(
            "Merge PI range → single curve (select rows spanning\n"
            "two or more curves in the element table first).")
        self._act_merge_selection.triggered.connect(
            lambda: self.element_table._on_merge_range())
        tb.addAction(self._act_merge_selection)

        self._act_undo_merge = QAction(
            icons.makeIcon("undo"), "Undo merge", self)
        self._act_undo_merge.setToolTip(
            "Undo the merge for the selected curve (select its row first).")
        self._act_undo_merge.triggered.connect(
            lambda: self.element_table.undo_selected_merge())
        tb.addAction(self._act_undo_merge)

        self._act_restore_default = QAction(
            icons.makeIcon("resetLayout"), "Restore default", self)
        self._act_restore_default.setToolTip(
            "Discard every edit made in Refine/Consolidate and return to\n"
            "the freshly-fitted alignment for the selected candidate.")
        self._act_restore_default.triggered.connect(self._on_restore_default)
        tb.addAction(self._act_restore_default)

        tb.addSeparator()
        self._act_edit_mode = QAction(icons.makeIcon("edit"), "Edit mode", self)
        self._act_edit_mode.setCheckable(True)
        self._act_edit_mode.setProperty("seriesToggle", True)
        self._act_edit_mode.setToolTip(
            "Default mode keeps the tangent polygon locked (radius, spiral,\n"
            "merge, omit, split and delete all still work). Edit mode also\n"
            "lets you drag PI markers on the map to reshape the alignment.")
        self._act_edit_mode.toggled.connect(self._on_edit_mode_toggled)
        tb.addAction(self._act_edit_mode)

        tb.addSeparator()
        self._build_original_appearance_action(tb, style)

        # Map settings (Part J): owned/wired by MapWidget itself, laid out
        # here instead of a separate row above the map.
        tb.addSeparator()
        map_label = QLabel("Map:")
        map_label.setToolTip("Base map, overlays and layer opacity.")
        tb.addWidget(map_label)
        self.map_widget._provider_combo.setToolTip("Base map tile provider.")
        tb.addWidget(self.map_widget._provider_combo)
        self.map_widget._rail_chk.setToolTip(
            "Overlay OSM railway lines on the base map.")
        tb.addWidget(self.map_widget._rail_chk)
        self.map_widget._cadastre_chk.setToolTip(
            "Overlay the ČÚZK cadastre (land-register) map layer.")
        tb.addWidget(self.map_widget._cadastre_chk)
        self.map_widget._cadastre_opacity_slider.setToolTip(
            "Cadastre layer opacity.")
        tb.addWidget(self.map_widget._cadastre_opacity_slider)

        # Req 9b: alignment colour mode — element type (default) or a
        # deviation-from-baseline heatmap.
        self._color_mode_combo = QComboBox()
        self._color_mode_combo.addItem("Colour: type", "type")
        self._color_mode_combo.addItem("Colour: deviation", "deviation")
        self._color_mode_combo.setToolTip(
            "How the alignment is coloured on the map.\n"
            "Type: Line / Arc / Spiral, as usual.\n"
            "Deviation: green -> yellow -> red heatmap against the "
            "project's max-deviation tolerance — quickly spot the worst-\n"
            "fitting sections of a long alignment."
        )
        self._color_mode_combo.currentIndexChanged.connect(
            self._on_color_mode_changed)
        tb.addWidget(self._color_mode_combo)

        tb.addSeparator()
        self._act_coypu_repo = QAction(
            icons.makeIcon("externalLink"), "Explore COYPU", self)
        self._act_coypu_repo.setToolTip(
            "COYPU is a separate railway design application this Feeder "
            "exports for — open its GitHub repository.")
        self._act_coypu_repo.triggered.connect(self._open_coypu_repo)
        tb.addAction(self._act_coypu_repo)

        self._mount_toolbar_scroll(tb)

        # Back routing: each step's OWN back handler (some do overlay
        # cleanup) keyed by the step it goes back FROM — mirrors exactly
        # what that step's in-page "← Back" button does.
        self._toolbar_back_map = {
            S_SELECT:      lambda: self._goto_step(S_FIND),
            S_CONFIG:      lambda: self._goto_step(S_SELECT),
            S_CANDIDATES:  self._on_candidates_back,
            S_DESIGN:      self._on_refine_back,
            S_STATIONS:    lambda: self._goto_step(S_DESIGN),
            S_CROSSSEC:    lambda: self._goto_step(S_STATIONS),
            S_VERTICAL:    lambda: self._goto_step(S_CROSSSEC),
            S_EXPORT:      lambda: self._goto_step(S_VERTICAL),
        }

    def _mount_toolbar_scroll(self, tb: QToolBar):
        """Host `tb` in a horizontally-scrollable row instead of
        QMainWindow's native toolbar area (see _build_layout). A QScrollArea
        with `setWidgetResizable(False)` never shrinks its child below its
        natural size, so the toolbar's own internal overflow-chevron logic
        (which only kicks in when it's GIVEN less width than it wants) never
        triggers — the overflow becomes scrollable instead of hidden behind
        a bare "»" button. Small `<`/`>` buttons flank the scroll area,
        shown only while there's something to scroll to."""
        row = QWidget()
        row.setObjectName("MainToolbarRow")
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(0)

        left_arrow = QToolButton()
        left_arrow.setObjectName("ToolbarScrollArrow")
        left_arrow.setArrowType(Qt.ArrowType.LeftArrow)
        left_arrow.setAutoRepeat(True)
        left_arrow.setFixedWidth(16)
        left_arrow.setVisible(False)

        right_arrow = QToolButton()
        right_arrow.setObjectName("ToolbarScrollArrow")
        right_arrow.setArrowType(Qt.ArrowType.RightArrow)
        right_arrow.setAutoRepeat(True)
        right_arrow.setFixedWidth(16)
        right_arrow.setVisible(False)

        scroll = _ToolbarScrollArea(self)
        scroll.setObjectName("MainToolbarScroll")
        scroll.setWidget(tb)
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(tb.sizeHint().height())

        bar = scroll.horizontalScrollBar()
        step = 120

        def _update_arrows(*_):
            overflow = bar.maximum() > 0
            left_arrow.setVisible(overflow)
            right_arrow.setVisible(overflow)

        left_arrow.clicked.connect(lambda: bar.setValue(bar.value() - step))
        right_arrow.clicked.connect(lambda: bar.setValue(bar.value() + step))
        bar.rangeChanged.connect(_update_arrows)

        row_l.addWidget(left_arrow)
        row_l.addWidget(scroll, 1)
        row_l.addWidget(right_arrow)

        self._toolbar_scroll = scroll
        self._toolbar_row = row
        self._central_layout.insertWidget(0, row)

    def _finish_menu_toolbar_wiring(self):
        """
        A handful of menu entries mirror toolbar actions or control the
        toolbar itself, so they can only be wired up once `_build_toolbar`
        has run (it depends on `_build_menu`'s File actions, so it always
        runs second — this is the follow-up third step).
        """
        mb = self.menuBar()

        # "&Edit" — mirrors the toolbar's domain-specific actions using the
        # SAME QAction objects, so enabled/checked state stays in sync
        # automatically wherever they appear. Inserted before Settings
        # (rather than appended after Help via addMenu) for a conventional
        # File/View/Edit/Settings/Help ordering.
        from PySide6.QtWidgets import QMenu
        edit_menu = QMenu("&Edit", self)
        mb.insertMenu(self._settings_menu.menuAction(), edit_menu)
        for act in (self._act_show_alignment, self._act_merge_selection,
                   self._act_undo_merge, self._act_restore_default,
                   self._act_edit_mode, self._act_original_appearance):
            edit_menu.addAction(act)

        # "Show toolbar" — the toolbar is now a plain widget row (Part C's
        # scrollable host, not QMainWindow's native toolbar area), so there's
        # no ready-made toggleViewAction(); mirror the exact same
        # checkable-action + _prefs["show_toolbar"] pattern by hand instead.
        self._act_show_toolbar = QAction("Show toolbar", self, checkable=True)
        self._act_show_toolbar.setChecked(self._prefs["show_toolbar"])
        self._toolbar_row.setVisible(self._prefs["show_toolbar"])
        self._act_show_toolbar.toggled.connect(self._on_toggle_toolbar)
        self._view_menu.addAction(self._act_show_toolbar)

    def _on_toggle_toolbar(self, on: bool):
        self._prefs["show_toolbar"] = bool(on)
        self._toolbar_row.setVisible(on)
        self._save_prefs()

    def _build_original_appearance_action(self, tb, style):
        """
        Part E: a small popover (visible / opacity / colour) controlling the
        original (OSM reference) alignment overlay. Applied to the map via
        `set_osm_reference_style` (restyles the current layers, no redraw)
        and persisted in `_prefs` like the theme settings.
        """
        from PySide6.QtWidgets import (
            QMenu, QWidgetAction, QCheckBox, QSlider, QPushButton, QLabel,
        )
        from PySide6.QtGui import QColor
        from gui.palette import contrast_text

        act = QAction(icons.makeIcon("layers"), "Original…", self)
        act.setToolTip(
            "Visibility, opacity and colour of the original OSM alignment\n"
            "overlay (the dashed reference line).")

        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        chk = QCheckBox("Show original alignment")
        chk.setChecked(bool(self._prefs["osm_ref_visible"]))
        v.addWidget(chk)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Opacity:"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(5, 100)
        slider.setValue(int(round(float(self._prefs["osm_ref_opacity"]) * 100)))
        slider.setFixedWidth(120)
        op_row.addWidget(slider)
        v.addLayout(op_row)

        color_btn = QPushButton("Colour…")
        color_btn.setFixedHeight(24)

        def _swatch(hex_color: str):
            color_btn.setStyleSheet(
                f"background-color: {hex_color};"
                f" color: {contrast_text(hex_color)};")

        _swatch(self._prefs["osm_ref_color"])
        v.addWidget(color_btn)

        def apply_style():
            self._prefs["osm_ref_visible"] = chk.isChecked()
            self._prefs["osm_ref_opacity"] = slider.value() / 100.0
            self._save_prefs_debounced()
            self.map_widget.set_osm_reference_style(
                self._prefs["osm_ref_visible"], self._prefs["osm_ref_color"],
                self._prefs["osm_ref_opacity"])

        def pick_color():
            from PySide6.QtWidgets import QColorDialog
            c = QColorDialog.getColor(
                QColor(self._prefs["osm_ref_color"]), self,
                "Original alignment colour")
            if c.isValid():
                self._prefs["osm_ref_color"] = c.name()
                _swatch(c.name())
                apply_style()

        chk.toggled.connect(apply_style)
        slider.valueChanged.connect(apply_style)
        color_btn.clicked.connect(pick_color)

        menu = QMenu(self)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(panel)
        menu.addAction(wa)
        act.setMenu(menu)
        tb.addAction(act)
        btn = tb.widgetForAction(act)
        if btn is not None:
            btn.setPopupMode(btn.ToolButtonPopupMode.InstantPopup)
        self._act_original_appearance = act

    def _history_push(self, label: str):
        """Called by the element table just before it mutates the PI model —
        snapshots the pre-edit state for the toolbar's Back/Forward undo."""
        self._edit_history.push(self._pi_model, label)

    def _make_baseline(self, model):
        if model is None:
            return None
        from geometry.candidates import _light_copy
        return _light_copy(model)

    def _on_restore_default(self):
        if self._pi_model is None or self._refine_baseline is None:
            return
        reply = QMessageBox.question(
            self, "Restore default",
            "Discard every edit made in Refine/Consolidate and return to "
            "the alignment as it was when this candidate was selected (or "
            "the project was opened)?\n\nThis clears the undo/redo history too.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        from gui.edit_history import apply_model_snapshot
        apply_model_snapshot(self._pi_model, self._refine_baseline)
        self._edit_history.clear()
        self._refine_baseline = self._make_baseline(self._pi_model)
        self._on_consolidation_model_changed()
        self.log_panel.log("Alignment restored to its default (unedited) state.", "ok")

    def _on_edit_mode_toggled(self, on: bool):
        if on:
            self._deactivate_all_tools(keep="edit")
        self.map_widget.set_pi_edit_mode(on)
        if not on:
            # Defensive: leaving edit mode mid-drag (shouldn't normally
            # happen — the marker holds the mouse — but never leave a
            # dangling history push a dragend will never arrive to close).
            self._drag_history_pushed = False
            self._pending_drag = None
        self.log_panel.log(
            "Edit mode ON — drag PI markers on the map to move them."
            if on else "Edit mode off — PI markers are locked again.",
            "info")

    def _on_pi_dragged(self, pi_index: int, lat: float, lon: float):
        """A PI marker was dragged to (lat, lon) — the drag-END position.

        If live-drag preview ran during the gesture, `_on_pi_drag_start`
        already pushed the ONE history entry for this whole drag and
        `_apply_pending_drag` already moved the model repeatedly (no
        further history pushes) — this call just finalizes with the exact
        release position and performs the full refresh (element table,
        metrics chip, staleness) those cheap live ticks deliberately skip.
        """
        from geometry.candidates import move_pi
        from geometry.projection import wgs84_to_projected
        if self._pi_model is None:
            return
        # A pending preview tick for THIS drag may still be queued — apply
        # it first so it can't fire after we've already finalized below.
        if self._pending_drag is not None and self._pending_drag[0] == pi_index:
            self._pending_drag = None
        live_preview_ran = self._drag_history_pushed
        self._drag_history_pushed = False
        if not live_preview_ran:
            self._history_push(f"move PI {pi_index}")
        xy = wgs84_to_projected([(lat, lon)], self._work_epsg)[0]
        ok, msg = move_pi(self._pi_model, pi_index, xy)
        if not ok:
            if not live_preview_ran:
                self._edit_history.discard_last_push()
            self.log_panel.log(f"Move PI {pi_index}: {msg}", "warn")
            if live_preview_ran:
                # The model still reflects the last successful live-preview
                # tick (a real edit) — only the exact release point failed,
                # so the table/chip still need to catch up to what the map
                # already shows.
                self._on_consolidation_model_changed(moved_pi=pi_index)
            return
        self._on_consolidation_model_changed(moved_pi=pi_index)
        self.log_panel.log(msg, "ok")

    def _on_pi_drag_start(self, pi_index: int):
        """First event of a drag gesture (live preview only) — push the
        ONE history entry that covers the whole gesture."""
        if self._pi_model is None:
            return
        self._history_push(f"move PI {pi_index}")
        self._drag_history_pushed = True

    def _on_pi_drag_preview(self, pi_index: int, lat: float, lon: float):
        """A live, throttled (~20/s) position update while dragging.

        Coalesced through a zero-interval timer so a burst of ticks that
        arrive before Qt processes the last one collapses to the latest
        position only — never a backlog."""
        if self._pi_model is None or not self._drag_history_pushed:
            return   # no matching dragstart (pref toggled mid-drag) — ignore
        self._pending_drag = (pi_index, lat, lon)
        if self._drag_preview_timer is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(0)
            t.timeout.connect(self._apply_pending_drag)
            self._drag_preview_timer = t
        if not self._drag_preview_timer.isActive():
            self._drag_preview_timer.start()

    def _apply_pending_drag(self):
        """Fires the latest coalesced live-preview tick: move the model and
        refresh only the map + PI overlay — deliberately skipping the
        element table (full Qt rebuild), metrics chip and staleness, which
        `_on_pi_dragged` catches up on once at the end of the gesture."""
        if self._pending_drag is None or self._pi_model is None:
            return
        pi_index, lat, lon = self._pending_drag
        self._pending_drag = None
        from geometry.candidates import move_pi
        from geometry.projection import wgs84_to_projected
        xy = wgs84_to_projected([(lat, lon)], self._work_epsg)[0]
        ok, _msg = move_pi(self._pi_model, pi_index, xy)
        if not ok:
            return   # e.g. dragged past a neighbour mid-gesture — dragend reports it properly
        self._elements = list(self._pi_model.elements)
        self._render_elements_on_map(self._elements, splice=self._pi_model.last_splice)
        self._show_pi_overlay(self._pi_model, moved_only=pi_index)

    def _on_radius_spiral_preview(self, elements: list, splice: dict | None):
        """A live radius/spiral-length preview tick (element_table's own
        coalesced timer — see ElementTableDock._apply_preview_tick).
        Map-only refresh, same deliberate omissions as _apply_pending_drag:
        no element-table rebuild, metrics chip or staleness marking — the
        debounced full commit (element_table._apply_pending) catches up on
        those once the user stops. No PI-overlay refresh either: unlike a
        drag, a radius/spiral edit never moves a PI vertex."""
        self._elements = list(elements)
        self._render_elements_on_map(self._elements, splice=splice)

    def _on_pi_restore(self, pi_index: int):
        """The map's restore badge (Part H) asked to snap PI `pi_index`
        back to its position in `_refine_baseline`."""
        from geometry.candidates import move_pi
        if self._pi_model is None or self._refine_baseline is None:
            return
        # Restoring a single PI's position only makes sense if no merge/
        # insert/delete has changed the vertex topology since the baseline
        # was stashed — those renumber indices, and a drag/restore never
        # does, so a length mismatch means index k no longer refers to the
        # same PI in both models.
        if len(self._pi_model.V) != len(self._refine_baseline.V):
            self.log_panel.log(
                "Restore is only available when the PI layout hasn't "
                "changed since fitting (no merges/inserts/deletes) — use "
                "Restore default to revert everything instead.", "warn")
            return
        target_xy = self._refine_baseline.V[pi_index]
        self._history_push(f"restore PI {pi_index}")
        ok, msg = move_pi(self._pi_model, pi_index, target_xy)
        if not ok:
            self._edit_history.discard_last_push()
            self.log_panel.log(f"Restore PI {pi_index}: {msg}", "warn")
            return
        self._on_consolidation_model_changed(moved_pi=pi_index)
        self.log_panel.log(f"PI {pi_index} restored to its fitted position.", "ok")

    def _on_toolbar_back(self):
        """In the Refine & Consolidate step, Back drains the undo stack
        first (like a normal program's Ctrl+Z) before falling back to step
        navigation — so it only leaves the step once there's nothing left
        to undo."""
        if (self._current_step == S_DESIGN
                and self._edit_history.can_undo()):
            label = self._edit_history.undo(self._pi_model)
            self._after_history_change("Undid", label)
            return
        handler = self._toolbar_back_map.get(self._current_step)
        if handler:
            handler()

    def _on_toolbar_forward(self):
        if (self._current_step == S_DESIGN
                and self._edit_history.can_redo()):
            label = self._edit_history.redo(self._pi_model)
            self._after_history_change("Redid", label)
            return
        if self._suggested_step is not None:
            self._goto_step(self._suggested_step)

    def _after_history_change(self, verb: str, label: str | None):
        """Undo/redo restores the PI model's V/idx/pis/elements/stats
        in place — refresh exactly like a Consolidate-driven model change
        (same shared-model rebind, no rebuild needed)."""
        self._on_consolidation_model_changed()
        msg = f"{verb}: {label}." if label else f"{verb} last edit."
        self.log_panel.log(msg, "info")

    def _refresh_toolbar_edit_actions(self):
        et = self.element_table
        in_edit_step = self._current_step == S_DESIGN
        self._act_show_alignment.setEnabled(
            in_edit_step and self._pi_model is not None)
        self._act_merge_selection.setEnabled(
            in_edit_step and et._selected_pi_range() is not None)
        self._act_undo_merge.setEnabled(
            in_edit_step and et.selected_merge_pi() is not None)
        self._act_edit_mode.setEnabled(
            in_edit_step and self._pi_model is not None)
        self._act_restore_default.setEnabled(
            in_edit_step and self._pi_model is not None
            and self._refine_baseline is not None)

    def _on_toggle_log(self, on: bool):
        self._prefs["show_log"] = bool(on)
        self.log_panel.setVisible(on)
        self._save_prefs()

    def _on_toggle_metrics_chip(self, on: bool):
        self._prefs["show_metrics_chip"] = bool(on)
        self._save_prefs()
        self._update_metrics_chip()

    def _on_toggle_map_legend(self, on: bool):
        self._prefs["show_map_legend"] = bool(on)
        self._save_prefs()
        self.map_widget.set_legend_visible(on)

    def _on_color_mode_changed(self, _index: int):
        from gui.palette import LEGEND_ENTRIES, DEVIATION_LEGEND_ENTRIES
        mode = self._color_mode_combo.currentData() or "type"
        self._prefs["map_color_mode"] = mode
        self._save_prefs()
        self.map_widget.set_legend_entries(
            DEVIATION_LEGEND_ENTRIES if mode == "deviation" else LEGEND_ENTRIES)
        if self._elements:
            self._render_elements_on_map(self._elements, color_mode=mode)

    def _on_toggle_live_drag_preview(self, on: bool):
        self._prefs["live_drag_preview"] = bool(on)
        self._save_prefs()
        self.map_widget.set_live_drag_preview(on)
        self.element_table.set_live_preview(on)

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
        import basemap_key
        dlg = SettingsDialog(self._prefs, self,
                             basemap_api_key=self.map_widget.carto_api_key,
                             basemap_key_source=basemap_key.keySource())
        if dlg.exec():
            self._prefs.update(dlg.values())
            self._save_prefs()
            self._apply_prefs()
            self._store_carto_api_key(dlg.basemap_api_key())
            # keep the menu toggles in sync
            self._act_show_log.setChecked(self._prefs["show_log"])
            for a in self._theme_group.actions():
                a.setChecked(a.data() == self._prefs["theme_mode"])
            self.log_panel.log("Preferences updated.", "info")

    def _store_carto_api_key(self, api_key: str):
        """Vault the CARTO key (ported from COYPU's storeCartoApiKey).

        An unchanged key is never re-written, so a key that came from COYPU's
        entry or the environment is not copied into the Feeder's own entry.
        An empty field removes the Feeder's entry. The map then follows
        whatever `loadKey()` resolves (COYPU's key may still apply). Never
        logs the key itself.
        """
        import basemap_key
        if api_key == self.map_widget.carto_api_key:
            return
        stored = basemap_key.saveKey(api_key) if api_key else basemap_key.deleteKey()
        if not stored:
            self.log_panel.log(
                "The CARTO key could not be saved securely (credential vault "
                "unavailable) — it applies to this session only.", "warn")
            effective = api_key
        else:
            effective = basemap_key.loadKey() if not api_key else api_key
        self.map_widget.set_carto_api_key(effective)
        if api_key:
            self.log_panel.log("CARTO API key saved.", "info")
        elif effective:
            self.log_panel.log("CARTO API key removed — the key saved by "
                               "COYPU (or the environment) still applies.", "info")
        else:
            self.log_panel.log("CARTO API key removed — CARTO basemaps disabled.",
                               "info")

    def _open_about(self):
        AboutDialog(self).exec()

    def _open_readme(self):
        AboutDialog(self)._open_readme()

    def _open_releases(self):
        import webbrowser
        import app_meta as meta
        webbrowser.open(meta.RELEASES_URL)

    def _open_coypu_repo(self):
        import webbrowser
        import app_meta as meta
        webbrowser.open(meta.COYPU_REPO_URL)

    # ------------------------------------------------------------------
    # Project files (.coypuf)
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
            "profile":      self._profile,
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
            "COYPU Feeder project (*.coypuf);;All files (*.*)")
        if path:
            self._load_project(path)

    def _on_save_project(self) -> bool:
        if not self._project_path:
            return self._on_save_project_as()
        return self._save_project(self._project_path)

    def _on_save_project_as(self) -> bool:
        from PySide6.QtWidgets import QFileDialog
        suggested = self._settings.get("project_name", "project") + ".coypuf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save COYPU Feeder project", suggested,
            "COYPU Feeder project (*.coypuf)")
        if not path:
            return False
        if not path.lower().endswith(".coypuf"):
            path += ".coypuf"
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
        self._profile         = st.get("profile")
        self._project_path    = path
        if st.get("version_warning"):
            self.log_panel.log(f"⚠ {st['version_warning']}", "warn")
        self._refine_baseline = self._make_baseline(self._pi_model)

        # Repopulate the steps that own UI state
        self.step2.populate(self._tracks)
        self.map_widget.show_tracks(self._tracks)
        if not self._elements and self._tracks:
            # No alignment to draw at all — the map shows only the raw OSM
            # track(s) (non-interactive, no per-element click/hover), which
            # reads exactly like "everything is a plain red line and nothing
            # is clickable" if this goes unnoticed. Surface it loudly rather
            # than silently falling back to that.
            self.log_panel.log(
                "⚠ This project has no alignment elements to display "
                "(no PI model in the file, or it rebuilt to nothing) — only "
                "the raw OSM track is shown, and it isn't interactive. Re-run "
                "Candidates for this section to rebuild the alignment.", "warn")
        if self._elements:
            metrics = metrics_from_stats(
                getattr(self._pi_model, "last_stats", {}) or {}, self._elements)
            self.element_table.prepare(
                self._pi_model, self._elements,
                check_interval=self._settings.get("check_interval", 5.0))
            self.step5_design.bind(self._pi_model)
            self.step5_design.set_elements(self._elements, metrics)
            self._render_elements_on_map(self._elements, fit_view=True)
            self._show_pi_overlay(self._pi_model)
            if self._pi_model is None:
                # Level 1 (raw) — no PI model to read live stats from; the
                # metrics chip needs a one-off pass against the OSM baseline.
                from geometry.candidates import evaluate_candidate
                xy        = self._xy_list[0]        if self._xy_list        else None
                chainages = self._chainages_list[0] if self._chainages_list else None
                self._level1_metrics = evaluate_candidate(
                    self._elements, xy, chainages,
                    self._settings.get("check_interval", 5.0))
            self._update_metrics_chip()
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
        step = st.get("step", S_DESIGN)
        if step not in self._step_reasons():
            self._goto_step(step)
        else:
            self._goto_step(S_DESIGN if self._elements else S_FIND)

    def _build_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # _build_toolbar (runs after this) inserts the toolbar's scrollable
        # row at index 0 — kept as a real full-width row in the central
        # widget's own layout, not QMainWindow's native toolbar area, so it
        # can be given a QScrollArea instead of being clipped behind Qt's
        # native "»" overflow chevron when actions don't fit the window.
        self._central_layout = outer

        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        outer.addWidget(body, 1)

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
        # The profile canvas shares the bottom row with the element
        # table — only one of the two is ever visible (see _goto_step),
        # so a longitudinal profile gets the full width it needs.
        self.profile_canvas = ProfileCanvas()
        self.profile_canvas.setVisible(False)
        self.profile_canvas.setMinimumWidth(460)

        self._bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._bottom_splitter.addWidget(self.log_panel)
        self._bottom_splitter.addWidget(self.element_table)
        self._bottom_splitter.addWidget(self.profile_canvas)
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
        self.step3              = Step3Configure(defaults=self._prefs)
        self.step4_candidates   = Step4Candidates()
        self.step5_design       = Step5Design()          # step 5 in the UI
        self.step6_stations     = Step6Stations()        # step 6 in the UI
        self.step6_crosssec     = Step6CrossSection()    # step 7 in the UI
        self.step8_vertical     = Step8Vertical()        # step 8 in the UI
        self.step7_export       = Step7Export()          # step 9 in the UI

        self.stack.addWidget(self.step1)              # S_FIND
        self.stack.addWidget(self.step2)              # S_SELECT
        self.stack.addWidget(self.step3)              # S_CONFIG
        self.stack.addWidget(self.step4_candidates)   # S_CANDIDATES
        self.stack.addWidget(self.step5_design)       # S_DESIGN
        self.stack.addWidget(self.step6_stations)     # S_STATIONS
        self.stack.addWidget(self.step6_crosssec)     # S_CROSSSEC
        self.stack.addWidget(self.step8_vertical)     # S_VERTICAL
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

        # Step 2 → highlight / fit / confirm / back / track editing
        self.step2.highlight_changed.connect(self._on_highlight_changed)
        self.step2.fit_to_tracks_requested.connect(self._on_fit_to_tracks)
        self.step2.section_confirmed.connect(self._on_section_confirmed)
        self.step2.back_requested.connect(lambda: self._goto_step(S_FIND))
        self.step2.log_message.connect(self.log_panel.log)
        self.step2.tracks_changed.connect(self._on_step2_tracks_changed)
        self.step2.split_pick_mode.connect(self._on_track_split_pick_mode)

        # Step 3 → config confirmed / back
        self.step3.config_confirmed.connect(self._on_config_confirmed)
        self.step3.back_requested.connect(lambda: self._goto_step(S_SELECT))

        # Step 4 (candidates) → map update + selection + hover emphasis + back
        self.step4_candidates.candidate_map_update.connect(self._on_candidate_map_update)
        self.step4_candidates.candidate_selected.connect(self._on_candidate_selected)
        self.step4_candidates.candidate_hovered.connect(
            self.map_widget.emphasize_candidate)
        self.step4_candidates.back_requested.connect(self._on_candidates_back)

        # Step 5 (Refine & Consolidate) → done / back / model edits / highlight
        self.step5_design.design_done.connect(self._on_design_done)
        self.step5_design.back_requested.connect(self._on_refine_back)
        self.step5_design.model_changed.connect(
            self._on_consolidation_model_changed)
        self.step5_design.log_message.connect(self.log_panel.log)
        self.step5_design.highlight_span.connect(
            self.map_widget.highlight_elements)
        self.step5_design.on_before_edit = self._history_push
        self.step5_design.on_edit_failed = self._edit_history.discard_last_push

        # Element table (bottom dock) → rebuilds + row selection + log
        self.element_table.rebuilt.connect(self._on_elements_rebuilt)
        self.element_table.preview_changed.connect(self._on_radius_spiral_preview)
        self.element_table.elements_selected.connect(self._on_element_rows_selected)
        self.element_table.log_message.connect(self.log_panel.log)
        self.element_table.show_alignment_requested.connect(
            self._on_show_alignment_clicked)
        self.element_table.sync_parameters_requested.connect(
            self._on_sync_parameters)
        self.element_table.split_pick_mode.connect(self._on_split_pick_mode)
        self.element_table.on_before_edit = self._history_push
        self.element_table.on_edit_failed = self._edit_history.discard_last_push
        self.map_widget.split_point_clicked.connect(self._on_split_point_clicked)
        self.map_widget.pi_dragged.connect(self._on_pi_dragged)
        self.map_widget.pi_restore_requested.connect(self._on_pi_restore)
        self.map_widget.pi_drag_start.connect(self._on_pi_drag_start)
        self.map_widget.pi_drag_preview.connect(self._on_pi_drag_preview)
        # Map element click (Ctrl = toggle into multiselect) → table selection
        self.map_widget.element_clicked.connect(self.element_table.select_element)
        # Metrics chip click (Req 9a) → highlight + pan to the worst element,
        # same round trip a table-row click already does.
        self._metrics_chip.metric_clicked.connect(self._on_metric_clicked)

        # Step 6 (stations) → map markers / click mode / navigation
        self.step6_stations.stations_changed.connect(self._on_stations_changed)
        self.step6_stations.map_click_mode.connect(self._on_station_pick_mode)
        self.step6_stations.stations_done.connect(self._on_stations_done)
        self.step6_stations.back_requested.connect(lambda: self._goto_step(S_DESIGN))
        self.map_widget.map_clicked.connect(self.step6_stations.on_map_clicked)

        self.step6_crosssec.back_requested.connect(lambda: self._goto_step(S_STATIONS))
        self.step6_crosssec.analysis_done.connect(self._on_analysis_done)
        self.step6_crosssec.cross_section_ready.connect(self._on_cross_section_ready)

        # Step 7 (export) → back
        self.step7_export.back_requested.connect(lambda: self._goto_step(S_VERTICAL))

        # Step 8 (Vertical Profile) → confirm / back / edits / pick mode
        self.step8_vertical.profile_confirmed.connect(self._on_profile_confirmed)
        self.step8_vertical.back_requested.connect(
            lambda: self._goto_step(S_CROSSSEC))
        self.step8_vertical.profile_changed.connect(self._on_profile_changed)
        self.step8_vertical.log_message.connect(self.log_panel.log)

        # Step 7 (export) → alignment display / fit / export / restart
        self.step7_export.osm_track_ready.connect(self._on_osm_track_ready)
        self.step7_export.alignment_ready.connect(self._on_alignment_ready)
        self.step7_export.alignment_segments_ready.connect(self._on_alignment_segments_ready)
        self.step7_export.log_message.connect(self.log_panel.log)
        self.step7_export.fit_to_alignment_requested.connect(self._on_fit_to_alignment)
        self.step7_export.export_finished.connect(self._on_export_finished)
        self.step7_export.start_over_requested.connect(self._start_over)

    # ------------------------------------------------------------------
    # System colour-scheme changes (dark ↔ light)
    # ------------------------------------------------------------------

    def _connect_scheme_changes(self):
        """Restyle whenever the theme changes, whoever triggered it.

        ThemeManager already listens to the OS colour-scheme signal and
        re-applies itself while the mode is "auto", so this only has to
        react to the result rather than duplicate the detection.
        """
        from gui.theme import manager
        manager().themeChanged.connect(self._on_theme_changed)

    def _on_theme_changed(self, mode: str = ""):
        """Fan the new tokens out to every widget that owns local styling."""
        from gui.theme import current_tokens, is_dark_active
        tokens = current_tokens()
        dark   = is_dark_active()

        # Cached QIcons hold pixmaps painted in the previous foreground
        # colour, so clear the factory first, then re-fetch every icon.
        icons.iconFactory.applyTheme(tokens)
        self._retint_action_icons()

        brand = getattr(self, "_brand", None)
        if brand is not None:
            brand.setStyleSheet(
                f"color:{tokens['highlight']}; font-weight:600;"
                " font-size:13px; padding:0 10px;")

        for w in (getattr(self, "sidebar", None), self.element_table,
                  getattr(self, "profile_canvas", None),
                  self.log_panel, self.map_widget):
            fn = getattr(w, "applyTheme", None)
            if fn is not None:
                fn(dark, tokens)
        for i in range(self.stack.count()):
            fn = getattr(self.stack.widget(i), "applyTheme", None)
            if fn is not None:
                fn(dark, tokens)

    def _retint_action_icons(self):
        """Re-fetch every QAction icon from the refreshed factory."""
        for attr, name in (
            ("_act_back",            "undo"),
            ("_act_forward",         "redo"),
            ("_act_file_new",        "newProject"),
            ("_act_file_open",       "open"),
            ("_act_file_save",       "save"),
            ("_act_show_alignment",  "showAlignment"),
            ("_act_merge_selection", "merge"),
            ("_act_undo_merge",      "undo"),
            ("_act_restore_default", "resetLayout"),
            ("_act_edit_mode",       "edit"),
            ("_act_coypu_repo",      "externalLink"),
        ):
            act = getattr(self, attr, None)
            if act is not None:
                act.setIcon(icons.makeIcon(name))

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
        S_DESIGN: ("Step 5 — Refine & Consolidate", [
            "Edit Radius / Spiral L in the table; the map updates live.",
            "Click elements on the map to select rows; Ctrl+click multi-selects.",
            "Select first & last tangent → 'Merge PI range → single curve'.",
            "Short straights between curves offer 'Merge spirals'.",
            "'Consolidate curve runs' replaces runs of same-direction curves",
            "joined by short straights with one transition–circular–transition",
            "curve — most runs are already auto-merged during fitting.",
            "🗺 'Show alignment' re-draws everything after a map reload.",
        ]),
        S_STATIONS: ("Step 6 — Stations", [
            "⚡ Auto-detect stations from OSM, 📍 place on map, or add rows.",
            "The CSV (Station,Dwell Time,Name) is written next to the LandXML.",
        ]),
        S_CROSSSEC: ("Step 7 — Cross-Section", [
            "Optional terrain cross-slope check at perpendicular offsets.",
        ]),
        S_VERTICAL: ("Step 8 — Vertical Profile", [
            "'Sample terrain' queries the DEM along the fitted centreline.",
            "Pick a smoothing algorithm; the profile redraws as you tune it.",
            "Max gradient is a hard limit — steeper terrain becomes cut/fill.",
            "Add tunnel/bridge ranges to connect two points in a straight line.",
            "Drag to pan the profile, wheel to zoom, double-click to reset.",
        ]),
        S_EXPORT: ("Step 9 — Export", [
            "Pick the output CRS and file; 👁 Preview shows the LandXML first.",
        ]),
    }

    # Steps that consume the alignment and must be refreshed after an edit
    _DOWNSTREAM = (S_STATIONS, S_CROSSSEC, S_VERTICAL, S_EXPORT)

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
            for i in (S_DESIGN,) + self._DOWNSTREAM:
                r[i] = "Select a candidate level first (step 4)."
        # Level 1 (raw OSM polyline) has no PI model — the step stays
        # available (read-only refine metrics), but Step5Design's own
        # Consolidate section self-disables (see prepare()/bind()).
        return r

    def _refresh_nav(self):
        reasons = self._step_reasons()
        available = {i for i in range(N_STEPS) if i not in reasons}
        done = set(self._done_steps) & available
        suggested = None
        for i in range(self._current_step + 1, N_STEPS):
            if i in available:
                suggested = i
                break
        self.sidebar.set_states(available, done, suggested, reasons)
        self._suggested_step = suggested
        in_edit_step = self._current_step == S_DESIGN
        can_undo = in_edit_step and self._edit_history.can_undo()
        can_redo = in_edit_step and self._edit_history.can_redo()
        self._act_back.setEnabled(
            can_undo or self._current_step in self._toolbar_back_map)
        self._act_back.setToolTip(
            f"Undo: {self._edit_history.peek_undo_label()}" if can_undo
            else "Go to the previous step")
        self._act_forward.setEnabled(can_redo or suggested is not None)
        self._act_forward.setToolTip(
            f"Redo: {self._edit_history.peek_redo_label()}" if can_redo
            else "Go to the next suggested step")
        self._refresh_toolbar_edit_actions()

    def _goto_step(self, idx: int):
        changed_step = idx != self._current_step
        # Refresh a downstream step from the current alignment if it went stale
        if idx in self._DOWNSTREAM and idx in self._stale:
            self._refresh_downstream(idx)
        # A sidebar jump can reach the vertical step without passing
        # through the forward path; it cannot show anything unbound.
        elif idx == S_VERTICAL and self._profile is None and self._elements:
            self._refresh_downstream(idx)
        # Cross-section overlays are only meaningful from their own step on;
        # navigating back must not leave thousands of stale sample runs
        # taxing every pan/zoom repaint.
        if idx < S_CROSSSEC and self._current_step >= S_CROSSSEC:
            self.map_widget.clear_cross_section()
        self._current_step = idx
        self._done_steps.add(idx)
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_step(idx)
        # The table/stats matter more than which step is being viewed once
        # there's a real alignment to inspect — the wizard sidebar remains
        # free, advisory-only navigation (see CLAUDE.md).
        # The element table and the profile canvas share the bottom row.
        self.element_table.setVisible(
            self._pi_model is not None and idx != S_VERTICAL)
        self.profile_canvas.setVisible(idx == S_VERTICAL)
        if changed_step:
            self._deactivate_all_tools()   # also disarms map dragging/pick modes
            self._ensure_interactive_alignment_shown(idx)
            self._clear_osm_relict_tracks(idx)
        tip = self._STEP_TIPS.get(idx)
        if tip:
            self.log_panel.log_step(tip[0], tip[1])
            self._step_label.setText(tip[0])
        self._refresh_nav()

    # Steps whose map view is expected to show the interactive per-element
    # alignment (hover/click/highlight bindings) rather than tracks/
    # candidates/nothing.
    _ALIGNMENT_STEPS = (S_DESIGN, S_STATIONS, S_CROSSSEC, S_VERTICAL,
                        S_EXPORT)

    def _ensure_interactive_alignment_shown(self, idx: int):
        """Req 4, defense-in-depth: if navigation lands on a step that
        expects the interactive alignment view but the map is currently
        showing the non-interactive merged draw (or nothing at all) while
        `self._elements` says there SHOULD be something to show, re-render
        it. `MapWidget._js_aln_count` is 0 after the non-interactive
        `show_alignment`/`clear_alignment` path and None before anything is
        ever drawn; it's the element count after the interactive
        `show_alignment_segmented`/`update_alignment_segmented` path — so a
        falsy value here alongside real elements means "should be
        interactive, isn't", not just "nothing to show yet"."""
        if idx not in self._ALIGNMENT_STEPS or not self._elements:
            return
        if getattr(self.map_widget, "_js_aln_count", None):
            return
        self._render_elements_on_map(self._elements)
        self._show_pi_overlay(self._pi_model)

    def _clear_osm_relict_tracks(self, idx: int):
        """The raw OSM track layer (`tracksPane`, opaque `TRACK_COLORS[0]`
        red) is drawn once in Step 2 and nothing ever removed it on step
        advance — once the styled dashed OSM reference
        (`show_osm_reference`) takes over for editing, the track layer left
        underneath reads as an uncontrollable red relict that the "Original
        appearance" popover can't touch (it only governs the dashed
        reference). Once an alignment is actually being designed, the raw
        tracks have served their purpose (Step 2's own view) — clear them
        and make sure the properly-styled reference is the one showing."""
        if idx not in self._ALIGNMENT_STEPS:
            return
        self.map_widget.clear_tracks()
        if self._selected_tracks:
            osm_ref = [[[lat, lon] for lat, lon in t.nodes]
                       for t in self._selected_tracks]
            if any(len(r) > 0 for r in osm_ref):
                self.map_widget.show_osm_reference(osm_ref)

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
                                            self._selected_tracks, self._work_epsg,
                                            fallback_stops=self._osm_stop_nodes)
            elif idx == S_CROSSSEC:
                self.step6_crosssec.prepare(self._elements, self._work_epsg)
            elif idx == S_VERTICAL:
                self._prepare_vertical()
            elif idx == S_EXPORT:
                self._prepare_export()
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
        """Export finished — redraw the alignment.

        Req 4 fix: this used to unconditionally call `show_alignment(...)`
        — the merged, non-interactive draw (no hover/click/highlight
        bindings at all, `map.html`'s `showAlignment`) — and rely on the
        worker's OWN, separately-recomputed `alignment_segments_ready`
        signal to supersede it with the interactive per-element view. That
        signal only fires `if per_element_segments` (`worker.py`), built
        inside a bare `except Exception: pass` — any hiccup there left the
        map permanently non-interactive after export, silently, with no
        error anywhere. `self._elements` is the SAME element list the
        export just wrote to disk (App owns it — CLAUDE.md), so route
        through the one interactive render path every other step already
        uses instead of trusting a second, fragile recomputation.
        """
        if self._elements:
            self._render_elements_on_map(self._elements, fit_view=True)
            self.statusBar().showMessage(
                f"Export complete — alignment shown on map "
                f"({len(self._elements)} elements). Hover any segment for "
                "parameters.")
            return
        if not alignments or not any(len(a) > 0 for a in alignments):
            self.statusBar().showMessage(
                "⚠ Export finished but alignment contains no points — "
                "nothing drawn on map."
            )
            return
        # No working element list at all (shouldn't normally happen — export
        # requires a selected candidate) — fall back to the old merged draw.
        self.map_widget.show_alignment(alignments)
        total_pts = sum(len(a) for a in alignments)
        self.statusBar().showMessage(
            f"Both overlays ready — 🔴 red: fitted LandXML ({total_pts} pts)  "
            f"🔵 cyan dashed: OSM reference  ({len(alignments)} track(s))."
        )

    def _on_alignment_segments_ready(self, segments: list):
        """Per-element coloured rendering after final export.

        `_on_alignment_ready` above already draws the interactive view from
        `self._elements` when one exists — this only still draws when it
        didn't (the same rare fallback case), so the two signals never
        race/redraw over each other."""
        if self._elements:
            return
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
        summary = (error.splitlines() or [""])[0]   # detail lines → log
        self.step1.show_view_results([], status=f"Search failed: {summary}")
        self.statusBar().showMessage(f"Lines-in-View search failed: {summary}")
        self.log_panel.log(f"Lines-in-View search failed: {error}", "warn")

    # ------------------------------------------------------------------
    # Railway loaded
    # ------------------------------------------------------------------

    def _on_railway_fetched(self, overpass_data, relation_info: dict):
        from osm.parser import parse_tracks
        from osm.query import stops_from_elements
        try:
            self._tracks = parse_tracks(overpass_data)
            self._osm_stop_nodes = stops_from_elements(
                overpass_data.get("elements", []))
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
        # Level 1 (raw OSM polyline) has no PI model — its metrics never
        # change post-selection, so cache the candidate's own numbers
        # (already computed at generation time) for the metrics chip
        # instead of re-running a fresh deviation pass on every refresh.
        self._level1_metrics = None if self._pi_model is not None else {
            "max_deviation":        getattr(candidate, "max_deviation", 0.0),
            "rmse":                 getattr(candidate, "rmse", 0.0),
            "mean_deviation":       getattr(candidate, "mean_deviation", 0.0),
            "n_elements":           len(self._elements),
            "max_heading_jump_deg": getattr(candidate, "max_heading_jump_deg", 0.0),
        }
        self._edit_history.clear()   # fresh working alignment — no prior edits to undo
        self._refine_baseline = self._make_baseline(self._pi_model)
        self._mark_stale()
        # Clear candidate overlays; Step 5 will show the chosen one as
        # per-element coloured alignment with hover tooltips.
        self.map_widget.clear_candidates()
        xy        = self._xy_list[0]        if self._xy_list        else None
        chainages = self._chainages_list[0] if self._chainages_list else None
        self.step5_design.prepare(
            candidate, self._pi_model, xy, chainages, self._settings)

        # One draw only: the per-element coloured polylines when available,
        # else the merged red fallback. (Drawing both used to trigger two
        # full layer builds and two competing 0.8 s flyToBounds animations —
        # a visible red-then-coloured flash on every candidate selection.)
        merged   = getattr(candidate, "geo_wgs84", None)
        segments = getattr(candidate, "geo_segments_wgs84", None) or []
        n_line   = sum(1 for s in segments if s.get("type") == "Line")
        n_arc    = sum(1 for s in segments if s.get("type") == "Arc")
        n_spiral = sum(1 for s in segments if s.get("type") == "Spiral")
        print(f"[App] candidate '{getattr(candidate, 'label', '?')}' selected: "
              f"{len(segments)} segments ({n_line} Lines, {n_arc} Arcs, {n_spiral} Spirals); "
              f"merged_pts={len(merged) if merged else 0}")
        if segments:
            self.map_widget.show_alignment_segmented(segments)
        elif merged:
            self.map_widget.show_alignment([[list(pt) for pt in merged]])

        # Element table dock (editable when the candidate carries a PI model)
        self.element_table.prepare(
            self._pi_model, self._elements,
            check_interval=self._settings.get("check_interval", 5.0),
        )
        self._show_pi_overlay(self._pi_model)
        self._update_metrics_chip()

        self.statusBar().showMessage(
            f"Candidate '{getattr(candidate, 'label', '')}' selected — "
            f"{n_line} Lines · {n_arc} Arcs · {n_spiral} Spirals shown. "
            "Hover any segment for parameters."
        )
        self._goto_step(S_DESIGN)

    def _on_candidates_back(self):
        """Go back from Candidates to Configure — clear overlays."""
        self.map_widget.clear_candidates()
        self.map_widget.clear_alignment()
        self.map_widget.clear_osm_reference()
        self._goto_step(S_CONFIG)

    def _update_metrics_chip(self, metrics: dict | None = None):
        """Single funnel refreshing the persistent deviation-metrics chip —
        call after anything that changes `self._pi_model`/`self._elements`.
        Cheap: reuses whatever stats the triggering rebuild already
        computed (or the cached Level-1 metrics), never a fresh pass.

        `metrics`, when given, is the dict the caller's own rebuild already
        computed (e.g. element_table's `rebuilt` signal) — reuse it instead
        of recomputing via `metrics_from_stats`, which used to run a second,
        redundant O(N_elements) heading-jump scan on every single edit."""
        chip = getattr(self, "_metrics_chip", None)
        if chip is None:
            return
        if not self._prefs.get("show_metrics_chip", True):
            chip.clear()
            return
        if self._pi_model is not None:
            if metrics is None:
                from geometry.candidates import metrics_from_stats
                metrics = metrics_from_stats(
                    getattr(self._pi_model, "last_stats", {}) or {}, self._elements)
            xy_ref = getattr(self._pi_model, "xy_ref", None)
            n_points = len(xy_ref) if xy_ref is not None else 0
            chip.set_metrics(metrics, n_points=n_points, level=self._level)
        elif self._elements and self._level1_metrics is not None:
            chip.set_metrics(self._level1_metrics, level=self._level)
        else:
            chip.clear()

    def _on_metric_clicked(self, element_id: str):
        """Metrics chip clicked (Req 9a) — highlight + select the worst-
        deviation element, same round trip a table-row click already does
        (`highlightElements` pans to it automatically when off-screen)."""
        self.map_widget.highlight_elements([element_id])
        self.element_table.select_element(element_id)

    def _sample_segments(self, elements: list, interval: float,
                         color_mode: str = "type") -> list | None:
        """Sample + batch-project `elements` into the JS segment payload
        shape ({type, params, points}, one dict per element). None on any
        failure — the caller decides the fallback.

        `color_mode="deviation"` (Req 9b) additionally stamps a `"color"`
        per segment from that element's `_max_dev` — map.html's
        `_addSegmentLayer` prefers `seg.color` over the type-based lookup
        when present, so hover/click/highlight are completely unaffected."""
        from geometry.alignment import reconstruct_alignment_per_element
        from geometry.projection import projected_to_wgs84
        try:
            from gui.worker import _serialise_element_params
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
            threshold = float(self._settings.get("max_deviation", 1.0) or 1.0)
            payload = []
            for el, off, n in spans:
                if not n:
                    continue
                seg = {
                    "type":   el.get("type", "Line"),
                    "params": _serialise_element_params(el),
                    "points": [list(p) for p in wgs_all[off:off + n]],
                }
                if color_mode == "deviation":
                    from gui.palette import deviation_color
                    seg["color"] = deviation_color(
                        float(el.get("_max_dev", 0.0) or 0.0), threshold)
                payload.append(seg)
            return payload
        except Exception as exc:
            # Was silent — degraded the ENTIRE map to a non-interactive
            # merged redraw (see _render_elements_on_map's fallback) with no
            # trace of why. The load path and every edit-driven refresh
            # (splits included) go through this every time, unlike the
            # fresh-wizard path which draws a pre-baked payload instead —
            # so a real failure here needs to be visible, not swallowed.
            self.log_panel.log(
                f"⚠ Map: per-element sampling failed ({exc}) — falling back "
                "to a plain merged redraw (no hover/click on the map until "
                "the next successful refresh).", "warn")
            return None

    def _render_elements_on_map(self, elements: list, fit_view: bool = False,
                                splice: dict | None = None,
                                color_mode: str | None = None):
        """Render an element chain as the segmented alignment overlay.

        fit_view=False (default) keeps the user's current zoom/pan — used
        for edit-driven rebuilds from the element table. `splice` (from
        `model.last_splice` after a `rebuild_pi_span` edit) lets a localized
        change ship only the affected element span to the map — sampling,
        projecting and serializing the WHOLE alignment on every PI drag or
        radius keystroke was the single largest per-edit map cost on long
        corridors, even though the JS side only ever kept the diffed layers.

        `color_mode` (Req 9b): None reuses whatever mode the map prefs say
        (`self._prefs["map_color_mode"]`, "type" or "deviation") — pass it
        explicitly only to override that for one call."""
        from geometry.alignment import reconstruct_alignment_projected
        from geometry.projection import projected_to_wgs84

        if not elements:
            return
        if color_mode is None:
            color_mode = self._prefs.get("map_color_mode", "type")
        try:
            total_len = sum(float(e.get("length", 0.0)) for e in elements)
            # Sample density scales with length: 2 m on short sections, up
            # to 20 m on very long ones — keeps the JSON payload and the
            # Leaflet layer work bounded on 50 km+ alignments.
            interval = min(20.0, max(2.0, total_len / 4000.0))

            if splice is not None and not fit_view:
                start, removed, added = (
                    splice["start"], splice["removed"], splice["added"])
                changed_segments = self._sample_segments(
                    elements[start:start + added], interval, color_mode)
                if changed_segments is not None:
                    from gui.worker import _serialise_element_params
                    # Elements before `start` are untouched by this edit —
                    # no station/id shift, nothing to retarget — so only
                    # [start:] needs sending; that used to be the WHOLE
                    # alignment on every single edit regardless of where
                    # in a long corridor the edit happened.
                    metas = [{"type": el.get("type", "Line"),
                              "params": _serialise_element_params(el)}
                             for el in elements[start:]]
                    if color_mode == "deviation":
                        from gui.palette import deviation_color
                        threshold = float(
                            self._settings.get("max_deviation", 1.0) or 1.0)
                        for meta, el in zip(metas, elements[start:]):
                            meta["color"] = deviation_color(
                                float(el.get("_max_dev", 0.0) or 0.0), threshold)
                    if self.map_widget.patch_alignment_segmented(
                            start, removed, changed_segments, metas):
                        return
                    # bookkeeping mismatch — fall through to a full redraw

            segments_payload = self._sample_segments(elements, interval, color_mode)
            if segments_payload:
                if fit_view:
                    self.map_widget.show_alignment_segmented(segments_payload, True)
                else:
                    # edit-driven refresh: diff-update keeps unchanged layers
                    self.map_widget.update_alignment_segmented(segments_payload)
            else:
                geo_xy     = reconstruct_alignment_projected(elements, sample_interval=5.0)
                geo_latlon = projected_to_wgs84(geo_xy, self._work_epsg)
                # _sample_segments already logged why — draw muted/thin so
                # this unplanned fallback never buries the OSM reference
                # layer the way the bold "selected" export-preview look
                # would (that look stays reserved for its two deliberate
                # call sites: candidate selection and export-ready).
                self.map_widget.show_alignment(
                    [[[lat, lon] for lat, lon in geo_latlon]], style="degraded")
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ Map update failed: {exc}")

    def _show_pi_overlay(self, pi_model, moved_only: int | None = None):
        """Draw PI markers + dashed virtual tangent stubs for the model.

        moved_only=k: only PI k's position changed (a drag/restore) and no
        PI was added/removed/reindexed — patch just that marker + the
        tangent stubs in place instead of tearing down and rebuilding every
        marker on the map (which also made the dragged marker itself
        visibly snap)."""
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
            payload = {"pis": pi_pts, "tangents": tangents}

            if (moved_only is not None
                    and getattr(self.map_widget, "_last_pi_overlay", None) is not None):
                moved = next((p for p in pi_pts if p["id"] == moved_only), None)
                if moved is not None:
                    self.map_widget.move_pi_marker(moved_only, moved["latlon"])
                    self.map_widget.update_pi_tangents(tangents)
                    self.map_widget.sync_pi_overlay_cache(payload)
                    return

            self.map_widget.show_pi_overlay(payload)
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ PI overlay failed: {exc}")

    def _on_elements_rebuilt(self, elements: list, metrics: dict,
                             splice: dict | None = None):
        """Element table edited → refresh Step 5, map, PI overlay and log.

        `splice` is `model.last_splice` from the edit that just ran
        (element_table._rebuild always emits it) — a value edit, omit,
        merge, split, delete, trim or undo-merge all route through
        `rebuild_pi_span` internally, so it correctly describes exactly
        this one change."""
        # App owns the alignment; any edit invalidates the downstream steps,
        # which re-read it when the user next opens them.
        self._elements = list(elements)
        self._mark_stale()
        self._refresh_nav()
        self.step5_design.set_elements(elements, metrics)
        self._render_elements_on_map(elements, splice=splice)
        self._show_pi_overlay(self.element_table._model)
        self._update_metrics_chip(metrics)
        n_spirals = sum(1 for e in elements if e.get("type") == "Spiral")
        msg = (f"Alignment rebuilt — {len(elements)} elements "
               f"({n_spirals} spiral{'s' if n_spirals != 1 else ''}), "
               f"max dev {metrics.get('max_deviation', 0.0):.2f} m, "
               f"C1 {metrics.get('max_heading_jump_deg', 0.0):.3f}°")
        self.statusBar().showMessage(msg)
        self.log_panel.log(msg, "ok")
        # Geometry notes from the rebuild (radius clamps, skipped curves, …)
        # — replay just the new tail, not the whole history every time.
        model = self.element_table._model
        new_notes, self._pi_log_replayed = _replay_tail(
            getattr(model, "log", None) or [], self._pi_log_replayed)
        for note in new_notes:
            self.log_panel.log(note, "warn" if note.startswith("⚠") else "info")
        # The route statistics' C1 number should read ~0.000° (the solver
        # builds elements exactly C1 by construction) — a non-empty report
        # here means a genuine continuity break, not the old small-angle
        # measurement artifact, and is worth a specific warning.
        junctions = metrics.get("c1_junctions") or []
        if junctions:
            worst = max(junctions, key=lambda j: j["jump_deg"])
            self.log_panel.log(
                f"⚠ C1 continuity break at {len(junctions)} junction"
                f"{'s' if len(junctions) != 1 else ''} — worst "
                f"{worst['jump_deg']:.3f}° between {worst['a_type']} "
                f"{worst.get('a_id') or '?'} and {worst['b_type']} "
                f"{worst.get('b_id') or '?'}.", "warn")

    def _on_element_rows_selected(self, element_ids: list):
        self.map_widget.highlight_elements(element_ids)
        self._refresh_toolbar_edit_actions()

    def _on_step2_tracks_changed(self, tracks: list):
        """Step 2 split/merged/removed a track — the input data itself
        changed, so anything already fitted from the old tracks is invalid."""
        self._tracks = tracks
        # fit=False: a split/merge/remove edit must not throw away the
        # user's current zoom/pan mid-edit.
        self.map_widget.show_tracks(tracks, fit=False)
        if self._selected_tracks or self._elements:
            self.log_panel.log(
                "Tracks changed — re-select and confirm the section before "
                "continuing (previous fit is now stale).", "warn")
        self._selected_tracks = []
        self._settings = {}
        self._elements = []
        self._pi_model = None
        self._xy_list = []
        self._chainages_list = []
        self._edit_history.clear()
        self._set_dirty(True)
        self._refresh_nav()

    def _on_split_pick_mode(self, on: bool):
        # Refine's "split a Line" and Step 2's "split a track" share the
        # same JS click-mode/signal (the two are never armed at once — one
        # per step's own UI); this flag decides who the next click is for.
        if on:
            self._deactivate_all_tools(keep="split")
        self._split_pick_target = "line" if on else None
        self.map_widget.set_split_click_mode(on)
        if on:
            self.log_panel.log(
                "Split-pick mode armed — click a point on the map to "
                "insert a PI there.", "info")

    def _on_track_split_pick_mode(self, on: bool):
        if on:
            self._deactivate_all_tools(keep="split")
        self._split_pick_target = "track" if on else None
        self.map_widget.set_split_click_mode(on)

    def _on_station_pick_mode(self, on: bool):
        if on:
            self._deactivate_all_tools(keep="station")
        self.map_widget.set_station_click_mode(on)

    def _deactivate_all_tools(self, keep: str | None = None):
        """Force-disarm every modal map tool (Edit mode, split-pick — shared
        by Refine's "split a Line" and Step 2's "split a track" — and
        Stations' "place station") so switching steps, or arming one of
        these tools, never leaves another one stuck armed with its map
        click-mode flag still live (each tool's own `toggled` handler does
        the actual disarming once its button flips back to unchecked)."""
        if keep != "edit" and self._act_edit_mode.isChecked():
            self._act_edit_mode.setChecked(False)
        if keep != "split" and self.element_table._pick_split_btn.isChecked():
            self.element_table._pick_split_btn.setChecked(False)
        if keep != "split" and self.step2._split_track_btn.isChecked():
            self.step2._split_track_btn.setChecked(False)
        if keep != "station" and self.step6_stations._place_btn.isChecked():
            self.step6_stations._place_btn.setChecked(False)

    def _on_split_point_clicked(self, lat: float, lon: float):
        if self._split_pick_target == "track":
            self.step2.on_map_split_point(lat, lon)
            return
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

    def _on_sync_parameters(self):
        """"⟳ Sync" button (Part 2B): force a full table↔map↔metrics resync
        on demand — covers any edit path that left the halo/table/metrics
        chip momentarily out of step with the model (e.g. a fast-path table
        update that didn't re-emit the selection)."""
        elements = self._elements or self.element_table.current_elements()
        if not elements:
            self.log_panel.log("No alignment to sync yet.", "warn")
            return
        self._render_elements_on_map(elements)
        self._show_pi_overlay(self._pi_model)
        self.element_table._on_selection()   # re-push the halo
        self._update_metrics_chip()
        self.log_panel.log("Table, map and metrics resynced.", "ok")

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
    # Refine & Consolidate accepted → Stations
    # ------------------------------------------------------------------

    def _on_design_done(self, elements: list):
        if elements:
            self._elements = elements
        self._mark_stale()
        self._goto_step(S_STATIONS)

    def _on_consolidation_model_changed(self, moved_pi: int | None = None):
        """Consolidation applied/undone (or a PI moved) — refresh table, map
        and downstream.

        `moved_pi`: set ONLY by `_on_pi_dragged`/`_on_pi_restore`, whose
        preceding `move_pi` call is the single `rebuild_pi_span` that
        produced `self._pi_model.last_splice` — every other caller here
        (restore-default, undo/redo, consolidation apply/undo) either
        replaces the model wholesale or applies several splices in one
        call, so they always render the full alignment."""
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
        self.step5_design.set_elements(self._elements, metrics)
        splice = self._pi_model.last_splice if moved_pi is not None else None
        self._render_elements_on_map(self._elements, splice=splice)
        self._show_pi_overlay(self._pi_model, moved_only=moved_pi)
        self._update_metrics_chip()
        self._mark_stale()
        self._refresh_nav()
        self.statusBar().showMessage(
            f"Alignment now has {len(self._elements)} elements "
            f"(max dev {metrics.get('max_deviation', 0.0):.2f} m).")

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
            "analysis or skip to the vertical profile."
        )
        self._goto_step(S_CROSSSEC)

    # ------------------------------------------------------------------
    # Cross-section analysis done → go to the Vertical Profile
    # ------------------------------------------------------------------

    def _on_analysis_done(self, results: list):
        # results may be [] if the step was skipped
        self._prepare_vertical()
        self._stale.discard(S_VERTICAL)
        n = len(results)
        msg = (
            f"Cross-section: {n} stations analysed. Now build the "
            "vertical profile."
            if n else
            "Skipped cross-section analysis. Now build the vertical profile."
        )
        self.statusBar().showMessage(msg)
        self._goto_step(S_VERTICAL)

    # ------------------------------------------------------------------
    # Vertical profile
    # ------------------------------------------------------------------

    def _prepare_vertical(self):
        """Bind the App-owned profile (creating a default one for a new or
        pre-2.0 project) to the step and the shared canvas."""
        from geometry.profile import make_default_profile
        if self._profile is None:
            self._profile = make_default_profile(self._elements, self._settings)
        self.step8_vertical.prepare(
            self._elements, self._work_epsg, self._settings,
            self._profile, self.profile_canvas)

    def _prepare_export(self):
        """Hand the export the confirmed vertical elements when there are
        any — otherwise it falls back to sampling the DEM itself."""
        pvis = getattr(self._profile, "pvis", None)
        self.step7_export.prepare(
            [self._elements], self._selected_tracks, self._settings,
            self._work_epsg, self._xy_list,
            stations=self._stations,
            vertical_list=[list(pvis)] if pvis else None,
        )

    def _on_profile_changed(self):
        """A smoothing parameter or a manual connection changed. The step
        mutates the shared profile in place, so only the export has to
        re-read it — the vertical step itself is already current."""
        self._stale.add(S_EXPORT)
        self._set_dirty(True)

    def _on_profile_confirmed(self, profile):
        self._profile = profile
        self._prepare_export()
        self._stale.discard(S_EXPORT)
        self._set_dirty(True)
        st = getattr(profile, "stats", None) or {}
        self.statusBar().showMessage(
            f"Vertical profile confirmed: {st.get('n_pvi', 0)} PVIs, "
            f"max gradient {st.get('max_grade_pm', 0.0):.2f} ‰. "
            "Choose a file and export.")
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
        self._level1_metrics     = None
        self._stations           = []
        self._osm_stop_nodes     = []
        self._profile            = None
        self._xy_list            = []
        self._chainages_list     = []
        self._done_steps         = set()
        self._stale              = set()
        self._edit_history.clear()
        self._refine_baseline    = None
        self.map_widget.clear_all()   # clears tracks + osmRef + alignment + candidates + cross-section + PI/stations
        self.element_table.clear()
        self.step6_stations.reset()
        self.step8_vertical.reset()
        self.profile_canvas.clear()
        self.step5_design.reset()
        self.sidebar.reset()
        self._update_metrics_chip()
        self._goto_step(S_FIND)
        self.statusBar().showMessage(
            "Ready. Search for a new railway or use 'Lines in View'."
        )
