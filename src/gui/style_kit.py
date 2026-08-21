"""
Shared QSS builders for the few widgets the application stylesheet can't reach.

COYPU styles nearly everything centrally in ``theme_manager.buildStyleSheet``.
What is left over here are Feeder-specific emphases that live inside the step
panels — the confirm button that ends a step, muted captions, metric panels.
Building them from tokens in one place keeps them out of the individual panels
and guarantees they all restyle together on a theme switch.

Note the confirm button: COYPU has no saturated filled accent button anywhere,
so a positive/confirm action is expressed the way COYPU marks a positive state —
an ``accentDone`` tint with a matching border, not a solid green fill.
"""

from __future__ import annotations


def confirm_button(t: dict) -> str:
    """The primary 'this finishes the step' action (Accept, Start Export)."""
    return (
        "QPushButton {"
        f" background:{t['accentDoneBackground']};"
        f" color:{t['accentDone']};"
        f" border:1px solid {t['accentDone']};"
        " border-radius:3px; font-weight:600; padding:6px 12px; }"
        "QPushButton:hover {"
        f" background:{t['accentDone']}; color:{t['highlightText']}; }}"
        "QPushButton:disabled {"
        f" background:{t['alternateBase']}; color:{t['disabledText']};"
        f" border-color:{t['border']}; }}"
    )


def secondary_button(t: dict) -> str:
    """A flat, outlined action — COYPU's default button weight."""
    return (
        "QPushButton {"
        f" background:{t['button']}; color:{t['text']};"
        f" border:1px solid {t['border']};"
        " border-radius:3px; padding:5px; }"
        "QPushButton:hover {"
        f" background:{t['alternateBase']}; }}"
        "QPushButton:disabled {"
        f" background:{t['alternateBase']}; color:{t['disabledText']}; }}"
    )


def panel_frame(t: dict) -> str:
    """A grouped read-out panel (metrics block)."""
    return (
        "QFrame {"
        f" background:{t['alternateBase']};"
        f" border:1px solid {t['border']}; border-radius:3px; }}"
    )


def progress_bar(t: dict) -> str:
    return (
        "QProgressBar {"
        f" background:{t['alternateBase']}; color:{t['text']};"
        f" border:1px solid {t['border']};"
        " border-radius:3px; text-align:center; font-size:9px; }"
        "QProgressBar::chunk {"
        f" background:{t['highlight']}; border-radius:3px; }}"
    )


# ---------------------------------------------------------------------------
# Label colours
# ---------------------------------------------------------------------------

def muted_label(t: dict, size: int = 10) -> str:
    """Small caption text (COYPU uses palette(mid) for these)."""
    return f"color:{t['disabledText']}; font-size:{size}px;"


def value_label(t: dict, size: int = 10) -> str:
    """A read-out value: normal text colour."""
    return f"color:{t['text']}; font-size:{size}px;"


def ok_label(t: dict, size: int = 10) -> str:
    return f"color:{t['accentDone']}; font-size:{size}px;"


def warn_label(t: dict, size: int = 10) -> str:
    from gui.palette import WARNING
    return f"color:{WARNING}; font-size:{size}px;"


def error_label(t: dict, size: int = 10) -> str:
    return f"color:{t['error']}; font-size:{size}px;"


def title_label(t: dict, size: int = 12) -> str:
    """A step panel's heading."""
    return f"color:{t['highlight']}; font-size:{size}px; font-weight:600;"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def bold_font(widget, delta: int = 0):
    """Bold *widget*, optionally *delta* points larger than the app font.

    COYPU never sets a font family, and the Feeder exposes a base font size
    as a preference, so sizes are derived from the application font rather
    than pinned to an absolute point size and a hardcoded family.
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    f = app.font() if app is not None else widget.font()
    f.setBold(True)
    if delta:
        f.setPointSize(max(6, f.pointSize() + delta))
    widget.setFont(f)
    return widget


# ---------------------------------------------------------------------------
# Semantic label roles
# ---------------------------------------------------------------------------

# Labels whose colour is re-set as results arrive (a deviation read-out, a
# pass/fail indicator) can't be registered with ThemedPanel.themed() - that
# would grow the registry on every update. They carry their meaning in a
# Qt dynamic property instead, so applyTheme can re-derive the colour.
ROLE_TOKEN = {
    "value":     "text",
    "muted":     "disabledText",
    "ok":        "accentDone",
    "error":     "error",
    "title":     "highlight",
    "separator": "border",
}

ROLE_PROPERTY = "colorRole"


def role_style(role: str, t: dict) -> str:
    """The stylesheet for a semantic role under the given tokens."""
    if role == "warn":
        from gui.palette import WARNING
        return f"color:{WARNING};"
    return f"color:{t[ROLE_TOKEN.get(role, 'text')]};"


def set_role(widget, role: str, extra: str = ""):
    """Colour *widget* by role and remember the role for later restyling."""
    from gui.theme import current_tokens
    widget.setProperty(ROLE_PROPERTY, role)
    if extra:
        widget.setProperty("colorRoleExtra", extra)
    widget.setStyleSheet(role_style(role, current_tokens()) + extra)
    return widget


# ---------------------------------------------------------------------------
# Live retheming
# ---------------------------------------------------------------------------

class ThemedPanel:
    """Mixin giving a panel an ``applyTheme`` built from registered widgets.

    Step panels create most of their captions as locals, so there is no
    attribute to reach for when the theme changes. ``themed()`` keeps the
    reference alongside the builder that produced its stylesheet, which is all
    ``applyTheme`` needs to restyle everything the panel owns.

    Mix in before the Qt base class::

        class Step1Find(ThemedPanel, QWidget):
            ...
            self.themed(hint, style_kit.muted_label)
    """

    def bolded(self, widget, delta: int = 0):
        """Bold *widget* now and again whenever the base font changes."""
        registry = self.__dict__.setdefault("_bolded_widgets", [])
        registry.append((widget, delta))
        return bold_font(widget, delta)

    def themed(self, widget, builder):
        """Style *widget* from the active tokens now, and again on every switch."""
        from gui.theme import current_tokens
        registry = self.__dict__.setdefault("_themed_widgets", [])
        registry.append((widget, builder))
        widget.setStyleSheet(builder(current_tokens()))
        return widget

    def _apply_registries(self, t: dict):
        """Re-run this panel's own registered builders. No recursion."""
        for widget, delta in self.__dict__.get("_bolded_widgets", ()):
            try:
                bold_font(widget, delta)
            except RuntimeError:
                pass

        for widget, builder in self.__dict__.get("_themed_widgets", ()):
            try:
                widget.setStyleSheet(builder(t))
            except RuntimeError:
                pass    # the underlying C++ widget is already gone

    def applyTheme(self, isDark=None, tokens: dict | None = None):
        """Restyle every registered widget (COYPU's dock convention)."""
        from gui.theme import current_tokens
        from PySide6.QtWidgets import QWidget
        t = tokens or current_tokens()

        self._apply_registries(t)

        for child in self.findChildren(QWidget):
            # Nested panels (the candidate cards inside Step 4, say) keep their
            # own registries, which this walk would otherwise never reach.
            if isinstance(child, ThemedPanel):
                child._apply_registries(t)

            # Role-tagged widgets restyle from their property, so labels that
            # are recoloured as results arrive stay correct too.
            role = child.property(ROLE_PROPERTY)
            if not role:
                continue
            try:
                extra = child.property("colorRoleExtra") or ""
                child.setStyleSheet(role_style(role, t) + extra)
            except RuntimeError:
                pass
