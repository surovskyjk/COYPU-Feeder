"""
Theme facade over the COYPU-ported :mod:`gui.theme_manager`.

All colour decisions live in ``theme_manager`` (a near-verbatim copy of COYPU's
own module) so the two programs share one visual language. This module keeps the
Feeder's existing snake_case entry points working and owns the process-wide
:class:`ThemeManager` instance.

Prefer :func:`apply_theme_mode` over :func:`apply_theme`: it passes the mode
("auto"/"dark"/"light") straight through, so "auto" keeps tracking the OS
colour scheme instead of being flattened to a one-shot boolean.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from gui.theme_manager import (          # noqa: F401  (re-exported for callers)
    ThemeManager, MODE_AUTO, MODE_DARK, MODE_LIGHT,
    LIGHT_TOKENS, DARK_TOKENS,
)

_manager: ThemeManager | None = None


# ---------------------------------------------------------------------------
# The shared manager
# ---------------------------------------------------------------------------

def manager() -> ThemeManager:
    """The process-wide ThemeManager, created on first use.

    Requires a live QApplication — ThemeManager installs a QProxyStyle in its
    constructor.
    """
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def apply_theme_mode(mode: str) -> bool:
    """Apply 'auto' | 'dark' | 'light'; return whether dark is now active.

    Emits ``ThemeManager.themeChanged`` so widgets can restyle in place.
    """
    m = manager()
    m.applyTheme(mode)
    return m.isDarkActive


def current_tokens() -> dict:
    """The active colour-token dict (see LIGHT_TOKENS / DARK_TOKENS)."""
    return manager().currentTokens


def is_dark_active() -> bool:
    """Whether the *applied* theme is dark (not what the OS reports)."""
    return manager().isDarkActive


def token(name: str) -> str:
    """One colour token by name, '#000000' if unknown."""
    return manager().token(name)


# ---------------------------------------------------------------------------
# Font size — no COYPU equivalent, the Feeder exposes it as a preference
# ---------------------------------------------------------------------------

def apply_font_size(app: QApplication, point_size: int) -> None:
    """Set the base application font size (points). Widgets inherit it."""
    f = app.font()
    f.setPointSize(int(point_size))
    app.setFont(f)


# ---------------------------------------------------------------------------
# Back-compatible entry points
# ---------------------------------------------------------------------------

def apply_theme(app: QApplication, dark: bool) -> None:
    """Apply an explicit dark/light theme. Kept for callers holding a bool."""
    apply_theme_mode(MODE_DARK if dark else MODE_LIGHT)


def apply_dark_theme(app: QApplication) -> None:
    """Convenience: follow the system theme."""
    apply_theme_mode(MODE_AUTO)


def is_dark_mode() -> bool:
    """Whether the OS is currently in dark mode."""
    return manager().detectSystemDark()


def resolve_dark(mode: str) -> bool:
    """Map a theme-mode preference ('auto'|'dark'|'light') to dark=bool."""
    return manager().resolveDark(mode)
