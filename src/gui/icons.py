"""
Programmatically generated vector icons, no binary image files are used.

Ported near-verbatim from the COYPU application (``icons.py``) so both
programs draw from one glyph set; keep the factory API identical to COYPU's
so re-syncing stays a plain diff. The Feeder-specific alignment-editing
glyphs are grouped at the end of ICON_BODIES.
"""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QCommonStyle, QProxyStyle, QStyle

# Nominal size the SVG markup below is authored against
ICON_VIEWBOX = 24

# Size the generated pixmaps are rasterised at before Qt scales them down
ICON_RENDER_SIZE = 64

# Stroke only SVG bodies, the placeholders are filled in by the factory
ICON_BODIES = {
    "open": "<path d='M3 7h6l2 2h10v10H3z'/><path d='M3 7V5h5l2 2'/>",
    "openLandxml": "<path d='M4 4h11l5 5v11H4z'/><path d='M15 4v5h5'/><path d='M8 13l-2 2 2 2'/><path d='M13 13l2 2-2 2'/>",
    "openTtp": "<path d='M4 4h11l5 5v11H4z'/><path d='M15 4v5h5'/><path d='M8 14h8'/><path d='M8 17h5'/>",
    "openText": "<path d='M4 4h11l5 5v11H4z'/><path d='M15 4v5h5'/>",
    "append": "<path d='M3 7h6l2 2h10v10H3z'/><path d='M15 11v6'/><path d='M12 14h6'/>",
    "appendLandxml": "<path d='M4 4h9l4 4v5'/><path d='M4 4v16h7'/><path d='M17 15v6'/><path d='M14 18h6'/>",
    "appendTtp": "<path d='M4 4h9l4 4v5'/><path d='M4 4v16h7'/><path d='M7 12h6'/><path d='M17 15v6'/><path d='M14 18h6'/>",
    "clean": "<path d='M4 7h16'/><path d='M9 7V4h6v3'/><path d='M6 7l1 13h10l1-13'/>",
    "cleanPart": "<path d='M4 7h16'/><path d='M9 7V4h6v3'/><path d='M6 7l1 13h10l1-13'/><path d='M10 11v6'/>",
    "calculate": "<rect x='4' y='3' width='16' height='18' rx='2'/><path d='M8 7h8'/><path d='M8 12h2'/><path d='M14 12h2'/><path d='M8 16h2'/><path d='M14 16h2'/>",
    "calculateAlt": "<rect x='4' y='3' width='16' height='18' rx='2'/><path d='M8 7h8'/><path d='M8 12h8'/><path d='M8 16h4'/>",
    "run": "<circle cx='12' cy='12' r='9'/><path d='M10 8l6 4-6 4z'/>",
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M12 2v3'/><path d='M12 19v3'/><path d='M2 12h3'/><path d='M19 12h3'/><path d='M5 5l2 2'/><path d='M17 17l2 2'/><path d='M19 5l-2 2'/><path d='M7 17l-2 2'/>",
    "vehicle": "<rect x='3' y='6' width='18' height='9' rx='2'/><circle cx='8' cy='18' r='2'/><circle cx='16' cy='18' r='2'/><path d='M3 11h18'/>",
    "stops": "<path d='M12 3a6 6 0 0 1 6 6c0 4-6 12-6 12S6 13 6 9a6 6 0 0 1 6-6z'/><circle cx='12' cy='9' r='2'/>",
    "map": "<path d='M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z'/><path d='M9 4v14'/><path d='M15 6v14'/>",
    "report": "<path d='M5 3h9l5 5v13H5z'/><path d='M14 3v5h5'/><path d='M9 13h6'/><path d='M9 17h6'/>",
    "export": "<path d='M12 3v11'/><path d='M8 10l4 4 4-4'/><path d='M4 17v3h16v-3'/>",
    "help": "<circle cx='12' cy='12' r='9'/><path d='M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7'/><path d='M12 17h.01'/>",
    "exit": "<path d='M10 4H5v16h5'/><path d='M15 8l4 4-4 4'/><path d='M19 12H9'/>",
    "viewMap": "<circle cx='12' cy='12' r='9'/><path d='M3 12h18'/><path d='M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z'/>",
    "viewReport": "<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M7 9h10'/><path d='M7 13h10'/><path d='M7 17h6'/>",
    "layout": "<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M9 4v16'/><path d='M9 12h12'/>",
    "resetLayout": "<path d='M20.5 13a8.5 8.5 0 1 1-2.5-7'/><path d='M20 3v5h-5'/><path d='M3.5 11a8.5 8.5 0 0 0 2.5 7'/>",
    "foldAll": "<path d='M6 9l6-5 6 5'/><path d='M6 15l6 5 6-5'/><path d='M3 12h18'/>",
    "unfoldAll": "<path d='M6 5l6 5 6-5'/><path d='M6 19l6-5 6 5'/><path d='M3 12h18'/>",
    "themeAuto": "<circle cx='12' cy='12' r='8'/><path d='M12 4a8 8 0 0 0 0 16z' fill='CURRENT' stroke='none'/>",
    "themeLight": "<circle cx='12' cy='12' r='4'/><path d='M12 2v3'/><path d='M12 19v3'/><path d='M2 12h3'/><path d='M19 12h3'/><path d='M5 5l2 2'/><path d='M17 17l2 2'/><path d='M19 5l-2 2'/><path d='M7 17l-2 2'/>",
    "themeDark": "<path d='M20 14a8 8 0 1 1-10-10a7 7 0 0 0 10 10z'/>",
    "units": "<path d='M4 8h16'/><path d='M4 16h16'/><path d='M8 5v6'/><path d='M16 13v6'/>",
    "panel": "<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M3 9h18'/>",
    "zoomIn": "<circle cx='11' cy='11' r='7'/><path d='M16 16l5 5'/><path d='M11 8v6'/><path d='M8 11h6'/>",
    "zoomOut": "<circle cx='11' cy='11' r='7'/><path d='M16 16l5 5'/><path d='M8 11h6'/>",
    "pan": "<path d='M12 3v18'/><path d='M3 12h18'/><path d='M9 6l3-3 3 3'/><path d='M9 18l3 3 3-3'/><path d='M6 9l-3 3 3 3'/><path d='M18 9l3 3-3 3'/>",
    "resetView": "<path d='M4 9a8 8 0 1 1-1 5'/><path d='M3 4v5h5'/>",
    "exportImage": "<rect x='3' y='4' width='18' height='14' rx='2'/><path d='M3 15l5-4 4 3 3-3 6 5'/><circle cx='9' cy='9' r='1.5'/>",
    "expand": "<path d='M4 10V4h6'/><path d='M20 14v6h-6'/><path d='M4 4l7 7'/><path d='M20 20l-7-7'/>",
    "detach": "<path d='M14 4h6v6'/><path d='M20 4l-9 9'/><path d='M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'/>",
    "railway": "<path d='M7 3v18'/><path d='M17 3v18'/><path d='M4 8h16'/><path d='M4 13h16'/><path d='M4 18h16'/>",
    "style": "<path d='M4 18c4 0 4-12 8-12s4 12 8 12'/>",
    "station": "<path d='M4 21h16'/><path d='M6 21V11l6-5 6 5v10'/><path d='M10 21v-6h4v6'/>",
    "layers": "<path d='M12 3l9 5-9 5-9-5z'/><path d='M3 13l9 5 9-5'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='M16 16l5 5'/>",
    "grid": "<path d='M3 9h18'/><path d='M3 15h18'/><path d='M9 3v18'/><path d='M15 3v18'/>",
    "highlight": "<path d='M4 20l4-1 9-9-3-3-9 9z'/><path d='M14 7l3 3'/>",
    "close": "<path d='M6 6l12 12'/><path d='M18 6L6 18'/>",
    "float": "<rect x='4' y='7' width='13' height='13' rx='1'/><path d='M8 7V4h12v12h-3'/>",
    "batch": "<rect x='3' y='4' width='18' height='4' rx='1'/><rect x='3' y='10' width='18' height='4' rx='1'/><rect x='3' y='16' width='18' height='4' rx='1'/>",
    "dashboard": "<path d='M4 20V10'/><path d='M10 20V4'/><path d='M16 20V13'/><path d='M20 20H4'/>",
    # Alignment-editing glyphs specific to the Feeder (no COYPU equivalent)
    "showAlignment": "<path d='M4 18l6-8 4 4 6-8'/><circle cx='14' cy='14' r='2.4' fill='CURRENT' stroke='none'/>",
    "merge": "<path d='M4 5l9 7'/><path d='M4 19l9-7'/><path d='M13 12h7'/><path d='M17 8l3 4-3 4'/>",
    "undo": "<path d='M4 9a8 8 0 1 1-1 5'/><path d='M3 4v5h5'/>",
    "redo": "<path d='M20 9a8 8 0 1 0 1 5'/><path d='M21 4v5h-5'/>",
    "edit": "<path d='M4 20l1-4 11-11 3 3-11 11z'/><path d='M15 6l3 3'/>",
    "newProject": "<path d='M5 3h9l5 5v13H5z'/><path d='M14 3v5h5'/><path d='M12 12v6'/><path d='M9 15h6'/>",
    "save": "<path d='M5 3h11l3 3v15H5z'/><path d='M8 3v6h7V3'/><path d='M8 21v-7h8v7'/>",
}

