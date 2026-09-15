"""
Part 4 — responsive layout at 1920x1080 (Req 7).

- Element table columns: Interactive resize (not ResizeToContents, which
  ignores setCellWidget spinboxes entirely and can't shrink) + real
  horizontal scrolling.
- Element table dock header: wrapped in a horizontally-scrollable row
  (`gui.scroll_row`) instead of a plain non-wrapping QHBoxLayout; the wide
  hint label was demoted to a tooltip on the title.
- Step 5 panel: wrapped in a QScrollArea so Back/Accept stay reachable when
  the content (info blurb + metrics + two collapsible sections) is taller
  than the window leaves room for.
- Batch-spiral form: WrapLongRows + shortened combo labels so a QFormLayout
  row doesn't force the fixed-width step panel wider than it has.
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


class TestElementTableColumns:

    def _table(self):
        from gui.element_table import ElementTableDock
        return ElementTableDock()

    def test_columns_are_interactive_not_resize_to_contents(self):
        from PySide6.QtWidgets import QHeaderView
        from gui.element_table import COL_ID, COL_ACT
        table = self._table()
        hdr = table._table.horizontalHeader()
        assert hdr.sectionResizeMode(COL_ID) == QHeaderView.ResizeMode.Interactive
        # COL_ACT keeps stretching to fill remaining space, as before.
        assert hdr.sectionResizeMode(COL_ACT) == QHeaderView.ResizeMode.Stretch

    def test_columns_have_sane_default_widths(self):
        from gui.element_table import COL_ID, COL_TYPE, COL_R, COL_SL
        table = self._table()
        for col in (COL_ID, COL_TYPE, COL_R, COL_SL):
            assert table._table.columnWidth(col) > 0

    def test_horizontal_scroll_is_per_pixel(self):
        from PySide6.QtWidgets import QAbstractItemView
        table = self._table()
        assert (table._table.horizontalScrollMode()
               == QAbstractItemView.ScrollMode.ScrollPerPixel)

    def test_radius_spinbox_has_a_minimum_width(self):
        """ResizeToContents used to be the only thing sizing this column —
        Interactive mode needs the spinbox to demand its own minimum."""
        from test_edit_history import _make_model
        table = self._table()
        table.prepare(_make_model(), _make_model().elements)
        found = False
        for r in range(table._table.rowCount()):
            from gui.element_table import COL_R
            w = table._table.cellWidget(r, COL_R)
            if w is not None:
                assert w.minimumWidth() >= 60
                found = True
        assert found, "expected at least one radius spinbox"


class TestElementTableHeaderScrollable:

    def test_hint_label_no_longer_exists_as_a_widget(self):
        """Demoted to the title's tooltip (Req 7) — must not still be a
        wide standalone QLabel eating horizontal space."""
        from gui.element_table import ElementTableDock
        table = ElementTableDock()
        assert not hasattr(table, "_hint_lbl")

    def test_title_carries_the_hint_as_a_tooltip(self):
        from gui.element_table import ElementTableDock
        table = ElementTableDock()
        assert "Radius" in table._title.toolTip()

    def test_header_row_is_wrapped_in_a_scroll_area(self):
        from gui.element_table import ElementTableDock
        from gui.scroll_row import ScrollableRow
        table = ElementTableDock()
        found = table.findChild(ScrollableRow)
        assert found is not None

    def test_prepare_updates_tooltip_for_read_only_mode(self):
        from gui.element_table import ElementTableDock
        from test_edit_history import _make_model
        table = ElementTableDock()
        table.prepare(None, _make_model().elements)
        assert "read-only" in table._title.toolTip().lower()


class TestStep5ScrollArea:

    def test_back_and_accept_are_outside_the_scroll_area(self):
        from PySide6.QtWidgets import QScrollArea
        from gui.steps.step5_design import Step5Design
        panel = Step5Design()
        scroll = panel.findChild(QScrollArea)
        assert scroll is not None
        # Back/Accept must NOT be descendants of the scroll area's viewport
        # — they need to stay visible regardless of scroll position.
        assert not _is_descendant(panel._back_btn, scroll)
        assert not _is_descendant(panel._accept_btn, scroll)

    def test_scrollable_content_contains_the_info_and_metrics(self):
        from PySide6.QtWidgets import QScrollArea
        from gui.steps.step5_design import Step5Design
        panel = Step5Design()
        scroll = panel.findChild(QScrollArea)
        assert _is_descendant(panel._metrics_lbl, scroll)
        assert _is_descendant(panel._title_lbl, scroll)


def _is_descendant(widget, ancestor) -> bool:
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = w.parentWidget()
    return False


class TestBatchSpiralFormWrapping:

    def test_rules_form_wraps_long_rows(self):
        from PySide6.QtWidgets import QFormLayout
        from gui.steps.step5_design import Step5Design
        panel = Step5Design()
        forms = panel.findChildren(QFormLayout)
        assert forms
        assert all(f.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
                   for f in forms)

    def test_batch_mode_combo_labels_are_short(self):
        from gui.steps.step5_design import Step5Design
        panel = Step5Design()
        texts = [panel._batch_mode_combo.itemText(i)
                for i in range(panel._batch_mode_combo.count())]
        assert all(len(t) <= 20 for t in texts), texts
