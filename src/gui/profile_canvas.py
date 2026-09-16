"""
Longitudinal profile canvas — chainage [km] against elevation [m a.s.l.].

Three series are drawn over the same axes:

* the raw sampled terrain (muted),
* the manual direct-connection spans (highlighted connector lines),
* the smoothed design profile (bold accent).

Unlike the map, this is a plain ``QWidget`` with a ``paintEvent`` — no
QWebEngineView — so it renders offscreen and is directly testable.  All chrome
comes from the ``plotBackground`` / ``plotForeground`` / ``plotGrid`` theme
tokens, which ``theme_manager`` has always carried but nothing used until now.

A long line is a lot of points (135 km at 20 m is ~6 750 per series), so the
polylines are decimated to roughly the pixel width before they are built —
drawing sub-pixel detail costs time and shows nothing.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from gui import palette
from gui.style_kit import ThemedPanel
from gui.theme import current_tokens

# Plot margins: the left gutter holds elevation labels, the bottom one holds
# chainage labels.
M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 62, 14, 12, 30

# Points per pixel of width above which the series is strided before drawing.
DRAW_DENSITY = 4.0


def _nice_step(span: float, target_ticks: int) -> float:
    """A 1 / 2 / 5 x 10^n step covering *span* in about *target_ticks*."""
    if span <= 0 or target_ticks <= 0:
        return 1.0
    raw = span / target_ticks
    mag = 10.0 ** np.floor(np.log10(raw)) if raw > 0 else 1.0
    for mult in (1.0, 2.0, 5.0, 10.0):
        if raw <= mult * mag:
            return mult * mag
    return 10.0 * mag


class ProfileCanvas(ThemedPanel, QWidget):
    """
    Interactive vertical-profile viewport.

    Signals
    -------
    range_picked(float, float)
        A start/end chainage pair (metres) dragged out while pick mode is
        armed — feeds the direct-connection table.
    hover_changed(float)
        Chainage under the cursor, or -1 when the cursor leaves the plot.
    """

    range_picked   = Signal(float, float)
    hover_changed  = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = None
        self._tokens: dict = {}
        self._view: tuple[float, float] | None = None    # visible (s0, s1)
        self._hover_x: float | None = None
        self._pick_mode = False
        self._drag_from: float | None = None
        self._drag_to:   float | None = None
        self._panning = False
        self._pan_anchor: tuple[float, float] | None = None

        self.setMinimumHeight(140)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_profile(self, profile) -> None:
        """Bind (or rebind) the profile to draw. Never copies — the step owns
        the object and mutates it in place, exactly as Step 5 does with the
        shared PI model."""
        reset_view = (
            self._profile is None
            or profile is None
            or len(getattr(profile, "s", ())) != len(getattr(self._profile, "s", ()))
        )
        self._profile = profile
        if reset_view:
            self._view = None
        self.update()

    def clear(self) -> None:
        self._profile = None
        self._view = None
        self._hover_x = None
        self._drag_from = self._drag_to = None
        self.update()

    def set_pick_mode(self, on: bool) -> None:
        """Arm click-drag range picking (mirrors the map's click-to-place arm
        in Step 6)."""
        self._pick_mode = bool(on)
        self._drag_from = self._drag_to = None
        self.setCursor(Qt.CursorShape.CrossCursor if on
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def reset_view(self) -> None:
        self._view = None
        self.update()

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        self._tokens = tokens or current_tokens()
        super().applyTheme(isDark, self._tokens)
        self.update()

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _tok(self) -> dict:
        return self._tokens or current_tokens()

    def _plot_rect(self) -> QRectF:
        return QRectF(M_LEFT, M_TOP,
                      max(1.0, self.width() - M_LEFT - M_RIGHT),
                      max(1.0, self.height() - M_TOP - M_BOTTOM))

    def _has_data(self) -> bool:
        p = self._profile
        return p is not None and len(getattr(p, "s", ())) >= 2

    def _s_range(self) -> tuple[float, float]:
        p = self._profile
        s = np.asarray(p.s, dtype=float)
        full = (float(s[0]), float(s[-1]))
        if self._view is None:
            return full
        v0, v1 = self._view
        if v1 - v0 < 1e-6:
            return full
        return (max(v0, full[0]), min(v1, full[1]))

    def _z_range(self, s0: float, s1: float) -> tuple[float, float]:
        """Auto-fit elevation over the *visible* chainage window."""
        p = self._profile
        s = np.asarray(p.s, dtype=float)
        m = (s >= s0) & (s <= s1)
        if not m.any():
            m = np.ones(len(s), dtype=bool)

        vals = []
        for name in ("z_raw", "z_override", "z_design"):
            arr = np.asarray(getattr(p, name, ()), dtype=float)
            if len(arr) == len(s):
                sub = arr[m]
                sub = sub[np.isfinite(sub)]
                if len(sub):
                    vals.append(sub)
        if not vals:
            return (0.0, 1.0)

        allv = np.concatenate(vals)
        lo, hi = float(np.min(allv)), float(np.max(allv))
        if hi - lo < 1e-6:
            lo, hi = lo - 1.0, hi + 1.0
        pad = (hi - lo) * 0.08
        return (lo - pad, hi + pad)

    def _stride(self, n_visible: int) -> int:
        limit = max(1.0, self._plot_rect().width() * DRAW_DENSITY)
        return max(1, int(np.ceil(n_visible / limit)))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        t = self._tok()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(t["plotBackground"]))

        rect = self._plot_rect()
        if not self._has_data():
            self._draw_placeholder(painter, t)
            painter.end()
            return

        s0, s1 = self._s_range()
        z0, z1 = self._z_range(s0, s1)
        if s1 - s0 < 1e-6:
            painter.end()
            return

        def sx(v):
            return rect.left() + (v - s0) / (s1 - s0) * rect.width()

        def sy(v):
            return rect.top() + (z1 - v) / (z1 - z0) * rect.height()

        self._draw_grid(painter, t, rect, s0, s1, z0, z1, sx, sy)
        self._draw_connection_bands(painter, t, rect, s0, s1, sx)
        self._draw_series(painter, t, rect, s0, s1, sx, sy)
        self._draw_drag_band(painter, t, rect, sx)
        painter.setClipping(False)
        self._draw_readout(painter, t, rect, s0, s1, sx, sy)
        painter.end()

    def _draw_placeholder(self, painter: QPainter, t: dict):
        painter.setPen(QPen(QColor(t["disabledText"])))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         "No terrain samples yet — run “Sample terrain”.")

    def _draw_grid(self, painter, t, rect, s0, s1, z0, z1, sx, sy):
        grid_pen = QPen(QColor(t["plotGrid"]), 1)
        grid_pen.setStyle(Qt.PenStyle.DotLine)
        text_col = QColor(t["plotForeground"])
        fm = painter.fontMetrics()

        # Chainage axis, labelled in km
        step = _nice_step(s1 - s0, 6)
        v = np.ceil(s0 / step) * step
        while v <= s1 + 1e-9:
            x = sx(v)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(text_col)
            label = f"{v / 1000.0:,.3f}".rstrip("0").rstrip(".") + " km"
            painter.drawText(QPointF(x - fm.horizontalAdvance(label) / 2.0,
                                     rect.bottom() + fm.ascent() + 6), label)
            v += step

        # Elevation axis
        step = _nice_step(z1 - z0, 5)
        v = np.ceil(z0 / step) * step
        while v <= z1 + 1e-9:
            y = sy(v)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(text_col)
            label = f"{v:,.0f}"
            painter.drawText(
                QPointF(rect.left() - 8 - fm.horizontalAdvance(label),
                        y + fm.ascent() / 2.0 - 1), label)
            v += step

        painter.setPen(QPen(QColor(t["border"]), 1))
        painter.drawRect(rect)

    def _draw_connection_bands(self, painter, t, rect, s0, s1, sx):
        p = self._profile
        band = QColor(palette.WARNING)
        band.setAlpha(34)
        for c in getattr(p, "connections", ()) or ():
            a, b = float(c.s_start), float(c.s_end)
            if b < s0 or a > s1:
                continue
            x0, x1 = sx(max(a, s0)), sx(min(b, s1))
            painter.fillRect(QRectF(x0, rect.top(), max(1.0, x1 - x0),
                                    rect.height()), band)

    def _polyline(self, s, z, mask, stride, sx, sy) -> QPolygonF:
        sv = s[mask][::stride]
        zv = z[mask][::stride]
        poly = QPolygonF()
        for a, b in zip(sv, zv):
            if np.isfinite(b):
                poly.append(QPointF(sx(a), sy(b)))
        return poly

    def _draw_series(self, painter, t, rect, s0, s1, sx, sy):
        p = self._profile
        s = np.asarray(p.s, dtype=float)
        # One sample of margin each side so the line reaches the frame edge.
        m = (s >= s0) & (s <= s1)
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            return
        lo = max(0, idx[0] - 1)
        hi = min(len(s) - 1, idx[-1] + 1)
        m = np.zeros(len(s), dtype=bool)
        m[lo:hi + 1] = True
        stride = self._stride(int(m.sum()))

        painter.setClipRect(rect)

        z_raw = np.asarray(getattr(p, "z_raw", ()), dtype=float)
        if len(z_raw) == len(s):
            painter.setPen(QPen(QColor(t["disabledText"]), 1.0))
            painter.drawPolyline(self._polyline(s, z_raw, m, stride, sx, sy))

        # The connector spans, drawn from the override series so the user sees
        # exactly the straight line they imposed.
        z_ovr = np.asarray(getattr(p, "z_override", ()), dtype=float)
        conns = getattr(p, "connections", ()) or ()
        if len(z_ovr) == len(s) and conns:
            pen = QPen(QColor(palette.WARNING), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            for c in conns:
                cm = m & (s >= float(c.s_start)) & (s <= float(c.s_end))
                if cm.sum() >= 2:
                    painter.drawPolyline(
                        self._polyline(s, z_ovr, cm, 1, sx, sy))

        z_des = np.asarray(getattr(p, "z_design", ()), dtype=float)
        if len(z_des) == len(s):
            pen = QPen(QColor(t["highlight"]), 2.0)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(self._polyline(s, z_des, m, stride, sx, sy))

    def _draw_drag_band(self, painter, t, rect, sx):
        if self._drag_from is None or self._drag_to is None:
            return
        a, b = sorted((self._drag_from, self._drag_to))
        col = QColor(t["highlight"])
        col.setAlpha(50)
        painter.fillRect(QRectF(sx(a), rect.top(), max(1.0, sx(b) - sx(a)),
                                rect.height()), col)

    def _draw_readout(self, painter, t, rect, s0, s1, sx, sy):
        if self._hover_x is None:
            return
        p = self._profile
        s = np.asarray(p.s, dtype=float)
        sv = float(np.clip(self._hover_x, s0, s1))

        pen = QPen(QColor(t["highlight"]), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(sx(sv), rect.top()),
                         QPointF(sx(sv), rect.bottom()))

        parts = [f"{sv / 1000.0:.3f} km"]
        z_raw = np.asarray(getattr(p, "z_raw", ()), dtype=float)
        if len(z_raw) == len(s):
            v = float(np.interp(sv, s, np.nan_to_num(z_raw, nan=np.nan)))
            if np.isfinite(v):
                parts.append(f"terrain {v:.1f} m")
        z_des = np.asarray(getattr(p, "z_design", ()), dtype=float)
        if len(z_des) == len(s):
            parts.append(f"design {float(np.interp(sv, s, z_des)):.1f} m")
            g = self._grade_at(sv)
            if g is not None:
                parts.append(f"{g:+.1f} ‰")

        text = "   ".join(parts)
        painter.setPen(QPen(QColor(t["plotForeground"])))
        painter.drawText(QPointF(rect.left() + 8,
                                 rect.top() + painter.fontMetrics().ascent() + 4),
                         text)

    def _grade_at(self, sv: float) -> float | None:
        """Design grade in per mille at chainage *sv*, read off the PVI
        tangents rather than the resampled series."""
        pvis = [e for e in (getattr(self._profile, "pvis", None) or [])
                if e.get("type") == "PVI"]
        if len(pvis) < 2:
            return None
        for a, b in zip(pvis[:-1], pvis[1:]):
            if a["station"] <= sv <= b["station"]:
                ds = b["station"] - a["station"]
                if ds <= 1e-9:
                    return None
                return (b["elevation"] - a["elevation"]) / ds * 1000.0
        return None

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _x_to_s(self, x: float) -> float:
        rect = self._plot_rect()
        s0, s1 = self._s_range()
        frac = (x - rect.left()) / max(rect.width(), 1.0)
        return s0 + float(np.clip(frac, 0.0, 1.0)) * (s1 - s0)

    def mouseMoveEvent(self, event):
        if not self._has_data():
            return
        pos = event.position()
        sv = self._x_to_s(pos.x())

        if self._panning and self._pan_anchor is not None:
            s0, s1 = self._s_range()
            rect = self._plot_rect()
            anchor_x, anchor_s = self._pan_anchor
            per_px = (s1 - s0) / max(rect.width(), 1.0)
            shift = (anchor_x - pos.x()) * per_px
            full = (float(self._profile.s[0]), float(self._profile.s[-1]))
            width = s1 - s0
            new0 = float(np.clip(anchor_s + shift - (anchor_x - rect.left()) * per_px,
                                 full[0], max(full[0], full[1] - width)))
            self._view = (new0, new0 + width)
            self.update()
            return

        self._hover_x = sv
        if self._drag_from is not None:
            self._drag_to = sv
        self.hover_changed.emit(sv)
        self.update()

    def leaveEvent(self, event):
        self._hover_x = None
        self.hover_changed.emit(-1.0)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if not self._has_data():
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        sv = self._x_to_s(event.position().x())
        if self._pick_mode:
            self._drag_from = sv
            self._drag_to = sv
        else:
            self._panning = True
            self._pan_anchor = (event.position().x(), self._s_range()[0])
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._panning:
            self._panning = False
            self._pan_anchor = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._drag_from is None:
            return
        a, b = sorted((self._drag_from, self._drag_to if self._drag_to
                       is not None else self._drag_from))
        self._drag_from = self._drag_to = None
        self.update()
        if b - a > 1.0:                      # ignore an accidental click
            self.range_picked.emit(float(a), float(b))

    def wheelEvent(self, event):
        """Zoom the chainage axis about the cursor."""
        if not self._has_data():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s0, s1 = self._s_range()
        full = (float(self._profile.s[0]), float(self._profile.s[-1]))
        pivot = self._x_to_s(event.position().x())
        factor = 0.8 if delta > 0 else 1.25
        width = min((s1 - s0) * factor, full[1] - full[0])
        if width < 50.0:                     # never zoom past ~50 m of line
            width = 50.0
        frac = (pivot - s0) / max(s1 - s0, 1e-9)
        new0 = pivot - frac * width
        new0 = float(np.clip(new0, full[0], max(full[0], full[1] - width)))
        self._view = (new0, new0 + width)
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.reset_view()