# Icons whose glyph should always be tinted with the accent colour instead
ACCENT_ICONS = ("run", "calculate", "calculateAlt")

# Icon size given to the dock widget title bar buttons
DOCK_TITLE_ICON_SIZE = 18

# Margin kept around the dock widget title bar buttons
DOCK_TITLE_BUTTON_MARGIN = 4


class IconFactory:
    def __init__(self):
        self.foreground = "#1c1c1c"
        self.accent = "#2f6fb5"
        self.badgeBackground = "#ececec"
        self.cache = {}

    # Adopt the active theme tokens and drop every previously rendered icon
    def applyTheme(self, tokens):
        if tokens:
            self.foreground = tokens.get("text", self.foreground)
            self.accent = tokens.get("highlight", self.accent)
            self.badgeBackground = tokens.get("button", self.badgeBackground)
        self.cache.clear()

    # Return a cached QIcon for one of the names in ICON_BODIES
    def icon(self, name):
        cacheKey = ("icon", name)
        if cacheKey in self.cache:
            return self.cache[cacheKey]

        body = ICON_BODIES.get(name)
        if body is None:
            icon = QIcon()
        else:
            color = self.accent if name in ACCENT_ICONS else self.foreground
            icon = self.renderSvg(body, color)

        self.cache[cacheKey] = icon
        return icon

    # Rasterise a stroke only SVG body into a QIcon at the active colour
    def renderSvg(self, body, color, strokeWidth=1.8):
        markup = (
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ICON_VIEWBOX} {ICON_VIEWBOX}' "
            f"fill='none' stroke='{color}' stroke-width='{strokeWidth}' "
            f"stroke-linecap='round' stroke-linejoin='round'>"
            f"{body.replace('CURRENT', color)}</svg>"
        )

        renderer = QSvgRenderer(markup.encode("utf-8"))
        pixmap = QPixmap(ICON_RENDER_SIZE, ICON_RENDER_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()

        return QIcon(pixmap)

    # Build a rounded text badge used for the parameter and data series buttons
    def badge(self, text, accentColor=None):
        cacheKey = ("badge", text, accentColor)
        if cacheKey in self.cache:
            return self.cache[cacheKey]

        size = ICON_RENDER_SIZE
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        borderColor = QColor(accentColor) if accentColor else QColor(self.foreground)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        frame = QRectF(3, size * 0.22, size - 6, size * 0.56)
        painter.setPen(QPen(borderColor, 3.0))
        painter.setBrush(QColor(self.badgeBackground))
        painter.drawRoundedRect(frame, 8, 8)

        font = QFont()
        font.setBold(True)
        font.setPixelSize(self.fitBadgeFontSize(text, frame.width() - 8))
        painter.setFont(font)
        painter.setPen(QPen(QColor(self.foreground)))
        painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

        icon = QIcon(pixmap)
        self.cache[cacheKey] = icon
        return icon

    # Pick the largest pixel size at which the badge label still fits
    def fitBadgeFontSize(self, text, availableWidth):
        length = max(len(text), 1)
        estimated = int(availableWidth / (length * 0.62))
        return max(9, min(estimated, 26))

    # Red close glyph used by the dock widget title bars
    def closeIcon(self):
        cacheKey = ("closeIcon",)
        if cacheKey not in self.cache:
            self.cache[cacheKey] = self.renderSvg(ICON_BODIES["close"], "#e81123", strokeWidth=2.6)
        return self.cache[cacheKey]

    # Float or restore glyph used by the dock widget title bars
    def floatIcon(self):
        cacheKey = ("floatIcon",)
        if cacheKey not in self.cache:
            self.cache[cacheKey] = self.renderSvg(ICON_BODIES["float"], self.foreground, strokeWidth=2.0)
        return self.cache[cacheKey]


# Shared factory so every module renders icons from the same theme state
iconFactory = IconFactory()


class CoypuProxyStyle(QProxyStyle):
    # Swap the dock title bar glyphs without giving up the native title bar behaviour
    def standardIcon(self, standardIcon, option=None, widget=None):
        if standardIcon == QStyle.StandardPixmap.SP_TitleBarCloseButton:
            return iconFactory.closeIcon()
        if standardIcon == QStyle.StandardPixmap.SP_TitleBarNormalButton:
            return iconFactory.floatIcon()

        wrappedStyle = self.wrappedStyle()
        if wrappedStyle is None:
            return QCommonStyle.standardIcon(self, standardIcon, option, widget)
        return wrappedStyle.standardIcon(standardIcon, option, widget)

    # Enlarge only the dock title bar buttons so the close target stays clickable
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_SmallIconSize and self.isDockTitleButton(widget):
            return DOCK_TITLE_ICON_SIZE
        if metric == QStyle.PixelMetric.PM_DockWidgetTitleBarButtonMargin:
            return DOCK_TITLE_BUTTON_MARGIN

        wrappedStyle = self.wrappedStyle()
        if wrappedStyle is None:
            return QCommonStyle.pixelMetric(self, metric, option, widget)
        return wrappedStyle.pixelMetric(metric, option, widget)

    # Return the wrapped style, or None when delegating to it would recurse
    def wrappedStyle(self):
        # An unset base resolves back to this proxy, so super would never terminate
        baseStyle = self.baseStyle()
        if baseStyle is None or baseStyle is self:
            return None
        return baseStyle

    # True when the widget is one of the buttons Qt places on a dock title bar
    def isDockTitleButton(self, widget):
        if widget is None:
            return False
        try:
            return widget.metaObject().className() == "QDockWidgetTitleButton"
        except RuntimeError:
            # The underlying C++ widget can already be gone during teardown
            return False


# Build a small QIcon for a plain glyph name, kept for call site readability
def makeIcon(name):
    return iconFactory.icon(name)


# Build a text badge icon, kept for call site readability
def makeBadge(text, accentColor=None):
    return iconFactory.badge(text, accentColor)


# Icon size the ribbon uses for its large buttons
def ribbonIconSize():
    return QSize(24, 24)
