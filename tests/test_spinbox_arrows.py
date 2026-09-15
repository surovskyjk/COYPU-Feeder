"""
Part 2C — QSpinBox/QDoubleSpinBox step-arrow visibility (Req 8).

Styling a QSpinBox/QDoubleSpinBox at all (the app-wide QSS rule in
theme_manager.py already did, for the border/background/padding) hands its
subcontrol layout to QStyleSheetStyle — without an explicit
::up-button/::down-button rect the default one collides with that padding
and the arrows end up squeezed behind the text or entirely unclickable.
This asserts the generated stylesheet actually reserves and draws them.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _build_stylesheet():
    from gui.theme_manager import ThemeManager
    return ThemeManager().buildStyleSheet()


class TestSpinBoxSubcontrolRules:

    def test_stylesheet_declares_up_and_down_buttons(self):
        css = _build_stylesheet()
        assert "QSpinBox::up-button" in css
        assert "QDoubleSpinBox::up-button" in css
        assert "QSpinBox::down-button" in css
        assert "QDoubleSpinBox::down-button" in css

    def test_stylesheet_declares_visible_arrow_glyphs(self):
        css = _build_stylesheet()
        assert "QSpinBox::up-arrow" in css
        assert "QDoubleSpinBox::up-arrow" in css
        assert "QSpinBox::down-arrow" in css
        assert "QDoubleSpinBox::down-arrow" in css
        # `image: none` + a CSS-triangle border trick — not left to the
        # (now-bypassed) platform style's default arrow pixmap.
        assert "image: none" in css

    def test_padding_leaves_room_for_the_buttons(self):
        css = _build_stylesheet()
        assert "padding-right" in css

    def test_button_rects_are_explicitly_positioned(self):
        css = _build_stylesheet()
        assert "subcontrol-position: top right" in css
        assert "subcontrol-position: bottom right" in css
