"""
Overpass API queries for railway data.
Supports search by name, relation ID, and bounding box.

Fallback strategy:
  1. Disk cache (1 h for search results, 24 h for full relation data)
  2. Public Overpass mirrors (OVERPASS_ENDPOINTS). The order adapts within a
     session: a mirror that answers moves to the front, one that hangs moves
     to the back. Each query declares its own server-side [timeout:]; the
     client waits that long plus QUEUE_SLACK, because busy servers queue a
     request before running it (26 s for a trivial query was measured).
     On HTTP 429 the mirror's /status page says when a slot frees up; a short
     wait there beats moving on. Searches and station queries get a second
     pass after a pause when every mirror was only busy.
  3. Relation geometry, metadata and member lists: the official OSM API v0.6
     right after ONE Overpass pass, since it answers those directly.

Every failure across all mirrors surfaces as OverpassUnavailable, which keeps
the per-server reasons; `friendly_error()` turns any fetch exception into the
text shown to the user.
"""

import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Endpoints & timeouts
# ---------------------------------------------------------------------------

# Distinct services only: overpass.kumi.systems is a DNS alias of
# private.coffee, and lz4/z are the two backends behind overpass-api.de —
# reachable directly when the front proxy answers 502.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

CONNECT_TIMEOUT = 6     # an unreachable mirror fails fast
QUEUE_SLACK     = 30    # read timeout = the query's [timeout:] + this

# Server-side [timeout:] per query kind. A low declared timeout (and maxsize)
# also helps a busy server admit the query at all.
T_SEARCH   = 30
T_RELATION = 60
T_SMALL    = 15
T_STATIONS = 25
SMALL_MAXSIZE = 134_217_728   # 128 MiB, for the tag-only queries

OVERPASS_DEADLINE = 150   # whole budget for one query, both passes
FALLBACK_DEADLINE = 60    # Overpass budget when the OSM API can take over
RETRY_PAUSE   = 10        # before the second pass
MAX_SLOT_WAIT = 15        # wait at most this long for a 429'd mirror's slot

OSM_API_BASE    = "https://api.openstreetmap.org/api/0.6"
OSM_API_HOST    = "api.openstreetmap.org"
OSM_API_TIMEOUT = (CONNECT_TIMEOUT, 60)

# Identifying User-Agent — required by the OSM/Overpass usage policy.
# overpass-api.de rejects the default python-requests agent with HTTP 406.
HTTP_HEADERS = {
    "User-Agent": "COYPU-Feeder/1.0 (+https://github.com/surovskyjk/COYPU-Feeder)"
}

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _http() -> requests.Session:
    """One keep-alive session for every OSM request (no cookies involved;
    urllib3's connection pool is thread-safe)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update(HTTP_HEADERS)
        return _session


_endpoint_order: list[str] = list(OVERPASS_ENDPOINTS)
_order_lock = threading.Lock()


def _endpoints() -> list[str]:
    with _order_lock:
        return list(_endpoint_order)


def _promote(url: str) -> None:
    with _order_lock:
        if url in _endpoint_order:
            _endpoint_order.remove(url)
            _endpoint_order.insert(0, url)


def _demote(url: str) -> None:
    with _order_lock:
        if url in _endpoint_order:
            _endpoint_order.remove(url)
            _endpoint_order.append(url)


def _reset_endpoint_order() -> None:
    """Back to the configured order (tests)."""
    with _order_lock:
        _endpoint_order[:] = OVERPASS_ENDPOINTS


def _host(url: str) -> str:
    return urlparse(url).hostname or url


def _ql_header(timeout: int, maxsize: Optional[int] = None) -> str:
    head = f"[out:json][timeout:{timeout}]"
    if maxsize:
        head += f"[maxsize:{maxsize}]"
    return head + ";"


def _ql_quote(text: str) -> str:
    """Escape user text for an Overpass QL string literal — a stray quote
    or backslash otherwise makes every mirror answer HTTP 400."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OverpassUnavailable(RuntimeError):
    """
    No Overpass mirror (nor the OSM API fallback, where one exists) delivered.

    `attempts` lists (host, category, detail) per failed try, category being
    one of "busy", "timeout", "offline", "bad_query", "server_error".
    """

    _SUMMARIES = {
        "busy": "The Overpass servers are busy right now (rate-limited or "
                "overloaded). Please try again in a minute or two.",
        "timeout": "The Overpass servers did not answer in time — they are "
                   "probably overloaded. Please try again in a minute or two.",
        "offline": "Could not reach any Overpass server. Please check your "
                   "internet connection.",
        "bad_query": "Overpass rejected the query (HTTP 400) — the search "
                     "text may contain characters it cannot handle.",
        "server_error": "The Overpass servers returned errors. Please try "
                        "again later.",
    }

    def __init__(self, attempts: list[tuple[str, str, str]]):
        self.attempts = list(attempts)
        super().__init__(self._message())

    @property
    def category(self) -> str:
        cats = [c for _, c, _ in self.attempts]
        if not cats:
            return "server_error"
        if "bad_query" in cats:
            return "bad_query"
        if all(c == "offline" for c in cats):
            return "offline"
        for c in ("busy", "timeout"):
            if c in cats:
                return c
        return "server_error"

    @property
    def summary(self) -> str:
        return self._SUMMARIES[self.category]

    def plus(self, host: str, exc: BaseException) -> "OverpassUnavailable":
        """A copy with one more failed attempt (the OSM API fallback)."""
        return OverpassUnavailable(self.attempts + [(host, *_classify(exc))])

    def _message(self) -> str:
        lines = [self.summary]
        lines += [f"  • {host}: {detail}" for host, _, detail in self.attempts]
        return "\n".join(lines)


