"""
Coypu-Feeder — entry point (run from project root).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QSplashScreen
from gui.theme import apply_theme_mode, apply_font_size, current_tokens
from gui.branding import make_app_icon, make_splash_pixmap


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("COYPU Feeder")

    # Apply the saved preferences before anything is painted, so the splash
    # opens in the theme the main window will use rather than always following
    # the OS. App re-applies the same values once it exists.
    settings = QSettings("COYPU", "COYPU-Feeder")
    apply_font_size(app, int(settings.value("font_pt", 9, int)))
    apply_theme_mode(settings.value("theme_mode", "auto", str))

    app.setWindowIcon(make_app_icon())

    # Loading screen — visible while the heavy imports (QtWebEngine, scipy,
    # pyproj, …) and the main-window construction run.
    splash = QSplashScreen(make_splash_pixmap())
    splash.show()
    splash.showMessage(
        "Loading map engine & geometry libraries…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        QColor(current_tokens()["disabledText"]),
    )
    app.processEvents()

    from gui.app import App          # deferred: pulls in QtWebEngine et al.
    window = App()
    window.setWindowIcon(app.windowIcon())
    window.show()
    splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
