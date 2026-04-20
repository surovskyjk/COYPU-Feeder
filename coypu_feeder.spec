# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Coypu-Feeder.

Build from project root:
    venv\Scripts\pyinstaller.exe coypu_feeder.spec
"""

import os
from pathlib import Path

project_root = Path(SPECPATH)
src_dir      = project_root / "src"

# ── Data files to bundle ─────────────────────────────────────────────────────
datas = [
    # Leaflet map assets (served via local HTTP from QWebEngineView)
    (str(src_dir / "gui" / "static" / "map.html"),  "gui/static"),
    (str(src_dir / "gui" / "static" / "leaflet.js"), "gui/static"),
    (str(src_dir / "gui" / "static" / "leaflet.css"),"gui/static"),
    # Curated railway lines database
    (str(src_dir / "data" / "suggested_lines.py"),   "data"),
]

# ── Hidden imports ────────────────────────────────────────────────────────────
# Modules that PyInstaller's static analyser misses (dynamic imports, plugins)
hidden_imports = [
    # scipy submodules loaded at runtime
    "scipy.special._cdflib",
    "scipy.special._ufuncs",
    "scipy.signal",
    "scipy.interpolate",
    "scipy._lib.messagestream",
    # shapely geometry engine
    "shapely.geometry",
    "shapely.ops",
    # pyproj / PROJ
    "pyproj",
    "pyproj.transformer",
    # lxml serialisation
    "lxml.etree",
    "lxml._elementpath",
    # overpy (Overpass API client)
    "overpy",
    # PySide6 WebEngine (embedded Chromium for the Leaflet map)
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    # project modules (dynamic path insertion in main.py)
    "gui.app",
    "gui.theme",
    "gui.map_widget",
    "gui.step_sidebar",
    "gui.worker",
    "gui.steps.step1_find",
    "gui.steps.step2_section",
    "gui.steps.step3_configure",
    "gui.steps.step4_candidates",
    "gui.steps.step5_refine",
    "gui.steps.step6_crosssection",
    "gui.steps.step7_export",
    "osm.query",
    "osm.parser",
    "geometry.alignment",
    "geometry.candidates",
    "geometry.curvature",
    "geometry.elevation",
    "geometry.projection",
    "landxml.builder",
    "data.suggested_lines",
]

# ── Excludes (reduce bundle size) ────────────────────────────────────────────
excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "notebook",
    "pytest",
    "setuptools",
    "distutils",
]

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

# ── Executable ───────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # --onedir mode (faster startup, easier distribution)
    name="Coypu-Feeder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX is unreliable with Qt; leave off
    console=False,           # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ── One-directory bundle ─────────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Coypu-Feeder",
)