class _AttemptFailed(Exception):
    """One mirror's failure inside _run_query (never escapes it)."""

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category
        self.detail = detail


def _http_status_category(code: int) -> tuple[str, str]:
    if code == 429:
        return "busy", "rate-limited (HTTP 429)"
    if code in (502, 503, 504):
        return "busy", f"overloaded (HTTP {code})"
    if code == 400:
        return "bad_query", "rejected the query (HTTP 400)"
    if code >= 500:
        return "server_error", f"server error (HTTP {code})"
    return "server_error", f"refused the request (HTTP {code})"


def _classify(exc: BaseException) -> tuple[str, str]:
    """(category, detail) for an exception from one request."""
    if isinstance(exc, _AttemptFailed):
        return exc.category, exc.detail
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return _http_status_category(exc.response.status_code)
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "offline", "unreachable (connect timed out)"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout", "no answer in time"
    if isinstance(exc, requests.exceptions.ConnectionError):
        text = str(exc)
        if "NameResolution" in text or "getaddrinfo" in text:
            return "offline", "unreachable (DNS lookup failed)"
        return "offline", "unreachable (connection failed)"
    if isinstance(exc, (ValueError, ET.ParseError)):   # incl. JSONDecodeError
        return "server_error", "sent an unreadable response"
    return "server_error", type(exc).__name__


def friendly_error(exc: BaseException) -> str:
    """User-facing text for any exception a fetch can raise. The first line
    is the summary; any following lines are per-server detail."""
    if isinstance(exc, OverpassUnavailable):
        return str(exc)
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in (404, 410):
            return ("The requested OSM relation was not found. "
                    "Please check the relation ID.")
        if code == 429 or code >= 500:
            return (f"The OpenStreetMap server is busy (HTTP {code}). "
                    "Please try again in a minute or two.")
        return f"The OpenStreetMap server refused the request (HTTP {code})."
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return ("Could not reach the OpenStreetMap servers. "
                "Please check your internet connection.")
    if isinstance(exc, requests.exceptions.Timeout):
        return ("The OpenStreetMap servers did not answer in time. "
                "Please try again in a minute or two.")
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ("Could not connect to the OpenStreetMap servers. "
                "Please check your internet connection.")
    if isinstance(exc, ET.ParseError):
        return "The OpenStreetMap server sent an unreadable response. Please try again."
    first_line = str(exc).split("\n")[0]
    return first_line or type(exc).__name__


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

_CACHE_DIR = (
    Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    / "coypu_osm_cache"
)
_CACHE_TTL_SEARCH   = 3_600    # 1 hour for search / member-list results
_CACHE_TTL_RELATION = 86_400   # 24 hours for full relation way/node data


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.json"


