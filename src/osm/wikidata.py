"""
Municipality populations from Wikidata (property P1082, "population").

OSM's own ``population`` tags on administrative boundaries are often years
out of date (Kolín: 30 826 in OSM, 33 444 for 2025 in Wikidata, which bots
refresh from the Czech Statistical Office every year), so the station step
asks Wikidata first and keeps the OSM tag only as the fallback.

All requests go through the shared ``osm.query._http()`` session, whose
identifying User-Agent the Wikimedia API policy also asks for.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import requests

from osm.query import CONNECT_TIMEOUT, _http

WIKIDATA_API     = "https://www.wikidata.org/w/api.php"
WIKIDATA_TIMEOUT = (CONNECT_TIMEOUT, 20)
MAX_WORKERS      = 4

_QID = re.compile(r"^Q\d+$")


def _year_of(time_text: str) -> Optional[int]:
    """Year of a Wikidata time value such as ``+2025-01-01T00:00:00Z``."""
    m = re.match(r"^\+?(\d{4})", time_text or "")
    return int(m.group(1)) if m else None


def pick_population(claims: list[dict]) -> Optional[tuple[int, Optional[int]]]:
    """
    ``(population, year)`` from a list of P1082 claims, or None.

    A preferred-rank claim wins over normal ones; within a rank the latest
    "point in time" (P585) wins, and a claim without one counts as the
    oldest. Deprecated claims and claims without a value are ignored.
    """
    best = None      # ((is_preferred, time), amount, year)
    for c in claims or []:
        rank = c.get("rank", "normal")
        snak = c.get("mainsnak") or {}
        if rank == "deprecated" or snak.get("snaktype") != "value":
            continue
        try:
            amount = int(round(float(snak["datavalue"]["value"]["amount"])))
        except (KeyError, TypeError, ValueError):
            continue
        time_text = ""
        for q in (c.get("qualifiers") or {}).get("P585") or []:
            if q.get("snaktype") == "value":
                time_text = str(((q.get("datavalue") or {}).get("value") or {})
                                .get("time", ""))
                break
        key = (rank == "preferred", time_text)
        if best is None or key > best[0]:
            best = (key, amount, _year_of(time_text))
    return None if best is None else (best[1], best[2])


class _Skipped(Exception):
    pass


def fetch_populations(
    qids: list[str],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> tuple[dict[str, tuple[int, Optional[int]]], int]:
    """
    Latest population of every Wikidata item in *qids*.

    Returns ``({qid: (population, year)}, failed)`` where *failed* counts
    the items that could not be read (network or API errors). Items with no
    usable P1082 claim are simply absent. Never raises for network trouble:
    once Wikidata proves unreachable the remaining items are skipped.
    """
    wanted = sorted({q for q in qids if q and _QID.match(q)})
    if not wanted:
        return {}, 0
    if progress_cb:
        progress_cb(f"Reading {len(wanted)} population(s) from Wikidata…")
    offline = threading.Event()

    def one(qid: str):
        if offline.is_set():
            raise _Skipped(qid)
        try:
            r = _http().get(WIKIDATA_API, timeout=WIKIDATA_TIMEOUT, params={
                "action": "wbgetclaims", "entity": qid,
                "property": "P1082", "format": "json"})
            r.raise_for_status()
            claims = (r.json().get("claims") or {}).get("P1082") or []
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            offline.set()
            raise
        return pick_population(claims)

    found: dict[str, tuple[int, Optional[int]]] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {qid: pool.submit(one, qid) for qid in wanted}
        for qid, fut in futures.items():
            try:
                value = fut.result()
            except (_Skipped, requests.exceptions.RequestException, ValueError):
                failed += 1
                continue
            if value is not None:
                found[qid] = value
    return found, failed
