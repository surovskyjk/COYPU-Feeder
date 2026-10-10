"""
Small layout helpers shared by the step panels.

These grew up inside ``step6_crosssection.py``; the vertical-profile step needs
the same label-plus-control rows and read-out rows, so they live here rather
than being copy-pasted a second time. The 130 px label column and the 26 px
control height are what keep every step panel's controls on the same grid
inside the fixed 340 px column.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QSpinBox, QVBoxLayout,
)

from gui import style_kit as sk

LABEL_WIDTH = 130
CONTROL_HEIGHT = 26


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    sk.set_role(f, "separator")
    return f


def _row_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFixedWidth(LABEL_WIDTH)
    lbl.setStyleSheet("font-size: 11px;")
    return lbl


def _labeled_spin(
    layout: QVBoxLayout,
    label: str,
    min_v: float,
    max_v: float,
    default: float,
    suffix: str,
    tooltip: str = "",
    decimals: int = 1,
    step: float | None = None,
) -> QDoubleSpinBox:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(_row_label(label))

    spin = QDoubleSpinBox()
    spin.setRange(min_v, max_v)
    spin.setDecimals(decimals)
    spin.setValue(default)
    spin.setSuffix(suffix)
    spin.setFixedHeight(CONTROL_HEIGHT)
    if step is not None:
        spin.setSingleStep(step)
    # Keyboard tracking off: typing "2" on the way to "200" must not fire a
    # recompute with the intermediate value (same rule as the element table).
    spin.setKeyboardTracking(False)
    if tooltip:
        spin.setToolTip(tooltip)
    row.addWidget(spin)
    row.addStretch()

    layout.addLayout(row)
    return spin


def _labeled_int_spin(
    layout: QVBoxLayout,
    label: str,
    min_v: int,
    max_v: int,
    default: int,
    suffix: str = "",
    tooltip: str = "",
) -> QSpinBox:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(_row_label(label))

    spin = QSpinBox()
    spin.setRange(min_v, max_v)
    spin.setValue(default)
    if suffix:
        spin.setSuffix(suffix)
    spin.setFixedHeight(CONTROL_HEIGHT)
    spin.setKeyboardTracking(False)
    if tooltip:
        spin.setToolTip(tooltip)
    row.addWidget(spin)
    row.addStretch()

    layout.addLayout(row)
    return spin


def _labeled_combo(
    layout: QVBoxLayout,
    label: str,
    items: list[str],
    tooltip: str = "",
) -> QComboBox:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(_row_label(label))

    combo = QComboBox()
    combo.addItems(items)
    combo.setFixedHeight(CONTROL_HEIGHT)
    if tooltip:
        combo.setToolTip(tooltip)
    row.addWidget(combo)
    row.addStretch()

    layout.addLayout(row)
    return combo


def _labeled_check(
    layout: QVBoxLayout,
    text: str,
    checked: bool = True,
    tooltip: str = "",
) -> QCheckBox:
    chk = QCheckBox(text)
    chk.setChecked(checked)
    chk.setStyleSheet("font-size: 11px;")
    if tooltip:
        chk.setToolTip(tooltip)
    layout.addWidget(chk)
    return chk


def _stat_row(layout: QVBoxLayout, label: str) -> QLabel:
    """Add a two-column (label : value) row; return the value label."""
    row = QHBoxLayout()
    row.setSpacing(6)

    lbl = QLabel(label)
    sk.set_role(lbl, "muted", " font-size:10px;")
    lbl.setFixedWidth(155)
    row.addWidget(lbl)

    val = QLabel("—")
    sk.set_role(val, "value", " font-size:10px;")
    row.addWidget(val)
    row.addStretch()

    layout.addLayout(row)
    return val
