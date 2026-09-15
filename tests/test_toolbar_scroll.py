"""
Part C — the main toolbar's horizontally-scrollable host (`_ToolbarScrollArea`,
`App._mount_toolbar_scroll`), which replaces QMainWindow's native toolbar area
(whose response to an overflowing toolbar is to clip actions behind a bare
"»" chevron with no way to reach them by scrolling).

`App()` itself can't be constructed offscreen (QWebEngineView inside
MapWidget segfaults offscreen — a pre-existing, already-confirmed environment
limitation, unrelated to this feature), so these tests exercise the scroll
mechanism in isolation: a bare QToolBar with enough wide buttons to overflow
a narrow QScrollArea, wrapped exactly the way `_mount_toolbar_scroll` does.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QToolBar, QToolButton  # noqa: E402

from gui.app import _ToolbarScrollArea  # noqa: E402


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _wide_toolbar(n_buttons: int = 12) -> QToolBar:
    tb = QToolBar("Main")
    for i in range(n_buttons):
        btn = QToolButton()
        btn.setText(f"Action {i}")
        btn.setMinimumWidth(80)
        tb.addWidget(btn)
    return tb


class TestToolbarScrollOverflow:

    def test_narrow_viewport_reports_scrollable_overflow(self):
        tb = _wide_toolbar()
        scroll = _ToolbarScrollArea()
        scroll.setWidget(tb)
        scroll.setWidgetResizable(False)
        scroll.resize(150, 60)   # far narrower than 12 * 80px of buttons
        scroll.show()
        assert scroll.horizontalScrollBar().maximum() > 0

    def test_wide_viewport_no_overflow(self):
        tb = _wide_toolbar(n_buttons=2)
        scroll = _ToolbarScrollArea()
        scroll.setWidget(tb)
        scroll.setWidgetResizable(False)
        scroll.resize(2000, 60)   # comfortably wider than 2 buttons
        scroll.show()
        assert scroll.horizontalScrollBar().maximum() == 0

    def test_widget_resizable_false_keeps_toolbar_natural_width(self):
        """The whole fix hinges on this: a resizable-to-viewport child would
        shrink instead of overflow, and QToolBar's own native "»" chevron
        would kick back in — silently reproducing the bug this replaces."""
        tb = _wide_toolbar()
        natural_width = tb.sizeHint().width()
        scroll = _ToolbarScrollArea()
        scroll.setWidget(tb)
        scroll.setWidgetResizable(False)
        scroll.resize(150, 60)
        scroll.show()
        assert tb.width() >= natural_width


class TestToolbarScrollWheel:

    def test_vertical_wheel_scrolls_horizontally(self):
        tb = _wide_toolbar()
        scroll = _ToolbarScrollArea()
        scroll.setWidget(tb)
        scroll.setWidgetResizable(False)
        scroll.resize(150, 60)
        scroll.show()
        bar = scroll.horizontalScrollBar()
        assert bar.maximum() > 0
        start = bar.value()
        ev = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        scroll.wheelEvent(ev)
        assert bar.value() != start
