"""
Offline Czech railway line list.

A snapshot of the members of OSM relation 2332889 ("Railways in Czech
Republic") ships with the app so the Czech Railways tab works instantly and
without network access. The user can refresh it (🔄 Update list); the
refreshed copy is written to the per-user data directory and takes
precedence over the bundled one from then on.

The stored dicts are exactly what `osm.query.fetch_relation_members`
returns — no transformation on either side.
"""

from __future__ import annotations

import json
import os
from datetime import date

_FILENAME = "cz_railways.json"
CZ_RAILWAYS_RELATION = 2332889


def _bundled_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _FILENAME)


def _user_path() -> str:
    """Per-user copy (written by 'Update list'); may not exist."""
    base = ""
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation) or ""
    except Exception:
        base = ""
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".coypu-feeder")
    # AppDataLocation only includes the app name once QApplication has one;
    # pin our own folder so the file never lands loose in Roaming/.
    if os.path.basename(base).lower() not in ("coypu-feeder", "coypu feeder"):
        base = os.path.join(base, "COYPU-Feeder")
    return os.path.join(base, _FILENAME)


def load_cz_lines() -> tuple[list, dict]:
    """
    Return (lines, meta). `meta` = {"generated": "YYYY-MM-DD",
    "source": "updated"|"bundled"|"none", "count": int}.

    The user copy wins over the bundled snapshot.
    """
    for path, source in ((_user_path(), "updated"), (_bundled_path(), "bundled")):
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            lines = data.get("lines") or []
            if not lines:
                continue
            return lines, {
                "generated": data.get("generated", "?"),
                "source": source,
                "count": len(lines),
            }
        except Exception:
            continue
    return [], {"generated": "?", "source": "none", "count": 0}


def save_cz_lines(lines: list) -> str:
    """Persist a refreshed list to the user data directory; returns the path."""
    path = _user_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "generated": date.today().isoformat(),
        "relation": CZ_RAILWAYS_RELATION,
        "lines": lines,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path
