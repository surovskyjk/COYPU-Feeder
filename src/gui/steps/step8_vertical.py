"""
Step 8 — Vertical Profile.

Terrain elevation is sampled once along the *fitted* centreline, smoothed into
a set of PVIs under a hard maximum-gradient limit, and handed to the LandXML
``<Profile>`` / ``<ProfAlign>`` export.  Before this step existed the DEM was
queried inside the export worker, fitted against the raw OSM polyline chainage
and thrown away, so the vertical alignment was neither visible nor editable.

Two smoothing methods share one PVI back end (see ``geometry.profile``):

  • Gradient-constrained simplification — simplify the terrain into
    constant-grade tangents within Δz tolerance, then clamp every grade to
    s_max.  Where the terrain is steeper than the limit the design deviates
    into cut/fill, which the read-out reports.
  • Moving window filter — Savitzky-Golay (or a plain moving average at
    polynomial order 0) to strip DEM micro-spikes first, then the same
    simplification and clamp.

Manual direct connections (tunnels / viaducts) replace the terrain inside a
station range with a straight line; their boundaries are forced PVIs, so no
filter can smooth them away.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui import icons
from gui import style_kit as sk
from gui.style_kit import ThemedPanel
from gui.steps._widgets import (
    _hline, _labeled_check, _labeled_combo, _labeled_int_spin, _labeled_spin,
    _stat_row,
)
from geometry import profile as vprof

COLS = ["Start [km]", "End [km]", "Label", ""]
COL_START, COL_END, COL_LABEL, COL_DEL = range(4)

# The smoothing pipeline is pure numpy over a few thousand points, so a slider
# drag only needs coalescing — not a thread. 120 ms is short enough to feel
# live and long enough that a spinbox auto-repeat doesn't recompute per tick.
RECOMPUTE_DEBOUNCE_MS = 120

_SOURCE_ITEMS = [
    "ČÚZK DMR 5G (CZ, ~0.2 m accuracy)",
    "OpenTopoData (EU-DEM/SRTM, ~25–30 m)",
]
_SOURCE_KEYS = ["cuzk_dmr5g", "opentopodata"]

_METHOD_ITEMS = [
    "Gradient-constrained (PVI)",
    "Moving window filter",
]
_METHOD_KEYS = [vprof.METHOD_GRADIENT, vprof.METHOD_WINDOW]


class Step8Vertical(ThemedPanel, QWidget):
    """
    Vertical profile smoothing step.

    Signals
    -------
    profile_confirmed(object)
        "Apply / Confirm Profile" — carries the ``VerticalProfile`` whose
        ``pvis`` feed the LandXML export.
    profile_changed()
        Any edit that makes the project dirty and the export stale.
    back_requested()
    log_message(str, str)
    range_pick_mode(bool)
        Arm/disarm click-drag range picking on the profile canvas.
    """

    profile_confirmed = Signal(object)
    profile_changed   = Signal()
    back_requested    = Signal()
    log_message       = Signal(str, str)
    range_pick_mode   = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = None
        self._canvas  = None
        self._elements: list = []
        self._work_epsg: int = 32633
        self._settings: dict = {}
        self._worker = None
        self._timer = None
        self._populating = False
        self._pick_armed = False
        self._loading = False       # suppress recompute while seeding controls
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        # Five groups plus the connection table overflow the 340 px column on a
        # 1080p screen; without a scroll area Qt squeezes the rows below their
        # minimum and the spinboxes overlap. Same construction as Step 5.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("Vertical Profile")
        self.bolded(title, 4)
        sk.set_role(title, "title")
        root.addWidget(title)

        sub = QLabel(
            "Smooth the sampled terrain into a vertical alignment of\n"
            "constant-grade tangents, under a hard gradient limit."
        )
        sub.setWordWrap(True)
        self.themed(sub, sk.muted_label)
        root.addWidget(sub)

        root.addWidget(_hline())
        root.addWidget(self._build_sampling_box())
        root.addWidget(self._build_smoothing_box())
        root.addWidget(self._build_connections_box())
        root.addWidget(self._build_results_box())
        root.addLayout(self._build_bottom_row())
        root.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    def _build_sampling_box(self) -> QGroupBox:
        box = QGroupBox("Terrain sampling")
        box.setStyleSheet("QGroupBox { font-weight:600; }")
        g = QVBoxLayout(box)
        g.setSpacing(6)

        self._source_combo = _labeled_combo(
            g, "Elevation source:", _SOURCE_ITEMS,
            "Source for terrain elevation along the fitted centreline.\n"
            "ČÚZK DMR 5G: Czech national lidar terrain model, © ČÚZK – on-line.\n"
            "Falls back to OpenTopoData outside its coverage.",
        )
        self._interval_spin = _labeled_spin(
            g, "Sample interval:", 5.0, 200.0,
            vprof.DEFAULT_SAMPLE_INTERVAL, " m",
            "Distance between DEM samples along the alignment.\n"
            "Smaller is more faithful but means proportionally more queries.",
        )

        row = QHBoxLayout()
        row.setSpacing(6)
        self._sample_btn = QPushButton("Sample terrain")
        self._sample_btn.setFixedHeight(30)
        self._sample_btn.setIcon(icons.makeIcon("run"))
        self.themed(self._sample_btn, sk.secondary_button)
        self._sample_btn.clicked.connect(self._start_sampling)
        row.addWidget(self._sample_btn)
        g.addLayout(row)

        self._status_lbl = QLabel("Sample terrain to begin.")
        self._status_lbl.setWordWrap(True)
        self.themed(self._status_lbl, sk.muted_label)
        g.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(16)
        self.themed(self._progress, sk.progress_bar)
        self._progress.hide()
        g.addWidget(self._progress)
        return box

    def _build_smoothing_box(self) -> QGroupBox:
        box = QGroupBox("Smoothing")
        box.setStyleSheet("QGroupBox { font-weight:600; }")
        g = QVBoxLayout(box)
        g.setSpacing(6)

        self._method_combo = _labeled_combo(
            g, "Algorithm:", _METHOD_ITEMS,
            "Gradient-constrained: simplify straight to PVIs.\n"
            "Moving window: filter DEM micro-spikes first, then simplify.\n"
            "Both then enforce the gradient limit below.",
        )
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)

        self._grade_spin = _labeled_spin(
            g, "Max gradient:", 0.5, vprof.MAX_GRADE_PM_LIMIT,
            vprof.DEFAULT_MAX_GRADE_PM, " ‰",
            "Hard limit on |dz/ds|. Where the terrain is steeper the design\n"
            "profile deviates into cut/fill rather than exceeding the limit.",
            decimals=1, step=0.5,
        )
        self._tol_spin = _labeled_spin(
            g, "Δz tolerance:", 0.05, 5.0, vprof.DEFAULT_Z_TOL, " m",
            "Simplification tolerance: the design profile stays within this\n"
            "vertical distance of the terrain before the gradient clamp.\n"
            "Larger ⇒ fewer PVIs.",
            decimals=2, step=0.05,
        )
        self._window_spin = _labeled_spin(
            g, "Window:", vprof.WINDOW_M_RANGE[0], vprof.WINDOW_M_RANGE[1],
            vprof.DEFAULT_WINDOW_M, " m",
            "Filter window length. Longer ⇒ smoother, less terrain detail.",
            decimals=0, step=10.0,
        )
        self._order_spin = _labeled_int_spin(
            g, "Polynomial order:", 0, 4, vprof.DEFAULT_POLY_ORDER, "",
            "0 = plain moving average; 1–4 = Savitzky-Golay local polynomial.",
        )

        self._vc_check = _labeled_check(
            g, "Insert vertical curves (ParaCurve)", True,
            "Emit a parabolic vertical curve at every interior PVI.\n"
            "Without them the exported profile is a kinked polyline.",
        )
        self._vc_spin = _labeled_spin(
            g, "Curve length:", 20.0, 2000.0, vprof.DEFAULT_VC_LENGTH, " m",
            "Nominal vertical-curve length; automatically shortened where the\n"
            "neighbouring tangents are too short to hold it.",
            decimals=0, step=10.0,
        )

        for w in (self._grade_spin, self._tol_spin, self._window_spin,
                  self._vc_spin):
            w.valueChanged.connect(self._arm_recompute)
        self._order_spin.valueChanged.connect(self._arm_recompute)
        self._vc_check.toggled.connect(self._arm_recompute)
        self._method_combo.currentIndexChanged.connect(self._arm_recompute)
        return box

    def _build_connections_box(self) -> QGroupBox:
        box = QGroupBox("Direct connections (tunnels / bridges)")
        box.setStyleSheet("QGroupBox { font-weight:600; }")
        g = QVBoxLayout(box)
        g.setSpacing(6)

        hint = QLabel(
            "Terrain inside a range is replaced by a straight line between\n"
            "its boundary elevations, and its ends become fixed PVIs."
        )
        hint.setWordWrap(True)
        self.themed(hint, sk.muted_label)
        g.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._add_btn = QPushButton("＋ Add range")
        self._add_btn.setFixedHeight(28)
        self._add_btn.clicked.connect(self._on_add_range)
        row.addWidget(self._add_btn)

        self._pick_btn = QPushButton("📍 Pick on profile")
        self._pick_btn.setFixedHeight(28)
        self._pick_btn.setCheckable(True)
        self._pick_btn.setToolTip(
            "Drag across the profile canvas to define a range.")
        self._pick_btn.toggled.connect(self._on_pick_toggled)
        row.addWidget(self._pick_btn)
        row.addStretch()
        g.addLayout(row)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(90)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_LABEL, QHeaderView.ResizeMode.Stretch)
        self._table.cellChanged.connect(self._on_cell_changed)
        g.addWidget(self._table)
        return box

    def _build_results_box(self) -> QGroupBox:
        box = QGroupBox("Profile")
        box.setStyleSheet("QGroupBox { font-weight:600; }")
        g = QVBoxLayout(box)
        g.setSpacing(4)
        self._stat_grade = _stat_row(g, "Max gradient:")
        self._stat_zmin  = _stat_row(g, "Min / max elevation:")
        self._stat_cut   = _stat_row(g, "Max cut / fill:")
        self._stat_pvi   = _stat_row(g, "PVIs / samples:")
        self._results_box = box
        box.setEnabled(False)
        return box

    def _build_bottom_row(self) -> QHBoxLayout:
        bot = QHBoxLayout()
        bot.setSpacing(6)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setFixedHeight(32)
        self._back_btn.clicked.connect(self.back_requested.emit)
        bot.addWidget(self._back_btn)

        self._confirm_btn = QPushButton("Apply / Confirm Profile →")
        self._confirm_btn.setFixedHeight(32)
        self._confirm_btn.setEnabled(False)
        self.themed(self._confirm_btn, sk.confirm_button)
        self._confirm_btn.clicked.connect(self._on_confirm)
        bot.addWidget(self._confirm_btn)
        return bot

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def bind(self, profile, canvas) -> None:
        """Bind the App-owned profile and the shared canvas. The profile is
        mutated in place and never replaced — the same rule Step 5 follows
        with the shared PI model."""
        self._profile = profile
        if canvas is not None and canvas is not self._canvas:
            # bind() runs on every entry to the step, so connect once per
            # canvas rather than disconnect-then-reconnect — Qt warns when
            # asked to drop a connection that was never made.
            if self._canvas is not None:
                try:
                    self._canvas.range_picked.disconnect(self._on_range_picked)
                except (RuntimeError, TypeError):
                    pass    # the previous canvas is already gone
            canvas.range_picked.connect(self._on_range_picked)
        self._canvas = canvas
        if canvas is not None:
            canvas.set_profile(profile)
        self._seed_controls()
        self._populate()
        self._recompute(emit=False)

    def prepare(self, elements: list, work_epsg: int, settings: dict,
                profile, canvas=None) -> None:
        """Called by App on entry / after a downstream refresh."""
        self._elements  = elements or []
        self._work_epsg = work_epsg
        self._settings  = settings or {}
        self.bind(profile, canvas if canvas is not None else self._canvas)
        self._check_fingerprint()

    def profile(self):
        return self._profile

    def reset(self) -> None:
        self._profile = None
        self._elements = []
        self._worker = None
        self._pick_btn.setChecked(False)
        self._populating = True
        try:
            self._table.setRowCount(0)
        finally:
            self._populating = False
        self._results_box.setEnabled(False)
        self._confirm_btn.setEnabled(False)
        self._progress.hide()
        self._progress.setValue(0)
        self._status_lbl.setText("Sample terrain to begin.")
        for lbl in (self._stat_grade, self._stat_zmin, self._stat_cut,
                    self._stat_pvi):
            lbl.setText("—")
        if self._canvas is not None:
            self._canvas.clear()

    # ------------------------------------------------------------------
    # Controls <-> model
    # ------------------------------------------------------------------

    def _seed_controls(self) -> None:
        p = self._profile
        if p is None:
            return
        self._loading = True
        try:
            src = p.elevation_source
            self._source_combo.setCurrentIndex(
                _SOURCE_KEYS.index(src) if src in _SOURCE_KEYS else 0)
            self._method_combo.setCurrentIndex(
                _METHOD_KEYS.index(p.method) if p.method in _METHOD_KEYS else 0)
            self._interval_spin.setValue(float(p.sample_interval))
            self._grade_spin.setValue(float(p.max_grade_pm))
            self._tol_spin.setValue(float(p.z_tol))
            self._window_spin.setValue(float(p.window_m))
            self._order_spin.setValue(int(p.poly_order))
            self._vc_check.setChecked(bool(p.vc_enabled))
            self._vc_spin.setValue(float(p.vc_length))
        finally:
            self._loading = False
        self._on_method_changed()

    def _harvest_controls(self) -> None:
        p = self._profile
        if p is None:
            return
        p.elevation_source = _SOURCE_KEYS[self._source_combo.currentIndex()]
        p.method       = _METHOD_KEYS[self._method_combo.currentIndex()]
        p.sample_interval = float(self._interval_spin.value())
        p.max_grade_pm = float(self._grade_spin.value())
        p.z_tol        = float(self._tol_spin.value())
        p.window_m     = float(self._window_spin.value())
        p.poly_order   = int(self._order_spin.value())
        p.vc_enabled   = bool(self._vc_check.isChecked())
        p.vc_length    = float(self._vc_spin.value())

    def _on_method_changed(self) -> None:
        window_method = (self._method_combo.currentIndex()
                         == _METHOD_KEYS.index(vprof.METHOD_WINDOW))
        for w in (self._window_spin, self._order_spin):
            w.setEnabled(window_method)
            # The label sits in the same row layout, so grey the row together.
            w.setVisible(True)

    # ------------------------------------------------------------------
    # Recompute (debounced)
    # ------------------------------------------------------------------

    def _arm_recompute(self, *_args) -> None:
        if self._loading:
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(RECOMPUTE_DEBOUNCE_MS)
            self._timer.timeout.connect(self._recompute)
        self._timer.start()

    def _recompute(self, emit: bool = True) -> None:
        p = self._profile
        if p is None:
            return
        self._harvest_controls()
        vprof.rebuild_profile(p)
        if self._canvas is not None:
            self._canvas.set_profile(p)
        self._populate_results()
        if emit:
            self.profile_changed.emit()

    def _populate_results(self) -> None:
        p = self._profile
        st = (p.stats if p is not None and p.stats else vprof.empty_stats())
        has = bool(p is not None and p.has_samples)
        self._results_box.setEnabled(has)
        self._confirm_btn.setEnabled(has and bool(p.pvis))
        if not has:
            for lbl in (self._stat_grade, self._stat_zmin, self._stat_cut,
                        self._stat_pvi):
                lbl.setText("—")
            return

        limit = float(self._grade_spin.value())
        self._stat_grade.setText(f"{st['max_grade_pm']:.2f} ‰  "
                                 f"(limit {limit:.1f} ‰)")
        sk.set_role(self._stat_grade,
                    "ok" if st["max_grade_pm"] <= limit + 1e-6 else "warn",
                    " font-size:10px;")
        self._stat_zmin.setText(f"{st['z_min']:.1f} / {st['z_max']:.1f} m")
        self._stat_cut.setText(f"{st['max_cut']:.2f} / {st['max_fill']:.2f} m")
        nodata = (f"  ({st['n_nodata']} no-data)" if st["n_nodata"] else "")
        self._stat_pvi.setText(f"{st['n_pvi']} / {st['n_samples']}{nodata}")

    # ------------------------------------------------------------------
    # Terrain sampling
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._sample_btn.setEnabled(not busy)
        self._back_btn.setEnabled(not busy)
        self._confirm_btn.setEnabled(not busy and self._confirm_btn.isEnabled())
        self._progress.setVisible(busy)

    def _start_sampling(self) -> None:
        if not self._elements:
            self._status_lbl.setText("No alignment to sample along.")
            return
        if self._profile is None:
            self._status_lbl.setText("No profile bound.")
            return

        from gui.progress_util import ElapsedTimer
        from gui.worker import ProfileSampleWorker

        self._harvest_controls()
        self._timerx = ElapsedTimer()
        self._set_busy(True)
        self._progress.setValue(0)
        self._status_lbl.setText("Querying terrain elevation…")

        self._worker = ProfileSampleWorker(
            self._elements, self._work_epsg,
            interval=self._profile.sample_interval,
            elevation_source=self._profile.elevation_source,
            parent=self,
        )
        self._worker.progress.connect(self._on_sample_progress)
        self._worker.finished.connect(self._on_sample_finished)
        self._worker.failed.connect(self._on_sample_failed)
        self._worker.start()

    def _on_sample_progress(self, done: int, total: int) -> None:
        from gui.progress_util import format_eta
        self._progress.setValue(int(100 * done / max(total, 1)))
        self._status_lbl.setText(
            "Querying terrain elevation… "
            + format_eta(self._timerx.elapsed(), done, total))

    def _on_sample_finished(self, s, z) -> None:
        p = self._profile
        self._set_busy(False)
        self._progress.setValue(100)
        self._progress.hide()
        if p is None:
            return

        p.s = np.asarray(s, dtype=float)
        # None (DEM miss) becomes NaN so the arrays stay a plain float dtype;
        # rebuild_profile bridges the gaps.
        p.z_raw = np.array(
            [np.nan if v is None else float(v) for v in z], dtype=float)
        p.geom_fingerprint = vprof.geom_fingerprint(self._elements)

        n_bad = int(np.count_nonzero(~np.isfinite(p.z_raw)))
        self._status_lbl.setText(
            f"Sampled {len(p.s)} points"
            + (f" ({n_bad} without DEM data, bridged)." if n_bad else "."))
        self.log_message.emit(
            f"Terrain sampled along the fitted alignment: {len(p.s)} points "
            f"at {p.sample_interval:.0f} m"
            + (f", {n_bad} no-data." if n_bad else "."), "info")
        self._recompute()

    def _on_sample_failed(self, error: str) -> None:
        self._set_busy(False)
        self._progress.hide()
        self._status_lbl.setText(f"Sampling failed: {error}")
        self.log_message.emit(f"⚠ Terrain sampling failed: {error}", "warn")

    def _check_fingerprint(self) -> None:
        """Warn when the horizontal alignment moved after the DEM query.

        The samples are lat/lon anchored, so they no longer sit exactly on the
        centreline — but a radius tweak moves it by centimetres, and discarding
        a multi-minute query would be far worse than a warning.
        """
        p = self._profile
        if p is None or not p.has_samples:
            return
        live = vprof.geom_fingerprint(self._elements)
        if live != tuple(p.geom_fingerprint):
            self._status_lbl.setText(
                "⚠ The alignment changed after sampling — re-sample terrain "
                "for an exact profile.")
            sk.set_role(self._status_lbl, "warn")
            self.log_message.emit(
                "⚠ Vertical profile: the horizontal alignment changed after "
                "the terrain was sampled; the existing samples were kept.",
                "warn")

    # ------------------------------------------------------------------
    # Direct-connection table
    # ------------------------------------------------------------------

    def _connections(self) -> list:
        return self._profile.connections if self._profile is not None else []

    def _populate(self) -> None:
        self._populating = True
        try:
            self._table.setRowCount(0)
            for i, c in enumerate(self._connections()):
                row = self._table.rowCount()
                self._table.insertRow(row)

                for col, value in ((COL_START, c.s_start), (COL_END, c.s_end)):
                    item = QTableWidgetItem(f"{value / 1000.0:.3f}")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                    self._table.setItem(row, col, item)

                self._table.setItem(row, COL_LABEL,
                                    QTableWidgetItem(c.label or ""))

                btn = QPushButton("✕")
                btn.setFixedWidth(28)
                btn.setStyleSheet("font-size: 9px;")
                btn.clicked.connect(lambda _=False, idx=i: self._on_delete(idx))
                self._table.setCellWidget(row, COL_DEL, btn)
        finally:
            self._populating = False

    def _on_cell_changed(self, row: int, col: int) -> None:
        conns = self._connections()
        if self._populating or row >= len(conns):
            return
        c = conns[row]
        item = self._table.item(row, col)
        text = (item.text() if item is not None else "").strip()
        if col in (COL_START, COL_END):
            try:
                metres = max(0.0, float(text.replace(",", ".")) * 1000.0)
            except ValueError:
                self._populate()
                return
            if col == COL_START:
                c.s_start = metres
            else:
                c.s_end = metres
        elif col == COL_LABEL:
            c.label = text
        self._refresh_connections()

    def _refresh_connections(self) -> None:
        """Normalise, repopulate and recompute — one path for every edit."""
        p = self._profile
        if p is None:
            return
        if p.has_samples:
            p.connections = vprof.normalise_connections(
                p.connections, float(p.s[0]), float(p.s[-1]))
        self._populate()
        self._recompute()

    def _default_range(self) -> tuple[float, float]:
        p = self._profile
        if p is not None and p.has_samples:
            total = float(p.s[-1] - p.s[0])
            mid = float(p.s[0]) + total / 2.0
            half = min(250.0, max(total * 0.02, 10.0))
            return (mid - half, mid + half)
        return (0.0, 500.0)

    def _on_add_range(self) -> None:
        if self._profile is None:
            return
        a, b = self._default_range()
        self._profile.connections.append(vprof.DirectConnection(a, b, ""))
        self._refresh_connections()
        self._status_lbl.setText(
            "Range added — edit the km values in the table, or use "
            "“Pick on profile”.")

    def _on_delete(self, idx: int) -> None:
        conns = self._connections()
        if 0 <= idx < len(conns):
            conns.pop(idx)
            self._refresh_connections()

    def _on_pick_toggled(self, on: bool) -> None:
        self._pick_armed = bool(on)
        if self._canvas is not None:
            self._canvas.set_pick_mode(self._pick_armed)
        self.range_pick_mode.emit(self._pick_armed)
        if on:
            self._status_lbl.setText("Drag across the profile to set a range.")

    def _on_range_picked(self, s_start: float, s_end: float) -> None:
        if self._profile is None:
            return
        self._profile.connections.append(
            vprof.DirectConnection(float(s_start), float(s_end), ""))
        self._pick_btn.setChecked(False)
        self._refresh_connections()
        self._status_lbl.setText(
            f"Range {s_start / 1000.0:.3f}–{s_end / 1000.0:.3f} km added.")

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------

    def _on_confirm(self) -> None:
        p = self._profile
        if p is None or not p.pvis:
            return
        self._recompute(emit=False)
        st = p.stats
        self.log_message.emit(
            f"Vertical profile confirmed: {st['n_pvi']} PVIs, max gradient "
            f"{st['max_grade_pm']:.2f} ‰, max cut {st['max_cut']:.2f} m / "
            f"fill {st['max_fill']:.2f} m.", "info")
        self.profile_confirmed.emit(p)
