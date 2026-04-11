"""
Dark Fusion theme for PySide6.
"""

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from PySide6.QtCore import Qt


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()

    dark   = QColor(45,  45,  48)
    darker = QColor(30,  30,  30)
    mid    = QColor(60,  60,  63)
    light  = QColor(80,  80,  84)
    text   = QColor(220, 220, 220)
    bright = QColor(255, 255, 255)
    accent = QColor(42,  130, 218)
    dis    = QColor(120, 120, 120)
    link   = QColor(100, 170, 255)

    palette.setColor(QPalette.ColorRole.Window,          dark)
    palette.setColor(QPalette.ColorRole.WindowText,      text)
    palette.setColor(QPalette.ColorRole.Base,            darker)
    palette.setColor(QPalette.ColorRole.AlternateBase,   dark)
    palette.setColor(QPalette.ColorRole.ToolTipBase,     bright)
    palette.setColor(QPalette.ColorRole.ToolTipText,     bright)
    palette.setColor(QPalette.ColorRole.Text,            text)
    palette.setColor(QPalette.ColorRole.Button,          mid)
    palette.setColor(QPalette.ColorRole.ButtonText,      text)
    palette.setColor(QPalette.ColorRole.BrightText,      bright)
    palette.setColor(QPalette.ColorRole.Link,            link)
    palette.setColor(QPalette.ColorRole.Highlight,       accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, bright)
    palette.setColor(QPalette.ColorRole.Light,           light)
    palette.setColor(QPalette.ColorRole.Midlight,        mid)
    palette.setColor(QPalette.ColorRole.Mid,             mid)
    palette.setColor(QPalette.ColorRole.Dark,            darker)
    palette.setColor(QPalette.ColorRole.Shadow,          QColor(0, 0, 0))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Text,       dis)
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.ButtonText, dis)

    app.setPalette(palette)
    app.setStyleSheet("""
        QToolTip {
            color: #ffffff;
            background-color: #2d2d30;
            border: 1px solid #555;
        }
        QGroupBox {
            border: 1px solid #555;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 4px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            color: #aaa;
        }
        QTabBar::tab {
            background: #3c3c3f;
            color: #ccc;
            padding: 6px 14px;
            border: 1px solid #555;
            border-bottom: none;
            border-radius: 3px 3px 0 0;
        }
        QTabBar::tab:selected {
            background: #2a82da;
            color: #fff;
        }
        QScrollBar:vertical {
            width: 8px;
            background: #1e1e1e;
        }
        QScrollBar::handle:vertical {
            background: #555;
            border-radius: 4px;
            min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QPushButton {
            padding: 5px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #4a4a4f;
        }
        QProgressBar {
            border: 1px solid #555;
            border-radius: 4px;
            text-align: center;
            color: #fff;
        }
        QProgressBar::chunk {
            background-color: #2a82da;
            border-radius: 3px;
        }
    """)
