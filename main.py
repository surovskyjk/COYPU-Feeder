"""
Coypu-Feeder — entry point (run from project root).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplashScreen
from gui.theme import is_dark_mode, apply_theme
from gui.branding import make_app_icon, make_splash_pixmap


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("COYPU Feeder")
    app.setWindowIcon(make_app_icon())
    apply_theme(app, is_dark_mode())

    # Loading screen — visible while the heavy imports (QtWebEngine, scipy,
    # pyproj, …) and the main-window construction run.
    splash = QSplashScreen(make_splash_pixmap())
    splash.show()
    splash.showMessage(
        "Loading map engine & geometry libraries…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        Qt.GlobalColor.gray,
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
