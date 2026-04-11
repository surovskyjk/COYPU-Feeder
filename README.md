# Coypu-Feeder

**Coypu-Feeder** is a desktop application that queries railway route data from [OpenStreetMap](https://www.openstreetmap.org/) via the Overpass API and converts it into a geometric alignment in the [LandXML 1.2](http://www.landxml.org/) format — the standard exchange format used by civil engineering and railway design software (e.g. Bentley OpenRail, Autodesk Civil 3D, Novapoint).

The fitted alignment follows the canonical railway geometry sequence:

```
Line → Transition Spiral (Clothoid) → Circular Arc → Transition Spiral → Line → …
```

Elevation is sampled from the [Open-Elevation](https://open-elevation.com/) DEM service and fitted as a vertical alignment with parabolic vertical curves.

---

## Screenshots

> *3-column layout: step sidebar | interactive map | step panel*

```
┌──────────────────────────────────────────────────────────────────────┐
│ [1] Find Railway  │        Interactive Leaflet Map         │  Step   │
│ [2] Select        │  (tile provider selector + railway     │  panel  │
│ [3] Configure     │   overlay toggle)                      │ 340 px  │
│ [4] Export        │                                        │         │
│   200 px sidebar  │                                        │         │
├──────────────────────────────────────────────────────────────────────┤
│ Status bar                                                           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Features

- **Find any railway** by timetable reference number, name, number embedded in name, or direct OSM relation ID
- **Search in map view** — find all railway route-relations visible in the current map window (≤ 20 × 20 km area limit)
- **Czech Railways browser** — lazy-loads all ~300 lines from the *Railways in Czech Republic* OSM collection (relation 2332889) with instant client-side filtering
- **Interactive Leaflet map** with 6 selectable tile providers and an optional OpenRailwayMap overlay
- **Track selection** — load a relation, select one or more tracks, highlight individual tracks on the map
- **Geometry fitting** — curvature-based segmentation into Line / Circular Arc / Clothoid spiral elements with per-type minimum length thresholds
- **Radius continuity enforcement** — spiral `radiusStart`/`radiusEnd` are automatically matched to adjacent Line (∞) and Arc (finite radius)
- **Elevation sampling** — DEM-based vertical profile at a configurable chainage interval, fitted with parabolic vertical curves
- **LandXML 1.2 export** — complete `<Alignment>` element with horizontal and vertical geometry, station equations, and CRS metadata
- **Exported alignment drawn on map** — bright red overlay, zoom-to-fit button
- **Dark / light mode** — automatically follows the OS system theme; switches tile provider accordingly

---

## Requirements

### Python

Python **3.10 or newer** is required (PySide6 uses the stable ABI from 3.10+).

### Dependencies

Install with pip:

```bash
pip install -r requirements.txt
```

| Package | Version | Purpose |
|---|---|---|
| `PySide6` | 6.11.0 | GUI framework (Qt 6), WebEngine for the map |
| `requests` | 2.33.1 | Overpass API and elevation API HTTP calls |
| `pyproj` | 3.7.2 | WGS84 ↔ projected CRS transformations |
| `numpy` | 2.4.4 | Curvature computation, SVD line fitting |
| `scipy` | 1.17.1 | Savitzky-Golay smoothing, curve fitting |
| `lxml` | 6.0.3 | LandXML file generation |
| `shapely` | 2.1.2 | Geometry utilities |
| `overpy` | 0.7 | Overpass API helper |

> **Note:** `PySide6` bundles QtWebEngine (Chromium). On Windows this requires the Microsoft Visual C++ Redistributable. On Linux, `libglib2.0` and `libnss3` must be installed.

### Internet access

The following external services are used at runtime:

| Service | Used for |
|---|---|
| Overpass API (`overpass-api.de`, `overpass.kumi.systems`, `overpass.private.coffee`) | Railway geometry and relation metadata |
| CARTO / OpenStreetMap / OpenTopoMap / Esri / OpenRailwayMap tile servers | Map background tiles |
| Open-Elevation API (`api.open-elevation.com`) | DEM elevation sampling |

---

## Installation

```bash
git clone https://github.com/surovskyjk/Coypu-Feeder.git
cd Coypu-Feeder
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

---

## Usage — Step by Step

### Step 1 — Find Railway

Three ways to find a railway:

**Search tab**
- *Timetable line number (ref tag)* — exact match on the OSM `ref` tag (e.g. `210`)
- *Name* — partial name search (e.g. `Praha`)
- *Number in relation name* — finds lines whose name contains the number, even when the `ref` tag is missing (e.g. `212` matches `212 – Čerčany – Světlá nad Sázavou`)
- *By relation ID* — direct fetch using the numeric OSM relation ID (e.g. `3128446`)

**In View tab**
Zoom the map to an area ≤ 20 × 20 km, then click *Search Railway Lines in Current View*. Returns all railway route-relations whose geometry intersects the visible map area.

**Czech Railways tab**
Click *Load Czech Railway Lines* once to fetch all members of OSM relation 2332889. The list of ~300 lines loads in a few seconds and is then filtered instantly as you type — by line number, name, or terminal stations.

Double-click any result or select it and click *Fetch selected* to load the full relation geometry.

### Step 2 — Select Section

The loaded tracks appear in a list and are drawn on the map in distinct colours. You can:

- Select individual tracks or use *Select all* / *Clear selection*
- Click a track row to highlight it on the map in yellow
- Click *👁 Highlight selected* to re-highlight after a multi-selection change
- Click *🔄 Reset colours* to restore all tracks to their original colours
- Click *📍 Fit map to tracks* to zoom the map to show all tracks

Click **Next → Configure** when your selection is ready.

### Step 3 — Configure

| Setting | Default | Description |
|---|---|---|
| Project name | `Railway Alignment` | Written into the LandXML `<Project>` element |
| Output CRS preset | WGS 84 | Target coordinate reference system for the output file |
| Custom EPSG | *(empty)* | Overrides the preset; enter any valid EPSG code |
| Curvature smooth window | 21 | Savitzky-Golay window length for curvature pre-smoothing (odd, 5–51) |
| Elevation sample interval | 20 m | Chainage step for DEM elevation queries |
| Vertical curve length | 100 m | Length of parabolic vertical curves at grade changes |
| Minimum Line length | 10 m | Segments shorter than this are merged into adjacent elements |
| Minimum Arc length | 10 m | As above for circular arc elements |
| Minimum Spiral length | 10 m | As above for clothoid transition elements |
| Force positive coordinates | off | Applies `abs()` to all coordinates — use for S-JTSK positive convention |

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

### Step 4 — Export

1. Click **Browse…** and choose an output `.xml` file path
2. Click **▶ Start Export**

The progress bar tracks five stages:

| Stage | Description |
|---|---|
| Projecting | WGS84 → working CRS |
| Fitting | Curvature segmentation and element fitting |
| Querying | DEM elevation sampling via Open-Elevation |
| Building | LandXML tree construction |
| Writing | File output |

On completion:
- The exported alignment is drawn on the map as a **bright red line** with a white glow halo
- Click **📍 Zoom to exported line** to fit the map to the alignment
- Click **🔄 Export Another Railway** to start over from Step 1

---

## Output Format

The output is a valid **LandXML 1.2** file containing:

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
        <Spiral .../>
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
- **Clothoid parameter** `A = √(R × L)` recomputed after any continuity correction
- `staStart` and `staEnd` present on every element

---

## Map Tile Providers

The map toolbar lets you choose the background layer at any time:

| Provider | Style |
|---|---|
| OpenStreetMap | Standard detailed map |
| CARTO Dark Matter | Dark UI-friendly basemap |
| CARTO Voyager | Clean light basemap |
| OpenTopoMap | Terrain with contour lines |
| Esri Satellite | Aerial imagery |
| OpenRailwayMap | Railway infrastructure only |

The **🚂 Railway overlay** checkbox independently toggles the OpenRailwayMap layer on top of the selected base map.

---

## Project Structure

```
Coypu-Feeder/
├── main.py                     # Entry point
├── requirements.txt
├── LICENSE
└── src/
    ├── gui/
    │   ├── app.py              # QMainWindow, signal wiring
    │   ├── map_widget.py       # Leaflet map via QWebEngineView + local HTTP server
    │   ├── step_sidebar.py     # Numbered step sidebar
    │   ├── theme.py            # Dark/light Fusion palette + stylesheet
    │   ├── worker.py           # QThread workers (Search, Fetch, Export)
    │   ├── static/             # Leaflet 1.9.4 JS + CSS (served locally)
    │   └── steps/
    │       ├── step1_find.py   # Search / In View / Czech Railways tabs
    │       ├── step2_section.py
    │       ├── step3_configure.py
    │       └── step4_export.py
    ├── osm/
    │   ├── query.py            # Overpass API queries
    │   └── parser.py           # OSM way → Track objects
    ├── geometry/
    │   ├── curvature.py        # Curvature computation and segmentation
    │   ├── alignment.py        # Element fitting + continuity enforcement
    │   ├── elevation.py        # DEM sampling and vertical geometry
    │   └── projection.py       # CRS transformations (pyproj)
    └── landxml/
        └── builder.py          # LandXML 1.2 tree builder and file writer
```

---

## License

[MIT License](LICENSE) — © 2025 Jakub Surovský
