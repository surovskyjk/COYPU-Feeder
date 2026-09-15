"""
Theme manager - OS theme detection, manual override and instant application.

Ported near-verbatim from the COYPU application (``theme_manager.py``) so the
two programs share one visual language; keep the camelCase API and the token
dicts below byte-identical to COYPU's so re-syncing stays a plain diff.

Only deviation: COYPU pushes the tokens into pyqtgraph's plot defaults, which
the Feeder has no use for (and no pyqtgraph dependency), so ``applyPlotStyles``
is dropped. The plotBackground/plotForeground/plotGrid tokens are kept anyway
to keep the dicts comparable.
"""
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from gui import icons

# Theme mode identifiers persisted in QSettings
MODE_AUTO = "auto"
MODE_LIGHT = "light"
MODE_DARK = "dark"

# Style the proxy wraps when the current one cannot be identified by name
DEFAULT_STYLE_KEY = "Fusion"

# Semi transparent tints for the checkable series buttons, readable in both themes
TOGGLE_ON_BACKGROUND = "rgba(46, 213, 115, 0.25)"
TOGGLE_ON_HOVER = "rgba(46, 213, 115, 0.40)"
TOGGLE_ON_BORDER = "rgba(46, 213, 115, 0.75)"
TOGGLE_OFF_BACKGROUND = "rgba(255, 71, 87, 0.12)"
TOGGLE_OFF_HOVER = "rgba(255, 71, 87, 0.24)"
TOGGLE_OFF_BORDER = "rgba(255, 71, 87, 0.35)"

# Colour tokens for the light theme
LIGHT_TOKENS = {
    "window": "#f3f3f3",
    "base": "#ffffff",
    "alternateBase": "#e9e9ec",
    "text": "#1c1c1c",
    "disabledText": "#9a9a9a",
    "button": "#ececec",
    "highlight": "#2f6fb5",
    "highlightText": "#ffffff",
    "border": "#c4c4c4",
    "plotBackground": "#ffffff",
    "plotForeground": "#1c1c1c",
    "plotGrid": "#c8c8c8",
    "accentDone": "#2e9e4f",
    "accentDoneBackground": "#dff3e4",
    "error": "#c0392b",
}

# Colour tokens for the dark theme
DARK_TOKENS = {
    "window": "#2b2b2b",
    "base": "#1e1e1e",
    "alternateBase": "#333333",
    "text": "#e6e6e6",
    "disabledText": "#7a7a7a",
    "button": "#3a3a3a",
    "highlight": "#4a90d9",
    "highlightText": "#ffffff",
    "border": "#4d4d4d",
    "plotBackground": "#1e1e1e",
    "plotForeground": "#e6e6e6",
    "plotGrid": "#4d4d4d",
    "accentDone": "#4cc46e",
    "accentDoneBackground": "#264d33",
    "error": "#e57373",
}