def _cache_get(key: str, ttl: int) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > ttl:
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(key: str, data: dict) -> None:
    """Atomic write — a reader never sees a half-written file."""
    try:
        p = _cache_path(key)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    except Exception:
        pass


def _query_key(query: str) -> str:
    return hashlib.md5(query.encode("utf-8")).hexdigest()


def _has_ways(data: dict) -> bool:
    return any(el.get("type") == "way" for el in data.get("elements", []))


# ---------------------------------------------------------------------------
# Core Overpass runner
# ---------------------------------------------------------------------------

_SLOT_FREE_RE = re.compile(r"(\d+)\s+slots?\s+available\s+now", re.I)
_SLOT_WAIT_RE = re.compile(r"in\s+(-?\d+)\s+seconds", re.I)


def _slot_wait(url: str) -> Optional[float]:
    """Seconds until a query slot frees up on this mirror, per its /status
    page — 0 when one is free now, None when the page can't tell."""
    status_url = url.rsplit("/", 1)[0] + "/status"
    try:
        text = _http().get(status_url, timeout=(CONNECT_TIMEOUT, 5)).text
    except requests.exceptions.RequestException:
        return None
    m = _SLOT_FREE_RE.search(text)
    if m and int(m.group(1)) > 0:
        return 0.0
    waits = [int(s) for s in _SLOT_WAIT_RE.findall(text)]
    return max(0.0, float(min(waits))) if waits else None


def _runtime_error(data: dict) -> str:
    """The server's own "runtime error" remark (query timed out / ran out of
    memory) — sent with HTTP 200 and partial or no elements."""
    remark = str(data.get("remark") or "")
    return remark if "runtime error" in remark.lower() else ""


def _attempt(url: str, query: str, read_timeout: float) -> dict:
    """One POST to one mirror. Raises _AttemptFailed or a requests error."""
    response = _http().post(url, data={"data": query},
                            timeout=(CONNECT_TIMEOUT, read_timeout))
    if not response.ok:
        raise _AttemptFailed(*_http_status_category(response.status_code))
    try:
        data = response.json()
    except ValueError:
        raise _AttemptFailed("server_error", "sent an unreadable response")
    remark = _runtime_error(data)
    if remark:
        if "timed out" in remark.lower():
            raise _AttemptFailed("timeout", "ran out of time on the server")
        raise _AttemptFailed("server_error", "ran out of memory on the server")
    return data


