"""
COYPU-Feeder branding: programmatically drawn application icon and
splash/loading screen (no binary assets needed — everything is painted
with QPainter at startup).

The motif mirrors the app's element palette: a railway alignment running
through the tile as blue tangent → green transition spiral → red circular
curve, with sleepers and a PI marker. Those hues come from `gui.palette`, the
same source the map and the element table draw from.

The two surfaces are themed differently on purpose:

* the **icon** is a brand mark painted on `palette.BRAND`'s fixed tile — it
  ends up in the taskbar and title bar, outside the app's own theme;
* the **splash** is drawn inside the themed application, so its background,
  border and type colours come from the active theme tokens and it no longer
  flashes a dark panel when the app opens in light mode.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QPainterPath, QPen, QBrush, QColor,
    QFont, QLinearGradient,
)

from app_meta import APP_NAME, APP_VERSION, APP_TAGLINE  # noqa: F401

from gui.palette import ELEMENT_COLORS, MAP_COLORS, BRAND

_BLUE   = QColor(ELEMENT_COLORS["Line"])     # tangents
_GREEN  = QColor(ELEMENT_COLORS["Spiral"])   # transition spirals
_RED    = QColor(ELEMENT_COLORS["Arc"])      # circular curves


def _alignment_path(w: float, h: float) -> QPainterPath:
    """S-shaped 'alignment' curve used on both the icon and the splash."""
    p = QPainterPath(QPointF(0.10 * w, 0.82 * h))
    p.cubicTo(QPointF(0.38 * w, 0.82 * h),
              QPointF(0.40 * w, 0.55 * h),
              QPointF(0.52 * w, 0.44 * h))
    p.cubicTo(QPointF(0.64 * w, 0.33 * h),
              QPointF(0.66 * w, 0.18 * h),
              QPointF(0.92 * w, 0.16 * h))
    return p


def _paint_alignment(painter: QPainter, w: float, h: float,
                     line_width: float, ink: QColor | None = None) -> None:
    """Draw the tri-colour alignment with sleepers + a PI marker.

    `ink` is the colour the sleepers and the dashed virtual tangents are
    tinted with — white on the icon's dark tile, the theme's text colour on
    the splash so they stay visible in a light theme.
    """
    ink = QColor(ink) if ink is not None else QColor(255, 255, 255)
    path = _alignment_path(w, h)

    # Sleepers under the track
    sleeper_col = QColor(ink)
    sleeper_col.setAlpha(46)
    sleeper_pen = QPen(sleeper_col, line_width * 0.55)
    sleeper_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(sleeper_pen)
    n_sleepers = 13
    for i in range(n_sleepers + 1):
        pct = i / n_sleepers
        pos = path.pointAtPercent(pct)
        ang = path.angleAtPercent(pct)
        painter.save()
        painter.translate(pos)
        painter.rotate(-ang + 90)
        half = line_width * 1.35
        painter.drawLine(QPointF(0, -half), QPointF(0, half))
        painter.restore()

    # Tri-colour track: tangent (blue) → spiral (green) → arc (red) → …
    spans = [(0.00, 0.30, _BLUE), (0.30, 0.46, _GREEN),
             (0.46, 0.72, _RED),  (0.72, 0.84, _GREEN),
             (0.84, 1.00, _BLUE)]
    for f0, f1, col in spans:
        seg = QPainterPath()
        steps = max(6, int((f1 - f0) * 60))
        seg.moveTo(path.pointAtPercent(f0))
        for i in range(1, steps + 1):
            seg.lineTo(path.pointAtPercent(f0 + (f1 - f0) * i / steps))
        pen = QPen(col, line_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(seg)

    # PI marker: dashed virtual tangents meeting at a point above the curve
    pi_pt = QPointF(0.60 * w, 0.62 * h)
    t0 = path.pointAtPercent(0.30)
    t1 = path.pointAtPercent(0.84)
    dash_col = QColor(ink)
    dash_col.setAlpha(90)
    dash_pen = QPen(dash_col, line_width * 0.30)
    dash_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(dash_pen)
    painter.drawLine(t0, pi_pt)
    painter.drawLine(t1, pi_pt)
    painter.setPen(QPen(QColor(MAP_COLORS["piBorder"]), line_width * 0.35))
    painter.setBrush(QBrush(QColor(MAP_COLORS["piFill"])))
    r = line_width * 0.75
    painter.drawEllipse(pi_pt, r, r)


def make_app_icon() -> QIcon:
    """Window / taskbar icon (rendered at several sizes).

    Painted on the fixed brand tile, not the active theme — see the module
    docstring.
    """
    icon = QIcon()
    bg_top = QColor(BRAND["bgTop"])
    bg_bot = QColor(BRAND["bgBot"])
    accent = QColor(BRAND["accent"])
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        grad = QLinearGradient(0, 0, 0, size)
        grad.setColorAt(0.0, bg_top)
        grad.setColorAt(1.0, bg_bot)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(accent, max(1.0, size * 0.03)))
        radius = size * 0.20
        painter.drawRoundedRect(
            QRectF(size * 0.02, size * 0.02, size * 0.96, size * 0.96),
            radius, radius)

        _paint_alignment(painter, size, size, max(1.5, size * 0.075))
        painter.end()
        icon.addPixmap(pm)
    return icon


def make_splash_pixmap() -> QPixmap:
    """Loading-screen pixmap (520 × 320), painted in the active theme."""
    from gui.theme import current_tokens
    t = current_tokens()

    w, h = 520, 320
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0.0, QColor(t["window"]))
    grad.setColorAt(1.0, QColor(t["base"]))
    painter.setBrush(QBrush(grad))
    painter.setPen(QPen(QColor(t["highlight"]), 2))
    painter.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 3, 3)

    # Alignment motif in the lower-right area
    painter.save()
    painter.translate(w * 0.30, h * 0.18)
    painter.setOpacity(0.9)
    _paint_alignment(painter, w * 0.66, h * 0.72, 9.0, QColor(t["text"]))
    painter.restore()

    # Titles — family is left to the application font (COYPU sets none).
    base = painter.font()
    painter.setPen(QColor(t["text"]))
    title_font = QFont(base)
    title_font.setPointSize(26)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(QRectF(28, 26, w - 56, 48),
                     Qt.AlignmentFlag.AlignLeft, APP_NAME)

    tagline_font = QFont(base)
    tagline_font.setPointSize(11)
    painter.setPen(QColor(t["highlight"]))
    painter.setFont(tagline_font)
    painter.drawText(QRectF(30, 78, w - 60, 24),
                     Qt.AlignmentFlag.AlignLeft, APP_TAGLINE)

    footer_font = QFont(base)
    footer_font.setPointSize(9)
    painter.setPen(QColor(t["disabledText"]))
    painter.setFont(footer_font)
    painter.drawText(QRectF(30, h - 34, w - 60, 20),
                     Qt.AlignmentFlag.AlignLeft,
                     f"v{APP_VERSION} · data © OpenStreetMap contributors")
    painter.end()
    return pm
