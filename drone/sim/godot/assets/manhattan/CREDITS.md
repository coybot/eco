# Manhattan scene data: sources and licences

Every file here is baked by a tool in `drone/sim/tools/`; rerun the tool
rather than editing a file by hand. Files and folders starting with `_` are
gitignored (caches and large imagery the tools fetch again).

| File | Source | Licence | Tool |
|---|---|---|---|
| `buildings.json` | NYC Open Data, Building Footprints (5zhs-2jue) | Public domain (NYC Open Data terms of use) | `fetch_manhattan.py` |
| `ortho.jpg`, `ortho.json` | USGS National Map imagery | Public domain | `fetch_manhattan_ortho.py` |
| `water.png`, `water.json` | OpenStreetMap coastline; USGS National Hydrography Dataset waterbodies | Map data (c) OpenStreetMap contributors, ODbL 1.0 (<https://www.openstreetmap.org/copyright>); NHD public domain | `fetch_manhattan_water.py` |
| `roads.json` | NYC Open Data, Centerline (CSCL, inkn-q76z) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_roads.py` |
| `_hi/photo_*.jpg`, `_hi/far.jpg`, `_hi/tiles.json` (gitignored) | USGS National Map, NAIP Imagery (USDA National Agriculture Imagery Program) | Public domain | `fetch_manhattan_naip.py` |
| `_hi/streets_*.png` (gitignored) | NYC Open Data, NYC Planimetric Database: Roadbed (i36f-5ih7), Sidewalk (52n9-sdep), Median (ees7-4ufv) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_planimetric.py` |
| `piers.json` | NYC Open Data, NYC Planimetric Database: Hydrography Structures (6hbv-tek4), feat_code 2800 piers | Public domain (NYC Open Data terms of use) | `fetch_manhattan_planimetric.py` |
| `shore.png`, `shore_far.png`, `shore.json`, `coast.json` | Derived from `water.png` (OpenStreetMap coastline, ODbL 1.0, (c) OpenStreetMap contributors; USGS NHD, public domain) and `piers.json` | ODbL 1.0 (produced work of the OSM coastline): attribute "(c) OpenStreetMap contributors" | `bake_manhattan_shore.py` |
