# Istanbul scene data: sources and licences

Every file here is baked by a tool in `drone/sim/tools/`; rerun the tool
rather than editing a file by hand. Files and folders starting with `_` are
gitignored (caches and large downloads the tools fetch again).

| File | Source | Licence | Tool |
|---|---|---|---|
| `buildings.dat`, `buildings.json` | OpenStreetMap building footprints and tags (BBBike Istanbul extract); heights from OSM `height` / `building:levels`, else the European Commission JRC's GHSL GHS-BUILT-H R2023A average building height | Footprints: (c) OpenStreetMap contributors, ODbL 1.0 (<https://www.openstreetmap.org/copyright>). Heights: (c) European Union, 1995-2026, CC BY 4.0 (changes: sampled per building and scaled by footprint size) | `fetch_istanbul.py` |
| `terrain.zi16`, `terrain.json` | NASA SRTM 1 arc-second elevation (AWS Terrain Tiles); flattened to sea level from `water.png` | SRTM public domain | `fetch_istanbul.py` |
| `water.png`, `water.json` | OpenStreetMap coastline and water areas | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `landcover.png`, `landcover.json` | OpenStreetMap land use, leisure and natural areas | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `roads.json` | OpenStreetMap highways, railways (T1 tram), ferry routes, piers | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `features.json` | OpenStreetMap city walls, minarets, towers, aqueducts, historic sites, building parts, trees | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `life.json` | OpenStreetMap access restrictions (`motor_vehicle`/`access` = no), `highway=traffic_signals` nodes and tram stops, for the moving world (`scripts/istanbul_life*.gd`) | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul_life.py` |

The OpenStreetMap-derived files are produced works / derivative databases of
OpenStreetMap and stay under the ODbL; credit "(c) OpenStreetMap
contributors" wherever the scene is shown.