class ThemeManager(QObject):
    # Emitted after a theme switch so widgets can restyle themselves
    themeChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentMode = MODE_AUTO
        self.currentTokens = LIGHT_TOKENS
        self.isDarkActive = False
        self.proxyStyle = None

        self.installProxyStyle()

        # React to OS level colour scheme changes while running in auto mode
        styleHints = QGuiApplication.styleHints()
        if hasattr(styleHints, "colorSchemeChanged"):
            styleHints.colorSchemeChanged.connect(self.onSystemSchemeChanged)

    # Wrap the active style so the dock title bars get the generated glyphs
    def installProxyStyle(self):
        app = QApplication.instance()
        if app is None or self.proxyStyle is not None:
            return

        currentStyle = app.style()

        # Wrapping our own proxy again would make it delegate to itself
        if isinstance(currentStyle, icons.CoypuProxyStyle):
            self.proxyStyle = currentStyle
            return

        # The key overload gives the proxy a private base that setStyle cannot delete
        self.proxyStyle = icons.CoypuProxyStyle(self.resolveStyleKey(currentStyle))
        app.setStyle(self.proxyStyle)

    # Name of the style the proxy should wrap, falling back to a always present one
    def resolveStyleKey(self, currentStyle):
        availableKeys = {key.lower(): key for key in QStyleFactory.keys()}

        currentKey = currentStyle.objectName() if currentStyle is not None else ""
        return availableKeys.get(currentKey.lower(), DEFAULT_STYLE_KEY)

    # Return True when the operating system currently reports a dark scheme
    def detectSystemDark(self):
        # Qt does not clear a previous override synchronously, so ask the OS first
        registryValue = self.readWindowsAppsUseLightTheme()
        if registryValue is not None:
            return registryValue == 0

        styleHints = QGuiApplication.styleHints()
        if hasattr(styleHints, "colorScheme"):
            scheme = styleHints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False

        # Fallback for platforms that do not report a colour scheme
        palette = QGuiApplication.palette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128

    # Read the Windows personalisation flag, returns None on other platforms
    def readWindowsAppsUseLightTheme(self):
        if not sys.platform.startswith("win"):
            return None
        try:
            import winreg
            keyPath = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keyPath) as registryKey:
                value, _ = winreg.QueryValueEx(registryKey, "AppsUseLightTheme")
                return int(value)
        except (OSError, ValueError):
            return None

    # Slot for the OS scheme signal, only relevant while the mode is auto
    def onSystemSchemeChanged(self, scheme=None):
        if self.currentMode == MODE_AUTO:
            self.applyTheme(MODE_AUTO)

    # Resolve the requested mode into an effective dark or light decision
    def resolveDark(self, mode):
        if mode == MODE_DARK:
            return True
        if mode == MODE_LIGHT:
            return False
        return self.detectSystemDark()

    # Apply a theme mode across the whole application and notify listeners
    def applyTheme(self, mode):
        self.currentMode = mode

        app = QApplication.instance()
        if app is None:
            return

        # Clear any previous override first so auto mode reads the real OS scheme
        styleHints = QGuiApplication.styleHints()
        if hasattr(styleHints, "setColorScheme"):
            if mode == MODE_AUTO:
                styleHints.setColorScheme(Qt.ColorScheme.Unknown)
            elif mode == MODE_DARK:
                styleHints.setColorScheme(Qt.ColorScheme.Dark)
            else:
                styleHints.setColorScheme(Qt.ColorScheme.Light)

        self.isDarkActive = self.resolveDark(mode)
        self.currentTokens = DARK_TOKENS if self.isDarkActive else LIGHT_TOKENS

        app.setPalette(self.buildPalette())
        app.setStyleSheet(self.buildStyleSheet())

        self.themeChanged.emit(mode)

    # Build a QPalette from the active colour tokens
    def buildPalette(self):
        tokens = self.currentTokens
        palette = QPalette()

        palette.setColor(QPalette.ColorRole.Window, QColor(tokens["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(tokens["base"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens["alternateBase"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens["base"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(tokens["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens["text"]))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff5555"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens["highlight"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens["highlightText"]))
        palette.setColor(QPalette.ColorRole.Link, QColor(tokens["highlight"]))

        disabled = QPalette.ColorGroup.Disabled
        palette.setColor(disabled, QPalette.ColorRole.Text, QColor(tokens["disabledText"]))
        palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(tokens["disabledText"]))
        palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(tokens["disabledText"]))

        return palette

    # Build the application wide stylesheet for docks, ribbon and status bar
    def buildStyleSheet(self):
        tokens = self.currentTokens
        return f"""
QMainWindow::separator {{
    background: {tokens['border']};
    width: 3px;
    height: 3px;
}}
QDockWidget {{
    font-weight: 600;
}}
QDockWidget::title {{
    background: {tokens['button']};
    color: {tokens['text']};
    padding: 5px 24px 5px 8px;
    border: 1px solid {tokens['border']};
}}
QDockWidget::close-button, QDockWidget::float-button {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 0px;
}}
QDockWidget::float-button:hover {{
    background: {tokens['alternateBase']};
}}
QDockWidget::close-button:hover {{
    background: #e81123;
}}
QDockWidget::close-button:pressed, QDockWidget::float-button:pressed {{
    background: {tokens['highlight']};
}}
QTabWidget::pane {{
    border: 1px solid {tokens['border']};
    background: {tokens['window']};
}}
QTabBar::tab {{
    background: {tokens['button']};
    color: {tokens['text']};
    padding: 6px 14px;
    border: 1px solid {tokens['border']};
    border-bottom: none;
}}
QTabBar::tab:selected {{
    background: {tokens['base']};
    border-bottom: 2px solid {tokens['highlight']};
}}
QStatusBar {{
    background: {tokens['button']};
    color: {tokens['text']};
    border-top: 1px solid {tokens['border']};
}}
QStatusBar QLabel {{
    padding: 0px 10px;
}}
QToolBar {{
    background: {tokens['window']};
    border: none;
    spacing: 3px;
}}
QToolButton {{
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: 3px;
    color: {tokens['text']};
}}
QToolButton:hover {{
    background: {tokens['alternateBase']};
    border: 1px solid {tokens['border']};
}}
QToolButton[seriesToggle="true"] {{
    margin: 1px;
}}
QToolButton[seriesToggle="true"]:checked {{
    background: {TOGGLE_ON_BACKGROUND};
    border: 1px solid {TOGGLE_ON_BORDER};
}}
QToolButton[seriesToggle="true"]:checked:hover {{
    background: {TOGGLE_ON_HOVER};
    border: 1px solid {TOGGLE_ON_BORDER};
}}
QToolButton[seriesToggle="true"]:!checked {{
    background: {TOGGLE_OFF_BACKGROUND};
    border: 1px solid {TOGGLE_OFF_BORDER};
}}
QToolButton[seriesToggle="true"]:!checked:hover {{
    background: {TOGGLE_OFF_HOVER};
    border: 1px solid {TOGGLE_OFF_BORDER};
}}
QPushButton[seriesToggle="true"]:checked {{
    background: {TOGGLE_ON_BACKGROUND};
    border: 1px solid {TOGGLE_ON_BORDER};
    border-radius: 3px;
}}
QPushButton[seriesToggle="true"]:!checked {{
    background: {TOGGLE_OFF_BACKGROUND};
    border: 1px solid {TOGGLE_OFF_BORDER};
    border-radius: 3px;
}}
QWidget#MainToolbarRow {{
    background: {tokens['window']};
    border-bottom: 1px solid {tokens['border']};
}}
QScrollArea#MainToolbarScroll {{
    background: {tokens['window']};
    border: none;
}}
QScrollArea#MainToolbarScroll > QWidget > QWidget {{
    background: {tokens['window']};
}}
QToolButton#ToolbarScrollArrow {{
    background: {tokens['window']};
    border: none;
    padding: 2px;
}}
QToolButton#ToolbarScrollArrow:hover {{
    background: {tokens['alternateBase']};
    border: 1px solid {tokens['border']};
}}
QHeaderView::section {{
    background: {tokens['button']};
    color: {tokens['text']};
    border: 1px solid {tokens['border']};
    padding: 3px;
}}
QDialog {{
    background: {tokens['window']};
    color: {tokens['text']};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {tokens['base']};
    color: {tokens['text']};
    border: 1px solid {tokens['border']};
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: {tokens['highlight']};
    selection-color: {tokens['highlightText']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {tokens['disabledText']};
    background: {tokens['alternateBase']};
}}
/* Step-arrow visibility (Req 8): styling QSpinBox/QDoubleSpinBox at all
   hands their subcontrol layout to QStyleSheetStyle, which then needs an
   explicit ::up-button/::down-button rect or its default one collides with
   the rule above's padding — the arrows end up squeezed behind the text or
   entirely unclickable. Reserve the room and draw them explicitly instead
   of relying on the platform style's (now bypassed) default. */
QSpinBox, QDoubleSpinBox {{
    padding-right: 16px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    height: 10px;
    border-left: 1px solid {tokens['border']};
    border-bottom: 1px solid {tokens['border']};
    border-top-right-radius: 3px;
    background: {tokens['button']};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 15px;
    height: 10px;
    border-left: 1px solid {tokens['border']};
    border-top: 1px solid {tokens['border']};
    border-bottom-right-radius: 3px;
    background: {tokens['button']};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {tokens['alternateBase']};
}}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background: {tokens['highlight']};
}}
QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled {{
    background: {tokens['alternateBase']};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {tokens['text']};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {tokens['text']};
}}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
    border-bottom-color: {tokens['disabledText']};
}}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    border-top-color: {tokens['disabledText']};
}}
QComboBox::drop-down {{
    border-left: 1px solid {tokens['border']};
}}
QComboBox QAbstractItemView {{
    background: {tokens['base']};
    color: {tokens['text']};
    border: 1px solid {tokens['border']};
    selection-background-color: {tokens['highlight']};
    selection-color: {tokens['highlightText']};
    outline: 0;
}}
QMenu {{
    background: {tokens['base']};
    color: {tokens['text']};
    border: 1px solid {tokens['border']};
}}
QMenu::item {{
    padding: 4px 20px;
}}
QMenu::item:selected {{
    background: {tokens['highlight']};
    color: {tokens['highlightText']};
}}
QMenu::separator {{
    height: 1px;
    background: {tokens['border']};
    margin: 4px 8px;
}}
QListWidget, QTreeWidget, QTableWidget {{
    background: {tokens['base']};
    alternate-background-color: {tokens['alternateBase']};
    color: {tokens['text']};
    border: 1px solid {tokens['border']};
    gridline-color: {tokens['border']};
}}
QListWidget::item, QTreeWidget::item, QTreeView::item {{
    padding: 2px;
    color: {tokens['text']};
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected,
QTableWidget::item:selected {{
    background: {tokens['highlight']};
    color: {tokens['highlightText']};
}}
QLabel[errorState="true"] {{
    color: {tokens['error']};
}}
"""

    # Convenience accessor used by widgets that need a single token
    def token(self, name):
        return self.currentTokens.get(name, "#000000")
