# Coypu-Feeder

**Coypu-Feeder** is a desktop application that queries railway route data from [OpenStreetMap](https://www.openstreetmap.org/) via the Overpass API and converts it into a geometric alignment in the [LandXML 1.2](http://www.landxml.org/) format — the standard exchange format used by civil engineering and railway design software (e.g. Bentley OpenRail, Autodesk Civil 3D, Novapoint).

The fitted alignment follows the canonical railway geometry sequence:

```
Line → Transition Spiral (Clothoid) → Circular Arc → Transition Spiral → Line → …
```

Elevation is sampled from [ČÚZK DMR 5G](https://cuzk.gov.cz/) (Czech national lidar terrain model, ~0.2 m accuracy) by default, with [OpenTopoData](https://www.opentopodata.org/) (EU-DEM/SRTM/ASTER, ~25–30 m) as a selectable alternative and automatic per-point fallback — sampled along the fitted centreline, then smoothed in a dedicated **Vertical Profile** step into constant-grade tangents under a hard maximum gradient, with parabolic vertical curves.

---

## Download

Pre-built Windows x64 binaries are published as [GitHub Releases](https://github.com/surovskyjk/COYPU-Feeder/releases/latest).

1. Download `COYPU-Feeder-v1.3.0-windows-x64.zip`
2. Extract to any folder
3. Run `Coypu-Feeder.exe`

No Python installation required.

---

## Features

- **Find any railway** by timetable reference number, name, or direct OSM relation ID
- **Search in map view** — find all railway route-relations visible in the current map window (≤ 20 × 20 km)
- **Czech Railways browser** — all ~240 lines of the *Railways in Czech Republic* OSM collection from a **bundled offline snapshot** (instant, no network), refreshable with 🔄 Update
- **Interactive Leaflet map** with 7 selectable tile providers, an optional OpenRailwayMap overlay, and a dimmable ČÚZK land-register (katastrální mapa) overlay for orientation — © ČÚZK – on-line, not a legally binding cadastre extract. The CARTO basemaps need your own free API key, stored securely (see [CARTO basemaps](#carto-basemaps-api-key))
- **Track selection** — load a relation, select one or more tracks, highlight individual tracks on the map
- **Track editing** — split a track at a map point, merge two or more tracks end-to-end (endpoints matched and reversed automatically), or remove a track from the selection — all before fitting
- **3 alignment levels** — raw polyline / Lines + Arcs / Lines + Spirals + Arcs, compared side by side on the map before export
- **Clothoid transition curves** — entry/exit spirals at every arc, exact Fresnel-based tangent placement
- **Interactive element table** — every Line/Arc/Spiral with parameters; edit arc radius or spiral length with live map update, omit/restore/delete PIs, split a Line by inserting a new PI, trim the alignment's start/end, merge short straights between curves into prolonged symmetric spirals, or replace a whole PI range with **one** curve — both boundary tangents kept fixed, always exactly Spiral–Arc–Spiral (or a plain Arc), for any turn including a reflex/major arc past 180°. A total turn within ~2° of exactly 180° (geometrically singular for any single circular PI) automatically falls back to one auxiliary PI — two arcs sharing the same fitted radius, joined with a zero-length, fully C1-continuous connector — instead of refusing.
- **Curve consolidation** — same-direction curve runs are auto-merged during fitting where possible; a collapsible tool in Refine & Consolidate scans for runs joined by short straights and replaces each with a single transition–circular–transition curve, within a deviation tolerance you choose, for the wider cases the automatic pass didn't cover. Handles high-angle and near-180° runs (via the same auxiliary-PI fallback) the same way. Scan and Apply run in the background with a progress bar and a time-remaining estimate, so the UI stays responsive on long alignments.
- **Toolbar** — a compact, two-row (icon-above-text) bar: the app name, icon-only New/Open/Save, ↩ Undo / ↪ Redo (doubling as step navigation — see below), ↺ Restore default, the most-used Refine actions, and the map's tile-provider/railway-overlay controls, all in one place. When the window is too narrow to show every action, the bar scrolls horizontally instead of hiding actions behind a menu — wheel/Shift+wheel over it, or the small ‹ › arrows that appear at each end. Minimize it from View ▸ Show toolbar; several of its actions are mirrored in a new **Edit** menu too.
- **Undo/redo** — every alignment edit (merge, radius/spiral, omit, split, delete, trim, PI drag/restore) can be undone with the toolbar's ↩ Undo button (or Ctrl+Z) and redone with ↪ Redo (Ctrl+Y); Undo only falls through to step navigation once there's nothing left to undo
- **Edit modes** — default mode keeps the tangent polygon locked (radius, spiral, merge, omit, split and delete all still work); toggle **✏ Edit mode** to unlock individual PIs for dragging. Each PI starts locked (🔒) and bounces on hover as an "unlock me" cue; click its lock badge to unlock (🔓) and drag it, then use the small restore badge (↺) to snap it back to the fitted position, or re-lock when done.
- **Restore default** — one click (↺ in the toolbar) discards every edit made in Refine & Consolidate and returns to the freshly-fitted alignment, clearing the undo history
- **Original-alignment appearance** — the toolbar's "Original…" popover controls the OSM reference overlay's visibility, opacity and colour, applied instantly and remembered across sessions. It defaults to a muted grey dashed line at low opacity so the constructed alignment's colour-coded elements (blue Line / red Arc / green Spiral) stay the visual focus; a bottom-left map legend explains the colours (View ▸ Show map legend)
- **Free navigation** — the steps *suggest* an order but never lock you in: edit the alignment at any point, and the later steps refresh themselves
- **Project files (`.coypuf`)** — save and resume work; self-contained (OSM tracks + editable PI model + stations + sampled vertical profile), so reopening needs no network and no re-fitting. Files from earlier versions open unchanged; a file from a *newer* build still opens, with a warning that data this version doesn't know about is dropped on save
- **PI display** — Points of Intersection as markers (lockable/draggable in Edit mode), virtual tangent extensions dashed light-grey
- **Hover inspection** — elements glow on hover; a 3-second hover shows ID, parameters and deviation statistics
- **Stations & stops** — OSM auto-detection (passenger stations/halts only), map click-to-place or manual entry; chainage estimated along the alignment. A separate lookup fills in each stop's **municipality and population** (OSM boundaries + Wikidata), and the **Os / Sp / Ex stopping patterns** follow COYPU's rules with manual overrides. Exported as **COYPU's stops CSV**, consistent with the LandXML stationing and importable in COYPU as-is
- **Cross-section computation** — perpendicular deviations between the chosen candidate and the OSM polyline (optional analysis)
- **Vertical profile** — terrain sampled from the DEM along the *fitted* centreline (so `<Profile>` and `<CoordGeom>` share one station axis), then smoothed into constant-grade tangents under a **hard maximum gradient** with either a gradient-constrained simplification or a moving-window filter. Live preview on an interactive profile chart under the map (terrain, design line, PVIs; wheel zoom, drag pan, hover read-out, linked to the map: hovering the profile shows the matching point on the track and vice versa), with a cut/fill read-out wherever the terrain is steeper than the limit. Tunnels and viaducts can be bridged with **direct connections** (straight-line ranges whose ends become fixed PVIs). Persisted in the project file.
- **LandXML 1.2 export** — COYPU-compatible structure with horizontal + vertical geometry (the confirmed vertical profile is written verbatim, and shown in the LandXML preview), Cant block (0 mm placeholders) and CRS metadata
- **Dark / light mode** — automatic, or forced from Settings ▸ Preferences (with adjustable font size)
- **Engineering defaults** — Settings ▸ Preferences also holds app-wide defaults (min curve radius, spiral length, deviation tolerance, and a few normally-hardcoded fitting thresholds) that pre-fill Step 3 for a *new* project; each project can still override them there — changing these only affects projects started afterward

---

## Screenshots

> *3-column layout: step sidebar | interactive map | step panel*

```
┌──────────────────────────────────────────────────────────────────────┐
│ File  Edit  View  Settings  Help                                     │
├──────────────────────────────────────────────────────────────────────┤
│COYPU │ ↩  ↪  │New│Open│Save│ 🗺  ⤺  ↩  ↺ │ ✏  │ 🎨  │Map:[OSM ▾]🚂│
│Feeder│Undo Redo Step 5·Refine&Consolidate  Show Merge Undo Rst Edit  │
├────────────────────────┬───────────────────────────────────┬─────────┤
│ [1] Find Railway       │        Interactive Leaflet Map    │  Step   │
│ [2] Select             │                                   │  panel  │
│ [3] Configure          │                                   │ 340 px  │
│ [4] Candidates         │                                   │         │
│ [5] Refine&Consolidate ├──────────────┬────────────────────┤         │
│ [6] Stations           │   Log        │  Element table:    │         │
│ [7] Cross-section      │  (timestamped│  ID · type · sta · │         │
│ [8] Vertical Profile   │   activity + │  length · radius · │         │
│ [9] Export             │   step tips) │  A · defl · SL · ⋯ │         │
│   200 px sidebar       │              │                    │         │
└────────────────────────┴──────────────┴────────────────────┴─────────┘
```

The toolbar is a single compact, two-row (icon-above-text) bar: the app name, then step navigation, the File actions (icon-only — New/Open/Save are self-explanatory), the alignment's most-used edit actions, and — folded in from what used to be a separate row above the map — the tile-provider selector and railway-overlay checkbox. It's enabled only where the current selection supports it, and can be hidden entirely from **View ▸ Show toolbar** (the preference persists across restarts). A new **Edit** menu mirrors its domain-specific actions (Show alignment, Merge selection, Undo merge, Restore default, Edit mode, Original…) using the exact same actions, so their enabled/checked state always matches the toolbar.

**↩ Undo / ↪ Redo** are dual-purpose: inside Refine & Consolidate they first drain the undo/redo history (Ctrl+Z / Ctrl+Y both work) — including a Consolidate Apply, which lands in the same history as every other edit — so repeatedly pressing ↩ Undo walks each alignment edit backwards one at a time before falling through to ordinary step navigation (matching what the current step's own Back button and the sidebar's suggested-next step would do). Their tooltip always says which it will do next ("Undo: merge PIs 4–7" vs "Go to the previous step").

Editing actions: **🗺 Show alignment** re-draws the edited alignment and PI overlay; **⤺ Merge selection** replaces the selected PI range with one curve (see Step 5 below); **↩ Undo merge** reverts the spiral-merge feature for the selected curve; **↺ Restore default** discards every edit and returns to the freshly-fitted alignment (with a confirmation, since it also clears the undo history); **✏ Edit mode** enables per-PI lock/unlock dragging on the map (all other edits work in both modes — see Step 5 below); **🎨 Original…** opens a small popover to control the OSM reference overlay's visibility, opacity and colour. Toggling **✏ Edit mode**, and hovering/clicking a checkable action, gives it a distinct amber glow so its on-state reads at a glance.

A **deviation-metrics chip** in the status bar (Δmean / RMSE / Δmax / C1 / element count) stays visible from Refine & Consolidate through Export — toggle it from View ▸ Show deviation metrics.

---

## Working with projects

The nine steps **suggest** an order — they do not lock you into one. Any step whose data exists is clickable in the sidebar at any time (the recommended next one is accented ▸; a locked step's tooltip says what it needs). Edit the alignment in *Refine & Consolidate* whenever you like — even after exporting — and the later steps refresh themselves from the current geometry when you open them (station chainages are re-snapped automatically; a sampled vertical profile that no longer matches the edited horizontal geometry is flagged so you can re-sample). The **element table** follows the alignment rather than the step: once a project has a real PI model (from fitting or from opening a `.coypuf` file) it stays visible under the map through Cross-section and Export too, not just in Refine & Consolidate (the Stations step swaps it for the station table and the Vertical Profile step for the profile chart; all three share one panel, so a width you drag is kept) — the sidebar is there for orientation, but a lot of real work is re-opening a saved project and editing directly.

Save your work with **File ▸ Save project** (`Ctrl+S`) to a `.coypuf` file and pick it up later with **File ▸ Open project** (`Ctrl+O`) or the *Recent projects* list. The file is XML in the same house style as the LandXML output and is **self-contained** — the OSM track nodes, the editable PI model (every radius/spiral override, omitted PI and merge state), settings and stations. Reopening needs no network and re-runs no fitting: the element chain is regenerated from the model exactly as you left it. The title bar shows the project name and marks unsaved changes with `*`.

---

## Usage — Step by Step

### Step 1 — Find Railway

Three ways to find a railway:

**Search tab**
- *Timetable line number (ref tag)* — exact match on the OSM `ref` tag (e.g. `210`)
- *Name* — partial name search (e.g. `Praha`)
- *Number in relation name* — finds lines whose name contains the number, even when the `ref` tag is missing
- *By relation ID* — direct fetch using the numeric OSM relation ID (e.g. `3128446`)

**In View tab**
Zoom the map to an area ≤ 20 × 20 km, then click *Search Railway Lines in Current View*. Returns all railway route-relations whose geometry intersects the visible map area.

**Czech Railways tab**
All members of OSM relation 2332889 are listed **immediately from a bundled offline snapshot** — no network needed — and filtered instantly as you type, by line number, name, or terminal stations. The header shows the snapshot date and whether it is the bundled copy or your own; **🔄 Update list** re-fetches it from OpenStreetMap and stores the result in your user data folder, which is preferred from then on. A failed update keeps the existing snapshot.

Double-click any result or select it and click *Fetch selected* to load the full relation geometry.

**When the servers are busy.** The public Overpass servers get heavily loaded on weekdays. The status line names the server being asked. A server that answers is asked first for the rest of the session. A server that is rate-limited is waited on briefly when its status page says a slot frees up soon. Relation geometry falls back to the official OSM API after one round of Overpass servers. If everything fails, the message says why (busy, timed out, or offline) and lists each server's reason.

---

### Step 2 — Select Section

The loaded tracks appear in a list and are drawn on the map in distinct colours. You can:

- Select individual tracks or use *Select all* / *Clear selection*
- Click a track row to highlight it on the map in yellow
- Click *📍 Fit map to tracks* to zoom the map to show all tracks

**Edit tracks**, if the loaded relation doesn't already match what you want to export:

- **📍 Split at map point** — select exactly one track, arm the mode, then click the map: the track splits at the nearest point into two, sharing the boundary so there is no gap.
- **🔗 Merge selected** — select two or more tracks and chain them end-to-end; the closest pair of endpoints is matched automatically (reversing a track if needed). Tracks whose endpoints are more than 50 m apart are refused with a clear reason rather than silently joined.
- **🗑 Remove selected** — drop the selected track(s) from the list; the OSM data can always be re-fetched.

Editing tracks here re-locks the later steps (they need to be re-confirmed with the new track set) — this is expected, since the fitted alignment was built from the old tracks.

Click **Next → Configure** when your selection is ready.

---

### Step 3 — Configure

Configure geometry parameters before running the fitting algorithms:

| Group | Setting | Default | Description |
|---|---|---|---|
| **Project** | Project name | `Railway Alignment` | Written into the LandXML `<Project>` element |
| **Alignment Geometry** | Max deviation (PI tolerance) | 1.0 m | Douglas-Peucker tolerance for extracting the tangent polygon (Points of Intersection) |
| | Minimum curve radius | 150 m | Minimum horizontal curve radius for all circular arcs |
| | Spiral length (Level 3) | 20 m | Clothoid length; auto-shortened where tangents are too short |
| **Export Settings** | Elevation sample interval | 20 m | Default chainage step for DEM elevation queries (seeds Step 8, where it can be changed per project) |
| | Vertical curve length | 100 m | Default length of parabolic vertical curves at grade changes (seeds Step 8) |
| | Elevation source | ČÚZK DMR 5G | ČÚZK DMR 5G (CZ) or OpenTopoData (anywhere); also selectable in Step 8 |

---

### Step 4 — Candidates

Three alignment **levels** are computed in a background thread. Results appear on the map as coloured overlays as each finishes:

| Colour | Level | Description |
|---|---|---|
| 🟣 Purple | **Level 1 — OSM Polyline** | The raw OSM geometry, one Line per vertex pair. Exact but no C1 continuity (reference only) |
| 🟠 Orange | **Level 2 — Lines + Arcs** | Tangent polygon from the OSM line; a circular curve tangent to both tangents at every PI. Fully C1-continuous |
| 🩵 Cyan | **Level 3 — Lines + Spirals + Arcs** | As Level 2, plus clothoid transition spirals at every curve. Spiral radius matches the arc exactly |

Construction principle (Levels 2 & 3): the OSM polyline is simplified into a **tangent polygon** whose interior vertices are the Points of Intersection (PIs). Split curves are automatically re-merged, the curve radius is estimated from the genuinely curved OSM points, and the alignment **starts and ends exactly at the original OSM endpoints**. For Level 3 the spiral tangent length uses the exact Fresnel-based shift: `T_s = (R + p)·tan(|δ|/2) + k`, so every Line–Spiral–Arc–Spiral–Line zone closes on the outgoing tangent by construction. Where a spiral cannot fit, the curve falls back to a plain arc — C1 continuity is never sacrificed.

Each result card shows the element count, maximum deviation, RMSE, and the worst C1 heading mismatch (green < 0.1°). Step 4 automatically tries three PI tolerances per level and keeps the best result. Select one level and click **Next →** to proceed.

---

### Step 5 — Refine & Consolidate

The full **element table** appears under the map: every Line, Arc and Spiral with its ID, station [km], length, radius, clothoid A and deflection.

- **Edit** an arc *Radius* or a *Spiral L* value via its spinbox — with **live preview** (View ▸ Live preview while editing, on by default) the map updates continuously as you type/scroll/drag the spinbox, not just once you stop; the full table/metrics/log catch-up happens a moment after you settle on a value, and it's one undo step either way. Values are clamped to what the tangent geometry allows; C1 continuity is preserved by construction.
- **Omit PI** removes a curve; the neighbouring curves absorb its deflection. Omitted PIs stay listed (struck through) and can be restored, or **🗑 Delete**d outright (unlike Omit, this physically removes the vertex — the neighbours connect directly and it cannot be restored).
- **✂ Split here** on a Line row inserts a new PI at that line's midpoint — useful when Douglas-Peucker simplified away a real curve. **📍 Pick split point** arms a map click-to-insert mode instead, so you can choose the exact location.
- **✂ Trim start / Trim end** discard everything before/after the selected row — the alignment starts or ends fresh at that point.
- **Merge spirals ↔** appears on short straights (< 30 m) sandwiched between two transition curves: the straight is removed by prolonging the adjacent spirals, and the spirals on the far side of each circular curve prolong identically so both curves stay **symmetrical**. *Undo merge* restores the previous state.
- **Merge PI range → single curve** replaces every PI between the two selected tangents with **one** curve: select the first and last tangent (Ctrl+click works directly on the map) and press the button. Both boundary tangents are kept fixed — the new PI sits at their intersection — and the result is always exactly one Spiral–Arc–Spiral (or a plain Arc), never a chain: for turns past 180° the arc becomes a reflex/major arc, its radius fitted to the OSM points of the whole range. A total turn within ~2° of exactly 180° is geometrically singular for a *single* PI (the boundary tangents are near-parallel and never meet at a finite point) — instead of refusing, this automatically falls back to **one auxiliary PI**: two arcs sharing the same fitted radius, meeting at a zero-length, fully C1-continuous connector, so the range still reads as one smooth curve. In the rarer case where the total turn is genuinely ambiguous (a single PI's own reading sits within a few degrees of ±180°), you're asked which interpretation you meant, each with its resulting deviation; the common case never asks.
- The map shows the **PIs** as markers and the **virtual tangent extensions** toward each PI as dashed light-grey lines. Click a table row to highlight that element, or click an element on the map to select its row (Ctrl+click multi-selects); hover an element for 3 s to see its ID and deviation statistics.
- **✏ Edit mode** lets you drag PIs directly on the map instead. Every PI starts **locked** (🔒) for safety; hovering one makes it bounce to signal it's interactive, and clicking its lock badge unlocks it (🔓) so you can drag it — with **live preview** (View ▸ Live preview while editing, on by default — the same toggle that covers the radius/spiral-length spinboxes above) the neighbouring curves rebuild continuously *while* you drag, not just when you drop it; the whole gesture is one undo step either way. While unlocked, a small **↺ restore** badge appears next to it to snap that one PI back to its fitted-alignment position; click the badge again to re-lock. Leaving and re-entering Edit mode re-locks everything.
- Every edit here (including a PI drag/restore) can be undone with the toolbar's ↩ Undo (or Ctrl+Z) and redone with ↪ Redo (Ctrl+Y); **↺ Restore default** discards all of them at once and returns to the freshly-fitted alignment.

**Consolidate curve runs** (collapsed by default — click to expand): Level 2/3 sometimes split one physical curve into a run of consecutive curves. Same-direction runs joined by short straights are already auto-merged during fitting (Step 4); this tool exists for the wider cases the automatic pass didn't cover.

| Setting | Default | Meaning |
|---|---|---|
| Max intermediate straight | 30 m | Curves separated by a longer straight are left alone (0 = only curves that touch directly) |
| Max deviation from OSM | 2.0 m | The merged curve must stay within this of the OSM polyline, otherwise the run is rejected |

**🔍 Scan** lists every run of same-direction curves that matches the rules: `PIs | Curves | R before → after | Max dev | Status`. Runs within tolerance are ticked; rejected runs are greyed **with the reason shown** rather than hidden. Untick anything you want to keep, then **✅ Apply selected** — this lands in the same undo/redo history as every other edit here, so ↩ Undo reverts it like any other change. Selecting a row highlights that run on the map. Both Scan and Apply run in the background with a progress bar and a "~Xs left" estimate, so the window stays responsive on a long alignment with many candidate runs.

> Runs separated by less than ~60 m are already merged automatically while the alignment is built; the Consolidate tool exists for the wider cases you want to control yourself. Long runs (even past 180°, and the same auxiliary-PI fallback right at 180°) are handled the same way as Merge PI range above — one curve (or one auxiliary-PI pair), radius fitted from the OSM points, reflex arc if the total turn needs it.

**Batch extend spirals** (collapsed by default, alongside Consolidate): lengthens every eligible curve's entry/exit spiral toward a target length in one pass, wherever the adjoining straights and neighbouring curves leave room — it never shrinks a spiral that's already at or beyond the target. Choose **Minimize deviation**, which also re-fits each curve's radius at the new spiral length to hug the OSM baseline as tightly as possible (optionally floored by an **Override minimum radius**), or **Keep existing radius** to only change the spiral length. Runs in the background with a progress bar, and finishes with a report of how many curves were lengthened, clamped by geometry (grouped by reason), or skipped — the same undo/redo history as every other edit here covers the whole batch as one step.

Click **Accept →** when the geometry is final — though you can return here and keep editing at any later point.

---

### Step 6 — Stations

Build the station/stop list whose chainages are measured along the fitted alignment (identical to the LandXML stationing):

- **⚡ Auto-detect** — queries OpenStreetMap for `railway=station`, `railway=halt` and train `stop_position` nodes within 100 m of the alignment (passenger facilities only; yards, service stations and freight-only sites are excluded). Each name appears once — the closest occurrence wins. If every Overpass server is down, it falls back to the stops that came with the railway in Step 1 (only those mapped on the track itself) and says so; run it again later for the full set. This fallback isn't available after reopening a saved project.
- **📍 Place on map** — click the map; the point is snapped to the alignment and you are asked for a name.
- **＋ Add row** — manual entry; edit the km value directly in the table.

- **🏛 Municipality & population** — a separate lookup, so a missing or unreachable source never gets in the way of detecting the stations themselves. It finds the municipality each stop lies in from the OpenStreetMap administrative boundaries (Overpass `is_in`; in Czechia the obec carrying the LAU code — so a stop in Prague belongs to *Praha*, not to the city district) and its number of inhabitants from Wikidata (*population*, P1082 — refreshed every year from the Czech Statistical Office), falling back to the OSM `population` tag, which is often years old. Rows whose municipality or population you typed in are left alone; clear both to have a row looked up again.

The station table sits in the wide panel under the map. km, Dwell [s] (default 30), Name, Municipality and Population are editable (names must be unique; `12 500`, `12,500` and `12.500` all read as 12 500). The **Os / Sp / Ex** columns say which trains stop there, with the same rules COYPU uses:

- **Os** (local) — every stop, unless you untick it;
- **Sp** (semi-fast) — the first and last stop, and every stop whose municipality has at least the **Sp threshold** inhabitants (default 2 000; a stop without a known population never qualifies). Untick *Automatic* to set Sp by hand only;
- **Ex** (express) — the first and last stop.

Ticking Sp or Ex yourself is a manual override, marked ✎ in italics; it survives threshold changes until **Reset overrides**. The list is exported in Step 9 next to the LandXML file as COYPU's stops CSV (💾 Export CSV… saves it any time), which COYPU's *Stops ▸ Import CSV* reads unchanged — same flags, and only your own ticks arrive as manual overrides when both programs use the same threshold. For example (km values illustrative; an empty population means unknown):

```csv
stopName,stationingKm,dwellTimeS,municipalityName,population,stopInOs,stopInSp,stopInEx
Kolín,0.000,30,Kolín,33444,1,1,1
Some halt,4.250,30,Some village,640,1,0,0
Other stop,7.900,30,,,1,0,0
Kutná Hora hl.n.,12.100,30,Kutná Hora,21642,1,1,1
```

---

### Step 7 — Cross-Section

The perpendicular deviation between the fitted alignment and the OSM polyline is plotted as a profile. Maximum and mean deviation values are displayed. This step is informational — it helps you verify the fit quality before committing to export. It is optional (**Skip →**) and has no influence on the exported vertical profile.

Click **Continue →** to proceed to the vertical profile.

---

### Step 8 — Vertical Profile

Terrain elevation is sampled **once**, along the *fitted* centreline (not the raw OSM polyline), so the LandXML `<Profile>` and `<CoordGeom>` sit on exactly the same station axis. The terrain is then smoothed into a vertical alignment of constant-grade tangents — a profile chart replaces the element table under the map and updates live as you adjust the controls.

| Group | Control | Default | Meaning |
|---|---|---|---|
| **Terrain sampling** | Elevation source | ČÚZK DMR 5G | ČÚZK lidar model (CZ, ~0.2 m) with per-point OpenTopoData fallback, or OpenTopoData (EU-DEM/SRTM, ~25–30 m) |
| | Sample interval | 20 m | Distance between DEM samples; smaller is more faithful but means proportionally more queries |
| **Smoothing** | Algorithm | Gradient-constrained | *Gradient-constrained*: simplify the terrain straight to PVIs. *Moving window*: filter DEM micro-spikes first (Savitzky-Golay, or a plain moving average at polynomial order 0), then simplify. Both then enforce the gradient limit |
| | Max gradient | 15 ‰ | **Hard** limit on \|dz/ds\| (up to 40 ‰). Where the terrain is steeper, the design line deviates into cut/fill instead of exceeding it |
| | Δz tolerance | 0.50 m | The design line stays within this vertical distance of the terrain before the gradient clamp; larger ⇒ fewer PVIs |
| | Window / Polynomial order | 150 m / 2 | Moving-window algorithm only |
| | Vertical curves | on, 100 m | Parabolic curve at every interior PVI, automatically shortened where neighbouring tangents are too short |

**Direct connections (tunnels / bridges)** — **＋ Add range** (or **📍 Pick on profile**, then drag across the chart) replaces the terrain inside a station range with a straight line between its boundary elevations. The range ends become fixed PVIs, so no smoothing can remove them and the gradient clamp still reaches them.

The chart shows the terrain, the design profile and any connections; **wheel** zooms, **drag** pans and hovering reads out chainage and elevation. Hovering is linked to the map: a blue dot marks the same chainage on the track, and moving the mouse along the alignment on the map moves the profile's crosshair in turn. The *Profile* read-out reports the achieved max gradient, min/max elevation, max cut/fill and PVI/sample counts. If you later edit the horizontal alignment, the step warns that the sampled profile no longer matches it so you can re-sample.

Click **Apply / Confirm Profile →** to hand the profile to the export. Skipping this step is still possible — the export then falls back to sampling along the OSM polyline.

---

### Step 9 — Export

1. Choose an output CRS from the preset list or enter a custom EPSG code
2. Click **Browse…** and choose an output `.xml` file path
3. Click **▶ Start Export** — the LandXML and the stations as COYPU's stops CSV (`same-name.csv`) are written together

**Available CRS presets:**

| Preset | EPSG |
|---|---|
| WGS 84 | 4326 |
| S-JTSK / Krovak East North | 5514 |
| UTM zone 32N | 32632 |
| UTM zone 33N | 32633 |
| UTM zone 34N | 32634 |
| ETRS89 / UTM zone 32N | 25832 |
| ETRS89 / UTM zone 33N | 25833 |
| Auto UTM (from track centroid) | — |

Use **👁 Preview LandXML** to inspect the file first — it now includes the vertical `<Profile>` once it has been confirmed in Step 8. The progress bar tracks the export stages (Projecting → Fitting → Building XML → Writing); the DEM is not queried again here, since the confirmed profile is written verbatim. On completion, the exported alignment is drawn on the map as a **bright red line** with a white glow halo.

---

## Output Format

The output is a valid **LandXML 1.2** file structured for direct use in
**COYPU** (GeoTEL Rail-style layout — coordinate points as child elements,
spirals with tangent PI, and a Cant block):

```xml
<LandXML version="1.2" ...>
  <Application name="COYPU Feeder" version="1.3.0" .../>
  <Project name="Railway Alignment" desc="Generated by COYPU Feeder from OpenStreetMap data …"/>
  <Units><Metric areaUnit="squareMeter" linearUnit="meter" .../></Units>
  <CoordinateSystem epsgCode="5514" .../>
  <Alignments name="alignments">
    <Alignment name="..." length="..." staStart="0.000000">
      <CoordGeom>
        <Line staStart="...">
          <Start>… …</Start><End>… …</End>
        </Line>
        <Spiral staStart="..." desc="TangentToCurve" length="..." radiusStart="INF"
                radiusEnd="450.000000" rot="cw" spiType="clothoid" constant="...">
          <Start>… …</Start><PI>… …</PI><End>… …</End>
        </Spiral>
        <Curve staStart="..." rot="cw" crvType="arc" radius="450.000000">
          <Start>… …</Start><Center>… …</Center><End>… …</End>
        </Curve>
        <Spiral desc="CurveToTangent" radiusStart="450.000000" radiusEnd="INF" .../>
        ...
      </CoordGeom>
      <Profile staStart="...">
        <ProfAlign name="...">
          <PVI>… …</PVI>
          <ParaCurve length="100.000000">… …</ParaCurve>
        </ProfAlign>
      </Profile>
      <Cant name="..." gauge="1.435000" superelevationBase="1.500000"
            rotationPoint="insideRail" coPlane="false" stationType="centreline">
        <CantStation station="..." appliedCant="0.000000" rotationPoint="insideRail"
                     appliedGauge="1.435000" appliedSuperelevation="1.500000"/>
        ...
      </Cant>
    </Alignment>
  </Alignments>
</LandXML>
```

Key properties guaranteed by the geometry engine:
- **Radius continuity** at every element boundary: `Line → Spiral (radiusStart=∞)`, `Spiral → Arc (radiusEnd = arc radius)`, etc.
- **Clothoid parameter** `A = √(R × L)` (the `constant` attribute) recomputed from the actual fitted radius and spiral length; each spiral carries its tangent **PI**
- **Cant block**: one `CantStation` per element boundary with `appliedCant="0"` — cant cannot be derived from OSM and is meant to be designed downstream in COYPU
- `staStart` present on every element; all values with 6-decimal precision

---

## Requirements (source installation)

### Python

Python **3.10 or newer**.

### Dependencies

```bash
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| `PySide6` | GUI framework (Qt 6), WebEngine for the map |
| `requests` | Overpass API and elevation API HTTP calls |
| `pyproj` | WGS84 ↔ projected CRS transformations |
| `numpy` | Curvature computation, SVD line fitting |
| `scipy` | Savitzky-Golay smoothing, Fresnel integrals |
| `lxml` | LandXML file generation |
| `shapely` | Geometry utilities |
| `overpy` | Overpass API helper |
| `keyring` | Stores the CARTO API key in Windows Credential Manager |

### Internet access

| Service | Used for |
|---|---|
| Overpass API (`overpass-api.de` and its `lz4.` / `z.` backends, `overpass.private.coffee`, `maps.mail.ru`) | Railway geometry, relation metadata, station detection |
| OSM API v0.6 (`api.openstreetmap.org`) | Fallback for relation geometry, metadata and the Czech line list |
| CARTO (needs your API key) / OpenStreetMap / OpenTopoMap / Esri / ČÚZK / OpenRailwayMap tile servers | Map background tiles |
| ČÚZK DMR 5G (`ags.cuzk.gov.cz`) | DEM elevation sampling (default) |
| OpenTopoData API (`api.opentopodata.org`) | DEM elevation sampling (alternate / fallback) |
| ČÚZK WMS (`services.cuzk.cz`) | Land register (katastrální mapa) overlay |

### CARTO basemaps (API key)

CARTO requires an API key on every tile request, and returns watermarked tiles without one. Until a key is set, the *CARTO Voyager* and *CARTO Dark Matter* entries are greyed out and the map uses OpenStreetMap in both light and dark themes.

- **Get a key:** register a free one at [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/) (free up to 5 M tile requests/month for non-commercial use), or ask the [author](https://github.com/surovskyjk).
- **Enter it:** *Settings ▸ Preferences ▸ Map basemap*. The field is masked. The key is stored encrypted for your Windows account in Windows Credential Manager, never in the settings, the registry or a `.coypuf` project. Clear the field to remove it.
- **Shared with COYPU:** if the Feeder has no key of its own, it uses the one saved by COYPU. Saving or removing a key in the Feeder never changes COYPU's.
- **Scripted / development runs:** the `COYPU_FEEDER_MAP_API_KEY` environment variable (or COYPU's `COYPU_MAP_API_KEY`) is read when no stored key exists.
- The key is only sent to `basemaps.cartocdn.com`, and is masked (`key=***`) in any map error message. Tiles are cached in memory only.
- Keep the © OpenStreetMap contributors, © CARTO attribution visible (the map shows it). Never commit or bundle a key.

---

## Installation (from source)

```bash
git clone https://github.com/surovskyjk/COYPU-Feeder.git
cd COYPU-Feeder
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

---

## Project Structure

```
COYPU-Feeder/
├── main.py                       # Entry point
├── requirements.txt
├── coypu_feeder.spec             # PyInstaller build spec
├── LICENSE
└── src/
    ├── gui/
    │   ├── app.py                # QMainWindow, signal wiring
    │   ├── map_widget.py         # Leaflet map via QWebEngineView + local HTTP server
    │   ├── element_table.py      # Editable element table (bottom dock)
    │   ├── edit_history.py       # Undo/redo snapshot stack (toolbar Undo/Redo)
    │   ├── toolbar_icons.py      # Procedurally-drawn toolbar icons (no bundled assets)
    │   ├── progress_util.py      # format_eta / ElapsedTimer for progress bars
│   ├── log_panel.py         # Timestamped activity log (bottom-left)
│   ├── branding.py          # App icon + splash artwork (painted)
│   ├── dialogs.py           # About / Settings dialogs
    │   ├── profile_canvas.py     # Vertical-profile chart (terrain / design / PVIs; zoom, pan, range pick)
    │   ├── step_sidebar.py       # Numbered step sidebar
    │   ├── theme.py              # Dark/light Fusion palette + stylesheet
    │   ├── worker.py             # QThread workers (Search, Fetch, Candidates, Stations, Consolidate, Export)
    │   ├── static/               # Leaflet 1.9.4 JS + CSS (served locally)
    │   └── steps/
    │       ├── step1_find.py     # Search / In View / Czech Railways tabs
    │       ├── step2_section.py  # Track selection
    │       ├── step3_configure.py# Geometry parameters
    │       ├── step4_candidates.py  # Level 1/2/3 comparison
    │       ├── step5_design.py   # Refine & Consolidate: editing (element
    │       │                     # table) + curve-run consolidation (step 5)
    │       ├── step6_stations.py # Stations & stops → chainage CSV (step 6)
    │       ├── step6_crosssection.py  # Deviation profile (step 7)
    │       ├── step8_vertical.py # Vertical profile smoothing + direct connections (step 8)
    │       ├── step7_export.py   # CRS selection + LandXML/CSV export (step 9)
    │       └── _widgets.py       # Shared labelled-spinbox / combo / stat-row helpers
    ├── osm/
    │   ├── query.py              # Overpass API queries (incl. station detection), mirror failover, OSM API fallback
    │   └── parser.py             # OSM way → Track objects
    ├── geometry/
    │   ├── alignment.py          # Element fitting + continuity enforcement
    │   ├── candidates.py         # Level 1/2/3 PI-model + editing + consolidation
    │   ├── stationing.py         # Station chainage projection + CSV writer
    │   ├── curvature.py          # Curvature computation and segmentation
    │   ├── elevation.py          # DEM sampling (ČÚZK DMR 5G / OpenTopoData)
    │   ├── profile.py            # Vertical profile model: smoothing, gradient clamp, PVI synthesis
    │   └── projection.py         # CRS transformations (pyproj)
    ├── app_meta.py              # Name / version / license / data sources
    ├── basemap_key.py           # CARTO API key in the OS credential vault (port of COYPU's)
    ├── project_io.py            # .coypuf project save / load
    ├── data/
    │   ├── suggested_lines.py    # Curated Czech railway line database
    │   ├── cz_lines.py          # Offline CZ list loader (+ user copy)
    │   └── cz_railways.json     # Bundled snapshot of relation 2332889
    └── landxml/
        └── builder.py            # LandXML 1.2 tree builder and file writer
```

---

## License

[MIT License](LICENSE) — © 2025 Jakub Surovsky
