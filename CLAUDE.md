# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
venv\Scripts\python.exe main.py                          # run the app (venv is committed to the working tree)
venv\Scripts\python.exe -m pytest tests\                 # run all tests
venv\Scripts\python.exe -m pytest tests\test_alignment.py -k spiral   # single test / filter
venv\Scripts\pyinstaller.exe coypu_feeder.spec           # build the Windows one-dir bundle (→ dist/Coypu-Feeder)
```

- Headless GUI checks: set `QT_QPA_PLATFORM=offscreen`. Offscreen runs spew Qt GPU errors to stdout — write your own results to stderr with a grep-able prefix.
- The Windows console is cp1250; run `python -X utf8` when a script prints non-ASCII (box-drawing chars, `→`, etc.).
- When adding a module or data file, also add it to `hiddenimports` / `datas` in `coypu_feeder.spec` — PyInstaller's analyzer misses the dynamic `sys.path` setup in `main.py`.

## Architecture

Desktop PySide6 app converting OSM railway polylines into C1-continuous LandXML 1.2 alignments (Line → Spiral → Arc → Spiral → Line) for the COYPU railway software. `main.py` inserts `src/` on `sys.path`; all imports are rooted there (`geometry.candidates`, `gui.app`, …).

### The PI model is the single source of truth

`src/geometry/candidates.py` holds the core editable model:

- `PIAlignment` (tangent polygon: vertices `V`, reference polyline `xy_ref`, per-vertex `PIData` list, `elements`, `last_stats`) and `PIData` (radius/spiral overrides, `omitted`, merge flags).
- `extract_pi_model(...)` derives the model from a fitted candidate; it **auto-merges same-rotation curves whose gap is < `_PI_MIN_TANGENT` (60 m)** — synthetic test tracks need larger separations or runs vanish before you can test merging.
- `rebuild_from_pi_model(model)` regenerates the element chain from the model. **Elements are never persisted anywhere** — not in `.coypu` files, not across edits; always regenerate. Rebuild also refreshes `model.last_stats` (deviation stats) — consumers read that instead of recomputing.
- `merge_pi_range`, `merge_intermediate_line`, `undo_merge` edit the model *and* leave `model.elements` correct; UI callers must not trigger a second rebuild (see perf invariants).
- The Consolidate step (`find_consolidation_groups` / `apply_consolidation`) trials merges on a `deepcopy`, then applies accepted groups **back-to-front** so PI indices stay valid.

`gui/app.py` (`App`) is the **single owner** of the working `_pi_model`/`_elements`. Steps never keep their own copy: Refine and Consolidate bind the shared element-table dock to the same model object (so undo/restore must mutate the model in place, not replace it). Navigation is free, not gated: `_step_reasons()` computes prerequisites, edits mark downstream steps (Stations/Cross-section/Export) stale via `_mark_stale()`, and stale steps re-prepare from current geometry on entry. Step indices use the symbolic constants `S_FIND..S_EXPORT` — never bare integers.

Note the step *file names* do not match the 9-step UI numbering (`step6_consolidate.py`, `step6_stations.py`, `step6_crosssection.py`, `step7_export.py` are UI steps 6–9); the stack order in `app.py` is authoritative.

### Geometry conventions

- All geometry math runs in a projected CRS in meters (transformer via `geometry/projection.py`, `make_transformer` is `lru_cache`d); WGS84 appears only at the map/OSM boundary. COYPU targets EPSG:5514 (S-JTSK) for Czech data.
- Spirals are true clothoids (scipy Fresnel). `_compute_zone_geometry(n_pts=0)` is the fast endpoint-only path used everywhere except `_zone_objective` (which passes `n_pts=40`).
- `.coypu` project files (`src/project_io.py`) are XML in the LandXML house style: 6 decimals for user-facing values, **9 decimals (`NUM_GEOM`) for internal geometry** — 6 is not enough for exact round-trips. Chainages and elements are recomputed on load.

### Performance invariants (hard-won; do not regress)

A merge click on a 1,700-element line went from 171 s to 0.6 s. Keep these:

- Deviation checks go through the vectorized `compute_deviation_stats` / `_dists_to_element_vec` — never per-point Python loops over elements.
- Projection calls are **batched**: one `projected_to_wgs84` call for all element samples / all PIs, not one per element.
- `element_table._rebuild(regenerate=False)` for handlers whose geometry op already rebuilt the elements; the table has an in-place cell-update fast path keyed on element ids.
- The Leaflet map (`gui/static/map.html`, loaded in QWebEngineView) uses shared `L.canvas()` renderers and lazily-created glow polylines.

When touching geometry code, prove behavior preservation: fingerprint every element (length/radius/station/coords rounded to 9 decimals) before and after, plus max_dev/rmse/C1/endpoint metrics — bit-identical output is the expectation, not "close".

### Data sources

- Overpass API with OSM API fallback (`src/osm/query.py`). Overpass **rejects the default python-requests User-Agent with 406** — all HTTP goes through the custom `HTTP_HEADERS`. Overpass mirrors are frequently down; keep the fallback path working.
- Czech railway list: bundled snapshot `src/data/cz_railways.json` (relation 2332889); a user-updated copy in `<AppData>/COYPU-Feeder/` takes precedence (`src/data/cz_lines.py`).
- Elevation from the Open-Elevation DEM service.
