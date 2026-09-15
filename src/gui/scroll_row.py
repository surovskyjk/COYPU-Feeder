"""
Horizontally-scrollable row helper — the toolbar-overflow pattern (Part C)
generalised so more than one place can use it: the main toolbar (app.py)
and the element table dock's header row (Req 7, 1080p clipping) both pack
more content than fits at a narrow window width, and both want the same
fix — a real horizontal scrollbar with small `<`/`>` buttons that only
appear once there's something to scroll to, instead of Qt's silent "»"
overflow chevron or (for a plain QHBoxLayout) content simply clipping.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QScrollArea, QToolButton, QWidget,
)


class ScrollableRow(QScrollArea):
    """A plain QScrollArea only reacts to wheel events along the axis the
    wheel itself reports; there's nothing to scroll vertically in a
    single-row bar, so any wheel tick over it (Shift-held or not) pans
    horizontally instead of doing nothing."""

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)


def mount_horizontal_scroll(content: QWidget, parent=None,
                            fixed_height: int | None = None) -> QWidget:
    """Wrap `content` in a `ScrollableRow` flanked by `<`/`>` buttons that
    only show while `content` overflows the available width. Returns the
    assembled row widget — insert it wherever `content` itself would have
    gone; `content` is reparented into the scroll area."""
    row = QWidget(parent)
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

    scroll = ScrollableRow(row)
    scroll.setWidget(content)
    scroll.setWidgetResizable(False)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    if fixed_height is None:
        fixed_height = content.sizeHint().height()
    scroll.setFixedHeight(fixed_height)

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

    row._scroll_area = scroll   # kept reachable for callers that need it
    return row