def _run_query(
    query: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    ttl: int = _CACHE_TTL_SEARCH,
    *,
    timeout: int = T_SEARCH,
    passes: int = 2,
    deadline: float = OVERPASS_DEADLINE,
    cacheable: Optional[Callable[[dict], bool]] = None,
) -> dict:
    """
    Run an Overpass QL query, trying the mirrors in their adaptive order.

    *timeout* is the server-side [timeout:] the query declares; the client
    read timeout is that plus QUEUE_SLACK, capped by what is left of
    *deadline* (seconds for the whole call). *passes* = 2 allows one retry
    round after RETRY_PAUSE when every mirror was merely busy.
    Results are cached to disk for *ttl* seconds unless *cacheable* rejects
    them. *progress_cb*, if given, is called with a short status string each
    time the endpoint changes (safe to call from any thread).

    Raises OverpassUnavailable once every mirror failed.
    """
    def _p(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    key = _query_key(query)
    cached = _cache_get(key, ttl)
    if cached is not None:
        _p("Loaded from cache.")
        return cached

    t_end = time.monotonic() + deadline
    attempts: list[tuple[str, str, str]] = []
    skip: set[str] = set()     # unreachable mirrors aren't retried in pass 2

    for pass_no in range(1, passes + 1):
        urls = [u for u in _endpoints() if u not in skip]
        pass_attempts: list[tuple[str, str, str]] = []
        for i, url in enumerate(urls):
            host = _host(url)
            slot_waited = False
            while True:
                remaining = t_end - time.monotonic()
                if remaining < CONNECT_TIMEOUT + 5:
                    break
                _p(f"Asking {host} ({i + 1}/{len(urls)})…")
                try:
                    data = _attempt(url, query,
                                    min(timeout + QUEUE_SLACK, remaining))
                except (requests.exceptions.RequestException, _AttemptFailed) as exc:
                    category, detail = _classify(exc)
                    if category == "bad_query":
                        # The same query fails on every mirror.
                        raise OverpassUnavailable(
                            attempts + [(host, category, detail)]) from exc
                    if (detail.startswith("rate-limited") and not slot_waited):
                        wait = _slot_wait(url)
                        if (wait is not None and wait <= MAX_SLOT_WAIT
                                and t_end - time.monotonic() > wait + timeout):
                            slot_waited = True
                            _p(f"{host} is rate-limited — waiting {wait + 1:.0f} s "
                               "for a free slot…")
                            time.sleep(wait + 1)
                            continue
                    if category == "timeout":
                        _demote(url)
                    if category == "offline":
                        skip.add(url)
                    pass_attempts.append((host, category, detail))
                    _p(f"{host}: {detail} — trying the next server…")
                    break
                _promote(url)
                if cacheable is None or cacheable(data):
                    _cache_put(key, data)
                return data
        attempts += pass_attempts

        retryable = [a for a in pass_attempts if a[1] != "offline"]
        if (pass_no < passes and retryable
                and t_end - time.monotonic() > RETRY_PAUSE + CONNECT_TIMEOUT + 15):
            _p(f"All Overpass servers busy — retrying in {RETRY_PAUSE} s…")
            time.sleep(RETRY_PAUSE)
            continue
        break

    if not attempts:
        attempts = [("Overpass", "timeout", "no time left to ask any server")]
    raise OverpassUnavailable(attempts)


def _overpass_or_osm_api(run_overpass: Callable, run_osm_api: Callable):
    """Run *run_overpass*; if every mirror failed, run *run_osm_api* instead.
    A relation the OSM API doesn't know (404/410) re-raises that error;
    any other OSM API failure joins the Overpass attempt list."""
    try:
        return run_overpass()
    except OverpassUnavailable as overpass_exc:
        if overpass_exc.category == "bad_query":
            raise
        try:
            return run_osm_api()
        except requests.exceptions.HTTPError as osm_exc:
            if osm_exc.response is not None and osm_exc.response.status_code in (404, 410):
                raise
            raise overpass_exc.plus(OSM_API_HOST, osm_exc) from osm_exc
        except (requests.exceptions.RequestException, ET.ParseError) as osm_exc:
            raise overpass_exc.plus(OSM_API_HOST, osm_exc) from osm_exc


# ---------------------------------------------------------------------------
# OSM API v0.6 XML fallback helpers
# ---------------------------------------------------------------------------

def _osm_api_get(url: str) -> requests.Response:
    resp = _http().get(url, timeout=OSM_API_TIMEOUT)
    resp.raise_for_status()
    return resp


def _relation_summary(relation_id: int, tags: dict) -> dict:
    return {
        "id":       relation_id,
        "name":     tags.get("name", f"Relation {relation_id}"),
        "network":  tags.get("network", ""),
        "operator": tags.get("operator", ""),
        "from":     tags.get("from", ""),
        "to":       tags.get("to", ""),
    }


def _parse_osm_xml(xml_text: str) -> dict:
    """
    Convert an OSM API v0.6 XML response to an Overpass-compatible dict.

    Nodes, ways and relation tags are extracted (relation members are not
    needed); parse_tracks only reads nodes and ways, and the relation's
    tags give the metadata without a second request.
    """
    root = ET.fromstring(xml_text)
    elements: list[dict] = []

    for node in root.findall("node"):
        lat = node.get("lat")
        lon = node.get("lon")
        if lat is None or lon is None:
            continue
        elements.append({
            "type": "node",
            "id":   int(node.get("id")),
            "lat":  float(lat),
            "lon":  float(lon),
            "tags": {tag.get("k"): tag.get("v") for tag in node.findall("tag")},
        })

    for way in root.findall("way"):
        nodes = [int(nd.get("ref")) for nd in way.findall("nd")]
        elements.append({
            "type":  "way",
            "id":    int(way.get("id")),
            "nodes": nodes,
            "tags":  {tag.get("k"): tag.get("v") for tag in way.findall("tag")},
        })

    for rel in root.findall("relation"):
        elements.append({
            "type": "relation",
            "id":   int(rel.get("id")),
            "tags": {tag.get("k"): tag.get("v") for tag in rel.findall("tag")},
        })

    return {"elements": elements}


def _fetch_relation_via_osm_api(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch relation ways/nodes directly from the official OSM API v0.6.
    Returns an Overpass-compatible dict.
    """
    if progress_cb:
        progress_cb(f"Asking {OSM_API_HOST} directly…")
    resp = _osm_api_get(f"{OSM_API_BASE}/relation/{relation_id}/full")
    return _parse_osm_xml(resp.text)


def _fetch_relation_members_via_osm_api(
    parent_relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch member relations of a parent relation via the official OSM API v0.6.

    Two-step process:
      1. GET /relation/{id}        → extract member relation IDs
      2. GET /relations?relations=… → batch-fetch tags for those members

    Returns a list of dicts in the same shape as fetch_relation_members().
    """
    def _p(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    # Step 1 — get the list of member IDs
    _p("OSM API: fetching parent relation…")
    resp = _osm_api_get(f"{OSM_API_BASE}/relation/{parent_relation_id}")

    root = ET.fromstring(resp.text)
    member_ids: list[int] = []
    for rel_el in root.findall("relation"):
        for m in rel_el.findall("member"):
            if m.get("type") == "relation":
                try:
                    member_ids.append(int(m.get("ref")))
                except (TypeError, ValueError):
                    pass

    if not member_ids:
        return []

    # Step 2 — batch-fetch tags (max 500 IDs per request)
    BATCH = 500
    results: list[dict] = []
    for i in range(0, len(member_ids), BATCH):
        batch = member_ids[i: i + BATCH]
        ids_str = ",".join(str(x) for x in batch)
        _p(f"OSM API: fetching tags for {len(batch)} relations…")
        resp2 = _osm_api_get(f"{OSM_API_BASE}/relations?relations={ids_str}")
        root2 = ET.fromstring(resp2.text)
        for rel_el in root2.findall("relation"):
            rid  = int(rel_el.get("id"))
            tags = {t.get("k"): t.get("v") for t in rel_el.findall("tag")}
            results.append({
                "id":       rid,
                "name":     tags.get("name", f"Relation {rid}"),
                "ref":      tags.get("ref", ""),
                "network":  tags.get("network", ""),
                "operator": tags.get("operator", ""),
                "from":     tags.get("from", ""),
                "to":       tags.get("to", ""),
            })

    # Sort: numeric ref first, then alphabetical name
    def _sort_key(r: dict):
        try:
            return (0, int(r.get("ref", "")), r["name"])
        except (ValueError, TypeError):
            return (1, 0, r["name"])

    results.sort(key=_sort_key)
    return results


def _fetch_relation_metadata_via_osm_api(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """
    Fetch tags for a single relation via the official OSM API v0.6.
    Returns a dict in the same shape as fetch_relation_metadata(), or None.
    """
    if progress_cb:
        progress_cb("OSM API: fetching relation metadata…")
    resp = _osm_api_get(f"{OSM_API_BASE}/relation/{relation_id}")
    root = ET.fromstring(resp.text)
    for rel_el in root.findall("relation"):
        tags = {t.get("k"): t.get("v") for t in rel_el.findall("tag")}
        return _relation_summary(relation_id, tags)
    return None


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def search_railways_by_name(
    name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations matching a name.
    Returns a list of dicts with keys: id, name, network, operator, from, to.
    """
    query = f"""
{_ql_header(T_SEARCH, SMALL_MAXSIZE)}
relation["type"="route"]["route"="railway"]["name"~"{_ql_quote(name)}",i];
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb, timeout=T_SEARCH)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def fetch_relation_ways(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Fetch all ways and nodes belonging to a railway route relation, plus the
    relation's own tags (see relation_info_from_data) — one round trip.
    Returns raw Overpass-compatible JSON with nodes and ways.

    Falls back to the official OSM API v0.6 after one Overpass pass.
    """
    query = f"""
{_ql_header(T_RELATION)}
relation({relation_id})->.r;
.r out tags;
way(r.r);
out body;
node(w);
out body qt;
"""

    def _osm_api() -> dict:
        data = _fetch_relation_via_osm_api(relation_id, progress_cb=progress_cb)
        if _has_ways(data):
            _cache_put(_query_key(query), data)
        return data

    return _overpass_or_osm_api(
        lambda: _run_query(
            query, progress_cb=progress_cb, ttl=_CACHE_TTL_RELATION,
            timeout=T_RELATION, passes=1, deadline=FALLBACK_DEADLINE,
            cacheable=_has_ways,
        ),
        _osm_api,
    )


def relation_info_from_data(data: dict, relation_id: int) -> Optional[dict]:
    """Relation metadata from a fetch_relation_ways() result (both the
    Overpass and the OSM API path carry the relation's tags), or None."""
    for el in data.get("elements", []):
        if el.get("type") == "relation" and el.get("id") == relation_id:
            return _relation_summary(relation_id, el.get("tags", {}))
    return None


def search_relations_in_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations whose geometry intersects a bounding box.
    Returns relation metadata (id, name, ref, from, to, network).
    """
    query = f"""
{_ql_header(T_SEARCH, SMALL_MAXSIZE)}
relation["type"="route"]["route"="railway"]({south},{west},{north},{east});
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb, timeout=T_SEARCH)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def search_by_ref(
    ref: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations by timetable/line reference number.
    Matches the OSM 'ref' tag exactly (case-insensitive).
    Returns a list of relation metadata dicts.
    """
    safe_ref = _ql_quote(ref.strip()).replace(".", r"\.").replace("+", r"\+")
    query = f"""
{_ql_header(T_SEARCH, SMALL_MAXSIZE)}
relation["type"="route"]["route"="railway"]["ref"~"^{safe_ref}$",i];
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb, timeout=T_SEARCH)
    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def search_by_number_in_name(
    number: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Search for railway route relations whose name contains the given number,
    not adjacent to other digits.  Finds lines like "212 - Čerčany – Světlá…"
    even when the OSM 'ref' tag is missing.

    Uses a union of two Overpass queries because POSIX ERE (used by Overpass)
    does not treat '^' as a start-of-string anchor inside alternation groups.
    Query 1 catches the number at the very start of the name.
    Query 2 catches the number preceded by a non-digit character.
    """
    safe = _ql_quote(number.strip()).replace(".", r"\.").replace("+", r"\+")
    query = f"""
{_ql_header(T_SEARCH, SMALL_MAXSIZE)}
(
  relation["type"="route"]["route"="railway"]["name"~"^{safe}([^0-9]|$)"];
  relation["type"="route"]["route"="railway"]["name"~"[^0-9]{safe}([^0-9]|$)"];
);
out tags;
"""
    data = _run_query(query, progress_cb=progress_cb, timeout=T_SEARCH)
    seen: set[int] = set()
    results = []
    for el in data.get("elements", []):
        if el["id"] in seen:
            continue
        seen.add(el["id"])
        tags = el.get("tags", {})
        results.append({
            "id":       el["id"],
            "name":     tags.get("name", f"Relation {el['id']}"),
            "ref":      tags.get("ref", ""),
            "network":  tags.get("network", ""),
            "operator": tags.get("operator", ""),
            "from":     tags.get("from", ""),
            "to":       tags.get("to", ""),
        })
    return results


def fetch_relation_members(
    parent_relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch the tags of all sub-relation members of a parent relation.
    Used to list all railway lines that belong to a national/regional collection
    (e.g. 'Railways in Czech Republic', OSM relation 2332889).

    Returns a list of dicts with the same shape as search_by_ref().
    Members that are ways or nodes (not relations) are silently skipped.
    Results are sorted by ref tag (numeric where possible), then by name.

    Falls back to the official OSM API v0.6 after one Overpass pass.
    """
    query = f"""
{_ql_header(T_SMALL, SMALL_MAXSIZE)}
relation({parent_relation_id});
rel(r);
out tags;
"""

    def _overpass() -> list[dict]:
        data = _run_query(query, progress_cb=progress_cb, ttl=_CACHE_TTL_SEARCH,
                          timeout=T_SMALL, passes=1, deadline=FALLBACK_DEADLINE)
        results = []
        for el in data.get("elements", []):
            if el.get("type") != "relation":
                continue
            tags = el.get("tags", {})
            results.append({
                "id":       el["id"],
                "name":     tags.get("name", f"Relation {el['id']}"),
                "ref":      tags.get("ref", ""),
                "network":  tags.get("network", ""),
                "operator": tags.get("operator", ""),
                "from":     tags.get("from", ""),
                "to":       tags.get("to", ""),
            })

        # Sort: numeric ref first, then alpha name
        def _sort_key(r):
            ref = r.get("ref", "")
            try:
                return (0, int(ref), r["name"])
            except (ValueError, TypeError):
                return (1, 0, r["name"])

        results.sort(key=_sort_key)
        return results

    return _overpass_or_osm_api(
        _overpass,
        lambda: _fetch_relation_members_via_osm_api(
            parent_relation_id, progress_cb=progress_cb),
    )


def fetch_relation_metadata(
    relation_id: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """
    Fetch tags for a single relation.
    Falls back to the official OSM API v0.6 after one Overpass pass.
    """
    query = f"""
{_ql_header(T_SMALL, SMALL_MAXSIZE)}
relation({relation_id});
out tags;
"""

    def _overpass() -> Optional[dict]:
        data = _run_query(query, progress_cb=progress_cb, timeout=T_SMALL,
                          passes=1, deadline=FALLBACK_DEADLINE)
        elements = data.get("elements", [])
        if not elements:
            return None
        return _relation_summary(relation_id, elements[0].get("tags", {}))

    return _overpass_or_osm_api(
        _overpass,
        lambda: _fetch_relation_metadata_via_osm_api(
            relation_id, progress_cb=progress_cb),
    )


# ---------------------------------------------------------------------------
# Station / stop detection near a track
# ---------------------------------------------------------------------------

def stops_from_elements(elements: list[dict]) -> list[dict]:
    """
    Passenger stations, halts and train stop positions among Overpass-style
    *elements* — the same selection the station query makes, so it also
    works on the tagged track nodes of a fetch_relation_ways() result (the
    fallback when Overpass is down).

    Returns a list of dicts: {"name": str, "lat": float, "lon": float,
    "kind": "station"|"halt"|"stop_position"}.

    Freight-only and non-passenger facilities (yards, service stations,
    junctions, crossovers, sites with passenger=no) are excluded.
    """
    results: list[dict] = []
    seen_ids: set = set()
    for el in elements:
        if el.get("type") != "node" or el.get("id") in seen_ids:
            continue
        tags = el.get("tags") or {}
        is_stop = (tags.get("railway") in ("station", "halt")
                   or (tags.get("public_transport") == "stop_position"
                       and tags.get("train") == "yes"))
        if not is_stop:
            continue
        seen_ids.add(el.get("id"))
        # Passenger-relevant only
        if tags.get("passenger") == "no":
            continue
        if tags.get("railway") in ("yard", "service_station", "junction",
                                   "crossover", "site"):
            continue
        if tags.get("usage") in ("industrial", "military", "freight"):
            continue
        kind = tags.get("railway") or "stop_position"
        results.append({
            "name": tags.get("name", ""),
            "lat":  float(el.get("lat", 0.0)),
            "lon":  float(el.get("lon", 0.0)),
            "kind": kind,
        })
    return results


def fetch_stations_in_bbox(
    south: float, west: float, north: float, east: float,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """
    Fetch passenger stations, halts and train stop positions inside a
    bounding box (see stops_from_elements for the selection and shape).

    There is no OSM API equivalent for a tag query, so this one gets the
    full two-pass Overpass budget.
    """
    bbox = f"({south},{west},{north},{east})"
    query = f"""
{_ql_header(T_STATIONS, SMALL_MAXSIZE)}
(
  node["railway"="station"]{bbox};
  node["railway"="halt"]{bbox};
  node["public_transport"="stop_position"]["train"="yes"]{bbox};
);
out body;
"""
    data = _run_query(query, progress_cb=progress_cb, timeout=T_STATIONS)
    return stops_from_elements(data.get("elements", []))
