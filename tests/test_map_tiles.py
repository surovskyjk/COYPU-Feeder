"""
CARTO basemap key on the map: it is appended to CARTO tile URLs only (never to OSM, OpenTopoMap,
Esri, ČÚZK or OpenRailwayMap), URL-quoted, absent when no key is set, never hardcoded in the
sources, and kept out of the plaintext Preferences values.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.map_widget import TILE_PROVIDERS, tile_url  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
TEST_KEY = "testKey 123"


@pytest.fixture
def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _carto():
    return [p for p in TILE_PROVIDERS if p.get("carto")]


class TestTileUrl:

    def test_both_carto_basemaps_are_marked(self):
        labels = {p["label"] for p in _carto()}
        assert labels == {"CARTO Dark Matter", "CARTO Voyager"}
        for p in _carto():
            assert "basemaps.cartocdn.com/rastertiles/" in p["url"]

    def test_key_is_quoted_onto_carto_urls(self):
        for p in _carto():
            url = tile_url(p, TEST_KEY)
            assert url.endswith("?key=testKey%20123")
            assert "api_key" not in url

    def test_no_key_leaves_carto_urls_untouched(self):
        for p in _carto():
            assert tile_url(p, "") == p["url"]
            assert "key=" not in tile_url(p, "")

    def test_key_never_reaches_other_tile_servers(self):
        for p in TILE_PROVIDERS:
            if p.get("carto"):
                continue
            assert tile_url(p, TEST_KEY) == p["url"], p["label"]

    def test_no_key_is_hardcoded_anywhere(self):
        # A literal key in a URL, or CARTO's wrong parameter name — not the
        # `basemap_api_key=` keyword arguments.
        pattern = re.compile(r"[?&]key=[A-Za-z0-9_-]{6,}|[?&]api_key=")
        for path in list(SRC.rglob("*.py")) + list(SRC.rglob("*.html")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not pattern.search(text), path


class TestSettingsDialogKeyField:

    def test_key_field_is_masked_and_kept_out_of_values(self, _qapp):
        from PySide6.QtWidgets import QLineEdit
        from gui.dialogs import SettingsDialog
        dlg = SettingsDialog({}, basemap_api_key="secretKey", basemap_key_source="feeder")
        assert dlg._api_key.echoMode() == QLineEdit.EchoMode.Password
        assert dlg.basemap_api_key() == "secretKey"
        assert "secretKey" not in repr(dlg.values())

    def test_typed_key_is_stripped(self, _qapp):
        from gui.dialogs import SettingsDialog
        dlg = SettingsDialog({})
        dlg._api_key.setText("  newKey  ")
        assert dlg.basemap_api_key() == "newKey"

    def test_show_key_toggles_the_echo_mode(self, _qapp):
        from PySide6.QtWidgets import QLineEdit
        from gui.dialogs import SettingsDialog
        dlg = SettingsDialog({}, basemap_api_key="k")
        dlg._show_key.setChecked(True)
        assert dlg._api_key.echoMode() == QLineEdit.EchoMode.Normal
        dlg._show_key.setChecked(False)
        assert dlg._api_key.echoMode() == QLineEdit.EchoMode.Password
