"""
COYPU-Feeder branding: programmatically drawn application icon and
splash/loading screen (no binary assets needed — everything is painted
with QPainter at startup).

The motif mirrors the app's element palette: a railway alignment running
through the tile as blue tangent → green transition spiral → red circular
curve, with sleepers and a PI marker.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QPainterPath, QPen, QBrush, QColor,
    QFont, QLinearGradient,
)

APP_NAME    = "COYPU Feeder"
APP_TAGLINE = "OSM railways → LandXML alignments"
APP_VERSION = "1.0.0"

_BLUE   = QColor("#42a5f5")   # tangents
_GREEN  = QColor("#66bb6a")   # transition spirals
_RED    = QColor("#ef5350")   # circular curves
_ACCENT = QColor("#2a82da")
_BG_TOP = QColor("#20242d")
_BG_BOT = QColor("#14161c")


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
                     line_width: float) -> None:
    """Draw the tri-colour alignment with sleepers + a PI marker."""
    path = _alignment_path(w, h)
    total = path.length()

    # Sleepers under the track
    sleeper_pen = QPen(QColor(255, 255, 255, 46), line_width * 0.55)
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
    dash_pen = QPen(QColor(255, 255, 255, 90), line_width * 0.30)
    dash_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(dash_pen)
    painter.drawLine(t0, pi_pt)
    painter.drawLine(t1, pi_pt)
    painter.setPen(QPen(QColor("#37474f"), line_width * 0.35))
    painter.setBrush(QBrush(QColor("#ffffff")))
    r = line_width * 0.75
    painter.drawEllipse(pi_pt, r, r)


def make_app_icon() -> QIcon:
    """Window / taskbar icon (rendered at several sizes)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        grad = QLinearGradient(0, 0, 0, size)
        grad.setColorAt(0.0, _BG_TOP)
        grad.setColorAt(1.0, _BG_BOT)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(_ACCENT, max(1.0, size * 0.03)))
        radius = size * 0.20
        painter.drawRoundedRect(
            QRectF(size * 0.02, size * 0.02, size * 0.96, size * 0.96),
            radius, radius)

        _paint_alignment(painter, size, size, max(1.5, size * 0.075))
        painter.end()
        icon.addPixmap(pm)
    return icon


def make_splash_pixmap() -> QPixmap:
    """Loading-screen pixmap (520 × 320)."""
    w, h = 520, 320
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0.0, _BG_TOP)
    grad.setColorAt(1.0, _BG_BOT)
    painter.setBrush(QBrush(grad))
    painter.setPen(QPen(_ACCENT, 2))
    painter.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 14, 14)

    # Alignment motif in the lower-right area
    painter.save()
    painter.translate(w * 0.30, h * 0.18)
    painter.setOpacity(0.9)
    _paint_alignment(painter, w * 0.66, h * 0.72, 9.0)
    painter.restore()

    # Titles
    painter.setPen(QColor("#eceff1"))
    f = QFont("Segoe UI", 26, QFont.Weight.Bold)
    painter.setFont(f)
    painter.drawText(QRectF(28, 26, w - 56, 48),
                     Qt.AlignmentFlag.AlignLeft, APP_NAME)
    painter.setPen(_ACCENT)
    painter.setFont(QFont("Segoe UI", 11))
    painter.drawText(QRectF(30, 78, w - 60, 24),
                     Qt.AlignmentFlag.AlignLeft, APP_TAGLINE)
    painter.setPen(QColor("#78909c"))
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(QRectF(30, h - 34, w - 60, 20),
                     Qt.AlignmentFlag.AlignLeft,
                     f"v{APP_VERSION} · data © OpenStreetMap contributors")
    painter.end()
    return pm
