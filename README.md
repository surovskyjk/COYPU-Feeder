# Coypu-Feeder

**Coypu-Feeder** is a desktop application that queries railway route data from [OpenStreetMap](https://www.openstreetmap.org/) via the Overpass API and converts it into a geometric alignment in the [LandXML 1.2](http://www.landxml.org/) format — the standard exchange format used by civil engineering and railway design software (e.g. Bentley OpenRail, Autodesk Civil 3D, Novapoint).

The fitted alignment follows the canonical railway geometry sequence:

```
Line → Transition Spiral (Clothoid) → Circular Arc → Transition Spiral → Line → …
```

Elevation is sampled from the [Open-Elevation](https://open-elevation.com/) DEM service and fitted as a vertical alignment with parabolic vertical curves.

---

## Download

Pre-built Windows x64 binaries are published as [GitHub Releases](https://github.com/surovskyjk/COYPU-Feeder/releases/latest).

1. Download `Coypu-Feeder-v1.0.0-windows-x64.zip`
2. Extract to any folder
3. Run `Coypu-Feeder.exe`

No Python installation required.

---

## Features

- **Find any railway** by timetable reference number, name, or direct OSM relation ID
- **Search in map view** — find all railway route-relations visible in the current map window (≤ 20 × 20 km)
- **Czech Railways browser** — lazy-loads all ~300 lines from the *Railways in Czech Republic* OSM collection with instant client-side filtering
- **Interactive Leaflet map** with 6 selectable tile providers and an optional OpenRailwayMap overlay
- **Track selection** — load a relation, select one or more tracks, highlight individual tracks on the map
- **3 alignment levels** — raw polyline / Lines + Arcs / Lines + Spirals + Arcs, compared side by side on the map before export
- **Clothoid transition curves** — entry/exit spirals at every arc, with shift-compensated arc radius
- **Cross-section computation** — perpendicular deviations between the chosen candidate and the OSM polyline
- **Refinement step** — manually trim the alignment ends before export
- **Elevation sampling** — DEM-based vertical profile at a configurable chainage interval, fitted with parabolic vertical curves
- **LandXML 1.2 export** — complete `<Alignment>` with horizontal and vertical geometry, CRS metadata, and station equations
- **Exported alignment drawn on map** — bright red overlay with zoom-to-fit
- **Dark / light mode** — automatically follows the OS system theme

---

## Screenshots

> *3-column layout: step sidebar | interactive map | step panel*

```
┌──────────────────────────────────────────────────────────────────────┐
│ [1] Find Railway  │        Interactive Leaflet Map         │  Step   │
│ [2] Select        │  (tile provider selector + railway     │  panel  │
│ [3] Configure     │   overlay toggle)                      │ 340 px  │
│ [4] Candidates    │                                        │         │
│ [5] Refine        │                                        │         │
│ [6] Cross-section │                                        │         │
│ [7] Export        │                                        │         │
│   200 px sidebar  │                                        │         │
└──────────────────────────────────────────────────────────────────────┘
```

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
Click *Load Czech Railway Lines* once to fetch all members of OSM relation 2332889. The list of ~300 lines loads in a few seconds and is then filtered instantly as you type — by line number, name, or terminal stations.

Double-click any result or select it and click *Fetch selected* to load the full relation geometry.

---

### Step 2 — Select Section

The loaded tracks appear in a list and are drawn on the map in distinct colours. You can:

- Select individual tracks or use *Select all* / *Clear selection*
- Click a track row to highlight it on the map in yellow
- Click *📍 Fit map to tracks* to zoom the map to show all tracks

Click **Next → Configure** when your selection is ready.

---

### Step 3 — Configure

Configure geometry parameters before running the fitting algorithms:

| Group | Setting | Default | Description |
|---|---|---|---|
| **Project** | Project name | `Railway Alignment` | Written into the LandXML `<Project>` element |
| **Export Settings** | Elevation sample interval | 20 m | Chainage step for DEM elevation queries |
| | Vertical curve length | 100 m | Length of parabolic vertical curves at grade changes |
| | Spiral length | 20 m | Entry/exit clothoid length for *Segment & Fit (Spirals)*; 0 = disabled |
| **Alignment Geometry** | Max deviation (PI tolerance) | 1.0 m | Douglas-Peucker tolerance for extracting the tangent polygon (Points of Intersection) |
| | Minimum curve radius | 150 m | Minimum horizontal curve radius for all circular arcs |
| | Spiral length (Level 3) | 20 m | Clothoid length; auto-shortened where tangents are too short |

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

### Step 5 — Refine

Trim the alignment ends by adjusting the start and end chainage. The live map preview updates as you move the sliders. Use this step to remove poorly-fitted sections at the very beginning or end of the alignment.

Click **Next →** to accept the trimmed alignment.

---

### Step 6 — Cross-Section

The perpendicular deviation between the fitted alignment and the OSM polyline is plotted as a profile. Maximum and mean deviation values are displayed. This step is informational — it helps you verify the fit quality before committing to export.

Click **Next → Export** to proceed.

---

### Step 7 — Export

1. Choose an output CRS from the preset list or enter a custom EPSG code
2. Click **Browse…** and choose an output `.xml` file path
3. Click **▶ Start Export**

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

The progress bar tracks the export stages (Projecting → Fitting → Querying elevation → Building XML → Writing). On completion, the exported alignment is drawn on the map as a **bright red line** with a white glow halo.

---

## Output Format

The output is a valid **LandXML 1.2** file:

```xml
<LandXML version="1.2" ...>
  <Project name="Railway Alignment"/>
  <CoordinateSystem epsgCode="5514" .../>
  <Alignments>
    <Alignment name="..." staStart="0.000" length="...">
      <CoordGeom>
        <Line staStart="..." length="..." dir="...">...</Line>
        <Spiral staStart="..." length="..." radiusStart="INF" radiusEnd="450.0"
                clothoidConstant="..." rot="cw|ccw" spiType="clothoid"/>
        <Curve staStart="..." length="..." radius="450.0" rot="cw|ccw"/>
        <Spiral radiusStart="450.0" radiusEnd="INF" .../>
        ...
      </CoordGeom>
      <Profile>
        <ProfAlign>
          <PVI .../>
          <ParaCurve length="100.0" .../>
          ...
        </ProfAlign>
      </Profile>
    </Alignment>
  </Alignments>
</LandXML>
```

Key properties guaranteed by the geometry engine:
- **Radius continuity** at every element boundary: `Line → Spiral (radiusStart=∞)`, `Spiral → Arc (radiusEnd = arc radius)`, etc.
- **Clothoid parameter** `A = √(R × L)` recomputed from the actual fitted radius and spiral length
- **Shift compensation** for spirals: arc radius is inflated by `p = L²/(24R)` so the L–S–A–S–L path tracks the same OSM centreline as the no-spiral fit
- `staStart` present on every element

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

### Internet access

| Service | Used for |
|---|---|
| Overpass API (`overpass-api.de`, `overpass.kumi.systems`) | Railway geometry and relation metadata |
| CARTO / OpenStreetMap / OpenTopoMap / Esri / OpenRailwayMap tile servers | Map background tiles |
| Open-Elevation API (`api.open-elevation.com`) | DEM elevation sampling |

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
    │   ├── step_sidebar.py       # Numbered step sidebar
    │   ├── theme.py              # Dark/light Fusion palette + stylesheet
    │   ├── worker.py             # QThread workers (Search, Fetch, Candidates, Export)
    │   ├── static/               # Leaflet 1.9.4 JS + CSS (served locally)
    │   └── steps/
    │       ├── step1_find.py     # Search / In View / Czech Railways tabs
    │       ├── step2_section.py  # Track selection
    │       ├── step3_configure.py# Geometry parameters
    │       ├── step4_candidates.py  # 5-algorithm candidate comparison
    │       ├── step5_refine.py   # End trimming
    │       ├── step6_crosssection.py  # Deviation profile
    │       └── step7_export.py   # CRS selection + LandXML export
    ├── osm/
    │   ├── query.py              # Overpass API queries
    │   └── parser.py             # OSM way → Track objects
    ├── geometry/
    │   ├── alignment.py          # Element fitting + continuity enforcement
    │   ├── candidates.py         # Level 1/2/3 alignment construction (PI-based)
    │   ├── curvature.py          # Curvature computation and segmentation
    │   ├── elevation.py          # DEM sampling and vertical geometry
    │   └── projection.py         # CRS transformations (pyproj)
    ├── data/
    │   └── suggested_lines.py    # Curated Czech railway line database
    └── landxml/
        └── builder.py            # LandXML 1.2 tree builder and file writer
```

---

## License

[MIT License](LICENSE) — © 2025 Jakub Surovsky
