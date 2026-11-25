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
