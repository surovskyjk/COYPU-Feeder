"""
Small procedurally-drawn icons for the main toolbar's domain-specific
actions (show alignment, merge selection, undo merge, undo/redo the
edit history, edit mode) — the ones with no good `QStyle.standardIcon`
equivalent. File actions (New/Open/Save) use QStyle's own icons instead
(see app.py `_build_toolbar`), which already track the active OS theme.

Drawn at runtime with QPainter rather than bundled as SVG/PNG assets: no
new files to ship, and the stroke colour is picked from the current
palette so the icons stay legible after a light/dark theme switch (call
`make_icon` again after a theme change if you want them refreshed — the
toolbar actions are rebuilt once at startup, so this matters only if a
future caller creates icons dynamically mid-session).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QColor, QPixmap

_SIZE = 20


def _new_painter(color: QColor) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    return pm, p


def _draw_show_alignment(p: QPainter, color: QColor):
    """A little zigzag alignment with a highlighted vertex — 'show on map'."""
    path = QPainterPath()
    path.moveTo(3, 15)
    path.lineTo(8, 8)
    path.lineTo(12, 12)
    path.lineTo(17, 5)
    p.drawPath(path)
    p.setBrush(color)
    p.drawEllipse(QPointF(12, 12), 2.0, 2.0)


def _draw_merge(p: QPainter, color: QColor):
    """Two paths converging into one — PI-range merge."""
    p.drawLine(QPointF(3, 4), QPointF(11, 10))
    p.drawLine(QPointF(3, 16), QPointF(11, 10))
    p.drawLine(QPointF(11, 10), QPointF(17, 10))
    path = QPainterPath()
    path.moveTo(14, 7)
    path.lineTo(17, 10)
    path.lineTo(14, 13)
    p.drawPath(path)


def _draw_undo(p: QPainter, color: QColor):
    """Counter-clockwise hook arrow — classic undo glyph."""
    rect = QRectF(4, 5, 11, 11)
    p.drawArc(rect, 20 * 16, 280 * 16)
    path = QPainterPath()
    path.moveTo(3, 8)
    path.lineTo(4.5, 3.5)
    path.lineTo(8, 6.5)
    path.closeSubpath()
    p.setBrush(color)
    p.drawPath(path)


def _draw_redo(p: QPainter, color: QColor):
    """Clockwise hook arrow — a horizontal mirror of _draw_undo, for Redo."""
    p.save()
    p.translate(_SIZE, 0)
    p.scale(-1, 1)
    _draw_undo(p, color)
    p.restore()


def _draw_edit(p: QPainter, color: QColor):
    """A simple pencil — edit mode (drag PIs)."""
    p.drawLine(QPointF(4, 16), QPointF(13, 7))
    path = QPainterPath()
    path.moveTo(13, 7)
    path.lineTo(16, 4)
    path.lineTo(17.5, 5.5)
    path.lineTo(14.5, 8.5)
    path.closeSubpath()
    p.setBrush(color)
    p.drawPath(path)
    p.drawLine(QPointF(3, 17), QPointF(5, 15))


_DRAWERS = {
    "show_alignment": _draw_show_alignment,
    "merge":          _draw_merge,
    "undo":           _draw_undo,
    "redo":           _draw_redo,
    "edit":           _draw_edit,
}


def make_icon(name: str, color: QColor) -> QIcon:
    drawer = _DRAWERS.get(name)
    if drawer is None:
        return QIcon()
    pm, p = _new_painter(color)
    drawer(p, color)
    p.end()
    return QIcon(pm)
